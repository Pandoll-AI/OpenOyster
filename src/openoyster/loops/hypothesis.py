from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..events import bus
from ..llm import LLMProvider, provider_from_settings
from ..models import DecisionTrace, Hypothesis, Signal
from ..policies import get_active_policy
from ..schemas import HypothesisDraft
from ..scoring import (
    contradiction_score,
    evidence_gap_score,
    evidence_source_diversity,
    recompute_confidence,
    staleness_score,
    weighted_trigger_score,
)
from ..services.hypothesis_evidence import add_evidence
from ..services.hypothesis_merge import match_hypothesis, record_merge_decision
from ..utils import normalise_text, stable_hash
from .base import BaseLoop, LoopResult


class HypothesisLoop(BaseLoop):
    """Merges hypothesis candidates, updates evidence posture, and creates internal triggers."""

    name = "hypothesis"
    consumes = (
        "hypothesis.candidate_created",
        "evidence.added",
        "hypothesis.stale",
    )

    def __init__(self, settings: Settings | None = None, provider: LLMProvider | None = None):
        self.settings = settings or get_settings()
        self.provider = provider or provider_from_settings(self.settings)

    @staticmethod
    def _features(hypothesis: Hypothesis, policy: dict, signal: Signal | None) -> dict[str, float | int]:
        edges = hypothesis.evidence_edges
        support = [edge for edge in edges if edge.stance == "support"]
        oppose = [edge for edge in edges if edge.stance == "oppose"]
        source_diversity = evidence_source_diversity(edges)
        support_strength = sum(edge.strength for edge in support)
        oppose_strength = sum(edge.strength for edge in oppose)
        return {
            "novelty": signal.novelty_score if signal else 0.35,
            "impact": signal.impact_score if signal else 0.45,
            "contradiction": contradiction_score(oppose_strength, support_strength),
            "evidence_gap": evidence_gap_score(
                len(support),
                len(oppose),
                source_diversity,
            ),
            "staleness": staleness_score(
                hypothesis.updated_at,
                int(policy["hypothesis"]["stale_days"]),
            ),
            "support_count": len(support),
            "oppose_count": len(oppose),
            "source_diversity": source_diversity,
        }

    def _evaluate_and_emit(
        self,
        session: Session,
        *,
        hypothesis: Hypothesis,
        signal: Signal | None,
        event_id: int,
        policy_record,
        result: LoopResult,
    ) -> None:
        policy = policy_record.policy_json
        session.refresh(hypothesis, attribute_names=["evidence_edges"])
        hypothesis.confidence = recompute_confidence(hypothesis, policy)
        hypothesis.last_reviewed_at = datetime.now(UTC)
        features = self._features(hypothesis, policy, signal)
        score = weighted_trigger_score(
            novelty=float(features["novelty"]),
            impact=float(features["impact"]),
            contradiction=float(features["contradiction"]),
            evidence_gap=float(features["evidence_gap"]),
            staleness=float(features["staleness"]),
            policy=policy,
        )
        threshold = float(policy["trigger"]["fire_threshold"])
        decision = score >= threshold
        session.add(
            DecisionTrace(
                decision_type="trigger_decision",
                subject_type="hypothesis",
                subject_id=hypothesis.id,
                policy_version=policy_record.version,
                features_json={**features, "revision": hypothesis.revision},
                score=score,
                threshold=threshold,
                decision=decision,
                metadata_json={"event_id": event_id},
            )
        )
        update = bus.emit(
            session,
            "hypothesis.updated",
            {
                "hypothesis_id": hypothesis.id,
                "revision": hypothesis.revision,
                "confidence": hypothesis.confidence,
                "features": features,
                "trigger_score": score,
            },
            source_loop=self.name,
            parent_event_id=event_id,
            idempotency_key=f"hypothesis.updated:{hypothesis.id}:{hypothesis.revision}",
        )
        result.emitted_events += int(update.created)
        if decision:
            trigger = bus.emit(
                session,
                "trigger.fired",
                {
                    "hypothesis_id": hypothesis.id,
                    "revision": hypothesis.revision,
                    "score": score,
                    "features": features,
                    "policy_version": policy_record.version,
                },
                source_loop=self.name,
                parent_event_id=event_id,
                idempotency_key=(
                    f"trigger.fired:{hypothesis.id}:{hypothesis.revision}:{policy_record.version}"
                ),
            )
            result.emitted_events += int(trigger.created)
        high_threshold = float(policy["trigger"]["high_alert_threshold"])
        if score >= high_threshold:
            alert = bus.emit(
                session,
                "alert.candidate_created",
                {
                    "hypothesis_id": hypothesis.id,
                    "revision": hypothesis.revision,
                    "score": score,
                    "requires_approval": True,
                },
                source_loop=self.name,
                parent_event_id=event_id,
                idempotency_key=f"alert.candidate:{hypothesis.id}:{hypothesis.revision}",
            )
            result.emitted_events += int(alert.created)

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
        merge_candidate_top_k = int(policy_record.policy_json["hypothesis"].get("merge_candidate_top_k", 5))

        for event in batch.events:
            signal: Signal | None = None
            if event.event_type == "hypothesis.candidate_created":
                raw = event.payload_json.get("hypothesis")
                if not isinstance(raw, dict):
                    result.notes.append(f"event {event.id}: missing hypothesis payload")
                    continue
                draft = HypothesisDraft.model_validate(raw)
                merge_decision = match_hypothesis(
                    session,
                    draft,
                    merge_candidate_top_k,
                    self.provider,
                )
                hypothesis = merge_decision.hypothesis
                created = hypothesis is None
                if created:
                    metadata: dict[str, Any] = {
                        "origin": "extraction",
                        "merge_relation": merge_decision.relation,
                        "merge_candidate_ids": merge_decision.candidate_ids,
                    }
                    if merge_decision.judge_unavailable:
                        metadata["merge_judge_unavailable"] = True
                    hypothesis = Hypothesis(
                        claim=draft.claim,
                        claim_hash=stable_hash(normalise_text(draft.claim).casefold()),
                        scope=draft.scope,
                        confidence=draft.confidence,
                        status="active",
                        revision=1,
                        metadata_json=metadata,
                    )
                    session.add(hypothesis)
                    session.flush()
                    result.inc("hypotheses")
                assert hypothesis is not None
                record_merge_decision(
                    session,
                    decision=merge_decision,
                    subject_id=hypothesis.id,
                    policy_version=policy_record.version,
                    event_id=event.id,
                )
                signal_id = event.payload_json.get("signal_id")
                signal = session.get(Signal, signal_id) if signal_id else None
                evidence_created = add_evidence(
                    session,
                    hypothesis=hypothesis,
                    signal=signal,
                    document_id=event.payload_json.get("document_id"),
                    chunk_id=event.payload_json.get("chunk_id"),
                    draft=draft,
                )
                if evidence_created:
                    result.inc("evidence")
                    if not created:
                        hypothesis.revision += 1
                if not created and not evidence_created:
                    continue
            else:
                hypothesis_id = event.payload_json.get("hypothesis_id")
                hypothesis = session.get(Hypothesis, hypothesis_id) if hypothesis_id else None
                if not hypothesis:
                    continue
                if event.event_type == "evidence.added":
                    hypothesis.revision += 1
                elif event.event_type == "hypothesis.stale":
                    hypothesis.metadata_json = {
                        **hypothesis.metadata_json,
                        "stale_review_requested_at": datetime.now(UTC).isoformat(),
                    }

            assert hypothesis is not None
            self._evaluate_and_emit(
                session,
                hypothesis=hypothesis,
                signal=signal,
                event_id=event.id,
                policy_record=policy_record,
                result=result,
            )
        bus.ack(session, batch)
        return result
