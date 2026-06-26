from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..events import bus
from ..llm import LLMProvider, provider_from_settings
from ..models import Chunk, Claim, Document, Entity, Signal
from ..policies import get_active_policy
from ..services.text import TextAnalysis, chunk_text
from ..utils import normalise_name, normalise_text, stable_hash
from .base import BaseLoop, LoopResult


class ExtractionLoop(BaseLoop):
    """Separately turns documents into chunks, claims, signals, and hypothesis candidates."""

    name = "extraction"
    consumes = ("doc.fetched", "chunk.retry_requested")

    def __init__(
        self,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
    ):
        self.settings = settings or get_settings()
        self.provider = provider or provider_from_settings(self.settings)

    def _ensure_chunks(self, session: Session, document: Document, policy: dict) -> list[Chunk]:
        existing = list(
            session.scalars(select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.chunk_index))
        )
        if existing:
            return existing
        config = policy["extraction"]
        chunks: list[Chunk] = []
        for index, text in enumerate(
            chunk_text(
                document.raw_text,
                chunk_size=int(config["chunk_size"]),
                overlap=int(config["chunk_overlap"]),
            )
        ):
            chunk = Chunk(
                document_id=document.id,
                chunk_index=index,
                text=text,
                text_hash=stable_hash(text),
                status="pending",
            )
            session.add(chunk)
            chunks.append(chunk)
        session.flush()
        return chunks

    @staticmethod
    def _ensure_entity(session: Session, name: str) -> Entity:
        normalised = normalise_name(name)
        entity = session.scalar(
            select(Entity).where(
                Entity.normalised_name == normalised,
                Entity.kind == "unknown",
            )
        )
        if entity:
            return entity
        entity = Entity(name=name, normalised_name=normalised, kind="unknown")
        session.add(entity)
        session.flush()
        return entity

    def _persist_analysis(
        self,
        session: Session,
        *,
        document: Document,
        chunk: Chunk,
        analysis: TextAnalysis,
        result: LoopResult,
    ) -> None:
        for entity_name in analysis.entities:
            self._ensure_entity(session, entity_name)

        for claim_draft in analysis.claims:
            claim_hash = stable_hash(normalise_text(claim_draft.text).casefold())
            existing = session.scalar(
                select(Claim).where(
                    Claim.chunk_id == chunk.id,
                    Claim.claim_hash == claim_hash,
                )
            )
            if existing:
                continue
            session.add(
                Claim(
                    document_id=document.id,
                    chunk_id=chunk.id,
                    claim_hash=claim_hash,
                    text=claim_draft.text,
                    subject=claim_draft.subject,
                    predicate=claim_draft.predicate,
                    object=claim_draft.object,
                    confidence=claim_draft.confidence,
                    metadata_json={
                        **claim_draft.metadata_json,
                        "provider": analysis.provider,
                        "model": analysis.model,
                    },
                )
            )
            result.inc("claims")

        signal_by_summary: dict[str, Signal] = {}
        for signal_draft in analysis.signals:
            signal_hash = stable_hash(
                signal_draft.signal_type,
                normalise_text(signal_draft.summary).casefold(),
                signal_draft.entity,
            )
            signal = session.scalar(
                select(Signal).where(
                    Signal.chunk_id == chunk.id,
                    Signal.signal_hash == signal_hash,
                )
            )
            if signal is None:
                signal = Signal(
                    document_id=document.id,
                    chunk_id=chunk.id,
                    signal_hash=signal_hash,
                    entity=signal_draft.entity,
                    signal_type=signal_draft.signal_type,
                    summary=signal_draft.summary,
                    novelty_score=signal_draft.novelty_score,
                    impact_score=signal_draft.impact_score,
                    confidence=signal_draft.confidence,
                    metadata_json={
                        **signal_draft.metadata_json,
                        "stance": signal_draft.stance,
                        "provider": analysis.provider,
                        "model": analysis.model,
                    },
                )
                session.add(signal)
                session.flush()
                result.inc("signals")
                emission = bus.emit(
                    session,
                    "signal.detected",
                    {
                        "signal_id": signal.id,
                        "document_id": document.id,
                        "chunk_id": chunk.id,
                    },
                    source_loop=self.name,
                    idempotency_key=f"signal.detected:{signal.id}",
                )
                result.emitted_events += int(emission.created)
            signal_by_summary[normalise_text(signal_draft.summary).casefold()] = signal

        for hypothesis_draft in analysis.hypotheses:
            signal = signal_by_summary.get(
                normalise_text(hypothesis_draft.evidence_signal_summary or "").casefold()
            )
            payload = {
                "document_id": document.id,
                "chunk_id": chunk.id,
                "signal_id": signal.id if signal else None,
                "hypothesis": hypothesis_draft.model_dump(),
                "provider": analysis.provider,
                "model": analysis.model,
            }
            key = stable_hash(
                chunk.id, hypothesis_draft.claim, hypothesis_draft.scope, hypothesis_draft.stance
            )
            emission = bus.emit(
                session,
                "hypothesis.candidate_created",
                payload,
                source_loop=self.name,
                idempotency_key=f"hypothesis.candidate:{key}",
            )
            result.emitted_events += int(emission.created)
            if emission.created:
                result.inc("hypothesis_candidates")

    def _process_chunk(
        self,
        session: Session,
        document: Document,
        chunk: Chunk,
        policy: dict,
        result: LoopResult,
    ) -> None:
        if chunk.status == "processed":
            return
        chunk.attempts += 1
        try:
            analysis = self.provider.analyse(chunk.text, policy=policy)
            self._persist_analysis(
                session,
                document=document,
                chunk=chunk,
                analysis=analysis,
                result=result,
            )
            chunk.status = "processed"
            chunk.processed_at = datetime.now(UTC)
            chunk.last_error = None
            chunk.metadata_json = {
                **chunk.metadata_json,
                "provider": analysis.provider,
                "model": analysis.model,
                "usage": analysis.usage,
                "warnings": analysis.warnings,
                "analysis_metadata": analysis.metadata,
            }
            result.inc("chunks")
        except Exception as exc:
            chunk.status = "failed"
            chunk.last_error = str(exc)
            document.failure_count += 1
            document.last_error = str(exc)
            result.inc("failed_chunks")
            emission = bus.emit(
                session,
                "extraction.failed",
                {
                    "document_id": document.id,
                    "chunk_id": chunk.id,
                    "attempt": chunk.attempts,
                    "error": str(exc),
                },
                source_loop=self.name,
                idempotency_key=f"extraction.failed:{chunk.id}:{chunk.attempts}",
            )
            result.emitted_events += int(emission.created)

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        batch = bus.poll(
            session,
            loop_name=self.name,
            event_types=self.consumes,
            limit=limit,
            scan_multiplier=self.settings.event_scan_multiplier,
        )
        result = LoopResult(loop_name=self.name, consumed_events=len(batch.events))
        policy = get_active_policy(session).policy_json
        touched_documents: set[int] = set()

        for event in batch.events:
            if event.event_type == "doc.fetched":
                document_id = event.payload_json.get("document_id")
                document = session.get(Document, document_id) if document_id else None
                if not document:
                    continue
                chunks = self._ensure_chunks(session, document, policy)
                for chunk in chunks:
                    self._process_chunk(session, document, chunk, policy, result)
                touched_documents.add(document.id)
            else:
                chunk_id = event.payload_json.get("chunk_id")
                retry_chunk = session.get(Chunk, chunk_id) if chunk_id else None
                retry_document = session.get(Document, retry_chunk.document_id) if retry_chunk else None
                if not retry_chunk or not retry_document:
                    continue
                self._process_chunk(
                    session,
                    retry_document,
                    retry_chunk,
                    policy,
                    result,
                )
                touched_documents.add(retry_document.id)

        for document_id in touched_documents:
            document = session.get(Document, document_id)
            if not document:
                continue
            statuses = list(session.scalars(select(Chunk.status).where(Chunk.document_id == document_id)))
            if statuses and all(status == "processed" for status in statuses):
                document.status = "processed"
                document.processed_at = datetime.now(UTC)
                document.last_error = None
            elif any(status == "failed" for status in statuses):
                document.status = "partial_failure"

        bus.ack(session, batch)
        return result
