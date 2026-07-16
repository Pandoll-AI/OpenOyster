"""flip trigger optional LLM confirmation columns

Revision ID: 0012_flip_trigger_confirmation
Revises: 0011_deliberation_charters
Create Date: 2026-07-16

Adds confirmation / confirmation_anchors_json / confirmation_note on
deliberation_flip_triggers for the optional flip_confirm LLM stage.
Existing rows default to confirmation='none'. Watch status is unaffected.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_flip_trigger_confirmation"
down_revision: str | None = "0011_deliberation_charters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("deliberation_flip_triggers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "confirmation",
                sa.String(length=20),
                server_default="none",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "confirmation_anchors_json",
                sa.JSON(),
                server_default=sa.text("'[]'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "confirmation_note",
                sa.String(length=120),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("deliberation_flip_triggers", schema=None) as batch_op:
        batch_op.drop_column("confirmation_note")
        batch_op.drop_column("confirmation_anchors_json")
        batch_op.drop_column("confirmation")
