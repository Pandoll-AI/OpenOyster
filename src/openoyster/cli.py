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
import yaml
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .cli_eval import eval_app, gold_app
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
from .deliberation_contracts import Mission
from .events import bus
from .llm import provider_from_settings
from .loops.supervisor import Supervisor
from .models import (
    Artifact,
    ArtifactFeedback,
    DeliberationArtifact,
    DeliberationCognitiveImpact,
    DeliberationDossier,
    DeliberationRun,
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
from .services import deliberation, opencrab_packs, pack_answering
from .services.deliberation_replay import replay_deliberation
from .services.inspection import artifact_provenance, hypothesis_evidence

app = typer.Typer(
    no_args_is_help=True,
    help="OpenOyster durable signal-hypothesis-action intelligence runtime.",
)
policy_app = typer.Typer(help="Inspect and manage versioned policies.")
db_app = typer.Typer(help="Database migration commands.")
hypothesis_app = typer.Typer(help="Inspect hypotheses and evidence.")
artifact_app = typer.Typer(help="Inspect artifacts and provenance.")
pack_app = typer.Typer(help="Validate, install, inspect, and query trusted OpenCrab Pack directories.")
deliberate_app = typer.Typer(help="Run and audit Autonomous Deliberation D1 decisions.")
deliberate_watch_app = typer.Typer(help="Monitor flip-condition watches (D3).")
deliberate_outcome_app = typer.Typer(
    help="Record and inspect decision outcome ledger entries (usage records, not evidence)."
)
charter_app = typer.Typer(
    help="Manage deliberation charters (sustained concern grouping; not evidence)."
)
app.add_typer(policy_app, name="policy")
app.add_typer(db_app, name="db")
app.add_typer(hypothesis_app, name="hypothesis")
app.add_typer(artifact_app, name="artifact")
app.add_typer(pack_app, name="pack")
app.add_typer(deliberate_app, name="deliberate")
deliberate_app.add_typer(deliberate_watch_app, name="watch")
deliberate_app.add_typer(deliberate_outcome_app, name="outcome")
app.add_typer(charter_app, name="charter")
app.add_typer(eval_app, name="eval")
app.add_typer(gold_app, name="gold")
console = Console()


def _safe_pack_issues(issues: list[dict[str, object]]) -> list[dict[str, object]]:
    """Expose stable validation codes without echoing local paths or source content."""
    return [
        {key: issue[key] for key in ("code", "severity", "record_id") if key in issue} for issue in issues
    ]


def _pack_validation_payload(result: opencrab_packs.PackValidationResult) -> dict[str, object]:
    return {
        "status": result.status,
        "profile": result.profile,
        "pack_id": result.pack_id,
        "declared_version": result.declared_version,
        "format_version": result.format_version,
        "grammar_version": result.grammar_version,
        "source_digest": result.source_digest,
        "digest_verified": result.digest_verified,
        "issues": _safe_pack_issues(result.issues),
    }


def _pack_install_payload(result: opencrab_packs.PackInstallResult) -> dict[str, object]:
    return {
        "status": result.status,
        "pack_id": result.pack_id,
        "declared_version": result.declared_version,
        "source_digest": result.source_digest,
        "pack_install_id": result.pack_install_id,
        "noop": result.noop,
        "admission": {
            key: result.admission_report[key]
            for key in ("profile", "status", "node_count", "edge_count", "evidence_count", "file_count")
            if key in result.admission_report
        },
    }


def _print_pack_json(payload: object) -> None:
    console.file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


_DELIBERATION_HIDDEN_FIELDS = {
    "failure_detail",
    "idempotency_key",
    "prompt_visible_payload_json",
    "raw_record_json",
    "raw_response",
    "response_json",
    "runtime_config_json",
    "policy_snapshot_json",
    "storage_uri",
    "source_uri",
    "asset_ref",
}


def _sanitize_deliberation_value(value: object, *, field_name: str = "") -> object:
    """Remove internal response fields and redact filesystem/storage locations."""
    lowered = field_name.casefold()
    if (
        field_name in _DELIBERATION_HIDDEN_FIELDS
        or lowered.endswith("_path")
        or "secret" in lowered
        or "token" in lowered
        or "api_key" in lowered
        or "password" in lowered
    ):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            key: _sanitize_deliberation_value(item, field_name=key)
            for key, item in value.items()
            if key not in _DELIBERATION_HIDDEN_FIELDS
        }
    if isinstance(value, list):
        return [_sanitize_deliberation_value(item) for item in value]
    if isinstance(value, str):
        for marker in (
            "/private/",
            "/Users/",
            "/var/",
            "/tmp/",
            "file://",
            "storage://",
            "s3://",
            "gs://",
            "az://",
        ):
            if marker in value:
                return "[redacted]"
    return value


