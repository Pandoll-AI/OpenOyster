"""Runtime vertical slice tests for Autonomous Deliberation D1."""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from openoyster.config import Settings
from openoyster.deliberation_contracts import CitationAnchor, Mission, canonical_json, mission_digest
from openoyster.llm import LLMProvider
from openoyster.models import (
    DeliberationArtifact,
    DeliberationCognitiveImpact,
    DeliberationDossier,
    DeliberationEvidenceSnapshot,
    DeliberationPackScope,
    DeliberationReplayResult,
    DeliberationRun,
    DeliberationStageCall,
    PackEvidence,
    PackInstall,
)
from openoyster.schemas import TextAnalysis
from openoyster.services import deliberation, opencrab_packs, pack_retrieval
from openoyster.services.deliberation_gates import (
    EvidenceSnapshotView,
    StageGateError,
    validate_anchor,
)
from openoyster.services.deliberation_replay import replay_deliberation
from openoyster.utils import sha256_text, utcnow

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
EMPTY_EVIDENCE_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f7-empty-evidence"
)
MISSION_PATH = PROJECT_ROOT / "tests/fixtures/deliberation_d1/mission_happy.json"


def _copy_fixture(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest)
    return dest


def _load_mission() -> Mission:
    return Mission.model_validate(json.loads(MISSION_PATH.read_text(encoding="utf-8")))


def _install_fixture(
    session: Session,
    settings: Settings,
    tmp_path: Path,
    fixture: Path,
    *,
    pack_id: str | None = None,
    dirname: str = "pack-a",
) -> PackInstall:
    pack_dir = _copy_fixture(fixture, tmp_path / dirname)
    if pack_id is not None:
        manifest = pack_dir / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["pack_id"] = pack_id
        manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result = opencrab_packs.install_pack(
        session,
        pack_dir,
        workspace=settings.workspace,
        profile="compatible",
    )
    session.commit()
    install = session.get(PackInstall, result.pack_install_id)
    assert install is not None
    return install


def _install_minimal(
    session: Session,
    settings: Settings,
    tmp_path: Path,
    *,
    pack_id: str | None = None,
    dirname: str = "pack-a",
) -> PackInstall:
    return _install_fixture(
        session,
        settings,
        tmp_path,
        MINIMAL_FIXTURE,
        pack_id=pack_id,
        dirname=dirname,
    )


class CountingProvider(LLMProvider):
    """Records query_json calls and delegates to the real stub."""

    name = "counting-stub"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.calls.append(stage)
        from openoyster.services.llm_judges import stub_query_json

        return stub_query_json(prompt, stage)


class FailingProvider(LLMProvider):
    name = "failing-provider"

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        del prompt, stage
        raise RuntimeError("provider unavailable")


class CriticGapProvider(CountingProvider):
    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        payload = super().query_json(prompt, stage)
        if stage == "deliberation_critic":
            return {
                "verdict": "revise",
                "issues": [
                    {
                        "code": "missing_option",
                        "artifact_ref": "options",
                        "detail": "A contract-backed option is missing.",
                    }
                ],
                "findings": [
                    {
                        "text": "The handoff contract is not established.",
                        "classification": "gap",
                        "artifact_ref": "options",
                        "unresolved_question": "What handoff schema and integrity fields are supported?",
                    }
                ],
            }
        return payload


