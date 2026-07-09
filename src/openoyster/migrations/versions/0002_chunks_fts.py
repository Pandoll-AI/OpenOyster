from collections.abc import Sequence

from alembic import op

from openoyster.database import drop_sqlite_chunks_fts, ensure_sqlite_chunks_fts

revision: str = "0002_chunks_fts"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    ensure_sqlite_chunks_fts(op.get_bind())


def downgrade() -> None:
    drop_sqlite_chunks_fts(op.get_bind())
