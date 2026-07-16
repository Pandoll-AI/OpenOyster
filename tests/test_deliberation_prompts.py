from openoyster.deliberation_contracts import Mission
from openoyster.services.deliberation_prompts import build_stage_prompt


def test_stage_prompt_exposes_closed_output_contract_and_hidden_invariants() -> None:
    prompt = build_stage_prompt(
        "deliberation_decision",
        mission=Mission(goal="Choose", decision_question="What next?"),
        evidence_snapshots=[
            {
                "snapshot_key": "snap:1",
                "global_evidence_id": "evidence:1",
                "prompt_visible_payload": {"text": "Supported evidence."},
            }
        ],
        prior_artifacts={},
    )

    assert '"abstention_reasons"' in prompt
    assert "critic_non_pass" in prompt
    assert "Supply exactly one of quote or json_pointer" in prompt
    assert "at least 12 non-padding characters after strip" in prompt
    assert "mission_pointer is an RFC 6901 JSON Pointer" in prompt
    assert "Do not add wrapper fields" in prompt
    assert "flip_conditions[].predicate" in prompt
    assert "query_terms" in prompt
    assert "dossier-only" in prompt
