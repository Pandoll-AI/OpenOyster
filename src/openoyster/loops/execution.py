from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..events import EventBatch, bus
from ..models import Artifact, EvidenceEdge, Hypothesis, Run, Task
from ..policies import get_active_policy
from ..services.artifacts import next_artifact_version
from ..services.tools import run_tool
from ..utils import ensure_utc, stable_hash
from .base import BaseLoop, LoopResult


class ExecutionLoop(BaseLoop):
    """Executes bounded internal tools; failures become retryable state, not lost work."""

    name = "execution"
    consumes = ("task.created", "task.retry_requested")

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @staticmethod
    def _daily_cost(session: Session) -> float:
        now = datetime.now(UTC)
        start = datetime(now.year, now.month, now.day, tzinfo=UTC)
        return float(
            session.scalar(select(func.coalesce(func.sum(Run.cost), 0.0)).where(Run.started_at >= start))
            or 0.0
        )

    def _execute_task(
        self,
        session: Session,
        *,
        task: Task,
        policy_record,
        parent_event_id: int,
        result: LoopResult,
    ) -> None:
        if task.status == "completed" or ensure_utc(task.available_at) > datetime.now(UTC):
            return
        hypothesis = session.get(Hypothesis, task.hypothesis_id) if task.hypothesis_id else None
        if not hypothesis:
            task.status = "failed"
            task.last_error = "Linked hypothesis no longer exists"
            return
        if self._daily_cost(session) >= float(policy_record.policy_json["execution"]["daily_cost_limit"]):
            result.notes.append("Daily execution cost limit reached; remaining tasks deferred.")
            return

        task.status = "running"
        task.attempts += 1
        task.started_at = datetime.now(UTC)
        run = Run(
            task_id=task.id,
            policy_version=policy_record.version,
            model_used=policy_record.policy_json["execution"]["default_model"],
            tools_used_json=[task.task_type],
            input_context_json={
                "hypothesis_id": hypothesis.id,
                "hypothesis_revision": hypothesis.revision,
            },
            output_summary="Execution started",
            success=False,
        )
        session.add(run)
        session.flush()

        try:
            tool_result = run_tool(
                session,
                task_type=task.task_type,
                hypothesis=hypothesis,
                policy=policy_record.policy_json,
            )
            version = next_artifact_version(
                session,
                artifact_type=tool_result.artifact_type,
                hypothesis_id=hypothesis.id,
            )
            artifact = Artifact(
                artifact_type=tool_result.artifact_type,
                title=tool_result.title,
                content=tool_result.content,
                content_hash=stable_hash(tool_result.content),
                version=version,
                status="draft",
                linked_hypothesis_id=hypothesis.id,
                linked_task_id=task.id,
                metadata_json={
                    **tool_result.metadata,
                    "generated_by": self.name,
                    "policy_version": policy_record.version,
                    "hypothesis_revision": hypothesis.revision,
                },
            )
            session.add(artifact)
            session.flush()
            result.inc("artifacts")

            added_evidence = 0
            for candidate in tool_result.evidence_candidates:
                evidence_hash = stable_hash(
                    hypothesis.id,
                    candidate.chunk_id,
                    candidate.stance,
                    "retrieval-tool-v2",
                )
                existing = session.scalar(
                    select(EvidenceEdge).where(
                        EvidenceEdge.hypothesis_id == hypothesis.id,
                        EvidenceEdge.evidence_hash == evidence_hash,
                    )
                )
                if existing:
                    continue
                session.add(
                    EvidenceEdge(
                        hypothesis_id=hypothesis.id,
                        signal_id=None,
                        document_id=candidate.document_id,
                        chunk_id=candidate.chunk_id,
                        evidence_hash=evidence_hash,
                        stance=candidate.stance,
                        strength=candidate.strength,
                        summary=candidate.summary,
                        provenance="retrieval-tool-v2",
                        metadata_json=candidate.metadata,
                    )
                )
                added_evidence += 1
            if added_evidence:
                evidence_event = bus.emit(
                    session,
                    "evidence.added",
                    {
                        "hypothesis_id": hypothesis.id,
                        "task_id": task.id,
                        "count": added_evidence,
                    },
                    source_loop=self.name,
                    parent_event_id=parent_event_id,
                    idempotency_key=f"evidence.added:task:{task.id}",
                )
                result.emitted_events += int(evidence_event.created)
                result.inc("evidence", added_evidence)

            now = datetime.now(UTC)
            run.output_summary = tool_result.summary
            run.cost = tool_result.cost
            run.input_tokens = tool_result.input_tokens
            run.output_tokens = tool_result.output_tokens
            run.success = True
            run.completed_at = now
            task.status = "completed"
            task.completed_at = now
            task.last_error = None
            session.flush()

            artifact_event = bus.emit(
                session,
                "artifact.created",
                {
                    "artifact_id": artifact.id,
                    "artifact_type": artifact.artifact_type,
                    "hypothesis_id": hypothesis.id,
                    "task_id": task.id,
                },
                source_loop=self.name,
                parent_event_id=parent_event_id,
                idempotency_key=f"artifact.created:{artifact.id}",
            )
            task_event = bus.emit(
                session,
                "task.completed",
                {
                    "task_id": task.id,
                    "run_id": run.id,
                    "artifact_id": artifact.id,
                    "hypothesis_id": hypothesis.id,
                },
                source_loop=self.name,
                parent_event_id=parent_event_id,
                idempotency_key=f"task.completed:{task.id}",
            )
            result.emitted_events += int(artifact_event.created) + int(task_event.created)
            result.inc("completed_tasks")
        except Exception as exc:
            now = datetime.now(UTC)
            run.output_summary = "Execution failed"
            run.success = False
            run.error = str(exc)
            run.completed_at = now
            task.status = "failed"
            task.last_error = str(exc)
            task.available_at = now + timedelta(seconds=min(30 * (2 ** (task.attempts - 1)), 3600))
            failure = bus.emit(
                session,
                "task.failed",
                {
                    "task_id": task.id,
                    "attempt": task.attempts,
                    "error": str(exc),
                },
                source_loop=self.name,
                parent_event_id=parent_event_id,
                idempotency_key=f"task.failed:{task.id}:{task.attempts}",
            )
            result.emitted_events += int(failure.created)
            result.inc("failed_tasks")

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        batch = bus.poll(
            session,
            loop_name=self.name,
            event_types=self.consumes,
            limit=limit,
            scan_multiplier=self.settings.event_scan_multiplier,
        )
        result = LoopResult(loop_name=self.name)
        policy_record = get_active_policy(session)
        max_tasks = int(policy_record.policy_json["planning"]["max_tasks_per_cycle"])
        processed_events = []

        for event in batch.events:
            if (
                result.created_records.get("completed_tasks", 0)
                + result.created_records.get("failed_tasks", 0)
                >= max_tasks
            ):
                break
            task_id = event.payload_json.get("task_id")
            task = session.get(Task, task_id) if task_id else None
            if task:
                self._execute_task(
                    session,
                    task=task,
                    policy_record=policy_record,
                    parent_event_id=event.id,
                    result=result,
                )
            processed_events.append(event)

        result.consumed_events = len(processed_events)
        if processed_events:
            bus.ack(
                session,
                EventBatch(
                    loop_name=self.name,
                    events=processed_events,
                    checkpoint_id=processed_events[-1].id,
                    scanned_count=len(processed_events),
                ),
            )
        elif not batch.events:
            bus.ack(session, batch)
        return result
