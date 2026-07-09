from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from openoyster.events import bus
from openoyster.llm import ExtractionUnavailable, LLMProvider
from openoyster.loops.extraction import ExtractionLoop
from openoyster.loops.maintenance import MaintenanceLoop
from openoyster.models import Chunk, Document, Event
from openoyster.policies import ensure_default_policy
from openoyster.schemas import TextAnalysis
from openoyster.utils import stable_hash


class UnavailableProvider(LLMProvider):
    name = "unavailable-test"

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        raise ExtractionUnavailable("codex unavailable for test")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        del prompt, stage
        raise ExtractionUnavailable("codex unavailable for test")


class MissingChunkProvider(LLMProvider):
    name = "missing-chunk-test"

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        return [
            TextAnalysis(
                entities=[],
                claims=[],
                signals=[],
                hypotheses=[],
                provider=self.name,
                model="missing",
                warnings=["missing result for chunk_index 0"],
                metadata={"missing_chunk_index": True},
            )
        ]

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        del prompt, stage
        raise ExtractionUnavailable("missing chunk provider does not implement JSON stages")


def test_extraction_unavailable_defers_chunks_without_document_failure(temp_settings, session_factory):
    with session_factory() as session:
        ensure_default_policy(session, temp_settings)
        document = Document(
            source="test",
            source_uri="memory://deferred",
            title="Deferred",
            content_hash=stable_hash("deferred"),
            ingest_key=stable_hash("deferred-ingest"),
            raw_text="Acme shipped a platform. Beta reported a governance issue.",
        )
        session.add(document)
        session.flush()
        bus.emit(session, "doc.fetched", {"document_id": document.id})
        session.commit()
        document_id = document.id

    with session_factory() as session:
        result = ExtractionLoop(temp_settings, provider=UnavailableProvider()).run(session)
        session.commit()

    with session_factory() as session:
        document = session.get(Document, document_id)
        chunk = session.scalar(select(Chunk).where(Chunk.document_id == document_id))
        deferred_events = session.scalar(
            select(func.count(Event.id)).where(Event.event_type == "extraction.deferred")
        )
        assert document.status == "deferred"
        assert document.failure_count == 0
        assert chunk.status == "deferred"
        assert chunk.attempts == 1
        assert chunk.last_error == "codex unavailable for test"
        assert chunk.metadata_json["deferred_count"] == 1
        assert deferred_events == 1
    assert result.created_records["deferred_chunks"] == 1


def test_missing_llm_chunk_index_defers_the_chunk(temp_settings, session_factory):
    with session_factory() as session:
        ensure_default_policy(session, temp_settings)
        document = Document(
            source="test",
            source_uri="memory://missing",
            title="Missing",
            content_hash=stable_hash("missing"),
            ingest_key=stable_hash("missing-ingest"),
            raw_text="Acme shipped a platform.",
        )
        session.add(document)
        session.flush()
        bus.emit(session, "doc.fetched", {"document_id": document.id})
        session.commit()
        document_id = document.id

    with session_factory() as session:
        result = ExtractionLoop(temp_settings, provider=MissingChunkProvider()).run(session)
        session.commit()

    with session_factory() as session:
        document = session.get(Document, document_id)
        chunk = session.scalar(select(Chunk).where(Chunk.document_id == document_id))
        deferred_events = session.scalar(
            select(func.count(Event.id)).where(Event.event_type == "extraction.deferred")
        )
        assert document.status == "deferred"
        assert document.failure_count == 0
        assert chunk.status == "deferred"
        assert chunk.last_error == "missing result for chunk_index 0"
        assert deferred_events == 1
    assert result.created_records["deferred_chunks"] == 1


