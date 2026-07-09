from __future__ import annotations

import re
from typing import Any

from ..llm_contracts import ExtractionUnavailable
from ..utils import normalise_text
from .chunking import split_sentences

_NEW_CLAIM_RE = re.compile(r"\[NEW CLAIM\]\s*scope: (?P<scope>.*?)\s*claim: (?P<claim>.*?)\s*\[/NEW CLAIM\]", re.S)
_CANDIDATE_RE = re.compile(
    r"\[CANDIDATE (?P<index>\d+)\]\s*id: (?P<id>\d+)\s*scope: (?P<scope>.*?)\s*claim: (?P<claim>.*?)\s*\[/CANDIDATE (?P=index)\]",
    re.S,
)
_CHUNK_RE = re.compile(r"\[CHUNK (?P<index>\d+)\]\n(?P<text>.*?)\n\[/CHUNK (?P=index)\]", re.S)


def stub_query_json(prompt: str, stage: str) -> dict[str, Any]:
    match stage:
        case "merge_judge":
            return _stub_merge_judge(prompt)
        case "stance_judge":
            return _stub_stance_judge(prompt)
        case _:
            raise ExtractionUnavailable(f"stub does not implement JSON stage: {stage}")


def _stub_merge_judge(prompt: str) -> dict[str, Any]:
    new_claim_match = _NEW_CLAIM_RE.search(prompt)
    new_claim = _stub_claim_key(new_claim_match.group("claim")) if new_claim_match else ""
    for match in _CANDIDATE_RE.finditer(prompt):
        candidate_claim = _stub_claim_key(match.group("claim"))
        if candidate_claim == new_claim:
            return {
                "match_index": int(match.group("index")),
                "relation": "same",
                "reasoning": "deterministic stub matched normalized claim text",
            }
    return {
        "match_index": None,
        "relation": "different",
        "reasoning": "deterministic stub found no normalized claim match",
    }


def _stub_claim_key(text: str) -> str:
    return " ".join(re.findall(r"[A-Za-z0-9가-힣_\-]+", normalise_text(text).casefold()))


def _stub_stance_judge(prompt: str) -> dict[str, Any]:
    judgements: list[dict[str, Any]] = []
    for match in _CHUNK_RE.finditer(prompt):
        chunk_text = match.group("text")
        folded = chunk_text.casefold()
        stance = "oppose" if "반대" in chunk_text or "no evidence" in folded else "support"
        sentences = split_sentences(chunk_text) or [normalise_text(chunk_text)]
        quoted = sentences[0] if sentences else ""
        if "bad quote" in folded:
            quoted = "not a verbatim quote"
        judgements.append(
            {
                "chunk_index": int(match.group("index")),
                "stance": stance,
                "quoted_evidence": quoted,
                "strength": 0.7,
                "reasoning": "deterministic stub stance from chunk text marker",
            }
        )
    return {"judgements": judgements}
