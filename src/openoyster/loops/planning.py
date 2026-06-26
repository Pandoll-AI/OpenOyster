from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..events import EventBatch, bus
from ..models import EvidenceEdge, Hypothesis, Task
from ..policies import get_active_policy
from .base import BaseLoop, LoopResult


class PlanningLoop(BaseLoop):
    """Converts triggers into revision-scoped, bounded, idempotent tasks."""

    name = "planning"
    consumes = ("trigger.fired", "hypothesis.stale")

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @staticmethod
    def _task_specs(
        session: Session,
        hypothesis: Hypothesis,
    ) -> list[tuple[str, str, str]]:
        edges = list(session.scalars(select(EvidenceEdge).where(EvidenceEdge.hypothesis_id == hypothesis.id)))
        support_count = sum(edge.stance == "support" for edge in edges)
        oppose_count = sum(edge.stance == "oppose" for edge in edges)
        source_diversity = len({edge.document_id for edge in edges if edge.document_id is not None})
        specs: list[tuple[str, str, str]] = []
        if oppose_count == 0:
            specs.append(
                (
                    "counter_evidence_scan",
                    "Search for counter-evidence",
                    "Search the indexed corpus for evidence that could falsify or weaken the hypothesis.",
                )
            )
        if support_count < 2:
            specs.append(
                (
                    "support_evidence_scan",
                    "Search for independent support",
                    "Search the indexed corpus for additional support from another document or context.",
                )
            )
        specs.append(
            (
                "hypothesis_brief",
                "Render a traceable hypothesis brief",
                "Compile support, counter-evidence, uncertainty, and open falsification questions.",
            )
        )
        if source_diversity <= 1:
            specs.append(
                (
                    "baseline_compare",
                    "Compare against corpus baseline",
                    "Check whether the source universe is too narrow to assess this hypothesis reliably.",
                )
            )
        return specs

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        policy_record = get_active_policy(session)
        config = policy_record.policy_json["planning"]
        max_tasks = int(config["max_tasks_per_cycle"])
        max_per_trigger = int(config["max_tasks_per_trigger"])
        batch = bus.poll(
            session,
            loop_name=self.name,
            event_types=self.consumes,
            limit=min(limit, max_tasks),
            scan_multiplier=self.settings.event_scan_multiplier,
        )
        result = LoopResult(loop_name=self.name)
        processed_events = []

        for event in batch.events:
            if result.created_records.get("tasks", 0) >= max_tasks:
                break
            hypothesis_id = event.payload_json.get("hypothesis_id")
            hypothesis = session.get(Hypothesis, hypothesis_id) if hypothesis_id else None
            if not hypothesis:
                processed_events.append(event)
                continue
            remaining = max_tasks - result.created_records.get("tasks", 0)
            specs = self._task_specs(session, hypothesis)[: min(max_per_trigger, remaining)]
            for task_type, title, description in specs:
                idempotency_key = f"{task_type}:{hypothesis.id}:r{hypothesis.revision}"
                existing = session.scalar(select(Task).where(Task.idempotency_key == idempotency_key))
                if existing:
                    continue
                task = Task(
                    idempotency_key=idempotency_key,
                    trigger_event_id=event.id,
                    hypothesis_id=hypothesis.id,
                    task_type=task_type,
                    title=f"{title} — hypothesis #{hypothesis.id}",
                    description=description,
                    priority=float(event.payload_json.get("score", 0.5)),
                    status="pending",
                    max_cost=float(policy_record.policy_json["execution"]["daily_cost_limit"]),
                    max_depth=int(config["max_depth"]),
                    tool_budget=int(policy_record.policy_json["execution"]["max_tool_calls_per_task"]),
                    metadata_json={
                        "hypothesis_revision": hypothesis.revision,
                        "policy_version": policy_record.version,
                        "trigger_payload": event.payload_json,
                    },
                )
                session.add(task)
                session.flush()
                emission = bus.emit(
                    session,
                    "task.created",
                    {
                        "task_id": task.id,
                        "hypothesis_id": hypothesis.id,
                        "task_type": task.task_type,
                    },
                    source_loop=self.name,
                    parent_event_id=event.id,
                    idempotency_key=f"task.created:{task.id}",
                )
                result.emitted_events += int(emission.created)
                result.inc("tasks")
            processed_events.append(event)

        result.consumed_events = len(processed_events)
        if processed_events:
            partial = EventBatch(
                loop_name=self.name,
                events=processed_events,
                checkpoint_id=processed_events[-1].id,
                scanned_count=len(processed_events),
            )
            bus.ack(session, partial)
        elif not batch.events:
            bus.ack(session, batch)
        return result
