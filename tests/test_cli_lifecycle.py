from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from openoyster.cli import app
from openoyster.config import clear_settings_cache

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip Rich/ANSI styles so substring asserts stay stable under TTY capture."""
    return _ANSI_RE.sub("", text)


def test_cli_local_lifecycle(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "openoyster.db"
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "note.md").write_text(
        "Acme announced a governed data platform. The release includes audit logs, "
        "but deployment remains blocked by approval delays and missing ownership.",
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENOYSTER_WORKSPACE", str(workspace))
    monkeypatch.setenv("OPENOYSTER_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENOYSTER_API_KEY", "test-secret")
    monkeypatch.setenv("OPENOYSTER_LLM_PROVIDER", "stub")
    clear_settings_cache()

    runner = CliRunner()
    try:
        initialised = runner.invoke(app, ["init"])
        assert initialised.exit_code == 0, initialised.output

        ingested = runner.invoke(app, ["ingest", str(input_dir)])
        assert ingested.exit_code == 0, ingested.output
        assert "Copied 1 file" in _plain(ingested.output)

        executed = runner.invoke(app, ["run", "--cycles", "3", "--sleep", "0"])
        assert executed.exit_code == 0, executed.output
        assert "document_intake" in _plain(executed.output)

        diagnosed = runner.invoke(app, ["doctor"])
        assert diagnosed.exit_code == 0, diagnosed.output
        assert "PASS" in _plain(diagnosed.output)

        policy_created = runner.invoke(
            app,
            [
                "policy",
                "create",
                str(Path(__file__).parents[1] / "examples" / "policy.sample.yaml"),
                "--version",
                "test-policy",
            ],
        )
        assert policy_created.exit_code == 0, policy_created.output
        assert "candidate" in policy_created.output

        export_path = tmp_path / "export.json"
        exported = runner.invoke(app, ["export", "--output", str(export_path)])
        assert exported.exit_code == 0, exported.output
        payload = json.loads(export_path.read_text(encoding="utf-8"))
        assert payload["hypotheses"]
        assert "artifacts" in payload
    finally:
        clear_settings_cache()


def test_doctor_fails_when_codex_config_is_missing(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "openoyster.db"
    missing_config = tmp_path / "missing-codex-config"

    monkeypatch.setenv("OPENOYSTER_WORKSPACE", str(workspace))
    monkeypatch.setenv("OPENOYSTER_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OPENOYSTER_API_KEY", "test-secret")
    monkeypatch.setenv("OPENOYSTER_LLM_PROVIDER", "codex")
    monkeypatch.setenv("OPENOYSTER_CODEX_BINARY", "/bin/echo")
    monkeypatch.setenv("OPENOYSTER_CODEX_CONFIG_DIR", str(missing_config))
    clear_settings_cache()

    runner = CliRunner()
    try:
        initialised = runner.invoke(app, ["init"])
        assert initialised.exit_code == 0, initialised.output

        diagnosed = runner.invoke(app, ["doctor"])
        assert diagnosed.exit_code == 1, diagnosed.output
        assert "codex models config" in diagnosed.output
        assert "codex pipeline config" in diagnosed.output
    finally:
        clear_settings_cache()
