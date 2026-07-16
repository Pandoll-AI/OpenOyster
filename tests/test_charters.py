"""First-class deliberation charters — create, validate, archive, API isolation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openoyster.api.app import create_app
from openoyster.config import Settings
from openoyster.deliberation_contracts import Mission
from openoyster.llm import LLMProvider
from openoyster.models import DeliberationCharter, DeliberationRun, DeliberationStageCall
from openoyster.services import charters, deliberation, opencrab_packs, outcome_ledger
from openoyster.services.llm_judges import stub_query_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
MISSION_PATH = PROJECT_ROOT / "tests/fixtures/deliberation_d1/mission_happy.json"


def _load_mission(**overrides: Any) -> Mission:
    payload = json.loads(MISSION_PATH.read_text(encoding="utf-8"))
    payload.update(overrides)
    return Mission.model_validate(payload)


class RecordingStubProvider(LLMProvider):
    """Stub LLM that records every stage prompt for epistemic assertions."""

    name = "recording-stub"

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[Any]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.prompts.append((stage, prompt))
        return stub_query_json(prompt, stage)


def _install_minimal(
    session: Session, settings: Settings, tmp_path: Path, *, pack_id: str
) -> str:
    dest = tmp_path / pack_id
    shutil.copytree(MINIMAL_FIXTURE, dest)
    manifest = dest / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["pack_id"] = pack_id
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result = opencrab_packs.install_pack(
        session,
        dest,
        workspace=settings.workspace,
        profile="compatible",
    )
    session.commit()
    return result.pack_id


def test_create_charter_then_run_succeeds(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        charter = charters.create_charter(session, title="Domain A")
        session.commit()
        pack_id = _install_minimal(session, temp_settings, tmp_path, pack_id="pack.charter-ok")
        run = deliberation.run_deliberation(
            session,
            _load_mission(mission_charter_id=charter.id),
            pack_ids=[pack_id],
            impact_baseline_pack_ids=[pack_id],
            idempotency_key="charter-ok-run",
            provider=RecordingStubProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.status == "completed"
        assert run.mission_snapshot_json.get("mission_charter_id") == charter.id


def test_unknown_charter_rejects_run(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        pack_id = _install_minimal(session, temp_settings, tmp_path, pack_id="pack.charter-unknown")
        with pytest.raises(charters.CharterError) as exc_info:
            deliberation.run_deliberation(
                session,
                _load_mission(mission_charter_id=999_999),
                pack_ids=[pack_id],
                impact_baseline_pack_ids=[pack_id],
                idempotency_key="charter-unknown-run",
                provider=RecordingStubProvider(),
                settings=temp_settings,
                allow_compatible_packs=True,
            )
        assert exc_info.value.code == charters.ERROR_UNKNOWN_CHARTER
        assert session.scalar(select(DeliberationRun)) is None


def test_archived_charter_rejects_run(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        charter = charters.create_charter(session, title="Archived domain")
        charters.archive_charter(session, charter.id)
        session.commit()
        pack_id = _install_minimal(session, temp_settings, tmp_path, pack_id="pack.charter-arch")
        with pytest.raises(charters.CharterError) as exc_info:
            deliberation.run_deliberation(
                session,
                _load_mission(mission_charter_id=charter.id),
                pack_ids=[pack_id],
                impact_baseline_pack_ids=[pack_id],
                idempotency_key="charter-arch-run",
                provider=RecordingStubProvider(),
                settings=temp_settings,
                allow_compatible_packs=True,
            )
        assert exc_info.value.code == charters.ERROR_CHARTER_ARCHIVED
        assert session.scalar(select(DeliberationRun)) is None


def test_archive_transition(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        charter = charters.create_charter(
            session, title="To archive", description="desc"
        )
        session.commit()
        assert charter.status == "active"
        listed_active = charters.list_charters(session, status="active")
        assert any(c.id == charter.id for c in listed_active)

        archived = charters.archive_charter(session, charter.id)
        session.commit()
        assert archived.status == "archived"
        # Idempotent archive of already-archived
        again = charters.archive_charter(session, charter.id)
        session.commit()
        assert again.status == "archived"

        listed_archived = charters.list_charters(session, status="archived")
        assert any(c.id == charter.id for c in listed_archived)
        listed_active_after = charters.list_charters(session, status="active")
        assert all(c.id != charter.id for c in listed_active_after)

        shown = charters.get_charter(session, charter.id)
        assert shown is not None
        assert shown.status == "archived"


def test_api_auth_and_sanitize(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
) -> None:
    app = create_app(settings=temp_settings, session_factory=session_factory)
    with TestClient(app) as client:
        denied = client.post(
            "/v1/charters",
            json={"title": "No auth"},
        )
        assert denied.status_code == 401

        headers = {temp_settings.api_key_header: temp_settings.api_key or ""}
        created = client.post(
            "/v1/charters",
            json={
                "title": "API charter",
                "description": "see file:///Users/secret/charter.txt",
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["charter"]["title"] == "API charter"
        assert body["charter"]["status"] == "active"
        assert body["charter"]["description"] == "[redacted]"
        charter_id = body["charter"]["id"]

        listed = client.get("/v1/charters", headers=headers)
        assert listed.status_code == 200
        assert any(c["id"] == charter_id for c in listed.json()["charters"])

        shown = client.get(f"/v1/charters/{charter_id}", headers=headers)
        assert shown.status_code == 200
        assert shown.json()["charter"]["id"] == charter_id

        archived = client.post(f"/v1/charters/{charter_id}/archive", headers=headers)
        assert archived.status_code == 200
        assert archived.json()["charter"]["status"] == "archived"

        missing = client.get("/v1/charters/999999", headers=headers)
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "unknown_charter"


def test_charter_text_never_enters_stage_prompts(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """Charter is Mission control grouping only — title/description never in prompts."""
    secret_marker = "CHARTER_SECRET_MARKER_NEVER_IN_PROMPT_XYZ"
    with session_factory() as session:
        charter = charters.create_charter(
            session,
            title=f"Title with {secret_marker}",
            description=f"Description embeds {secret_marker} fully",
        )
        session.commit()
        pack_id = _install_minimal(
            session, temp_settings, tmp_path, pack_id="pack.charter-epistemic"
        )

        provider_a = RecordingStubProvider()
        run_plain = deliberation.run_deliberation(
            session,
            _load_mission(),  # no charter_id
            pack_ids=[pack_id],
            impact_baseline_pack_ids=[pack_id],
            idempotency_key="charter-plain-run",
            provider=provider_a,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run_plain.status == "completed"
        digests_plain = {
            call.stage: call.prompt_digest
            for call in session.scalars(
                select(DeliberationStageCall).where(
                    DeliberationStageCall.run_id == run_plain.id
                )
            ).all()
            if call.prompt_digest
        }
        assert digests_plain

        provider_b = RecordingStubProvider()
        run_chartered = deliberation.run_deliberation(
            session,
            _load_mission(mission_charter_id=charter.id),
            pack_ids=[pack_id],
            impact_baseline_pack_ids=[pack_id],
            idempotency_key="charter-with-id-run",
            provider=provider_b,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run_chartered.status == "completed"
        # mission_charter_id is in mission snapshot (digest may differ), but
        # title/description text must never appear in any stage prompt.
        for stage, prompt in provider_b.prompts:
            assert secret_marker not in prompt, f"charter text leaked into {stage}"
            assert "CHARTER_SECRET" not in prompt
            assert charter.title not in prompt
            assert (charter.description or "") not in prompt or not charter.description

        for _stage, prompt in provider_a.prompts:
            assert secret_marker not in prompt

        # Stored stage digests exist; free-text secret is not in persisted stage rows.
        for call in session.scalars(
            select(DeliberationStageCall).where(
                DeliberationStageCall.run_id == run_chartered.id
            )
        ).all():
            blob = json.dumps(
                {
                    "prompt_digest": call.prompt_digest,
                    "response": call.response_json,
                    "error": call.error,
                },
                ensure_ascii=False,
                default=str,
            )
            assert secret_marker not in blob


def test_calibration_unknown_charter_stable_error(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        with pytest.raises(charters.CharterError) as exc_info:
            outcome_ledger.calibration_report(session, mission_charter_id=424242)
        assert exc_info.value.code == charters.ERROR_UNKNOWN_CHARTER


def test_none_charter_id_unconstrained(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """mission_charter_id=None remains unconstrained (no charter table lookup)."""
    with session_factory() as session:
        pack_id = _install_minimal(session, temp_settings, tmp_path, pack_id="pack.charter-none")
        run = deliberation.run_deliberation(
            session,
            _load_mission(),
            pack_ids=[pack_id],
            impact_baseline_pack_ids=[pack_id],
            idempotency_key="charter-none-run",
            provider=RecordingStubProvider(),
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.status == "completed"
        assert run.mission_snapshot_json.get("mission_charter_id") is None
        assert session.scalar(select(DeliberationCharter)) is None
