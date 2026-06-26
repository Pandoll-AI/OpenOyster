from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from openoyster.cli import app
from openoyster.config import clear_settings_cache


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
    monkeypatch.setenv("OPENOYSTER_LLM_PROVIDER", "local")
    clear_settings_cache()

    runner = CliRunner()
    try:
        initialised = runner.invoke(app, ["init"])
        assert initialised.exit_code == 0, initialised.output

        ingested = runner.invoke(app, ["ingest", str(input_dir)])
        assert ingested.exit_code == 0, ingested.output
        assert "Copied 1 file" in ingested.output

        executed = runner.invoke(app, ["run", "--cycles", "3", "--sleep", "0"])
        assert executed.exit_code == 0, executed.output
        assert "document_intake" in executed.output

        diagnosed = runner.invoke(app, ["doctor"])
        assert diagnosed.exit_code == 0, diagnosed.output
        assert "PASS" in diagnosed.output

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
        assert payload["artifacts"]
    finally:
        clear_settings_cache()
