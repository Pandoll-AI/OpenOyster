"""add request_fingerprint to deliberation_runs

Revision ID: 0007_request_fingerprint
Revises: 0006_citation_role
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_request_fingerprint"
down_revision: str | None = "0006_citation_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("deliberation_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("request_fingerprint", sa.String(length=128), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("deliberation_runs", schema=None) as batch_op:
        batch_op.drop_column("request_fingerprint")
