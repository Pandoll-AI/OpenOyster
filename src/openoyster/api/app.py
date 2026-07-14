from __future__ import annotations

import html
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from hmac import compare_digest
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from .. import __version__
from ..config import Settings, get_settings
from ..connectors.http import fetch_url
from ..database import init_db, make_engine, make_session_factory
from ..deliberation_contracts import Mission
from ..events import bus
from ..llm import provider_from_settings
from ..loops.supervisor import Supervisor
from ..models import (
    Artifact,
    ArtifactFeedback,
    DeliberationArtifact,
    DeliberationCognitiveImpact,
    DeliberationDossier,
    DeliberationRun,
    Document,
    Event,
    EvidenceEdge,
    Hypothesis,
    LoopRun,
    Policy,
    Signal,
    Task,
)
from ..policies import (
    ensure_default_mission,
    ensure_default_policy,
    get_active_policy,
    promote_policy,
)
from ..schemas import (
    ArtifactFeedbackIn,
    ArtifactFeedbackOut,
    ArtifactOut,
    DocumentOut,
    EventOut,
    HypothesisOut,
    LoopRunOut,
    PolicyOut,
    TaskOut,
)
from ..services import deliberation, opencrab_packs, pack_answering
from ..services.deliberation_replay import replay_deliberation
from ..services.inspection import artifact_provenance, hypothesis_evidence


class UrlIngestRequest(BaseModel):
    url: HttpUrl


class PackPathRequest(BaseModel):
    path: str
    profile: Literal["compatible", "strict"] = "compatible"


class PackQueryRequest(BaseModel):
    question: str
    packs: list[str] | None = None
    top_k: int = Field(default=20, ge=1, le=100)


class DeliberationCreateRequest(BaseModel):
    """Public request for a bounded Autonomous Deliberation D1 run."""

    mission: Mission
    packs: list[str] = Field(min_length=1)
    impact_baseline_packs: list[str] | None = None
    allow_compatible_packs: bool = False

    model_config = {"extra": "forbid"}


class DeliberationContinueRequest(BaseModel):
    """Public request for a linked D1 re-deliberation."""

    packs: list[str] = Field(min_length=1)
    impact_baseline_packs: list[str] | None = None
    fulfilled_knowledge_request_keys: list[str] = Field(min_length=1)
    allow_compatible_packs: bool = False

    model_config = {"extra": "forbid"}


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
    """Keep D1 public responses free of Pack bodies and local runtime details."""
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


