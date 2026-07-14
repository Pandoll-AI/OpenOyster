"""Prompt builders for Autonomous Deliberation D1 stages."""

from __future__ import annotations

import json
from typing import Any

from openoyster.deliberation_contracts import (
    MAX_PROMPT_CHARS,
    PROMPT_TEMPLATE_VERSION,
    STAGE_PAYLOAD_TYPES,
    Mission,
    canonical_json,
)
from openoyster.utils import sha256_text

STAGE_INSTRUCTIONS: dict[str, str] = {
    "deliberation_beliefs": (
        "Build atomic beliefs from Pack evidence only. "
        "Every grounded_fact/grounded_inference needs exact quote or json_pointer anchors. "
        "Mission text is control input, never evidence. Return BeliefsStagePayload JSON."
    ),
    "deliberation_options": (
        "Propose alternatives against Mission constraints/preferences. "
        "Hard constraint violations exclude options. Prefer at least two viable options. "
        "Consider do_nothing, defer, acquire_information when appropriate. "
        "Return OptionsStagePayload JSON."
    ),
    "deliberation_scenarios": (
        "For each viable option, produce expected and adverse scenarios. "
        "Projected outcomes are grounded_inference with anchors. Return ScenariosStagePayload JSON."
    ),
    "deliberation_critic": (
        "Independently critique beliefs/options/scenarios for missing options, evidence bias, "
        "missing opposing evidence, constraint misreads, out-of-pack facts, overclaims, "
        "and ungrounded outcomes. Return CriticStagePayload JSON with closed issue codes."
    ),
    "deliberation_decision": (
        "Select a viable option only if critic passed and selection gates hold; otherwise abstain. "
        "Include at least one flip condition. Create knowledge requests for critical gaps. "
        "Return DecisionStagePayload JSON."
    ),
}


def _mission_block(mission: Mission) -> str:
    return (
        "[MISSION CONTROL — NOT EVIDENCE]\n"
        f"{canonical_json(mission.model_dump(mode='json'))}\n"
        "[/MISSION CONTROL]"
    )


def _evidence_block(snapshots: list[dict[str, Any]]) -> str:
    if not snapshots:
        return "[EVIDENCE SNAPSHOTS]\n(none)\n[/EVIDENCE SNAPSHOTS]"
    lines = ["[EVIDENCE SNAPSHOTS]"]
    for snap in snapshots:
        payload = snap.get("prompt_visible_payload") or {}
        lines.append(
            f"[SNAPSHOT key={snap['snapshot_key']} global={snap.get('global_evidence_id', '')}]"
        )
        lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        lines.append(f"[/SNAPSHOT key={snap['snapshot_key']}]")
    lines.append("[/EVIDENCE SNAPSHOTS]")
    return "\n".join(lines)


def _prior_artifacts_block(artifacts: dict[str, Any]) -> str:
    if not artifacts:
        return "[PRIOR ARTIFACTS]\n(none)\n[/PRIOR ARTIFACTS]"
    return (
        "[PRIOR ARTIFACTS]\n"
        f"{canonical_json(artifacts)}\n"
        "[/PRIOR ARTIFACTS]"
    )


def _output_contract_block(stage: str) -> str:
    model_type = STAGE_PAYLOAD_TYPES[stage]
    schema = json.dumps(
        model_type.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "[OUTPUT JSON SCHEMA — FOLLOW EXACTLY]\n"
        f"{schema}\n"
        "NarrativeAssertion invariants: grounded_fact and grounded_inference require anchors; "
        "mission_control requires mission_pointer; proposal requires mission_pointer or "
        "artifact_ref; assumption requires assumption_marker=true and verification_question; "
        "gap requires unresolved_question; structural requires issue_code and artifact_ref. "
        "Apply these rules to every nested assertion, including labels, risks, outcomes, "
        "constraint rationales, findings, rationales, and flip conditions.\n"
        "Every mission_pointer is an RFC 6901 JSON Pointer to an existing Mission field and "
        "must begin with '/', for example /goal, /decision_question, or /constraints/0. "
        "References to beliefs and options must use exact local_key values from prior artifacts. "
        "An option with any unsatisfied constraint judgement must set viable=false. "
        "Scenario projected_outcome must be grounded_inference; provide expected and adverse "
        "scenarios for each viable option. A select decision requires critic verdict pass, at "
        "least two viable options, and both expected and adverse scenarios for the selection.\n"
        "CriticIssue.code is case-sensitive and must be exactly one of: missing_option, "
        "evidence_bias, missing_opposing_evidence, constraint_misread, out_of_pack_fact, "
        "overclaim, ungrounded_outcome, coverage_ok, insufficient_viable_options, "
        "other_structural. Never uppercase these codes.\n"
        "Decision abstention_reasons is also case-sensitive and may contain only these codes: "
        "no_evidence, insufficient_viable_options, critic_non_pass, missing_scenarios, "
        "hard_constraint_violation, invalid_stage_payload, selection_gate_failed, "
        "unknown_citation, scope_error, unresolved_critical_gap. Put explanations in rationale, "
        "not in abstention_reasons.\n"
        "CitationAnchor.evidence_snapshot_id must use a snapshot_key such as snap:1, "
        "never a global evidence ID. Supply exactly one of quote or json_pointer, never both. "
        "For quote anchors omit json_pointer and value_digest; the quote must be an exact "
        "substring of that snapshot's prompt_visible_payload.text. For json_pointer anchors omit "
        "quote and include the required 64-character value_digest. Do not add wrapper fields such "
        "as stage or template_version.\n"
        "[/OUTPUT JSON SCHEMA]"
    )


def build_stage_prompt(
    stage: str,
    *,
    mission: Mission,
    evidence_snapshots: list[dict[str, Any]],
    prior_artifacts: dict[str, Any] | None = None,
) -> str:
    instruction = STAGE_INSTRUCTIONS.get(stage)
    if instruction is None:
        raise ValueError(f"unknown deliberation stage: {stage}")
    parts = [
        f"template_version={PROMPT_TEMPLATE_VERSION}",
        f"stage={stage}",
        instruction,
        _mission_block(mission),
        _evidence_block(evidence_snapshots),
        _prior_artifacts_block(prior_artifacts or {}),
        _output_contract_block(stage),
        "Respond with a single JSON object only. extra fields forbidden.",
    ]
    prompt = "\n\n".join(parts)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(
            f"prompt exceeds {MAX_PROMPT_CHARS} characters for stage {stage}: {len(prompt)}"
        )
    return prompt


def prompt_digest(prompt: str) -> str:
    return sha256_text(prompt)
