from __future__ import annotations

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..database import init_db, make_engine, make_session_factory
from ..events import bus
from ..llm import LLMProvider
from ..loops.execution import ExecutionLoop
from ..loops.extraction import ExtractionLoop
from ..loops.hypothesis import HypothesisLoop
from ..loops.planning import PlanningLoop
from ..loops.supervisor import Supervisor
from ..models import Chunk, Document, EvidenceEdge, Hypothesis
from ..policies import ensure_default_policy
from ..utils import sha256_text, stable_hash
from .evaluation_common import load_documents, most_common, safe_div
from .evaluation_models import CounterAuditDetail, CounterEvalReport, GoldDocument
from .prompts import build_counter_audit_prompt


def evaluate_counter_evidence(
    provider: LLMProvider,
    *,
    docs_dir: Path,
    policy: dict[str, Any],
    cycles: int = 1,
) -> CounterEvalReport:
    documents = load_documents(docs_dir)
    provider_name = getattr(provider, "name", provider.__class__.__name__)
    with TemporaryDirectory(prefix="openoyster-counter-") as temp_dir:
        temp_path = Path(temp_dir)
        settings = Settings(
            db_url=f"sqlite:///{temp_path / 'counter.db'}",
            workspace=temp_path / "workspace",
            llm_provider="stub",
            codex_batch_size=get_settings().codex_batch_size,
        )
        settings.ensure_workspace()
        engine = make_engine(settings)
        try:
            init_db(engine)
            factory = make_session_factory(engine)
            _ingest_gold_documents(factory, documents, policy, settings)
            supervisor = Supervisor(
                session_factory=factory,
                settings=settings,
                loops=[
                    ExtractionLoop(settings, provider),
                    HypothesisLoop(settings, provider),
                    PlanningLoop(settings),
                    ExecutionLoop(settings, provider),
                ],
            )
            try:
                supervisor.run_cycles(cycles=cycles, sleep_seconds=0.0)
            finally:
                supervisor.close()
            audits, models = _audit_counter_edges(factory, provider)
        finally:
            engine.dispose()

    if not audits:
        return CounterEvalReport(
            provider=provider_name,
            model=None,
            cycles=cycles,
            docs_ingested=len(documents),
            oppose_edges=0,
            audited_edges=0,
            precision=None,
            measurable=False,
            status="0건 — 측정 불가",
            audit_model_note=_audit_note(),
        )
    true_count = sum(1 for audit in audits if audit.contradicts)
    return CounterEvalReport(
        provider=provider_name,
        model=most_common(models),
        cycles=cycles,
        docs_ingested=len(documents),
        oppose_edges=len(audits),
        audited_edges=len(audits),
        precision=safe_div(true_count, len(audits)),
        measurable=True,
        status="measured",
        audit_model_note=_audit_note(),
        audits=audits,
    )


def _ingest_gold_documents(
    factory: sessionmaker[Session],
    documents: list[GoldDocument],
    policy: dict[str, Any],
    settings: Settings,
) -> None:
    with factory() as session:
        policy_record = ensure_default_policy(session, settings)
        policy_record.policy_json = policy
        for document in documents:
            record = Document(
                source=document.source,
                source_uri=document.url,
                title=document.title,
                content_hash=sha256_text(document.text),
                ingest_key=stable_hash("goldset-counter", document.id),
                raw_text=document.text,
                status="fetched",
                metadata_json={"gold_doc_id": document.id, "language": document.language},
            )
            session.add(record)
            session.flush()
            bus.emit(
                session,
                "doc.fetched",
                {"document_id": record.id, "gold_doc_id": document.id},
                source_loop="goldset_eval",
                idempotency_key=f"goldset.eval.doc.fetched:{record.id}",
            )
        session.commit()


def _audit_counter_edges(
    factory: sessionmaker[Session],
    provider: LLMProvider,
) -> tuple[list[CounterAuditDetail], Counter[str]]:
    audits: list[CounterAuditDetail] = []
    models: Counter[str] = Counter()
    with factory() as session:
        rows = list(
            session.execute(
                select(EvidenceEdge, Hypothesis, Chunk)
                .join(Hypothesis, EvidenceEdge.hypothesis_id == Hypothesis.id)
                .outerjoin(Chunk, EvidenceEdge.chunk_id == Chunk.id)
                .where(EvidenceEdge.stance == "oppose")
                .order_by(EvidenceEdge.id.asc())
            )
        )
        for edge, hypothesis, chunk in rows:
            quote = str((edge.metadata_json or {}).get("quoted_evidence") or edge.summary)
            prompt = build_counter_audit_prompt(
                hypothesis_claim=hypothesis.claim,
                evidence_quote=quote,
                evidence_summary=edge.summary,
                source_text=chunk.text if chunk else "",
            )
            response = provider.query_json(prompt, "gold_label")
            model = response.get("model")
            if isinstance(model, str):
                models[model] += 1
            audits.append(
                CounterAuditDetail(
                    evidence_edge_id=edge.id,
                    hypothesis_id=hypothesis.id,
                    contradicts=response.get("contradicts") is True,
                    reasoning=str(response.get("reasoning", "")),
                    quoted_evidence=quote,
                )
            )
    return audits, models

def _audit_note() -> str:
    return (
        "Single-model policy (gpt-5.6-sol): judge, verifier, and auditor share one model and are separated "
        "only by role prompts and reasoning effort — treat precision as self-consistency, not independent "
        "confirmation."
    )
