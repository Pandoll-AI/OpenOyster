from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..events import bus
from ..models import (
    Artifact,
    ArtifactFeedback,
    DecisionTrace,
    Evaluation,
    EvidenceEdge,
    Run,
    Task,
)
from ..policies import get_active_policy
from ..scoring import clamp
from .base import BaseLoop, LoopResult


class EvaluationLoop(BaseLoop):
    """Evaluates evidence posture and downstream human value, not prose length."""

    name = "evaluation"
    consumes = ("task.completed", "artifact.created", "artifact.feedback.recorded")

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @staticmethod
    def _upsert(
        session: Session,
        *,
        target_type: str,
        target_id: int,
        metric_name: str,
        evaluator_type: str,
        score: float,
        comment: str,
        metadata: dict,
    ) -> Evaluation:
        evaluation = session.scalar(
            select(Evaluation).where(
                Evaluation.target_type == target_type,
                Evaluation.target_id == target_id,
                Evaluation.metric_name == metric_name,
                Evaluation.evaluator_type == evaluator_type,
            )
        )
        if evaluation is None:
            evaluation = Evaluation(
                target_type=target_type,
                target_id=target_id,
                metric_name=metric_name,
                evaluator_type=evaluator_type,
                score=score,
                comment=comment,
                metadata_json=metadata,
            )
            session.add(evaluation)
        else:
            evaluation.score = score
            evaluation.comment = comment
            evaluation.metadata_json = metadata
        session.flush()
        return evaluation

    @staticmethod
    def _artifact_quality(session: Session, artifact: Artifact) -> tuple[float, dict]:
        edges: list[EvidenceEdge] = []
        if artifact.linked_hypothesis_id:
            edges = list(
                session.scalars(
                    select(EvidenceEdge).where(EvidenceEdge.hypothesis_id == artifact.linked_hypothesis_id)
                )
            )
        support = sum(edge.stance == "support" for edge in edges)
        oppose = sum(edge.stance == "oppose" for edge in edges)
        source_diversity = len({edge.document_id for edge in edges if edge.document_id is not None})
        evidence_coverage = min(len(edges) / 3, 1.0)
        diversity_score = min(source_diversity / 2, 1.0)
        counter_score = 1.0 if oppose else (0.25 if edges else 0.0)
        traceability = (
            1.0 if "[evidence:" in artifact.content or artifact.metadata_json.get("evidence_count") else 0.55
        )
        uncertainty = (
            1.0
            if any(
                token in artifact.content.casefold()
                for token in ("confidence", "uncertainty", "open question", "falsif", "불확실")
            )
            else 0.35
        )
        score = clamp(
            0.30 * evidence_coverage
            + 0.25 * diversity_score
            + 0.20 * counter_score
            + 0.15 * traceability
            + 0.10 * uncertainty
        )
        return score, {
            "evidence_count": len(edges),
            "support_count": support,
            "oppose_count": oppose,
            "source_diversity": source_diversity,
            "evidence_coverage": evidence_coverage,
            "diversity_score": diversity_score,
            "counter_score": counter_score,
            "traceability": traceability,
            "uncertainty": uncertainty,
        }

    def _emit_evaluation(
        self,
        session: Session,
        *,
        evaluation: Evaluation,
        parent_event_id: int,
        result: LoopResult,
    ) -> None:
        emission = bus.emit(
            session,
            "evaluation.completed",
            {
                "evaluation_id": evaluation.id,
                "target_type": evaluation.target_type,
                "target_id": evaluation.target_id,
                "metric_name": evaluation.metric_name,
                "evaluator_type": evaluation.evaluator_type,
                "score": evaluation.score,
            },
            source_loop=self.name,
            parent_event_id=parent_event_id,
            idempotency_key=(
                f"evaluation.completed:{evaluation.target_type}:{evaluation.target_id}:"
                f"{evaluation.metric_name}:{evaluation.evaluator_type}:{evaluation.updated_at.isoformat()}"
            ),
        )
        result.emitted_events += int(emission.created)

    def _evaluate_feedback(
        self,
        session: Session,
        *,
        artifact: Artifact,
        event_id: int,
        result: LoopResult,
        policy: dict,
    ) -> None:
        feedback = list(
            session.scalars(select(ArtifactFeedback).where(ArtifactFeedback.artifact_id == artifact.id))
        )
        if not feedback:
            return
        positive = set(policy["evaluation"]["feedback_positive_verdicts"])
        negative = set(policy["evaluation"]["feedback_negative_verdicts"])
        values = []
        for item in feedback:
            if item.score is not None:
                values.append(item.score)
            elif item.verdict in positive:
                values.append(1.0)
            elif item.verdict in negative:
                values.append(0.0)
            else:
                values.append(0.5)
        score = sum(values) / len(values)
        counts = Counter(item.verdict for item in feedback)
        evaluation = self._upsert(
            session,
            target_type="artifact",
            target_id=artifact.id,
            metric_name="human_value",
            evaluator_type="human",
            score=score,
            comment=f"Aggregated {len(feedback)} explicit feedback item(s).",
            metadata={"verdict_counts": dict(counts), "feedback_ids": [item.id for item in feedback]},
        )
        artifact.status = "accepted" if score >= 0.6 else "rejected"

        if artifact.linked_hypothesis_id:
            revision = int(artifact.metadata_json.get("hypothesis_revision", artifact.version))
            traces = list(
                session.scalars(
                    select(DecisionTrace).where(
                        DecisionTrace.subject_type == "hypothesis",
                        DecisionTrace.subject_id == artifact.linked_hypothesis_id,
                    )
                )
            )
            for trace in traces:
                if int(trace.features_json.get("revision", revision)) == revision:
                    trace.outcome_score = score
                    trace.metadata_json = {
                        **trace.metadata_json,
                        "label_source": "artifact_feedback",
                        "artifact_id": artifact.id,
                    }
        self._emit_evaluation(
            session,
            evaluation=evaluation,
            parent_event_id=event_id,
            result=result,
        )
        result.inc("human_evaluations")

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        batch = bus.poll(
            session,
            loop_name=self.name,
            event_types=self.consumes,
            limit=limit,
            scan_multiplier=self.settings.event_scan_multiplier,
        )
        result = LoopResult(loop_name=self.name, consumed_events=len(batch.events))
        policy = get_active_policy(session).policy_json

        for event in batch.events:
            if event.event_type == "artifact.created":
                artifact_id = event.payload_json.get("artifact_id")
                artifact = session.get(Artifact, artifact_id) if artifact_id else None
                if not artifact:
                    continue
                score, metadata = self._artifact_quality(session, artifact)
                evaluation = self._upsert(
                    session,
                    target_type="artifact",
                    target_id=artifact.id,
                    metric_name="evidence_quality",
                    evaluator_type="rule",
                    score=score,
                    comment="Evidence posture, diversity, counter-evidence, and traceability score.",
                    metadata=metadata,
                )
                self._emit_evaluation(
                    session,
                    evaluation=evaluation,
                    parent_event_id=event.id,
                    result=result,
                )
                result.inc("rule_evaluations")

            elif event.event_type == "task.completed":
                task_id = event.payload_json.get("task_id")
                task = session.get(Task, task_id) if task_id else None
                if not task:
                    continue
                successful_run = session.scalar(
                    select(Run).where(Run.task_id == task.id, Run.success.is_(True)).limit(1)
                )
                artifact = session.scalar(select(Artifact).where(Artifact.linked_task_id == task.id).limit(1))
                score = (
                    1.0
                    if task.status == "completed" and successful_run and artifact
                    else 0.5
                    if task.status == "completed" and successful_run
                    else 0.0
                )
                evaluation = self._upsert(
                    session,
                    target_type="task",
                    target_id=task.id,
                    metric_name="verified_completion",
                    evaluator_type="rule",
                    score=score,
                    comment="Completion requires a successful run and a persisted output artifact.",
                    metadata={
                        "has_successful_run": bool(successful_run),
                        "has_artifact": bool(artifact),
                    },
                )
                self._emit_evaluation(
                    session,
                    evaluation=evaluation,
                    parent_event_id=event.id,
                    result=result,
                )
                result.inc("rule_evaluations")

            else:
                artifact_id = event.payload_json.get("artifact_id")
                artifact = session.get(Artifact, artifact_id) if artifact_id else None
                if artifact:
                    self._evaluate_feedback(
                        session,
                        artifact=artifact,
                        event_id=event.id,
                        result=result,
                        policy=policy,
                    )

        bus.ack(session, batch)
        return result
