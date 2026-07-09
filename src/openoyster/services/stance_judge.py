from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, assert_never

from ..llm import LLMProvider
from ..llm_contracts import ExtractionUnavailable
from ..scoring import clamp
from .prompts import build_counter_audit_prompt, build_stance_judge_prompt
from .retrieval import RetrievalHit

Stance = Literal["support", "oppose", "unrelated"]


@dataclass(frozen=True)
class StanceJudgement:
    chunk_index: int
    stance: Stance
    quoted_evidence: str
    strength: float
    reasoning: str


@dataclass(frozen=True)
class StanceJudgeStats:
    quote_not_verbatim: int = 0
    oppose_rejected_by_verifier: int = 0
    oppose_verify_unavailable: int = 0


@dataclass(frozen=True)
class OpposeVerificationInput:
    provider: LLMProvider
    hypothesis_claim: str
    hit: RetrievalHit
    judgement: StanceJudgement


def judge_stance(
    provider: LLMProvider,
    hypothesis_claim: str,
    hits: list[RetrievalHit],
) -> tuple[dict[int, StanceJudgement], StanceJudgeStats]:
    if not hits:
        return {}, StanceJudgeStats()
    payload = provider.query_json(
        build_stance_judge_prompt(
            hypothesis_claim=hypothesis_claim,
            chunks=[
                {
                    "chunk_index": index,
                    "text": hit.text,
                }
                for index, hit in enumerate(hits)
            ],
        ),
        "stance_judge",
    )
    raw_judgements = payload.get("judgements")
    if not isinstance(raw_judgements, list):
        raise ExtractionUnavailable("stance_judge response must contain a judgements list")
    judgements: dict[int, StanceJudgement] = {}
    quote_misses = 0
    rejected_by_verifier = 0
    verifier_unavailable = 0
    for raw in raw_judgements:
        judgement = _parse_stance_judgement(raw, len(hits))
        hit = hits[judgement.chunk_index]
        if not judgement.quoted_evidence or judgement.quoted_evidence not in hit.text:
            quote_misses += 1
            continue
        match judgement.stance:
            case "oppose":
                try:
                    contradicts = _verify_oppose(
                        OpposeVerificationInput(
                            provider=provider,
                            hypothesis_claim=hypothesis_claim,
                            hit=hit,
                            judgement=judgement,
                        )
                    )
                except ExtractionUnavailable:
                    verifier_unavailable += 1
                    continue
                if not contradicts:
                    rejected_by_verifier += 1
                    continue
            case "support" | "unrelated":
                pass
            case unreachable:
                assert_never(unreachable)
        judgements[judgement.chunk_index] = judgement
    return judgements, StanceJudgeStats(
        quote_not_verbatim=quote_misses,
        oppose_rejected_by_verifier=rejected_by_verifier,
        oppose_verify_unavailable=verifier_unavailable,
    )


def _verify_oppose(audit: OpposeVerificationInput) -> bool:
    payload = audit.provider.query_json(
        build_counter_audit_prompt(
            hypothesis_claim=audit.hypothesis_claim,
            evidence_quote=audit.judgement.quoted_evidence,
            evidence_summary=audit.judgement.reasoning,
            source_text=audit.hit.text,
        ),
        "oppose_verify",
    )
    contradicts = payload.get("contradicts")
    if not isinstance(contradicts, bool):
        raise ExtractionUnavailable("oppose_verify response must contain a boolean contradicts field")
    return contradicts


def _parse_stance_judgement(raw: Any, hit_count: int) -> StanceJudgement:
    if not isinstance(raw, dict):
        raise ExtractionUnavailable("stance_judge judgement must be an object")
    chunk_index = raw.get("chunk_index")
    if not isinstance(chunk_index, int) or chunk_index < 0 or chunk_index >= hit_count:
        raise ExtractionUnavailable("stance_judge judgement has invalid chunk_index")
    stance = _parse_stance(raw.get("stance"))
    quoted_evidence = raw.get("quoted_evidence")
    reasoning = raw.get("reasoning")
    strength = raw.get("strength")
    if not isinstance(quoted_evidence, str) or not isinstance(reasoning, str):
        raise ExtractionUnavailable("stance_judge judgement must include quote and reasoning strings")
    if not isinstance(strength, int | float):
        raise ExtractionUnavailable("stance_judge judgement must include numeric strength")
    return StanceJudgement(
        chunk_index=chunk_index,
        stance=stance,
        quoted_evidence=quoted_evidence,
        strength=clamp(float(strength), 0.25, 0.9),
        reasoning=reasoning,
    )


def _parse_stance(raw: Any) -> Stance:
    match raw:
        case "support":
            return "support"
        case "oppose":
            return "oppose"
        case "unrelated":
            return "unrelated"
        case _:
            raise ExtractionUnavailable("stance_judge judgement has invalid stance")