def _deliberation_run_payload(run: DeliberationRun) -> dict[str, object]:
    return {
        "id": run.id,
        "parent_run_id": run.parent_run_id,
        "status": run.status,
        "current_stage": run.current_stage,
        "outcome": run.outcome,
        "failure_code": run.failure_code,
        "llm_attempt_count": run.llm_attempt_count,
        "mission_digest": run.mission_digest,
        "policy_digest": run.policy_digest,
        "runtime_config_digest": run.runtime_config_digest,
        "primary_scope_digest": run.primary_scope_digest,
        "impact_baseline_scope_digest": run.impact_baseline_scope_digest,
        "contract_version": run.contract_version,
        "prompt_template_version": run.prompt_template_version,
        "created_at": run.created_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _load_deliberation_mission(path: Path) -> Mission:
    if not path.is_file():
        raise ValueError("mission_file_required")
    try:
        raw = path.read_text(encoding="utf-8")
        payload = yaml.safe_load(raw) if path.suffix.casefold() in {".yaml", ".yml"} else json.loads(raw)
        return Mission.model_validate(payload)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError("mission_invalid") from exc


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
            checks.append(("database", True, _redact_url(settings.db_url)))
            checks.append(("active policy", True, policy.version))
    except Exception as exc:
        checks.append(("database/policy", False, str(exc)))
    if settings.llm_provider == "codex":
        binary = shutil.which(settings.codex_binary)
        checks.append(
            (
                "codex CLI",
                binary is not None,
                binary or f"{settings.codex_binary} not found on PATH",
            )
        )
        config_dir = settings.codex_config_dir
        checks.append(
            ("codex models config", (config_dir / "models.json").is_file(), str(config_dir / "models.json"))
        )
        checks.append(
            (
                "codex pipeline config",
                (config_dir / "pipeline.json").is_file(),
                str(config_dir / "pipeline.json"),
            )
        )
    elif settings.llm_provider == "openai-compatible" and not settings.llm_api_key:
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


def _redact_url(raw_url: str) -> str:
    try:
        url = make_url(raw_url)
    except ValueError:
        return "<invalid database URL>"
    query = {key: "***" if _query_key_is_secret(key) else value for key, value in url.query.items()}
    return url.set(query=query).render_as_string(hide_password=True)


def _query_key_is_secret(key: str) -> bool:
    lowered = key.casefold()
    return any(token in lowered for token in ("password", "passwd", "pwd", "secret", "token", "key"))


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
    provenance: Annotated[
        bool, typer.Option(help="Include task, hypothesis, and evidence provenance.")
    ] = False,
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


@pack_app.command("validate")
def pack_validate(
    path: Annotated[Path, typer.Argument(help="Trusted local OpenCrab Pack directory.")],
    profile: Annotated[
        Literal["compatible", "strict"], typer.Option(help="Admission profile.")
    ] = "compatible",
) -> None:
    """Validate a trusted local Pack directory without modifying it."""
    if not path.is_dir():
        _print_pack_json({"status": "fail", "error": {"code": "pack_directory_required"}})
        raise typer.Exit(code=2)
    result = opencrab_packs.validate_pack_directory(path, profile=profile)
    _print_pack_json(_pack_validation_payload(result))
    if result.status != "pass":
        raise typer.Exit(code=1)


@pack_app.command("install")
def pack_install(
    path: Annotated[Path, typer.Argument(help="Trusted local OpenCrab Pack directory.")],
    profile: Annotated[
        Literal["compatible", "strict"], typer.Option(help="Admission profile.")
    ] = "compatible",
) -> None:
    """Validate and install a trusted local Pack directory."""
    if not path.is_dir():
        _print_pack_json({"status": "fail", "error": {"code": "pack_directory_required"}})
        raise typer.Exit(code=2)
    with _runtime() as (settings, _, factory), factory() as session:
        try:
            result = opencrab_packs.install_pack(
                session,
                path,
                workspace=settings.workspace,
                profile=profile,
            )
            session.commit()
        except opencrab_packs.PackValidationError as exc:
            session.rollback()
            _print_pack_json(
                {
                    "status": "fail",
                    "error": {
                        "code": "pack_validation_failed",
                        "issues": _safe_pack_issues(exc.issues),
                    },
                }
            )
            raise typer.Exit(code=1) from exc
        except opencrab_packs.PackConflictError as exc:
            session.rollback()
            _print_pack_json({"status": "fail", "error": {"code": "pack_revision_conflict"}})
            raise typer.Exit(code=1) from exc
    _print_pack_json(_pack_install_payload(result))


@pack_app.command("list")
def pack_list() -> None:
    """List active Pack installations without exposing filesystem locations."""
    with _runtime() as (_, _, factory), factory() as session:
        installs = opencrab_packs.list_active_installs(session)
        payload = [
            {
                "pack_id": install.pack_id,
                "declared_version": install.declared_version,
                "source_digest": install.source_digest,
                "status": install.status,
                "admission_profile": install.admission_profile,
            }
            for install in installs
        ]
    _print_pack_json(payload)


@pack_app.command("show")
def pack_show(
    pack_id: Annotated[str, typer.Argument(help="Active Pack identifier.")],
) -> None:
    """Show one active Pack installation and its non-sensitive registry metadata."""
    with _runtime() as (_, _, factory), factory() as session:
        install = opencrab_packs.get_active_install(session, pack_id)
        if install is None:
            _print_pack_json({"error": {"code": "pack_not_found"}})
            raise typer.Exit(code=1)
        report = install.admission_report_json or {}
        payload = {
            "pack_id": install.pack_id,
            "declared_version": install.declared_version,
            "format_version": install.format_version,
            "grammar_version": install.grammar_version,
            "source_digest": install.source_digest,
            "source_type": install.source_type,
            "admission_profile": install.admission_profile,
            "status": install.status,
            "counts": {
                key.removesuffix("_count"): report[key]
                for key in ("node_count", "edge_count", "evidence_count", "file_count")
                if key in report
            },
        }
    _print_pack_json(payload)


@pack_app.command("query")
def pack_query(
    question: Annotated[str, typer.Argument(help="Question answered only from active Pack evidence.")],
    packs: Annotated[
        str | None, typer.Option(help="Comma-separated active Pack ids to narrow the scope.")
    ] = None,
    top_k: Annotated[int, typer.Option(min=1, max=100, help="Maximum retrieval hits.")] = 20,
) -> None:
    """Return a grounded answer or fail closed as unknown."""
    pack_ids = [item.strip() for item in (packs or "").split(",") if item.strip()] or None
    with _runtime() as (settings, _, factory), factory() as session:
        answer = pack_answering.answer_pack_query(
            session,
            question,
            provider_from_settings(settings),
            pack_ids=pack_ids,
            top_k=top_k,
        )
    _print_pack_json(
        {
            "status": answer.status,
            "answer": answer.answer,
            "citations": answer.citations,
            "pack_scope": answer.pack_scope,
            "retrieval": answer.retrieval,
            "reason": answer.reason,
        }
    )


def _get_deliberation_run(session: Session, run_id: int) -> DeliberationRun:
    run = session.get(DeliberationRun, run_id)
    if run is None:
        _print_pack_json({"error": {"code": "deliberation_not_found"}})
        raise typer.Exit(code=2)
    return run


@deliberate_app.command("run")
def deliberate_run(
    mission_path: Annotated[Path, typer.Argument(help="Mission JSON or YAML file.")],
    packs: Annotated[str, typer.Option(help="Comma-separated installed Pack IDs.")],
    idempotency_key: Annotated[str, typer.Option(help="Unique key for this deliberation execution.")],
    impact_baseline_packs: Annotated[
        str | None, typer.Option(help="Comma-separated frozen baseline Pack IDs.")
    ] = None,
    allow_compatible_packs: Annotated[
        bool, typer.Option(help="Allow explicitly installed compatible Packs.")
    ] = False,
) -> None:
    """Run one bounded, Pack-grounded Autonomous Deliberation D1 execution."""
    try:
        mission = _load_deliberation_mission(mission_path)
    except ValueError as exc:
        _print_pack_json({"status": "failed_input", "error": {"code": str(exc)}})
        raise typer.Exit(code=2) from exc
    pack_ids = [item.strip() for item in packs.split(",") if item.strip()]
    baseline_ids = [item.strip() for item in (impact_baseline_packs or "").split(",") if item.strip()]
    if not pack_ids or not idempotency_key.strip():
        _print_pack_json({"status": "failed_input", "error": {"code": "scope_or_key_required"}})
        raise typer.Exit(code=2)

    try:
        with _runtime() as (settings, _, factory), factory() as session:
            run = deliberation.run_deliberation(
                session,
                mission,
                pack_ids=pack_ids,
                impact_baseline_pack_ids=baseline_ids,
                idempotency_key=idempotency_key,
                provider=provider_from_settings(settings),
                settings=settings,
                allow_compatible_packs=allow_compatible_packs,
            )
            session.commit()
            payload = _deliberation_run_payload(run)
    except SQLAlchemyError as exc:
        _print_pack_json({"status": "failed_database", "error": {"code": "database_error"}})
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        from .services import charters as charter_service

        if isinstance(exc, charter_service.CharterError):
            _print_pack_json({"status": "failed_input", "error": {"code": exc.code}})
            raise typer.Exit(code=2) from exc
        _print_pack_json({"status": "indeterminate", "error": {"code": "deliberation_failed"}})
        raise typer.Exit(code=1) from exc

    _print_pack_json(payload)
    if run.status == "failed_input":
        raise typer.Exit(code=2)
    if run.status in {"failed_database", "failed_execution", "indeterminate"}:
        raise typer.Exit(code=1)


@deliberate_app.command("continue")
def deliberate_continue(
    parent_run_id: Annotated[int, typer.Argument(min=1, help="Completed abstaining parent run ID.")],
    packs: Annotated[str, typer.Option(help="Comma-separated installed Pack IDs.")],
    fulfills: Annotated[
        str, typer.Option(help="Comma-separated parent Knowledge Request local keys fulfilled by this run.")
    ],
    idempotency_key: Annotated[str, typer.Option(help="Unique key for this continuation execution.")],
    impact_baseline_packs: Annotated[
        str | None, typer.Option(help="Comma-separated frozen baseline Pack IDs.")
    ] = None,
    allow_compatible_packs: Annotated[
        bool, typer.Option(help="Allow explicitly installed compatible Packs.")
    ] = False,
) -> None:
    """Continue a completed abstention after fulfilling its knowledge requests."""
    pack_ids = [item.strip() for item in packs.split(",") if item.strip()]
    fulfilled_keys = [item.strip() for item in fulfills.split(",") if item.strip()]
    baseline_ids = [item.strip() for item in (impact_baseline_packs or "").split(",") if item.strip()]
    if not pack_ids or not fulfilled_keys or not idempotency_key.strip():
        _print_pack_json({"status": "failed_input", "error": {"code": "scope_or_key_required"}})
        raise typer.Exit(code=2)

    try:
        with _runtime() as (settings, _, factory), factory() as session:
            run = deliberation.continue_deliberation(
                session,
                parent_run_id,
                pack_ids,
                baseline_ids,
                fulfilled_keys,
                idempotency_key,
                provider_from_settings(settings),
                settings=settings,
                allow_compatible_packs=allow_compatible_packs,
            )
            session.commit()
            payload = _deliberation_run_payload(run)
    except deliberation.DeliberationContinuationError as exc:
        _print_pack_json({"status": "failed_input", "error": {"code": exc.code}})
        raise typer.Exit(code=2) from exc
    except SQLAlchemyError as exc:
        _print_pack_json({"status": "failed_database", "error": {"code": "database_error"}})
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        from .services import charters as charter_service

        if isinstance(exc, charter_service.CharterError):
            _print_pack_json({"status": "failed_input", "error": {"code": exc.code}})
            raise typer.Exit(code=2) from exc
        _print_pack_json({"status": "indeterminate", "error": {"code": "deliberation_failed"}})
        raise typer.Exit(code=1) from exc

    _print_pack_json(payload)
    if run.status == "failed_input":
        raise typer.Exit(code=2)
    if run.status in {"failed_database", "failed_execution", "indeterminate"}:
        raise typer.Exit(code=1)


@deliberate_app.command("show")
def deliberate_show(run_id: Annotated[int, typer.Argument(min=1)]) -> None:
    """Show safe execution metadata for one deliberation run."""
    with _runtime() as (_, _, factory), factory() as session:
        _print_pack_json(_deliberation_run_payload(_get_deliberation_run(session, run_id)))


@deliberate_app.command("dossier")
def deliberate_dossier(
    run_id: Annotated[int, typer.Argument(min=1)],
    format: Annotated[Literal["json", "markdown"], typer.Option()] = "json",
) -> None:
    """Return the persisted decision dossier without internal Pack or prompt data."""
    with _runtime() as (_, _, factory), factory() as session:
        _get_deliberation_run(session, run_id)
        dossier = session.scalar(select(DeliberationDossier).where(DeliberationDossier.run_id == run_id))
        if dossier is None:
            _print_pack_json({"error": {"code": "dossier_not_found"}})
            raise typer.Exit(code=1)
        payload: object = dossier.dossier_markdown if format == "markdown" else dossier.dossier_json
    _print_pack_json({"format": format, "dossier": _sanitize_deliberation_value(payload)})


@deliberate_app.command("replay")
def deliberate_replay(run_id: Annotated[int, typer.Argument(min=1)]) -> None:
    """Run deterministic audit replay; this never calls the LLM."""
    try:
        with _runtime() as (_, _, factory), factory() as session:
            _get_deliberation_run(session, run_id)
            replay = replay_deliberation(session, run_id)
            session.commit()
            payload = {
                "run_id": run_id,
                "matched": replay.matched,
                "result": _sanitize_deliberation_value(replay.result_json),
            }
    except SQLAlchemyError as exc:
        _print_pack_json({"status": "failed_database", "error": {"code": "database_error"}})
        raise typer.Exit(code=1) from exc
    _print_pack_json(payload)


@deliberate_app.command("impact")
def deliberate_impact(run_id: Annotated[int, typer.Argument(min=1)]) -> None:
    """Show the persisted citation-scope Cognitive Impact projection."""
    with _runtime() as (_, _, factory), factory() as session:
        _get_deliberation_run(session, run_id)
        impact = session.scalar(
            select(DeliberationCognitiveImpact).where(DeliberationCognitiveImpact.run_id == run_id)
        )
        if impact is None:
            _print_pack_json({"error": {"code": "cognitive_impact_not_found"}})
            raise typer.Exit(code=1)
        payload = _sanitize_deliberation_value(impact.impact_json)
    _print_pack_json(payload)


@deliberate_app.command("transition")
def deliberate_transition(run_id: Annotated[int, typer.Argument(min=1)]) -> None:
    """Show the persisted, sanitized cognitive transition for a continuation run."""
    with _runtime() as (_, _, factory), factory() as session:
        _get_deliberation_run(session, run_id)
        artifact = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run_id,
                DeliberationArtifact.kind == "cognitive_transition",
                DeliberationArtifact.local_key == "cognitive_transition",
            )
        )
        if artifact is None:
            _print_pack_json({"error": {"code": "cognitive_transition_not_found"}})
            raise typer.Exit(code=1)
        payload = _sanitize_deliberation_value(artifact.payload_json)
    _print_pack_json(payload)