def test_frozen_install_id_retrieval_ignores_active_status(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        install_id = install.id
        install.status = "inactive"
        session.commit()

        # Active-scope search finds nothing.
        active = pack_retrieval.search_pack_context(
            session, "supports this claim", pack_ids=[install.pack_id]
        )
        assert not active.evidence

        frozen = pack_retrieval.search_pack_context(
            session,
            "supports this claim",
            pack_install_ids=[install_id],
        )
        assert frozen.evidence
        assert all(row.pack_install_id == install_id for row in frozen.evidence)


def test_happy_path_exactly_five_llm_calls_and_persists_outputs(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        source_digest_before = install.source_digest
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="happy-path-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        run_id = run.id

        assert provider.calls == [
            "deliberation_beliefs",
            "deliberation_options",
            "deliberation_scenarios",
            "deliberation_critic",
            "deliberation_decision",
        ]
        loaded = session.get(DeliberationRun, run_id)
        assert loaded is not None
        assert loaded.status == "completed"
        assert loaded.outcome in {"select", "abstain"}
        assert loaded.llm_attempt_count == 5
        assert session.scalars(
            select(DeliberationStageCall).where(DeliberationStageCall.run_id == run_id)
        ).all()
        kinds = {
            row.kind
            for row in session.scalars(
                select(DeliberationArtifact).where(DeliberationArtifact.run_id == run_id)
            ).all()
        }
        assert {
            "beliefs",
            "options",
            "scenarios",
            "critic_result",
            "decision",
            "flip_conditions",
            "knowledge_requests",
        } <= kinds
        assert session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == run_id)
        )
        assert session.scalar(
            select(DeliberationCognitiveImpact).where(
                DeliberationCognitiveImpact.run_id == run_id
            )
        )
        snaps = session.scalars(
            select(DeliberationEvidenceSnapshot).where(
                DeliberationEvidenceSnapshot.run_id == run_id
            )
        ).all()
        assert 1 <= len(snaps) <= 24
        install_after = session.get(PackInstall, install.id)
        assert install_after is not None
        assert install_after.source_digest == source_digest_before


def test_no_evidence_completes_abstention_with_zero_llm_calls(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        # Remove evidence rows so retrieval is empty while pack remains installed.
        for row in session.scalars(
            select(PackEvidence).where(PackEvidence.pack_install_id == install.id)
        ).all():
            session.delete(row)
        session.commit()

        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="no-evidence-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert provider.calls == []
        assert run.status == "completed"
        assert run.outcome == "abstain"
        assert run.llm_attempt_count == 0
        decision = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "decision",
            )
        )
        assert decision is not None
        assert decision.payload_json.get("outcome") == "abstain"
        reasons = decision.payload_json.get("abstention_reasons") or []
        assert "no_evidence" in reasons
        knowledge = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "knowledge_requests",
            )
        )
        assert knowledge is not None
        requests = knowledge.payload_json.get("knowledge_requests") or []
        assert requests == [
            {
                "local_key": "kr_no_evidence",
                "question": mission.decision_question,
                "gap_ref": "evidence:no_evidence",
                "priority": "critical",
                "retrieval_status": "pack_has_no_evidence",
            }
        ]


def test_empty_evidence_pack_abstains_with_pack_has_no_evidence(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            EMPTY_EVIDENCE_FIXTURE,
            dirname="empty-evidence-pack",
        )
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="empty-evidence-pack-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert provider.calls == []
        assert run.status == "completed"
        assert run.outcome == "abstain"
        knowledge = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "knowledge_requests",
            )
        )
        assert knowledge is not None
        requests = knowledge.payload_json.get("knowledge_requests") or []
        assert len(requests) == 1
        assert requests[0]["local_key"] == "kr_no_evidence"
        assert requests[0]["retrieval_status"] == "pack_has_no_evidence"


def test_no_match_in_pack_evidence_abstains_with_retrieval_status(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission().model_copy(
        update={"decision_question": "zzz qqq xxx unmatched lexical query"}
    )
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        evidence_count = int(
            session.scalar(
                select(func.count())
                .select_from(PackEvidence)
                .where(PackEvidence.pack_install_id == install.id)
            )
            or 0
        )
        assert evidence_count > 0

        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="no-match-in-pack-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert provider.calls == []
        assert run.status == "completed"
        assert run.outcome == "abstain"
        knowledge = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "knowledge_requests",
            )
        )
        assert knowledge is not None
        requests = knowledge.payload_json.get("knowledge_requests") or []
        assert len(requests) == 1
        assert requests[0]["local_key"] == "kr_no_evidence"
        assert requests[0]["retrieval_status"] == "no_match_in_pack_evidence"


def test_provider_failure_is_execution_failure_not_epistemic_abstention(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="provider-failure-1",
            provider=FailingProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )

        assert run.status == "failed_execution"
        assert run.outcome is None
        assert run.failure_code == "provider_error"
        assert run.completed_at is not None
        assert session.scalars(
            select(DeliberationArtifact).where(DeliberationArtifact.run_id == run.id)
        ).all() == []
        assert session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == run.id)
        ) is None


