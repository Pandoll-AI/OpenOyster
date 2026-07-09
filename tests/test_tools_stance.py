from __future__ import annotations

from typing import Any

import pytest

from openoyster.llm import ExtractionUnavailable, LLMProvider, StubProvider
from openoyster.models import Chunk, Document, Hypothesis
from openoyster.policies import DEFAULT_POLICY
from openoyster.schemas import TextAnalysis
from openoyster.services.tools import run_tool
from openoyster.utils import normalise_text, sha256_text, stable_hash


class UnavailableStanceProvider(LLMProvider):
    name = "unavailable-stance"

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        del texts, policy
        raise ExtractionUnavailable("stance judge unavailable")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        del prompt, stage
        raise ExtractionUnavailable("stance judge unavailable")


def _document(session, *, title: str, text: str) -> Document:
    document = Document(
        source="rss",
        source_uri=f"https://example.com/{stable_hash(title)}",
        title=title,
        content_hash=sha256_text(text),
        ingest_key=stable_hash(title, text),
        raw_text=text,
        status="processed",
    )
    session.add(document)
    session.flush()
    session.add(
        Chunk(
            document_id=document.id,
            chunk_index=0,
            text=text,
            text_hash=sha256_text(text),
            status="processed",
        )
    )
    session.flush()
    return document


def _hypothesis(session, claim: str) -> Hypothesis:
    hypothesis = Hypothesis(
        claim=claim,
        claim_hash=stable_hash(normalise_text(claim).casefold()),
        scope="Acme",
        confidence=0.5,
        status="active",
        revision=1,
    )
    session.add(hypothesis)
    session.flush()
    return hypothesis


def test_counter_scan_uses_stance_judge_oppose_evidence_for_korean_marker(session_factory):
    with session_factory() as session:
        hypothesis = _hypothesis(session, "모델 품질이 장애물이다")
        _document(session, title="반증", text="모델 품질이 장애물이라는 주장에 반대한다. 감사 결과는 거버넌스 지연을 지목했다.")
        session.commit()

        result = run_tool(
            session,
            task_type="counter_evidence_scan",
            hypothesis=hypothesis,
            policy=DEFAULT_POLICY,
            provider=StubProvider(),
        )

    assert len(result.evidence_candidates) == 1
    candidate = result.evidence_candidates[0]
    assert candidate.stance == "oppose"
    assert "반대한다" in candidate.metadata["quoted_evidence"]
    assert 0.25 <= candidate.strength <= 0.9


def test_counter_scan_discards_non_verbatim_quote(session_factory):
    with session_factory() as session:
        hypothesis = _hypothesis(session, "model quality blocker")
        _document(
            session,
            title="Bad quote",
            text="bad quote marker: no evidence shows model quality blocker is the issue.",
        )
        session.commit()

        result = run_tool(
            session,
            task_type="counter_evidence_scan",
            hypothesis=hypothesis,
            policy=DEFAULT_POLICY,
            provider=StubProvider(),
        )

    assert result.evidence_candidates == []
    assert result.metadata["quote_not_verbatim"] == 1


def test_counter_scan_propagates_stance_judge_failure(session_factory):
    with session_factory() as session:
        hypothesis = _hypothesis(session, "model quality blocker")
        _document(session, title="Failure", text="no evidence shows model quality blocker is the issue.")
        session.commit()

        with pytest.raises(ExtractionUnavailable, match="stance judge unavailable"):
            run_tool(
                session,
                task_type="counter_evidence_scan",
                hypothesis=hypothesis,
                policy=DEFAULT_POLICY,
                provider=UnavailableStanceProvider(),
            )
