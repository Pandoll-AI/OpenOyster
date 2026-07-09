from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..events import bus
from ..llm import ExtractionUnavailable, LLMProvider, provider_from_settings
from ..models import Chunk, Document
from ..policies import get_active_policy
from ..services.chunking import chunk_text
from ..services.extraction_records import mark_deferred, mark_failed, mark_processed
from ..utils import stable_hash
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

    def _process_batch(
        self,
        session: Session,
        targets: list[tuple[Document, Chunk]],
        policy: dict,
        result: LoopResult,
    ) -> None:
        active = [(document, chunk) for document, chunk in targets if chunk.status != "processed"]
        if not active:
            return
        for _, chunk in active:
            chunk.attempts += 1
        try:
            analyses = self.provider.analyse_batch([chunk.text for _, chunk in active], policy=policy)
        except ExtractionUnavailable as exc:
            for document, chunk in active:
                mark_deferred(
                    session,
                    document=document,
                    chunk=chunk,
                    reason=exc.reason,
                    source_loop=self.name,
                    result=result,
                )
            return

        if len(analyses) != len(active):
            reason = f"provider returned {len(analyses)} analyses for {len(active)} chunks"
            for document, chunk in active:
                mark_failed(
                    session,
                    document=document,
                    chunk=chunk,
                    reason=reason,
                    source_loop=self.name,
                    result=result,
                )
            return

        for (document, chunk), analysis in zip(active, analyses, strict=True):
            if analysis.metadata.get("missing_chunk_index"):
                reason = analysis.warnings[0] if analysis.warnings else "missing LLM result for chunk"
                mark_deferred(
                    session,
                    document=document,
                    chunk=chunk,
                    reason=reason,
                    source_loop=self.name,
                    result=result,
                )
                continue
            try:
                mark_processed(
                    session,
                    document=document,
                    chunk=chunk,
                    analysis=analysis,
                    source_loop=self.name,
                    result=result,
                )
            except Exception as exc:
                mark_failed(
                    session,
                    document=document,
                    chunk=chunk,
                    reason=str(exc),
                    source_loop=self.name,
                    result=result,
                )

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
        targets: list[tuple[Document, Chunk]] = []
        seen_chunks: set[int] = set()

        def add_target(document: Document, chunk: Chunk) -> None:
            if chunk.id in seen_chunks or chunk.status == "processed":
                return
            seen_chunks.add(chunk.id)
            targets.append((document, chunk))

        for event in batch.events:
            if event.event_type == "doc.fetched":
                document_id = event.payload_json.get("document_id")
                document = session.get(Document, document_id) if document_id else None
                if not document:
                    continue
                chunks = self._ensure_chunks(session, document, policy)
                for chunk in chunks:
                    add_target(document, chunk)
                touched_documents.add(document.id)
            else:
                chunk_id = event.payload_json.get("chunk_id")
                retry_chunk = session.get(Chunk, chunk_id) if chunk_id else None
                retry_document = session.get(Document, retry_chunk.document_id) if retry_chunk else None
                if not retry_chunk or not retry_document:
                    continue
                add_target(retry_document, retry_chunk)
                touched_documents.add(retry_document.id)

        batch_size = self.settings.codex_batch_size
        for index in range(0, len(targets), batch_size):
            self._process_batch(session, targets[index : index + batch_size], policy, result)

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
            elif any(status == "deferred" for status in statuses):
                document.status = "deferred"
                document.last_error = "one or more chunks are deferred"

        bus.ack(session, batch)
        return result
