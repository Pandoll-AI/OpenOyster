from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..events import bus
from ..models import Chunk, Hypothesis, SourceItem, SystemState, Task
from ..policies import get_active_policy
from ..utils import ensure_utc
from .base import BaseLoop, LoopResult


class MaintenanceLoop(BaseLoop):
    """Creates internal heartbeats and schedules retries, optimisation, and premise review."""

    name = "maintenance"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @staticmethod
    def _due(session: Session, key: str, interval: timedelta) -> bool:
        state = session.get(SystemState, key)
        if state is None:
            return True
        raw = state.value_json.get("at")
        if not raw:
            return True
        try:
            return datetime.now(UTC) - ensure_utc(datetime.fromisoformat(raw)) >= interval
        except ValueError:
            return True

    @staticmethod
    def _mark(session: Session, key: str) -> None:
        now = datetime.now(UTC).isoformat()
        state = session.get(SystemState, key)
        if state is None:
            session.add(SystemState(key=key, value_json={"at": now}))
        else:
            state.value_json = {"at": now}

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        result = LoopResult(loop_name=self.name)
        policy = get_active_policy(session).policy_json
        now = datetime.now(UTC)
        heartbeat_minutes = int(policy["maintenance"]["heartbeat_interval_minutes"])
        bucket = int(now.timestamp() // max(heartbeat_minutes * 60, 60))
        heartbeat = bus.emit(
            session,
            "system.heartbeat",
            {"at": now.isoformat(), "bucket": bucket},
            source_loop=self.name,
            idempotency_key=f"system.heartbeat:{bucket}",
        )
        result.emitted_events += int(heartbeat.created)

        optimisation_hours = float(policy["optimisation"]["review_interval_hours"])
        if self._due(session, "schedule.optimisation", timedelta(hours=optimisation_hours)):
            emission = bus.emit(
                session,
                "optimisation.review_requested",
                {"reason": "scheduled", "at": now.isoformat()},
                source_loop=self.name,
                idempotency_key=f"optimisation.review:{now.date().isoformat()}:{now.hour // max(int(optimisation_hours), 1)}",
            )
            result.emitted_events += int(emission.created)
            self._mark(session, "schedule.optimisation")

        cadence_days = float(policy["meta_review"]["cadence_days"])
        if self._due(session, "schedule.premise", timedelta(days=cadence_days)):
            emission = bus.emit(
                session,
                "premise.review_requested",
                {"reason": "scheduled", "at": now.isoformat()},
                source_loop=self.name,
                idempotency_key=f"premise.review:{now.date().isoformat()}",
            )
            result.emitted_events += int(emission.created)
            self._mark(session, "schedule.premise")

        stale_scan_hours = float(policy["maintenance"]["stale_hypothesis_scan_hours"])
        if self._due(session, "schedule.staleness", timedelta(hours=stale_scan_hours)):
            stale_cutoff = now - timedelta(days=int(policy["hypothesis"]["stale_days"]))
            stale = list(
                session.scalars(
                    select(Hypothesis)
                    .where(
                        Hypothesis.status == "active",
                        Hypothesis.updated_at < stale_cutoff,
                    )
                    .limit(limit)
                )
            )
            for hypothesis in stale:
                emission = bus.emit(
                    session,
                    "hypothesis.stale",
                    {"hypothesis_id": hypothesis.id, "revision": hypothesis.revision},
                    source_loop=self.name,
                    idempotency_key=f"hypothesis.stale:{hypothesis.id}:{hypothesis.revision}",
                )
                result.emitted_events += int(emission.created)
            result.created_records["stale_hypotheses"] = len(stale)
            self._mark(session, "schedule.staleness")

        retry_limit = int(policy["planning"]["task_retry_limit"])
        failed_tasks = list(
            session.scalars(
                select(Task)
                .where(
                    Task.status == "failed",
                    Task.attempts < retry_limit,
                    Task.available_at <= now,
                )
                .order_by(Task.priority.desc())
                .limit(limit)
            )
        )
        for task in failed_tasks:
            task.status = "pending"
            emission = bus.emit(
                session,
                "task.retry_requested",
                {"task_id": task.id, "attempt": task.attempts + 1},
                source_loop=self.name,
                idempotency_key=f"task.retry:{task.id}:{task.attempts + 1}",
            )
            result.emitted_events += int(emission.created)
        result.created_records["task_retries"] = len(failed_tasks)

        failed_chunks = list(
            session.scalars(
                select(Chunk)
                .where(
                    Chunk.status == "failed",
                    Chunk.attempts < int(policy["maintenance"]["max_document_failures"]),
                )
                .limit(limit)
            )
        )
        for chunk in failed_chunks:
            chunk.status = "pending"
            emission = bus.emit(
                session,
                "chunk.retry_requested",
                {"chunk_id": chunk.id, "document_id": chunk.document_id},
                source_loop=self.name,
                idempotency_key=f"chunk.retry:{chunk.id}:{chunk.attempts + 1}",
            )
            result.emitted_events += int(emission.created)
        result.created_records["chunk_retries"] = len(failed_chunks)

        # Archiving occurs here, after the intake transaction has committed.
        archive_items = list(
            session.scalars(select(SourceItem).where(SourceItem.status == "ingested").limit(limit))
        )
        archived_count = 0
        for item in archive_items:
            metadata = dict(item.metadata_json or {})
            if not metadata.get("archive_requested") or metadata.get("archived_to"):
                continue
            source_path = Path(str(metadata.get("archive_source_path", "")))
            if not source_path.exists():
                metadata["archive_error"] = "source file no longer exists"
                item.metadata_json = metadata
                continue
            self.settings.ensure_workspace()
            assert self.settings.archive_dir is not None
            target = self.settings.archive_dir / source_path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target = target.with_name(f"{target.stem}-{int(datetime.now().timestamp())}{target.suffix}")
            shutil.move(str(source_path), str(target))
            metadata["archived_to"] = str(target)
            metadata["archive_requested"] = False
            metadata.pop("archive_error", None)
            item.metadata_json = metadata
            archived_count += 1
        result.created_records["archived_files"] = archived_count
        return result
