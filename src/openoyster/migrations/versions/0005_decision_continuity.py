"""decision continuity parent run link

Revision ID: 0005_decision_continuity
Revises: 0004_autonomous_deliberation_d1
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_decision_continuity"
down_revision: str | None = "0004_autonomous_deliberation_d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("deliberation_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("parent_run_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_deliberation_runs_parent_run_id",
            "deliberation_runs",
            ["parent_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_deliberation_runs_parent_run_id", ["parent_run_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("deliberation_runs", schema=None) as batch_op:
        batch_op.drop_index("ix_deliberation_runs_parent_run_id")
        batch_op.drop_constraint("fk_deliberation_runs_parent_run_id", type_="foreignkey")
        batch_op.drop_column("parent_run_id")
