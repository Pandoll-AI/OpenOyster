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

        upgrade_database(temp_settings, revision="0005_decision_continuity")
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


def test_0006_citation_role_migration_round_trip(temp_settings: Settings) -> None:
    """0006 adds deliberation_citations.role with server_default=statement; preserves rows."""
    engine = make_engine(temp_settings)
    try:
        upgrade_database(temp_settings, revision="0005_decision_continuity")
        inspector = inspect(engine)
        assert "role" not in {
            column["name"] for column in inspector.get_columns("deliberation_citations")
        }

        # Seed a citation row before upgrade so we can prove existing rows survive.
        with engine.begin() as conn:
            # Minimal parent chain: pack_install → pack_evidence → run → artifact → assertion → citation
            # (only if tables exist after 0005; insert via raw SQL for migration isolation)
            conn.execute(
                text(
                    """
                    INSERT INTO pack_installs (
                        pack_id, declared_version, format_version, source_digest,
                        source_type, source_location, storage_uri, admission_profile,
                        status, original_manifest_json, admission_report_json, created_at
                    ) VALUES (
                        'mig-pack', '0.0.1', 'opencrab-pack-v1', :digest,
                        'directory', '/tmp', '/tmp', 'compatible',
                        'active', '{}', '{}', CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"digest": "m" * 64},
            )
            pack_id = conn.execute(text("SELECT id FROM pack_installs LIMIT 1")).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO pack_evidence (
                        pack_install_id, local_evidence_id, global_evidence_id,
                        source_json, parser_json, location_json, links_json,
                        raw_record_json, record_hash
                    ) VALUES (
                        :pack_id, 'e1', 'pack://e1',
                        '{}', '{}', '{}', '{}',
                        '{}', :hash
                    )
                    """
                ),
                {"pack_id": pack_id, "hash": "h" * 64},
            )
            pe_id = conn.execute(text("SELECT id FROM pack_evidence LIMIT 1")).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_runs (
                        idempotency_key, mission_snapshot_json, mission_digest,
                        policy_snapshot_json, runtime_config_json, policy_digest,
                        runtime_config_digest, contract_version, prompt_template_version,
                        primary_scope_digest, impact_baseline_scope_digest, status,
                        degraded_json, llm_attempt_count, created_at, updated_at
                    ) VALUES (
                        'mig-0006', '{}', :d, '{}', '{}', :d, :d,
                        'deliberation-d1-v1', 'deliberation-prompts-d1-v1',
                        :d, :d, 'completed',
                        '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"d": "d" * 64},
            )
            run_id = conn.execute(text("SELECT id FROM deliberation_runs LIMIT 1")).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_evidence_snapshots (
                        run_id, snapshot_key, pack_evidence_id, global_evidence_id,
                        local_evidence_id, pack_install_id, record_hash,
                        prompt_visible_payload_json, payload_digest,
                        retrieval_rank, retrieval_score
                    ) VALUES (
                        :run_id, 'snap:1', :pe_id, 'pack://e1',
                        'e1', :pack_id, :hash,
                        '{}', :d, 1, 1.0
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "pe_id": pe_id,
                    "pack_id": pack_id,
                    "hash": "h" * 64,
                    "d": "d" * 64,
                },
            )
            snap_id = conn.execute(
                text("SELECT id FROM deliberation_evidence_snapshots LIMIT 1")
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_artifacts (
                        run_id, kind, local_key, payload_json, payload_digest, created_at
                    ) VALUES (
                        :run_id, 'beliefs', 'beliefs', '{}', :d, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"run_id": run_id, "d": "d" * 64},
            )
            art_id = conn.execute(
                text("SELECT id FROM deliberation_artifacts LIMIT 1")
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_assertions (
                        artifact_id, path, text, classification, metadata_json
                    ) VALUES (
                        :art_id, 'beliefs.b1.statement', 'claim', 'grounded_fact', '{}'
                    )
                    """
                ),
                {"art_id": art_id},
            )
            assertion_id = conn.execute(
                text("SELECT id FROM deliberation_assertions LIMIT 1")
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_citations (
                        assertion_id, evidence_snapshot_id, quote
                    ) VALUES (
                        :assertion_id, :snap_id, 'pre-upgrade quote'
                    )
                    """
                ),
                {"assertion_id": assertion_id, "snap_id": snap_id},
            )

        upgrade_database(temp_settings, revision="head")
        inspector = inspect(engine)
        role_col = next(
            col
            for col in inspector.get_columns("deliberation_citations")
            if col["name"] == "role"
        )
        assert role_col is not None
        # Existing row preserved with default role.
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT quote, role FROM deliberation_citations "
                    "WHERE quote = 'pre-upgrade quote'"
                )
            ).one()
            assert row[0] == "pre-upgrade quote"
            assert row[1] == "statement"

        config = _alembic_config(temp_settings.db_url)
        command.downgrade(config, "0005_decision_continuity")
        inspector = inspect(engine)
        assert "role" not in {
            column["name"] for column in inspector.get_columns("deliberation_citations")
        }
        with engine.connect() as conn:
            quote = conn.execute(
                text(
                    "SELECT quote FROM deliberation_citations "
                    "WHERE quote = 'pre-upgrade quote'"
                )
            ).scalar_one()
            assert quote == "pre-upgrade quote"
    finally:
        engine.dispose()


