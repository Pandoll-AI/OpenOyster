from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .evaluation_models import CounterEvalReport, GoldEvalReport


def write_eval_outputs(
    *,
    gold_report: GoldEvalReport | None = None,
    counter_report: CounterEvalReport | None = None,
    report_path: Path = Path("docs/EVAL_REPORT.md"),
    results_dir: Path = Path("goldset/results"),
) -> Path:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    git_rev = _git_rev()
    results_dir.mkdir(parents=True, exist_ok=True)
    raw_path = results_dir / f"{git_rev}-{_filename_timestamp(generated_at)}.json"
    raw_payload = {
        "generated_at": generated_at,
        "git_rev": git_rev,
        "gold": gold_report.model_dump(mode="json") if gold_report else None,
        "counter": counter_report.model_dump(mode="json") if counter_report else None,
    }
    raw_path.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("a", encoding="utf-8") as stream:
        stream.write(_render_markdown(generated_at, git_rev, gold_report, counter_report, raw_path))
    return raw_path


def _render_markdown(
    generated_at: str,
    git_rev: str,
    gold_report: GoldEvalReport | None,
    counter_report: CounterEvalReport | None,
    raw_path: Path,
) -> str:
    lines = [
        "",
        f"## Eval Iteration {generated_at} `{git_rev}`",
        "",
        "라벨은 LLM-judge(gpt-5.4) 초벌, 사람 미검수",
        "",
        f"Raw result: `{raw_path}`",
        "",
    ]
    if gold_report:
        lines.extend(_render_gold_section(gold_report))
    if counter_report:
        lines.extend(_render_counter_section(counter_report))
    return "\n".join(lines) + "\n"


def _render_gold_section(report: GoldEvalReport) -> list[str]:
    lines = [
        f"Provider/model: `{report.provider}` / `{report.model or 'unknown'}`",
        "",
        "| slice | entity_recall_core | entity_precision | signal_type_f1 | quote_existence_rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in ("overall", "ko", "en"):
        metrics = report.metrics[key]
        lines.append(
            f"| {key} | {_fmt(metrics.entity_recall_core)} | {_fmt(metrics.entity_precision)} | "
            f"{_fmt(metrics.signal_type_f1)} | {_fmt(metrics.quote_existence_rate)} |"
        )
    lines.extend(
        [
            "",
            "Gates:",
            f"- signal F1 >= 0.75: {_gate(report.metrics['overall'].signal_type_f1, 0.75)}",
            f"- ko entity_recall_core >= 0.80: {_gate(report.metrics['ko'].entity_recall_core, 0.80)}",
            "- counter precision >= 0.70: N/A",
            f"- quote_existence >= 0.95: {_gate(report.metrics['overall'].quote_existence_rate, 0.95)}",
            "",
            f"Review statuses: {report.review_status_counts}",
            f"Top errors: {_top_gold_errors(report)}",
            "",
        ]
    )
    return lines


def _render_counter_section(report: CounterEvalReport) -> list[str]:
    precision = "N/A" if report.precision is None else _fmt(report.precision)
    gate = "N/A" if report.precision is None else _gate(report.precision, 0.70)
    return [
        f"Counter provider/model: `{report.provider}` / `{report.model or 'unknown'}`",
        f"Counter precision: {precision}",
        "",
        "Gates:",
        "- signal F1 >= 0.75: N/A",
        "- ko entity_recall_core >= 0.80: N/A",
        f"- counter precision >= 0.70: {gate}",
        "- quote_existence >= 0.95: N/A",
        "",
        f"Counter status: {report.status}",
        f"Counter note: {report.audit_model_note}",
        "",
    ]


def _top_gold_errors(report: GoldEvalReport) -> str:
    missing_entities = sum(len(item.missing_core_entities) for item in report.per_doc)
    missing_signals = sum(len(item.missing_signal_types) for item in report.per_doc)
    extra_signals = sum(len(item.extra_signal_types) for item in report.per_doc)
    fabricated_quotes = sum(len(item.fabricated_quotes) for item in report.per_doc)
    return (
        f"missing_core_entities={missing_entities}, missing_signal_types={missing_signals}, "
        f"extra_signal_types={extra_signals}, fabricated_quotes={fabricated_quotes}"
    )


def _git_rev() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unknown"


def _filename_timestamp(value: str) -> str:
    return value.replace("-", "").replace(":", "").replace("+", "Z")


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _gate(value: float, threshold: float) -> str:
    return "PASS" if value >= threshold else "FAIL"
