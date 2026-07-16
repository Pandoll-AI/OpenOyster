"""Integration coverage for the Autonomous Deliberation D1 public surfaces."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from openoyster.api.app import create_app
from openoyster.cli import app
from openoyster.config import Settings, clear_settings_cache, get_settings
from openoyster.database import make_engine, make_session_factory
from openoyster.models import PackEvidence, PackInstall
from openoyster.services import opencrab_packs

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
MISSION_PATH = PROJECT_ROOT / "tests/fixtures/deliberation_d1/mission_happy.json"


def _mission_payload() -> dict[str, object]:
    payload = json.loads(MISSION_PATH.read_text(encoding="utf-8"))
    payload["context"] = "Control input only; do not expose /private/openoyster/secret-path."
    return payload


def _set_cli_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENOYSTER_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("OPENOYSTER_DB_URL", f"sqlite:///{tmp_path / 'deliberation-cli-api.db'}")
    monkeypatch.setenv("OPENOYSTER_API_KEY", "test-secret")
    monkeypatch.setenv("OPENOYSTER_LLM_PROVIDER", "stub")
    clear_settings_cache()


def _install_fixture(session_factory, settings: Settings, tmp_path: Path) -> str:
    with session_factory() as session:
        result = opencrab_packs.install_pack(
            session,
            MINIMAL_FIXTURE,
            workspace=settings.workspace,
            profile="compatible",
        )
        session.commit()
    return result.pack_id


def _copy_fixture_with_id(tmp_path: Path, pack_id: str) -> Path:
    destination = tmp_path / pack_id
    shutil.copytree(MINIMAL_FIXTURE, destination)
    manifest = destination / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["pack_id"] = pack_id
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination


def _remove_pack_evidence(session_factory, pack_id: str) -> None:
    with session_factory() as session:
        install = session.scalar(select(PackInstall).where(PackInstall.pack_id == pack_id))
        assert install is not None
        for row in session.scalars(
            select(PackEvidence).where(PackEvidence.pack_install_id == install.id)
        ).all():
            session.delete(row)
        session.commit()


def test_deliberation_cli_run_and_read_commands(monkeypatch, tmp_path: Path) -> None:
    _set_cli_env(monkeypatch, tmp_path)
    runner = CliRunner()
    mission_file = tmp_path / "mission.json"
    mission_file.write_text(json.dumps(_mission_payload()), encoding="utf-8")
    try:
        installed = runner.invoke(app, ["pack", "install", str(MINIMAL_FIXTURE)])
        assert installed.exit_code == 0, installed.output

        invalid = runner.invoke(
            app, ["deliberate", "run", str(tmp_path / "missing.json"), "--packs", "p0-f1-minimal"]
        )
        assert invalid.exit_code == 2

        missing_idempotency_key = runner.invoke(
            app,
            [
                "deliberate",
                "run",
                str(mission_file),
                "--packs",
                "p0-f1-minimal",
                "--allow-compatible-packs",
            ],
        )
        assert missing_idempotency_key.exit_code == 2

        compatible_without_opt_in = runner.invoke(
            app,
            [
                "deliberate",
                "run",
                str(mission_file),
                "--packs",
                "p0-f1-minimal",
                "--idempotency-key",
                "cli-compatible-rejected-1",
            ],
        )
        assert compatible_without_opt_in.exit_code == 2
        assert "compatible_pack_not_allowed" in compatible_without_opt_in.output

        created = runner.invoke(
            app,
            [
                "deliberate",
                "run",
                str(mission_file),
                "--packs",
                "p0-f1-minimal",
                "--impact-baseline-packs",
                "p0-f1-minimal",
                "--allow-compatible-packs",
                "--idempotency-key",
                "cli-deliberation-1",
            ],
        )
        assert created.exit_code == 0, created.output
        created_payload = json.loads(created.output)
        run_id = created_payload["id"]
        assert created_payload["status"] == "completed"
        assert "/private/openoyster/secret-path" not in created.output

        for command in (
            ["deliberate", "show", str(run_id)],
            ["deliberate", "dossier", str(run_id), "--format", "json"],
            ["deliberate", "dossier", str(run_id), "--format", "markdown"],
            ["deliberate", "impact", str(run_id)],
            ["deliberate", "knowledge-requests", str(run_id)],
            ["deliberate", "replay", str(run_id)],
        ):
            result = runner.invoke(app, command)
            assert result.exit_code == 0, result.output
            assert "/private/openoyster/secret-path" not in result.output
    finally:
        clear_settings_cache()


def test_deliberation_api_requires_key_idempotency_and_sanitizes(
    temp_settings: Settings, session_factory, tmp_path: Path
) -> None:
    pack_id = _install_fixture(session_factory, temp_settings, tmp_path)
    application = create_app(settings=temp_settings, session_factory=session_factory)
    headers = {
        temp_settings.api_key_header: str(temp_settings.api_key),
        "Idempotency-Key": "api-deliberation-1",
    }
    request_payload = {
        "mission": _mission_payload(),
        "packs": [pack_id],
        "impact_baseline_packs": [pack_id],
        "allow_compatible_packs": True,
    }
    unsafe_settings = temp_settings.model_copy(update={"api_key": None, "api_allow_unsafe_no_key": True})
    with TestClient(create_app(settings=unsafe_settings, session_factory=session_factory)) as client:
        unconfigured = client.post(
            "/v1/deliberations",
            json=request_payload,
            headers={"Idempotency-Key": "api-deliberation-no-key"},
        )
        assert unconfigured.status_code == 503

    with TestClient(application) as client:
        unauthorized = client.post("/v1/deliberations", json=request_payload)
        assert unauthorized.status_code == 401

        missing_key = client.post(
            "/v1/deliberations",
            json=request_payload,
            headers={temp_settings.api_key_header: str(temp_settings.api_key)},
        )
        assert missing_key.status_code == 422

        created = client.post("/v1/deliberations", json=request_payload, headers=headers)
        assert created.status_code == 200, created.text
        run_id = created.json()["id"]
        assert created.json()["status"] == "completed"

        repeated = client.post("/v1/deliberations", json=request_payload, headers=headers)
        assert repeated.status_code == 200
        assert repeated.json()["id"] == run_id

        protected = (
            ("get", f"/v1/deliberations/{run_id}"),
            ("get", f"/v1/deliberations/{run_id}/dossier"),
            ("post", f"/v1/deliberations/{run_id}/replay"),
            ("get", f"/v1/deliberations/{run_id}/cognitive-impact"),
            ("get", f"/v1/deliberations/{run_id}/knowledge-requests"),
        )
        for method, url in protected:
            assert getattr(client, method)(url).status_code == 401

        for method, url in protected:
            response = getattr(client, method)(
                url, headers={temp_settings.api_key_header: str(temp_settings.api_key)}
            )
            assert response.status_code == 200, response.text
            assert "/private/openoyster/secret-path" not in response.text
            assert "raw_record_json" not in response.text
            assert "prompt_visible_payload_json" not in response.text


def test_cli_continues_an_abstention_and_exposes_transition(monkeypatch, tmp_path: Path) -> None:
    _set_cli_env(monkeypatch, tmp_path)
    runner = CliRunner()
    mission_file = tmp_path / "continuity-mission.json"
    mission_file.write_text(json.dumps(_mission_payload()), encoding="utf-8")
    parent_pack = _copy_fixture_with_id(tmp_path, "cli-continuity-parent")
    child_pack = _copy_fixture_with_id(tmp_path, "cli-continuity-child")
    try:
        for pack_path in (parent_pack, child_pack):
            installed = runner.invoke(app, ["pack", "install", str(pack_path)])
            assert installed.exit_code == 0, installed.output

        settings = get_settings()
        engine = make_engine(settings)
        try:
            _remove_pack_evidence(make_session_factory(engine), "cli-continuity-parent")
        finally:
            engine.dispose()

        parent_result = runner.invoke(
            app,
            [
                "deliberate",
                "run",
                str(mission_file),
                "--packs",
                "cli-continuity-parent",
                "--allow-compatible-packs",
                "--idempotency-key",
                "cli-continuity-parent-run",
            ],
        )
        assert parent_result.exit_code == 0, parent_result.output
        parent = json.loads(parent_result.output)
        assert parent["outcome"] == "abstain"

        parent_transition = runner.invoke(app, ["deliberate", "transition", str(parent["id"])])
        assert parent_transition.exit_code == 1, parent_transition.output
        assert json.loads(parent_transition.output)["error"]["code"] == (
            "cognitive_transition_not_found"
        )

        continued = runner.invoke(
            app,
            [
                "deliberate",
                "continue",
                str(parent["id"]),
                "--packs",
                "cli-continuity-child",
                "--fulfills",
                "kr_no_evidence",
                "--allow-compatible-packs",
                "--idempotency-key",
                "cli-continuity-child-run",
            ],
        )
        assert continued.exit_code == 0, continued.output
        child = json.loads(continued.output)
        assert child["parent_run_id"] == parent["id"]

        transition = runner.invoke(app, ["deliberate", "transition", str(child["id"])])
        assert transition.exit_code == 0, transition.output
        transition_payload = json.loads(transition.output)
        assert transition_payload["method"] == "cognitive_transition_v2"
        assert transition_payload["fulfilled_knowledge_requests"][0]["local_key"] == (
            "kr_no_evidence"
        )
    finally:
        clear_settings_cache()


def test_api_continues_an_abstention_and_exposes_transition(
    temp_settings: Settings, session_factory, tmp_path: Path
) -> None:
    parent_path = _copy_fixture_with_id(tmp_path, "api-continuity-parent")
    child_path = _copy_fixture_with_id(tmp_path, "api-continuity-child")
    with session_factory() as session:
        for pack_path in (parent_path, child_path):
            opencrab_packs.install_pack(
                session,
                pack_path,
                workspace=temp_settings.workspace,
                profile="compatible",
            )
        session.commit()
    _remove_pack_evidence(session_factory, "api-continuity-parent")

    application = create_app(settings=temp_settings, session_factory=session_factory)
    auth = {temp_settings.api_key_header: str(temp_settings.api_key)}
    with TestClient(application) as client:
        parent_response = client.post(
            "/v1/deliberations",
            json={
                "mission": _mission_payload(),
                "packs": ["api-continuity-parent"],
                "allow_compatible_packs": True,
            },
            headers={**auth, "Idempotency-Key": "api-continuity-parent-run"},
        )
        assert parent_response.status_code == 200, parent_response.text
        parent = parent_response.json()
        assert parent["outcome"] == "abstain"

        parent_transition = client.get(
            f"/v1/deliberations/{parent['id']}/transition",
            headers=auth,
        )
        assert parent_transition.status_code == 409, parent_transition.text
        assert parent_transition.json()["detail"]["code"] == "cognitive_transition_not_ready"

        request_payload = {
            "packs": ["api-continuity-child"],
            "fulfilled_knowledge_request_keys": ["kr_no_evidence"],
            "allow_compatible_packs": True,
        }
        unauthorized = client.post(
            f"/v1/deliberations/{parent['id']}/continue",
            json=request_payload,
            headers={"Idempotency-Key": "api-continuity-unauthorized"},
        )
        assert unauthorized.status_code == 401

        unknown_fulfilled_key = client.post(
            f"/v1/deliberations/{parent['id']}/continue",
            json={
                **request_payload,
                "fulfilled_knowledge_request_keys": ["kr_unknown"],
            },
            headers={**auth, "Idempotency-Key": "api-continuity-unknown-key"},
        )
        assert unknown_fulfilled_key.status_code == 422, unknown_fulfilled_key.text
        assert unknown_fulfilled_key.json()["detail"]["code"] == (
            "fulfilled_knowledge_request_keys_unknown"
        )

        continued = client.post(
            f"/v1/deliberations/{parent['id']}/continue",
            json=request_payload,
            headers={**auth, "Idempotency-Key": "api-continuity-child-run"},
        )
        assert continued.status_code == 200, continued.text
        child = continued.json()
        assert child["parent_run_id"] == parent["id"]

        repeated = client.post(
            f"/v1/deliberations/{parent['id']}/continue",
            json=request_payload,
            headers={**auth, "Idempotency-Key": "api-continuity-child-run"},
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["id"] == child["id"]

        transition = client.get(
            f"/v1/deliberations/{child['id']}/transition",
            headers=auth,
        )
        assert transition.status_code == 200, transition.text
        assert transition.json()["method"] == "cognitive_transition_v2"


def test_cli_knowledge_requests_export_schema(monkeypatch, tmp_path: Path) -> None:
    _set_cli_env(monkeypatch, tmp_path)
    runner = CliRunner()
    mission = _mission_payload()
    mission_file = tmp_path / "mission-export.json"
    mission_file.write_text(json.dumps(mission), encoding="utf-8")
    try:
        installed = runner.invoke(app, ["pack", "install", str(MINIMAL_FIXTURE)])
        assert installed.exit_code == 0, installed.output

        created = runner.invoke(
            app,
            [
                "deliberate",
                "run",
                str(mission_file),
                "--packs",
                "p0-f1-minimal",
                "--impact-baseline-packs",
                "p0-f1-minimal",
                "--allow-compatible-packs",
                "--idempotency-key",
                "cli-kr-export-1",
            ],
        )
        assert created.exit_code == 0, created.output
        run_id = json.loads(created.output)["id"]

        default_result = runner.invoke(app, ["deliberate", "knowledge-requests", str(run_id)])
        assert default_result.exit_code == 0, default_result.output
        default_payload = json.loads(default_result.output)
        assert "knowledge_requests" in default_payload
        assert "schema" not in default_payload

        export_result = runner.invoke(
            app, ["deliberate", "knowledge-requests", str(run_id), "--format", "export"]
        )
        assert export_result.exit_code == 0, export_result.output
        assert "/private/openoyster/secret-path" not in export_result.output
        export_payload = json.loads(export_result.output)
        assert export_payload["schema"] == "openoyster.knowledge_request_export/v1"
        assert export_payload["run_id"] == run_id
        assert export_payload["parent_run_id"] is None
        assert isinstance(export_payload["mission_digest"], str)
        assert len(export_payload["mission_digest"]) > 0
        assert export_payload["decision_question"] == mission["decision_question"]
        assert isinstance(export_payload["requests"], list)
        for item in export_payload["requests"]:
            assert "local_key" in item
            assert "question" in item
            assert "gap_ref" in item
            assert "priority" in item
    finally:
        clear_settings_cache()


def test_api_knowledge_requests_export_schema(
    temp_settings: Settings, session_factory, tmp_path: Path
) -> None:
    pack_id = _install_fixture(session_factory, temp_settings, tmp_path)
    application = create_app(settings=temp_settings, session_factory=session_factory)
    auth = {temp_settings.api_key_header: str(temp_settings.api_key)}
    mission = _mission_payload()
    with TestClient(application) as client:
        created = client.post(
            "/v1/deliberations",
            json={
                "mission": mission,
                "packs": [pack_id],
                "impact_baseline_packs": [pack_id],
                "allow_compatible_packs": True,
            },
            headers={**auth, "Idempotency-Key": "api-kr-export-1"},
        )
        assert created.status_code == 200, created.text
        run_id = created.json()["id"]

        default = client.get(f"/v1/deliberations/{run_id}/knowledge-requests", headers=auth)
        assert default.status_code == 200
        assert "knowledge_requests" in default.json()
        assert "schema" not in default.json()

        export = client.get(
            f"/v1/deliberations/{run_id}/knowledge-requests",
            params={"format": "export"},
            headers=auth,
        )
        assert export.status_code == 200, export.text
        assert "/private/openoyster/secret-path" not in export.text
        payload = export.json()
        assert payload["schema"] == "openoyster.knowledge_request_export/v1"
        assert payload["run_id"] == run_id
        assert payload["parent_run_id"] is None
        assert isinstance(payload["mission_digest"], str)
        assert payload["decision_question"] == mission["decision_question"]
        assert isinstance(payload["requests"], list)
        for item in payload["requests"]:
            assert {"local_key", "question", "gap_ref", "priority"} <= set(item)