@deliberate_app.command("knowledge-requests")
def deliberate_knowledge_requests(
    run_id: Annotated[int, typer.Argument(min=1)],
    format: Annotated[Literal["default", "export"], typer.Option()] = "default",
) -> None:
    """Export inert Knowledge Requests; this command never executes them."""
    from .services.knowledge_request_verifiers import build_knowledge_request_export

    with _runtime() as (_, _, factory), factory() as session:
        run = _get_deliberation_run(session, run_id)
        artifact = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run_id,
                DeliberationArtifact.kind == "knowledge_requests",
            )
        )
        raw = artifact.payload_json if artifact is not None else {"knowledge_requests": []}
        if format == "export":
            items = raw.get("knowledge_requests") if isinstance(raw, dict) else None
            mission = run.mission_snapshot_json if isinstance(run.mission_snapshot_json, dict) else {}
            payload = build_knowledge_request_export(
                run_id=run.id,
                parent_run_id=run.parent_run_id,
                mission_digest=run.mission_digest,
                decision_question=str(mission.get("decision_question") or ""),
                knowledge_requests=list(items) if isinstance(items, list) else [],
            )
        else:
            payload = raw
    _print_pack_json(_sanitize_deliberation_value(payload))


@deliberate_watch_app.command("list")
def deliberate_watch_list(
    status: Annotated[str | None, typer.Option(help="Filter by watch status.")] = None,
    charter: Annotated[
        int | None,
        typer.Option("--charter", help="Filter by mission_charter_id on the parent run."),
    ] = None,
) -> None:
    """List flip-condition watches (D3). Never re-runs deliberation."""
    from .services import flip_monitoring

    try:
        with _runtime() as (_, _, factory), factory() as session:
            watches = flip_monitoring.list_watches(
                session, status=status, mission_charter_id=charter
            )
            payload = {
                "watches": [
                    _sanitize_deliberation_value(flip_monitoring.watch_public_payload(w))
                    for w in watches
                ]
            }
    except flip_monitoring.FlipWatchError as exc:
        _print_pack_json({"error": {"code": exc.code}})
        raise typer.Exit(code=2) from exc
    _print_pack_json(payload)