def test_0008_fulfilled_request_keys_and_fingerprint_backfill(
    temp_settings: Settings,
) -> None:
    """0008 adds fulfilled_request_keys_json and backfills recoverable fingerprints."""
    import hashlib
    import json

    engine = make_engine(temp_settings)
    try:
        upgrade_database(temp_settings, revision="0007_request_fingerprint")
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("deliberation_runs")}
        assert "request_fingerprint" in cols
        assert "fulfilled_request_keys_json" not in cols

        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO pack_installs (
                        pack_id, declared_version, format_version, source_digest,
                        source_type, source_location, storage_uri, admission_profile,
                        status, original_manifest_json, admission_report_json, created_at
                    ) VALUES (
                        'fp-pack', '0.0.1', 'opencrab-pack-v1', :digest,
                        'directory', '/tmp', '/tmp', 'compatible',
                        'active', '{}', '{}', CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"digest": "f" * 64},
            )
            pack_install_id = conn.execute(
                text("SELECT id FROM pack_installs WHERE pack_id = 'fp-pack'")
            ).scalar_one()
            mission_digest = "m" * 64
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_runs (
                        idempotency_key, request_fingerprint, parent_run_id,
                        mission_snapshot_json, mission_digest,
                        policy_snapshot_json, runtime_config_json, policy_digest,
                        runtime_config_digest, contract_version, prompt_template_version,
                        primary_scope_digest, impact_baseline_scope_digest, status,
                        degraded_json, llm_attempt_count, created_at, updated_at
                    ) VALUES (
                        'mig-0008-root', NULL, NULL,
                        '{}', :md,
                        :policy, '{}', :d, :d,
                        'deliberation-d1-v1', 'deliberation-prompts-d1-v1',
                        :d, :d, 'completed',
                        '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "md": mission_digest,
                    "policy": json.dumps({"allow_compatible_packs": True}),
                    "d": "d" * 64,
                },
            )
            run_id = conn.execute(
                text(
                    "SELECT id FROM deliberation_runs WHERE idempotency_key = 'mig-0008-root'"
                )
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_pack_scopes (
                        run_id, role, pack_install_id, pack_id,
                        declared_version, source_digest, admission_profile, snapshot_json
                    ) VALUES (
                        :run_id, 'primary', :pack_install_id, 'fp-pack',
                        '0.0.1', :digest, 'compatible', '{}'
                    )
                    """
                ),
                {
                    "run_id": run_id,
                    "pack_install_id": pack_install_id,
                    "digest": "f" * 64,
                },
            )

        upgrade_database(temp_settings, revision="head")
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("deliberation_runs")}
        assert "fulfilled_request_keys_json" in cols

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT request_fingerprint, fulfilled_request_keys_json
                      FROM deliberation_runs
                     WHERE idempotency_key = 'mig-0008-root'
                    """
                )
            ).one()
            fp, keys_json = row[0], row[1]
            assert fp is not None and len(fp) == 64
            keys = keys_json if isinstance(keys_json, list) else json.loads(keys_json or "[]")
            assert keys == []
            expected = hashlib.sha256(
                json.dumps(
                    {
                        "mission_digest": mission_digest,
                        "pack_ids": ["fp-pack"],
                        "impact_baseline_pack_ids": [],
                        "allow_compatible_packs": True,
                        "parent_run_id": None,
                        "fulfilled_keys": [],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8", errors="ignore")
            ).hexdigest()
            assert fp == expected

        config = _alembic_config(temp_settings.db_url)
        command.downgrade(config, "0007_request_fingerprint")
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("deliberation_runs")}
        assert "fulfilled_request_keys_json" not in cols
        # Schema drop only; fingerprint backfill is intentionally irreversible data repair.
        assert "request_fingerprint" in cols
    finally:
        engine.dispose()


def test_0008_does_not_trust_transition_claimed_for_continuation_backfill(
    temp_settings: Settings,
) -> None:
    """R3: legacy continuation keeps fulfilled=[] and fingerprint NULL (no claimed trust)."""
    import json

    engine = make_engine(temp_settings)
    try:
        upgrade_database(temp_settings, revision="0007_request_fingerprint")
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO pack_installs (
                        pack_id, declared_version, format_version, source_digest,
                        source_type, source_location, storage_uri, admission_profile,
                        status, original_manifest_json, admission_report_json, created_at
                    ) VALUES (
                        'cont-pack', '0.0.1', 'opencrab-pack-v1', :digest,
                        'directory', '/tmp', '/tmp', 'compatible',
                        'active', '{}', '{}', CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"digest": "c" * 64},
            )
            pack_install_id = conn.execute(
                text("SELECT id FROM pack_installs WHERE pack_id = 'cont-pack'")
            ).scalar_one()
            # Parent root
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_runs (
                        idempotency_key, request_fingerprint, parent_run_id,
                        mission_snapshot_json, mission_digest,
                        policy_snapshot_json, runtime_config_json, policy_digest,
                        runtime_config_digest, contract_version, prompt_template_version,
                        primary_scope_digest, impact_baseline_scope_digest, status,
                        degraded_json, llm_attempt_count, created_at, updated_at
                    ) VALUES (
                        'mig-0008-parent', NULL, NULL,
                        '{}', :md,
                        :policy, '{}', :d, :d,
                        'deliberation-d1-v1', 'deliberation-prompts-d1-v1',
                        :d, :d, 'completed',
                        '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "md": "m" * 64,
                    "policy": json.dumps({"allow_compatible_packs": True}),
                    "d": "d" * 64,
                },
            )
            parent_id = conn.execute(
                text(
                    "SELECT id FROM deliberation_runs WHERE idempotency_key = 'mig-0008-parent'"
                )
            ).scalar_one()
            # Child continuation with tamperable claimed list on transition artifact
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_runs (
                        idempotency_key, request_fingerprint, parent_run_id,
                        mission_snapshot_json, mission_digest,
                        policy_snapshot_json, runtime_config_json, policy_digest,
                        runtime_config_digest, contract_version, prompt_template_version,
                        primary_scope_digest, impact_baseline_scope_digest, status,
                        degraded_json, llm_attempt_count, created_at, updated_at
                    ) VALUES (
                        'mig-0008-child', NULL, :parent_id,
                        '{}', :md,
                        :policy, '{}', :d, :d,
                        'deliberation-d1-v1', 'deliberation-prompts-d1-v1',
                        :d, :d, 'completed',
                        '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "parent_id": parent_id,
                    "md": "m" * 64,
                    "policy": json.dumps({"allow_compatible_packs": True}),
                    "d": "d" * 64,
                },
            )
            child_id = conn.execute(
                text(
                    "SELECT id FROM deliberation_runs WHERE idempotency_key = 'mig-0008-child'"
                )
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_pack_scopes (
                        run_id, role, pack_install_id, pack_id,
                        declared_version, source_digest, admission_profile, snapshot_json
                    ) VALUES (
                        :run_id, 'primary', :pack_install_id, 'cont-pack',
                        '0.0.1', :digest, 'compatible', '{}'
                    )
                    """
                ),
                {
                    "run_id": child_id,
                    "pack_install_id": pack_install_id,
                    "digest": "c" * 64,
                },
            )
            claimed = [{"local_key": "TAMPERED-CLAIMED-KEY", "status": "claimed"}]
            conn.execute(
                text(
                    """
                    INSERT INTO deliberation_artifacts (
                        run_id, stage_call_id, kind, local_key,
                        payload_json, payload_digest, created_at
                    ) VALUES (
                        :run_id, NULL, 'cognitive_transition', 'cognitive_transition',
                        :payload, :digest, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "run_id": child_id,
                    "payload": json.dumps(
                        {
                            "method": "cognitive_transition_v2",
                            "claimed_knowledge_requests": claimed,
                        }
                    ),
                    "digest": "a" * 64,
                },
            )

        upgrade_database(temp_settings, revision="head")
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT request_fingerprint, fulfilled_request_keys_json
                      FROM deliberation_runs
                     WHERE idempotency_key = 'mig-0008-child'
                    """
                )
            ).one()
            fp, keys_json = row[0], row[1]
            keys = keys_json if isinstance(keys_json, list) else json.loads(keys_json or "[]")
            # Must not promote transition claimed into fulfilled keys.
            assert keys == []
            assert "TAMPERED-CLAIMED-KEY" not in keys
            # Continuation fingerprint left for lazy-fill (not reconstructed from claimed).
            assert fp is None
    finally:
        engine.dispose()
