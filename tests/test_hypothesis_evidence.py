from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openoyster.events import bus
from openoyster.llm import ExtractionUnavailable, StubProvider
from openoyster.loops.hypothesis import HypothesisLoop
from openoyster.models import Chunk, Document, EvidenceEdge
from openoyster.policies import ensure_default_policy
from openoyster.utils import sha256_text, stable_hash

_CLAIM = "Acme constraints limit adoption."
_SUMMARY = "Acme adoption remains constrained."


class RecordingStubProvider(StubProvider):
    def __init__(self) -> None:
        self.stages: list[str] = []

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.stages.append(stage)
        return super().query_json(prompt, stage)


class UnavailableOpposeVerifyProvider(RecordingStubProvider):
    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.stages.append(stage)
        match stage:
            case "oppose_verify":
                raise ExtractionUnavailable("oppose verifier unavailable")
            case _:
                return StubProvider.query_json(self, prompt, stage)


def _emit_oppose_candidate(session: Session, source_text: str) -> None:
    document = Document(
        source="rss",
        source_uri="https://example.com/acme-constraints",
        title="Acme constraints",
        content_hash=sha256_text(source_text),
        ingest_key=stable_hash("rss", source_text),
        raw_text=source_text,
        status="processed",
    )
    session.add(document)
    session.flush()
    chunk = Chunk(
        document_id=document.id,
        chunk_index=0,
        text=source_text,
        text_hash=sha256_text(source_text),
        status="processed",
    )
    session.add(chunk)
    session.flush()
    bus.emit(
        session,
        "hypothesis.candidate_created",
        {
            "document_id": document.id,
            "chunk_id": chunk.id,
            "hypothesis": {
                "claim": _CLAIM,
                "scope": "Acme",
                "confidence": 0.6,
                "evidence_signal_summary": _SUMMARY,
                "stance": "oppose",
                "quoted_evidence": "Acme constraints limit adoption.",
            },
        },
    )


def test_hypothesis_loop_persists_oppose_extraction_when_verifier_approves(session_factory):
    # Given
    provider = RecordingStubProvider()
    with session_factory() as session:
        ensure_default_policy(session)
        _emit_oppose_candidate(session, "Acme constraints limit adoption. The report documents the limit.")
        session.commit()

        # When
        HypothesisLoop(provider=provider).run(session)
        session.commit()

    # Then
    with session_factory() as session:
        edge = session.scalar(select(EvidenceEdge))
        assert edge is not None
        assert edge.stance == "oppose"
        assert provider.stages.count("oppose_verify") == 1


def test_hypothesis_loop_downgrades_rejected_oppose_extraction_to_neutral(session_factory):
    # Given
    provider = RecordingStubProvider()
    with session_factory() as session:
        ensure_default_policy(session)
        _emit_oppose_candidate(
            session,
            "Acme constraints limit adoption. The source carries the verifier marker VERIFY_REJECT.",
        )
        session.commit()

        # When
        HypothesisLoop(provider=provider).run(session)
        session.commit()

    # Then
    with session_factory() as session:
        edge = session.scalar(select(EvidenceEdge))
        oppose_count = session.scalar(
            select(func.count(EvidenceEdge.id)).where(EvidenceEdge.stance == "oppose")
        )
        assert edge is not None
        assert edge.stance == "neutral"
        assert edge.metadata_json["oppose_rejected_by_verifier"] is True
        assert edge.metadata_json["verifier_reasoning"] == (
            "deterministic stub oppose verifier from prompt marker"
        )
        assert oppose_count == 0
        assert provider.stages.count("oppose_verify") == 1


def test_hypothesis_loop_downgrades_oppose_extraction_when_verifier_unavailable(session_factory):
    # Given
    provider = UnavailableOpposeVerifyProvider()
    with session_factory() as session:
        ensure_default_policy(session)
        _emit_oppose_candidate(session, "Acme constraints limit adoption. The report documents the limit.")
        session.commit()

        # When
        HypothesisLoop(provider=provider).run(session)
        session.commit()

    # Then
    with session_factory() as session:
        edge = session.scalar(select(EvidenceEdge))
        oppose_count = session.scalar(
            select(func.count(EvidenceEdge.id)).where(EvidenceEdge.stance == "oppose")
        )
        assert edge is not None
        assert edge.stance == "neutral"
        assert edge.metadata_json["oppose_verify_unavailable"] is True
        assert oppose_count == 0
        assert provider.stages.count("oppose_verify") == 1
