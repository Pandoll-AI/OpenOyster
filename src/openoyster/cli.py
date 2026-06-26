from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings
from .connectors.filesystem import SUPPORTED_SUFFIXES
from .connectors.github import fetch_github_items
from .connectors.http import fetch_url
from .connectors.rss import parse_feed_config, parse_rss
from .database import (
    init_db,
    make_engine,
    make_session_factory,
    upgrade_database,
)
from .events import bus
from .loops.supervisor import Supervisor
from .models import (
    Artifact,
    ArtifactFeedback,
    Document,
    Evaluation,
    Event,
    Hypothesis,
    LoopRun,
    Policy,
    Signal,
    Task,
)
from .policies import (
    deep_merge,
    ensure_default_mission,
    ensure_default_policy,
    get_active_policy,
    load_yaml_policy,
    promote_policy,
    validate_policy,
)
from .services.evaluation import evaluate_fixture_path
from .services.inspection import artifact_provenance, hypothesis_evidence

app = typer.Typer(
    no_args_is_help=True,
    help="OpenOyster durable signal-hypothesis-action intelligence runtime.",
)
policy_app = typer.Typer(help="Inspect and manage versioned policies.")
db_app = typer.Typer(help="Database migration commands.")
hypothesis_app = typer.Typer(help="Inspect hypotheses and evidence.")
artifact_app = typer.Typer(help="Inspect artifacts and provenance.")
eval_app = typer.Typer(help="Run local evaluation fixtures.")
app.add_typer(policy_app, name="policy")
app.add_typer(db_app, name="db")
app.add_typer(hypothesis_app, name="hypothesis")
app.add_typer(artifact_app, name="artifact")
app.add_typer(eval_app, name="eval")
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


@app.command()
def init(
    allow_create_all_fallback: Annotated[
        bool,
        typer.Option(
            help=(
                "Use SQLAlchemy create_all only when Alembic fails. This is intended for "
                "disposable local recovery, not managed deployments."
            )
        ),
    ] = False,
) -> None:
    """Initialise workspace, schema, default policy, and mission charter."""

    settings = get_settings()
    settings.ensure_workspace()
    try:
        upgrade_database(settings)
        schema_mode = "Alembic migrations"
    except Exception as exc:
        if not allow_create_all_fallback:
            console.print(f"[bold red]Database migration failed:[/] {exc}")
            console.print(
                "Run [bold]openoyster db upgrade[/] after correcting the database, or use "
                "[bold]--allow-create-all-fallback[/] only for a disposable local database."
            )
            raise typer.Exit(code=1) from exc
        console.print(f"[yellow]Using explicit create_all fallback after migration failure: {exc}[/]")
        engine = make_engine(settings)
        try:
            init_db(engine)
        finally:
            engine.dispose()
        schema_mode = "embedded schema creation (explicit fallback)"
    with _runtime(settings) as (_, _, factory), factory() as session:
        policy = ensure_default_policy(session, settings)
        mission = ensure_default_mission(session)
        session.commit()
    console.print(f"[bold green]OpenOyster initialised[/] at {settings.workspace}")
    console.print(f"Schema: {schema_mode}")
    console.print(f"Active policy: {policy.version}")
    console.print(f"Mission: {mission.version}")


@app.command()
def ingest(
    path: Annotated[Path, typer.Argument(help="File or directory to copy into the inbox.")],
) -> None:
    """Copy supported documents into the inbox for durable intake."""

    settings = get_settings()
    settings.ensure_workspace()
    assert settings.inbox_dir is not None
    if not path.exists():
        raise typer.BadParameter(f"Path does not exist: {path}")
    files = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
    copied = 0
    skipped = 0
    for item in files:
        if item.suffix.casefold() not in SUPPORTED_SUFFIXES:
            skipped += 1
            continue
        relative = item.name if path.is_file() else str(item.relative_to(path))
        target = settings.inbox_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == item.read_bytes():
            skipped += 1
            continue
        shutil.copy2(item, target)
        copied += 1
    console.print(f"[green]Copied {copied} file(s)[/] into {settings.inbox_dir}; skipped {skipped}.")


