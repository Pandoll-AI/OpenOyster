from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings
from .database import init_db, make_engine, make_session_factory
from .llm import provider_from_settings
from .policies import get_active_policy
from .services.evaluation import evaluate_counter_evidence, evaluate_goldset
from .services.evaluation_common import json_path_for_id, safe_child_path
from .services.evaluation_deliberation import (
    DEFAULT_SCENARIOS_DIR,
    DeliberationGoldsetReport,
    evaluate_deliberation_goldset,
)
from .services.evaluation_report import write_eval_outputs

GOLD_DOCS_DIR = Path("goldset/docs")
GOLD_LABELS_DIR = Path("goldset/labels")

eval_app = typer.Typer(help="Run evaluation harnesses.")
gold_app = typer.Typer(help="Review gold-set labels.")
console = Console()


@contextmanager
def _runtime(settings: Settings | None = None) -> Iterator[tuple[Settings, Engine, sessionmaker[Session]]]:
    runtime_settings = settings or get_settings()
    runtime_settings.ensure_workspace()
    engine = make_engine(runtime_settings)
    try:
        init_db(engine)
        yield runtime_settings, engine, make_session_factory(engine)
    finally:
        engine.dispose()


@eval_app.command("gold")
def eval_gold(
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
    no_report: Annotated[bool, typer.Option("--no-report")] = False,
    docs_dir: Annotated[Path, typer.Option("--docs-dir", hidden=True)] = GOLD_DOCS_DIR,
    labels_dir: Annotated[Path, typer.Option("--labels-dir", hidden=True)] = GOLD_LABELS_DIR,
) -> None:
    with _runtime() as (settings, _, factory), factory() as session:
        policy = get_active_policy(session).policy_json
        provider = provider_from_settings(settings)
    if settings.llm_provider == "stub":
        console.print("[yellow]Stub provider selected; eval metrics are not meaningful.[/]")
    report = evaluate_goldset(provider, docs_dir=docs_dir, labels_dir=labels_dir, policy=policy, doc_limit=limit)
    _print_gold_eval_summary(report)
    if not no_report:
        raw_path = write_eval_outputs(gold_report=report)
        console.print(f"Raw result: {raw_path}")


@eval_app.command("counter")
def eval_counter(
    cycles: Annotated[int, typer.Option("--cycles", min=1)] = 1,
    docs_dir: Annotated[Path, typer.Option("--docs-dir", hidden=True)] = GOLD_DOCS_DIR,
) -> None:
    with _runtime() as (settings, _, factory), factory() as session:
        policy = get_active_policy(session).policy_json
        provider = provider_from_settings(settings)
    if settings.llm_provider == "stub":
        console.print("[yellow]Stub provider selected; eval metrics are not meaningful.[/]")
    report = evaluate_counter_evidence(provider, docs_dir=docs_dir, policy=policy, cycles=cycles)
    _print_counter_eval_summary(report)
    raw_path = write_eval_outputs(counter_report=report)
    console.print(f"Raw result: {raw_path}")


