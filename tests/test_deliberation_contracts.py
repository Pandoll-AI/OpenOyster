"""Strict typed stage contracts for Autonomous Deliberation D1."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from openoyster.deliberation_contracts import (
    CONTRACT_VERSION,
    MAX_BELIEFS,
    MAX_EVIDENCE_SNAPSHOTS,
    MAX_OPTIONS,
    MAX_SCENARIOS_PER_OPTION,
    PROMPT_TEMPLATE_VERSION,
    AssertionClass,
    Belief,
    BeliefsStagePayload,
    CitationAnchor,
    CriticStagePayload,
    DecisionStagePayload,
    Mission,
    OptionsStagePayload,
    ScenariosStagePayload,
    canonical_json,
    mission_digest,
    validate_stage_payload,
)


def _quote_anchor(evidence_snapshot_id: str = "snap:1") -> dict[str, Any]:
    return {
        "evidence_snapshot_id": evidence_snapshot_id,
        "quote": "This source supports this claim.",
    }


def _grounded_assertion(text: str = "The claim is supported.") -> dict[str, Any]:
    return {
        "text": text,
        "classification": "grounded_fact",
        "anchors": [_quote_anchor()],
    }


def test_contract_version_constants_are_frozen() -> None:
    assert CONTRACT_VERSION == "deliberation-d1-v1"
    assert PROMPT_TEMPLATE_VERSION == "deliberation-prompts-d1-v1"
    assert MAX_BELIEFS == 20
    assert MAX_OPTIONS == 5
    assert MAX_SCENARIOS_PER_OPTION == 3
    assert MAX_EVIDENCE_SNAPSHOTS == 24


def test_mission_requires_goal_and_decision_question() -> None:
    with pytest.raises(ValidationError):
        Mission.model_validate({"goal": "only goal"})
    with pytest.raises(ValidationError):
        Mission.model_validate({"decision_question": "only question"})
    mission = Mission.model_validate(
        {
            "goal": "Choose a response path",
            "decision_question": "Should we accept the claim?",
        }
    )
    assert mission.constraints == []
    assert mission.preferences == []
    assert mission.context is None


def test_mission_digest_is_canonical_sha256() -> None:
    mission = Mission(
        goal="g",
        decision_question="q",
        constraints=["c1"],
        preferences=["p1"],
        context="background only",
    )
    digest = mission_digest(mission)
    reloaded = Mission.model_validate(json.loads(canonical_json(mission.model_dump(mode="json"))))
    assert mission_digest(reloaded) == digest
    assert len(digest) == 64


def test_mission_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Mission.model_validate(
            {
                "goal": "g",
                "decision_question": "q",
                "extra_field": "nope",
            }
        )


def test_citation_anchor_requires_quote_or_pointer_not_both_empty() -> None:
    with pytest.raises(ValidationError):
        CitationAnchor.model_validate({"evidence_snapshot_id": "snap:1"})
    with pytest.raises(ValidationError):
        CitationAnchor.model_validate(
            {
                "evidence_snapshot_id": "snap:1",
                "quote": "",
            }
        )
    quote = CitationAnchor.model_validate(_quote_anchor())
    assert quote.quote == "This source supports this claim."
    pointer = CitationAnchor.model_validate(
        {
            "evidence_snapshot_id": "snap:1",
            "json_pointer": "/text",
            "value_digest": "a" * 64,
        }
    )
    assert pointer.json_pointer == "/text"


def test_assertion_class_requires_matching_support() -> None:
    with pytest.raises(ValidationError):
        # grounded_fact without anchors
        BeliefsStagePayload.model_validate(
            {
                "beliefs": [
                    {
                        "local_key": "b1",
                        "statement": {
                            "text": "fact",
                            "classification": "grounded_fact",
                            "anchors": [],
                        },
                        "status": "supported",
                    }
                ]
            }
        )
    with pytest.raises(ValidationError):
        BeliefsStagePayload.model_validate(
            {
                "beliefs": [
                    {
                        "local_key": "b1",
                        "statement": {
                            "text": "control",
                            "classification": "mission_control",
                        },
                        "status": "supported",
                    }
                ]
            }
        )


def test_beliefs_payload_accepts_supported_grounded_belief() -> None:
    payload = BeliefsStagePayload.model_validate(
        {
            "beliefs": [
                {
                    "local_key": "b1",
                    "statement": _grounded_assertion(),
                    "status": "supported",
                    "supporting_anchors": [_quote_anchor()],
                    "opposing_anchors": [],
                    "assumptions": [],
                    "gaps": [],
                    "invalidation_conditions": ["If source text is withdrawn."],
                }
            ]
        }
    )
    assert len(payload.beliefs) == 1
    assert payload.beliefs[0].status == "supported"
    assert isinstance(payload.beliefs[0], Belief)


def test_beliefs_rejects_extra_fields_and_over_limit() -> None:
    with pytest.raises(ValidationError):
        BeliefsStagePayload.model_validate(
            {
                "beliefs": [
                    {
                        "local_key": "b1",
                        "statement": _grounded_assertion(),
                        "status": "supported",
                        "truth_confidence": 0.9,
                    }
                ]
            }
        )
    too_many = [
        {
            "local_key": f"b{i}",
            "statement": _grounded_assertion(f"fact {i}"),
            "status": "supported",
            "supporting_anchors": [_quote_anchor()],
        }
        for i in range(MAX_BELIEFS + 1)
    ]
    with pytest.raises(ValidationError):
        BeliefsStagePayload.model_validate({"beliefs": too_many})


def test_options_payload_constraint_judgements_and_limits() -> None:
    payload = OptionsStagePayload.model_validate(
        {
            "options": [
                {
                    "local_key": "opt_accept",
                    "label": {
                        "text": "Accept the claim",
                        "classification": "proposal",
                        "mission_pointer": "/decision_question",
                    },
                    "viable": True,
                    "constraint_judgements": [
                        {
                            "constraint_index": 0,
                            "satisfied": True,
                            "rationale": {
                                "text": "No hard constraint violated",
                                "classification": "mission_control",
                                "mission_pointer": "/constraints/0",
                            },
                        }
                    ],
                    "supporting_belief_keys": ["b1"],
                    "opposing_belief_keys": [],
                    "risks": [],
                    "reversibility": "high",
                    "expected_outcome": {
                        "text": "Proceed with acceptance",
                        "classification": "proposal",
                        "mission_pointer": "/goal",
                    },
                },
                {
                    "local_key": "opt_defer",
                    "label": {
                        "text": "Defer",
                        "classification": "proposal",
                        "mission_pointer": "/goal",
                    },
                    "viable": True,
                    "constraint_judgements": [],
                    "supporting_belief_keys": [],
                    "opposing_belief_keys": [],
                    "risks": [],
                    "reversibility": "high",
                    "expected_outcome": {
                        "text": "Wait for more evidence",
                        "classification": "proposal",
                        "mission_pointer": "/goal",
                    },
                },
            ]
        }
    )
    assert len(payload.options) == 2
    with pytest.raises(ValidationError):
        OptionsStagePayload.model_validate({"options": payload.model_dump()["options"] * 3})


def test_scenarios_payload_requires_expected_and_adverse_kinds() -> None:
    payload = ScenariosStagePayload.model_validate(
        {
            "scenarios": [
                {
                    "local_key": "s_accept_expected",
                    "option_key": "opt_accept",
                    "kind": "expected",
                    "projected_outcome": {
                        "text": "Claim holds under normal conditions",
                        "classification": "grounded_inference",
                        "anchors": [_quote_anchor()],
                    },
                    "facts": [_grounded_assertion()],
                    "inferences": [
                        {
                            "text": "Support implies acceptance risk is limited",
                            "classification": "grounded_inference",
                            "anchors": [_quote_anchor()],
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
                        "anchors": [_quote_anchor()],
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
            ]
        }
    )
    assert {s.kind for s in payload.scenarios} == {"expected", "adverse"}


def test_critic_and_decision_payloads_closed_codes() -> None:
    critic = CriticStagePayload.model_validate(
        {
            "verdict": "pass",
            "issues": [],
            "findings": [
                {
                    "text": "Options cover acceptance and deferral",
                    "classification": "structural",
                    "issue_code": "coverage_ok",
                    "artifact_ref": "options:opt_accept",
                }
            ],
        }
    )
    assert critic.verdict == "pass"
    decision = DecisionStagePayload.model_validate(
        {
            "outcome": "select",
            "selected_option_key": "opt_accept",
            "rationale": {
                "text": "Supported claim and critic passed",
                "classification": "grounded_inference",
                "anchors": [_quote_anchor()],
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
    )
    assert decision.outcome == "select"
    abstain = DecisionStagePayload.model_validate(
        {
            "outcome": "abstain",
            "selected_option_key": None,
            "rationale": {
                "text": "Insufficient viable options",
                "classification": "structural",
                "issue_code": "insufficient_viable_options",
                "artifact_ref": "options",
            },
            "abstention_reasons": ["insufficient_viable_options"],
            "flip_conditions": [
                {
                    "local_key": "flip1",
                    "condition": {
                        "text": "If two viable options appear",
                        "classification": "proposal",
                        "mission_pointer": "/goal",
                    },
                }
            ],
            "knowledge_requests": [
                {
                    "local_key": "kr1",
                    "question": "What additional evidence confirms the claim?",
                    "gap_ref": "gap:critical",
                    "priority": "critical",
                }
            ],
        }
    )
    assert abstain.outcome == "abstain"


def test_validate_stage_payload_routes_by_stage_name() -> None:
    beliefs = {
        "beliefs": [
            {
                "local_key": "b1",
                "statement": _grounded_assertion(),
                "status": "supported",
                "supporting_anchors": [_quote_anchor()],
            }
        ]
    }
    model = validate_stage_payload("deliberation_beliefs", beliefs)
    assert isinstance(model, BeliefsStagePayload)
    with pytest.raises(ValueError):
        validate_stage_payload("unknown_stage", beliefs)


def test_assertion_class_enum_is_closed() -> None:
    assert set(AssertionClass) == {
        AssertionClass.grounded_fact,
        AssertionClass.grounded_inference,
        AssertionClass.mission_control,
        AssertionClass.proposal,
        AssertionClass.assumption,
        AssertionClass.gap,
        AssertionClass.structural,
    }
