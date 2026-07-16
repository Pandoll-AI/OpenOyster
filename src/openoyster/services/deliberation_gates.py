"""Deterministic gates and anchor validation for Autonomous Deliberation D1."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from openoyster.deliberation_contracts import (
    MIN_QUOTE_CHARS,
    AssertionClass,
    BeliefsStagePayload,
    CitationAnchor,
    ConstraintJudgement,
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

# Quote anchors must carry enough letter/digit content after NFKC (not just punctuation).
MIN_QUOTE_ALNUM_CHARS = 6


def safe_validation_error_message(exc: ValidationError) -> str:
    """Compose a ValidationError summary without input values or docs URLs."""
    parts: list[str] = []
    for err in exc.errors(include_input=False, include_url=False):
        loc = err.get("loc") or ()
        loc_str = ".".join(str(item) for item in loc) if loc else "(root)"
        err_type = err.get("type") or "validation_error"
        parts.append(f"{loc_str}: {err_type}")
    if not parts:
        return "payload validation failed"
    return "payload validation failed: " + "; ".join(parts)


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
    # Viable options that have both expected and adverse scenarios (filled after scenarios).
    scenario_complete_option_keys: set[str] = field(default_factory=set)
    critic_verdict: str | None = None


@dataclass(frozen=True)
class SelectionBlockers:
    """Shared selection-gate findings used by validate_decision and selection_gate_allows."""

    critic_non_pass: bool = False
    insufficient_viable_options: bool = False
    selected_not_viable: bool = False
    missing_scenarios: bool = False

    def reason_codes(self) -> list[str]:
        reasons: list[str] = []
        if self.critic_non_pass:
            reasons.append("critic_non_pass")
        if self.insufficient_viable_options:
            reasons.append("insufficient_viable_options")
        if self.selected_not_viable:
            reasons.append("selection_gate_failed")
        if self.missing_scenarios:
            reasons.append("missing_scenarios")
        return reasons

    def any(self) -> bool:
        return bool(self.reason_codes())


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


def require_known_belief_ref(key: str, belief_keys: set[str]) -> None:
    """Fail-closed belief key resolution (empty prior set still rejects unknowns)."""
    if key not in belief_keys:
        raise StageGateError("unknown_belief_ref", f"unknown belief key: {key}")


def require_known_option_ref(key: str, option_keys: set[str]) -> None:
    """Fail-closed option key resolution (empty prior set still rejects unknowns)."""
    if key not in option_keys:
        raise StageGateError("unknown_option_ref", f"unknown option key: {key}")


def validate_constraint_coverage(
    judgements: list[ConstraintJudgement],
    constraint_count: int,
    *,
    option_key: str,
) -> None:
    """Require exact coverage of mission.constraints indices (no missing/duplicate/OOB)."""
    indices: list[int] = []
    for judgement in judgements:
        if judgement.constraint_index >= constraint_count:
            raise StageGateError(
                "invalid_constraint_index",
                f"constraint_index {judgement.constraint_index} out of range "
                f"for option {option_key} (mission has {constraint_count} constraints)",
            )
        indices.append(judgement.constraint_index)
    if len(indices) != len(set(indices)):
        raise StageGateError(
            "duplicate_constraint_index",
            f"option {option_key} has duplicate constraint_index values",
        )
    expected = set(range(constraint_count))
    if set(indices) != expected:
        raise StageGateError(
            "constraint_coverage_incomplete",
            f"option {option_key} constraint_judgements must cover exactly "
            f"indices {sorted(expected)}; got {sorted(set(indices))}",
        )


def validate_critic_verdict_consistency(payload: CriticStagePayload) -> None:
    """pass verdict may only carry coverage_ok issues/findings (or none).

    Gap findings without issue_code may coexist with pass. Any finding with a
    non-coverage_ok issue_code is treated as blocking and rejects pass.
    """
    if payload.verdict != "pass":
        return
    for issue in payload.issues:
        if issue.code != "coverage_ok":
            raise StageGateError(
                "critic_verdict_inconsistent",
                f"verdict pass is inconsistent with issue code {issue.code}",
            )
    for finding in payload.findings:
        code = finding.issue_code
        if code is not None and code != "coverage_ok":
            raise StageGateError(
                "critic_verdict_inconsistent",
                f"verdict pass is inconsistent with finding issue_code {code}",
            )


def _scenario_kinds_complete(kinds: set[str]) -> bool:
    return "expected" in kinds and "adverse" in kinds


def scenario_complete_viable_keys(ctx: GateContext) -> set[str]:
    """Viable options that carry both expected and adverse scenarios."""
    if ctx.scenario_complete_option_keys:
        return set(ctx.scenario_complete_option_keys) & set(ctx.viable_option_keys)
    complete: set[str] = set()
    for key in ctx.viable_option_keys:
        if _scenario_kinds_complete(ctx.scenario_index.get(key, set())):
            complete.add(key)
    return complete


def evaluate_selection_gate(
    ctx: GateContext, decision: DecisionStagePayload
) -> SelectionBlockers:
    """Shared selection eligibility checks for validate_decision and selection_gate_allows."""
    if decision.outcome != "select":
        return SelectionBlockers()
    kinds = ctx.scenario_index.get(decision.selected_option_key or "", set())
    scenario_complete = scenario_complete_viable_keys(ctx)
    return SelectionBlockers(
        critic_non_pass=ctx.critic_verdict != "pass",
        # Selection requires at least two *scenario-complete* viable alternatives.
        insufficient_viable_options=len(scenario_complete) < 2,
        selected_not_viable=decision.selected_option_key not in ctx.viable_option_keys,
        missing_scenarios=not _scenario_kinds_complete(kinds),
    )


def _quote_letter_digit_count(quote: str) -> int:
    """Count Unicode letters/digits after NFKC (punctuation-only quotes fail)."""
    normalized = unicodedata.normalize("NFKC", quote)
    count = 0
    for ch in normalized:
        category = unicodedata.category(ch)
        if category.startswith("L") or category.startswith("N"):
            count += 1
    return count


def validate_anchor(anchor: CitationAnchor, snapshots: dict[str, EvidenceSnapshotView]) -> None:
    snap = snapshots.get(anchor.evidence_snapshot_id)
    if snap is None:
        raise StageGateError(
            "unknown_citation",
            f"evidence_snapshot_id not in run snapshot: {anchor.evidence_snapshot_id}",
        )
    if anchor.quote is not None:
        stripped = anchor.quote.strip()
        if len(stripped) < MIN_QUOTE_CHARS or _quote_letter_digit_count(stripped) < MIN_QUOTE_ALNUM_CHARS:
            raise StageGateError(
                "quote_too_short",
                f"quote must be at least {MIN_QUOTE_CHARS} characters after strip "
                f"and at least {MIN_QUOTE_ALNUM_CHARS} letters/digits after NFKC "
                f"for {anchor.evidence_snapshot_id}",
            )
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
        validate_constraint_coverage(
            option.constraint_judgements,
            constraint_count,
            option_key=option.local_key,
        )
        for judgement in option.constraint_judgements:
            validate_assertion(judgement.rationale, ctx)
            if judgement.rationale.classification == AssertionClass.mission_control:
                expected_pointer = f"/constraints/{judgement.constraint_index}"
                if judgement.rationale.mission_pointer != expected_pointer:
                    raise StageGateError(
                        "constraint_pointer_mismatch",
                        f"option {option.local_key} constraint_index "
                        f"{judgement.constraint_index} mission_control rationale "
                        f"must point at {expected_pointer}",
                    )
        for key in option.supporting_belief_keys + option.opposing_belief_keys:
            require_known_belief_ref(key, ctx.belief_keys)
        # Hard constraint violation must exclude.
        hard_fail = any(not j.satisfied for j in option.constraint_judgements)
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
        require_known_option_ref(scenario.option_key, ctx.option_keys)
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
    ctx.scenario_complete_option_keys = {
        key for key, kinds in index.items() if _scenario_kinds_complete(kinds)
    }


def validate_critic_payload(payload: CriticStagePayload, ctx: GateContext) -> None:
    validate_critic_verdict_consistency(payload)
    for finding in payload.findings:
        validate_assertion(finding, ctx)
    ctx.critic_verdict = payload.verdict


def validate_decision_payload(payload: DecisionStagePayload, ctx: GateContext) -> None:
    validate_assertion(payload.rationale, ctx)
    for flip in payload.flip_conditions:
        validate_assertion(flip.condition, ctx)
    if payload.outcome != "select":
        return
    blockers = evaluate_selection_gate(ctx, payload)
    if blockers.critic_non_pass:
        raise StageGateError("critic_non_pass", "cannot select when critic did not pass")
    if blockers.insufficient_viable_options:
        raise StageGateError(
            "insufficient_viable_options", "selection requires at least two viable options"
        )
    if blockers.selected_not_viable:
        raise StageGateError(
            "unknown_selected_option",
            f"selected option not viable: {payload.selected_option_key}",
        )
    if blockers.missing_scenarios:
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
    except ValidationError as exc:
        raise StageGateError(
            "invalid_stage_payload",
            safe_validation_error_message(exc),
        ) from exc
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
    blockers = evaluate_selection_gate(ctx, decision)
    reasons = blockers.reason_codes()
    return (not reasons), reasons
