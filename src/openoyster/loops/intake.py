from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..connectors.filesystem import file_fingerprint, iter_supported_files, parse_file
from ..events import bus
from ..models import Document, Source, SourceItem
from .base import BaseLoop, LoopResult


class DocumentIntakeLoop(BaseLoop):
    """Discovers and ingests supported files without transaction-wide duplicate rollbacks."""

    name = "document_intake"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _ensure_source(self, session: Session) -> Source:
        source = session.scalar(select(Source).where(Source.name == "local-inbox"))
        if source:
            source.uri = str(self.settings.inbox_dir)
            return source
        source = Source(
            name="local-inbox",
            kind="filesystem",
            uri=str(self.settings.inbox_dir),
            enabled=True,
            metadata_json={"managed_by": self.name},
        )
        session.add(source)
        session.flush()
        return source

    def _source_item(self, session: Session, path: Path) -> SourceItem:
        source_uri = path.resolve().as_uri()
        item = session.scalar(
            select(SourceItem).where(
                SourceItem.source == "local-inbox",
                SourceItem.source_uri == source_uri,
            )
        )
        if item:
            return item
        item = SourceItem(source="local-inbox", source_uri=source_uri)
        session.add(item)
        session.flush()
        return item

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        self.settings.ensure_workspace()
        assert self.settings.inbox_dir is not None
        result = LoopResult(loop_name=self.name)
        self._ensure_source(session)
        files = list(iter_supported_files(self.settings.inbox_dir))[:limit]

        for path in files:
            item = self._source_item(session, path)
            item.last_seen_at = datetime.now(UTC)
            try:
                fingerprint = file_fingerprint(path)
                if item.fingerprint == fingerprint and item.status == "ingested":
                    result.inc("unchanged")
                    continue
                parsed = parse_file(
                    path,
                    max_bytes=self.settings.max_file_bytes,
                    source="local-inbox",
                )
                document = session.scalar(select(Document).where(Document.ingest_key == parsed.ingest_key))
                created = False
                if document is None:
                    document = Document(
                        source=parsed.source,
                        source_uri=parsed.source_uri,
                        title=parsed.title,
                        content_hash=parsed.content_hash,
                        ingest_key=parsed.ingest_key,
                        raw_text=parsed.text,
                        status="pending",
                        parser_version=parsed.parser_version,
                        metadata_json=parsed.metadata,
                    )
                    try:
                        with session.begin_nested():
                            session.add(document)
                            session.flush()
                        created = True
                    except IntegrityError:
                        document = session.scalar(
                            select(Document).where(Document.ingest_key == parsed.ingest_key)
                        )
                        if document is None:
                            raise
                item.fingerprint = fingerprint
                item.status = "ingested"
                item.last_document_id = document.id
                item.last_error = None
                item.ingested_at = datetime.now(UTC)
                emission = bus.emit(
                    session,
                    "doc.fetched",
                    {
                        "document_id": document.id,
                        "source_item_id": item.id,
                        "created": created,
                    },
                    source_loop=self.name,
                    idempotency_key=f"doc.fetched:{document.id}",
                )
                if emission.created:
                    result.emitted_events += 1
                result.inc("documents" if created else "duplicates")
                if self.settings.archive_processed_files:
                    item.metadata_json = {
                        **item.metadata_json,
                        "archive_requested": True,
                        "archive_source_path": str(path.resolve()),
                    }
            except Exception as exc:  # one bad file must not poison later files
                item.status = "failed"
                item.last_error = str(exc)
                result.inc("failed")
                result.notes.append(f"{path.name}: {exc}")
        return result
