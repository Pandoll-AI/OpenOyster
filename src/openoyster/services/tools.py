from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Chunk, Document, EvidenceEdge, Hypothesis, Signal
from ..scoring import clamp, tokenize
from .artifacts import render_hypothesis_brief
from .retrieval import RetrievalHit, search_chunks

_NEGATION = {
    "not",
    "no",
    "never",
    "failed",
    "contrary",
    "disputed",
    "denied",
    "unsupported",
    "반대",
    "아니다",
    "부인",
    "확인되지",
}


@dataclass(frozen=True)
class EvidenceCandidate:
    chunk_id: int
    document_id: int
    stance: str
    strength: float
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    artifact_type: str
    title: str
    content: str
    summary: str
    evidence_candidates: list[EvidenceCandidate] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


Tool = Callable[[Session, Hypothesis, dict], ToolResult]


def hypothesis_brief(session: Session, hypothesis: Hypothesis, policy: dict) -> ToolResult:
    del policy
    edges = list(
        session.scalars(
            select(EvidenceEdge)
            .where(EvidenceEdge.hypothesis_id == hypothesis.id)
            .order_by(EvidenceEdge.strength.desc())
        )
    )
    content = render_hypothesis_brief(hypothesis, edges)
    return ToolResult(
        artifact_type="hypothesis_brief",
        title=f"Hypothesis brief: {hypothesis.claim[:80]}",
        content=content,
        summary=f"Rendered a traceable brief from {len(edges)} evidence item(s).",
        metadata={"evidence_count": len(edges)},
    )


def _existing_chunk_ids(session: Session, hypothesis_id: int) -> set[int]:
    return {
        chunk_id
        for chunk_id in session.scalars(
            select(EvidenceEdge.chunk_id).where(
                EvidenceEdge.hypothesis_id == hypothesis_id,
                EvidenceEdge.chunk_id.is_not(None),
            )
        )
        if chunk_id is not None
    }


def _is_counter_hit(hit: RetrievalHit) -> bool:
    tokens = tokenize(hit.text)
    return bool(tokens & _NEGATION)


def _scan(
    session: Session,
    hypothesis: Hypothesis,
    policy: dict,
    *,
    stance: str,
) -> ToolResult:
    hits = search_chunks(
        session,
        hypothesis.claim,
        policy=policy,
        exclude_chunk_ids=_existing_chunk_ids(session, hypothesis.id),
        mode="counter" if stance == "oppose" else "support",
    )
    selected: list[RetrievalHit] = []
    for hit in hits:
        is_counter = _is_counter_hit(hit)
        if (stance == "oppose" and is_counter) or (stance == "support" and not is_counter):
            selected.append(hit)
    limit = int(policy.get("execution", {}).get("max_candidate_evidence", 8))
    selected = selected[:limit]
    candidates = [
        EvidenceCandidate(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            stance=stance,
            strength=clamp(0.30 + 0.50 * hit.score, 0.25, 0.80),
            summary=hit.text[:600],
            metadata={
                "retrieval_score": hit.score,
                "retrieval_mode": hit.retrieval_mode,
                "matched_terms": hit.matched_terms,
                "document_title": hit.document_title,
                "source": hit.source,
            },
        )
        for hit in selected
    ]
    heading = "Counter-evidence" if stance == "oppose" else "Supporting evidence"
    lines = [f"# {heading} scan", "", f"Hypothesis: {hypothesis.claim}", ""]
    if not candidates:
        lines.append("No new candidate evidence passed the conservative lexical filter.")
    else:
        for candidate in candidates:
            lines.append(
                f"- [document:{candidate.document_id}; chunk:{candidate.chunk_id}; "
                f"strength:{candidate.strength:.2f}] {candidate.summary}"
            )
    return ToolResult(
        artifact_type=f"{stance}_evidence_scan",
        title=f"{heading} scan: {hypothesis.claim[:72]}",
        content="\n".join(lines),
        summary=f"Found {len(candidates)} new {stance} candidate(s).",
        evidence_candidates=candidates,
        metadata={
            "retrieval_hits": len(hits),
            "selected": len(candidates),
            "retrieval_mode": hits[0].retrieval_mode if hits else policy.get("retrieval", {}).get("mode", "lexical"),
        },
    )


def support_evidence_scan(session: Session, hypothesis: Hypothesis, policy: dict) -> ToolResult:
    return _scan(session, hypothesis, policy, stance="support")


def counter_evidence_scan(session: Session, hypothesis: Hypothesis, policy: dict) -> ToolResult:
    return _scan(session, hypothesis, policy, stance="oppose")


def baseline_compare(session: Session, hypothesis: Hypothesis, policy: dict) -> ToolResult:
    del policy
    signal_counts: dict[str, int] = {
        str(signal_type): int(count)
        for signal_type, count in session.execute(
            select(Signal.signal_type, func.count(Signal.id)).group_by(Signal.signal_type)
        )
    }
    source_counts: dict[str, int] = {
        str(source): int(count)
        for source, count in session.execute(
            select(Document.source, func.count(Document.id)).group_by(Document.source)
        )
    }
    total_chunks = int(session.scalar(select(func.count(Chunk.id))) or 0)
    content = "\n".join(
        [
            "# Corpus baseline",
            "",
            f"Hypothesis: {hypothesis.claim}",
            "",
            f"- Processed corpus chunks: `{total_chunks}`",
            f"- Signal distribution: `{signal_counts}`",
            f"- Source distribution: `{source_counts}`",
            "",
            "Interpretation: this baseline does not prove the hypothesis; it exposes whether the corpus is too narrow to assess it.",
        ]
    )
    return ToolResult(
        artifact_type="baseline_comparison",
        title=f"Corpus baseline: {hypothesis.claim[:76]}",
        content=content,
        summary="Compared the hypothesis with corpus-level source and signal distributions.",
        metadata={
            "total_chunks": total_chunks,
            "signal_counts": signal_counts,
            "source_counts": source_counts,
        },
    )


TOOL_REGISTRY: dict[str, Tool] = {
    "hypothesis_brief": hypothesis_brief,
    "support_evidence_scan": support_evidence_scan,
    "counter_evidence_scan": counter_evidence_scan,
    "baseline_compare": baseline_compare,
}


def run_tool(
    session: Session,
    *,
    task_type: str,
    hypothesis: Hypothesis,
    policy: dict,
) -> ToolResult:
    tool = TOOL_REGISTRY.get(task_type)
    if tool is None:
        raise ValueError(f"Unknown task tool: {task_type}")
    return tool(session, hypothesis, policy)