def test_linked_redeliberation_fulfills_knowledge_request_and_records_cognitive_transition(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    mission = _load_mission()
    with session_factory() as session:
        parent_install = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="continuity-parent-pack",
            dirname="continuity-parent-pack",
        )
        for row in session.scalars(
            select(PackEvidence).where(PackEvidence.pack_install_id == parent_install.id)
        ).all():
            session.delete(row)
        session.commit()
        parent = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[parent_install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="continuity-parent-1",
            provider=CountingProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        assert parent.outcome == "abstain"
        parent_dossier = session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == parent.id)
        )
        assert parent_dossier is not None
        parent_digest_before = parent_dossier.json_digest

        child_install = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="continuity-child-pack",
            dirname="continuity-child-pack",
        )
        child = deliberation.continue_deliberation(
            session,
            parent_run_id=parent.id,
            pack_ids=[child_install.pack_id],
            impact_baseline_pack_ids=[],
            fulfilled_knowledge_request_keys=["kr_no_evidence"],
            idempotency_key="continuity-child-1",
            provider=CountingProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )

        assert child.status == "completed"
        assert child.parent_run_id == parent.id
        transition = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == child.id,
                DeliberationArtifact.kind == "cognitive_transition",
            )
        )
        assert transition is not None
        payload = transition.payload_json
        assert payload["method"] == "cognitive_transition_v2"
        assert payload["parent_run_id"] == parent.id
        assert payload["child_run_id"] == child.id
        assert payload["fulfilled_knowledge_requests"] == [
            {
                "local_key": "kr_no_evidence",
                "question": mission.decision_question,
                "gap_ref": "evidence:no_evidence",
                "priority": "critical",
                "retrieval_status": "pack_has_no_evidence",
                "status": "verified_fulfilled",
                "verification_method": "added_cited_evidence_v1",
                "verification_evidence_ids": payload["citation_scope_changes"][
                    "added_global_evidence_ids"
                ],
            }
        ]
        assert payload["claimed_knowledge_requests"][0]["status"] == "claimed_fulfilled"
        assert payload["unverified_claimed_knowledge_requests"] == []
        assert "decision_change" in payload
        assert "belief_changes" in payload
        assert "option_changes" in payload
        assert "citation_scope_changes" in payload
        assert "pack_diff" not in payload

        child_dossier = session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == child.id)
        )
        assert child_dossier is not None
        assert child_dossier.dossier_json["parent_run_id"] == parent.id
        assert child_dossier.dossier_json["cognitive_transition"] == payload

        parent_dossier_after = session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == parent.id)
        )
        assert parent_dossier_after is not None
        assert parent_dossier_after.json_digest == parent_digest_before

        replay = replay_deliberation(session, child.id)
        assert replay.matched is True


def test_claimed_fulfillment_without_new_cited_evidence_remains_unverified(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    mission = _load_mission()
    with session_factory() as session:
        parent_install = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="unverified-parent-pack",
            dirname="unverified-parent-pack",
        )
        child_install = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="unverified-child-pack",
            dirname="unverified-child-pack",
        )
        for install in (parent_install, child_install):
            for row in session.scalars(
                select(PackEvidence).where(PackEvidence.pack_install_id == install.id)
            ).all():
                session.delete(row)
        session.commit()

        parent = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[parent_install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="unverified-parent-run",
            provider=CountingProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        child = deliberation.continue_deliberation(
            session,
            parent_run_id=parent.id,
            pack_ids=[child_install.pack_id],
            impact_baseline_pack_ids=[],
            fulfilled_knowledge_request_keys=["kr_no_evidence"],
            idempotency_key="unverified-child-run",
            provider=CountingProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        transition = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == child.id,
                DeliberationArtifact.kind == "cognitive_transition",
            )
        )
        assert transition is not None
        payload = transition.payload_json
        assert payload["claimed_knowledge_requests"][0]["status"] == "claimed_fulfilled"
        assert payload["verified_fulfilled_knowledge_requests"] == []
        assert payload["fulfilled_knowledge_requests"] == []
        assert payload["unverified_claimed_knowledge_requests"][0]["status"] == (
            "claimed_unverified"
        )
        assert payload["remaining_knowledge_requests"][0]["local_key"] == "kr_no_evidence"


