"""Optional secondary critic (critic2) tests for Autonomous Deliberation D1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openoyster.config import Settings
from openoyster.deliberation_contracts import Mission
from openoyster.llm import LLMProvider, critic2_provider_from_settings
from openoyster.models import DeliberationArtifact, DeliberationStageCall
from openoyster.schemas import TextAnalysis
from openoyster.services import deliberation, opencrab_packs
from openoyster.services.llm_judges import stub_query_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
MISSION_PATH = PROJECT_ROOT / "tests/fixtures/deliberation_d1/mission_happy.json"


def _load_mission() -> Mission:
    return Mission.model_validate(json.loads(MISSION_PATH.read_text(encoding="utf-8")))


def _install_minimal(
    session: Session, settings: Settings, tmp_path: Path
) -> Any:
    import shutil

    pack_dir = tmp_path / "pack-a"
    shutil.copytree(MINIMAL_FIXTURE, pack_dir)
    result = opencrab_packs.install_pack(
        session,
        pack_dir,
        workspace=settings.workspace,
        profile="compatible",
    )
    session.commit()
    from openoyster.models import PackInstall

    return session.get(PackInstall, result.pack_install_id)


class PrimaryPassProvider(LLMProvider):
    """Primary path uses real stub; records all stage names."""

    name = "primary-pass"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.calls.append(stage)
        return stub_query_json(prompt, stage)


class SecondaryReviseProvider(LLMProvider):
    """Secondary critic always returns revise (stricter than primary pass).

    After provider_stage fix, the secondary provider is queried with
    ``deliberation_critic`` (primary critic contract); only stage_call.stage is
    recorded as deliberation_critic_secondary.
    """

    name = "stub"

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("unused")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        # Secondary provider is only used for the critic pass.
        if stage in {"deliberation_critic", "deliberation_critic_secondary"}:
            return {
                "verdict": "revise",
                "issues": [
                    {
                        "code": "missing_opposing_evidence",
                        "artifact_ref": "beliefs:b1",
                        "detail": "secondary critic forces revise",
                    }
                ],
                "findings": [
                    {
                        "text": "Secondary critic found a coverage gap",
                        "classification": "structural",
                        "issue_code": "missing_opposing_evidence",
                        "artifact_ref": "beliefs:b1",
                    }
                ],
            }
        return stub_query_json(prompt, stage)


def test_settings_critic2_provider_defaults_to_none(temp_settings: Settings) -> None:
    assert temp_settings.critic2_provider == "none"
    assert critic2_provider_from_settings(temp_settings) is None


def test_critic2_none_preserves_five_call_path(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    assert temp_settings.critic2_provider == "none"
    provider = PrimaryPassProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="critic2-off-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert provider.calls == [
            "deliberation_beliefs",
            "deliberation_options",
            "deliberation_scenarios",
            "deliberation_critic",
            "deliberation_decision",
        ]
        assert run.llm_attempt_count == 5
        kinds = {
            row.kind
            for row in session.scalars(
                select(DeliberationArtifact).where(DeliberationArtifact.run_id == run.id)
            ).all()
        }
        assert "critic_result_secondary" not in kinds
        assert "critic_effective" not in kinds


def test_secondary_critic_revise_blocks_select(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Primary stub passes; secondary revise forces abstain via conservative combine."""
    temp_settings.critic2_provider = "stub"
    monkeypatch.setattr(
        deliberation,
        "critic2_provider_from_settings",
        lambda _settings: SecondaryReviseProvider(),
    )
    provider = PrimaryPassProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="critic2-revise-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert run.outcome == "abstain"
        assert run.status == "completed"

        primary = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "critic_result",
            )
        )
        secondary = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "critic_result_secondary",
            )
        )
        effective = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "critic_effective",
            )
        )
        assert primary is not None
        assert primary.payload_json.get("verdict") == "pass"
        assert secondary is not None
        assert secondary.payload_json.get("verdict") == "revise"
        assert effective is not None
        assert effective.payload_json == {
            "primary_verdict": "pass",
            "secondary_verdict": "revise",
            "effective_verdict": "revise",
            "combination": "conservative_v1",
        }

        decision = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "decision",
            )
        )
        assert decision is not None
        assert decision.payload_json.get("outcome") == "abstain"
        assert "critic_non_pass" in (decision.payload_json.get("abstention_reasons") or [])

        sec_calls = session.scalars(
            select(DeliberationStageCall).where(
                DeliberationStageCall.run_id == run.id,
                DeliberationStageCall.stage == "deliberation_critic_secondary",
            )
        ).all()
        assert sec_calls
        assert all(c.status == "succeeded" for c in sec_calls)

        from openoyster.models import DeliberationDossier

        dossier = session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == run.id)
        )
        assert dossier is not None
        assert dossier.dossier_json.get("critic_result_secondary") is not None
        assert dossier.dossier_json.get("critic_effective") is not None


