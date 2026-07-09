from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings
from .models import Base

_CHUNKS_FTS_TRIGRAM = "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id', tokenize='trigram')"
_CHUNKS_FTS_UNICODE61 = "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id', tokenize='unicode61')"
_CHUNKS_FTS_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS chunks_fts_au AFTER UPDATE OF text ON chunks BEGIN
        INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
        INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
    END
    """,
)


def make_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    is_sqlite = settings.db_url.startswith("sqlite")
    engine = create_engine(
        settings.db_url,
        future=True,
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 30} if is_sqlite else {},
        pool_pre_ping=True,
    )
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            for statement in (
                "PRAGMA foreign_keys=ON",
                "PRAGMA journal_mode=WAL",
                "PRAGMA synchronous=NORMAL",
                "PRAGMA busy_timeout=30000",
            ):
                cursor.execute(statement)
            cursor.close()

    return engine


def make_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine or make_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def init_db(engine: Engine | None = None) -> None:
    """Create tables for embedded/test deployments.

    Long-lived deployments should run :func:`upgrade_database` instead so schema
    history remains explicit.
    """

    owned = engine is None
    runtime_engine = engine or make_engine()
    try:
        Base.metadata.create_all(runtime_engine)
        ensure_sqlite_chunks_fts(runtime_engine)
    finally:
        if owned:
            runtime_engine.dispose()


def ensure_sqlite_chunks_fts(bind: Engine | Connection) -> None:
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            ensure_sqlite_chunks_fts(connection)
        return
    if bind.dialect.name != "sqlite":
        return

    tokenizer = _existing_chunks_fts_tokenizer(bind)
    if tokenizer is None:
        try:
            bind.execute(text(_CHUNKS_FTS_TRIGRAM))
            tokenizer = "trigram"
        except OperationalError:
            try:
                bind.execute(text(_CHUNKS_FTS_UNICODE61))
                tokenizer = "unicode61"
            except OperationalError:
                _record_chunks_fts_tokenizer(bind, "unavailable")
                return

    for statement in _CHUNKS_FTS_TRIGGERS:
        bind.execute(text(statement))
    bind.execute(text("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')"))
    _record_chunks_fts_tokenizer(bind, tokenizer)


def drop_sqlite_chunks_fts(bind: Engine | Connection) -> None:
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            drop_sqlite_chunks_fts(connection)
        return
    if bind.dialect.name != "sqlite":
        return
    for trigger_name in ("chunks_fts_ai", "chunks_fts_ad", "chunks_fts_au"):
        bind.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name}"))
    bind.execute(text("DROP TABLE IF EXISTS chunks_fts"))
    _record_chunks_fts_tokenizer(bind, "dropped")


def _existing_chunks_fts_tokenizer(connection: Connection) -> str | None:
    sql = connection.scalar(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'chunks_fts'")
    )
    if not isinstance(sql, str):
        return None
    if "tokenize='trigram'" in sql or "tokenize=trigram" in sql:
        return "trigram"
    if "tokenize='unicode61'" in sql or "tokenize=unicode61" in sql:
        return "unicode61"
    return "unknown"


def _record_chunks_fts_tokenizer(connection: Connection, tokenizer: str) -> None:
    payload = json.dumps({"tokenizer": tokenizer}, ensure_ascii=False, sort_keys=True)
    connection.execute(
        text(
            """
            INSERT INTO system_state (key, value_json, updated_at)
            VALUES ('chunks_fts_tokenizer', :payload, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {"payload": payload},
    )


def upgrade_database(settings: Settings | None = None, revision: str = "head") -> None:
    settings = settings or get_settings()
    script_location = Path(__file__).with_name("migrations")
    config = Config()
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", settings.db_url.replace("%", "%%"))
    command.upgrade(config, revision)


@contextmanager
def session_scope(
    factory: sessionmaker[Session] | None = None,
) -> Iterator[Session]:
    owned_engine: Engine | None = None
    if factory is None:
        owned_engine = make_engine()
        factory = make_session_factory(owned_engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        if owned_engine is not None:
            owned_engine.dispose()