def test_critic_gap_is_promoted_to_a_persisted_knowledge_request(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="critic-gap-pack",
            dirname="critic-gap-pack",
        )
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="critic-gap-run",
            provider=CriticGapProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )

        assert run.outcome == "abstain"
        knowledge = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "knowledge_requests",
            )
        )
        assert knowledge is not None
        assert knowledge.payload_json["knowledge_requests"] == [
            {
                "local_key": "kr_critic_1",
                "question": "What handoff schema and integrity fields are supported?",
                "gap_ref": "options",
                "priority": "important",
            }
        ]


def test_continuation_idempotency_rejects_an_unrelated_run(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    mission = _load_mission()
    with session_factory() as session:
        parent_install = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="continuity-idempotency-parent",
            dirname="continuity-idempotency-parent",
        )
        for row in session.scalars(
            select(PackEvidence).where(PackEvidence.pack_install_id == parent_install.id)
        ).all():
            session.delete(row)
        session.commit()
        parent = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[parent_install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="continuity-idempotency-parent-run",
            provider=CountingProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )

        child_install = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="continuity-idempotency-child",
            dirname="continuity-idempotency-child",
        )
        unrelated = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[child_install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="continuity-idempotency-conflict",
            provider=CountingProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        assert unrelated.parent_run_id is None

        with pytest.raises(deliberation.DeliberationContinuationError) as exc_info:
            deliberation.continue_deliberation(
                session,
                parent_run_id=parent.id,
                pack_ids=[child_install.pack_id],
                impact_baseline_pack_ids=[],
                fulfilled_knowledge_request_keys=["kr_no_evidence"],
                idempotency_key="continuity-idempotency-conflict",
                provider=CountingProvider(),
                settings=temp_settings,
                allow_compatible_packs=True,
            )
        assert exc_info.value.code == "idempotency_key_conflict"


def test_continuation_rejects_invalid_persisted_parent_states(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    mission = _load_mission()
    with session_factory() as session:
        parent_install = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="continuation-rejections-parent",
            dirname="continuation-rejections-parent",
        )
        for row in session.scalars(
            select(PackEvidence).where(PackEvidence.pack_install_id == parent_install.id)
        ).all():
            session.delete(row)
        session.commit()
        parent = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[parent_install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="continuation-rejections-parent-run",
            provider=CountingProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert parent.status == "completed"
        assert parent.outcome == "abstain"

        child_install = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="continuation-rejections-child",
            dirname="continuation-rejections-child",
        )

        def assert_rejected(
            expected_code: str,
            *,
            parent_run_id: int = parent.id,
            fulfilled_keys: list[str] | None = None,
        ) -> None:
            with pytest.raises(deliberation.DeliberationContinuationError) as exc_info:
                deliberation.continue_deliberation(
                    session,
                    parent_run_id=parent_run_id,
                    pack_ids=[child_install.pack_id],
                    impact_baseline_pack_ids=[],
                    fulfilled_knowledge_request_keys=(
                        fulfilled_keys if fulfilled_keys is not None else ["kr_no_evidence"]
                    ),
                    idempotency_key=f"continuation-rejections-{expected_code}",
                    provider=CountingProvider(),
                    settings=temp_settings,
                    allow_compatible_packs=True,
                )
            assert exc_info.value.code == expected_code

        assert_rejected("parent_run_not_found", parent_run_id=parent.id + 1000)
        assert_rejected("fulfilled_knowledge_request_keys_empty", fulfilled_keys=[])
        assert_rejected(
            "fulfilled_knowledge_request_keys_unknown", fulfilled_keys=["kr_unknown"]
        )

        knowledge_requests = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == parent.id,
                DeliberationArtifact.kind == "knowledge_requests",
            )
        )
        assert knowledge_requests is not None
        session.delete(knowledge_requests)
        session.commit()
        assert_rejected("parent_knowledge_requests_missing")

        parent.status = "failed_execution"
        session.commit()
        assert_rejected("parent_run_not_completed_abstain")