@deliberate_watch_app.command("show")
def deliberate_watch_show(watch_id: Annotated[int, typer.Argument(min=1)]) -> None:
    """Show one flip-condition watch and its triggers."""
    from .services import flip_monitoring

    with _runtime() as (_, _, factory), factory() as session:
        watch = flip_monitoring.get_watch(session, watch_id)
        if watch is None:
            _print_pack_json({"error": {"code": "watch_not_found"}})
            raise typer.Exit(code=2)
        triggers = flip_monitoring.list_triggers(session, watch_id=watch_id)
        payload = {
            "watch": _sanitize_deliberation_value(flip_monitoring.watch_public_payload(watch)),
            "triggers": [
                _sanitize_deliberation_value(
                    flip_monitoring.trigger_public_payload(trigger, parent)
                )
                for trigger, parent in triggers
            ],
        }
    _print_pack_json(payload)


@deliberate_watch_app.command("scan")
def deliberate_watch_scan(
    pack_install: Annotated[
        int | None,
        typer.Option("--pack-install", help="Pack install ID to scan against watching predicates."),
    ] = None,
) -> None:
    """Manually scan watching flip predicates against a Pack install (deterministic)."""
    from .services import flip_monitoring

    if pack_install is None:
        _print_pack_json({"error": {"code": "pack_install_required"}})
        raise typer.Exit(code=2)
    try:
        with _runtime() as (_, _, factory), factory() as session:
            triggers = flip_monitoring.scan_pack_install(session, pack_install)
            session.commit()
            trigger_payloads = []
            for trigger in triggers:
                watch = flip_monitoring.get_watch(session, trigger.watch_id)
                if watch is None:
                    continue
                trigger_payloads.append(
                    _sanitize_deliberation_value(
                        flip_monitoring.trigger_public_payload(trigger, watch)
                    )
                )
            payload = {
                "pack_install_id": pack_install,
                "triggered": len(triggers),
                "triggers": trigger_payloads,
            }
    except flip_monitoring.FlipWatchError as exc:
        _print_pack_json({"error": {"code": exc.code}})
        raise typer.Exit(code=2) from exc
    except SQLAlchemyError as exc:
        _print_pack_json({"status": "failed_database", "error": {"code": "database_error"}})
        raise typer.Exit(code=1) from exc
    _print_pack_json(payload)


