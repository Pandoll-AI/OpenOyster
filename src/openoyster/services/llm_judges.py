from __future__ import annotations

import json
import re
from typing import Any

from openoyster.deliberation_contracts import MIN_QUOTE_CHARS

from ..llm_contracts import ExtractionUnavailable
from ..utils import normalise_text
from .chunking import split_sentences

_NEW_CLAIM_RE = re.compile(r"\[NEW CLAIM\]\s*scope: (?P<scope>.*?)\s*claim: (?P<claim>.*?)\s*\[/NEW CLAIM\]", re.S)
_CANDIDATE_RE = re.compile(
    r"\[CANDIDATE (?P<index>\d+)\]\s*id: (?P<id>\d+)\s*scope: (?P<scope>.*?)\s*claim: (?P<claim>.*?)\s*\[/CANDIDATE (?P=index)\]",
    re.S,
)
_CHUNK_RE = re.compile(r"\[CHUNK (?P<index>\d+)\]\n(?P<text>.*?)\n\[/CHUNK (?P=index)\]", re.S)
_EVIDENCE_QUOTE_RE = re.compile(r"\[EVIDENCE QUOTE\]\n(?P<quote>.*?)\n\[/EVIDENCE QUOTE\]", re.S)


_PACK_EVIDENCE_RE = re.compile(
    r"\[EVIDENCE id=(?P<local>[^\s\]]+) global=(?P<global>[^\s\]]+)\]",
    re.S,
)
_DELIBERATION_SNAPSHOT_RE = re.compile(
    r"\[SNAPSHOT key=(?P<key>[^\s\]]+)[^\]]*\](?P<body>.*?)\[/SNAPSHOT key=(?P=key)\]",
    re.S,
)


def _extract_pack_evidence_ids(prompt: str) -> list[str]:
    """Extract global evidence ids from untrusted Pack prompt blocks."""
    ids = [match.group("global") for match in _PACK_EVIDENCE_RE.finditer(prompt)]
    return list(dict.fromkeys(ids))


def _extract_deliberation_snapshot_keys(prompt: str) -> list[str]:
    """Extract evidence snapshot keys from deliberation prompt blocks."""
    keys = [match.group("key") for match in _DELIBERATION_SNAPSHOT_RE.finditer(prompt)]
    return list(dict.fromkeys(keys))


def _deliberation_quote_from_prompt(prompt: str, snapshot_key: str | None) -> str:
    """Pick a verbatim quote from a snapshot body, preferring known fixture text."""
    preferred = "This source supports this claim."
    if preferred in prompt:
        return preferred
    for match in _DELIBERATION_SNAPSHOT_RE.finditer(prompt):
        if snapshot_key is not None and match.group("key") != snapshot_key:
            continue
        body = match.group("body")
        # Prefer JSON "text" field when present.
        text_match = re.search(r'"text"\s*:\s*"(?P<text>(?:\\.|[^"\\])*)"', body)
        if text_match:
            raw = text_match.group("text").encode("utf-8").decode("unicode_escape")
            if raw and len(raw.strip()) >= MIN_QUOTE_CHARS:
                # Use a stable substring that still meets MIN_QUOTE_CHARS.
                return raw if len(raw) <= 120 else raw[:120]
        stripped = body.strip()
        if len(stripped) >= MIN_QUOTE_CHARS:
            return stripped[:120]
    return preferred


def _mission_constraint_count_from_prompt(prompt: str) -> int:
    """Count Mission.constraints embedded in the deliberation prompt control block."""
    match = re.search(r'"constraints"\s*:\s*(\[(?:[^\[\]]|\[(?:[^\[\]])*\])*\])', prompt)
    if match is None:
        return 0
    try:
        constraints = json.loads(match.group(1))
    except json.JSONDecodeError:
        return 0
    if not isinstance(constraints, list):
        return 0
    return len(constraints)


def _stub_constraint_judgements(constraint_count: int) -> list[dict[str, Any]]:
    """Emit one satisfied judgement per mission constraint (exact coverage)."""
    judgements: list[dict[str, Any]] = []
    for index in range(constraint_count):
        pointer = f"/constraints/{index}"
        judgements.append(
            {
                "constraint_index": index,
                "satisfied": True,
                "rationale": {
                    "text": f"Constraint {index} is satisfied under Pack evidence",
                    "classification": "mission_control",
                    "mission_pointer": pointer,
                },
            }
        )
    return judgements


def stub_query_json(prompt: str, stage: str) -> dict[str, Any]:
    match stage:
        case "merge_judge":
            return _stub_merge_judge(prompt)
        case "stance_judge":
            return _stub_stance_judge(prompt)
        case "oppose_verify":
            return _stub_oppose_verify(prompt)
        case "gold_label":
            return _stub_gold_label(prompt)
        case "pack_answer":
            return _stub_pack_answer(prompt)
        case "retrieval_query_expansion":
            return _stub_retrieval_query_expansion(prompt)
        case "deliberation_beliefs":
            return _stub_deliberation_beliefs(prompt)
        case "deliberation_options":
            return _stub_deliberation_options(prompt)
        case "deliberation_scenarios":
            return _stub_deliberation_scenarios(prompt)
        case "deliberation_critic":
            return _stub_deliberation_critic(prompt)
        case "deliberation_decision":
            return _stub_deliberation_decision(prompt)
        case "flip_confirm":
            return _stub_flip_confirm(prompt)
        case _:
            raise ExtractionUnavailable(f"stub does not implement JSON stage: {stage}")


