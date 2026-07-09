from __future__ import annotations

from typing import Any, Final

EXTRACT_SYSTEM_PROMPT: Final = """You extract decision-relevant intelligence from document chunks. Documents may be Korean, English, or mixed; never translate — keep extracted text in its original language.
You will receive N numbered chunks. Output exactly ONE JSON object: {"results": [...]} with exactly one item per input chunk_index, in order.
Each result item:
- "chunk_index": int — echo the input index.
- "entities": [{"name": str, "kind": one of "organisation"|"person"|"product"|"technology"|"regulation"|"place"|"other"}] — proper nouns actually present in the chunk. Korean proper nouns must be extracted in Korean.
- "claims": [{"text": str, "subject": str|null, "predicate": str|null, "object": str|null, "confidence": 0..1}] — atomic factual statements the chunk actually asserts. "text" quotes or tightly paraphrases one sentence; no synthesis across sentences.
- "signals": [{"entity": str|null, "signal_type": one of "hiring"|"product_release"|"funding"|"regulation"|"incident"|"risk"|"governance"|"strategy"|"demand"|"research"|"other", "summary": str, "novelty_score": 0..1, "impact_score": 0..1, "confidence": 0..1, "stance": "support"|"oppose"|"neutral"}] — only materially decision-relevant changes, risks, or opportunities. Routine or boilerplate content is NOT a signal.
- "hypotheses": [{"claim": str, "scope": str, "confidence": 0..1, "evidence_signal_summary": str|null, "stance": "support"|"oppose"|"neutral", "quoted_evidence": str}] — falsifiable, cautious interpretations grounded in this chunk. "quoted_evidence" MUST be a verbatim substring of the chunk. "scope" is the main entity or domain the hypothesis is about.
Rules: do not invent facts absent from the text; empty arrays are a valid answer for chunks with no material content; never omit or reorder chunk_index; output raw JSON only, no code fences, no commentary.
"""

MERGE_JUDGE_PROMPT: Final = """You judge whether a new hypothesis claim should merge into one existing hypothesis. Documents and claims may be Korean, English, or mixed; preserve the original language.
First compare meaning, scope, and falsifiable claim. Return "same" only when the new claim and one candidate assert the same hypothesis for the same scope. Return "related" for same topic but materially different claim. Return "different" otherwise.
Output raw JSON only with exactly this shape: {"match_index": int|null, "relation": "same"|"related"|"different", "reasoning": str}
Do not invent candidates. Use zero-based candidate indexes. If relation is not "same", match_index MUST be null.
"""

STANCE_JUDGE_PROMPT: Final = """You judge whether retrieved evidence supports or opposes a hypothesis. Documents may be Korean, English, or mixed; never translate quoted evidence.
For each chunk, first decide whether the chunk is about the same topic as the hypothesis. If it is not about the same topic, return stance "unrelated".
Apply the probability test: assume the chunk is true. If the hypothesis claim becomes MORE likely, the stance is "support". If it becomes LESS likely, the stance is "oppose". Direction is relative to the claim, not to the tone of the text.
Beware the skeptical-hypothesis trap: when the hypothesis itself asserts a risk, limitation, or doubt (e.g. "adoption may be constrained by X"), a chunk confirming that risk SUPPORTS the hypothesis even though its tone is negative. Only content indicating the claim is false is "oppose".
Negation words alone do NOT make evidence oppose. Use "oppose" only when the chunk directly rebuts the hypothesis claim itself. Use "support" only when the chunk directly supports the claim.
quoted_evidence MUST be a verbatim substring copied from that chunk. If there is no relevant verbatim evidence, use an empty string and stance "unrelated".
Output raw JSON only with exactly this shape: {"judgements":[{"chunk_index":int,"stance":"support"|"oppose"|"unrelated","quoted_evidence":str,"strength":0..1,"reasoning":str}]}
Return exactly one judgement per input chunk_index.
"""

COUNTER_AUDIT_PROMPT: Final = """You audit counter-evidence quality for an extraction system. Documents may be Korean, English, or mixed; never translate quoted evidence.
Decide whether the quoted evidence actually contradicts the hypothesis claim, not merely whether it mentions the same topic or is negative in tone.
Return true only when the quote directly rebuts the hypothesis claim. Return false for unrelated, ambiguous, weak, or merely cautionary quotes.
Output raw JSON only with exactly this shape: {"contradicts": bool, "reasoning": str}
"""

