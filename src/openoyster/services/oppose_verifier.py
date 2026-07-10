from __future__ import annotations

from dataclasses import dataclass

from ..llm import LLMProvider
from ..llm_contracts import ExtractionUnavailable
from .prompts import build_counter_audit_prompt


@dataclass(frozen=True, slots=True)
class OpposeVerificationRequest:
    hypothesis_claim: str
    evidence_quote: str
    evidence_summary: str
    source_text: str


@dataclass(frozen=True, slots=True)
class OpposeVerificationResult:
    contradicts: bool
    reasoning: str


def verify_oppose(
    provider: LLMProvider,
    request: OpposeVerificationRequest,
) -> OpposeVerificationResult:
    payload = provider.query_json(
        build_counter_audit_prompt(
            hypothesis_claim=request.hypothesis_claim,
            evidence_quote=request.evidence_quote,
            evidence_summary=request.evidence_summary,
            source_text=request.source_text,
        ),
        "oppose_verify",
    )
    contradicts = payload.get("contradicts")
    if not isinstance(contradicts, bool):
        raise ExtractionUnavailable("oppose_verify response must contain a boolean contradicts field")
    reasoning = payload.get("reasoning")
    return OpposeVerificationResult(
        contradicts=contradicts,
        reasoning=reasoning if isinstance(reasoning, str) else "",
    )
