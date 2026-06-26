from __future__ import annotations

import os

from sqlalchemy import func, select

from openoyster.loops.intake import DocumentIntakeLoop
from openoyster.models import Document, Event, SourceItem


def test_duplicate_reingest_does_not_rollback_or_starve_new_file(
    temp_settings,
    session_factory,
):
    first = temp_settings.inbox_dir / "first.md"
    first.write_text("Acme launched a material governance programme.", encoding="utf-8")
    loop = DocumentIntakeLoop(temp_settings)

    with session_factory() as session:
        result = loop.run(session)
        session.commit()
    assert result.created_records["documents"] == 1

    os.utime(first, None)
    second = temp_settings.inbox_dir / "second.md"
    second.write_text("Beta reported a critical operating incident.", encoding="utf-8")
    with session_factory() as session:
        result = loop.run(session)
        session.commit()

    with session_factory() as session:
        assert session.scalar(select(func.count(Document.id))) == 2
        assert session.scalar(select(func.count(SourceItem.id))) == 2
        assert session.scalar(select(func.count(Event.id)).where(Event.event_type == "doc.fetched")) == 2
    assert result.created_records.get("duplicates", 0) == 1
    assert result.created_records.get("documents", 0) == 1


def test_bad_document_does_not_block_good_document(temp_settings, session_factory):
    (temp_settings.inbox_dir / "bad.json").write_text("{broken", encoding="utf-8")
    (temp_settings.inbox_dir / "good.md").write_text(
        "Gamma is hiring data governance specialists.",
        encoding="utf-8",
    )
    with session_factory() as session:
        result = DocumentIntakeLoop(temp_settings).run(session)
        session.commit()
    with session_factory() as session:
        assert session.scalar(select(func.count(Document.id))) == 1
        states = {item.source_uri: item.status for item in session.scalars(select(SourceItem))}
    assert any(status == "failed" for status in states.values())
    assert any(status == "ingested" for status in states.values())
    assert result.created_records["failed"] == 1
