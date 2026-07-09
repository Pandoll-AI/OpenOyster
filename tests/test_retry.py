from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from openoyster.events import bus
from openoyster.loops.execution import ExecutionLoop
from openoyster.loops.maintenance import MaintenanceLoop
from openoyster.models import Hypothesis, Run, Task
from openoyster.policies import ensure_default_policy
from openoyster.utils import stable_hash


def test_failed_task_is_retryable(temp_settings, session_factory):
    with session_factory() as session:
        ensure_default_policy(session, temp_settings)
        hypothesis = Hypothesis(
            claim="A falsifiable test hypothesis.",
            claim_hash=stable_hash("retry-h"),
            scope="test",
            confidence=0.5,
        )
        session.add(hypothesis)
        session.flush()
        task = Task(
            idempotency_key="unknown-tool:1",
            hypothesis_id=hypothesis.id,
            task_type="unknown_tool",
            title="Fail safely",
            description="Exercise retry state",
            status="pending",
        )
        session.add(task)
        session.flush()
        bus.emit(session, "task.created", {"task_id": task.id})
        session.commit()
        task_id = task.id

    with session_factory() as session:
        ExecutionLoop(temp_settings).run(session)
        session.commit()
    with session_factory() as session:
        task = session.get(Task, task_id)
        assert task.status == "failed"
        assert session.scalar(select(func.count(Run.id)).where(Run.success.is_(False))) == 1
        task.available_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with session_factory() as session:
        result = MaintenanceLoop(temp_settings).run(session)
        session.commit()
        task = session.get(Task, task_id)
    assert task.status == "pending"
    assert result.created_records["task_retries"] == 1
