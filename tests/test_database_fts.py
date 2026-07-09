from __future__ import annotations

from openoyster.database import drop_sqlite_chunks_fts, ensure_sqlite_chunks_fts


class FakeDialect:
    name = "postgresql"


class FakePostgresConnection:
    dialect = FakeDialect()

    def execute(self, statement, parameters=None):
        del statement, parameters
        raise AssertionError("PostgreSQL FTS migration path must not execute SQLite DDL")


def test_chunks_fts_helpers_are_noop_for_postgresql_dialect():
    connection = FakePostgresConnection()

    ensure_sqlite_chunks_fts(connection)
    drop_sqlite_chunks_fts(connection)
