"""opencrab pack runtime tables

Revision ID: 0003_opencrab_pack_runtime
Revises: 0002_chunks_fts
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_opencrab_pack_runtime"
down_revision: str | None = "0002_chunks_fts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pack_installs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pack_id", sa.String(length=250), nullable=False),
        sa.Column("declared_version", sa.String(length=120), nullable=False),
        sa.Column("format_version", sa.String(length=80), nullable=False),
        sa.Column("grammar_version", sa.String(length=120), nullable=True),
        sa.Column("source_digest", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_location", sa.Text(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("admission_profile", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("original_manifest_json", sa.JSON(), nullable=False),
        sa.Column("original_quality_json", sa.JSON(), nullable=True),
        sa.Column("admission_report_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_digest", name="uq_pack_installs_source_digest"),
        sa.UniqueConstraint(
            "pack_id",
            "declared_version",
            "source_digest",
            name="uq_pack_installs_revision",
        ),
    )
    with op.batch_alter_table("pack_installs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_pack_installs_pack_id"), ["pack_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_pack_installs_declared_version"), ["declared_version"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_pack_installs_source_digest"), ["source_digest"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_pack_installs_status"), ["status"], unique=False)
        batch_op.create_index(
            "ix_pack_installs_pack_status", ["pack_id", "status"], unique=False
        )

    op.create_table(
        "pack_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pack_install_id", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=True),
        sa.Column("declared_hash", sa.String(length=128), nullable=True),
        sa.Column("computed_hash", sa.String(length=128), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("validation_status", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["pack_install_id"], ["pack_installs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pack_install_id", "relative_path", name="uq_pack_files_install_path"
        ),
    )
    with op.batch_alter_table("pack_files", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_pack_files_pack_install_id"), ["pack_install_id"], unique=False
        )
        batch_op.create_index("ix_pack_files_hash", ["computed_hash"], unique=False)

    op.create_table(
        "pack_nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pack_install_id", sa.Integer(), nullable=False),
        sa.Column("local_node_id", sa.String(length=500), nullable=False),
        sa.Column("global_node_id", sa.String(length=1000), nullable=False),
        sa.Column("space", sa.String(length=120), nullable=True),
        sa.Column("node_type", sa.String(length=120), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("properties_json", sa.JSON(), nullable=False),
        sa.Column("quality_json", sa.JSON(), nullable=False),
        sa.Column("record_hash", sa.String(length=128), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["pack_install_id"], ["pack_installs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("global_node_id", name="uq_pack_nodes_global_id"),
        sa.UniqueConstraint(
            "pack_install_id", "local_node_id", name="uq_pack_nodes_install_local"
        ),
    )
    with op.batch_alter_table("pack_nodes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_pack_nodes_pack_install_id"), ["pack_install_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_pack_nodes_global_node_id"), ["global_node_id"], unique=False)
        batch_op.create_index("ix_pack_nodes_label", ["label"], unique=False)
        batch_op.create_index("ix_pack_nodes_type", ["node_type"], unique=False)

    op.create_table(
        "pack_edges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pack_install_id", sa.Integer(), nullable=False),
        sa.Column("local_edge_id", sa.String(length=500), nullable=False),
        sa.Column("global_edge_id", sa.String(length=1000), nullable=False),
        sa.Column("from_local_id", sa.String(length=500), nullable=False),
        sa.Column("to_local_id", sa.String(length=500), nullable=False),
        sa.Column("from_global_id", sa.String(length=1000), nullable=False),
        sa.Column("to_global_id", sa.String(length=1000), nullable=False),
        sa.Column("from_space", sa.String(length=120), nullable=True),
        sa.Column("to_space", sa.String(length=120), nullable=True),
        sa.Column("relation", sa.String(length=120), nullable=True),
        sa.Column("properties_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("record_hash", sa.String(length=128), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["pack_install_id"], ["pack_installs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("global_edge_id", name="uq_pack_edges_global_id"),
        sa.UniqueConstraint(
            "pack_install_id", "local_edge_id", name="uq_pack_edges_install_local"
        ),
    )
    with op.batch_alter_table("pack_edges", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_pack_edges_pack_install_id"), ["pack_install_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_pack_edges_global_edge_id"), ["global_edge_id"], unique=False)
        batch_op.create_index("ix_pack_edges_relation", ["relation"], unique=False)

    op.create_table(
        "pack_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pack_install_id", sa.Integer(), nullable=False),
        sa.Column("local_evidence_id", sa.String(length=500), nullable=False),
        sa.Column("global_evidence_id", sa.String(length=1000), nullable=False),
        sa.Column("kind", sa.String(length=120), nullable=True),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("parser_json", sa.JSON(), nullable=False),
        sa.Column("ocr_json", sa.JSON(), nullable=True),
        sa.Column("vision_json", sa.JSON(), nullable=True),
        sa.Column("location_json", sa.JSON(), nullable=False),
        sa.Column("links_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("asset_ref", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("raw_record_json", sa.JSON(), nullable=False),
        sa.Column("record_hash", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["pack_install_id"], ["pack_installs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("global_evidence_id", name="uq_pack_evidence_global_id"),
        sa.UniqueConstraint(
            "pack_install_id",
            "local_evidence_id",
            name="uq_pack_evidence_install_local",
        ),
    )
    with op.batch_alter_table("pack_evidence", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_pack_evidence_pack_install_id"), ["pack_install_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_pack_evidence_global_evidence_id"),
            ["global_evidence_id"],
            unique=False,
        )
        batch_op.create_index("ix_pack_evidence_kind", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_table("pack_evidence")
    op.drop_table("pack_edges")
    op.drop_table("pack_nodes")
    op.drop_table("pack_files")
    op.drop_table("pack_installs")
