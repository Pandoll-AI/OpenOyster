from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings
from .models import Base


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
    finally:
        if owned:
            runtime_engine.dispose()


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
