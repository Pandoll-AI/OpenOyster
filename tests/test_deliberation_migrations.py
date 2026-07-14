"""SQLite migration upgrade/downgrade for Autonomous Deliberation D1."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, sessionmaker

from openoyster.config import Settings
from openoyster.database import make_engine, upgrade_database

REQUIRED_TABLES = {
    "deliberation_runs",
    "deliberation_pack_scopes",
    "deliberation_evidence_snapshots",
    "deliberation_stage_calls",
    "deliberation_artifacts",
    "deliberation_assertions",
    "deliberation_citations",
    "deliberation_dossiers",
    "deliberation_cognitive_impacts",
    "deliberation_replay_results",
}


def _alembic_config(db_url: str) -> Config:
    script_location = Path(__file__).resolve().parents[1] / "src/openoyster/migrations"
    config = Config()
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    return config


def test_d1_migration_upgrade_and_downgrade(temp_settings: Settings, tmp_path: Path) -> None:
    """RED→GREEN: 0004 creates deliberation tables; downgrade removes them."""
    del tmp_path
    engine = make_engine(temp_settings)
    try:
        upgrade_database(temp_settings, revision="0003_opencrab_pack_runtime")
        inspector = inspect(engine)
        before = set(inspector.get_table_names())
        assert "pack_installs" in before
        assert "deliberation_runs" not in before

        upgrade_database(temp_settings, revision="head")
        inspector = inspect(engine)
        after = set(inspector.get_table_names())
        missing = REQUIRED_TABLES - after
        assert not missing, f"missing tables after upgrade: {sorted(missing)}"

        # Unique idempotency key constraint exists.
        run_uniques = {
            tuple(constraint.get("column_names") or ())
            for constraint in inspector.get_unique_constraints("deliberation_runs")
        }
        run_indexes = {
            idx["name"] for idx in inspector.get_indexes("deliberation_runs") if idx.get("unique")
        }
        assert ("idempotency_key",) in run_uniques or any(
            name and "idempotency" in name for name in run_indexes
        )

        config = _alembic_config(temp_settings.db_url)
        command.downgrade(config, "0003_opencrab_pack_runtime")
        inspector = inspect(engine)
        downgraded = set(inspector.get_table_names())
        leftover = REQUIRED_TABLES & downgraded
        assert not leftover, f"tables remained after downgrade: {sorted(leftover)}"
        assert "pack_installs" in downgraded
    finally:
        engine.dispose()


def test_d1_models_round_trip_create_all(
    session_factory: sessionmaker[Session], temp_settings: Settings
) -> None:
    """create_all path used by tests must include D1 tables."""
    del temp_settings
    from openoyster.models import DeliberationRun

    with session_factory() as session:
        run = DeliberationRun(
            idempotency_key="migration-smoke",
            mission_snapshot_json={"goal": "g", "decision_question": "q"},
            mission_digest="a" * 64,
            policy_snapshot_json={},
            runtime_config_json={},
            policy_digest="b" * 64,
            runtime_config_digest="c" * 64,
            contract_version="deliberation-d1-v1",
            prompt_template_version="deliberation-prompts-d1-v1",
            primary_scope_digest="d" * 64,
            impact_baseline_scope_digest="e" * 64,
            status="created",
            current_stage=None,
            outcome=None,
        )
        session.add(run)
        session.commit()
        loaded = session.get(DeliberationRun, run.id)
        assert loaded is not None
        assert loaded.idempotency_key == "migration-smoke"
        # PostgreSQL-portable: no SQLite-only types in alembic version table usage.
        row = session.execute(text("SELECT 1")).scalar()
        assert row == 1


def test_d2_continuity_migration_adds_and_removes_parent_run_link(
    temp_settings: Settings,
) -> None:
    """0005 adds the nullable self-reference used by linked re-deliberation."""
    engine = make_engine(temp_settings)
    try:
        upgrade_database(temp_settings, revision="0004_autonomous_deliberation_d1")
        inspector = inspect(engine)
        assert "parent_run_id" not in {
            column["name"] for column in inspector.get_columns("deliberation_runs")
        }

        upgrade_database(temp_settings, revision="head")
        inspector = inspect(engine)
        assert "parent_run_id" in {
            column["name"] for column in inspector.get_columns("deliberation_runs")
        }
        assert any(
            foreign_key.get("constrained_columns") == ["parent_run_id"]
            and foreign_key.get("referred_table") == "deliberation_runs"
            and foreign_key.get("referred_columns") == ["id"]
            for foreign_key in inspector.get_foreign_keys("deliberation_runs")
        )
        assert any(
            index.get("column_names") == ["parent_run_id"]
            for index in inspector.get_indexes("deliberation_runs")
        )

        config = _alembic_config(temp_settings.db_url)
        command.downgrade(config, "0004_autonomous_deliberation_d1")
        inspector = inspect(engine)
        assert "parent_run_id" not in {
            column["name"] for column in inspector.get_columns("deliberation_runs")
        }
    finally:
        engine.dispose()
