from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm import LLMProvider
from ..llm_contracts import ExtractionUnavailable
from ..models import Chunk, EvidenceEdge, Hypothesis, Signal
from ..schemas import HypothesisDraft
from ..utils import normalise_text, stable_hash
from .oppose_verifier import OpposeVerificationRequest, verify_oppose


@dataclass(frozen=True, slots=True)
class ExtractionEvidenceRequest:
    hypothesis: Hypothesis
    signal: Signal | None
    document_id: int | None
    chunk_id: int | None
    draft: HypothesisDraft


def add_evidence(
    session: Session,
    provider: LLMProvider,
    request: ExtractionEvidenceRequest,
) -> bool:
    summary = request.draft.evidence_signal_summary or (
        request.signal.summary if request.signal else "Hypothesis candidate without a linked signal."
    )
    stance = request.draft.stance
    verifier_metadata: dict[str, bool | str] = {}
    match stance:
        case "oppose":
            chunk = session.get(Chunk, request.chunk_id) if request.chunk_id is not None else None
            try:
                verification = verify_oppose(
                    provider,
                    OpposeVerificationRequest(
                        hypothesis_claim=request.hypothesis.claim,
                        evidence_quote=request.draft.quoted_evidence or summary,
                        evidence_summary=summary,
                        source_text=chunk.text if chunk else "",
                    ),
                )
            except ExtractionUnavailable:
                stance = "neutral"
                verifier_metadata["oppose_verify_unavailable"] = True
            else:
                if not verification.contradicts:
                    stance = "neutral"
                    verifier_metadata = {
                        "oppose_rejected_by_verifier": True,
                        "verifier_reasoning": verification.reasoning,
                    }
        case "support" | "neutral":
            pass
        case unreachable:
            assert_never(unreachable)
    evidence_hash = stable_hash(
        request.hypothesis.id,
        request.signal.id if request.signal else None,
        request.chunk_id,
        stance,
        normalise_text(summary).casefold(),
    )
    existing = session.scalar(
        select(EvidenceEdge).where(
            EvidenceEdge.hypothesis_id == request.hypothesis.id,
            EvidenceEdge.evidence_hash == evidence_hash,
        )
    )
    if existing:
        return False
    strength = min(
        max(
            (
                (request.signal.confidence if request.signal else request.draft.confidence)
                + request.draft.confidence
            )
            / 2,
            0.25,
        ),
        0.95,
    )
    session.add(
        EvidenceEdge(
            hypothesis_id=request.hypothesis.id,
            signal_id=request.signal.id if request.signal else None,
            document_id=request.document_id,
            chunk_id=request.chunk_id,
            evidence_hash=evidence_hash,
            stance=stance,
            strength=strength,
            summary=summary,
            provenance="extraction",
            metadata_json={
                "candidate_metadata": request.draft.metadata_json,
                "quoted_evidence": request.draft.quoted_evidence,
                **verifier_metadata,
            },
        )
    )
    session.flush()
    return True
