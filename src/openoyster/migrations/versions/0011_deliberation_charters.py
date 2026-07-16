"""deliberation charters first-class table

Revision ID: 0011_deliberation_charters
Revises: 0010_decision_outcome_ledger
Create Date: 2026-07-16

Adds deliberation_charters so mission_charter_id can reference a real
active/archived sustained concern. Soft-delete via archive only.
Charters are control-plane grouping — never Pack evidence.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_deliberation_charters"
down_revision: str | None = "0010_decision_outcome_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deliberation_charters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=250), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("deliberation_charters", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_charters_status"), ["status"], unique=False
        )
        batch_op.create_index(
            "ix_deliberation_charters_created_at", ["created_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("deliberation_charters", schema=None) as batch_op:
        batch_op.drop_index("ix_deliberation_charters_created_at")
        batch_op.drop_index(batch_op.f("ix_deliberation_charters_status"))
    op.drop_table("deliberation_charters")
