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
    # Caller owns the post-commit flip scan (install_pack no longer scans).
    opencrab_packs.scan_installed_pack(session, result.pack_install_id)
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


# ---------------------------------------------------------------------------
# #5 predicate matching: full-phrase + body-only (not any-token / not provenance)
# ---------------------------------------------------------------------------


def _fake_evidence(
    *,
    text: str | None,
    local_id: str = "e1",
    source_path: str | None = None,
) -> Any:
    """Minimal PackEvidence stand-in for pure matcher unit tests."""
    from types import SimpleNamespace

    return SimpleNamespace(
        text=text,
        local_evidence_id=local_id,
        global_evidence_id=f"pack://{local_id}",
        source_json={"path": source_path} if source_path else {},
        location_json={},
        kind=None,
    )


def test_predicate_requires_full_phrase_not_any_token() -> None:
    """#5 RED/GREEN: query_terms=['recovery time'] must not match body with only 'time'."""
    rows = [_fake_evidence(text="Estimated time remaining is two hours.")]
    matched = flip_monitoring._match_predicate_against_evidence(
        {"query_terms": ["recovery time"]},
        rows,  # type: ignore[arg-type]
    )
    assert matched == []


def test_predicate_ignores_source_path_provenance() -> None:
    """#5 RED/GREEN: source path 'recovery-time' must not trigger without body phrase."""
    rows = [
        _fake_evidence(
            text="Unrelated operational notes about staffing and budget.",
            source_path="docs/recovery-time-slo.md",
        )
    ]
    matched = flip_monitoring._match_predicate_against_evidence(
        {"query_terms": ["recovery time"]},
        rows,  # type: ignore[arg-type]
    )
    assert matched == []


def test_predicate_matches_full_phrase_in_body() -> None:
    """#5 RED/GREEN: full phrase in evidence body triggers."""
    rows = [
        _fake_evidence(
            text="Estimated recovery time is under two hours for the primary path.",
            source_path="docs/other.md",
        )
    ]
    matched = flip_monitoring._match_predicate_against_evidence(
        {"query_terms": ["recovery time"]},
        rows,  # type: ignore[arg-type]
    )
    assert matched == ["pack://e1"]


def test_predicate_or_of_full_phrases() -> None:
    """#5: any one full query_term phrase is enough (OR of phrases, not tokens)."""
    rows = [_fake_evidence(text="현장 복구 시간이 2시간 이내입니다.")]
    matched = flip_monitoring._match_predicate_against_evidence(
        {"query_terms": ["recovery time", "복구 시간"]},
        rows,  # type: ignore[arg-type]
    )
    assert matched == ["pack://e1"]


# ---------------------------------------------------------------------------
# #6 install scan isolation + bounds
# ---------------------------------------------------------------------------


