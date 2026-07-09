from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm import LLMProvider
from ..llm_contracts import ExtractionUnavailable
from ..models import DecisionTrace, Hypothesis
from ..schemas import HypothesisDraft
from ..scoring import jaccard, tokenize
from ..utils import normalise_text, stable_hash
from .prompts import build_merge_judge_prompt


@dataclass(frozen=True, slots=True)
class MergeDecision:
    hypothesis: Hypothesis | None
    relation: str
    reasoning: str
    candidate_ids: list[int]
    match_index: int | None
    score: float
    judge_unavailable: bool = False


def match_hypothesis(
    session: Session,
    draft: HypothesisDraft,
    top_k: int,
    provider: LLMProvider,
) -> MergeDecision:
    claim_hash = stable_hash(normalise_text(draft.claim).casefold())
    exact = session.scalar(
        select(Hypothesis).where(
            Hypothesis.scope == draft.scope,
            Hypothesis.claim_hash == claim_hash,
        )
    )
    if exact:
        return MergeDecision(
            hypothesis=exact,
            relation="same",
            reasoning="claim_hash exact match",
            candidate_ids=[exact.id],
            match_index=0,
            score=1.0,
        )
    ranked = _rank_candidates(session, draft, top_k)
    if not ranked:
        return MergeDecision(
            hypothesis=None,
            relation="different",
            reasoning="no active same-scope candidates shared tokens",
            candidate_ids=[],
            match_index=None,
            score=0.0,
        )
    try:
        payload = provider.query_json(
            build_merge_judge_prompt(
                new_claim=draft.claim,
                new_scope=draft.scope,
                candidates=[
                    {"id": candidate.id, "scope": candidate.scope, "claim": candidate.claim}
                    for candidate, _, _ in ranked
                ],
            ),
            "merge_judge",
        )
    except ExtractionUnavailable:
        return MergeDecision(
            hypothesis=None,
            relation="unavailable",
            reasoning="merge_judge_unavailable",
            candidate_ids=[candidate.id for candidate, _, _ in ranked],
            match_index=None,
            score=max((similarity for _, _, similarity in ranked), default=0.0),
            judge_unavailable=True,
        )

    relation = _merge_relation(payload.get("relation"))
    match_index = _merge_match_index(payload.get("match_index"), len(ranked))
    reasoning = _reasoning_text(payload.get("reasoning"))
    if relation == "same" and match_index is not None:
        candidate, _, similarity = ranked[match_index]
        return MergeDecision(
            hypothesis=candidate,
            relation=relation,
            reasoning=reasoning,
            candidate_ids=[item.id for item, _, _ in ranked],
            match_index=match_index,
            score=similarity,
        )
    return MergeDecision(
        hypothesis=None,
        relation=relation,
        reasoning=reasoning,
        candidate_ids=[candidate.id for candidate, _, _ in ranked],
        match_index=match_index,
        score=max((similarity for _, _, similarity in ranked), default=0.0),
    )


def record_merge_decision(
    session: Session,
    *,
    decision: MergeDecision,
    subject_id: int,
    policy_version: str,
    event_id: int,
) -> None:
    session.add(
        DecisionTrace(
            decision_type="merge_decision",
            subject_type="hypothesis",
            subject_id=subject_id,
            policy_version=policy_version,
            features_json={
                "candidate_ids": decision.candidate_ids,
                "match_index": decision.match_index,
                "relation": decision.relation,
                "reasoning": decision.reasoning[:500],
                "judge_unavailable": decision.judge_unavailable,
            },
            score=decision.score,
            threshold=1.0,
            decision=decision.hypothesis is not None,
            metadata_json={"event_id": event_id},
        )
    )


def _rank_candidates(
    session: Session,
    draft: HypothesisDraft,
    top_k: int,
) -> list[tuple[Hypothesis, int, float]]:
    candidates = list(
        session.scalars(
            select(Hypothesis).where(
                Hypothesis.scope == draft.scope,
                Hypothesis.status.in_(["active", "mature", "challenged"]),
            )
        )
    )
    draft_tokens = tokenize(draft.claim)
    scored = [
        (
            candidate,
            len(draft_tokens & tokenize(candidate.claim)),
            jaccard(candidate.claim, draft.claim),
        )
        for candidate in candidates
        if draft_tokens & tokenize(candidate.claim)
    ]
    return sorted(scored, key=lambda item: (item[1], item[2], -item[0].id), reverse=True)[:top_k]


def _merge_relation(value: Any) -> str:
    if value in {"same", "related", "different"}:
        return str(value)
    return "different"


def _merge_match_index(value: Any, candidate_count: int) -> int | None:
    if isinstance(value, int) and 0 <= value < candidate_count:
        return value
    return None


def _reasoning_text(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "merge_judge returned no reasoning"
