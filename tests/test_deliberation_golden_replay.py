"""Golden replay + impact/transition digest recompute tests for Deliberation D1."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openoyster.config import Settings
from openoyster.deliberation_contracts import Mission, payload_digest
from openoyster.llm import LLMProvider
from openoyster.models import (
    DeliberationArtifact,
    DeliberationCognitiveImpact,
    DeliberationDossier,
    PackEvidence,
    PackInstall,
)
from openoyster.schemas import TextAnalysis
from openoyster.services import deliberation, opencrab_packs
from openoyster.services.deliberation_replay import replay_deliberation
from openoyster.services.llm_judges import stub_query_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
EMPTY_EVIDENCE_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f7-empty-evidence"
)
MISSION_PATH = PROJECT_ROOT / "tests/fixtures/deliberation_d1/mission_happy.json"

# Golden dossier digests for the fixed-clock stub runs below.
# Update rule: when the dossier contract intentionally changes, re-run this
# module, print the new digests from a failing assertion, and commit the new
# constants only after confirming the delta is intentional.
# 2026-07-16: rebased for the intentional `retrieval_trace` dossier key (W-A1
# query-expansion provenance), then the D3 flip-condition `predicate` field and
# prompt template v9, then the policy-snapshot max_llm_attempts change as the core
# (10) and auxiliary (4) budgets were split; each delta verified as intentional
# contract growth.
GOLDEN_SELECT_DOSSIER_JSON_DIGEST = (
    "5baf4fefa82054d72335ad21040ec7b608b3e1aae52177fa072d8b1047086873"
)
GOLDEN_NO_EVIDENCE_DOSSIER_JSON_DIGEST = (
    "b9313d0573f4b9632242d67d1fd6e671c1476caed8798502e51aa3a8fcd3f1a1"
)


def _load_mission() -> Mission:
    return Mission.model_validate(json.loads(MISSION_PATH.read_text(encoding="utf-8")))


def _install_fixture(
    session: Session,
    settings: Settings,
    tmp_path: Path,
    fixture: Path,
    *,
    dirname: str,
    pack_id: str | None = None,
) -> PackInstall:
    pack_dir = tmp_path / dirname
    shutil.copytree(fixture, pack_dir)
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


class StubProvider(LLMProvider):
    name = "stub"

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("unused")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        return stub_query_json(prompt, stage)


@pytest.fixture()
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin openoyster.utils.utcnow (and deliberation import) to a fixed sequence."""
    base = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    seq = iter(range(0, 100_000))

    def _utcnow() -> datetime:
        i = next(seq)
        return base + timedelta(microseconds=i)

    monkeypatch.setattr("openoyster.utils.utcnow", _utcnow)
    monkeypatch.setattr("openoyster.services.deliberation.utcnow", _utcnow)
    return base


def test_replay_detects_cognitive_impact_digest_mismatch(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    provider = StubProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_fixture(
            session, temp_settings, tmp_path, MINIMAL_FIXTURE, dirname="impact-pack"
        )
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="impact-tamper-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        match = replay_deliberation(session, run.id)
        session.commit()
        assert match.matched is True

        impact = session.scalar(
            select(DeliberationCognitiveImpact).where(
                DeliberationCognitiveImpact.run_id == run.id
            )
        )
        assert impact is not None
        # Tamper stored impact and its digest so recompute (from assertions) diverges.
        tampered = dict(impact.impact_json or {})
        tampered["decision_support"] = "lost"
        impact.impact_json = tampered
        impact.impact_digest = payload_digest(tampered)
        session.commit()

        mismatch = replay_deliberation(session, run.id)
        session.commit()
        assert mismatch.matched is False
        assert "cognitive_impact_digest" in mismatch.result_json.get("mismatches", [])


def test_replay_detects_cognitive_transition_digest_mismatch(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    mission = _load_mission()
    with session_factory() as session:
        parent_install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="trans-parent",
            pack_id="trans-parent-pack",
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
            idempotency_key="trans-parent-1",
            provider=StubProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        assert parent.outcome == "abstain"

        child_install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="trans-child",
            pack_id="trans-child-pack",
        )
        child = deliberation.continue_deliberation(
            session,
            parent_run_id=parent.id,
            pack_ids=[child_install.pack_id],
            impact_baseline_pack_ids=[],
            fulfilled_knowledge_request_keys=["kr_no_evidence"],
            idempotency_key="trans-child-1",
            provider=StubProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        match = replay_deliberation(session, child.id)
        session.commit()
        assert match.matched is True

        transition = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == child.id,
                DeliberationArtifact.kind == "cognitive_transition",
            )
        )
        assert transition is not None
        # Tamper content while keeping method=v3 so recompute still runs.
        # (Non-v3 methods skip recompute by design — see method_version tests.)
        tampered = dict(transition.payload_json or {})
        tampered["critic_verdict_change"] = {"from": "pass", "to": "tampered"}
        transition.payload_json = tampered
        transition.payload_digest = payload_digest(tampered)
        session.commit()

        mismatch = replay_deliberation(session, child.id)
        session.commit()
        assert mismatch.matched is False
        assert "cognitive_transition_digest" in mismatch.result_json.get("mismatches", [])


