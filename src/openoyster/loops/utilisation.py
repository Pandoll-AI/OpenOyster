from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..events import bus
from ..models import Artifact, DecisionTrace, EvidenceEdge, Hypothesis
from ..policies import get_active_policy
from ..services.artifacts import render_decision_memo
from ..utils import stable_hash
from .base import BaseLoop, LoopResult


class UtilisationLoop(BaseLoop):
    """Turns sufficiently grounded hypotheses into revisioned decision artifacts."""

    name = "utilisation"
    consumes = ("hypothesis.updated",)

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        batch = bus.poll(
            session,
            loop_name=self.name,
            event_types=self.consumes,
            limit=limit,
            scan_multiplier=self.settings.event_scan_multiplier,
        )
        result = LoopResult(loop_name=self.name, consumed_events=len(batch.events))
        policy_record = get_active_policy(session)
        config = policy_record.policy_json["utilisation"]

        for event in batch.events:
            hypothesis_id = event.payload_json.get("hypothesis_id")
            hypothesis = session.get(Hypothesis, hypothesis_id) if hypothesis_id else None
            if not hypothesis:
                continue
            edges = list(
                session.scalars(select(EvidenceEdge).where(EvidenceEdge.hypothesis_id == hypothesis.id))
            )
            source_diversity = len({edge.document_id for edge in edges if edge.document_id is not None})
            features = {
                "confidence": hypothesis.confidence,
                "evidence_count": len(edges),
                "source_diversity": source_diversity,
                "oppose_count": sum(edge.stance == "oppose" for edge in edges),
                "revision": hypothesis.revision,
            }
            threshold = float(config["report_candidate_threshold"])
            decision = (
                hypothesis.confidence >= threshold
                and len(edges) >= int(config["minimum_evidence_count"])
                and source_diversity >= int(config["minimum_source_diversity"])
            )
            session.add(
                DecisionTrace(
                    decision_type="utilisation_decision",
                    subject_type="hypothesis",
                    subject_id=hypothesis.id,
                    policy_version=policy_record.version,
                    features_json=features,
                    score=hypothesis.confidence,
                    threshold=threshold,
                    decision=decision,
                    metadata_json={"event_id": event.id},
                )
            )
            if not decision:
                if features["oppose_count"] > 0:
                    hypothesis.status = "challenged"
                continue

            existing = session.scalar(
                select(Artifact).where(
                    Artifact.linked_hypothesis_id == hypothesis.id,
                    Artifact.artifact_type == "decision_memo",
                    Artifact.version == hypothesis.revision,
                )
            )
            if existing:
                continue
            content = render_decision_memo(hypothesis, edges)
            artifact = Artifact(
                artifact_type="decision_memo",
                title=f"Decision memo — hypothesis #{hypothesis.id}, revision {hypothesis.revision}",
                content=content,
                content_hash=stable_hash(content),
                version=hypothesis.revision,
                status="candidate",
                linked_hypothesis_id=hypothesis.id,
                linked_task_id=None,
                metadata_json={
                    "generated_by": self.name,
                    "policy_version": policy_record.version,
                    "hypothesis_revision": hypothesis.revision,
                    **features,
                },
            )
            session.add(artifact)
            hypothesis.status = "mature"
            session.flush()
            emission = bus.emit(
                session,
                "artifact.created",
                {
                    "artifact_id": artifact.id,
                    "artifact_type": artifact.artifact_type,
                    "hypothesis_id": hypothesis.id,
                    "revision": hypothesis.revision,
                },
                source_loop=self.name,
                parent_event_id=event.id,
                idempotency_key=f"artifact.created:{artifact.id}",
            )
            result.emitted_events += int(emission.created)
            result.inc("decision_memos")

        bus.ack(session, batch)
        return result