def test_unknown_citation_rejects_stage_and_does_not_store_artifact(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    class BadCitationProvider(LLMProvider):
        name = "bad-citation"

        def analyse_batch(
            self, texts: list[str], policy: dict[str, Any] | None = None
        ) -> list[TextAnalysis]:
            del texts, policy
            raise AssertionError("unused")

        def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
            if stage != "deliberation_beliefs":
                raise AssertionError(f"should fail before {stage}")
            return {
                "beliefs": [
                    {
                        "local_key": "b1",
                        "statement": {
                            "text": "Invented fact",
                            "classification": "grounded_fact",
                            "anchors": [
                                {
                                    "evidence_snapshot_id": "snap:does-not-exist",
                                    "quote": "not real",
                                }
                            ],
                        },
                        "status": "supported",
                        "supporting_anchors": [
                            {
                                "evidence_snapshot_id": "snap:does-not-exist",
                                "quote": "not real",
                            }
                        ],
                    }
                ]
            }

    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="bad-citation-1",
            provider=BadCitationProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.status == "completed"
        assert run.outcome == "abstain"
        belief_artifacts = session.scalars(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "beliefs",
            )
        ).all()
        assert belief_artifacts == []


def test_out_of_scope_pack_snapshot_citation_is_rejected() -> None:
    anchor = CitationAnchor.model_validate(
        {
            "evidence_snapshot_id": "snap:other-pack",
            "quote": "This source supports this claim.",
        }
    )
    with pytest.raises(StageGateError, match="not in run snapshot") as caught:
        validate_anchor(anchor, {})
    assert caught.value.code == "unknown_citation"


def test_metadata_value_cannot_masquerade_as_exact_quote() -> None:
    record_hash = "a" * 64
    snapshot = EvidenceSnapshotView(
        snapshot_key="snap:1",
        db_id=1,
        global_evidence_id="pack://evidence/1",
        text="This source supports this claim.",
        payload={
            "text": "This source supports this claim.",
            "record_hash": record_hash,
            "global_evidence_id": "pack://evidence/1",
        },
        pack_install_id=1,
        record_hash=record_hash,
    )
    anchor = CitationAnchor.model_validate(
        {"evidence_snapshot_id": "snap:1", "quote": record_hash}
    )
    with pytest.raises(StageGateError) as caught:
        validate_anchor(anchor, {"snap:1": snapshot})
    assert caught.value.code == "quote_mismatch"


def test_json_pointer_digest_mismatch_is_rejected() -> None:
    payload = {"text": "This source supports this claim."}
    snapshot = EvidenceSnapshotView(
        snapshot_key="snap:1",
        db_id=1,
        global_evidence_id="pack://evidence/1",
        text=payload["text"],
        payload=payload,
        pack_install_id=1,
        record_hash="b" * 64,
    )
    assert sha256_text(canonical_json(payload["text"])) != "0" * 64
    anchor = CitationAnchor.model_validate(
        {
            "evidence_snapshot_id": "snap:1",
            "json_pointer": "/text",
            "value_digest": "0" * 64,
        }
    )
    with pytest.raises(StageGateError) as caught:
        validate_anchor(anchor, {"snap:1": snapshot})
    assert caught.value.code == "pointer_mismatch"


def test_scope_freeze_survives_active_pack_change(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path, dirname="pack-a")
        frozen_install_id = install.id
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="freeze-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        scopes = session.scalars(
            select(DeliberationPackScope).where(DeliberationPackScope.run_id == run.id)
        ).all()
        assert all(scope.pack_install_id == frozen_install_id for scope in scopes)

        # Change active pack after freeze: run scope rows stay pinned.
        install.status = "inactive"
        session.commit()
        scopes_after = session.scalars(
            select(DeliberationPackScope).where(DeliberationPackScope.run_id == run.id)
        ).all()
        assert {s.pack_install_id for s in scopes_after} == {frozen_install_id}