def test_install_pack_succeeds_when_flip_scan_raises(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#6 RED/GREEN: a scan exception must not abort Pack admission (post-commit)."""

    def _boom(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise RuntimeError("simulated flip scan failure")

    monkeypatch.setattr(flip_monitoring, "scan_pack_install", _boom)

    with session_factory() as session:
        pack_dir = _copy_fixture(MINIMAL_FIXTURE, tmp_path / "install-scan-iso")
        manifest = pack_dir / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["pack_id"] = "pack.install-scan-iso"
        manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        result = opencrab_packs.install_pack(
            session,
            pack_dir,
            workspace=temp_settings.workspace,
            profile="compatible",
        )
        # Caller owns the commit; the post-commit scan isolates its own failure.
        session.commit()
        opencrab_packs.scan_installed_pack(session, result.pack_install_id)
        assert result.noop is False
        assert result.pack_install_id is not None
        install = session.get(PackInstall, result.pack_install_id)
        assert install is not None
        assert install.status == "active"


def test_install_pack_does_not_commit_caller_session(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """Regression: install_pack must not commit unrelated pending caller rows."""
    from openoyster.models import SystemState

    with session_factory() as session:
        marker = SystemState(key="rw5_marker", value_json={"unrelated": True})
        session.add(marker)

        pack_dir = _copy_fixture(MINIMAL_FIXTURE, tmp_path / "install-tx-iso")
        manifest = pack_dir / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["pack_id"] = "pack.install-tx-iso"
        manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        opencrab_packs.install_pack(
            session,
            pack_dir,
            workspace=temp_settings.workspace,
            profile="compatible",
        )
        # install_pack only flushed; rolling back must drop the unrelated marker
        # (and the not-yet-committed admission).
        session.rollback()

    with session_factory() as session:
        assert session.get(SystemState, "rw5_marker") is None


def test_load_evidence_bounded_uses_sql_limit(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """C: evidence load is SQL-limited (max_rows+1), not full-table .all()."""
    from sqlalchemy import event

    with session_factory() as session:
        matching = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="limit-obs",
            pack_id="pack.limit-obs",
            evidence_text="noise only",
        )
        from openoyster.models import PackEvidence

        for i in range(5):
            session.add(
                PackEvidence(
                    pack_install_id=matching.id,
                    local_evidence_id=f"e-extra-{i}",
                    global_evidence_id=f"{matching.pack_id}://e-extra-{i}",
                    kind="note",
                    source_json={},
                    parser_json={},
                    location_json={},
                    links_json={},
                    text=f"extra row {i}",
                    raw_record_json={"bulky": "x" * 100},
                    record_hash=("c" * 63) + str(i),
                )
            )
        session.commit()

        statements: list[str] = []
        engine = session.get_bind()

        def _capture(conn: Any, cursor: Any, statement: str, *args: Any, **kwargs: Any) -> None:
            del conn, cursor, args, kwargs
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            rows, truncated = flip_monitoring._load_evidence_bounded(
                session,
                matching.id,
                max_evidence_rows=2,
                max_evidence_chars=2_000_000,
            )
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        assert truncated is True
        assert len(rows) == 2
        pack_selects = [s for s in statements if "pack_evidence" in s.lower()]
        assert pack_selects, f"expected pack_evidence SELECT, got {statements!r}"
        assert any("limit" in s.lower() for s in pack_selects)
        # Projection: do not SELECT raw_record_json for the scan path.
        assert all("raw_record_json" not in s.lower() for s in pack_selects)


def test_scan_pack_install_respects_evidence_row_bound(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#6 RED/GREEN: evidence beyond row cap is skipped with a warning."""
    import logging

    with session_factory() as session:
        base = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="bound-base",
            pack_id="pack.bound-base",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=base.pack_id,
            idempotency_key="flip-bound-1",
            provider=PredicateDecisionProvider(query_terms=["unique-bound-phrase-xyz"]),
        )
        watch = session.scalar(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        )
        assert watch is not None

        # Install a matching pack, then inject extra evidence rows beyond the cap.
        matching = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="bound-match",
            pack_id="pack.bound-match",
            evidence_text="noise only — no bound phrase here",
        )
        # The first install already ran scan (no match). Reset watch + drop triggers.
        watch.status = flip_monitoring.WATCH_STATUS_WATCHING
        for t in session.scalars(
            select(DeliberationFlipTrigger).where(
                DeliberationFlipTrigger.pack_install_id == matching.id
            )
        ).all():
            session.delete(t)
        # Add a second evidence row that WOULD match, but only if scan reads past row 1.
        from openoyster.models import PackEvidence

        existing = session.scalars(
            select(PackEvidence)
            .where(PackEvidence.pack_install_id == matching.id)
            .order_by(PackEvidence.id.asc())
        ).all()
        assert len(existing) >= 1
        # Ensure the first row (by id) is non-matching noise; second has the phrase.
        existing[0].text = "noise only — no bound phrase here"
        session.add(
            PackEvidence(
                pack_install_id=matching.id,
                local_evidence_id="e-late-match",
                global_evidence_id=f"{matching.pack_id}://e-late-match",
                kind="note",
                source_json={},
                parser_json={},
                location_json={},
                links_json={},
                text="contains unique-bound-phrase-xyz in the late row",
                raw_record_json={},
                record_hash="b" * 64,
            )
        )
        session.commit()

        with caplog.at_level(logging.WARNING, logger="openoyster.services.flip_monitoring"):
            triggers = flip_monitoring.scan_pack_install(
                session,
                matching.id,
                max_evidence_rows=1,
                max_evidence_chars=2_000_000,
            )
            session.commit()

        # Cap stops at first row → no match, watch stays watching, warning logged.
        assert triggers == []
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_WATCHING
        assert any("flip scan bounded" in rec.message for rec in caplog.records)

        # Unbounded (or higher) scan would find the late phrase.
        triggers2 = flip_monitoring.scan_pack_install(
            session,
            matching.id,
            max_evidence_rows=10,
        )
        session.commit()
        assert len(triggers2) == 1
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE


# ---------------------------------------------------------------------------
# #7 atomic claim + idempotent double scan
# ---------------------------------------------------------------------------


def test_double_scan_same_watch_install_is_idempotent(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """#7 RED/GREEN: two scans → one trigger, no exception."""
    with session_factory() as session:
        base = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="idem-base",
            pack_id="pack.idem-base",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=base.pack_id,
            idempotency_key="flip-idem-1",
            provider=PredicateDecisionProvider(query_terms=["idempotent recovery phrase"]),
        )
        watch = session.scalar(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        )
        assert watch is not None

        matching = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="idem-match",
            pack_id="pack.idem-match",
            evidence_text="See the idempotent recovery phrase in this note.",
        )
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE

        # Second manual scan must be a no-op (already claimed).
        again = flip_monitoring.scan_pack_install(session, matching.id)
        session.commit()
        assert again == []
        triggers = session.scalars(
            select(DeliberationFlipTrigger).where(
                DeliberationFlipTrigger.watch_id == watch.id,
                DeliberationFlipTrigger.pack_install_id == matching.id,
            )
        ).all()
        assert len(triggers) == 1


def test_scan_skips_already_candidate_without_retrigger(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """#7: watch already triggered_candidate is not re-scanned into a second trigger."""
    with session_factory() as session:
        base = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="claim-base",
            pack_id="pack.claim-base",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=base.pack_id,
            idempotency_key="flip-claim-1",
            provider=PredicateDecisionProvider(query_terms=["claim once phrase"]),
        )
        watch = session.scalar(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        )
        assert watch is not None
        # Force candidate without a trigger for this install, then scan a new install.
        watch.status = flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE
        session.commit()

        matching = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="claim-match",
            pack_id="pack.claim-match",
            evidence_text="claim once phrase appears here",
        )
        triggers = session.scalars(
            select(DeliberationFlipTrigger).where(DeliberationFlipTrigger.watch_id == watch.id)
        ).all()
        assert triggers == []
        # Manual scan still must not create a trigger (not watching).
        again = flip_monitoring.scan_pack_install(session, matching.id)
        session.commit()
        assert again == []


