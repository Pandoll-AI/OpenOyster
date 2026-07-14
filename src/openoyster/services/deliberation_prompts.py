"""Prompt builders for Autonomous Deliberation D1 stages."""

from __future__ import annotations

import json
from typing import Any

from openoyster.deliberation_contracts import (
    MAX_PROMPT_CHARS,
    PROMPT_TEMPLATE_VERSION,
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
