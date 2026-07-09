from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from openoyster.config import Settings
from openoyster.database import init_db, make_engine, make_session_factory


@pytest.fixture()
def temp_settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    archive = workspace / "archive"
    inbox.mkdir(parents=True)
    archive.mkdir(parents=True)
    return Settings(
        db_url=f"sqlite:///{tmp_path / 'test.db'}",
        workspace=workspace,
        inbox_dir=inbox,
        archive_dir=archive,
        llm_provider="stub",
        api_key="test-secret",
        api_allow_unsafe_no_key=False,
        scheduler_tick_seconds=0.1,
    )


@pytest.fixture()
def engine(temp_settings: Settings) -> Generator[Engine]:
    runtime_engine = make_engine(temp_settings)
    init_db(runtime_engine)
    yield runtime_engine
    runtime_engine.dispose()


@pytest.fixture()
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)