def test_idempotency_key_returns_same_run_without_extra_llm_calls(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        first = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="idem-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        first_calls = list(provider.calls)
        second = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="idem-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert second.id == first.id
        assert provider.calls == first_calls
        count = session.scalars(
            select(DeliberationRun).where(DeliberationRun.idempotency_key == "idem-1")
        ).all()
        assert len(count) == 1


def test_critic_non_pass_forces_abstention(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    class CriticFailProvider(LLMProvider):
        name = "critic-fail"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def analyse_batch(
            self, texts: list[str], policy: dict[str, Any] | None = None
        ) -> list[TextAnalysis]:
            del texts, policy
            raise AssertionError("unused")

        def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
            self.calls.append(stage)
            from openoyster.services.llm_judges import stub_query_json

            payload = stub_query_json(prompt, stage)
            if stage == "deliberation_critic":
                payload = {
                    "verdict": "revise",
                    "issues": [
                        {
                            "code": "missing_opposing_evidence",
                            "artifact_ref": "beliefs:b1",
                            "detail": "forced fail",
                        }
                    ],
                    "findings": [
                        {
                            "text": "Opposing evidence missing",
                            "classification": "structural",
                            "issue_code": "missing_opposing_evidence",
                            "artifact_ref": "beliefs:b1",
                        }
                    ],
                }
            return payload

    provider = CriticFailProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="critic-fail-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.outcome == "abstain"
        decision = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "decision",
            )
        )
        assert decision is not None
        assert decision.payload_json.get("outcome") == "abstain"
        assert "critic_non_pass" in (decision.payload_json.get("abstention_reasons") or [])


def test_replay_matches_and_detects_tamper(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="replay-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        match = replay_deliberation(session, run.id)
        session.commit()
        assert match.matched is True
        assert session.scalars(
            select(DeliberationReplayResult).where(DeliberationReplayResult.run_id == run.id)
        ).all()

        beliefs = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "beliefs",
            )
        )
        assert beliefs is not None
        beliefs.payload_json = {"beliefs": []}
        session.commit()

        mismatch = replay_deliberation(session, run.id)
        session.commit()
        assert mismatch.matched is False
        assert "dossier_json_digest" in mismatch.result_json.get("mismatches", [])


def test_replay_detects_evidence_snapshot_payload_tamper(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="replay-evidence-tamper-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        snapshot = session.scalar(
            select(DeliberationEvidenceSnapshot).where(
                DeliberationEvidenceSnapshot.run_id == run.id
            )
        )
        assert snapshot is not None
        original = dict(snapshot.prompt_visible_payload_json)
        snapshot.prompt_visible_payload_json = {
            **original,
            "source": {"title": "tampered without updating the stored digest"},
        }
        session.commit()

        mismatch = replay_deliberation(session, run.id)
        session.commit()
        assert mismatch.matched is False
        assert "evidence_snapshot_digest" in mismatch.result_json.get("mismatches", [])


