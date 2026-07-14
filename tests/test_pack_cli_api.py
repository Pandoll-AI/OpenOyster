from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openoyster.api.app import create_app
from openoyster.cli import app
from openoyster.config import clear_settings_cache

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
BROKEN_FIXTURE = (
    PROJECT_ROOT
    / "tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/missing-evidence-ref"
)


def _set_pack_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENOYSTER_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("OPENOYSTER_DB_URL", f"sqlite:///{tmp_path / 'pack-cli-api.db'}")
    monkeypatch.setenv("OPENOYSTER_API_KEY", "test-secret")
    monkeypatch.setenv("OPENOYSTER_LLM_PROVIDER", "stub")
    clear_settings_cache()


def test_pack_cli_validate_install_list_show_and_query_end_to_end(monkeypatch, tmp_path: Path) -> None:
    _set_pack_env(monkeypatch, tmp_path)
    runner = CliRunner()
    try:
        validated = runner.invoke(app, ["pack", "validate", str(MINIMAL_FIXTURE)])
        assert validated.exit_code == 0, validated.output
        assert json.loads(validated.output)["status"] == "pass"

        installed = runner.invoke(app, ["pack", "install", str(MINIMAL_FIXTURE)])
        assert installed.exit_code == 0, installed.output
        assert json.loads(installed.output)["status"] == "active"

        listed = runner.invoke(app, ["pack", "list"])
        assert listed.exit_code == 0, listed.output
        assert json.loads(listed.output)[0]["pack_id"] == "p0-f1-minimal"

        shown = runner.invoke(app, ["pack", "show", "p0-f1-minimal"])
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.output)["source_digest"]

        answered = runner.invoke(
            app, ["pack", "query", "Does the source support this claim?"]
        )
        assert answered.exit_code == 0, answered.output
        payload = json.loads(answered.output)
        assert payload["status"] == "supported"
        assert payload["citations"][0]["global_evidence_id"].startswith("opencrab://")
    finally:
        clear_settings_cache()


def test_pack_api_validate_install_list_show_and_query_end_to_end(
    temp_settings, session_factory
) -> None:
    application = create_app(settings=temp_settings, session_factory=session_factory)
    missing_directory = str(MINIMAL_FIXTURE / "manifest.json")
    auth_headers = {temp_settings.api_key_header: temp_settings.api_key}
    with TestClient(application) as client:
        unauthorised_validate = client.post(
            "/v1/packs/validate", json={"path": str(MINIMAL_FIXTURE)}
        )
        assert unauthorised_validate.status_code == 401

        unauthorised = client.post(
            "/v1/packs/install", json={"path": str(MINIMAL_FIXTURE), "profile": "compatible"}
        )
        assert unauthorised.status_code == 401

        unauthorised_query = client.post(
            "/v1/packs/query", json={"question": "Does the source support this claim?"}
        )
        assert unauthorised_query.status_code == 401

        invalid = client.post(
            "/v1/packs/validate", json={"path": missing_directory}, headers=auth_headers
        )
        assert invalid.status_code == 422
        assert missing_directory not in invalid.text
        assert invalid.json()["detail"]["code"] == "pack_directory_required"

        failed_validation = client.post(
            "/v1/packs/validate",
            json={"path": str(BROKEN_FIXTURE)},
            headers=auth_headers,
        )
        assert failed_validation.status_code == 422
        assert failed_validation.json()["detail"]["code"] == "pack_validation_failed"
        assert str(BROKEN_FIXTURE) not in failed_validation.text

        validated = client.post(
            "/v1/packs/validate",
            json={"path": str(MINIMAL_FIXTURE)},
            headers=auth_headers,
        )
        assert validated.status_code == 200
        assert validated.json()["status"] == "pass"

        installed = client.post(
            "/v1/packs/install",
            json={"path": str(MINIMAL_FIXTURE), "profile": "compatible"},
            headers=auth_headers,
        )
        assert installed.status_code == 200
        assert installed.json()["status"] == "active"

        listed = client.get("/v1/packs")
        assert listed.status_code == 200
        assert listed.json()[0]["pack_id"] == "p0-f1-minimal"

        shown = client.get("/v1/packs/p0-f1-minimal")
        assert shown.status_code == 200
        assert shown.json()["source_digest"]

        answered = client.post(
            "/v1/packs/query",
            json={"question": "Does the source support this claim?"},
            headers=auth_headers,
        )
        assert answered.status_code == 200
        assert answered.json()["status"] == "supported"
        assert answered.json()["citations"][0]["global_evidence_id"].startswith("opencrab://")
