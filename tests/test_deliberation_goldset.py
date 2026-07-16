"""CI harness tests for deliberation gold-set evaluation (W-A2).

Stub provider proves harness correctness (load, isolate, compare), not judgment
quality. Deterministic retrieval abstentions (no_evidence / no_match) must pass
under stub; quality-sensitive scenarios may fail and are reported honestly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openoyster.config import Settings
from openoyster.llm import StubProvider
from openoyster.services.evaluation_deliberation import (
    DEFAULT_SCENARIOS_DIR,
    ScenarioActual,
    ScenarioSpec,
    compare_scenario,
    discover_scenarios,
    evaluate_deliberation_goldset,
    load_scenario,
    run_scenario,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDSET_DIR = PROJECT_ROOT / "tests/fixtures/deliberation_goldset"


def test_default_scenarios_dir_exists() -> None:
    assert GOLDSET_DIR.is_dir()
    # Relative default used by CLI resolves from repo root in normal usage.
    assert DEFAULT_SCENARIOS_DIR.name == "deliberation_goldset"


def test_discover_loads_six_scenarios() -> None:
    specs = discover_scenarios(GOLDSET_DIR)
    assert len(specs) >= 6
    ids = {s.scenario_id for s in specs}
    assert "01_clear_select" in ids
    assert "02_no_evidence" in ids
    assert "03_contradictory_evidence" in ids
    assert "04_single_option" in ids
    assert "05_hard_constraint_only" in ids
    assert "06_no_match" in ids
    for spec in specs:
        assert spec.mission is not None
        assert spec.pack_dirs
        assert (spec.pack_dirs[0] / "manifest.json").is_file()


def test_compare_scenario_red_wrong_expectation_fails() -> None:
    """RED: intentionally wrong expected outcome must yield verdict=fail."""
    actual = ScenarioActual(
        outcome="abstain",
        abstention_reasons=["no_evidence"],
        retrieval_status="pack_has_no_evidence",
        run_status="completed",
    )
    wrong = ScenarioSpec(
        scenario_id="red_wrong_select",
        description="temporary RED expectation",
        expected_outcome="select",
        expected_abstention_reasons=[],
        expected_critic_issue_codes=[],
    )
    result = compare_scenario(wrong, actual)
    assert result.verdict == "fail"
    assert any("outcome mismatch" in n for n in result.notes)


def test_compare_scenario_green_matching_expectation_passes() -> None:
    """GREEN: matching expectation yields pass."""
    actual = ScenarioActual(
        outcome="abstain",
        abstention_reasons=["no_evidence"],
        retrieval_status="pack_has_no_evidence",
        run_status="completed",
    )
    good = ScenarioSpec(
        scenario_id="green_no_evidence",
        description="matching expectation",
        expected_outcome="abstain",
        expected_abstention_reasons=["no_evidence"],
        expected_retrieval_status="pack_has_no_evidence",
    )
    result = compare_scenario(good, actual)
    assert result.verdict == "pass"


def test_compare_critic_any_of_and_miss() -> None:
    hit = ScenarioActual(
        outcome="abstain",
        abstention_reasons=["critic_non_pass"],
        critic_issue_codes=["evidence_bias"],
        run_status="completed",
    )
    miss = ScenarioActual(
        outcome="select",
        abstention_reasons=[],
        critic_issue_codes=["coverage_ok"],
        run_status="completed",
    )
    spec = ScenarioSpec(
        scenario_id="critic_check",
        description="critic any-of",
        expected_outcome="abstain",
        expected_abstention_reasons=["critic_non_pass"],
        expected_critic_issue_codes=["evidence_bias", "missing_opposing_evidence"],
    )
    assert compare_scenario(spec, hit).verdict == "pass"
    failed = compare_scenario(spec, miss)
    assert failed.verdict == "fail"
    assert any("critic issue miss" in n for n in failed.notes)


def test_stub_deterministic_no_evidence_and_no_match_pass(
    temp_settings: Settings,
) -> None:
    """no_evidence and no_match are LLM-independent; must pass under stub."""
    provider = StubProvider()
    report = evaluate_deliberation_goldset(
        provider,
        scenarios_dir=GOLDSET_DIR,
        settings=temp_settings,
        scenario_ids=["02_no_evidence", "06_no_match"],
    )
    assert report.scenarios_evaluated == 2
    assert report.provider == "stub"
    assert report.model == "stub"
    assert "stub" in report.judge_note.casefold() or "judgment quality" in report.judge_note
    by_id = {r.scenario_id: r for r in report.results}
    assert by_id["02_no_evidence"].verdict == "pass", by_id["02_no_evidence"].notes
    assert by_id["06_no_match"].verdict == "pass", by_id["06_no_match"].notes
    assert by_id["02_no_evidence"].actual["retrieval_status"] == "pack_has_no_evidence"
    assert by_id["06_no_match"].actual["retrieval_status"] == "no_match_in_pack_evidence"


def test_full_goldset_harness_isolation_and_report_shape(
    temp_settings: Settings,
) -> None:
    """Full suite runs with isolation; report carries provenance + aggregates."""
    provider = StubProvider()
    report = evaluate_deliberation_goldset(
        provider,
        scenarios_dir=GOLDSET_DIR,
        settings=temp_settings,
    )
    assert report.scenarios_seen >= 6
    assert report.scenarios_evaluated == report.scenarios_seen
    assert report.kind == "deliberation_goldset"
    assert report.provider == "stub"
    assert "pass_rate" in report.aggregates
    assert "abstention_appropriateness" in report.aggregates
    assert "critic_hit_rate" in report.aggregates
    assert "select_accuracy" in report.aggregates
    assert report.provenance.get("provider") == "stub"
    assert report.judge_note
    # Deterministic structural scenarios must pass even if quality ones fail.
    by_id = {r.scenario_id: r for r in report.results}
    assert by_id["02_no_evidence"].verdict == "pass"
    assert by_id["06_no_match"].verdict == "pass"
    payload = report.to_dict()
    assert payload["results"]
    assert "judge_note" in payload


def test_run_scenario_isolates_db(temp_settings: Settings, tmp_path: Path) -> None:
    """Each run_scenario uses its own temp tree (no shared DB pollution)."""
    spec = load_scenario(GOLDSET_DIR / "02_no_evidence")
    provider = StubProvider()
    r1 = run_scenario(spec, provider=provider, settings=temp_settings)
    r2 = run_scenario(spec, provider=provider, settings=temp_settings)
    assert r1.verdict == "pass"
    assert r2.verdict == "pass"
    # Idempotency keys reuse per scenario name but isolated DBs → both complete.
    assert r1.actual.get("outcome") == "abstain"
    assert r2.actual.get("outcome") == "abstain"


def test_cli_deliberation_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI eval deliberation --json emits machine-readable report."""
    from typer.testing import CliRunner

    from openoyster.cli import app

    # Point settings at isolated stub workspace/db for the CLI runtime.
    workspace = tmp_path / "cli-ws"
    workspace.mkdir()
    monkeypatch.setenv("OPENOYSTER_LLM_PROVIDER", "stub")
    monkeypatch.setenv("OPENOYSTER_DB_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("OPENOYSTER_WORKSPACE", str(workspace))
    monkeypatch.setenv("OPENOYSTER_API_KEY", "test-secret")
    from openoyster.config import clear_settings_cache

    clear_settings_cache()
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "eval",
            "deliberation",
            "--scenarios",
            str(GOLDSET_DIR),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    # stdout may include rich formatting; parse last JSON object if needed.
    text = result.stdout.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Rich Console may wrap; find outermost braces.
        start = text.find("{")
        end = text.rfind("}")
        assert start >= 0 and end > start, text
        data = json.loads(text[start : end + 1])
    assert data["kind"] == "deliberation_goldset"
    assert data["provider"] == "stub"
    assert "judge_note" in data
    ids = {row["scenario_id"] for row in data["results"]}
    assert "02_no_evidence" in ids
    assert "06_no_match" in ids
    clear_settings_cache()