def _stub_retrieval_query_expansion(prompt: str) -> dict[str, Any]:
    """Deterministic expansion: question whitespace tokens + pack title tokens.

    No fixed bilingual dictionary. Useful for tests that inject matchable terms
    via the expansion provider override; default path stays conservative.
    """
    queries: list[str] = []
    # decision_question line (control input).
    dq_match = re.search(r"decision_question:\s*(.+)", prompt)
    if dq_match:
        question = dq_match.group(1).strip()
        if question:
            queries.append(question)
            # Whitespace split into shorter alternate queries (cap later).
            for token in question.split():
                token = token.strip()
                if len(token) > 1:
                    queries.append(token)
    # pack title tokens from pack_metadata JSON blob in the prompt.
    for title_match in re.finditer(r'"title"\s*:\s*"(?P<title>(?:\\.|[^"\\])*)"', prompt):
        title = title_match.group("title").encode("utf-8").decode("unicode_escape")
        if title.strip():
            queries.append(title.strip())
            for token in title.split():
                token = token.strip()
                if len(token) > 1:
                    queries.append(token)
    # De-dupe while preserving order; hard-cap at 5.
    deduped: list[str] = []
    seen: set[str] = set()
    for item in queries:
        text = item.strip()
        if not text or text in seen:
            continue
        if len(text) > 200:
            text = text[:200]
        seen.add(text)
        deduped.append(text)
        if len(deduped) >= 5:
            break
    return {"queries": deduped}


def _stub_deliberation_anchor(prompt: str) -> dict[str, str]:
    keys = _extract_deliberation_snapshot_keys(prompt)
    snap = keys[0] if keys else "snap:1"
    return {
        "evidence_snapshot_id": snap,
        "quote": _deliberation_quote_from_prompt(prompt, snap),
    }


def _stub_deliberation_beliefs(prompt: str) -> dict[str, Any]:
    anchor = _stub_deliberation_anchor(prompt)
    return {
        "beliefs": [
            {
                "local_key": "b1",
                "statement": {
                    "text": "The source supports this claim.",
                    "classification": "grounded_fact",
                    "anchors": [anchor],
                },
                "status": "supported",
                "supporting_anchors": [anchor],
                "opposing_anchors": [],
                "assumptions": [],
                "gaps": [],
                "invalidation_conditions": ["If the supporting source text is withdrawn."],
            }
        ]
    }


def _stub_deliberation_options(prompt: str) -> dict[str, Any]:
    judgements = _stub_constraint_judgements(_mission_constraint_count_from_prompt(prompt))
    return {
        "options": [
            {
                "local_key": "opt_accept",
                "label": {
                    "text": "Accept the claim as decision basis",
                    "classification": "proposal",
                    "mission_pointer": "/decision_question",
                },
                "viable": True,
                "constraint_judgements": judgements,
                "supporting_belief_keys": ["b1"],
                "opposing_belief_keys": [],
                "risks": [],
                "reversibility": "high",
                "expected_outcome": {
                    "text": "Proceed with acceptance under Pack support",
                    "classification": "proposal",
                    "mission_pointer": "/goal",
                },
            },
            {
                "local_key": "opt_defer",
                "label": {
                    "text": "Defer the decision",
                    "classification": "proposal",
                    "mission_pointer": "/goal",
                },
                "viable": True,
                "constraint_judgements": list(judgements),
                "supporting_belief_keys": [],
                "opposing_belief_keys": [],
                "risks": [],
                "reversibility": "high",
                "expected_outcome": {
                    "text": "Wait for more evidence before accepting",
                    "classification": "proposal",
                    "mission_pointer": "/goal",
                },
            },
        ]
    }


