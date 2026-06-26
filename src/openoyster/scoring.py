from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime

from .models import EvidenceEdge, Hypothesis
from .utils import ensure_utc

WORD_RE = re.compile(r"[A-Za-z0-9가-힣_\-]+")


def tokenize(text: str) -> set[str]:
    return {word.casefold() for word in WORD_RE.findall(text) if len(word) > 1}


def jaccard(left: str, right: str) -> float:
    left_tokens, right_tokens = tokenize(left), tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def weighted_trigger_score(
    *,
    novelty: float,
    impact: float,
    contradiction: float,
    evidence_gap: float,
    staleness: float,
    policy: dict,
) -> float:
    trigger = policy.get("trigger", {})
    pairs = [
        (novelty, float(trigger.get("novelty_weight", 0.24))),
        (impact, float(trigger.get("impact_weight", 0.28))),
        (contradiction, float(trigger.get("contradiction_weight", 0.20))),
        (evidence_gap, float(trigger.get("evidence_gap_weight", 0.18))),
        (staleness, float(trigger.get("staleness_weight", 0.10))),
    ]
    denominator = sum(max(weight, 0.0) for _, weight in pairs)
    if denominator <= 0:
        return 0.0
    return clamp(sum(clamp(value) * max(weight, 0.0) for value, weight in pairs) / denominator)


def evidence_gap_score(
    support_count: int,
    oppose_count: int,
    source_diversity: int = 0,
) -> float:
    if support_count + oppose_count == 0:
        return 1.0
    support_gap = max(0.0, (2 - support_count) / 2)
    counter_gap = 1.0 if oppose_count == 0 else 0.0
    diversity_gap = 1.0 if source_diversity <= 1 else 0.0
    return clamp(0.45 * support_gap + 0.35 * counter_gap + 0.20 * diversity_gap)


def contradiction_score(oppose_strength: float, support_strength: float) -> float:
    total = oppose_strength + support_strength
    if total <= 0 or oppose_strength <= 0:
        return 0.0
    balance = 1 - abs(oppose_strength - support_strength) / total
    return clamp(0.55 * (oppose_strength / total) + 0.45 * balance)


def staleness_score(updated_at: datetime, stale_days: int) -> float:
    age_days = max(
        (datetime.now(UTC) - ensure_utc(updated_at)).total_seconds() / 86_400,
        0.0,
    )
    return clamp(age_days / max(stale_days, 1))


def evidence_source_diversity(edges: Iterable[EvidenceEdge]) -> int:
    sources = {
        edge.document_id if edge.document_id is not None else f"provenance:{edge.provenance}"
        for edge in edges
    }
    return len(sources)


def recompute_confidence(hypothesis: Hypothesis, policy: dict) -> float:
    config = policy.get("hypothesis", {})
    alpha = float(config.get("prior_alpha", 1.5))
    beta = float(config.get("prior_beta", 1.5))
    for edge in hypothesis.evidence_edges:
        strength = clamp(edge.strength)
        if edge.stance == "support":
            alpha += strength
        elif edge.stance == "oppose":
            beta += strength
        else:
            alpha += strength * 0.15
            beta += strength * 0.15
    return clamp(alpha / max(alpha + beta, 1e-9))


def concentration(items: list[str]) -> float:
    return max(Counter(items).values()) / len(items) if items else 0.0


def normalised_entropy(items: list[str]) -> float:
    if not items:
        return 0.0
    counts = Counter(items)
    if len(counts) == 1:
        return 0.0
    total = len(items)
    entropy = -sum((count / total) * math.log(count / total) for count in counts.values())
    return clamp(entropy / math.log(len(counts)))


def binary_classification_metrics(labels: list[bool], predictions: list[bool]) -> dict[str, float]:
    if len(labels) != len(predictions):
        raise ValueError("labels and predictions must have the same length")
    if not labels:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "accuracy": 0.0,
            "alert_rate": 0.0,
            "utility": 0.0,
            "n": 0.0,
        }
    tp = sum(label and prediction for label, prediction in zip(labels, predictions, strict=True))
    fp = sum((not label) and prediction for label, prediction in zip(labels, predictions, strict=True))
    fn = sum(label and (not prediction) for label, prediction in zip(labels, predictions, strict=True))
    tn = len(labels) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / len(labels)
    alert_rate = sum(predictions) / len(predictions)
    utility = clamp(0.55 * f1 + 0.25 * precision + 0.20 * accuracy - 0.05 * max(alert_rate - 0.65, 0.0))
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "alert_rate": alert_rate,
        "utility": utility,
        "n": float(len(labels)),
    }
