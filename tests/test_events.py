from __future__ import annotations

from uuid import uuid4

from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from openoyster.events import EventBatch, bus
from openoyster.models import Base, Event


def test_idempotent_event_emission(session_factory):
    with session_factory() as session:
        first = bus.emit(session, "x", {"n": 1}, idempotency_key="same")
        second = bus.emit(session, "x", {"n": 2}, idempotency_key="same")
        session.commit()
    assert first.created is True
    assert second.created is False
    assert first.event.id == second.event.id


def test_postgres_emit_holds_advisory_lock_until_transaction_end():
    statements: list[str] = []
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_postgres_lock_functions(dbapi_connection, _connection_record):
        dbapi_connection.create_function("pg_advisory_xact_lock", 1, lambda _lock_key: 1)
        dbapi_connection.create_function("pg_advisory_unlock", 1, lambda _lock_key: 1)

    @event.listens_for(engine, "before_cursor_execute")
    def record_statements(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    Base.metadata.create_all(engine)
    engine.dialect.name = "postgresql"
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with factory() as session:
            bus.emit(session, "wanted", {"ok": True})
            session.commit()
    finally:
        engine.dispose()

    lock_index = next(index for index, statement in enumerate(statements) if "pg_advisory_xact_lock" in statement)
    insert_index = next(index for index, statement in enumerate(statements) if "INSERT INTO events" in statement)
    assert lock_index < insert_index
    assert all("pg_advisory_unlock" not in statement for statement in statements)


def test_filtered_cursor_advances_without_starvation(session_factory):
    with session_factory() as session:
        for index in range(20):
            bus.emit(session, "noise", {"i": index})
        wanted = bus.emit(session, "wanted", {"ok": True}).event
        session.commit()

    found = []
    for _ in range(20):
        with session_factory() as session:
            batch = bus.poll(
                session,
                loop_name="consumer",
                event_types=("wanted",),
                limit=1,
                scan_multiplier=2,
            )
            found.extend(batch.events)
            bus.ack(session, batch)
            session.commit()
        if found:
            break
    assert [event.id for event in found] == [wanted.id]


def test_partial_checkpoint_does_not_drop_later_work(session_factory):
    with session_factory() as session:
        events = [bus.emit(session, "wanted", {"i": i}).event for i in range(3)]
        session.commit()

    with session_factory() as session:
        batch = bus.poll(
            session,
            loop_name="partial",
            event_types=("wanted",),
            limit=3,
        )
        bus.ack(
            session,
            EventBatch(
                loop_name="partial",
                events=[batch.events[0]],
                checkpoint_id=batch.events[0].id,
                scanned_count=1,
            ),
        )
        session.commit()

    with session_factory() as session:
        remaining = bus.poll(
            session,
            loop_name="partial",
            event_types=("wanted",),
            limit=3,
        )
    assert [event.id for event in remaining.events] == [events[1].id, events[2].id]


def test_poll_skips_unwanted_checkpoint_without_dropping_new_wanted(session_factory):
    with session_factory() as session:
        noise = [bus.emit(session, "noise", {"i": index}).event for index in range(5)]
        session.commit()

    with session_factory() as polling_session:
        batch = bus.poll(
            polling_session,
            loop_name="race-safe",
            event_types=("wanted",),
            limit=5,
        )

        with session_factory() as writer_session:
            wanted = bus.emit(writer_session, "wanted", {"after": "poll"}).event
            writer_session.commit()

        bus.ack(polling_session, batch)
        polling_session.commit()

    assert batch.events == []
    assert batch.checkpoint_id == noise[-1].id

    with session_factory() as session:
        next_batch = bus.poll(
            session,
            loop_name="race-safe",
            event_types=("wanted",),
            limit=5,
        )

    assert [event.id for event in next_batch.events] == [wanted.id]


def test_poll_sparse_wanted_stream_uses_sql_filter_not_scan_multiplier(session_factory):
    with session_factory() as session:
        for index in range(10_000):
            event_type = "wanted" if index % 100 == 0 else "noise"
            bus.emit(session, event_type, {"i": index})
        session.commit()

    with session_factory() as session:
        first_batch = bus.poll(
            session,
            loop_name="sparse",
            event_types=("wanted",),
            limit=50,
            scan_multiplier=1,
        )
        bus.ack(session, first_batch)
        session.commit()

    with session_factory() as session:
        second_batch = bus.poll(
            session,
            loop_name="sparse",
            event_types=("wanted",),
            limit=50,
            scan_multiplier=1,
        )

    assert len(first_batch.events) == 50
    assert len(second_batch.events) == 50
    assert first_batch.scanned_count == 50
    assert second_batch.scanned_count == 50


def test_poll_full_page_checkpoint_is_last_selected_event_id(session_factory):
    with session_factory() as session:
        first = bus.emit(session, "wanted", {"i": 1}).event
        bus.emit(session, "noise", {"i": 2})
        second = bus.emit(session, "wanted", {"i": 3}).event
        bus.emit(session, "noise", {"i": 4})
        third = bus.emit(session, "wanted", {"i": 5}).event
        bus.emit(session, "noise", {"i": 6})
        session.commit()

    with session_factory() as session:
        batch = bus.poll(
            session,
            loop_name="full-page",
            event_types=("wanted",),
            limit=2,
        )
        max_event_id = session.scalar(select(Event.id).order_by(Event.id.desc()).limit(1))

    assert [event.id for event in batch.events] == [first.id, second.id]
    assert batch.checkpoint_id == second.id
    assert batch.checkpoint_id != max_event_id

    with session_factory() as session:
        bus.ack(session, batch)
        session.commit()

    with session_factory() as session:
        next_batch = bus.poll(
            session,
            loop_name="full-page",
            event_types=("wanted",),
            limit=2,
        )

    assert [event.id for event in next_batch.events] == [third.id]


def test_poll_checkpoint_horizon_does_not_skip_wanted_inserted_during_poll(session_factory):
    with session_factory() as session:
        noise = [bus.emit(session, "noise", {"i": index}).event for index in range(3)]
        session.commit()

    inserted_ids: list[int] = []

    def insert_after_wanted_select(conn, _cursor, statement, _parameters, _context, _executemany):
        if inserted_ids or "event_type IN" not in statement:
            return
        result = conn.execute(
            insert(Event).values(
                event_type="wanted",
                payload_json={"race": "between-select-and-checkpoint"},
                correlation_id=str(uuid4()),
            )
        )
        inserted_id = result.inserted_primary_key[0]
        assert isinstance(inserted_id, int)
        inserted_ids.append(inserted_id)

    with session_factory() as session:
        bind = session.get_bind()
        event.listen(bind, "after_cursor_execute", insert_after_wanted_select)
        try:
            batch = bus.poll(
                session,
                loop_name="interleaved",
                event_types=("wanted",),
                limit=5,
            )
            bus.ack(session, batch)
            session.commit()
        finally:
            event.remove(bind, "after_cursor_execute", insert_after_wanted_select)

    assert batch.events == []
    assert batch.checkpoint_id == noise[-1].id
    assert len(inserted_ids) == 1

    with session_factory() as session:
        next_batch = bus.poll(
            session,
            loop_name="interleaved",
            event_types=("wanted",),
            limit=5,
        )

    assert [event.id for event in next_batch.events] == inserted_ids
