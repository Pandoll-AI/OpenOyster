"""persist fulfilled_request_keys_json and backfill request_fingerprint

Revision ID: 0008_fulfilled_request_keys
Revises: 0007_request_fingerprint
Create Date: 2026-07-16

Adds the immutable fulfilled-keys column used by continuation identity and
replay. Backfills request_fingerprint for root runs whose inputs are
deterministically recoverable (mission_digest, pack scopes, policy,
parent_run_id=None, fulfilled keys=[]).

Legacy continuation runs (parent_run_id IS NOT NULL) intentionally do NOT
recover fulfilled keys from cognitive_transition claimed_knowledge_requests:
that list is attacker-mutable before upgrade and must not be promoted to the
immutable identity column. Continuations keep fulfilled_request_keys_json=[]
and request_fingerprint=NULL so lazy-fill (deliberation._assert_request_fingerprint)
owns the first post-upgrade identity claim. Replay skips transition recompute
for that legacy shape (recompute_skipped=legacy_fulfilled_keys_unrecoverable).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0008_fulfilled_request_keys"
down_revision: str | None = "0007_request_fingerprint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _payload_digest(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8", errors="ignore")).hexdigest()


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return loaded if isinstance(loaded, list) else []
    return []


def upgrade() -> None:
    with op.batch_alter_table("deliberation_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "fulfilled_request_keys_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )

    conn = op.get_bind()

    # Do NOT backfill fulfilled keys from cognitive_transition claimed lists.
    # Claimed is not an independent integrity source: pre-upgrade tampering would
    # be promoted into the immutable identity column. Legacy continuation runs
    # keep fulfilled_request_keys_json=[] and request_fingerprint=NULL; identity
    # is established by lazy-fill on first post-upgrade reuse, and replay marks
    # transition recompute as legacy_fulfilled_keys_unrecoverable.

    # Backfill request_fingerprint only for root runs (parent_run_id IS NULL)
    # whose inputs are fully recoverable with empty fulfilled keys.
    runs = conn.execute(
        sa.text(
            """
            SELECT id, mission_digest, parent_run_id,
                   policy_snapshot_json, fulfilled_request_keys_json
              FROM deliberation_runs
             WHERE request_fingerprint IS NULL
               AND parent_run_id IS NULL
            """
        )
    ).fetchall()
    for run_id, mission_digest, parent_run_id, policy_json, fulfilled_keys_json in runs:
        primary_rows = conn.execute(
            sa.text(
                """
                SELECT pack_id
                  FROM deliberation_pack_scopes
                 WHERE run_id = :id AND role = 'primary'
                 ORDER BY pack_id
                """
            ),
            {"id": run_id},
        ).fetchall()
        baseline_rows = conn.execute(
            sa.text(
                """
                SELECT pack_id
                  FROM deliberation_pack_scopes
                 WHERE run_id = :id AND role = 'impact_baseline'
                 ORDER BY pack_id
                """
            ),
            {"id": run_id},
        ).fetchall()
        policy = _as_dict(policy_json)
        fulfilled_keys = sorted(
            str(k) for k in _as_list(fulfilled_keys_json) if isinstance(k, str)
        )
        fingerprint = _payload_digest(
            {
                "mission_digest": mission_digest,
                "pack_ids": [row[0] for row in primary_rows],
                "impact_baseline_pack_ids": [row[0] for row in baseline_rows],
                "allow_compatible_packs": bool(policy.get("allow_compatible_packs", False)),
                "parent_run_id": parent_run_id,
                "fulfilled_keys": fulfilled_keys,
            }
        )
        conn.execute(
            sa.text(
                """
                UPDATE deliberation_runs
                   SET request_fingerprint = :fp
                 WHERE id = :id
                """
            ),
            {"fp": fingerprint, "id": run_id},
        )


def downgrade() -> None:
    """Drop fulfilled_request_keys_json only.

    request_fingerprint backfill performed in upgrade is intentionally
    irreversible data repair: downgrade removes the column added by this
    revision but does not clear fingerprints that upgrade may have written
    onto pre-existing rows. Schema symmetry (column add/drop) is preserved;
    data correction is one-way by design.
    """
    with op.batch_alter_table("deliberation_runs", schema=None) as batch_op:
        batch_op.drop_column("fulfilled_request_keys_json")