def test_replay_transition_fulfilled_keys_from_run_column_not_claimed(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """RED→GREEN #5: emptying claimed list must not self-source fulfilled keys.

    Replay reads fulfilled_request_keys_json from the run row. If keys came
    from the transition claimed list, wiping claimed would recompute with
    empty keys and match the tampered payload (circular verification).
    """
    mission = _load_mission()
    with session_factory() as session:
        parent_install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="circ-parent",
            pack_id="circ-parent-pack",
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
            idempotency_key="circ-parent-1",
            provider=StubProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        child_install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="circ-child",
            pack_id="circ-child-pack",
        )
        child = deliberation.continue_deliberation(
            session,
            parent_run_id=parent.id,
            pack_ids=[child_install.pack_id],
            impact_baseline_pack_ids=[],
            fulfilled_knowledge_request_keys=["kr_no_evidence"],
            idempotency_key="circ-child-1",
            provider=StubProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert child.fulfilled_request_keys_json == ["kr_no_evidence"]

        transition = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == child.id,
                DeliberationArtifact.kind == "cognitive_transition",
            )
        )
        assert transition is not None
        tampered = dict(transition.payload_json or {})
        tampered["claimed_knowledge_requests"] = []
        transition.payload_json = tampered
        transition.payload_digest = payload_digest(tampered)
        session.commit()

        result = replay_deliberation(session, child.id)
        session.commit()
        assert result.matched is False
        assert "cognitive_transition_digest" in result.result_json.get("mismatches", [])
        assert result.result_json["cognitive_transition_integrity"]["matched"] is False


