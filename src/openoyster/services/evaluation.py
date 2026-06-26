from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..policies import DEFAULT_POLICY
from .text import analyse_text


def _load_fixtures(path: Path) -> list[dict[str, Any]]:
    if path.is_file():
        return [json.loads(path.read_text(encoding="utf-8"))]
    if not path.is_dir():
        raise FileNotFoundError(path)
    return [
        json.loads(item.read_text(encoding="utf-8"))
        for item in sorted(path.glob("*.json"))
        if item.name != "README.json"
    ]


def _score_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    documents = fixture.get("documents", [])
    expected_signal_types = {str(item) for item in fixture.get("expected_signal_types", [])}
    expected_counter_terms = [str(item).casefold() for item in fixture.get("expected_counter_terms", [])]
    signals = []
    hypotheses = []
    for document in documents:
        text = str(document.get("text", ""))
        analysis = analyse_text(text, policy=DEFAULT_POLICY)
        signals.extend(analysis.signals)
        hypotheses.extend(analysis.hypotheses)

    found_signal_types = {signal.signal_type for signal in signals}
    signal_type_recall = (
        len(found_signal_types & expected_signal_types) / len(expected_signal_types)
        if expected_signal_types
        else 1.0
    )
    signal_type_precision = (
        len(found_signal_types & expected_signal_types) / len(found_signal_types)
        if found_signal_types
        else 0.0
    )
    oppose_text = " ".join(signal.summary.casefold() for signal in signals if signal.stance == "oppose")
    counter_hits = sum(1 for term in expected_counter_terms if term in oppose_text)
    counter_evidence_discovery_rate = (
        counter_hits / len(expected_counter_terms) if expected_counter_terms else 1.0
    )
    traceable_hypotheses = sum(1 for item in hypotheses if item.evidence_signal_summary)
    artifact_traceability = traceable_hypotheses / len(hypotheses) if hypotheses else 0.0
    return {
        "name": fixture.get("name", "unnamed"),
        "documents": len(documents),
        "signals": len(signals),
        "hypotheses": len(hypotheses),
        "expected_signal_types": sorted(expected_signal_types),
        "found_signal_types": sorted(found_signal_types),
        "signal_type_precision": round(signal_type_precision, 3),
        "signal_type_recall": round(signal_type_recall, 3),
        "counter_evidence_discovery_rate": round(counter_evidence_discovery_rate, 3),
        "artifact_traceability": round(artifact_traceability, 3),
    }


def evaluate_fixture_path(path: Path) -> dict[str, Any]:
    fixture_results = [_score_fixture(fixture) for fixture in _load_fixtures(path)]
    if not fixture_results:
        raise ValueError(f"No JSON fixtures found in {path}")
    metric_names = [
        "signal_type_precision",
        "signal_type_recall",
        "counter_evidence_discovery_rate",
        "artifact_traceability",
    ]
    aggregate = {
        metric: round(sum(float(item[metric]) for item in fixture_results) / len(fixture_results), 3)
        for metric in metric_names
    }
    return {
        "fixture_count": len(fixture_results),
        "aggregate": aggregate,
        "fixtures": fixture_results,
    }
