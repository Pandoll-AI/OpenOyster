from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..events import bus
from ..models import DecisionTrace, Experiment, Policy
from ..policies import (
    get_active_policy,
    get_nested,
    promote_policy,
    set_nested,
    validate_policy,
)
from ..scoring import binary_classification_metrics, clamp, weighted_trigger_score
from ..utils import ensure_utc, stable_hash
from .base import BaseLoop, LoopResult


class HyperparameterOptimisationLoop(BaseLoop):
    """Tunes policy parameters using labelled replay followed by a separate shadow window."""

    name = "optimisation"
    consumes = ("optimisation.review_requested", "artifact.feedback.recorded")

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @staticmethod
    def _labelled_traces(session: Session, window_days: int) -> list[DecisionTrace]:
        since = datetime.now(UTC) - timedelta(days=window_days)
        return list(
            session.scalars(
                select(DecisionTrace)
                .where(
                    DecisionTrace.decision_type == "trigger_decision",
                    DecisionTrace.outcome_score.is_not(None),
                    DecisionTrace.created_at >= since,
                )
                .order_by(DecisionTrace.id)
            )
        )

    @staticmethod
    def _predict(trace: DecisionTrace, policy: dict) -> bool:
        features = trace.features_json
        score = weighted_trigger_score(
            novelty=float(features.get("novelty", 0.0)),
            impact=float(features.get("impact", 0.0)),
            contradiction=float(features.get("contradiction", 0.0)),
            evidence_gap=float(features.get("evidence_gap", 0.0)),
            staleness=float(features.get("staleness", 0.0)),
            policy=policy,
        )
        return score >= float(policy["trigger"]["fire_threshold"])

    def _replay(self, traces: list[DecisionTrace], policy: dict) -> dict[str, float]:
        labels = [float(trace.outcome_score or 0.0) >= 0.6 for trace in traces]
        predictions = [self._predict(trace, policy) for trace in traces]
        return binary_classification_metrics(labels, predictions)

    @staticmethod
    def _mutations(base_policy: dict, step: float) -> list[tuple[dict, list[dict]]]:
        candidates: list[tuple[dict, list[dict]]] = []
        threshold_path = "trigger.fire_threshold"
        current_threshold = float(get_nested(base_policy, threshold_path))
        for delta in (-step, step):
            value = clamp(current_threshold + delta, 0.10, 0.90)
            candidates.append(
                (
                    set_nested(base_policy, threshold_path, value),
                    [
                        {
                            "path": threshold_path,
                            "old_value": current_threshold,
                            "new_value": value,
                            "reason": "Replay threshold search",
                        }
                    ],
                )
            )

        for key in ("impact_weight", "contradiction_weight", "evidence_gap_weight"):
            path = f"trigger.{key}"
            current = float(get_nested(base_policy, path))
            for delta in (-step, step):
                value = clamp(current + delta, 0.01, 0.80)
                candidates.append(
                    (
                        set_nested(base_policy, path, value),
                        [
                            {
                                "path": path,
                                "old_value": current,
                                "new_value": value,
                                "reason": "Labelled replay weight search",
                            }
                        ],
                    )
                )
        return candidates

    def _evaluate_shadow(
        self,
        session: Session,
        *,
        shadow: Policy,
        traces: list[DecisionTrace],
        result: LoopResult,
    ) -> bool:
        config = shadow.policy_json["optimisation"]
        baseline_ids = set(shadow.evaluation_json.get("baseline_label_ids", []))
        new_traces = [trace for trace in traces if trace.id not in baseline_ids]
        minimum = int(config["shadow_min_new_labels"])
        if len(new_traces) < minimum:
            result.notes.append(
                f"Shadow policy {shadow.version} awaits {minimum - len(new_traces)} more labelled trace(s)."
            )
            return True

        base = session.get(Policy, shadow.parent_policy_id) if shadow.parent_policy_id else None
        if base is None:
            base = get_active_policy(session)
        base_metrics = self._replay(new_traces, base.policy_json)
        shadow_metrics = self._replay(new_traces, shadow.policy_json)
        improvement = shadow_metrics["utility"] - base_metrics["utility"]
        shadow.evaluation_json = {
            **shadow.evaluation_json,
            "shadow_label_ids": [trace.id for trace in new_traces],
            "shadow_base_metrics": base_metrics,
            "shadow_candidate_metrics": shadow_metrics,
            "shadow_improvement": improvement,
            "shadow_evaluated_at": datetime.now(UTC).isoformat(),
        }
        experiment = session.scalar(
            select(Experiment)
            .where(Experiment.candidate_policy_id == shadow.id)
            .order_by(Experiment.id.desc())
        )
        if experiment:
            experiment.shadow_result_json = {
                "base": base_metrics,
                "candidate": shadow_metrics,
                "improvement": improvement,
                "label_ids": [trace.id for trace in new_traces],
            }

        safety_labels = int(shadow.policy_json["safety"]["minimum_human_labels_for_auto_promotion"])
        should_promote = (
            improvement >= float(config["min_improvement"])
            and len(traces) >= safety_labels
            and bool(config["allow_auto_promotion"])
        )
        if should_promote:
            promote_policy(session, shadow, score=shadow_metrics["utility"])
            if experiment:
                experiment.decision = "promoted_after_shadow"
                experiment.decided_at = datetime.now(UTC)
            emission = bus.emit(
                session,
                "policy.promoted",
                {
                    "policy_id": shadow.id,
                    "version": shadow.version,
                    "shadow_metrics": shadow_metrics,
                    "improvement": improvement,
                },
                source_loop=self.name,
                idempotency_key=f"policy.promoted:{shadow.id}",
            )
            result.emitted_events += int(emission.created)
            result.inc("promoted_policies")
        else:
            shadow.status = "archived"
            if experiment:
                experiment.decision = "rejected_after_shadow"
                experiment.decided_at = datetime.now(UTC)
            emission = bus.emit(
                session,
                "policy.shadow_rejected",
                {
                    "policy_id": shadow.id,
                    "version": shadow.version,
                    "shadow_metrics": shadow_metrics,
                    "improvement": improvement,
                },
                source_loop=self.name,
                idempotency_key=f"policy.shadow_rejected:{shadow.id}",
            )
            result.emitted_events += int(emission.created)
            result.inc("rejected_policies")
        return True

    def _start_shadow(
        self,
        session: Session,
        *,
        active: Policy,
        traces: list[DecisionTrace],
        result: LoopResult,
    ) -> None:
        config = active.policy_json["optimisation"]
        minimum = int(config["min_labelled_traces"])
        if len(traces) < minimum:
            result.notes.append(
                f"Optimisation skipped: {len(traces)}/{minimum} labelled trigger traces available."
            )
            return
        baseline = self._replay(traces, active.policy_json)
        step = min(
            float(config["mutation_step"]),
            float(active.policy_json["safety"]["max_policy_change_per_run"]),
        )
        scored: list[tuple[float, dict, list[dict], dict]] = []
        for candidate, mutations in self._mutations(active.policy_json, step):
            validate_policy(candidate)
            metrics = self._replay(traces, candidate)
            scored.append((metrics["utility"], candidate, mutations, metrics))
        best_score, best_policy, mutations, best_metrics = max(scored, key=lambda item: item[0])
        improvement = best_score - baseline["utility"]
        if improvement < float(config["min_improvement"]):
            result.notes.append(
                f"No replay candidate cleared min improvement: {improvement:.3f} < {config['min_improvement']:.3f}."
            )
            return

        version = f"shadow-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{stable_hash(mutations)[:8]}"
        shadow = Policy(
            version=version,
            parent_policy_id=active.id,
            policy_json=deepcopy(best_policy),
            status="shadow",
            score=best_score,
            evaluation_json={
                "mutations": mutations,
                "replay_base_metrics": baseline,
                "replay_candidate_metrics": best_metrics,
                "replay_improvement": improvement,
                "baseline_label_ids": [trace.id for trace in traces],
                "shadow_started_at": datetime.now(UTC).isoformat(),
            },
        )
        session.add(shadow)
        session.flush()
        experiment = Experiment(
            base_policy_id=active.id,
            candidate_policy_id=shadow.id,
            replay_result_json={
                "base": baseline,
                "candidate": best_metrics,
                "improvement": improvement,
                "mutations": mutations,
                "label_ids": [trace.id for trace in traces],
            },
            decision="shadow_running",
        )
        session.add(experiment)
        emission = bus.emit(
            session,
            "policy.shadow_started",
            {
                "policy_id": shadow.id,
                "version": shadow.version,
                "mutations": mutations,
                "replay_improvement": improvement,
            },
            source_loop=self.name,
            idempotency_key=f"policy.shadow_started:{shadow.id}",
        )
        result.emitted_events += int(emission.created)
        result.inc("shadow_policies")

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        batch = bus.poll(
            session,
            loop_name=self.name,
            event_types=self.consumes,
            limit=limit,
            scan_multiplier=self.settings.event_scan_multiplier,
        )
        result = LoopResult(loop_name=self.name, consumed_events=len(batch.events))
        active = get_active_policy(session)
        if not active.policy_json["optimisation"]["enabled"]:
            result.notes.append("Optimisation is disabled by policy.")
            bus.ack(session, batch)
            return result
        if not batch.events:
            bus.ack(session, batch)
            return result

        window_days = int(active.policy_json["optimisation"]["window_days"])
        traces = self._labelled_traces(session, window_days)
        shadow = session.scalar(select(Policy).where(Policy.status == "shadow").order_by(Policy.id.desc()))
        if shadow:
            started_at_raw = shadow.evaluation_json.get("shadow_started_at")
            max_age = int(active.policy_json["optimisation"]["max_candidate_age_days"])
            if started_at_raw:
                started_at = datetime.fromisoformat(started_at_raw)
                if datetime.now(UTC) - ensure_utc(started_at) > timedelta(days=max_age):
                    shadow.status = "archived"
                    result.notes.append(f"Expired shadow policy {shadow.version} archived.")
                else:
                    self._evaluate_shadow(
                        session,
                        shadow=shadow,
                        traces=traces,
                        result=result,
                    )
            else:
                self._evaluate_shadow(
                    session,
                    shadow=shadow,
                    traces=traces,
                    result=result,
                )
        else:
            self._start_shadow(
                session,
                active=active,
                traces=traces,
                result=result,
            )

        bus.ack(session, batch)
        return result