@app.command("ingest-url")
def ingest_url(
    url: Annotated[str, typer.Argument(help="Public HTTP(S) URL to ingest once.")],
) -> None:
    """Fetch one public URL with SSRF and response-size protections."""

    settings = get_settings()
    parsed = fetch_url(
        url,
        max_bytes=settings.max_file_bytes,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    with _runtime(settings) as (_, _, factory), factory() as session:
        existing = session.scalar(select(Document).where(Document.ingest_key == parsed.ingest_key))
        if existing:
            console.print(f"[yellow]Already ingested as document {existing.id}.[/]")
            return
        document = Document(
            source=parsed.source,
            source_uri=parsed.source_uri,
            title=parsed.title,
            content_hash=parsed.content_hash,
            ingest_key=parsed.ingest_key,
            raw_text=parsed.text,
            status="pending",
            parser_version=parsed.parser_version,
            metadata_json=parsed.metadata,
        )
        session.add(document)
        session.flush()
        bus.emit(
            session,
            "doc.fetched",
            {"document_id": document.id, "created": True},
            source_loop="cli",
            idempotency_key=f"doc.fetched:{document.id}",
        )
        session.commit()
        console.print(f"[green]Ingested URL as document {document.id}.[/]")


@app.command("ingest-rss")
def ingest_rss(
    config_path: Annotated[Path, typer.Argument(help="YAML file containing RSS feed URLs.")],
    item_limit: Annotated[int, typer.Option(min=1, max=100, help="Maximum items per feed.")] = 20,
) -> None:
    """Ingest recent items from one or more RSS/Atom feeds."""

    settings = get_settings()
    feed_urls = parse_feed_config(config_path)
    created = 0
    skipped = 0
    with _runtime(settings) as (_, _, factory), factory() as session:
        for feed_url in feed_urls:
            for parsed in parse_rss(
                feed_url,
                max_bytes=settings.max_file_bytes,
                timeout_seconds=settings.llm_timeout_seconds,
                item_limit=item_limit,
            ):
                existing = session.scalar(select(Document).where(Document.ingest_key == parsed.ingest_key))
                if existing:
                    skipped += 1
                    continue
                document = Document(
                    source=parsed.source,
                    source_uri=parsed.source_uri,
                    title=parsed.title,
                    content_hash=parsed.content_hash,
                    ingest_key=parsed.ingest_key,
                    raw_text=parsed.text,
                    status="pending",
                    parser_version=parsed.parser_version,
                    metadata_json=parsed.metadata,
                )
                session.add(document)
                session.flush()
                bus.emit(
                    session,
                    "doc.fetched",
                    {"document_id": document.id, "created": True},
                    source_loop="cli",
                    idempotency_key=f"doc.fetched:{document.id}",
                )
                created += 1
        session.commit()
    console.print(f"[green]Ingested {created} RSS item(s)[/]; skipped {skipped}.")


@app.command("ingest-github")
def ingest_github(
    repo: Annotated[str, typer.Argument(help="GitHub repository as owner/name.")],
    kind: Annotated[str, typer.Option(help="Read source: releases or issues.")] = "releases",
    limit: Annotated[int, typer.Option(min=1, max=100, help="Maximum GitHub items.")] = 25,
) -> None:
    """Ingest public GitHub releases or issues without performing writes."""

    if kind not in {"releases", "issues"}:
        raise typer.BadParameter("kind must be releases or issues")
    github_kind = cast(Literal["releases", "issues"], kind)
    settings = get_settings()
    token = os.environ.get("OPENOYSTER_GITHUB_TOKEN")
    created = 0
    skipped = 0
    with _runtime(settings) as (_, _, factory), factory() as session:
        for parsed in fetch_github_items(
            repo,
            kind=github_kind,
            token=token,
            max_bytes=settings.max_file_bytes,
            timeout_seconds=settings.llm_timeout_seconds,
            limit=limit,
        ):
            existing = session.scalar(select(Document).where(Document.ingest_key == parsed.ingest_key))
            if existing:
                skipped += 1
                continue
            document = Document(
                source=parsed.source,
                source_uri=parsed.source_uri,
                title=parsed.title,
                content_hash=parsed.content_hash,
                ingest_key=parsed.ingest_key,
                raw_text=parsed.text,
                status="pending",
                parser_version=parsed.parser_version,
                metadata_json=parsed.metadata,
            )
            session.add(document)
            session.flush()
            bus.emit(
                session,
                "doc.fetched",
                {"document_id": document.id, "created": True},
                source_loop="cli",
                idempotency_key=f"doc.fetched:{document.id}",
            )
            created += 1
        session.commit()
    console.print(f"[green]Ingested {created} GitHub {kind} item(s)[/]; skipped {skipped}.")


@app.command()
def run(
    cycles: Annotated[int, typer.Option(min=1, help="Number of supervisor cycles.")] = 1,
    forever: Annotated[bool, typer.Option(help="Run until interrupted.")] = False,
    sleep: Annotated[float, typer.Option(min=0, help="Seconds between cycles.")] = 5.0,
) -> None:
    """Run all loops locally with durable leases and independent transactions."""

    supervisor = Supervisor()
    try:
        if forever:
            console.print("[bold]OpenOyster worker running. Press Ctrl+C to stop.[/]")
            while True:
                _print_results(supervisor.run_cycle())
                time.sleep(sleep)
        else:
            for index in range(cycles):
                console.print(f"[bold cyan]Cycle {index + 1}/{cycles}[/]")
                _print_results(supervisor.run_cycle())
                if index < cycles - 1 and sleep:
                    time.sleep(sleep)
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker stopped.[/]")
    finally:
        supervisor.close()


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Bind port.")] = 8080,
    reload: Annotated[bool, typer.Option(help="Enable development reload.")] = False,
) -> None:
    """Run the FastAPI service and read-only dashboard."""

    import uvicorn

    uvicorn.run("openoyster.api.app:app", host=host, port=port, reload=reload)


