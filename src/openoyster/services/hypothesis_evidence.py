from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EvidenceEdge, Hypothesis, Signal
from ..schemas import HypothesisDraft
from ..utils import normalise_text, stable_hash


def add_evidence(
    session: Session,
    *,
    hypothesis: Hypothesis,
    signal: Signal | None,
    document_id: int | None,
    chunk_id: int | None,
    draft: HypothesisDraft,
) -> bool:
    summary = draft.evidence_signal_summary or (
        signal.summary if signal else "Hypothesis candidate without a linked signal."
    )
    stance = draft.stance
    evidence_hash = stable_hash(
        hypothesis.id,
        signal.id if signal else None,
        chunk_id,
        stance,
        normalise_text(summary).casefold(),
    )
    existing = session.scalar(
        select(EvidenceEdge).where(
            EvidenceEdge.hypothesis_id == hypothesis.id,
            EvidenceEdge.evidence_hash == evidence_hash,
        )
    )
    if existing:
        return False
    strength = min(
        max(
            ((signal.confidence if signal else draft.confidence) + draft.confidence) / 2,
            0.25,
        ),
        0.95,
    )
    session.add(
        EvidenceEdge(
            hypothesis_id=hypothesis.id,
            signal_id=signal.id if signal else None,
            document_id=document_id,
            chunk_id=chunk_id,
            evidence_hash=evidence_hash,
            stance=stance,
            strength=strength,
            summary=summary,
            provenance="extraction",
            metadata_json={"candidate_metadata": draft.metadata_json},
        )
    )
    session.flush()
    return True
