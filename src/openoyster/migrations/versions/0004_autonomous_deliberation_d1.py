"""autonomous deliberation d1 tables

Revision ID: 0004_autonomous_deliberation_d1
Revises: 0003_opencrab_pack_runtime
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_autonomous_deliberation_d1"
down_revision: str | None = "0003_opencrab_pack_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deliberation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=250), nullable=False),
        sa.Column("mission_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("mission_digest", sa.String(length=128), nullable=False),
        sa.Column("policy_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("runtime_config_json", sa.JSON(), nullable=False),
        sa.Column("policy_digest", sa.String(length=128), nullable=False),
        sa.Column("runtime_config_digest", sa.String(length=128), nullable=False),
        sa.Column("contract_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_template_version", sa.String(length=80), nullable=False),
        sa.Column("primary_scope_digest", sa.String(length=128), nullable=False),
        sa.Column("impact_baseline_scope_digest", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("current_stage", sa.String(length=80), nullable=True),
        sa.Column("outcome", sa.String(length=40), nullable=True),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("degraded_json", sa.JSON(), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("llm_attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_deliberation_runs_idempotency_key"),
    )
    with op.batch_alter_table("deliberation_runs", schema=None) as batch_op:
        batch_op.create_index("ix_deliberation_runs_status", ["status"], unique=False)
        batch_op.create_index("ix_deliberation_runs_created_at", ["created_at"], unique=False)

    op.create_table(
        "deliberation_pack_scopes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("pack_install_id", sa.Integer(), nullable=False),
        sa.Column("pack_id", sa.String(length=250), nullable=False),
        sa.Column("declared_version", sa.String(length=120), nullable=False),
        sa.Column("source_digest", sa.String(length=128), nullable=False),
        sa.Column("admission_profile", sa.String(length=40), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["pack_install_id"], ["pack_installs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["deliberation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "role",
            "pack_install_id",
            name="uq_deliberation_pack_scopes_run_role_install",
        ),
    )
    with op.batch_alter_table("deliberation_pack_scopes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_pack_scopes_run_id"), ["run_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_deliberation_pack_scopes_pack_install_id"),
            ["pack_install_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_deliberation_pack_scopes_run_role", ["run_id", "role"], unique=False
        )

    op.create_table(
        "deliberation_evidence_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_key", sa.String(length=120), nullable=False),
        sa.Column("pack_evidence_id", sa.Integer(), nullable=False),
        sa.Column("global_evidence_id", sa.String(length=1000), nullable=False),
        sa.Column("local_evidence_id", sa.String(length=500), nullable=False),
        sa.Column("pack_install_id", sa.Integer(), nullable=False),
        sa.Column("record_hash", sa.String(length=128), nullable=False),
        sa.Column("prompt_visible_payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(length=128), nullable=False),
        sa.Column("retrieval_rank", sa.Integer(), nullable=False),
        sa.Column("retrieval_score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["pack_evidence_id"], ["pack_evidence.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["deliberation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "snapshot_key",
            name="uq_deliberation_evidence_snapshots_run_key",
        ),
    )
    with op.batch_alter_table("deliberation_evidence_snapshots", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_evidence_snapshots_run_id"), ["run_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_deliberation_evidence_snapshots_pack_evidence_id"),
            ["pack_evidence_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_deliberation_evidence_snapshots_global",
            ["global_evidence_id"],
            unique=False,
        )

    op.create_table(
        "deliberation_stage_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("effort", sa.String(length=40), nullable=True),
        sa.Column("template_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_digest", sa.String(length=128), nullable=True),
        sa.Column("config_digest", sa.String(length=128), nullable=True),
        sa.Column("input_manifest_digest", sa.String(length=128), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("response_digest", sa.String(length=128), nullable=True),
        sa.Column("raw_response_digest", sa.String(length=128), nullable=True),
        sa.Column("raw_response_length", sa.Integer(), nullable=True),
        sa.Column("usage_json", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["deliberation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "stage",
            "attempt_number",
            name="uq_deliberation_stage_calls_run_stage_attempt",
        ),
    )
    with op.batch_alter_table("deliberation_stage_calls", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_stage_calls_run_id"), ["run_id"], unique=False
        )
        batch_op.create_index(
            "ix_deliberation_stage_calls_run_stage", ["run_id", "stage"], unique=False
        )

    op.create_table(
        "deliberation_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("stage_call_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("local_key", sa.String(length=120), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["stage_call_id"], ["deliberation_stage_calls.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["deliberation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "kind",
            "local_key",
            name="uq_deliberation_artifacts_run_kind_key",
        ),
    )
    with op.batch_alter_table("deliberation_artifacts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_artifacts_run_id"), ["run_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_deliberation_artifacts_stage_call_id"),
            ["stage_call_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_deliberation_artifacts_run_kind", ["run_id", "kind"], unique=False
        )

    op.create_table(
        "deliberation_assertions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=250), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("classification", sa.String(length=40), nullable=False),
        sa.Column("mission_pointer", sa.String(length=250), nullable=True),
        sa.Column("artifact_ref", sa.String(length=250), nullable=True),
        sa.Column("issue_code", sa.String(length=120), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["deliberation_artifacts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("deliberation_assertions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_assertions_artifact_id"),
            ["artifact_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_deliberation_assertions_classification",
            ["classification"],
            unique=False,
        )

    op.create_table(
        "deliberation_citations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assertion_id", sa.Integer(), nullable=False),
        sa.Column("evidence_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("json_pointer", sa.String(length=500), nullable=True),
        sa.Column("value_digest", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["assertion_id"], ["deliberation_assertions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_snapshot_id"],
            ["deliberation_evidence_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("deliberation_citations", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_citations_assertion_id"),
            ["assertion_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_deliberation_citations_evidence_snapshot_id"),
            ["evidence_snapshot_id"],
            unique=False,
        )

    op.create_table(
        "deliberation_dossiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("dossier_json", sa.JSON(), nullable=False),
        sa.Column("dossier_markdown", sa.Text(), nullable=False),
        sa.Column("json_digest", sa.String(length=128), nullable=False),
        sa.Column("markdown_digest", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["deliberation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_deliberation_dossiers_run"),
    )
    with op.batch_alter_table("deliberation_dossiers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_dossiers_run_id"), ["run_id"], unique=False
        )

    op.create_table(
        "deliberation_cognitive_impacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=80), nullable=False),
        sa.Column("impact_json", sa.JSON(), nullable=False),
        sa.Column("impact_digest", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["deliberation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_deliberation_cognitive_impacts_run"),
    )
    with op.batch_alter_table("deliberation_cognitive_impacts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_cognitive_impacts_run_id"), ["run_id"], unique=False
        )

    op.create_table(
        "deliberation_replay_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("stored_dossier_digest", sa.String(length=128), nullable=False),
        sa.Column("recomputed_dossier_digest", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["deliberation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("deliberation_replay_results", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_replay_results_run_id"), ["run_id"], unique=False
        )
        batch_op.create_index("ix_deliberation_replay_results_run", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_table("deliberation_replay_results")
    op.drop_table("deliberation_cognitive_impacts")
    op.drop_table("deliberation_dossiers")
    op.drop_table("deliberation_citations")
    op.drop_table("deliberation_assertions")
    op.drop_table("deliberation_artifacts")
    op.drop_table("deliberation_stage_calls")
    op.drop_table("deliberation_evidence_snapshots")
    op.drop_table("deliberation_pack_scopes")
    op.drop_table("deliberation_runs")