@app.command()
def status() -> None:
    """Show durable object counts, failed work, and latest hypotheses."""

    with _runtime() as (_, _, factory), factory() as session:
        counts = {
            "events": session.scalar(select(func.count(Event.id))) or 0,
            "documents": session.scalar(select(func.count(Document.id))) or 0,
            "signals": session.scalar(select(func.count(Signal.id))) or 0,
            "hypotheses": session.scalar(select(func.count(Hypothesis.id))) or 0,
            "tasks": session.scalar(select(func.count(Task.id))) or 0,
            "artifacts": session.scalar(select(func.count(Artifact.id))) or 0,
            "evaluations": session.scalar(select(func.count(Evaluation.id))) or 0,
            "failed_tasks": session.scalar(select(func.count(Task.id)).where(Task.status == "failed")) or 0,
            "failed_loops": session.scalar(select(func.count(LoopRun.id)).where(LoopRun.status == "failed"))
            or 0,
        }
        active = get_active_policy(session)
        latest = list(session.scalars(select(Hypothesis).order_by(Hypothesis.updated_at.desc()).limit(8)))
    table = Table(title=f"OpenOyster Status — policy {active.version}")
    table.add_column("Object")
    table.add_column("Count", justify="right")
    for key, value in counts.items():
        table.add_row(key, str(value))
    console.print(table)
    if latest:
        hypothesis_table = Table(title="Latest hypotheses")
        hypothesis_table.add_column("ID", justify="right")
        hypothesis_table.add_column("Confidence", justify="right")
        hypothesis_table.add_column("Rev", justify="right")
        hypothesis_table.add_column("Status")
        hypothesis_table.add_column("Claim")
        for item in latest:
            hypothesis_table.add_row(
                str(item.id),
                f"{item.confidence:.3f}",
                str(item.revision),
                item.status,
                item.claim[:110],
            )
        console.print(hypothesis_table)


