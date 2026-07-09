from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..llm import LLMProvider
from ..llm_contracts import ExtractionUnavailable
from ..scoring import clamp
from .prompts import build_stance_judge_prompt
from .retrieval import RetrievalHit


@dataclass(frozen=True)
class StanceJudgement:
    chunk_index: int
    stance: str
    quoted_evidence: str
    strength: float
    reasoning: str


def judge_stance(
    provider: LLMProvider,
    hypothesis_claim: str,
    hits: list[RetrievalHit],
) -> tuple[dict[int, StanceJudgement], int]:
    if not hits:
        return {}, 0
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
    for raw in raw_judgements:
        judgement = _parse_stance_judgement(raw, len(hits))
        hit = hits[judgement.chunk_index]
        if not judgement.quoted_evidence or judgement.quoted_evidence not in hit.text:
            quote_misses += 1
            continue
        judgements[judgement.chunk_index] = judgement
    return judgements, quote_misses


def _parse_stance_judgement(raw: Any, hit_count: int) -> StanceJudgement:
    if not isinstance(raw, dict):
        raise ExtractionUnavailable("stance_judge judgement must be an object")
    chunk_index = raw.get("chunk_index")
    if not isinstance(chunk_index, int) or chunk_index < 0 or chunk_index >= hit_count:
        raise ExtractionUnavailable("stance_judge judgement has invalid chunk_index")
    stance = raw.get("stance")
    if stance not in {"support", "oppose", "unrelated"}:
        raise ExtractionUnavailable("stance_judge judgement has invalid stance")
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
