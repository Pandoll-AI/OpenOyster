from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import bus
from ..loops.base import LoopResult
from ..models import Chunk, Claim, Document, Entity, Signal
from ..schemas import TextAnalysis
from ..utils import normalise_name, normalise_text, stable_hash


def ensure_entity(session: Session, name: str, kind: str) -> Entity:
    normalised = normalise_name(name)
    entity = session.scalar(select(Entity).where(Entity.normalised_name == normalised, Entity.kind == kind))
    if entity:
        return entity
    entity = Entity(name=name, normalised_name=normalised, kind=kind)
    session.add(entity)
    session.flush()
    return entity


def persist_analysis(
    session: Session,
    *,
    document: Document,
    chunk: Chunk,
    analysis: TextAnalysis,
    source_loop: str,
    result: LoopResult,
) -> None:
    for entity in analysis.entities:
        ensure_entity(session, entity.name, entity.kind)
    for claim_draft in analysis.claims:
        claim_hash = stable_hash(normalise_text(claim_draft.text).casefold())
        existing = session.scalar(select(Claim).where(Claim.chunk_id == chunk.id, Claim.claim_hash == claim_hash))
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
        signal = session.scalar(select(Signal).where(Signal.chunk_id == chunk.id, Signal.signal_hash == signal_hash))
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
                {"signal_id": signal.id, "document_id": document.id, "chunk_id": chunk.id},
                source_loop=source_loop,
                idempotency_key=f"signal.detected:{signal.id}",
            )
            result.emitted_events += int(emission.created)
        signal_by_summary[normalise_text(signal_draft.summary).casefold()] = signal

    for hypothesis_draft in analysis.hypotheses:
        signal = signal_by_summary.get(normalise_text(hypothesis_draft.evidence_signal_summary or "").casefold())
        payload = {
            "document_id": document.id,
            "chunk_id": chunk.id,
            "signal_id": signal.id if signal else None,
            "hypothesis": hypothesis_draft.model_dump(),
            "provider": analysis.provider,
            "model": analysis.model,
        }
        key = stable_hash(chunk.id, hypothesis_draft.claim, hypothesis_draft.scope, hypothesis_draft.stance)
        emission = bus.emit(
            session,
            "hypothesis.candidate_created",
            payload,
            source_loop=source_loop,
            idempotency_key=f"hypothesis.candidate:{key}",
        )
        result.emitted_events += int(emission.created)
        if emission.created:
            result.inc("hypothesis_candidates")


def mark_processed(
    session: Session,
    *,
    document: Document,
    chunk: Chunk,
    analysis: TextAnalysis,
    source_loop: str,
    result: LoopResult,
) -> None:
    persist_analysis(
        session,
        document=document,
        chunk=chunk,
        analysis=analysis,
        source_loop=source_loop,
        result=result,
    )
    chunk.status = "processed"
    chunk.processed_at = datetime.now(UTC)
    chunk.last_error = None
    chunk.metadata_json = {
        **dict(chunk.metadata_json or {}),
        "provider": analysis.provider,
        "model": analysis.model,
        "usage": analysis.usage,
        "warnings": analysis.warnings,
        "analysis_metadata": analysis.metadata,
    }
    result.inc("chunks")


def mark_failed(
    session: Session,
    *,
    document: Document,
    chunk: Chunk,
    reason: str,
    source_loop: str,
    result: LoopResult,
) -> None:
    chunk.status = "failed"
    chunk.last_error = reason
    document.failure_count += 1
    document.last_error = reason
    result.inc("failed_chunks")
    emission = bus.emit(
        session,
        "extraction.failed",
        {"document_id": document.id, "chunk_id": chunk.id, "attempt": chunk.attempts, "error": reason},
        source_loop=source_loop,
        idempotency_key=f"extraction.failed:{chunk.id}:{chunk.attempts}",
    )
    result.emitted_events += int(emission.created)


def mark_deferred(
    session: Session,
    *,
    document: Document,
    chunk: Chunk,
    reason: str,
    source_loop: str,
    result: LoopResult,
) -> None:
    metadata = dict(chunk.metadata_json or {})
    deferred_count = int(metadata.get("deferred_count", 0)) + 1
    metadata.update(
        {
            "deferred_at": datetime.now(UTC).isoformat(),
            "deferred_count": deferred_count,
            "deferred_reason": reason,
        }
    )
    chunk.status = "deferred"
    chunk.last_error = reason
    chunk.metadata_json = metadata
    result.inc("deferred_chunks")
    emission = bus.emit(
        session,
        "extraction.deferred",
        {
            "document_id": document.id,
            "chunk_id": chunk.id,
            "attempt": chunk.attempts,
            "deferred_count": deferred_count,
            "reason": reason,
        },
        source_loop=source_loop,
        idempotency_key=f"extraction.deferred:{chunk.id}:{chunk.attempts}",
    )
    result.emitted_events += int(emission.created)
