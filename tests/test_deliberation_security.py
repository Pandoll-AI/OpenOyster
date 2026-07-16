"""Security tests: LLM/provider payloads must not leak into public surfaces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openoyster.api.app import _sanitize_deliberation_value
from openoyster.config import Settings
from openoyster.deliberation_contracts import Mission
from openoyster.llm import LLMProvider
from openoyster.models import DeliberationDossier, PackInstall
from openoyster.schemas import TextAnalysis
from openoyster.services import deliberation, opencrab_packs

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
MISSION_PATH = PROJECT_ROOT / "tests/fixtures/deliberation_d1/mission_happy.json"

SENTINEL = "SENTINEL-X"


def _load_mission() -> Mission:
    return Mission.model_validate(json.loads(MISSION_PATH.read_text(encoding="utf-8")))


def _install_minimal(
    session: Session,
    settings: Settings,
    tmp_path: Path,
) -> PackInstall:
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
    install = session.get(PackInstall, result.pack_install_id)
    assert install is not None
    return install


class SentinelLeakProvider(LLMProvider):
    """Returns a contract-violating payload that embeds a sentinel in a forbidden field."""

    name = "sentinel-leak"

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        del prompt, stage
        return {"beliefs": [], "raw_pack_body": SENTINEL}


def test_validation_error_does_not_leak_llm_input_into_dossier_or_api(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """Pydantic ValidationError must not put input_value into stage_calls.error / dossier / sanitize."""
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="security-sentinel-1",
            provider=SentinelLeakProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert run.status == "completed"
        assert run.outcome == "abstain"

        dossier = session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == run.id)
        )
        assert dossier is not None
        dossier_text = json.dumps(dossier.dossier_json, ensure_ascii=False)
        assert SENTINEL not in dossier_text

        sanitized = _sanitize_deliberation_value(dossier.dossier_json)
        sanitized_text = json.dumps(sanitized, ensure_ascii=False)
        assert SENTINEL not in sanitized_text

        # Stage call errors themselves must be safe (before sanitize redaction).
        stage_calls = (dossier.dossier_json or {}).get("stage_calls") or []
        for call in stage_calls:
            error = call.get("error") or ""
            assert SENTINEL not in error
            assert "input_value" not in error


SECRET_FRAG = "SECRET-FRAG-9z"


class SecretLeakProvider(LLMProvider):
    """Raises a provider exception whose message embeds a secret fragment."""

    name = "secret-leak"

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        del prompt, stage
        raise RuntimeError(f"auth failed token={SECRET_FRAG} body=leaked")


def test_provider_exception_message_not_leaked_into_dossier_or_api(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """Provider str(exc) must not appear on stage_calls.error / dossier / sanitize."""
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="security-secret-frag-1",
            provider=SecretLeakProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert run.status == "failed_execution"
        assert run.failure_code == "provider_error"
        # failure_detail is also a public-ish surface — must be safe.
        assert SECRET_FRAG not in (run.failure_detail or "")

        dossier = session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == run.id)
        )
        # failed_execution may still produce a partial dossier; if present, check it.
        if dossier is not None:
            dossier_text = json.dumps(dossier.dossier_json, ensure_ascii=False)
            assert SECRET_FRAG not in dossier_text
            sanitized = _sanitize_deliberation_value(dossier.dossier_json)
            sanitized_text = json.dumps(sanitized, ensure_ascii=False)
            assert SECRET_FRAG not in sanitized_text
            stage_calls = (dossier.dossier_json or {}).get("stage_calls") or []
            for call in stage_calls:
                error = call.get("error") or ""
                assert SECRET_FRAG not in error
                if error:
                    assert "provider_error" in error

        from openoyster.models import DeliberationStageCall

        calls = session.scalars(
            select(DeliberationStageCall).where(DeliberationStageCall.run_id == run.id)
        ).all()
        assert calls
        for call in calls:
            assert SECRET_FRAG not in (call.error or "")
            if call.error:
                assert "provider_error" in call.error
                assert "RuntimeError" in call.error


LEAK_SENTINEL = "LEAK-SENTINEL"


class GateLeakProvider(LLMProvider):
    """Beliefs OK; options references a non-existent belief key embedding LEAK-SENTINEL."""

    name = "gate-leak"

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        from openoyster.services.llm_judges import stub_query_json

        if stage == "deliberation_options":
            payload = stub_query_json(prompt, stage)
            # Force unknown_belief_ref: model-authored key must not leak to surfaces.
            for opt in payload.get("options") or []:
                opt["supporting_belief_keys"] = [LEAK_SENTINEL]
            return payload
        return stub_query_json(prompt, stage)


def test_gate_error_does_not_leak_model_identifiers_into_public_surfaces(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """R5: gate free-text (belief/option keys) must not reach dossier/sanitize/retry."""
    from openoyster.models import DeliberationStageCall

    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="security-gate-leak-1",
            provider=GateLeakProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert run.status == "completed"
        assert run.outcome == "abstain"

        calls = session.scalars(
            select(DeliberationStageCall).where(DeliberationStageCall.run_id == run.id)
        ).all()
        assert calls
        for call in calls:
            assert LEAK_SENTINEL not in (call.error or "")
            if call.error and call.stage == "deliberation_options":
                assert call.error == "gate_rejected: unknown_belief_ref"
            # Retry attempt prompt_digest is opaque; error surface must stay clean.
            usage = call.usage_json or {}
            assert LEAK_SENTINEL not in json.dumps(usage, ensure_ascii=False)

        dossier = session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == run.id)
        )
        assert dossier is not None
        dossier_text = json.dumps(dossier.dossier_json, ensure_ascii=False)
        assert LEAK_SENTINEL not in dossier_text

        sanitized = _sanitize_deliberation_value(dossier.dossier_json)
        sanitized_text = json.dumps(sanitized, ensure_ascii=False)
        assert LEAK_SENTINEL not in sanitized_text


class PrimaryStageProfileBoomProvider(LLMProvider):
    """stage_profile raises (e.g. missing Codex config) before any query_json."""

    name = "profile-boom"

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("unused")

    def stage_profile(self, stage: str) -> dict[str, Any]:
        del stage
        raise RuntimeError("primary Codex config missing for stage")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        del prompt, stage
        raise AssertionError("query_json must not run after stage_profile boom")


def test_primary_stage_profile_exception_ends_failed_execution(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """R1: stage_profile failure must not leave the run unfinished/crashing."""
    mission = _load_mission()
    with session_factory() as session:
        install = _install_minimal(session, temp_settings, tmp_path)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="security-profile-boom-1",
            provider=PrimaryStageProfileBoomProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.status == "failed_execution"
        assert run.failure_code == "provider_error"
        assert run.completed_at is not None
        assert "Codex config missing" not in (run.failure_detail or "")
