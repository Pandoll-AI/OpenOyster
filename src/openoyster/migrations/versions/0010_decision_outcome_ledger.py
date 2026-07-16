"""decision outcome ledger tables

Revision ID: 0010_decision_outcome_ledger
Revises: 0009_flip_monitoring_d3
Create Date: 2026-07-16

Adds append-only deliberation_outcomes so completed run results can be
recorded for deterministic calibration. No update/delete path; corrections
are new rows. Outcomes are usage records, never Pack evidence.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_decision_outcome_ledger"
down_revision: str | None = "0009_flip_monitoring_d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deliberation_outcomes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("outcome_label", sa.String(length=40), nullable=False),
        sa.Column("scenario_assessments", sa.JSON(), nullable=False),
        sa.Column("abstention_assessment", sa.String(length=80), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("noted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("noted_by", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=250), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["deliberation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Global unique: same key cannot be reused across runs. NULL keys are
        # exempt from the unique constraint (SQL standard / SQLite / Postgres).
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_deliberation_outcomes_idempotency_key",
        ),
    )
    with op.batch_alter_table("deliberation_outcomes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_outcomes_run_id"), ["run_id"], unique=False
        )
        batch_op.create_index(
            "ix_deliberation_outcomes_noted_at", ["noted_at"], unique=False
        )
        batch_op.create_index(
            "ix_deliberation_outcomes_outcome_label", ["outcome_label"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("deliberation_outcomes", schema=None) as batch_op:
        batch_op.drop_index("ix_deliberation_outcomes_outcome_label")
        batch_op.drop_index("ix_deliberation_outcomes_noted_at")
        batch_op.drop_index(batch_op.f("ix_deliberation_outcomes_run_id"))
    op.drop_table("deliberation_outcomes")
