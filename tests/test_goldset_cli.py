from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openoyster.config import clear_settings_cache

FIXTURE_ROOT = Path(__file__).parent / "goldset_fixtures"
DOCS_DIR = FIXTURE_ROOT / "docs"
LABELS_DIR = FIXTURE_ROOT / "labels"


def _set_stub_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENOYSTER_LLM_PROVIDER", "stub")
    monkeypatch.setenv("OPENOYSTER_DB_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("OPENOYSTER_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("OPENOYSTER_CODEX_BATCH_SIZE", "2")
    clear_settings_cache()


def test_eval_gold_cli_smoke_with_stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_stub_env(monkeypatch, tmp_path)
    from openoyster.cli import app

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "gold",
            "--no-report",
            "--docs-dir",
            str(DOCS_DIR),
            "--labels-dir",
            str(LABELS_DIR),
        ],
    )
    clear_settings_cache()

    assert result.exit_code == 0, result.output
    assert "stub" in result.output
    assert "Gold documents evaluated: 2" in result.output


def test_gold_review_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_stub_env(monkeypatch, tmp_path)
    labels_dir = tmp_path / "labels"
    shutil.copytree(LABELS_DIR, labels_dir)
    from openoyster.cli import app

    result = CliRunner().invoke(
        app,
        [
            "gold",
            "review",
            "--only",
            "../outside",
            "--docs-dir",
            str(DOCS_DIR),
            "--labels-dir",
            str(labels_dir),
        ],
    )
    clear_settings_cache()

    assert result.exit_code != 0
    assert not (tmp_path / "outside.json").exists()


def test_gold_review_bulk_skips_symlink_escape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_stub_env(monkeypatch, tmp_path)
    labels_dir = tmp_path / "labels"
    shutil.copytree(LABELS_DIR, labels_dir)
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps(
            {
                "doc_id": "outside",
                "review_status": "unreviewed",
                "expected_entities": [],
                "expected_signals": [],
            }
        ),
        encoding="utf-8",
    )
    (labels_dir / "escape.json").symlink_to(outside)
    from openoyster.cli import app

    result = CliRunner().invoke(
        app,
        [
            "gold",
            "review",
            "--docs-dir",
            str(DOCS_DIR),
            "--labels-dir",
            str(labels_dir),
        ],
        input="s\ns\n",
    )
    clear_settings_cache()

    assert result.exit_code == 0, result.output
    assert "Skipping unsafe label path" in result.output
    assert json.loads(outside.read_text(encoding="utf-8"))["review_status"] == "unreviewed"
