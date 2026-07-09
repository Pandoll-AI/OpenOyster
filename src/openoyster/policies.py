from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import yaml
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import MissionCharter, Policy

DEFAULT_POLICY: dict[str, Any] = {
    "retrieval": {
        "mode": "lexical",
        "top_k": 12,
        "max_scan_chunks": 5000,
        "recency_weight": 0.15,
        "minimum_similarity": 0.08,
        "source_diversity_cap": 0,
        "counter_evidence_terms": [
            "not",
            "no",
            "failed",
            "contrary",
            "disputed",
            "unsupported",
            "반대",
            "아니다",
        ],
    },
    "extraction": {
        "chunk_size": 1800,
        "chunk_overlap": 180,
        "max_hypotheses_per_chunk": 5,
        "max_signals_per_chunk": 8,
        "max_claims_per_chunk": 12,
    },
    "trigger": {
        "novelty_weight": 0.24,
        "impact_weight": 0.28,
        "contradiction_weight": 0.20,
        "evidence_gap_weight": 0.18,
        "staleness_weight": 0.10,
        "fire_threshold": 0.40,
        "high_alert_threshold": 0.86,
    },
    "hypothesis": {
        "merge_similarity_threshold": 0.62,
        "minimum_evidence_strength": 0.25,
        "stale_days": 21,
        "minimum_support_for_maturity": 2,
        "minimum_source_diversity": 2,
        "prior_alpha": 1.5,
        "prior_beta": 1.5,
    },
    "planning": {
        "max_depth": 3,
        "max_tasks_per_cycle": 20,
        "max_tasks_per_trigger": 3,
        "exploration_rate": 0.15,
        "task_retry_limit": 3,
    },
    "execution": {
        "daily_cost_limit": 5.0,
        "max_tool_calls_per_task": 8,
        "default_model": "codex",
        "max_candidate_evidence": 8,
    },
    "utilisation": {
        "notify_threshold": 0.86,
        "report_candidate_threshold": 0.64,
        "minimum_evidence_count": 2,
        "minimum_source_diversity": 1,
        "automation_candidate_threshold": 0.82,
    },
    "evaluation": {
        "target_artifact_quality": 0.72,
        "max_redundancy_rate": 0.30,
        "min_hypothesis_survival_rate": 0.40,
        "feedback_positive_verdicts": ["used", "useful"],
        "feedback_negative_verdicts": ["rejected", "stale", "not_useful"],
    },
    "maintenance": {
        "heartbeat_interval_minutes": 5,
        "failed_document_retry_minutes": 30,
        "deferred_chunk_retry_minutes": 60,
        "stale_hypothesis_scan_hours": 6,
        "max_document_failures": 3,
    },
    "safety": {
        "external_write_requires_approval": True,
        "mission_change_requires_approval": True,
        "max_policy_change_per_run": 0.08,
        "minimum_human_labels_for_auto_promotion": 5,
    },
}

DEFAULT_MISSION = {
    "version": "mission-001",
    "mission": (
        "Continuously observe heterogeneous evidence, identify material changes, build "
        "falsifiable hypotheses, validate them with support and counter-evidence, and "
        "turn only sufficiently grounded hypotheses into decision-ready artifacts."
    ),
    "domains": [
        "AI strategy",
        "business transformation",
        "healthcare AI",
        "public policy",
        "investment intelligence",
    ],
    "anti_goals": [
        "Do not make irreversible external changes without explicit approval.",
        "Do not optimise only for output volume or prose fluency.",
        "Do not suppress counter-evidence or uncertainty.",
        "Do not silently substitute a heuristic result for a failed remote model call.",
    ],
    "success_criteria": [
        "Material signals are detected without excessive alert noise.",
        "Hypotheses retain traceable support and counter-evidence.",
        "Artifacts are adopted into real decisions, reports, or backlogs.",
        "The observed source universe remains aligned with the mission charter.",
    ],
}