# ---------------------------------------------------------------------------
# #10 dismiss transition map
# ---------------------------------------------------------------------------


def test_dismiss_rejects_confirmed_watch(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """#10 RED/GREEN: dismiss must not overwrite confirmed."""
    with session_factory() as session:
        install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="dismiss-confirmed",
            pack_id="pack.dismiss-confirmed",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=install.pack_id,
            idempotency_key="flip-dismiss-confirmed-1",
            provider=PredicateDecisionProvider(),
        )
        watch = session.scalar(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        )
        assert watch is not None
        watch.status = flip_monitoring.WATCH_STATUS_CONFIRMED
        session.commit()

        with pytest.raises(flip_monitoring.FlipWatchError) as exc_info:
            flip_monitoring.dismiss_watch(session, watch.id, reason="should be rejected")
        assert exc_info.value.code == "invalid_watch_transition"
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_CONFIRMED
        assert watch.dismiss_reason is None


def test_dismiss_rejects_expired_and_dismissed(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """#10: expired/dismissed also reject dismiss with invalid_watch_transition."""
    with session_factory() as session:
        install = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="dismiss-terminal",
            pack_id="pack.dismiss-terminal",
        )
        run_id = _run_completed(
            session,
            temp_settings,
            pack_id=install.pack_id,
            idempotency_key="flip-dismiss-terminal-1",
            provider=PredicateDecisionProvider(),
        )
        watch = session.scalar(
            select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
        )
        assert watch is not None

        watch.status = flip_monitoring.WATCH_STATUS_EXPIRED
        session.commit()
        with pytest.raises(flip_monitoring.FlipWatchError) as exc_info:
            flip_monitoring.dismiss_watch(session, watch.id, reason="nope")
        assert exc_info.value.code == "invalid_watch_transition"

        watch.status = flip_monitoring.WATCH_STATUS_DISMISSED
        watch.dismiss_reason = "already"
        session.commit()
        with pytest.raises(flip_monitoring.FlipWatchError) as exc_info:
            flip_monitoring.dismiss_watch(session, watch.id, reason="again")
        assert exc_info.value.code == "invalid_watch_transition"
        session.refresh(watch)
        assert watch.dismiss_reason == "already"