def test_maintenance_requeues_deferred_chunks_after_cooloff(temp_settings, session_factory):
    with session_factory() as session:
        ensure_default_policy(session, temp_settings)
        document = Document(
            source="test",
            source_uri="memory://retry",
            title="Retry deferred",
            content_hash=stable_hash("retry"),
            ingest_key=stable_hash("retry-ingest"),
            raw_text="Acme shipped a platform.",
        )
        session.add(document)
        session.flush()
        chunk = Chunk(
            document_id=document.id,
            chunk_index=0,
            text="Acme shipped a platform.",
            text_hash=stable_hash("Acme shipped a platform."),
            status="deferred",
            attempts=99,
            last_error="codex unavailable",
            metadata_json={
                "deferred_count": 1,
                "deferred_at": (datetime.now(UTC) - timedelta(minutes=61)).isoformat(),
            },
        )
        session.add(chunk)
        session.commit()
        chunk_id = chunk.id

    with session_factory() as session:
        result = MaintenanceLoop(temp_settings).run(session)
        session.commit()

    with session_factory() as session:
        chunk = session.get(Chunk, chunk_id)
        retry_events = session.scalar(
            select(func.count(Event.id)).where(Event.event_type == "chunk.retry_requested")
        )
        assert chunk.status == "pending"
        assert chunk.attempts == 99
        assert retry_events == 1
    assert result.created_records["deferred_chunk_retries"] == 1


def test_maintenance_leaves_deferred_chunks_in_cooloff(temp_settings, session_factory):
    with session_factory() as session:
        ensure_default_policy(session, temp_settings)
        document = Document(
            source="test",
            source_uri="memory://cooloff",
            title="Cooloff deferred",
            content_hash=stable_hash("cooloff"),
            ingest_key=stable_hash("cooloff-ingest"),
            raw_text="Acme shipped a platform.",
        )
        session.add(document)
        session.flush()
        chunk = Chunk(
            document_id=document.id,
            chunk_index=0,
            text="Acme shipped a platform.",
            text_hash=stable_hash("Acme shipped a platform."),
            status="deferred",
            attempts=1,
            last_error="codex unavailable",
            metadata_json={
                "deferred_count": 1,
                "deferred_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
            },
        )
        session.add(chunk)
        session.commit()
        chunk_id = chunk.id

    with session_factory() as session:
        result = MaintenanceLoop(temp_settings).run(session)
        session.commit()

    with session_factory() as session:
        chunk = session.get(Chunk, chunk_id)
        retry_events = session.scalar(
            select(func.count(Event.id)).where(Event.event_type == "chunk.retry_requested")
        )
        assert chunk.status == "deferred"
        assert retry_events == 0
    assert result.created_records["deferred_chunk_retries"] == 0


def test_maintenance_stops_deferred_requeue_after_ten_deferrals(temp_settings, session_factory):
    with session_factory() as session:
        ensure_default_policy(session, temp_settings)
        document = Document(
            source="test",
            source_uri="memory://max",
            title="Max deferred",
            content_hash=stable_hash("max"),
            ingest_key=stable_hash("max-ingest"),
            raw_text="Acme shipped a platform.",
        )
        session.add(document)
        session.flush()
        chunk = Chunk(
            document_id=document.id,
            chunk_index=0,
            text="Acme shipped a platform.",
            text_hash=stable_hash("Acme shipped a platform."),
            status="deferred",
            attempts=1,
            last_error="codex unavailable",
            metadata_json={
                "deferred_count": 10,
                "deferred_at": (datetime.now(UTC) - timedelta(minutes=61)).isoformat(),
            },
        )
        session.add(chunk)
        session.commit()
        chunk_id = chunk.id

    with session_factory() as session:
        result = MaintenanceLoop(temp_settings).run(session)
        session.commit()

    with session_factory() as session:
        chunk = session.get(Chunk, chunk_id)
        retry_events = session.scalar(
            select(func.count(Event.id)).where(Event.event_type == "chunk.retry_requested")
        )
        assert chunk.status == "deferred"
        assert retry_events == 0
    assert result.created_records["deferred_chunk_retries"] == 0
