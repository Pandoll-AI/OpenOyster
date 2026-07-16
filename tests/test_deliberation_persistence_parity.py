"""Parity tests: every gated assertion/anchor is persisted; KR/transition completeness."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openoyster.deliberation_contracts import (
    BeliefsStagePayload,
    OptionsStagePayload,
    payload_digest,
)
from openoyster.models import (
    DeliberationArtifact,
    DeliberationAssertion,
    DeliberationCitation,
    DeliberationEvidenceSnapshot,
    DeliberationPackScope,
    DeliberationRun,
    PackEvidence,
    PackInstall,
)
from openoyster.services import deliberation
from openoyster.services.cognitive_transition import persist_cognitive_transition
from openoyster.utils import utcnow

QUOTE = "This source supports this claim."


def _seed_run_with_snapshot(
    session: Session,
    *,
    snap_key: str = "snap:1",
    pack_install_id: int | None = None,
) -> tuple[DeliberationRun, DeliberationArtifact, dict[str, int], PackInstall]:
    """Minimal run + artifact + evidence snapshot for direct persistence tests."""
    install = PackInstall(
        pack_id=f"pack-{utcnow().timestamp()}",
        declared_version="0.0.1",
        source_digest=f"digest-{utcnow().timestamp()}-{id(session)}"[:64].ljust(64, "0"),
        source_type="directory",
        source_location="/tmp/pack",
        storage_uri="/tmp/pack",
        admission_profile="compatible",
        status="active",
        original_manifest_json={},
        admission_report_json={},
    )
    session.add(install)
    session.flush()
    if pack_install_id is not None:
        # re-bind callers that already fixed an install id is not used here
        pass

    evidence = PackEvidence(
        pack_install_id=install.id,
        local_evidence_id="evidence:1",
        global_evidence_id=f"pack://{install.pack_id}/evidence/1",
        kind="text_chunk",
        source_json={"title": "Source"},
        parser_json={},
        location_json={},
        links_json={},
        text=QUOTE,
        raw_record_json={},
        record_hash="a" * 64,
    )
    session.add(evidence)
    session.flush()

    run = DeliberationRun(
        idempotency_key=f"parity-{install.id}",
        mission_snapshot_json={"goal": "g", "decision_question": "q"},
        mission_digest="m" * 64,
        policy_snapshot_json={},
        runtime_config_json={},
        policy_digest="p" * 64,
        runtime_config_digest="r" * 64,
        contract_version="deliberation-d1-v1",
        prompt_template_version="deliberation-prompts-d1-v1",
        primary_scope_digest="s" * 64,
        impact_baseline_scope_digest="b" * 64,
        status="beliefs_ready",
        current_stage="deliberation_beliefs",
        outcome=None,
    )
    session.add(run)
    session.flush()

    snap = DeliberationEvidenceSnapshot(
        run_id=run.id,
        snapshot_key=snap_key,
        pack_evidence_id=evidence.id,
        global_evidence_id=evidence.global_evidence_id,
        local_evidence_id=evidence.local_evidence_id,
        pack_install_id=install.id,
        record_hash=evidence.record_hash,
        prompt_visible_payload_json={"text": QUOTE, "title": "Source"},
        payload_digest="d" * 64,
        retrieval_rank=1,
        retrieval_score=1.0,
    )
    session.add(snap)
    session.flush()

    art = DeliberationArtifact(
        run_id=run.id,
        stage_call_id=None,
        kind="beliefs",
        local_key="beliefs",
        payload_json={},
        payload_digest="x" * 64,
    )
    session.add(art)
    session.flush()

    return run, art, {snap_key: snap.id}, install


def _anchor(snap_key: str = "snap:1") -> dict[str, str]:
    return {"evidence_snapshot_id": snap_key, "quote": QUOTE}


def _grounded(text: str, snap_key: str = "snap:1") -> dict[str, Any]:
    return {
        "text": text,
        "classification": "grounded_fact",
        "anchors": [_anchor(snap_key)],
    }


def test_opposing_anchors_persisted_as_opposing_citations(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _run, art, snap_ids, _install = _seed_run_with_snapshot(session)
        model = BeliefsStagePayload.model_validate(
            {
                "beliefs": [
                    {
                        "local_key": "b1",
                        "statement": _grounded("Claim with opposition."),
                        "status": "contested",
                        "supporting_anchors": [_anchor()],
                        "opposing_anchors": [
                            {
                                "evidence_snapshot_id": "snap:1",
                                "quote": QUOTE,
                            }
                        ],
                        "assumptions": [],
                        "gaps": [],
                        "invalidation_conditions": [],
                    }
                ]
            }
        )
        deliberation._persist_stage_assertions(
            session,
            artifact=art,
            stage="deliberation_beliefs",
            model=model,
            snap_ids=snap_ids,
        )
        session.flush()

        statement = session.scalar(
            select(DeliberationAssertion).where(
                DeliberationAssertion.artifact_id == art.id,
                DeliberationAssertion.path == "beliefs.b1.statement",
            )
        )
        assert statement is not None
        citations = session.scalars(
            select(DeliberationCitation).where(
                DeliberationCitation.assertion_id == statement.id
            )
        ).all()
        roles = sorted(getattr(c, "role", None) for c in citations)
        assert "opposing" in roles, f"expected role=opposing citation, got roles={roles}"
        assert "supporting" in roles, f"expected role=supporting citation, got roles={roles}"
        assert "statement" in roles, f"expected role=statement citation, got roles={roles}"
        opposing = [c for c in citations if getattr(c, "role", None) == "opposing"]
        assert opposing and opposing[0].quote == QUOTE


def test_exclusion_reason_and_constraint_rationale_assertions_persisted(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _run, art, snap_ids, _install = _seed_run_with_snapshot(session)
        art.kind = "options"
        art.local_key = "options"
        session.flush()

        model = OptionsStagePayload.model_validate(
            {
                "options": [
                    {
                        "local_key": "opt_skip",
                        "label": {
                            "text": "Skip option",
                            "classification": "proposal",
                            "mission_pointer": "/goal",
                        },
                        "viable": False,
                        "constraint_judgements": [
                            {
                                "constraint_index": 0,
                                "satisfied": False,
                                "rationale": _grounded("Constraint blocks this option."),
                            }
                        ],
                        "supporting_belief_keys": [],
                        "opposing_belief_keys": [],
                        "risks": [],
                        "reversibility": "high",
                        "expected_outcome": {
                            "text": "Would be excluded",
                            "classification": "proposal",
                            "mission_pointer": "/goal",
                        },
                        "exclusion_reason": _grounded("Excluded by hard constraint."),
                    }
                ]
            }
        )
        deliberation._persist_stage_assertions(
            session,
            artifact=art,
            stage="deliberation_options",
            model=model,
            snap_ids=snap_ids,
        )
        session.flush()

        paths = {
            row.path
            for row in session.scalars(
                select(DeliberationAssertion).where(
                    DeliberationAssertion.artifact_id == art.id
                )
            ).all()
        }
        assert "options.opt_skip.exclusion_reason" in paths
        assert "options.opt_skip.constraint_judgements[0].rationale" in paths


def test_unclaimed_parent_knowledge_request_preserved_in_remaining(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        parent = DeliberationRun(
            idempotency_key="kr-parent",
            mission_snapshot_json={"goal": "g", "decision_question": "q"},
            mission_digest="m" * 64,
            policy_snapshot_json={},
            runtime_config_json={},
            policy_digest="p" * 64,
            runtime_config_digest="r" * 64,
            contract_version="deliberation-d1-v1",
            prompt_template_version="deliberation-prompts-d1-v1",
            primary_scope_digest="s" * 64,
            impact_baseline_scope_digest="b" * 64,
            status="completed",
            current_stage=None,
            outcome="abstain",
        )
        child = DeliberationRun(
            idempotency_key="kr-child",
            mission_snapshot_json={"goal": "g", "decision_question": "q"},
            mission_digest="m" * 64,
            policy_snapshot_json={},
            runtime_config_json={},
            policy_digest="p" * 64,
            runtime_config_digest="r" * 64,
            contract_version="deliberation-d1-v1",
            prompt_template_version="deliberation-prompts-d1-v1",
            primary_scope_digest="s" * 64,
            impact_baseline_scope_digest="b" * 64,
            status="completed",
            current_stage=None,
            outcome="select",
        )
        session.add_all([parent, child])
        session.flush()
        child.parent_run_id = parent.id
        session.flush()

        parent_krs = {
            "knowledge_requests": [
                {
                    "local_key": "kr_claimed",
                    "question": "claimed?",
                    "gap_ref": "evidence:no_evidence",
                    "priority": "critical",
                },
                {
                    "local_key": "kr_unclaimed",
                    "question": "still open?",
                    "gap_ref": "beliefs.b1.gaps[0]",
                    "priority": "important",
                },
            ]
        }
        child_krs = {
            "knowledge_requests": [
                {
                    "local_key": "kr_child_new",
                    "question": "child gap?",
                    "gap_ref": "options",
                    "priority": "important",
                }
            ]
        }
        for run, kind_payload in (
            (parent, parent_krs),
            (child, child_krs),
        ):
            session.add(
                DeliberationArtifact(
                    run_id=run.id,
                    stage_call_id=None,
                    kind="knowledge_requests",
                    local_key="knowledge_requests",
                    payload_json=kind_payload,
                    payload_digest=payload_digest(kind_payload),
                )
            )
        session.flush()

        transition = persist_cognitive_transition(
            session,
            parent_run=parent,
            child_run=child,
            fulfilled_knowledge_request_keys={"kr_claimed"},
        )
        remaining_keys = {
            item["local_key"] for item in transition.payload_json["remaining_knowledge_requests"]
        }
        assert "kr_unclaimed" in remaining_keys
        unclaimed = next(
            item
            for item in transition.payload_json["remaining_knowledge_requests"]
            if item["local_key"] == "kr_unclaimed"
        )
        # Original parent request preserved as-is (no claimed status rewrite).
        assert unclaimed["question"] == "still open?"
        assert unclaimed["gap_ref"] == "beliefs.b1.gaps[0]"
        assert "status" not in unclaimed
        assert "kr_child_new" in remaining_keys


def test_parent_cited_pack_missing_from_child_scope_sets_flag(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        parent_install = PackInstall(
            pack_id="parent-cited-pack",
            declared_version="0.0.1",
            source_digest=("p" * 64),
            source_type="directory",
            source_location="/tmp/p",
            storage_uri="/tmp/p",
            admission_profile="compatible",
            status="active",
            original_manifest_json={},
            admission_report_json={},
        )
        child_install = PackInstall(
            pack_id="child-only-pack",
            declared_version="0.0.1",
            source_digest=("c" * 64),
            source_type="directory",
            source_location="/tmp/c",
            storage_uri="/tmp/c",
            admission_profile="compatible",
            status="active",
            original_manifest_json={},
            admission_report_json={},
        )
        session.add_all([parent_install, child_install])
        session.flush()

        parent_ev = PackEvidence(
            pack_install_id=parent_install.id,
            local_evidence_id="evidence:1",
            global_evidence_id="pack://parent/evidence/1",
            kind="text_chunk",
            source_json={},
            parser_json={},
            location_json={},
            links_json={},
            text=QUOTE,
            raw_record_json={},
            record_hash="a" * 64,
        )
        session.add(parent_ev)
        session.flush()

        parent = DeliberationRun(
            idempotency_key="scope-parent",
            mission_snapshot_json={"goal": "g", "decision_question": "q"},
            mission_digest="m" * 64,
            policy_snapshot_json={},
            runtime_config_json={},
            policy_digest="p" * 64,
            runtime_config_digest="r" * 64,
            contract_version="deliberation-d1-v1",
            prompt_template_version="deliberation-prompts-d1-v1",
            primary_scope_digest="s" * 64,
            impact_baseline_scope_digest="b" * 64,
            status="completed",
            outcome="abstain",
        )
        child = DeliberationRun(
            idempotency_key="scope-child",
            mission_snapshot_json={"goal": "g", "decision_question": "q"},
            mission_digest="m" * 64,
            policy_snapshot_json={},
            runtime_config_json={},
            policy_digest="p" * 64,
            runtime_config_digest="r" * 64,
            contract_version="deliberation-d1-v1",
            prompt_template_version="deliberation-prompts-d1-v1",
            primary_scope_digest="s" * 64,
            impact_baseline_scope_digest="b" * 64,
            status="completed",
            outcome="select",
        )
        session.add_all([parent, child])
        session.flush()
        child.parent_run_id = parent.id
        session.flush()

        session.add(
            DeliberationPackScope(
                run_id=child.id,
                role="primary",
                pack_install_id=child_install.id,
                pack_id=child_install.pack_id,
                declared_version=child_install.declared_version,
                source_digest=child_install.source_digest,
                admission_profile="compatible",
                snapshot_json={},
            )
        )

        parent_art = DeliberationArtifact(
            run_id=parent.id,
            stage_call_id=None,
            kind="beliefs",
            local_key="beliefs",
            payload_json={},
            payload_digest="x" * 64,
        )
        session.add(parent_art)
        session.flush()
        assertion = DeliberationAssertion(
            artifact_id=parent_art.id,
            path="beliefs.b1.statement",
            text="Parent claim",
            classification="grounded_fact",
            metadata_json={},
        )
        session.add(assertion)
        session.flush()
        snap = DeliberationEvidenceSnapshot(
            run_id=parent.id,
            snapshot_key="snap:1",
            pack_evidence_id=parent_ev.id,
            global_evidence_id=parent_ev.global_evidence_id,
            local_evidence_id=parent_ev.local_evidence_id,
            pack_install_id=parent_install.id,
            record_hash=parent_ev.record_hash,
            prompt_visible_payload_json={"text": QUOTE},
            payload_digest="d" * 64,
            retrieval_rank=1,
            retrieval_score=1.0,
        )
        session.add(snap)
        session.flush()
        session.add(
            DeliberationCitation(
                assertion_id=assertion.id,
                evidence_snapshot_id=snap.id,
                quote=QUOTE,
            )
        )
        session.add(
            DeliberationArtifact(
                run_id=parent.id,
                stage_call_id=None,
                kind="knowledge_requests",
                local_key="knowledge_requests",
                payload_json={"knowledge_requests": []},
                payload_digest=payload_digest({"knowledge_requests": []}),
            )
        )
        session.flush()

        transition = persist_cognitive_transition(
            session,
            parent_run=parent,
            child_run=child,
            fulfilled_knowledge_request_keys=set(),
        )
        payload = transition.payload_json
        missing = payload.get("parent_cited_pack_install_ids_missing_from_child_scope")
        assert missing is not None
        assert parent_install.id in missing
        assert payload.get("parent_citation_scope_dropped") is True


def test_unknown_snapshot_key_raises_runtime_error(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _run, art, snap_ids, _install = _seed_run_with_snapshot(session)
        model = BeliefsStagePayload.model_validate(
            {
                "beliefs": [
                    {
                        "local_key": "b1",
                        "statement": {
                            "text": "Uses unknown snap",
                            "classification": "grounded_fact",
                            "anchors": [
                                {
                                    "evidence_snapshot_id": "snap:missing",
                                    "quote": QUOTE,
                                }
                            ],
                        },
                        "status": "supported",
                        "supporting_anchors": [],
                        "opposing_anchors": [],
                        "assumptions": [],
                        "gaps": [],
                        "invalidation_conditions": [],
                    }
                ]
            }
        )
        with pytest.raises(RuntimeError, match=r"unknown evidence snapshot key"):
            deliberation._persist_stage_assertions(
                session,
                artifact=art,
                stage="deliberation_beliefs",
                model=model,
                # Only snap:1 known — snap:missing must hard-fail.
                snap_ids=snap_ids,
            )
