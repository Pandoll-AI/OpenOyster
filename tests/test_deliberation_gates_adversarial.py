"""Adversarial fail-closed tests for deliberation D1 decision gates.

These attacks currently exploit fail-open gaps. RED expects the gates to reject
them; until the gates are repaired the tests fail because validation incorrectly
returns success.
"""

from __future__ import annotations

from typing import Any

import pytest

from openoyster.deliberation_contracts import (
    CriticStagePayload,
    DecisionStagePayload,
    Mission,
    OptionsStagePayload,
    ScenariosStagePayload,
)
from openoyster.services.deliberation_gates import (
    EvidenceSnapshotView,
    GateContext,
    StageGateError,
    selection_gate_allows,
    validate_anchor,
    validate_critic_payload,
    validate_options_payload,
    validate_scenarios_payload,
    validate_stage,
)

LONG_EVIDENCE = "This source supports this claim. Extra context for anchors."


def _snapshot(
    key: str = "snap:1",
    text: str = LONG_EVIDENCE,
) -> EvidenceSnapshotView:
    return EvidenceSnapshotView(
        snapshot_key=key,
        db_id=1,
        global_evidence_id=f"pack://evidence/{key}",
        text=text,
        payload={"text": text},
        pack_install_id=1,
        record_hash="a" * 64,
    )


def _ctx(
    *,
    constraints: list[str] | None = None,
    belief_keys: set[str] | None = None,
    option_keys: set[str] | None = None,
    viable_option_keys: set[str] | None = None,
    scenario_index: dict[str, set[str]] | None = None,
    critic_verdict: str | None = None,
    text: str = LONG_EVIDENCE,
) -> GateContext:
    snap = _snapshot(text=text)
    return GateContext(
        mission=Mission(
            goal="Choose a path",
            decision_question="What should we do?",
            constraints=list(constraints or []),
        ),
        snapshots_by_key={snap.snapshot_key: snap},
        belief_keys=set(belief_keys or set()),
        option_keys=set(option_keys or set()),
        viable_option_keys=set(viable_option_keys or set()),
        scenario_index=dict(scenario_index or {}),
        critic_verdict=critic_verdict,
    )


def _proposal(text: str = "Do the thing", pointer: str = "/goal") -> dict[str, Any]:
    return {
        "text": text,
        "classification": "proposal",
        "mission_pointer": pointer,
    }


def _mission_control(text: str = "Constraint checked", pointer: str = "/constraints/0") -> dict[str, Any]:
    return {
        "text": text,
        "classification": "mission_control",
        "mission_pointer": pointer,
    }


def _viable_option(
    local_key: str,
    *,
    judgements: list[dict[str, Any]] | None = None,
    supporting_belief_keys: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "local_key": local_key,
        "label": _proposal(f"Option {local_key}"),
        "viable": True,
        "constraint_judgements": list(judgements if judgements is not None else []),
        "supporting_belief_keys": list(supporting_belief_keys or []),
        "opposing_belief_keys": [],
        "risks": [],
        "reversibility": "high",
        "expected_outcome": _proposal(f"Expected for {local_key}"),
    }


def _judgement(index: int, *, satisfied: bool = True) -> dict[str, Any]:
    return {
        "constraint_index": index,
        "satisfied": satisfied,
        "rationale": _mission_control(pointer=f"/constraints/{index}" if index >= 0 else "/goal"),
    }


def test_a_empty_beliefs_fabricated_belief_ref_must_fail() -> None:
    """(a) Empty prior beliefs must not allow forged belief references."""
    ctx = _ctx(belief_keys=set(), constraints=["c0"])
    payload = OptionsStagePayload.model_validate(
        {
            "options": [
                _viable_option(
                    "opt_forged",
                    judgements=[_judgement(0)],
                    supporting_belief_keys=["forged_belief_that_never_existed"],
                )
            ]
        }
    )
    with pytest.raises(StageGateError) as caught:
        validate_options_payload(payload, ctx)
    assert caught.value.code == "unknown_belief_ref"


