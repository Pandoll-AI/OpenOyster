"""Deterministic gates and anchor validation for Autonomous Deliberation D1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openoyster.deliberation_contracts import (
    AssertionClass,
    BeliefsStagePayload,
    CitationAnchor,
    CriticStagePayload,
    DecisionStagePayload,
    Mission,
    NarrativeAssertion,
    OptionsStagePayload,
    ScenariosStagePayload,
    StrictModel,
    canonical_json,
    validate_stage_payload,
)
from openoyster.utils import sha256_text


class StageGateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class EvidenceSnapshotView:
    snapshot_key: str
    db_id: int
    global_evidence_id: str
    text: str
    payload: dict[str, Any]
    pack_install_id: int
    record_hash: str


@dataclass
class GateContext:
    mission: Mission
    snapshots_by_key: dict[str, EvidenceSnapshotView]
    belief_keys: set[str] = field(default_factory=set)
    option_keys: set[str] = field(default_factory=set)
    viable_option_keys: set[str] = field(default_factory=set)
    scenario_index: dict[str, set[str]] = field(default_factory=dict)
    critic_verdict: str | None = None


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise StageGateError("invalid_json_pointer", f"json_pointer must start with /: {pointer}")
    current = document
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                raise StageGateError("json_pointer_mismatch", f"pointer path missing: {pointer}")
            current = current[part]
        elif isinstance(current, list):
            try:
                index = int(part)
            except ValueError as exc:
                raise StageGateError(
                    "json_pointer_mismatch", f"pointer list index invalid: {pointer}"
                ) from exc
            if index < 0 or index >= len(current):
                raise StageGateError("json_pointer_mismatch", f"pointer index out of range: {pointer}")
            current = current[index]
        else:
            raise StageGateError("json_pointer_mismatch", f"pointer cannot traverse: {pointer}")
    return current


def validate_mission_pointer(mission: Mission, pointer: str) -> Any:
    payload = mission.model_dump(mode="json")
    try:
        return resolve_json_pointer(payload, pointer)
    except StageGateError as exc:
        raise StageGateError("invalid_mission_pointer", exc.message) from exc


def validate_anchor(anchor: CitationAnchor, snapshots: dict[str, EvidenceSnapshotView]) -> None:
    snap = snapshots.get(anchor.evidence_snapshot_id)
    if snap is None:
        raise StageGateError(
            "unknown_citation",
            f"evidence_snapshot_id not in run snapshot: {anchor.evidence_snapshot_id}",
        )
    if anchor.quote is not None:
        if anchor.quote == "" or anchor.quote not in (snap.text or ""):
            raise StageGateError(
                "quote_mismatch",
                f"quote not found in evidence text {anchor.evidence_snapshot_id}",
            )
        return
    assert anchor.json_pointer is not None
    value = resolve_json_pointer(snap.payload, anchor.json_pointer)
    digest = sha256_text(canonical_json(value))
    if digest != anchor.value_digest:
        raise StageGateError(
            "pointer_mismatch",
            f"json_pointer value digest mismatch for {anchor.evidence_snapshot_id}",
        )


def validate_assertion(assertion: NarrativeAssertion, ctx: GateContext) -> None:
    if assertion.mission_pointer:
        validate_mission_pointer(ctx.mission, assertion.mission_pointer)
    for anchor in assertion.anchors:
        validate_anchor(anchor, ctx.snapshots_by_key)
    if assertion.classification in {
        AssertionClass.grounded_fact,
        AssertionClass.grounded_inference,
    } and not assertion.anchors:
        raise StageGateError("missing_anchor", "grounded assertion lacks anchors")


def validate_beliefs_payload(payload: BeliefsStagePayload, ctx: GateContext) -> None:
    for belief in payload.beliefs:
        validate_assertion(belief.statement, ctx)
        for anchor in belief.supporting_anchors + belief.opposing_anchors:
            validate_anchor(anchor, ctx.snapshots_by_key)
        for item in belief.assumptions + belief.gaps:
            validate_assertion(item, ctx)
    ctx.belief_keys = {belief.local_key for belief in payload.beliefs}


def validate_options_payload(payload: OptionsStagePayload, ctx: GateContext) -> None:
    constraint_count = len(ctx.mission.constraints)
    for option in payload.options:
        validate_assertion(option.label, ctx)
        validate_assertion(option.expected_outcome, ctx)
        if option.exclusion_reason is not None:
            validate_assertion(option.exclusion_reason, ctx)
        for risk in option.risks:
            validate_assertion(risk, ctx)
        for judgement in option.constraint_judgements:
            if judgement.constraint_index >= constraint_count and constraint_count > 0:
                raise StageGateError(
                    "invalid_constraint_index",
                    f"constraint_index {judgement.constraint_index} out of range",
                )
            validate_assertion(judgement.rationale, ctx)
        for key in option.supporting_belief_keys + option.opposing_belief_keys:
            if ctx.belief_keys and key not in ctx.belief_keys:
                raise StageGateError("unknown_belief_ref", f"unknown belief key: {key}")
        # Hard constraint violation must exclude.
        hard_fail = any(
            not j.satisfied for j in option.constraint_judgements
        )
        if hard_fail and option.viable:
            raise StageGateError(
                "hard_constraint_violation",
                f"option {option.local_key} is viable despite unsatisfied constraint",
            )
    ctx.option_keys = {option.local_key for option in payload.options}
    ctx.viable_option_keys = {option.local_key for option in payload.options if option.viable}


def validate_scenarios_payload(payload: ScenariosStagePayload, ctx: GateContext) -> None:
    index: dict[str, set[str]] = {}
    for scenario in payload.scenarios:
        if ctx.option_keys and scenario.option_key not in ctx.option_keys:
            raise StageGateError(
                "unknown_option_ref", f"unknown option key: {scenario.option_key}"
            )
        validate_assertion(scenario.projected_outcome, ctx)
        if scenario.projected_outcome.classification != AssertionClass.grounded_inference:
            raise StageGateError(
                "scenario_outcome_class",
                "projected_outcome must be grounded_inference",
            )
        for item in scenario.facts + scenario.inferences + scenario.assumptions:
            validate_assertion(item, ctx)
        index.setdefault(scenario.option_key, set()).add(scenario.kind)
    ctx.scenario_index = index


def validate_critic_payload(payload: CriticStagePayload, ctx: GateContext) -> None:
    for finding in payload.findings:
        validate_assertion(finding, ctx)
    ctx.critic_verdict = payload.verdict


def validate_decision_payload(payload: DecisionStagePayload, ctx: GateContext) -> None:
    validate_assertion(payload.rationale, ctx)
    for flip in payload.flip_conditions:
        validate_assertion(flip.condition, ctx)
    if payload.outcome == "select":
        if ctx.critic_verdict is not None and ctx.critic_verdict != "pass":
            raise StageGateError("critic_non_pass", "cannot select when critic did not pass")
        if len(ctx.viable_option_keys) < 2:
            raise StageGateError(
                "insufficient_viable_options", "selection requires at least two viable options"
            )
        if payload.selected_option_key not in ctx.viable_option_keys:
            raise StageGateError(
                "unknown_selected_option",
                f"selected option not viable: {payload.selected_option_key}",
            )
        kinds = ctx.scenario_index.get(payload.selected_option_key or "", set())
        if "expected" not in kinds or "adverse" not in kinds:
            raise StageGateError(
                "missing_scenarios",
                "selected option requires expected and adverse scenarios",
            )
        if payload.rationale.classification not in {
            AssertionClass.grounded_fact,
            AssertionClass.grounded_inference,
            AssertionClass.mission_control,
            AssertionClass.proposal,
        }:
            raise StageGateError(
                "invalid_decision_rationale",
                "decision rationale must be grounded or mission-controlled",
            )


def validate_stage(
    stage: str,
    raw_payload: dict[str, Any],
    ctx: GateContext,
) -> StrictModel:
    try:
        model = validate_stage_payload(stage, raw_payload)
    except Exception as exc:
        raise StageGateError("invalid_stage_payload", str(exc)) from exc

    if isinstance(model, BeliefsStagePayload):
        validate_beliefs_payload(model, ctx)
    elif isinstance(model, OptionsStagePayload):
        validate_options_payload(model, ctx)
    elif isinstance(model, ScenariosStagePayload):
        validate_scenarios_payload(model, ctx)
    elif isinstance(model, CriticStagePayload):
        validate_critic_payload(model, ctx)
    elif isinstance(model, DecisionStagePayload):
        validate_decision_payload(model, ctx)
    else:
        raise StageGateError("unknown_stage", f"unsupported stage model: {stage}")
    return model


def selection_gate_allows(ctx: GateContext, decision: DecisionStagePayload) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if decision.outcome != "select":
        return True, reasons
    if ctx.critic_verdict != "pass":
        reasons.append("critic_non_pass")
    if len(ctx.viable_option_keys) < 2:
        reasons.append("insufficient_viable_options")
    if decision.selected_option_key not in ctx.viable_option_keys:
        reasons.append("selection_gate_failed")
    kinds = ctx.scenario_index.get(decision.selected_option_key or "", set())
    if "expected" not in kinds or "adverse" not in kinds:
        reasons.append("missing_scenarios")
    return (not reasons), reasons
