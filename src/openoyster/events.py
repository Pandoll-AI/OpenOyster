from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Event, EventCursor, LoopLease
from .utils import ensure_utc

_POSTGRES_EVENT_STREAM_LOCK_KEY: Final = 3_377_001


@dataclass(frozen=True)
class EventEmission:
    event: Event
    created: bool


@dataclass(frozen=True)
class EventBatch:
    loop_name: str
    events: list[Event]
    checkpoint_id: int
    scanned_count: int


class EventBus:
    """Immutable event stream with idempotent emission and safe filtered checkpoints."""

    def emit(
        self,
        session: Session,
        event_type: str,
        payload: dict | None = None,
        *,
        source_loop: str | None = None,
        correlation_id: str | None = None,
        parent_event_id: int | None = None,
        idempotency_key: str | None = None,
    ) -> EventEmission:
        if idempotency_key:
            existing = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
            if existing:
                return EventEmission(existing, False)
        _lock_postgres_event_stream_until_commit(session)
        if idempotency_key:
            existing = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
            if existing:
                return EventEmission(existing, False)
        event = Event(
            event_type=event_type,
            payload_json=payload or {},
            source_loop=source_loop,
            correlation_id=correlation_id or str(uuid4()),
            parent_event_id=parent_event_id,
            idempotency_key=idempotency_key,
        )
        try:
            with session.begin_nested():
                session.add(event)
                session.flush()
        except IntegrityError:
            if not idempotency_key:
                raise
            existing = session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
            if existing is None:
                raise
            return EventEmission(existing, False)
        return EventEmission(event, True)

    def poll(
        self,
        session: Session,
        *,
        loop_name: str,
        event_types: Iterable[str],
        limit: int = 100,
        scan_multiplier: int = 20,
    ) -> EventBatch:
        # Deprecated compatibility parameter: SQL-side type filtering makes scan widening unnecessary.
        _ = scan_multiplier
        wanted = set(event_types)
        cursor = session.get(EventCursor, loop_name)
        last_event_id = cursor.last_event_id if cursor else 0
        locked = _lock_postgres_event_stream(session)
        try:
            max_event_id = session.scalar(select(func.max(Event.id)))
            upper_bound = max_event_id if max_event_id is not None and max_event_id > last_event_id else last_event_id
            selected = list(
                session.scalars(
                    select(Event)
                    .where(
                        Event.id > last_event_id,
                        Event.id <= upper_bound,
                        Event.event_type.in_(wanted),
                    )
                    .order_by(Event.id.asc())
                    .limit(limit)
                )
            )
        finally:
            if locked:
                _unlock_postgres_event_stream(session)
        checkpoint = selected[-1].id if len(selected) == limit else upper_bound
        return EventBatch(
            loop_name=loop_name,
            events=selected,
            checkpoint_id=checkpoint,
            scanned_count=len(selected),
        )

    def ack(self, session: Session, batch: EventBatch) -> None:
        if batch.checkpoint_id <= 0:
            return
        cursor = session.get(EventCursor, batch.loop_name)
        if cursor is None:
            session.add(
                EventCursor(
                    loop_name=batch.loop_name,
                    last_event_id=batch.checkpoint_id,
                )
            )
        else:
            cursor.last_event_id = max(cursor.last_event_id, batch.checkpoint_id)
        session.flush()

    def acquire_lease(
        self,
        session: Session,
        *,
        loop_name: str,
        owner: str,
        ttl_seconds: int,
    ) -> bool:
        now = datetime.now(UTC)
        lease = session.get(LoopLease, loop_name, with_for_update=True)
        if lease is not None:
            if lease.owner != owner and ensure_utc(lease.lease_until) > now:
                return False
            lease.owner = owner
            lease.lease_until = now + timedelta(seconds=ttl_seconds)
            session.flush()
            return True

        try:
            with session.begin_nested():
                session.add(
                    LoopLease(
                        loop_name=loop_name,
                        owner=owner,
                        lease_until=now + timedelta(seconds=ttl_seconds),
                    )
                )
                session.flush()
            return True
        except IntegrityError:
            lease = session.get(LoopLease, loop_name, with_for_update=True)
            if lease is None:
                raise
            if lease.owner != owner and ensure_utc(lease.lease_until) > now:
                return False
            lease.owner = owner
            lease.lease_until = now + timedelta(seconds=ttl_seconds)
            session.flush()
            return True

    def release_lease(
        self,
        session: Session,
        *,
        loop_name: str,
        owner: str,
    ) -> None:
        lease = session.get(LoopLease, loop_name, with_for_update=True)
        if lease and lease.owner == owner:
            lease.lease_until = datetime.now(UTC)
            session.flush()


bus = EventBus()


def _lock_postgres_event_stream(session: Session) -> bool:
    if session.get_bind().dialect.name != "postgresql":
        return False
    session.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": _POSTGRES_EVENT_STREAM_LOCK_KEY})
    return True


def _lock_postgres_event_stream_until_commit(session: Session) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _POSTGRES_EVENT_STREAM_LOCK_KEY},
    )


def _unlock_postgres_event_stream(session: Session) -> None:
    session.execute(
        text("SELECT pg_advisory_unlock(:lock_key)"),
        {"lock_key": _POSTGRES_EVENT_STREAM_LOCK_KEY},
    )