def test_secondary_provider_error_keeps_primary_verdict(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class BoomSecondary(LLMProvider):
        name = "stub"

        def analyse_batch(
            self, texts: list[str], policy: dict[str, Any] | None = None
        ) -> list[TextAnalysis]:
            del texts, policy
            raise AssertionError("unused")

        def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
            # Secondary provider is only used for critic; always fail.
            del prompt, stage
            raise RuntimeError("secondary provider down")

    temp_settings.critic2_provider = "stub"
    monkeypatch.setattr(
        deliberation,
        "critic2_provider_from_settings",
        lambda _settings: BoomSecondary(),
    )
    provider = PrimaryPassProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="critic2-boom-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.status == "completed"
        assert run.outcome in {"select", "abstain"}
        # Primary pass → effective pass → select path may succeed.
        effective = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "critic_effective",
            )
        )
        assert effective is not None
        payload = effective.payload_json
        assert payload["primary_verdict"] == "pass"
        assert payload["effective_verdict"] == "pass"
        assert payload["combination"] == "conservative_v1"
        assert "secondary_error" in payload
        # Public surface must not embed raw provider exception text.
        assert "secondary provider down" not in str(payload["secondary_error"])
        assert "provider_error" in str(payload["secondary_error"])
        assert session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "critic_result_secondary",
            )
        ) is None


class _RecordingProfileProvider(LLMProvider):
    """Records stage names passed to stage_profile and query_json."""

    name = "recording-profile"

    def __init__(self) -> None:
        self.profile_stages: list[str] = []
        self.query_stages: list[str] = []

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("unused")

    def stage_profile(self, stage: str) -> dict[str, Any]:
        self.profile_stages.append(stage)
        return {"provider": self.name, "model": "rec", "effort": None}

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.query_stages.append(stage)
        return stub_query_json(prompt, stage)


