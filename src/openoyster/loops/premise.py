from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..events import bus
from ..models import (
    Artifact,
    ArtifactFeedback,
    Document,
    Hypothesis,
    LoopRun,
    Signal,
)
from ..policies import ensure_default_mission, get_active_policy
from ..scoring import clamp, concentration
from ..services.artifacts import next_artifact_version
from ..utils import ensure_utc, stable_hash
from .base import BaseLoop, LoopResult


class MetaPremiseReviewLoop(BaseLoop):
    """Audits the system's source universe, failure pattern, adoption, and scope drift."""

    name = "meta_premise"
    consumes = ("premise.review_requested", "policy.promoted")

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @staticmethod
    def _profile(session: Session, policy: dict) -> dict:
        now = datetime.now(UTC)
        since = now - timedelta(days=int(policy["meta_review"]["window_days"]))
        documents = list(session.scalars(select(Document).where(Document.fetched_at >= since)))
        signals = list(session.scalars(select(Signal).where(Signal.created_at >= since)))
        feedback = list(session.scalars(select(ArtifactFeedback).where(ArtifactFeedback.created_at >= since)))
        loop_runs = list(session.scalars(select(LoopRun).where(LoopRun.started_at >= since)))
        hypotheses = list(
            session.scalars(
                select(Hypothesis).where(Hypothesis.status.in_(["active", "challenged", "mature"]))
            )
        )
        positives = set(policy["evaluation"]["feedback_positive_verdicts"])
        adoption_rate = (
            sum(item.verdict in positives for item in feedback) / len(feedback) if feedback else None
        )
        failure_rate = sum(run.status == "failed" for run in loop_runs) / len(loop_runs) if loop_runs else 0.0
        stale_days = int(policy["meta_review"]["max_open_hypothesis_age_days"])
        stale_cutoff = now - timedelta(days=stale_days)
        stale_count = sum(ensure_utc(hypothesis.updated_at) < stale_cutoff for hypothesis in hypotheses)
        return {
            "window_days": int(policy["meta_review"]["window_days"]),
            "document_count": len(documents),
            "signal_count": len(signals),
            "feedback_count": len(feedback),
            "loop_run_count": len(loop_runs),
            "open_hypothesis_count": len(hypotheses),
            "stale_hypothesis_count": stale_count,
            "source_concentration": concentration([document.source for document in documents]),
            "signal_type_concentration": concentration([signal.signal_type for signal in signals]),
            "adoption_rate": adoption_rate,
            "loop_failure_rate": failure_rate,
            "document_failure_rate": (
                sum(document.status in {"failed", "partial_failure"} for document in documents)
                / len(documents)
                if documents
                else 0.0
            ),
            "sources": sorted({document.source for document in documents}),
            "signal_types": sorted({signal.signal_type for signal in signals}),
        }

    @staticmethod
    def _drift(profile: dict, policy: dict) -> tuple[float, list[str], dict[str, float]]:
        meta = policy["meta_review"]
        components: dict[str, float] = {}
        reasons: list[str] = []

        components["source_concentration"] = (
            profile["source_concentration"] if profile["document_count"] >= 3 else 0.0
        )
        if components["source_concentration"] > float(meta["source_concentration_threshold"]):
            reasons.append("The recent corpus is dominated by one source class.")

        components["signal_concentration"] = (
            profile["signal_type_concentration"] if profile["signal_count"] >= 3 else 0.0
        )
        if components["signal_concentration"] > float(meta["signal_type_concentration_threshold"]):
            reasons.append("The hypothesis portfolio is being driven by one signal type.")

        adoption = profile["adoption_rate"]
        components["low_adoption"] = (
            1 - adoption if adoption is not None and profile["feedback_count"] >= 3 else 0.0
        )
        if adoption is not None and adoption < float(meta["low_adoption_threshold"]):
            reasons.append("Human feedback indicates low downstream adoption.")

        components["loop_failure"] = profile["loop_failure_rate"]
        if profile["loop_failure_rate"] > float(meta["high_failure_rate_threshold"]):
            reasons.append("Loop failures are high enough to invalidate optimisation conclusions.")

        components["document_failure"] = profile["document_failure_rate"]
        if profile["document_count"] and profile["signal_count"] == 0:
            components["no_signal_output"] = 1.0
            reasons.append("Documents are arriving but no strategic signals are being produced.")
        else:
            components["no_signal_output"] = 0.0

        stale_ratio = (
            profile["stale_hypothesis_count"] / profile["open_hypothesis_count"]
            if profile["open_hypothesis_count"]
            else 0.0
        )
        components["stale_portfolio"] = stale_ratio
        if stale_ratio > 0.5:
            reasons.append("More than half of the open hypothesis portfolio is stale.")

        score = clamp(
            0.20 * components["source_concentration"]
            + 0.15 * components["signal_concentration"]
            + 0.20 * components["low_adoption"]
            + 0.15 * components["loop_failure"]
            + 0.10 * components["document_failure"]
            + 0.10 * components["no_signal_output"]
            + 0.10 * components["stale_portfolio"]
        )
        return score, reasons, components

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        batch = bus.poll(
            session,
            loop_name=self.name,
            event_types=self.consumes,
            limit=limit,
            scan_multiplier=self.settings.event_scan_multiplier,
        )
        result = LoopResult(loop_name=self.name, consumed_events=len(batch.events))
        if not batch.events:
            bus.ack(session, batch)
            return result

        policy_record = get_active_policy(session)
        mission = ensure_default_mission(session)
        for event in batch.events:
            profile = self._profile(session, policy_record.policy_json)
            score, reasons, components = self._drift(profile, policy_record.policy_json)
            threshold = float(policy_record.policy_json["meta_review"]["drift_threshold"])
            recommendations = (
                [
                    "Add or rebalance source classes before drawing stronger conclusions.",
                    "Audit ignored and rejected artifacts to determine whether the mission or extraction policy is wrong.",
                    "Treat any mission-charter change as a proposal requiring human approval.",
                ]
                if score >= threshold
                else ["Continue monitoring; no mission or scope change is justified by current evidence."]
            )
            version = next_artifact_version(
                session,
                artifact_type="premise_review",
                hypothesis_id=None,
            )
            content = f"""# Meta-premise review

## Mission under review
{mission.mission}

## Drift score
`{score:.3f}` against threshold `{threshold:.3f}`

## System behaviour profile
```json
{json.dumps(profile, ensure_ascii=False, indent=2, default=str)}
```

## Drift components
```json
{json.dumps(components, ensure_ascii=False, indent=2)}
```

## Reasons
{chr(10).join(f"- {reason}" for reason in reasons) if reasons else "- No material premise drift detected."}

## Recommendations
{chr(10).join(f"- {recommendation}" for recommendation in recommendations)}
"""
            artifact = Artifact(
                artifact_type="premise_review",
                title=f"Meta-premise review #{version}",
                content=content,
                content_hash=stable_hash(content),
                version=version,
                status="review" if score >= threshold else "monitoring",
                linked_hypothesis_id=None,
                linked_task_id=None,
                metadata_json={
                    "profile": profile,
                    "drift_score": score,
                    "drift_threshold": threshold,
                    "components": components,
                    "reasons": reasons,
                    "request_event_id": event.id,
                    "mission_version": mission.version,
                },
            )
            session.add(artifact)
            session.flush()
            created = bus.emit(
                session,
                "artifact.created",
                {
                    "artifact_id": artifact.id,
                    "artifact_type": artifact.artifact_type,
                    "drift_score": score,
                },
                source_loop=self.name,
                parent_event_id=event.id,
                idempotency_key=f"artifact.created:{artifact.id}",
            )
            completed = bus.emit(
                session,
                "premise.review.completed",
                {
                    "artifact_id": artifact.id,
                    "drift_score": score,
                    "threshold": threshold,
                },
                source_loop=self.name,
                parent_event_id=event.id,
                idempotency_key=f"premise.review.completed:{event.id}",
            )
            result.emitted_events += int(created.created) + int(completed.created)
            if score >= threshold:
                proposed = bus.emit(
                    session,
                    "premise.action_proposed",
                    {
                        "artifact_id": artifact.id,
                        "drift_score": score,
                        "reasons": reasons,
                        "requires_human_approval": True,
                    },
                    source_loop=self.name,
                    parent_event_id=event.id,
                    idempotency_key=f"premise.action_proposed:{event.id}",
                )
                result.emitted_events += int(proposed.created)
            result.inc("premise_reviews")

        bus.ack(session, batch)
        return result
