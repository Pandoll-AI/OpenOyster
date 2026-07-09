from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from openoyster.events import bus
from openoyster.llm import ExtractionUnavailable, LLMProvider, StubProvider
from openoyster.loops.hypothesis import HypothesisLoop
from openoyster.models import DecisionTrace, Hypothesis
from openoyster.policies import ensure_default_policy
from openoyster.schemas import TextAnalysis
from openoyster.utils import normalise_text, stable_hash


class UnavailableMergeProvider(LLMProvider):
    name = "unavailable-merge"

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        del texts, policy
        raise ExtractionUnavailable("merge judge unavailable")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        del prompt, stage
        raise ExtractionUnavailable("merge judge unavailable")


def _hypothesis(claim: str, scope: str = "Acme") -> Hypothesis:
    return Hypothesis(
        claim=claim,
        claim_hash=stable_hash(normalise_text(claim).casefold()),
        scope=scope,
        confidence=0.55,
        status="active",
        revision=1,
    )


def _emit_candidate(session, claim: str, scope: str = "Acme") -> None:
    bus.emit(
        session,
        "hypothesis.candidate_created",
        {
            "hypothesis": {
                "claim": claim,
                "scope": scope,
                "confidence": 0.6,
                "evidence_signal_summary": claim,
                "stance": "support",
            }
        },
    )


def test_hypothesis_loop_merges_same_claim_with_stub_provider(session_factory):
    with session_factory() as session:
        ensure_default_policy(session)
        session.add(_hypothesis("Acme governance improves adoption."))
        _emit_candidate(session, "Acme governance improves adoption")
        session.commit()

        result = HypothesisLoop(provider=StubProvider()).run(session)
        session.commit()

    with session_factory() as session:
        traces = list(session.scalars(select(DecisionTrace).where(DecisionTrace.decision_type == "merge_decision")))
        assert session.scalar(select(func.count(Hypothesis.id))) == 1
        assert result.created_records.get("hypotheses", 0) == 0
        assert traces
        assert traces[0].decision is True
        assert traces[0].features_json["relation"] == "same"


def test_hypothesis_loop_creates_new_hypothesis_for_different_claim(session_factory):
    with session_factory() as session:
        ensure_default_policy(session)
        session.add(_hypothesis("Acme governance improves adoption."))
        _emit_candidate(session, "Acme model quality becomes the adoption blocker")
        session.commit()

        result = HypothesisLoop(provider=StubProvider()).run(session)
        session.commit()

    with session_factory() as session:
        trace = session.scalar(select(DecisionTrace).where(DecisionTrace.decision_type == "merge_decision"))
        assert session.scalar(select(func.count(Hypothesis.id))) == 2
        assert result.created_records["hypotheses"] == 1
        assert trace is not None
        assert trace.decision is False
        assert trace.features_json["relation"] == "different"


def test_hypothesis_loop_creates_new_hypothesis_when_merge_judge_unavailable(session_factory):
    with session_factory() as session:
        ensure_default_policy(session)
        session.add(_hypothesis("Acme governance improves adoption."))
        _emit_candidate(session, "Acme governance improves approval speed")
        session.commit()

        result = HypothesisLoop(provider=UnavailableMergeProvider()).run(session)
        session.commit()

    with session_factory() as session:
        created = session.scalar(
            select(Hypothesis).where(Hypothesis.claim == "Acme governance improves approval speed")
        )
        trace = session.scalar(
            select(DecisionTrace)
            .where(DecisionTrace.decision_type == "merge_decision")
            .order_by(DecisionTrace.id.desc())
        )
        assert created is not None
        assert created.metadata_json["merge_judge_unavailable"] is True
        assert result.created_records["hypotheses"] == 1
        assert trace is not None
        assert trace.decision is False
        assert trace.features_json["relation"] == "unavailable"


def test_hypothesis_loop_rejects_missing_payload_without_trace(session_factory):
    with session_factory() as session:
        ensure_default_policy(session)
        bus.emit(session, "hypothesis.candidate_created", {"hypothesis": None})
        session.commit()

        result = HypothesisLoop(provider=StubProvider()).run(session)
        session.commit()

    with session_factory() as session:
        assert result.notes
        assert session.scalar(select(func.count(DecisionTrace.id))) == 0