@eval_app.command("deliberation")
def eval_deliberation(
    scenarios: Annotated[
        Path,
        typer.Option("--scenarios", help="Directory of deliberation gold-set scenarios"),
    ] = DEFAULT_SCENARIOS_DIR,
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    """Run deliberation-engine quality gold set (outcome / abstention / critic)."""
    with _runtime() as (settings, _, _factory):
        provider = provider_from_settings(settings)
        report = evaluate_deliberation_goldset(
            provider,
            scenarios_dir=scenarios,
            settings=settings,
        )
    if as_json:
        # Plain stdout JSON (no Rich styling) for machine consumers / CI.
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    if settings.llm_provider == "stub":
        console.print(
            "[yellow]Stub provider selected; judgment-quality metrics are not meaningful "
            "except deterministic no_evidence / no_match retrieval abstentions.[/]"
        )
    _print_deliberation_eval_summary(report)


def _print_deliberation_eval_summary(report: DeliberationGoldsetReport) -> None:
    console.print(f"Provider: {report.provider} / {report.model or 'unknown'}")
    console.print(f"Scenarios evaluated: {report.scenarios_evaluated}")
    console.print(f"judge_note: {report.judge_note}")
    table = Table(title="Deliberation gold-set results")
    table.add_column("Scenario")
    table.add_column("Expected")
    table.add_column("Actual")
    table.add_column("Verdict")
    table.add_column("Notes")
    for row in report.results:
        table.add_row(
            row.scenario_id,
            str(row.expected.get("outcome")),
            str(row.actual.get("outcome") or row.actual.get("error") or "?"),
            row.verdict,
            "; ".join(row.notes) if row.notes else "",
        )
    console.print(table)
    agg = report.aggregates
    metrics = Table(title="Aggregates")
    metrics.add_column("Metric")
    metrics.add_column("Value", justify="right")
    for key in (
        "pass_rate",
        "abstention_appropriateness",
        "critic_hit_rate",
        "select_accuracy",
        "scenarios_passed",
        "scenarios_failed",
    ):
        value = agg.get(key)
        if value is None:
            continue
        if key.startswith("scenarios_"):
            metrics.add_row(key, f"{int(value)}")
        else:
            metrics.add_row(key, f"{value:.3f}")
    console.print(metrics)


@gold_app.command("review")
def gold_review(
    only: Annotated[str | None, typer.Option("--only")] = None,
    docs_dir: Annotated[Path, typer.Option("--docs-dir", hidden=True)] = GOLD_DOCS_DIR,
    labels_dir: Annotated[Path, typer.Option("--labels-dir", hidden=True)] = GOLD_LABELS_DIR,
) -> None:
    docs = _load_gold_docs_for_review(docs_dir)
    label_paths = [_review_label_path(labels_dir, only)] if only else _review_label_paths(labels_dir)
    for label_path in label_paths:
        if not label_path.exists():
            console.print(f"[yellow]Missing label:[/] {label_path}")
            continue
        label = json.loads(label_path.read_text(encoding="utf-8"))
        doc_id = str(label.get("doc_id", label_path.stem))
        _review_one_label(label_path, docs.get(doc_id, {}), label, doc_id)


def _review_one_label(label_path: Path, doc: dict, label: dict, doc_id: str) -> None:
    _print_review_item(doc_id, doc, label)
    action = typer.prompt("Action [a]pprove/[e]dit/[s]kip", default="s").strip().casefold()
    if action.startswith("a"):
        label["review_status"] = "approved"
        _write_label(label_path, label)
        console.print(f"[green]Approved[/] {doc_id}")
    elif action.startswith("e"):
        _edit_label(label)
        label["review_status"] = "edited"
        _write_label(label_path, label)
        console.print(f"[green]Edited[/] {doc_id}")
    else:
        console.print(f"[yellow]Skipped[/] {doc_id}")


def _review_label_path(labels_dir: Path, doc_id: str | None) -> Path:
    if doc_id is None:
        raise typer.BadParameter("--only requires a document id")
    try:
        return json_path_for_id(labels_dir, doc_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _review_label_paths(labels_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(labels_dir.glob("*.json")):
        try:
            paths.append(safe_child_path(labels_dir, path))
        except ValueError:
            console.print(f"[yellow]Skipping unsafe label path:[/] {path}")
    return paths


def _print_gold_eval_summary(report) -> None:
    overall = report.metrics["overall"]
    console.print(f"Provider: {report.provider} / {report.model or 'unknown'}")
    console.print(f"Gold documents evaluated: {report.docs_evaluated}")
    console.print(f"Skipped documents: {len(report.skipped_documents)}")
    table = Table(title="Gold eval metrics")
    table.add_column("Slice")
    table.add_column("Core recall", justify="right")
    table.add_column("Entity precision", justify="right")
    table.add_column("Signal F1", justify="right")
    table.add_column("Quote existence", justify="right")
    for key in ("overall", "ko", "en"):
        metrics = report.metrics[key]
        table.add_row(
            key,
            f"{metrics.entity_recall_core:.3f}",
            f"{metrics.entity_precision:.3f}",
            f"{metrics.signal_type_f1:.3f}",
            f"{metrics.quote_existence_rate:.3f}",
        )
    console.print(table)
    console.print(f"Overall signal F1: {overall.signal_type_f1:.3f}")


def _print_counter_eval_summary(report) -> None:
    precision = "N/A" if report.precision is None else f"{report.precision:.3f}"
    console.print(f"Counter oppose edges audited: {report.audited_edges}")
    console.print(f"Counter precision: {precision}")
    console.print(report.status)


def _load_gold_docs_for_review(docs_dir: Path) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for path in docs_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        docs[str(payload.get("id", path.stem))] = payload
    return docs


def _print_review_item(doc_id: str, doc: dict, label: dict) -> None:
    console.rule(doc_id)
    console.print(f"[bold]{doc.get('title', '(missing title)')}[/]")
    console.print(str(doc.get("url", "")))
    console.print(str(doc.get("text", ""))[:500])
    console.print("[bold]Entities[/]")
    for index, entity in enumerate(label.get("expected_entities", []), start=1):
        console.print(f"{index}. {entity}")
    console.print("[bold]Signals[/]")
    for index, signal in enumerate(label.get("expected_signals", []), start=1):
        console.print(f"{index}. {signal}")


def _edit_label(label: dict) -> None:
    entity_delete = _parse_indexes(typer.prompt("Entity numbers to delete, comma-separated", default=""))
    signal_delete = _parse_indexes(typer.prompt("Signal numbers to delete, comma-separated", default=""))
    label["expected_entities"] = [
        item for index, item in enumerate(label.get("expected_entities", []), start=1) if index not in entity_delete
    ]
    label["expected_signals"] = [
        item for index, item in enumerate(label.get("expected_signals", []), start=1) if index not in signal_delete
    ]
    note = typer.prompt("Review note", default="").strip()
    if note:
        existing = str(label.get("notes", "")).strip()
        label["notes"] = f"{existing}\nReview note: {note}".strip()


def _parse_indexes(raw: str) -> set[int]:
    indexes: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if item.isdecimal():
            indexes.add(int(item))
    return indexes


def _write_label(path: Path, label: dict) -> None:
    path.write_text(json.dumps(label, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
