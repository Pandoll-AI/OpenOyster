from __future__ import annotations

from sqlalchemy import select

from openoyster.events import bus
from openoyster.loops.optimisation import HyperparameterOptimisationLoop
from openoyster.models import DecisionTrace, Policy
from openoyster.policies import ensure_default_policy


def _trace(score: float, outcome: float) -> DecisionTrace:
    return DecisionTrace(
        decision_type="trigger_decision",
        subject_type="hypothesis",
        subject_id=int(score * 1000) + int(outcome * 100),
        policy_version="default-0.2.0",
        features_json={
            "novelty": score,
            "impact": score,
            "contradiction": score,
            "evidence_gap": score,
            "staleness": score,
            "revision": 1,
        },
        score=score,
        threshold=0.4,
        decision=score >= 0.4,
        outcome_score=outcome,
        metadata_json={"label_source": "artifact_feedback"},
    )


def test_policy_requires_replay_then_new_shadow_labels(temp_settings, session_factory):
    loop = HyperparameterOptimisationLoop(temp_settings)
    with session_factory() as session:
        ensure_default_policy(session, temp_settings)
        for score, outcome in [(0.38, 1.0), (0.38, 1.0), (0.38, 1.0), (0.2, 0.0), (0.2, 0.0)]:
            session.add(_trace(score, outcome))
        bus.emit(session, "optimisation.review_requested", {"reason": "test"})
        session.commit()

    with session_factory() as session:
        result = loop.run(session)
        session.commit()
    assert result.created_records.get("shadow_policies") == 1

    with session_factory() as session:
        shadow = session.scalar(select(Policy).where(Policy.status == "shadow"))
        assert shadow is not None
        for score, outcome in [(0.38, 1.0), (0.38, 1.0), (0.2, 0.0)]:
            session.add(_trace(score, outcome))
        bus.emit(session, "artifact.feedback.recorded", {"artifact_id": 999})
        session.commit()

    with session_factory() as session:
        result = loop.run(session)
        session.commit()
    assert result.created_records.get("promoted_policies") == 1
    with session_factory() as session:
        active = session.scalar(select(Policy).where(Policy.status == "active"))
        assert active is not None
        assert active.version.startswith("shadow-")