def test_b_viable_option_missing_constraint_judgements_must_fail() -> None:
    """(b) Viable option with zero/partial judgements when mission has 2 constraints."""
    ctx = _ctx(constraints=["c0", "c1"], belief_keys=set())

    empty = OptionsStagePayload.model_validate(
        {"options": [_viable_option("opt_empty", judgements=[])]}
    )
    with pytest.raises(StageGateError) as empty_err:
        validate_options_payload(empty, ctx)
    assert empty_err.value.code == "constraint_coverage_incomplete"

    partial = OptionsStagePayload.model_validate(
        {"options": [_viable_option("opt_partial", judgements=[_judgement(0)])]}
    )
    with pytest.raises(StageGateError) as partial_err:
        validate_options_payload(partial, ctx)
    assert partial_err.value.code == "constraint_coverage_incomplete"


def test_c_duplicate_constraint_index_must_fail() -> None:
    """(c) Duplicate constraint_index values are rejected."""
    ctx = _ctx(constraints=["c0", "c1"], belief_keys=set())
    payload = OptionsStagePayload.model_validate(
        {
            "options": [
                _viable_option(
                    "opt_dup",
                    judgements=[_judgement(0), _judgement(0)],
                )
            ]
        }
    )
    with pytest.raises(StageGateError) as caught:
        validate_options_payload(payload, ctx)
    assert caught.value.code == "duplicate_constraint_index"


def test_d_zero_constraints_with_judgement_must_fail() -> None:
    """(d) Mission with zero constraints requires zero judgements."""
    ctx = _ctx(constraints=[], belief_keys=set())
    # mission_control pointer must resolve; use /goal when no constraints exist.
    payload = OptionsStagePayload.model_validate(
        {
            "options": [
                {
                    "local_key": "opt_extra",
                    "label": _proposal(),
                    "viable": True,
                    "constraint_judgements": [
                        {
                            "constraint_index": 0,
                            "satisfied": True,
                            "rationale": _mission_control(pointer="/goal"),
                        }
                    ],
                    "supporting_belief_keys": [],
                    "opposing_belief_keys": [],
                    "risks": [],
                    "reversibility": "high",
                    "expected_outcome": _proposal(),
                }
            ]
        }
    )
    with pytest.raises(StageGateError) as caught:
        validate_options_payload(payload, ctx)
    assert caught.value.code in {
        "invalid_constraint_index",
        "constraint_coverage_incomplete",
    }


def test_e_critic_pass_with_non_coverage_issue_must_fail() -> None:
    """(e) verdict=pass cannot carry substantive issue codes."""
    ctx = _ctx()
    payload = CriticStagePayload.model_validate(
        {
            "verdict": "pass",
            "issues": [
                {
                    "code": "missing_opposing_evidence",
                    "artifact_ref": "beliefs:b1",
                    "detail": "No opposing anchors cited",
                }
            ],
            "findings": [
                {
                    "text": "Missing opposing evidence despite pass",
                    "classification": "structural",
                    "issue_code": "missing_opposing_evidence",
                    "artifact_ref": "beliefs:b1",
                }
            ],
        }
    )
    with pytest.raises(StageGateError) as caught:
        validate_critic_payload(payload, ctx)
    assert caught.value.code == "critic_verdict_inconsistent"


def test_f_three_char_quote_anchor_must_fail() -> None:
    """(f) Quote anchors shorter than MIN_QUOTE_CHARS are rejected."""
    text = "abc is present in the evidence body as a short token."
    snap = _snapshot(text=text)
    from openoyster.deliberation_contracts import CitationAnchor

    anchor = CitationAnchor.model_validate(
        {"evidence_snapshot_id": "snap:1", "quote": "abc"}
    )
    assert "abc" in text  # substring match alone is insufficient
    with pytest.raises(StageGateError) as caught:
        validate_anchor(anchor, {snap.snapshot_key: snap})
    assert caught.value.code == "quote_too_short"


def test_empty_option_keys_fabricated_option_ref_must_fail() -> None:
    """Parallel fail-open for scenarios: empty option set must still resolve refs."""
    ctx = _ctx(option_keys=set())
    payload = ScenariosStagePayload.model_validate(
        {
            "scenarios": [
                {
                    "local_key": "s1",
                    "option_key": "forged_option",
                    "kind": "expected",
                    "projected_outcome": {
                        "text": "Projected outcome for forged option",
                        "classification": "grounded_inference",
                        "anchors": [
                            {
                                "evidence_snapshot_id": "snap:1",
                                "quote": "This source supports this claim.",
                            }
                        ],
                    },
                    "facts": [],
                    "inferences": [],
                    "assumptions": [],
                }
            ]
        }
    )
    with pytest.raises(StageGateError) as caught:
        validate_scenarios_payload(payload, ctx)
    assert caught.value.code == "unknown_option_ref"


