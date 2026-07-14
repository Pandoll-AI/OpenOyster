"""Integration coverage for the Autonomous Deliberation D1 public surfaces."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openoyster.api.app import create_app
from openoyster.cli import app
from openoyster.config import Settings, clear_settings_cache
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