@app.command()
def doctor() -> None:
    """Run operational checks and exit non-zero when a critical check fails."""

    settings = get_settings()
    checks: list[tuple[str, bool, str]] = []
    settings.ensure_workspace()
    checks.append(("workspace", settings.workspace.is_dir(), str(settings.workspace)))
    probe = settings.workspace / ".write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(("workspace writable", True, "ok"))
    except OSError as exc:
        checks.append(("workspace writable", False, str(exc)))
    try:
        with _runtime(settings) as (_, _, factory), factory() as session:
            session.execute(text("SELECT 1"))
            policy = get_active_policy(session)
            validate_policy(policy.policy_json)
            checks.append(("database", True, settings.db_url))
            checks.append(("active policy", True, policy.version))
    except Exception as exc:
        checks.append(("database/policy", False, str(exc)))
    if settings.llm_provider == "openai-compatible" and not settings.llm_api_key:
        checks.append(("remote LLM credentials", False, "provider selected but API key missing"))
    else:
        checks.append(("LLM provider", True, settings.llm_provider))
    if settings.api_key:
        checks.append(("write API auth", True, f"header {settings.api_key_header}"))
    else:
        checks.append(
            (
                "write API auth",
                settings.api_allow_unsafe_no_key,
                "no key; writes are open" if settings.api_allow_unsafe_no_key else "writes disabled",
            )
        )

    table = Table(title="OpenOyster Doctor")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail")
    failed = False
    for name, ok, detail in checks:
        table.add_row(name, "PASS" if ok else "FAIL", detail)
        failed |= not ok
    console.print(table)
    if failed:
        raise typer.Exit(code=1)


@app.command("doctor-dev")
def doctor_dev() -> None:
    """Check whether the local development verification toolchain is available."""

    checks = [
        ("python", sys.version.split()[0], sys.version_info >= (3, 11)),
        ("sqlalchemy", "importable", find_spec("sqlalchemy") is not None),
        ("pytest", "importable", find_spec("pytest") is not None),
        ("ruff", "importable", find_spec("ruff") is not None),
        ("mypy", "importable", find_spec("mypy") is not None),
        ("build", "importable", find_spec("build") is not None),
    ]
    table = Table(title="OpenOyster Dev Doctor")
    table.add_column("Check")
    table.add_column("Detail")
    table.add_column("Result")
    failed = False
    for name, detail, ok in checks:
        failed |= not ok
        table.add_row(name, detail, "PASS" if ok else "FAIL")
    console.print(table)
    if failed:
        console.print('Install the dev environment with: [bold]pip install -e ".[dev]"[/]')
        raise typer.Exit(code=1)