@deliberate_watch_app.command("dismiss")
def deliberate_watch_dismiss(
    watch_id: Annotated[int, typer.Argument(min=1)],
    reason: Annotated[str, typer.Option(help="Audit reason for dismissing this watch.")],
) -> None:
    """Dismiss a flip watch with a required audit reason. Does not re-deliberate."""
    from .services import flip_monitoring

    try:
        with _runtime() as (_, _, factory), factory() as session:
            watch = flip_monitoring.dismiss_watch(session, watch_id, reason=reason)
            session.commit()
            payload = {
                "watch": _sanitize_deliberation_value(flip_monitoring.watch_public_payload(watch))
            }
    except flip_monitoring.FlipWatchError as exc:
        _print_pack_json({"error": {"code": exc.code}})
        raise typer.Exit(code=2) from exc
    except SQLAlchemyError as exc:
        _print_pack_json({"status": "failed_database", "error": {"code": "database_error"}})
        raise typer.Exit(code=1) from exc
    _print_pack_json(payload)


@deliberate_outcome_app.command("record")
def deliberate_outcome_record(
    run_id: Annotated[int, typer.Argument(min=1)],
    label: Annotated[
        str,
        typer.Option(
            "--label",
            help="Outcome label: adopted|adopted_modified|not_adopted|reversed|expired",
        ),
    ],
    scenario: Annotated[
        list[str] | None,
        typer.Option(
            "--scenario",
            help="Scenario assessment as key=status (e.g. expected=materialized).",
        ),
    ] = None,
    abstention: Annotated[
        str | None,
        typer.Option(
            "--abstention",
            help="Abstention assessment: abstention_was_right|information_arrived_late|should_have_selected",
        ),
    ] = None,
    note: Annotated[str | None, typer.Option("--note", help="Optional free-text note.")] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option("--idempotency-key", help="Optional idempotency key for safe retries."),
    ] = None,
    noted_by: Annotated[
        str,
        typer.Option("--noted-by", help="Who recorded this outcome."),
    ] = "user",
) -> None:
    """Append one outcome ledger entry for a completed run (usage record, not evidence)."""
    from .services import outcome_ledger

    try:
        with _runtime() as (_, _, factory), factory() as session:
            row = outcome_ledger.record_outcome(
                session,
                run_id,
                outcome_label=label,
                scenario_assessments=list(scenario or []),
                abstention_assessment=abstention,
                note=note,
                noted_by=noted_by,
                idempotency_key=idempotency_key,
            )
            session.commit()
            payload = {
                "outcome": _sanitize_deliberation_value(
                    outcome_ledger.outcome_public_payload(row)
                )
            }
    except outcome_ledger.OutcomeLedgerError as exc:
        _print_pack_json({"error": {"code": exc.code}})
        raise typer.Exit(code=2) from exc
    except SQLAlchemyError as exc:
        _print_pack_json({"status": "failed_database", "error": {"code": "database_error"}})
        raise typer.Exit(code=1) from exc
    _print_pack_json(payload)