def _stub_deliberation_scenarios(prompt: str) -> dict[str, Any]:
    anchor = _stub_deliberation_anchor(prompt)
    return {
        "scenarios": [
            {
                "local_key": "s_accept_expected",
                "option_key": "opt_accept",
                "kind": "expected",
                "projected_outcome": {
                    "text": "Claim holds under normal conditions",
                    "classification": "grounded_inference",
                    "anchors": [anchor],
                },
                "facts": [
                    {
                        "text": "The source supports this claim.",
                        "classification": "grounded_fact",
                        "anchors": [anchor],
                    }
                ],
                "inferences": [
                    {
                        "text": "Support implies acceptance risk is limited",
                        "classification": "grounded_inference",
                        "anchors": [anchor],
                    }
                ],
                "assumptions": [],
            },
            {
                "local_key": "s_accept_adverse",
                "option_key": "opt_accept",
                "kind": "adverse",
                "projected_outcome": {
                    "text": "Claim support is later withdrawn",
                    "classification": "grounded_inference",
                    "anchors": [anchor],
                },
                "facts": [],
                "inferences": [],
                "assumptions": [
                    {
                        "text": "Source integrity remains stable",
                        "classification": "assumption",
                        "assumption_marker": True,
                        "verification_question": "Is the source still valid?",
                    }
                ],
            },
            {
                "local_key": "s_defer_expected",
                "option_key": "opt_defer",
                "kind": "expected",
                "projected_outcome": {
                    "text": "Deferral preserves reversibility while evidence remains",
                    "classification": "grounded_inference",
                    "anchors": [anchor],
                },
                "facts": [],
                "inferences": [
                    {
                        "text": "Waiting avoids premature commitment",
                        "classification": "grounded_inference",
                        "anchors": [anchor],
                    }
                ],
                "assumptions": [],
            },
            {
                "local_key": "s_defer_adverse",
                "option_key": "opt_defer",
                "kind": "adverse",
                "projected_outcome": {
                    "text": "Deferral delays action if the claim remains valid",
                    "classification": "grounded_inference",
                    "anchors": [anchor],
                },
                "facts": [],
                "inferences": [],
                "assumptions": [
                    {
                        "text": "Decision timing is not critical",
                        "classification": "assumption",
                        "assumption_marker": True,
                        "verification_question": "Is there a hard deadline?",
                    }
                ],
            },
        ]
    }


def _stub_deliberation_critic(prompt: str) -> dict[str, Any]:
    del prompt
    return {
        "verdict": "pass",
        "issues": [],
        "findings": [
            {
                "text": "Options cover acceptance and deferral with Pack-grounded support",
                "classification": "structural",
                "issue_code": "coverage_ok",
                "artifact_ref": "options:opt_accept",
            }
        ],
    }


def _stub_deliberation_decision(prompt: str) -> dict[str, Any]:
    anchor = _stub_deliberation_anchor(prompt)
    return {
        "outcome": "select",
        "selected_option_key": "opt_accept",
        "rationale": {
            "text": "Supported claim and critic passed",
            "classification": "grounded_inference",
            "anchors": [anchor],
        },
        "abstention_reasons": [],
        "flip_conditions": [
            {
                "local_key": "flip1",
                "condition": {
                    "text": "If supporting evidence is invalidated",
                    "classification": "proposal",
                    "mission_pointer": "/goal",
                },
            }
        ],
        "knowledge_requests": [],
    }


def _stub_flip_confirm(prompt: str) -> dict[str, Any]:
    """Deterministic flip_confirm stub: related=true with a real evidence quote.

    Parses the first ``[EVIDENCE id=...]...[/EVIDENCE]`` body and returns a
    verbatim substring meeting MIN_QUOTE_CHARS so tests can assert
    llm_supported. Force unsupported by overriding the provider in tests
    (related=false or a fabricated quote).
    """
    if "FLIP_CONFIRM_UNRELATED" in prompt:
        return {"related": False, "quote": None}
    if "FLIP_CONFIRM_FAKE_QUOTE" in prompt:
        return {"related": True, "quote": "this quote is not in any evidence body"}
    match = re.search(
        r"\[EVIDENCE id=(?P<id>[^\]]+)\]\n(?P<body>.*?)\n\[/EVIDENCE\]",
        prompt,
        re.S,
    )
    if match is None:
        return {"related": False, "quote": None}
    body = match.group("body").strip()
    if len(body) < MIN_QUOTE_CHARS:
        return {"related": False, "quote": None}
    # First contiguous phrase long enough for the quote gate.
    quote = body if len(body) <= 120 else body[:120]
    if len(quote.strip()) < MIN_QUOTE_CHARS:
        return {"related": False, "quote": None}
    return {"related": True, "quote": quote}


def _stub_pack_answer(prompt: str) -> dict[str, Any]:
    evidence_ids = _extract_pack_evidence_ids(prompt)
    folded = prompt.casefold()
    if "INVENT_UNKNOWN_EVIDENCE" in prompt or "invent_unknown_evidence" in folded:
        return {
            "status": "supported",
            "answer": "Fabricated answer with unknown citation.",
            "citations": ["evidence:does-not-exist"],
        }
    if not evidence_ids:
        return {"status": "unknown", "answer": "unknown", "citations": []}
    # Prefer claim-related text when present in untrusted blocks.
    answer = "The source supports this claim."
    if "supports this claim" in folded:
        answer = "The source supports this claim."
    return {
        "status": "supported",
        "answer": answer,
        "citations": [evidence_ids[0]],
    }


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


def _stub_gold_label(prompt: str) -> dict[str, Any]:
    match = _EVIDENCE_QUOTE_RE.search(prompt)
    quote = match.group("quote") if match else prompt
    folded = quote.casefold()
    contradicts = "no evidence" in folded or "반대" in quote
    return {
        "contradicts": contradicts,
        "reasoning": "deterministic stub counter audit from evidence quote marker",
        "model": "test-double",
    }


def _stub_oppose_verify(prompt: str) -> dict[str, Any]:
    return {
        "contradicts": "VERIFY_REJECT" not in prompt,
        "reasoning": "deterministic stub oppose verifier from prompt marker",
        "model": "test-double",
    }