def _safe_pack_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep API errors stable without echoing local paths, source text, or secrets."""
    return [
        {key: issue[key] for key in ("code", "severity", "record_id") if key in issue} for issue in issues
    ]


def _pack_validation_payload(result: opencrab_packs.PackValidationResult) -> dict[str, Any]:
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


def _pack_install_payload(result: opencrab_packs.PackInstallResult) -> dict[str, Any]:
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


def _trusted_pack_directory(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_dir():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "pack_directory_required"},
        )
    return candidate


def _write_authorised(request: Request) -> None:
    settings: Settings = request.app.state.settings
    supplied = request.headers.get(settings.api_key_header)
    if settings.api_key:
        if supplied != settings.api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"A valid {settings.api_key_header} header is required.",
            )
    elif not settings.api_allow_unsafe_no_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Write API is disabled until OPENOYSTER_API_KEY is configured.",
        )


def _deliberation_authorised(request: Request) -> None:
    """D1 is always key-protected, even when legacy write routes are relaxed."""
    settings: Settings = request.app.state.settings
    supplied = request.headers.get(settings.api_key_header)
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "deliberation_api_key_not_configured"},
        )
    if supplied is None or not compare_digest(supplied, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "deliberation_api_key_required"},
        )


def create_app(
    *,
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    owned_engine = None
    if session_factory is None:
        owned_engine = make_engine(runtime_settings)
        session_factory = make_session_factory(owned_engine)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if owned_engine is not None:
            init_db(owned_engine)
        with session_factory() as session:
            ensure_default_policy(session, runtime_settings)
            ensure_default_mission(session)
            session.commit()
        yield
        if owned_engine is not None:
            owned_engine.dispose()

    application = FastAPI(
        title="OpenOyster API",
        version=__version__,
        description="Durable signal-hypothesis-action intelligence runtime.",
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.session_factory = session_factory

    def get_session() -> Iterator[Session]:
        with session_factory() as session:
            try:
                yield session
            finally:
                session.close()

    def page_limit(
        limit: Annotated[int, Query(ge=1)] = 50,
    ) -> int:
        return min(limit, runtime_settings.api_max_page_size)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/ready")
    def ready(session: Session = Depends(get_session)) -> dict[str, str]:
        session.execute(text("SELECT 1"))
        get_active_policy(session)
        return {"status": "ready"}

    @application.get("/", response_class=HTMLResponse)
    def dashboard(session: Session = Depends(get_session)) -> str:
        hypotheses = list(
            session.scalars(select(Hypothesis).order_by(Hypothesis.updated_at.desc()).limit(12))
        )
        artifacts = list(session.scalars(select(Artifact).order_by(Artifact.id.desc()).limit(12)))
        counts = {
            "Documents": session.scalar(select(func.count(Document.id))) or 0,
            "Signals": session.scalar(select(func.count(Signal.id))) or 0,
            "Hypotheses": session.scalar(select(func.count(Hypothesis.id))) or 0,
            "Tasks": session.scalar(select(func.count(Task.id))) or 0,
            "Artifacts": session.scalar(select(func.count(Artifact.id))) or 0,
        }
        count_cards = "".join(
            f"<div class='card'><strong>{html.escape(label)}</strong><span>{int(value)}</span></div>"
            for label, value in counts.items()
        )
        hypothesis_rows = (
            "".join(
                (
                    lambda evidence_count, source_diversity: (
                        "<tr>"
                        f"<td>{item.id}</td><td>{item.confidence:.3f}</td>"
                        f"<td>{html.escape(item.status)}</td>"
                        f"<td>{evidence_count}</td><td>{source_diversity}</td>"
                        f"<td>{html.escape(item.claim)}</td>"
                        "</tr>"
                    )
                )(
                    session.scalar(
                        select(func.count(EvidenceEdge.id)).where(EvidenceEdge.hypothesis_id == item.id)
                    )
                    or 0,
                    session.scalar(
                        select(func.count(func.distinct(EvidenceEdge.document_id))).where(
                            EvidenceEdge.hypothesis_id == item.id,
                            EvidenceEdge.document_id.is_not(None),
                        )
                    )
                    or 0,
                )
                for item in hypotheses
            )
            or "<tr><td colspan='6'>No hypotheses yet.</td></tr>"
        )
        artifact_rows = (
            "".join(
                "<tr>"
                f"<td>{item.id}</td><td>{html.escape(item.artifact_type)}</td>"
                f"<td>{html.escape(item.status)}</td>"
                f"<td>{'yes' if item.linked_hypothesis_id or item.linked_task_id else 'no'}</td>"
                f"<td>{html.escape(item.title)}</td>"
                "</tr>"
                for item in artifacts
            )
            or "<tr><td colspan='5'>No artifacts yet.</td></tr>"
        )
        return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>OpenOyster</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:1180px;margin:2rem auto;padding:0 1rem;color:#17202a}}
h1{{margin-bottom:.2rem}} .muted{{color:#667085}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.8rem;margin:1.5rem 0}}
.card{{border:1px solid #d0d5dd;border-radius:10px;padding:1rem;display:flex;justify-content:space-between}} .card span{{font-size:1.4rem}}
table{{border-collapse:collapse;width:100%;margin-bottom:2rem}} th,td{{border-bottom:1px solid #eaecf0;padding:.65rem;text-align:left;vertical-align:top}} th{{background:#f8fafc}}
code{{background:#f2f4f7;padding:.1rem .3rem;border-radius:4px}}
</style></head>
<body><h1>OpenOyster</h1><p class='muted'>Read-only operational dashboard. Writes require the API key when configured.</p>
<div class='grid'>{count_cards}</div>
<h2>Recent hypotheses</h2><table><thead><tr><th>ID</th><th>Confidence</th><th>Status</th><th>Evidence</th><th>Sources</th><th>Claim</th></tr></thead><tbody>{hypothesis_rows}</tbody></table>
<h2>Recent artifacts</h2><table><thead><tr><th>ID</th><th>Type</th><th>Status</th><th>Provenance</th><th>Title</th></tr></thead><tbody>{artifact_rows}</tbody></table>
<p class='muted'>Use <code>openoyster run --forever</code> for the worker and <code>/docs</code> for the API.</p></body></html>"""

    @application.get("/v1/status")
    def system_status(session: Session = Depends(get_session)) -> dict[str, Any]:
        active = get_active_policy(session)
        return {
            "counts": {
                "events": session.scalar(select(func.count(Event.id))) or 0,
                "documents": session.scalar(select(func.count(Document.id))) or 0,
                "signals": session.scalar(select(func.count(Signal.id))) or 0,
                "hypotheses": session.scalar(select(func.count(Hypothesis.id))) or 0,
                "tasks": session.scalar(select(func.count(Task.id))) or 0,
                "artifacts": session.scalar(select(func.count(Artifact.id))) or 0,
            },
            "active_policy": active.version,
        }

    def deliberation_run_or_404(session: Session, run_id: int) -> DeliberationRun:
        run = session.get(DeliberationRun, run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "deliberation_not_found"},
            )
        return run

    @application.post(
        "/v1/deliberations",
        dependencies=[Depends(_deliberation_authorised)],
    )
    def create_deliberation(
        payload: DeliberationCreateRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        """Create or return a D1 run for one caller-provided idempotency key."""
        try:
            run = deliberation.run_deliberation(
                session,
                payload.mission,
                pack_ids=payload.packs,
                impact_baseline_pack_ids=payload.impact_baseline_packs,
                idempotency_key=idempotency_key,
                provider=provider_from_settings(runtime_settings),
                settings=runtime_settings,
                allow_compatible_packs=payload.allow_compatible_packs,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "deliberation_execution_failed"},
            ) from None
        if run.status == "failed_input":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": run.failure_code or "deliberation_input_invalid"},
            )
        if run.status == "failed_execution":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": run.failure_code or "deliberation_execution_failed"},
            )
        return _deliberation_run_payload(run)

    @application.post(
        "/v1/deliberations/{run_id}/continue",
        dependencies=[Depends(_deliberation_authorised)],
    )
    def continue_deliberation(
        run_id: int,
        payload: DeliberationContinueRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        """Create or return a linked D1 re-deliberation after an abstention."""
        try:
            run = deliberation.continue_deliberation(
                session,
                parent_run_id=run_id,
                pack_ids=payload.packs,
                impact_baseline_pack_ids=payload.impact_baseline_packs,
                fulfilled_knowledge_request_keys=payload.fulfilled_knowledge_request_keys,
                idempotency_key=idempotency_key,
                provider=provider_from_settings(runtime_settings),
                settings=runtime_settings,
                allow_compatible_packs=payload.allow_compatible_packs,
            )
            session.commit()
        except deliberation.DeliberationContinuationError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": exc.code},
            ) from None
        except Exception:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "deliberation_execution_failed"},
            ) from None
        if run.status == "failed_input":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": run.failure_code or "deliberation_input_invalid"},
            )
        if run.status == "failed_execution":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": run.failure_code or "deliberation_execution_failed"},
            )
        return _deliberation_run_payload(run)

    @application.get(
        "/v1/deliberations/{run_id}",
        dependencies=[Depends(_deliberation_authorised)],
    )
    def get_deliberation(
        run_id: int,
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        return _deliberation_run_payload(deliberation_run_or_404(session, run_id))

    @application.get(
        "/v1/deliberations/{run_id}/dossier",
        dependencies=[Depends(_deliberation_authorised)],
    )
    def get_deliberation_dossier(
        run_id: int,
        format: Literal["json", "markdown"] = "json",
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        deliberation_run_or_404(session, run_id)
        dossier = session.scalar(select(DeliberationDossier).where(DeliberationDossier.run_id == run_id))
        if dossier is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "dossier_not_ready"},
            )
        value: object = dossier.dossier_markdown if format == "markdown" else dossier.dossier_json
        return {"format": format, "dossier": _sanitize_deliberation_value(value)}

    @application.post(
        "/v1/deliberations/{run_id}/replay",
        dependencies=[Depends(_deliberation_authorised)],
    )
    def replay_deliberation_run(
        run_id: int,
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        deliberation_run_or_404(session, run_id)
        try:
            replay = replay_deliberation(session, run_id)
            session.commit()
        except ValueError:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "replay_not_ready"},
            ) from None
        except Exception:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "deliberation_replay_failed"},
            ) from None
        return {
            "run_id": run_id,
            "matched": replay.matched,
            "result": _sanitize_deliberation_value(replay.result_json),
        }

    @application.get(
        "/v1/deliberations/{run_id}/cognitive-impact",
        dependencies=[Depends(_deliberation_authorised)],
    )
    def get_deliberation_cognitive_impact(
        run_id: int,
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        deliberation_run_or_404(session, run_id)
        impact = session.scalar(
            select(DeliberationCognitiveImpact).where(DeliberationCognitiveImpact.run_id == run_id)
        )
        if impact is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "cognitive_impact_not_ready"},
            )
        return _sanitize_deliberation_value(impact.impact_json)  # type: ignore[return-value]

    @application.get(
        "/v1/deliberations/{run_id}/knowledge-requests",
        dependencies=[Depends(_deliberation_authorised)],
    )
    def get_deliberation_knowledge_requests(
        run_id: int,
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        deliberation_run_or_404(session, run_id)
        artifact = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run_id,
                DeliberationArtifact.kind == "knowledge_requests",
            )
        )
        payload = artifact.payload_json if artifact is not None else {"knowledge_requests": []}
        return _sanitize_deliberation_value(payload)  # type: ignore[return-value]

    @application.get(
        "/v1/deliberations/{run_id}/transition",
        dependencies=[Depends(_deliberation_authorised)],
    )
    def get_deliberation_transition(
        run_id: int,
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        deliberation_run_or_404(session, run_id)
        artifact = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run_id,
                DeliberationArtifact.kind == "cognitive_transition",
                DeliberationArtifact.local_key == "cognitive_transition",
            )
        )
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "cognitive_transition_not_ready"},
            )
        return _sanitize_deliberation_value(artifact.payload_json)  # type: ignore[return-value]

    @application.post("/v1/run-cycle", dependencies=[Depends(_write_authorised)])
    def run_cycle() -> dict[str, Any]:
        supervisor = Supervisor(
            session_factory=session_factory,
            settings=runtime_settings,
        )
        return {"results": Supervisor.serialise(supervisor.run_cycle())}

    @application.post("/v1/ingest-url", dependencies=[Depends(_write_authorised)])
    def ingest_url(
        payload: UrlIngestRequest,
        session: Session = Depends(get_session),
    ) -> DocumentOut:
        parsed = fetch_url(
            str(payload.url),
            max_bytes=runtime_settings.max_file_bytes,
            timeout_seconds=runtime_settings.llm_timeout_seconds,
        )
        existing = session.scalar(select(Document).where(Document.ingest_key == parsed.ingest_key))
        if existing:
            return DocumentOut.model_validate(existing)
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
            source_loop="api",
            idempotency_key=f"doc.fetched:{document.id}",
        )
        session.commit()
        session.refresh(document)
        return DocumentOut.model_validate(document)

    @application.post("/v1/packs/validate", dependencies=[Depends(_write_authorised)])
    def validate_pack(payload: PackPathRequest) -> dict[str, Any]:
        """Validate a trusted local directory behind the write-auth boundary."""
        directory = _trusted_pack_directory(payload.path)
        try:
            result = opencrab_packs.validate_pack_directory(directory, profile=payload.profile)
        except opencrab_packs.PackValidationError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "pack_validation_failed"},
            ) from None
        if result.status != "pass":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "pack_validation_failed",
                    "issues": _safe_pack_issues(result.issues),
                },
            )
        return _pack_validation_payload(result)

    @application.post("/v1/packs/install", dependencies=[Depends(_write_authorised)])
    def install_pack(
        payload: PackPathRequest,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        """Write-authorized admission for one trusted local directory only."""
        directory = _trusted_pack_directory(payload.path)
        try:
            result = opencrab_packs.install_pack(
                session,
                directory,
                workspace=runtime_settings.workspace,
                profile=payload.profile,
            )
            session.commit()
        except opencrab_packs.PackValidationError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "pack_validation_failed",
                    "issues": _safe_pack_issues(exc.issues),
                },
            ) from None
        except opencrab_packs.PackConflictError:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "pack_revision_conflict"},
            ) from None
        return _pack_install_payload(result)

    @application.get("/v1/packs")
    def list_packs(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        """List active Packs without their local source or storage paths."""
        return [
            {
                "pack_id": install.pack_id,
                "declared_version": install.declared_version,
                "source_digest": install.source_digest,
                "status": install.status,
                "admission_profile": install.admission_profile,
            }
            for install in opencrab_packs.list_active_installs(session)
        ]

    @application.get("/v1/packs/{pack_id}")
    def show_pack(
        pack_id: str,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        install = opencrab_packs.get_active_install(session, pack_id)
        if install is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pack not found")
        report = install.admission_report_json or {}
        return {
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

    @application.post("/v1/packs/query", dependencies=[Depends(_write_authorised)])
    def query_pack(
        payload: PackQueryRequest,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        question = payload.question.strip()
        if not question:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "pack_question_required"},
            )
        answer = pack_answering.answer_pack_query(
            session,
            question,
            provider_from_settings(runtime_settings),
            pack_ids=payload.packs or None,
            top_k=payload.top_k,
        )
        return {
            "status": answer.status,
            "answer": answer.answer,
            "citations": answer.citations,
            "pack_scope": answer.pack_scope,
            "retrieval": answer.retrieval,
            "reason": answer.reason,
        }

    @application.get("/v1/events", response_model=list[EventOut])
    def list_events(
        limit: int = Depends(page_limit),
        offset: Annotated[int, Query(ge=0)] = 0,
        session: Session = Depends(get_session),
    ) -> list[Event]:
        return list(session.scalars(select(Event).order_by(Event.id.desc()).offset(offset).limit(limit)))

    @application.get("/v1/documents", response_model=list[DocumentOut])
    def list_documents(
        limit: int = Depends(page_limit),
        offset: Annotated[int, Query(ge=0)] = 0,
        session: Session = Depends(get_session),
    ) -> list[Document]:
        return list(
            session.scalars(select(Document).order_by(Document.id.desc()).offset(offset).limit(limit))
        )

    @application.get("/v1/hypotheses", response_model=list[HypothesisOut])
    def list_hypotheses(
        limit: int = Depends(page_limit),
        offset: Annotated[int, Query(ge=0)] = 0,
        session: Session = Depends(get_session),
    ) -> list[Hypothesis]:
        return list(
            session.scalars(
                select(Hypothesis).order_by(Hypothesis.updated_at.desc()).offset(offset).limit(limit)
            )
        )

    @application.get("/v1/hypotheses/{hypothesis_id}", response_model=HypothesisOut)
    def get_hypothesis(
        hypothesis_id: int,
        session: Session = Depends(get_session),
    ) -> Hypothesis:
        hypothesis = session.get(Hypothesis, hypothesis_id)
        if not hypothesis:
            raise HTTPException(status_code=404, detail="Hypothesis not found")
        return hypothesis

    @application.get("/v1/hypotheses/{hypothesis_id}/evidence")
    def get_hypothesis_evidence(
        hypothesis_id: int,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        hypothesis = session.get(Hypothesis, hypothesis_id)
        if not hypothesis:
            raise HTTPException(status_code=404, detail="Hypothesis not found")
        return hypothesis_evidence(session, hypothesis)

    @application.get("/v1/tasks", response_model=list[TaskOut])
    def list_tasks(
        limit: int = Depends(page_limit),
        offset: Annotated[int, Query(ge=0)] = 0,
        session: Session = Depends(get_session),
    ) -> list[Task]:
        return list(session.scalars(select(Task).order_by(Task.id.desc()).offset(offset).limit(limit)))

    @application.get("/v1/artifacts", response_model=list[ArtifactOut])
    def list_artifacts(
        limit: int = Depends(page_limit),
        offset: Annotated[int, Query(ge=0)] = 0,
        session: Session = Depends(get_session),
    ) -> list[Artifact]:
        return list(
            session.scalars(select(Artifact).order_by(Artifact.id.desc()).offset(offset).limit(limit))
        )

    @application.get("/v1/artifacts/{artifact_id}", response_model=ArtifactOut)
    def get_artifact(
        artifact_id: int,
        session: Session = Depends(get_session),
    ) -> Artifact:
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return artifact

    @application.get("/v1/artifacts/{artifact_id}/provenance")
    def get_artifact_provenance(
        artifact_id: int,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return artifact_provenance(session, artifact)

    @application.post(
        "/v1/artifacts/{artifact_id}/feedback",
        response_model=ArtifactFeedbackOut,
        dependencies=[Depends(_write_authorised)],
    )
    def add_feedback(
        artifact_id: int,
        payload: ArtifactFeedbackIn,
        session: Session = Depends(get_session),
    ) -> ArtifactFeedback:
        artifact = session.get(Artifact, artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="Artifact not found")
        feedback = ArtifactFeedback(artifact_id=artifact.id, **payload.model_dump())
        session.add(feedback)
        session.flush()
        bus.emit(
            session,
            "artifact.feedback.recorded",
            {
                "artifact_id": artifact.id,
                "feedback_id": feedback.id,
                "verdict": feedback.verdict,
            },
            source_loop="api",
            idempotency_key=f"artifact.feedback:{feedback.id}",
        )
        session.commit()
        session.refresh(feedback)
        return feedback

    @application.get("/v1/policies", response_model=list[PolicyOut])
    def list_policies(session: Session = Depends(get_session)) -> list[Policy]:
        return list(session.scalars(select(Policy).order_by(Policy.id.desc())))

    @application.post(
        "/v1/policies/{policy_id}/promote",
        response_model=PolicyOut,
        dependencies=[Depends(_write_authorised)],
    )
    def manual_policy_promotion(
        policy_id: int,
        session: Session = Depends(get_session),
    ) -> Policy:
        policy = session.get(Policy, policy_id)
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        promote_policy(session, policy)
        bus.emit(
            session,
            "policy.promoted",
            {"policy_id": policy.id, "version": policy.version, "manual": True},
            source_loop="api",
            idempotency_key=f"policy.promoted:{policy.id}",
        )
        session.commit()
        session.refresh(policy)
        return policy

    @application.get("/v1/loop-runs", response_model=list[LoopRunOut])
    def list_loop_runs(
        limit: int = Depends(page_limit),
        offset: Annotated[int, Query(ge=0)] = 0,
        session: Session = Depends(get_session),
    ) -> list[LoopRun]:
        return list(session.scalars(select(LoopRun).order_by(LoopRun.id.desc()).offset(offset).limit(limit)))

    return application


app = create_app()