@deliberate_outcome_app.command("show")
def deliberate_outcome_show(run_id: Annotated[int, typer.Argument(min=1)]) -> None:
    """List append-only outcome ledger entries for one run."""
    from .services import outcome_ledger

    with _runtime() as (_, _, factory), factory() as session:
        rows = outcome_ledger.list_outcomes(session, run_id)
        payload = {
            "run_id": run_id,
            "outcomes": [
                _sanitize_deliberation_value(outcome_ledger.outcome_public_payload(row))
                for row in rows
            ],
        }
    _print_pack_json(payload)


@deliberate_app.command("calibration")
def deliberate_calibration(
    since: Annotated[
        str | None,
        typer.Option("--since", help="Only outcomes noted at or after this ISO date."),
    ] = None,
    charter: Annotated[
        int | None,
        typer.Option("--charter", help="Filter by mission_charter_id from mission snapshot."),
    ] = None,
) -> None:
    """Deterministic calibration aggregates from the outcome ledger (no LLM)."""
    from datetime import datetime

    from .services import outcome_ledger

    since_dt = None
    if since is not None and since.strip():
        try:
            since_dt = datetime.fromisoformat(since.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            _print_pack_json({"error": {"code": "invalid_since_date"}})
            raise typer.Exit(code=2) from exc
    try:
        with _runtime() as (_, _, factory), factory() as session:
            report = outcome_ledger.calibration_report(
                session,
                since=since_dt,
                mission_charter_id=charter,
            )
    except Exception as exc:
        from .services import charters as charter_service

        if isinstance(exc, charter_service.CharterError):
            _print_pack_json({"error": {"code": exc.code}})
            raise typer.Exit(code=2) from exc
        raise
    _print_pack_json(_sanitize_deliberation_value(report))


@charter_app.command("create")
def charter_create(
    title: Annotated[str, typer.Option("--title", help="Required charter title.")],
    description: Annotated[
        str | None, typer.Option("--description", help="Optional description.")
    ] = None,
) -> None:
    """Create an active deliberation charter (control-plane grouping only)."""
    from .services import charters

    try:
        with _runtime() as (_, _, factory), factory() as session:
            row = charters.create_charter(session, title=title, description=description)
            session.commit()
            payload = {
                "charter": _sanitize_deliberation_value(charters.charter_public_payload(row))
            }
    except charters.CharterError as exc:
        _print_pack_json({"error": {"code": exc.code}})
        raise typer.Exit(code=2) from exc
    except SQLAlchemyError as exc:
        _print_pack_json({"error": {"code": "database_error"}})
        raise typer.Exit(code=1) from exc
    _print_pack_json(payload)


@charter_app.command("list")
def charter_list(
    status: Annotated[
        str | None,
        typer.Option("--status", help="Filter by status: active or archived."),
    ] = None,
) -> None:
    """List deliberation charters."""
    from .services import charters

    try:
        with _runtime() as (_, _, factory), factory() as session:
            rows = charters.list_charters(session, status=status)
            payload = {
                "charters": [
                    _sanitize_deliberation_value(charters.charter_public_payload(row))
                    for row in rows
                ]
            }
    except charters.CharterError as exc:
        _print_pack_json({"error": {"code": exc.code}})
        raise typer.Exit(code=2) from exc
    _print_pack_json(payload)


@charter_app.command("show")
def charter_show(charter_id: Annotated[int, typer.Argument(min=1)]) -> None:
    """Show one deliberation charter."""
    from .services import charters

    with _runtime() as (_, _, factory), factory() as session:
        row = charters.get_charter(session, charter_id)
        if row is None:
            _print_pack_json({"error": {"code": charters.ERROR_UNKNOWN_CHARTER}})
            raise typer.Exit(code=2)
        payload = {
            "charter": _sanitize_deliberation_value(charters.charter_public_payload(row))
        }
    _print_pack_json(payload)


@charter_app.command("archive")
def charter_archive(charter_id: Annotated[int, typer.Argument(min=1)]) -> None:
    """Archive a charter (soft-delete; no hard delete)."""
    from .services import charters

    try:
        with _runtime() as (_, _, factory), factory() as session:
            row = charters.archive_charter(session, charter_id)
            session.commit()
            payload = {
                "charter": _sanitize_deliberation_value(charters.charter_public_payload(row))
            }
    except charters.CharterError as exc:
        _print_pack_json({"error": {"code": exc.code}})
        raise typer.Exit(code=2) from exc
    except SQLAlchemyError as exc:
        _print_pack_json({"error": {"code": "database_error"}})
        raise typer.Exit(code=1) from exc
    _print_pack_json(payload)


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