def load_yaml_policy(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise ValueError("Policy YAML must contain a mapping at the top level")
    return payload


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_nested(policy_json: dict[str, Any], path: str, default: Any = None) -> Any:
    cursor: Any = policy_json
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return default
        cursor = cursor[part]
    return cursor


def set_nested(policy_json: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    result = deepcopy(policy_json)
    cursor = result
    parts = path.split(".")
    for part in parts[:-1]:
        child = cursor.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"Cannot set nested policy path through non-object: {path}")
        cursor = child
    cursor[parts[-1]] = value
    return result


def _bounded(value: Any, *, name: str, low: float = 0.0, high: float = 1.0) -> None:
    if not isinstance(value, int | float) or not low <= float(value) <= high:
        raise ValueError(f"{name} must be between {low} and {high}")


def validate_policy(policy_json: dict[str, Any]) -> None:
    required_sections = set(DEFAULT_POLICY)
    missing = required_sections - set(policy_json)
    if missing:
        raise ValueError(f"Missing policy sections: {sorted(missing)}")

    for path in (
        "trigger.fire_threshold",
        "trigger.high_alert_threshold",
        "retrieval.minimum_similarity",
        "retrieval.recency_weight",
        "planning.exploration_rate",
        "utilisation.report_candidate_threshold",
    ):
        _bounded(get_nested(policy_json, path), name=path)

    retrieval = policy_json["retrieval"]
    if retrieval.get("mode") not in {"lexical", "postgres_full_text", "auto"}:
        raise ValueError("retrieval.mode must be lexical, postgres_full_text, or auto")
    if int(retrieval.get("top_k", 0)) < 1:
        raise ValueError("retrieval.top_k must be at least 1")
    if int(retrieval.get("max_scan_chunks", 0)) < 1:
        raise ValueError("retrieval.max_scan_chunks must be at least 1")
    if int(retrieval.get("source_diversity_cap", 0)) < 0:
        raise ValueError("retrieval.source_diversity_cap cannot be negative")
    if not isinstance(retrieval.get("counter_evidence_terms"), list):
        raise ValueError("retrieval.counter_evidence_terms must be a list")

    trigger = policy_json["trigger"]
    weights = [
        float(trigger[f"{name}_weight"])
        for name in ("novelty", "impact", "contradiction", "evidence_gap", "staleness")
    ]
    if sum(weights) <= 0:
        raise ValueError("At least one trigger weight must be positive")

    chunk_size = int(get_nested(policy_json, "extraction.chunk_size"))
    chunk_overlap = int(get_nested(policy_json, "extraction.chunk_overlap"))
    if chunk_size < 200 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("extraction.chunk_overlap must be non-negative and smaller than chunk_size")

    if int(get_nested(policy_json, "planning.max_tasks_per_cycle")) < 1:
        raise ValueError("planning.max_tasks_per_cycle must be at least 1")
    if float(get_nested(policy_json, "execution.daily_cost_limit")) < 0:
        raise ValueError("execution.daily_cost_limit cannot be negative")


def ensure_default_policy(session: Session, settings: Settings | None = None) -> Policy:
    settings = settings or get_settings()
    existing = session.scalar(select(Policy).where(Policy.status == "active").order_by(Policy.id.desc()))
    if existing:
        return existing
    validate_policy(DEFAULT_POLICY)
    policy = Policy(
        version=settings.default_policy_version,
        policy_json=deepcopy(DEFAULT_POLICY),
        status="active",
        score=0.0,
        evaluation_json={"origin": "built-in default"},
        promoted_at=datetime.now(UTC),
    )
    session.add(policy)
    session.flush()
    return policy


def get_active_policy(session: Session) -> Policy:
    policy = session.scalar(select(Policy).where(Policy.status == "active").order_by(Policy.id.desc()))
    return policy or ensure_default_policy(session)


def promote_policy(session: Session, policy: Policy, *, score: float | None = None) -> Policy:
    validate_policy(policy.policy_json)
    session.execute(
        update(Policy).where(Policy.status == "active", Policy.id != policy.id).values(status="archived")
    )
    policy.status = "active"
    policy.score = score if score is not None else policy.score
    policy.promoted_at = datetime.now(UTC)
    session.flush()
    return policy


def ensure_default_mission(session: Session) -> MissionCharter:
    existing = session.scalar(
        select(MissionCharter).where(MissionCharter.active.is_(True)).order_by(MissionCharter.id.desc())
    )
    if existing:
        return existing
    mission = MissionCharter(
        version=DEFAULT_MISSION["version"],
        mission=DEFAULT_MISSION["mission"],
        domains_json=DEFAULT_MISSION["domains"],
        anti_goals_json=DEFAULT_MISSION["anti_goals"],
        success_criteria_json=DEFAULT_MISSION["success_criteria"],
        active=True,
    )
    session.add(mission)
    session.flush()
    return mission