@app.command()
def feedback(
    artifact_id: Annotated[int, typer.Argument(min=1)],
    verdict: Annotated[
        str,
        typer.Option(help="used, useful, rejected, stale, or not_useful"),
    ],
    score: Annotated[float | None, typer.Option(min=0, max=1)] = None,
    comment: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Record explicit downstream feedback used by evaluation and policy tuning."""

    allowed = {"used", "useful", "rejected", "stale", "not_useful"}
    if verdict not in allowed:
        raise typer.BadParameter(f"verdict must be one of {sorted(allowed)}")
    with _runtime() as (_, _, factory), factory() as session:
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise typer.BadParameter(f"Artifact not found: {artifact_id}")
        item = ArtifactFeedback(
            artifact_id=artifact.id,
            verdict=verdict,
            score=score,
            comment=comment,
            source="human-cli",
        )
        session.add(item)
        session.flush()
        bus.emit(
            session,
            "artifact.feedback.recorded",
            {
                "artifact_id": artifact.id,
                "feedback_id": item.id,
                "verdict": verdict,
            },
            source_loop="cli",
            idempotency_key=f"artifact.feedback:{item.id}",
        )
        session.commit()
    console.print(f"[green]Feedback recorded for artifact {artifact_id}.[/]")


@app.command("premise-review")
def premise_review() -> None:
    """Request the global scope and mission-alignment review loop."""

    with _runtime() as (_, _, factory), factory() as session:
        emission = bus.emit(
            session,
            "premise.review_requested",
            {"reason": "manual CLI request"},
            source_loop="cli",
        )
        session.commit()
    console.print(f"[green]Premise review event {emission.event.id} queued.[/]")


@app.command()
def export(
    output: Annotated[Path, typer.Option(help="Output JSON file.")] = Path("openoyster-export.json"),
) -> None:
    """Export hypotheses, evidence-light metadata, artifacts, and policy state."""

    with _runtime() as (_, _, factory), factory() as session:
        active = get_active_policy(session)
        payload = {
            "active_policy": active.version,
            "hypotheses": [
                {
                    "id": item.id,
                    "claim": item.claim,
                    "scope": item.scope,
                    "confidence": item.confidence,
                    "revision": item.revision,
                    "status": item.status,
                }
                for item in session.scalars(select(Hypothesis).order_by(Hypothesis.id))
            ],
            "artifacts": [
                {
                    "id": item.id,
                    "type": item.artifact_type,
                    "title": item.title,
                    "status": item.status,
                    "version": item.version,
                    "content": item.content,
                }
                for item in session.scalars(select(Artifact).order_by(Artifact.id))
            ],
        }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]Exported[/] {output}")


@hypothesis_app.command("show")
def hypothesis_show(
    hypothesis_id: Annotated[int, typer.Argument(min=1)],
    evidence: Annotated[bool, typer.Option(help="Include evidence and chunk excerpts.")] = False,
) -> None:
    """Show one hypothesis, optionally with traceable evidence."""

    with _runtime() as (_, _, factory), factory() as session:
        hypothesis = session.get(Hypothesis, hypothesis_id)
        if not hypothesis:
            raise typer.BadParameter(f"Hypothesis not found: {hypothesis_id}")
        if evidence:
            payload = hypothesis_evidence(session, hypothesis)
        else:
            payload = {
                "id": hypothesis.id,
                "claim": hypothesis.claim,
                "scope": hypothesis.scope,
                "confidence": hypothesis.confidence,
                "status": hypothesis.status,
                "revision": hypothesis.revision,
            }
    console.print(json.dumps(payload, ensure_ascii=False, indent=2))


@artifact_app.command("show")
def artifact_show(
    artifact_id: Annotated[int, typer.Argument(min=1)],
    provenance: Annotated[bool, typer.Option(help="Include task, hypothesis, and evidence provenance.")] = False,
) -> None:
    """Show one artifact, optionally with provenance."""

    with _runtime() as (_, _, factory), factory() as session:
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise typer.BadParameter(f"Artifact not found: {artifact_id}")
        if provenance:
            payload = artifact_provenance(session, artifact)
        else:
            payload = {
                "id": artifact.id,
                "artifact_type": artifact.artifact_type,
                "title": artifact.title,
                "version": artifact.version,
                "status": artifact.status,
                "linked_hypothesis_id": artifact.linked_hypothesis_id,
                "linked_task_id": artifact.linked_task_id,
            }
    console.print(json.dumps(payload, ensure_ascii=False, indent=2))


@eval_app.command("fixtures")
def eval_fixtures(
    path: Annotated[Path, typer.Argument(help="Evaluation fixture directory or JSON file.")] = Path(
        "examples/eval"
    ),
    output: Annotated[Path | None, typer.Option(help="Optional JSON output file.")] = None,
) -> None:
    """Run deterministic extraction/evidence fixture checks."""

    report = evaluate_fixture_path(path)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if output is not None:
        output.write_text(rendered, encoding="utf-8")
        console.print(f"[green]Wrote evaluation report[/] {output}")
    else:
        console.print(rendered)


@policy_app.command("create")
def policy_create(
    path: Annotated[Path, typer.Argument(help="YAML file containing policy overrides.")],
    version: Annotated[str | None, typer.Option(help="Explicit policy version.")] = None,
    activate: Annotated[
        bool,
        typer.Option(help="Immediately promote the validated policy. Default: keep as candidate."),
    ] = False,
) -> None:
    """Create a validated policy candidate from YAML overrides."""

    if not path.is_file():
        raise typer.BadParameter(f"Policy file not found: {path}")
    override = load_yaml_policy(str(path))
    with _runtime() as (_, _, factory), factory() as session:
        active = get_active_policy(session)
        merged = deep_merge(active.policy_json, override)
        validate_policy(merged)
        resolved_version = version or f"manual-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        existing = session.scalar(select(Policy).where(Policy.version == resolved_version))
        if existing:
            raise typer.BadParameter(f"Policy version already exists: {resolved_version}")
        policy = Policy(
            version=resolved_version,
            parent_policy_id=active.id,
            policy_json=merged,
            status="candidate",
            evaluation_json={"origin": "manual_yaml", "source_path": str(path)},
        )
        session.add(policy)
        session.flush()
        event_type = "policy.candidate_created"
        if activate:
            promote_policy(session, policy)
            event_type = "policy.promoted"
        bus.emit(
            session,
            event_type,
            {
                "policy_id": policy.id,
                "version": policy.version,
                "manual": True,
                "activated": activate,
            },
            source_loop="cli",
            idempotency_key=f"{event_type}:{policy.id}",
        )
        session.commit()
    state = "active" if activate else "candidate"
    console.print(f"[green]Created policy {resolved_version} ({state}).[/]")


@policy_app.command("show")
def policy_show() -> None:
    """Print the active policy JSON."""

    with _runtime() as (_, _, factory), factory() as session:
        policy = get_active_policy(session)
        console.print(
            json.dumps(
                {"version": policy.version, "policy": policy.policy_json},
                indent=2,
                ensure_ascii=False,
            )
        )


@policy_app.command("list")
def policy_list() -> None:
    """List active, shadow, and archived policies."""

    with _runtime() as (_, _, factory), factory() as session:
        policies = list(session.scalars(select(Policy).order_by(Policy.id.desc())))
    table = Table(title="Policies")
    table.add_column("ID", justify="right")
    table.add_column("Version")
    table.add_column("Status")
    table.add_column("Score", justify="right")
    for item in policies:
        table.add_row(
            str(item.id),
            item.version,
            item.status,
            "" if item.score is None else f"{item.score:.3f}",
        )
    console.print(table)


@policy_app.command("promote")
def policy_promote(policy_id: Annotated[int, typer.Argument(min=1)]) -> None:
    """Manually promote a validated policy version."""

    with _runtime() as (_, _, factory), factory() as session:
        policy = session.get(Policy, policy_id)
        if not policy:
            raise typer.BadParameter(f"Policy not found: {policy_id}")
        promote_policy(session, policy)
        bus.emit(
            session,
            "policy.promoted",
            {"policy_id": policy.id, "version": policy.version, "manual": True},
            source_loop="cli",
            idempotency_key=f"policy.promoted:{policy.id}",
        )
        session.commit()
    console.print(f"[green]Promoted policy {policy_id}.[/]")


@db_app.command("upgrade")
def db_upgrade(
    revision: Annotated[str, typer.Argument(help="Alembic revision, normally head.")] = "head",
) -> None:
    """Apply durable schema migrations."""

    settings = get_settings()
    upgrade_database(settings, revision)
    console.print(f"[green]Database upgraded to {revision}.[/]")


def _print_results(results) -> None:
    table = Table(title="Loop results")
    table.add_column("Loop")
    table.add_column("Consumed", justify="right")
    table.add_column("Emitted", justify="right")
    table.add_column("Created")
    table.add_column("Notes")
    for result in results:
        table.add_row(
            result.loop_name,
            str(result.consumed_events),
            str(result.emitted_events),
            ", ".join(f"{key}:{value}" for key, value in result.created_records.items()) or "-",
            "; ".join(result.notes) or "-",
        )
    console.print(table)