def test_selection_gate_shares_selection_blockers_with_validate_decision() -> None:
    """selection_gate_allows and validate_stage decision path share blocker logic."""
    ctx = _ctx(
        viable_option_keys={"opt_a"},
        critic_verdict="pass",
        scenario_index={"opt_a": {"expected", "adverse"}},
    )
    decision = DecisionStagePayload.model_validate(
        {
            "outcome": "select",
            "selected_option_key": "opt_a",
            "rationale": {
                "text": "Only one viable option",
                "classification": "proposal",
                "mission_pointer": "/goal",
            },
            "abstention_reasons": [],
            "flip_conditions": [
                {
                    "local_key": "flip1",
                    "condition": {
                        "text": "If another viable option appears",
                        "classification": "proposal",
                        "mission_pointer": "/goal",
                    },
                }
            ],
            "knowledge_requests": [],
        }
    )
    allowed, reasons = selection_gate_allows(ctx, decision)
    assert allowed is False
    assert "insufficient_viable_options" in reasons

    with pytest.raises(StageGateError) as caught:
        validate_stage(
            "deliberation_decision",
            decision.model_dump(mode="json"),
            ctx,
        )
    assert caught.value.code == "insufficient_viable_options"


def test_selection_requires_two_scenario_complete_viable_options() -> None:
    """Viable option without expected+adverse scenarios is not a selection alternative.

    Repro: viable={a,b} but only a has full scenarios → selecting a must fail
    (insufficient_viable_options), not pass with b as a fake alternative.
    """
    ctx = _ctx(
        viable_option_keys={"opt_a", "opt_b"},
        critic_verdict="pass",
        scenario_index={"opt_a": {"expected", "adverse"}},
        # opt_b has no scenarios at all
    )
    # Mirror post-scenarios gate fill when only a is complete.
    ctx.scenario_complete_option_keys = {"opt_a"}
    decision = DecisionStagePayload.model_validate(
        {
            "outcome": "select",
            "selected_option_key": "opt_a",
            "rationale": {
                "text": "Pick a despite incomplete alternative scenarios",
                "classification": "proposal",
                "mission_pointer": "/goal",
            },
            "abstention_reasons": [],
            "flip_conditions": [
                {
                    "local_key": "flip1",
                    "condition": {
                        "text": "If b also has scenarios",
                        "classification": "proposal",
                        "mission_pointer": "/goal",
                    },
                }
            ],
            "knowledge_requests": [],
        }
    )
    allowed, reasons = selection_gate_allows(ctx, decision)
    assert allowed is False
    assert "insufficient_viable_options" in reasons

    with pytest.raises(StageGateError) as caught:
        validate_stage(
            "deliberation_decision",
            decision.model_dump(mode="json"),
            ctx,
        )
    assert caught.value.code == "insufficient_viable_options"


def test_hyphen_only_quote_anchor_must_fail() -> None:
    """Punctuation-only quotes that meet length still fail alphanumeric content check."""
    hyphens = "-" * 12
    text = f"context {hyphens} more context around punctuation"
    snap = _snapshot(text=text)
    from openoyster.deliberation_contracts import CitationAnchor

    anchor = CitationAnchor.model_validate(
        {"evidence_snapshot_id": "snap:1", "quote": hyphens}
    )
    assert hyphens in text
    with pytest.raises(StageGateError) as caught:
        validate_anchor(anchor, {snap.snapshot_key: snap})
    assert caught.value.code == "quote_too_short"


def test_mission_control_constraint_pointer_must_match_index() -> None:
    """mission_control constraint rationale must point at /constraints/{index}."""
    ctx = _ctx(constraints=["c0", "c1"], belief_keys=set())
    payload = OptionsStagePayload.model_validate(
        {
            "options": [
                _viable_option(
                    "opt_bad_ptr",
                    judgements=[
                        {
                            "constraint_index": 0,
                            "satisfied": True,
                            "rationale": _mission_control(pointer="/goal"),
                        },
                        {
                            "constraint_index": 1,
                            "satisfied": True,
                            "rationale": _mission_control(pointer="/goal"),
                        },
                    ],
                )
            ]
        }
    )
    with pytest.raises(StageGateError) as caught:
        validate_options_payload(payload, ctx)
    assert caught.value.code == "constraint_pointer_mismatch"
