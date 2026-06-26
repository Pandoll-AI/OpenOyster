from __future__ import annotations

from openoyster.events import EventBatch, bus


def test_idempotent_event_emission(session_factory):
    with session_factory() as session:
        first = bus.emit(session, "x", {"n": 1}, idempotency_key="same")
        second = bus.emit(session, "x", {"n": 2}, idempotency_key="same")
        session.commit()
    assert first.created is True
    assert second.created is False
    assert first.event.id == second.event.id


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
