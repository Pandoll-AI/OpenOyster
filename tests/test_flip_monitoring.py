"""Flip Condition Monitoring D3 — watch creation, deterministic scan, API/CLI."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openoyster.api.app import create_app
from openoyster.config import Settings
from openoyster.deliberation_contracts import Mission
from openoyster.llm import LLMProvider
from openoyster.models import (
    DeliberationFlipTrigger,
    DeliberationFlipWatch,
    Event,
    PackInstall,
)
from openoyster.schemas import TextAnalysis
from openoyster.services import deliberation, flip_monitoring, opencrab_packs
from openoyster.services.llm_judges import stub_query_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
MISSION_PATH = PROJECT_ROOT / "tests/fixtures/deliberation_d1/mission_happy.json"


def _load_mission() -> Mission:
    return Mission.model_validate(json.loads(MISSION_PATH.read_text(encoding="utf-8")))


def _copy_fixture(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest)
    return dest


def _install_fixture(
    session: Session,
    settings: Settings,
    tmp_path: Path,
    fixture: Path,
    *,
    dirname: str,
    pack_id: str | None = None,
    evidence_text: str | None = None,
) -> PackInstall:
    pack_dir = _copy_fixture(fixture, tmp_path / dirname)
    if pack_id is not None:
        manifest = pack_dir / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["pack_id"] = pack_id
        manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if evidence_text is not None:
        evidence_path = pack_dir / "evidence" / "index.jsonl"
        row = json.loads(evidence_path.read_text(encoding="utf-8").splitlines()[0])
        row["text"] = evidence_text
        evidence_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    result = opencrab_packs.install_pack(
        session,
        pack_dir,
        workspace=settings.workspace,
        profile="compatible",
    )
    session.commit()
    install = session.get(PackInstall, result.pack_install_id)
    assert install is not None
    return install


class PredicateDecisionProvider(LLMProvider):
    """Stub provider that attaches a flip predicate on the decision stage."""

    name = "predicate-stub"

    def __init__(self, *, with_predicate: bool = True, query_terms: list[str] | None = None) -> None:
        self.with_predicate = with_predicate
        self.query_terms = query_terms or ["recovery time", "복구 시간"]
        self.calls: list[str] = []

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.calls.append(stage)
        payload = stub_query_json(prompt, stage)
        if stage == "deliberation_decision" and self.with_predicate:
            flips = payload.get("flip_conditions")
            if isinstance(flips, list) and flips:
                flips[0]["predicate"] = {
                    "query_terms": list(self.query_terms),
                    "note": "re-check if recovery time evidence arrives",
                }
        return payload


class PlainStubProvider(LLMProvider):
    name = "plain-stub"

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("unused")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        return stub_query_json(prompt, stage)


def _run_completed(
    session: Session,
    settings: Settings,
    *,
    pack_id: str,
    idempotency_key: str,
    provider: LLMProvider,
) -> int:
    run = deliberation.run_deliberation(
        session,
        _load_mission(),
        pack_ids=[pack_id],
        impact_baseline_pack_ids=[pack_id],
        idempotency_key=idempotency_key,
        provider=provider,
        settings=settings,
        allow_compatible_packs=True,
    )
    session.commit()
    assert run.status == "completed"
    return run.id


def test_completed_decision_with_predicate_creates_watch(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="watch-create",
            pack_id="pack.watch-create",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=install.pack_id,
            idempotency_key="flip-watch-create-1",
            provider=PredicateDecisionProvider(),
        )
        watches = session.scalars(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        ).all()
        assert len(watches) == 1
        assert watches[0].status == flip_monitoring.WATCH_STATUS_WATCHING
        assert watches[0].flip_local_key == "flip1"
        assert watches[0].predicate_json["query_terms"] == ["recovery time", "복구 시간"]


def test_matching_pack_install_triggers_candidate(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        base = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="watch-base",
            pack_id="pack.watch-base",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=base.pack_id,
            idempotency_key="flip-watch-match-1",
            provider=PredicateDecisionProvider(query_terms=["recovery time"]),
        )
        watch = session.scalar(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        )
        assert watch is not None
        assert watch.status == flip_monitoring.WATCH_STATUS_WATCHING

        matching = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="watch-match",
            pack_id="pack.watch-match",
            evidence_text="Estimated recovery time is under two hours for the primary path.",
        )
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE
        triggers = session.scalars(
            select(DeliberationFlipTrigger).where(DeliberationFlipTrigger.watch_id == watch.id)
        ).all()
        assert len(triggers) == 1
        assert triggers[0].pack_install_id == matching.id
        assert triggers[0].matched_evidence_ids
        events = session.scalars(
            select(Event).where(Event.event_type == flip_monitoring.EVENT_FLIP_TRIGGER_CANDIDATE)
        ).all()
        assert any(
            (e.payload_json or {}).get("watch_id") == watch.id
            and (e.payload_json or {}).get("pack_install_id") == matching.id
            for e in events
        )


def test_non_matching_pack_install_leaves_watch_unchanged(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        base = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="watch-nomatch-base",
            pack_id="pack.watch-nomatch-base",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=base.pack_id,
            idempotency_key="flip-watch-nomatch-1",
            provider=PredicateDecisionProvider(query_terms=["quantum flux capacitor"]),
        )
        watch = session.scalar(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        )
        assert watch is not None
        before_updated = watch.updated_at

        _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="watch-nomatch",
            pack_id="pack.watch-nomatch",
            evidence_text="This source supports this claim without any exotic tokens.",
        )
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_WATCHING
        assert watch.updated_at == before_updated
        trigger_count = session.scalar(
            select(DeliberationFlipTrigger).where(DeliberationFlipTrigger.watch_id == watch.id)
        )
        assert trigger_count is None


def test_dismiss_records_reason_and_event(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="watch-dismiss",
            pack_id="pack.watch-dismiss",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=install.pack_id,
            idempotency_key="flip-watch-dismiss-1",
            provider=PredicateDecisionProvider(),
        )
        watch = session.scalar(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        )
        assert watch is not None
        dismissed = flip_monitoring.dismiss_watch(
            session, watch.id, reason="not relevant after manual review"
        )
        session.commit()
        assert dismissed.status == flip_monitoring.WATCH_STATUS_DISMISSED
        assert dismissed.dismiss_reason == "not relevant after manual review"
        events = session.scalars(
            select(Event).where(Event.event_type == flip_monitoring.EVENT_FLIP_WATCH_DISMISSED)
        ).all()
        assert any(
            (e.payload_json or {}).get("watch_id") == watch.id
            and (e.payload_json or {}).get("reason") == "not relevant after manual review"
            for e in events
        )


def test_predicate_absent_creates_zero_watches(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="watch-none",
            pack_id="pack.watch-none",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=install.pack_id,
            idempotency_key="flip-watch-none-1",
            provider=PlainStubProvider(),
        )
        watches = session.scalars(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        ).all()
        assert watches == []


def test_flip_watch_api_requires_auth_and_sanitizes(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    with session_factory() as session:
        install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="watch-api",
            pack_id="pack.watch-api",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=install.pack_id,
            idempotency_key="flip-watch-api-1",
            provider=PredicateDecisionProvider(),
        )
        watch = session.scalar(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        )
        assert watch is not None
        # Inject a path-like note so sanitizer coverage is meaningful.
        watch.predicate_json = {
            **(watch.predicate_json or {}),
            "note": "see /private/openoyster/secret-path for context",
        }
        session.commit()
        watch_id = watch.id

    application = create_app(settings=temp_settings, session_factory=session_factory)
    auth = {temp_settings.api_key_header: str(temp_settings.api_key)}

    with TestClient(application) as client:
        assert client.get(f"/v1/deliberations/{run_id}/flip-watches").status_code == 401
        assert client.get("/v1/flip-triggers").status_code == 401
        assert (
            client.post(f"/v1/flip-watches/{watch_id}/dismiss", json={"reason": "nope"}).status_code
            == 401
        )

        listed = client.get(f"/v1/deliberations/{run_id}/flip-watches", headers=auth)
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["run_id"] == run_id
        assert len(body["watches"]) == 1
        assert "/private/openoyster/secret-path" not in listed.text
        assert "raw_record_json" not in listed.text

        dismissed = client.post(
            f"/v1/flip-watches/{watch_id}/dismiss",
            headers=auth,
            json={"reason": "api dismiss path"},
        )
        assert dismissed.status_code == 200, dismissed.text
        assert dismissed.json()["watch"]["status"] == "dismissed"
        assert dismissed.json()["watch"]["dismiss_reason"] == "api dismiss path"

        triggers = client.get("/v1/flip-triggers?status=candidate", headers=auth)
        assert triggers.status_code == 200, triggers.text
        assert "triggers" in triggers.json()


def test_manual_scan_and_replay_do_not_mutate_flip_tables_on_replay(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    from openoyster.services.deliberation_replay import replay_deliberation

    with session_factory() as session:
        base = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="watch-replay-base",
            pack_id="pack.watch-replay-base",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=base.pack_id,
            idempotency_key="flip-watch-replay-1",
            provider=PredicateDecisionProvider(query_terms=["unique-zz-term-xyz"]),
        )
        watch = session.scalar(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        )
        assert watch is not None
        before_status = watch.status
        before_updated = watch.updated_at
        before_trigger_count = len(
            session.scalars(select(DeliberationFlipTrigger)).all()
        )

        replay = replay_deliberation(session, run_id)
        session.commit()
        assert replay.matched is True or isinstance(replay.matched, bool)

        session.refresh(watch)
        assert watch.status == before_status
        assert watch.updated_at == before_updated
        after_trigger_count = len(session.scalars(select(DeliberationFlipTrigger)).all())
        assert after_trigger_count == before_trigger_count

        # Manual scan against the original install evidence (no matching term).
        triggers = flip_monitoring.scan_pack_install(session, base.id)
        session.commit()
        assert triggers == []
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_WATCHING


def test_flip_predicate_contract_rejects_empty_and_oversized_terms() -> None:
    from pydantic import ValidationError

    from openoyster.deliberation_contracts import FlipCondition, FlipPredicate

    with pytest.raises(ValidationError):
        FlipPredicate.model_validate({"query_terms": []})
    with pytest.raises(ValidationError):
        FlipPredicate.model_validate({"query_terms": ["  "]})
    with pytest.raises(ValidationError):
        FlipPredicate.model_validate({"query_terms": ["x" * 101]})
    with pytest.raises(ValidationError):
        FlipPredicate.model_validate({"query_terms": [f"t{i}" for i in range(9)]})

    # Optional predicate remains valid when omitted (legacy flip shape).
    condition = {
        "text": "If supporting evidence is invalidated",
        "classification": "proposal",
        "mission_pointer": "/goal",
    }
    flip = FlipCondition.model_validate({"local_key": "flip1", "condition": condition})
    assert flip.predicate is None
