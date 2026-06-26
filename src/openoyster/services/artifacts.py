from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Artifact, EvidenceEdge, Hypothesis


def next_artifact_version(
    session: Session,
    *,
    artifact_type: str,
    hypothesis_id: int | None,
) -> int:
    maximum = session.scalar(
        select(func.max(Artifact.version)).where(
            Artifact.artifact_type == artifact_type,
            Artifact.linked_hypothesis_id == hypothesis_id,
        )
    )
    return int(maximum or 0) + 1


def evidence_summary(edges: list[EvidenceEdge]) -> dict[str, int | float]:
    counts = Counter(edge.stance for edge in edges)
    return {
        "support_count": counts.get("support", 0),
        "oppose_count": counts.get("oppose", 0),
        "neutral_count": counts.get("neutral", 0),
        "support_strength": round(sum(edge.strength for edge in edges if edge.stance == "support"), 3),
        "oppose_strength": round(sum(edge.strength for edge in edges if edge.stance == "oppose"), 3),
        "source_diversity": len({edge.document_id for edge in edges if edge.document_id is not None}),
    }


def render_hypothesis_brief(hypothesis: Hypothesis, edges: list[EvidenceEdge]) -> str:
    support = [edge for edge in edges if edge.stance == "support"]
    oppose = [edge for edge in edges if edge.stance == "oppose"]
    neutral = [edge for edge in edges if edge.stance == "neutral"]

    def bullets(items: list[EvidenceEdge]) -> str:
        if not items:
            return "- None recorded."
        return "\n".join(
            f"- [evidence:{item.id}; document:{item.document_id or 'n/a'}; strength:{item.strength:.2f}] {item.summary}"
            for item in items
        )

    return f"""# Hypothesis brief

## Claim
{hypothesis.claim}

## State
- Scope: `{hypothesis.scope}`
- Confidence: `{hypothesis.confidence:.3f}`
- Revision: `{hypothesis.revision}`
- Status: `{hypothesis.status}`

## Supporting evidence
{bullets(support)}

## Counter-evidence
{bullets(oppose)}

## Neutral or contextual evidence
{bullets(neutral)}

## Open questions
- Is the evidence independent, or does it repeat one source?
- What observation would falsify this claim?
- Does the evidence describe intent, execution, or an actual outcome?
"""


def render_decision_memo(hypothesis: Hypothesis, edges: list[EvidenceEdge]) -> str:
    stats = evidence_summary(edges)
    support = sorted(
        (edge for edge in edges if edge.stance == "support"),
        key=lambda edge: edge.strength,
        reverse=True,
    )[:5]
    oppose = sorted(
        (edge for edge in edges if edge.stance == "oppose"),
        key=lambda edge: edge.strength,
        reverse=True,
    )[:5]

    def concise(items: list[EvidenceEdge]) -> str:
        return "\n".join(f"- {item.summary}" for item in items) or "- No evidence recorded."

    recommendation = (
        "Proceed only as a monitored hypothesis; collect independent counter-evidence before a high-impact decision."
        if int(stats["oppose_count"]) == 0 or int(stats["source_diversity"]) < 2
        else "Use as a decision input with explicit uncertainty and a defined falsification checkpoint."
    )
    return f"""# Decision memo

## Working hypothesis
{hypothesis.claim}

## Recommendation
{recommendation}

## Evidence posture
- Confidence: `{hypothesis.confidence:.3f}`
- Supporting items: `{stats["support_count"]}`
- Opposing items: `{stats["oppose_count"]}`
- Independent documents: `{stats["source_diversity"]}`

## Strongest support
{concise(support)}

## Strongest challenge
{concise(oppose)}

## Required next decision checkpoint
Define the observation, deadline, and owner that would confirm, weaken, or retire this hypothesis.
"""