def test_replay_skips_legacy_impact_method_without_false_mismatch(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """RED→GREEN #6: legacy impact method must not produce a false mismatch."""
    provider = StubProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_fixture(
            session, temp_settings, tmp_path, MINIMAL_FIXTURE, dirname="legacy-impact"
        )
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="legacy-impact-1",
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
        impact.method = "citation_scope_projection_v1"
        # Leave impact_json/digest as v2 bytes — naive recompute would diverge.
        session.commit()

        result = replay_deliberation(session, run.id)
        session.commit()
        integrity = result.result_json["cognitive_impact_integrity"]
        assert integrity["present"] is True
        assert integrity["matched"] is True
        assert integrity["recompute_skipped"] == "method_version_mismatch"
        assert integrity["stored_method"] == "citation_scope_projection_v1"
        assert "cognitive_impact_digest" not in result.result_json.get("mismatches", [])


def test_replay_detects_impact_stored_digest_when_method_downgraded(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """R4: tampering payload + flipping method to v1 must not hide behind skip."""
    provider = StubProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_fixture(
            session, temp_settings, tmp_path, MINIMAL_FIXTURE, dirname="impact-self-dig"
        )
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="impact-self-digest-1",
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
        original_digest = impact.impact_digest
        tampered = dict(impact.impact_json or {})
        tampered["decision_support"] = "lost"
        impact.impact_json = tampered
        impact.method = "citation_scope_projection_v1"
        # Leave impact_digest as the original v2 digest — self-digest must catch.
        assert impact.impact_digest == original_digest
        session.commit()

        result = replay_deliberation(session, run.id)
        session.commit()
        assert result.matched is False
        assert "cognitive_impact_stored_digest" in result.result_json.get("mismatches", [])
        integrity = result.result_json["cognitive_impact_integrity"]
        assert integrity["matched"] is False
        assert integrity.get("mismatch_reason") == "cognitive_impact_stored_digest"
        assert integrity.get("recompute_skipped") is None


def test_replay_legacy_continuation_empty_fulfilled_skips_transition_recompute(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """R3: legacy continuation ([], claimed present) must skip — not false matched recompute."""
    mission = _load_mission()
    with session_factory() as session:
        parent_install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="legacy-cont-parent",
            pack_id="legacy-cont-parent-pack",
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
            idempotency_key="legacy-cont-parent-1",
            provider=StubProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        child_install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="legacy-cont-child",
            pack_id="legacy-cont-child-pack",
        )
        child = deliberation.continue_deliberation(
            session,
            parent_run_id=parent.id,
            pack_ids=[child_install.pack_id],
            impact_baseline_pack_ids=[],
            fulfilled_knowledge_request_keys=["kr_no_evidence"],
            idempotency_key="legacy-cont-child-1",
            provider=StubProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        transition = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == child.id,
                DeliberationArtifact.kind == "cognitive_transition",
            )
        )
        assert transition is not None
        claimed = (transition.payload_json or {}).get("claimed_knowledge_requests") or []
        assert claimed, "fixture must have claimed keys to model legacy mismatch"

        # Simulate post-0008 legacy shape: empty fulfilled column, fingerprint NULL,
        # but stored transition still has claimed (not trusted for identity).
        child.fulfilled_request_keys_json = []
        child.request_fingerprint = None
        session.commit()

        result = replay_deliberation(session, child.id)
        session.commit()
        integrity = result.result_json["cognitive_transition_integrity"]
        assert integrity["present"] is True
        assert integrity.get("recompute_skipped") == "legacy_fulfilled_keys_unrecoverable"
        # Skip path must not pretend recompute matched against untrusted claimed.
        assert integrity.get("recomputed_digest") is None
        assert "cognitive_transition_digest" not in result.result_json.get("mismatches", [])


def test_golden_replay_select_and_no_evidence(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    frozen_clock: datetime,
) -> None:
    del frozen_clock
    mission = _load_mission()
    provider = StubProvider()

    with session_factory() as session:
        # --- select path (evidence present) ---
        select_install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="golden-select",
            pack_id="golden-select-pack",
        )
        select_run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[select_install.pack_id],
            impact_baseline_pack_ids=[select_install.pack_id],
            idempotency_key="golden-select-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert select_run.outcome == "select"
        select_replay = replay_deliberation(session, select_run.id)
        session.commit()
        assert select_replay.matched is True
        assert select_replay.result_json.get("mismatches") == []
        select_dossier = session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == select_run.id)
        )
        assert select_dossier is not None
        assert select_dossier.json_digest == GOLDEN_SELECT_DOSSIER_JSON_DIGEST, (
            f"select dossier digest drifted: {select_dossier.json_digest!r} "
            f"(update GOLDEN_SELECT_DOSSIER_JSON_DIGEST if intentional)"
        )

        # --- no-evidence abstain path ---
        empty_install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            EMPTY_EVIDENCE_FIXTURE,
            dirname="golden-empty",
            pack_id="golden-empty-pack",
        )
        empty_run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[empty_install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="golden-empty-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert empty_run.outcome == "abstain"
        assert empty_run.llm_attempt_count == 0
        empty_replay = replay_deliberation(session, empty_run.id)
        session.commit()
        assert empty_replay.matched is True
        assert empty_replay.result_json.get("mismatches") == []
        empty_dossier = session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == empty_run.id)
        )
        assert empty_dossier is not None
        assert empty_dossier.json_digest == GOLDEN_NO_EVIDENCE_DOSSIER_JSON_DIGEST, (
            f"no-evidence dossier digest drifted: {empty_dossier.json_digest!r} "
            f"(update GOLDEN_NO_EVIDENCE_DOSSIER_JSON_DIGEST if intentional)"
        )

        # Sanity: digests are real sha256 hex, not placeholders once green.
        assert len(select_dossier.json_digest) == 64
        assert len(empty_dossier.json_digest) == 64
        assert select_dossier.json_digest == payload_digest(select_dossier.dossier_json)
