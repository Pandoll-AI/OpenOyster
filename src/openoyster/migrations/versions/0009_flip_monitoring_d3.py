"""flip condition monitoring d3 tables

Revision ID: 0009_flip_monitoring_d3
Revises: 0008_fulfilled_request_keys
Create Date: 2026-07-16

Adds append-only flip watches and triggers so completed deliberation flip
conditions with structured predicates can be scanned against new Pack installs.
Status transitions only; no row deletion.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_flip_monitoring_d3"
down_revision: str | None = "0008_fulfilled_request_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deliberation_flip_watches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("flip_local_key", sa.String(length=120), nullable=False),
        sa.Column("predicate_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("dismiss_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["deliberation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "flip_local_key",
            name="uq_deliberation_flip_watches_run_key",
        ),
    )
    with op.batch_alter_table("deliberation_flip_watches", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_flip_watches_run_id"), ["run_id"], unique=False
        )
        batch_op.create_index(
            "ix_deliberation_flip_watches_status", ["status"], unique=False
        )
        batch_op.create_index(
            "ix_deliberation_flip_watches_created_at", ["created_at"], unique=False
        )

    op.create_table(
        "deliberation_flip_triggers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("watch_id", sa.Integer(), nullable=False),
        sa.Column("pack_install_id", sa.Integer(), nullable=False),
        sa.Column("matched_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["watch_id"], ["deliberation_flip_watches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["pack_install_id"], ["pack_installs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "watch_id",
            "pack_install_id",
            name="uq_deliberation_flip_triggers_watch_install",
        ),
    )
    with op.batch_alter_table("deliberation_flip_triggers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deliberation_flip_triggers_watch_id"),
            ["watch_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_deliberation_flip_triggers_pack_install_id"),
            ["pack_install_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("deliberation_flip_triggers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_deliberation_flip_triggers_pack_install_id"))
        batch_op.drop_index(batch_op.f("ix_deliberation_flip_triggers_watch_id"))
    op.drop_table("deliberation_flip_triggers")

    with op.batch_alter_table("deliberation_flip_watches", schema=None) as batch_op:
        batch_op.drop_index("ix_deliberation_flip_watches_created_at")
        batch_op.drop_index("ix_deliberation_flip_watches_status")
        batch_op.drop_index(batch_op.f("ix_deliberation_flip_watches_run_id"))
    op.drop_table("deliberation_flip_watches")