def test_run_stage_provider_stage_routes_config_not_recorded_stage(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """provider_stage drives stage_profile/query_json; recorded_stage is DB-only."""
    from openoyster.deliberation_contracts import STAGE_CRITIC
    from openoyster.models import DeliberationEvidenceSnapshot
    from openoyster.services.deliberation_gates import EvidenceSnapshotView, GateContext

    provider = _RecordingProfileProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="provider-stage-route-setup",
            provider=PrimaryPassProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        snapshots = list(
            session.scalars(
                select(DeliberationEvidenceSnapshot).where(
                    DeliberationEvidenceSnapshot.run_id == run.id
                )
            ).all()
        )
        assert snapshots
        views: dict[str, EvidenceSnapshotView] = {}
        for s in snapshots:
            payload = s.prompt_visible_payload_json or {}
            text = ""
            if isinstance(payload.get("text"), str):
                text = payload["text"]
            views[s.snapshot_key] = EvidenceSnapshotView(
                snapshot_key=s.snapshot_key,
                db_id=s.id,
                global_evidence_id=s.global_evidence_id,
                text=text,
                payload=payload,
                pack_install_id=s.pack_install_id,
                record_hash=s.record_hash,
            )
        ctx = GateContext(mission=mission, snapshots_by_key=views)
        model, call, err = deliberation._run_stage(
            session,
            run=run,
            mission=mission,
            stage=STAGE_CRITIC,
            provider=provider,
            settings=temp_settings,
            snapshots=snapshots,
            prior_artifacts={},
            ctx=ctx,
            provider_stage=STAGE_CRITIC,
            recorded_stage=deliberation.STAGE_CRITIC_SECONDARY,
        )
        assert err is None
        assert model is not None
        assert call is not None
        assert call.stage == deliberation.STAGE_CRITIC_SECONDARY
        assert provider.profile_stages == [STAGE_CRITIC]
        assert provider.query_stages == [STAGE_CRITIC]


def test_secondary_unexpected_exception_keeps_primary_and_records_secondary_error(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Any secondary exception must complete the run with primary verdict + secondary_error."""

    class ExplodingSecondary(LLMProvider):
        name = "stub"

        def analyse_batch(
            self, texts: list[str], policy: dict[str, Any] | None = None
        ) -> list[TextAnalysis]:
            del texts, policy
            raise AssertionError("unused")

        def stage_profile(self, stage: str) -> dict[str, Any]:
            del stage
            raise RuntimeError("codex config missing for secondary")

        def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
            del prompt, stage
            raise AssertionError("query should not run after stage_profile boom")

    temp_settings.critic2_provider = "stub"
    monkeypatch.setattr(
        deliberation,
        "critic2_provider_from_settings",
        lambda _settings: ExplodingSecondary(),
    )
    provider = PrimaryPassProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="critic2-explode-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.status == "completed"
        assert run.outcome in {"select", "abstain"}
        effective = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "critic_effective",
            )
        )
        assert effective is not None
        payload = effective.payload_json
        assert payload["primary_verdict"] == "pass"
        assert payload["effective_verdict"] == "pass"
        assert "secondary_error" in payload
        assert "codex config missing" not in str(payload["secondary_error"])
        assert "provider_error" in str(payload["secondary_error"])


def test_secondary_factory_exception_keeps_primary_and_records_secondary_error(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """R1: critic2 factory failure must not kill the run (primary + secondary_error)."""
    temp_settings.critic2_provider = "stub"

    def _boom_factory(_settings: Settings) -> LLMProvider:
        raise RuntimeError("critic2 factory: codex binary not found")

    monkeypatch.setattr(deliberation, "critic2_provider_from_settings", _boom_factory)
    provider = PrimaryPassProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="critic2-factory-boom-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.status == "completed"
        assert run.outcome in {"select", "abstain"}
        effective = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "critic_effective",
            )
        )
        assert effective is not None
        payload = effective.payload_json
        assert payload["primary_verdict"] == "pass"
        assert payload["effective_verdict"] == "pass"
        assert "secondary_error" in payload
        assert "codex binary not found" not in str(payload["secondary_error"])
        assert "provider_error" in str(payload["secondary_error"])


class SecondaryReviseWithGapProvider(LLMProvider):
    """Secondary revise carrying a gap finding with unresolved_question."""

    name = "stub"

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("unused")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        if stage in {"deliberation_critic", "deliberation_critic_secondary"}:
            return {
                "verdict": "revise",
                "issues": [
                    {
                        "code": "missing_opposing_evidence",
                        "artifact_ref": "beliefs:b1",
                        "detail": "secondary gap",
                    }
                ],
                "findings": [
                    {
                        "text": "Secondary gap: opposing evidence missing",
                        "classification": "gap",
                        "issue_code": "missing_opposing_evidence",
                        "artifact_ref": "beliefs:b1",
                        "unresolved_question": "What opposing evidence exists for b1?",
                    }
                ],
            }
        return stub_query_json(prompt, stage)


def test_secondary_gap_assertions_and_knowledge_requests_promoted(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Secondary revise+gap must persist assertions and promote KR on abstain."""
    from openoyster.models import DeliberationAssertion

    temp_settings.critic2_provider = "stub"
    monkeypatch.setattr(
        deliberation,
        "critic2_provider_from_settings",
        lambda _settings: SecondaryReviseWithGapProvider(),
    )
    provider = PrimaryPassProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="critic2-gap-kr-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert run.outcome == "abstain"
        assert run.status == "completed"

        secondary = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "critic_result_secondary",
            )
        )
        assert secondary is not None
        assertions = session.scalars(
            select(DeliberationAssertion).where(
                DeliberationAssertion.artifact_id == secondary.id
            )
        ).all()
        assert len(assertions) > 0

        kr = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "knowledge_requests",
            )
        )
        assert kr is not None
        items = (kr.payload_json or {}).get("knowledge_requests") or []
        questions = [item.get("question") for item in items if isinstance(item, dict)]
        keys = [item.get("local_key") for item in items if isinstance(item, dict)]
        assert "What opposing evidence exists for b1?" in questions
        assert any(isinstance(k, str) and k.startswith("kr_critic2_") for k in keys)