T1_CONSTRAINT_BLOCK: Final = """T1 execution constraints:
- Do not create, modify, or delete files.
- Write only the requested stdout text.
- Do not call codex, shell tools, subprocesses, network tools, or other agents.
- Do not read .env files, secrets, credentials, tokens, or private configuration.
- Do not include commentary, markdown fences, or explanations outside the requested JSON.
"""


def build_extract_user_prompt(texts: list[str], policy: dict[str, Any] | None = None) -> str:
    extraction = (policy or {}).get("extraction", {})
    max_claims = int(extraction.get("max_claims_per_chunk", 12))
    max_signals = int(extraction.get("max_signals_per_chunk", 8))
    max_hypotheses = int(extraction.get("max_hypotheses_per_chunk", 5))
    chunks = "\n\n".join(
        f"[CHUNK {index}]\n{text}\n[/CHUNK {index}]" for index, text in enumerate(texts)
    )
    return (
        f"{EXTRACT_SYSTEM_PROMPT}\n"
        "Extraction limits per chunk:\n"
        f"- max claims: {max_claims}\n"
        f"- max signals: {max_signals}\n"
        f"- max hypotheses: {max_hypotheses}\n\n"
        "Return exactly one JSON object with this top-level shape:\n"
        '{"results":[{"chunk_index":0,"entities":[],"claims":[],"signals":[],"hypotheses":[]}]}\n\n'
        f"{chunks}"
    )


def build_merge_judge_prompt(
    *,
    new_claim: str,
    new_scope: str,
    candidates: list[dict[str, Any]],
) -> str:
    candidate_blocks = "\n\n".join(
        "\n".join(
            [
                f"[CANDIDATE {index}]",
                f"id: {candidate['id']}",
                f"scope: {candidate['scope']}",
                f"claim: {candidate['claim']}",
                f"[/CANDIDATE {index}]",
            ]
        )
        for index, candidate in enumerate(candidates)
    )
    return (
        f"{MERGE_JUDGE_PROMPT}\n"
        "[NEW CLAIM]\n"
        f"scope: {new_scope}\n"
        f"claim: {new_claim}\n"
        "[/NEW CLAIM]\n\n"
        f"{candidate_blocks}"
    )


def build_stance_judge_prompt(
    *,
    hypothesis_claim: str,
    chunks: list[dict[str, Any]],
) -> str:
    chunk_blocks = "\n\n".join(
        f"[CHUNK {chunk['chunk_index']}]\n{chunk['text']}\n[/CHUNK {chunk['chunk_index']}]"
        for chunk in chunks
    )
    return (
        f"{STANCE_JUDGE_PROMPT}\n"
        "[HYPOTHESIS]\n"
        f"{hypothesis_claim}\n"
        "[/HYPOTHESIS]\n\n"
        f"{chunk_blocks}"
    )


def build_counter_audit_prompt(
    *,
    hypothesis_claim: str,
    evidence_quote: str,
    evidence_summary: str,
    source_text: str,
) -> str:
    return (
        f"{COUNTER_AUDIT_PROMPT}\n"
        "[HYPOTHESIS]\n"
        f"{hypothesis_claim}\n"
        "[/HYPOTHESIS]\n\n"
        "[EVIDENCE QUOTE]\n"
        f"{evidence_quote}\n"
        "[/EVIDENCE QUOTE]\n\n"
        "[EVIDENCE SUMMARY]\n"
        f"{evidence_summary}\n"
        "[/EVIDENCE SUMMARY]\n\n"
        "[SOURCE CHUNK]\n"
        f"{source_text}\n"
        "[/SOURCE CHUNK]"
    )


def build_json_repair_prompt(
    *,
    original_prompt: str,
    raw_response: str,
    validation_error: str,
) -> str:
    return (
        f"{EXTRACT_SYSTEM_PROMPT}\n"
        "The previous response failed JSON parsing or schema validation. Repair it.\n"
        "Return exactly one raw JSON object matching the requested schema. No markdown, no commentary.\n\n"
        "[ORIGINAL TASK]\n"
        f"{original_prompt}\n"
        "[/ORIGINAL TASK]\n\n"
        "[INVALID RESPONSE]\n"
        f"{raw_response}\n"
        "[/INVALID RESPONSE]\n\n"
        "[VALIDATION ERROR]\n"
        f"{validation_error}\n"
        "[/VALIDATION ERROR]"
    )