def test_prompt_limit_becomes_deterministic_abstention_without_llm_call(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CountingProvider()
    mission = _load_mission()

    def reject_oversized_prompt(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        raise ValueError("prompt exceeds deterministic D1 limit")

    monkeypatch.setattr(deliberation, "build_stage_prompt", reject_oversized_prompt)
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="prompt-limit-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        assert run.status == "completed"
        assert run.outcome == "abstain"
        assert run.llm_attempt_count == 0
        assert provider.calls == []


def test_expired_started_stage_becomes_indeterminate_without_llm_recall(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CountingProvider()
    mission = _load_mission()

    def crash_after_response(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise KeyboardInterrupt("simulated process death after provider response")

    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        pack_id = install.pack_id
        monkeypatch.setattr(deliberation, "validate_stage", crash_after_response)
        with pytest.raises(KeyboardInterrupt):
            deliberation.run_deliberation(
                session,
                mission,
                pack_ids=[pack_id],
                impact_baseline_pack_ids=[],
                idempotency_key="expired-stage-1",
                provider=provider,
                settings=temp_settings,
                allow_compatible_packs=True,
            )
    assert provider.calls == ["deliberation_beliefs"]

    with session_factory() as session:
        interrupted = session.scalar(
            select(DeliberationRun).where(
                DeliberationRun.idempotency_key == "expired-stage-1"
            )
        )
        assert interrupted is not None
        interrupted.lease_until = utcnow() - timedelta(seconds=1)
        session.commit()

    recovery_provider = CountingProvider()
    with session_factory() as session:
        recovered = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="expired-stage-1",
            provider=recovery_provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        assert recovered.status == "indeterminate"
        assert recovered.failure_code == "post_call_persistence_ambiguous"
        assert recovery_provider.calls == []


def test_cognitive_impact_identical_scope_is_retained(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="impact-same-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        impact = session.scalar(
            select(DeliberationCognitiveImpact).where(
                DeliberationCognitiveImpact.run_id == run.id
            )
        )
        assert impact is not None
        assert impact.method == "citation_scope_projection_v2"
        payload = impact.impact_json
        assert payload.get("decision_support") == "retained"
        grounded = payload.get("grounded_assertions") or []
        assert grounded
        assert all(item.get("support") == "retained" for item in grounded)


def test_cognitive_impact_empty_baseline_marks_unsupported(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="impact-empty-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        impact = session.scalar(
            select(DeliberationCognitiveImpact).where(
                DeliberationCognitiveImpact.run_id == run.id
            )
        )
        assert impact is not None
        payload = impact.impact_json
        grounded = payload.get("grounded_assertions") or []
        if grounded:
            assert any(item.get("support") == "unsupported" for item in grounded)
            assert payload.get("decision_support") in {"weakened", "lost"}


def test_mission_digest_frozen_on_run(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    provider = CountingProvider()
    mission = _load_mission()
    digest = mission_digest(mission)
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="mission-digest-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.mission_digest == digest


def test_quote_mismatch_rejects_anchor(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    class QuoteMismatchProvider(LLMProvider):
        name = "quote-mismatch"

        def analyse_batch(
            self, texts: list[str], policy: dict[str, Any] | None = None
        ) -> list[TextAnalysis]:
            del texts, policy
            raise AssertionError("unused")

        def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
            from openoyster.services.llm_judges import _extract_deliberation_snapshot_keys

            if stage != "deliberation_beliefs":
                raise AssertionError(f"should fail before {stage}")
            keys = _extract_deliberation_snapshot_keys(prompt)
            snap = keys[0] if keys else "snap:1"
            return {
                "beliefs": [
                    {
                        "local_key": "b1",
                        "statement": {
                            "text": "Bad quote",
                            "classification": "grounded_fact",
                            "anchors": [
                                {
                                    "evidence_snapshot_id": snap,
                                    "quote": "this quote is not in the evidence text",
                                }
                            ],
                        },
                        "status": "supported",
                        "supporting_anchors": [
                            {
                                "evidence_snapshot_id": snap,
                                "quote": "this quote is not in the evidence text",
                            }
                        ],
                    }
                ]
            }

    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="quote-mismatch-1",
            provider=QuoteMismatchProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.outcome == "abstain"
        assert (
            session.scalars(
                select(DeliberationArtifact).where(
                    DeliberationArtifact.run_id == run.id,
                    DeliberationArtifact.kind == "beliefs",
                )
            ).all()
            == []
        )


def test_legacy_null_fingerprint_lazy_backfill_then_mismatch(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """RED→GREEN #4: NULL fingerprint fills once, then fail-closed on mismatch.

    First reuse of a legacy NULL-fingerprint row accepts the presented
    fingerprint and persists it. A later request with a different fingerprint
    on the same idempotency key raises idempotency_request_mismatch.
    """
    mission = _load_mission()
    with session_factory() as session:
        install_a = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="legacy-fp-pack-a",
            dirname="legacy-fp-pack-a",
        )
        install_b = _install_minimal(
            session,
            temp_settings,
            tmp_path,
            pack_id="legacy-fp-pack-b",
            dirname="legacy-fp-pack-b",
        )
        # Simulate a pre-0007/incomplete-backfill row: completed but NULL fingerprint.
        legacy = DeliberationRun(
            idempotency_key="legacy-fp-key",
            request_fingerprint=None,
            fulfilled_request_keys_json=[],
            parent_run_id=None,
            mission_snapshot_json=mission.model_dump(mode="json"),
            mission_digest=mission_digest(mission),
            policy_snapshot_json={"allow_compatible_packs": True},
            runtime_config_json={},
            policy_digest="p" * 64,
            runtime_config_digest="r" * 64,
            contract_version="deliberation-d1-v1",
            prompt_template_version="deliberation-prompts-d1-v8",
            primary_scope_digest="s" * 64,
            impact_baseline_scope_digest="i" * 64,
            status="completed",
            outcome="select",
            llm_attempt_count=0,
        )
        session.add(legacy)
        session.commit()
        legacy_id = legacy.id

        first = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install_a.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="legacy-fp-key",
            provider=CountingProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        assert first.id == legacy_id
        reloaded = session.get(DeliberationRun, legacy_id)
        assert reloaded is not None
        assert reloaded.request_fingerprint is not None
        filled = reloaded.request_fingerprint

        with pytest.raises(deliberation.DeliberationContinuationError) as exc_info:
            deliberation.run_deliberation(
                session,
                mission,
                pack_ids=[install_b.pack_id],
                impact_baseline_pack_ids=[],
                idempotency_key="legacy-fp-key",
                provider=CountingProvider(),
                settings=temp_settings,
                allow_compatible_packs=True,
            )
        assert exc_info.value.code == "idempotency_request_mismatch"
        reloaded2 = session.get(DeliberationRun, legacy_id)
        assert reloaded2 is not None
        assert reloaded2.request_fingerprint == filled


def test_assert_request_fingerprint_does_not_commit_unrelated_dirty(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """R2: lazy fill must not session.commit() unrelated dirty rows on the Session."""
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        # Unrelated dirty row that must stay uncommitted after fingerprint helper.
        decoy = DeliberationRun(
            idempotency_key="decoy-dirty-row",
            request_fingerprint=None,
            fulfilled_request_keys_json=[],
            parent_run_id=None,
            mission_snapshot_json=mission.model_dump(mode="json"),
            mission_digest=mission_digest(mission),
            policy_snapshot_json={"allow_compatible_packs": True},
            runtime_config_json={},
            policy_digest="p" * 64,
            runtime_config_digest="r" * 64,
            contract_version="deliberation-d1-v1",
            prompt_template_version="deliberation-prompts-d1-v8",
            primary_scope_digest="s" * 64,
            impact_baseline_scope_digest="i" * 64,
            status="completed",
            outcome="select",
            llm_attempt_count=0,
        )
        session.add(decoy)
        session.flush()
        decoy_id = decoy.id

        legacy = DeliberationRun(
            idempotency_key="legacy-fp-no-commit",
            request_fingerprint=None,
            fulfilled_request_keys_json=[],
            parent_run_id=None,
            mission_snapshot_json=mission.model_dump(mode="json"),
            mission_digest=mission_digest(mission),
            policy_snapshot_json={"allow_compatible_packs": True},
            runtime_config_json={},
            policy_digest="p" * 64,
            runtime_config_digest="r" * 64,
            contract_version="deliberation-d1-v1",
            prompt_template_version="deliberation-prompts-d1-v8",
            primary_scope_digest="s" * 64,
            impact_baseline_scope_digest="i" * 64,
            status="completed",
            outcome="select",
            llm_attempt_count=0,
        )
        session.add(legacy)
        session.flush()
        legacy_id = legacy.id

        # Present a real fingerprint for the legacy row.
        fp = deliberation.compute_request_fingerprint(
            mission_digest_value=mission_digest(mission),
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            allow_compatible_packs=True,
            parent_run_id=None,
            fulfilled_keys=None,
        )
        deliberation._assert_request_fingerprint(session, legacy, fp)

        # Same session sees the fill, but helper must not have committed the decoy.
        session.expire_all()
        # Open a fresh connection/session view of durable state.
        with session_factory() as other:
            durable_decoy = other.get(DeliberationRun, decoy_id)
            durable_legacy = other.get(DeliberationRun, legacy_id)
            # Decoy was never committed by the helper.
            assert durable_decoy is None
            # Legacy fill also not committed yet (caller owns commit).
            assert durable_legacy is None

        # After explicit commit by caller, both persist; second fp mismatches.
        session.commit()
        reloaded = session.get(DeliberationRun, legacy_id)
        assert reloaded is not None
        assert reloaded.request_fingerprint == fp

        with pytest.raises(deliberation.DeliberationContinuationError) as exc_info:
            deliberation._assert_request_fingerprint(session, reloaded, "f" * 64)
        assert exc_info.value.code == "idempotency_request_mismatch"
        # silence unused pack install in lint-free path
        assert install.pack_id
