from __future__ import annotations

import html
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from .. import __version__
from ..config import Settings, get_settings
from ..connectors.http import fetch_url
from ..database import init_db, make_engine, make_session_factory
from ..events import bus
from ..loops.supervisor import Supervisor
from ..models import (
    Artifact,
    ArtifactFeedback,
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
from ..services.inspection import artifact_provenance, hypothesis_evidence


class UrlIngestRequest(BaseModel):
    url: HttpUrl


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

    @application.post("/v1/premise-review", dependencies=[Depends(_write_authorised)])
    def request_premise_review(session: Session = Depends(get_session)) -> dict[str, int]:
        emission = bus.emit(
            session,
            "premise.review_requested",
            {"reason": "manual API request"},
            source_loop="api",
        )
        session.commit()
        return {"event_id": emission.event.id}

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
