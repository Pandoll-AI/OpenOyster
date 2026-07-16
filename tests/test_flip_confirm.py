"""Optional flip-trigger LLM confirmation stage (default off)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openoyster.config import Settings
from openoyster.deliberation_contracts import Mission
from openoyster.llm import LLMProvider, flip_confirm_provider_from_settings
from openoyster.models import (
    DeliberationFlipTrigger,
    DeliberationFlipWatch,
    PackInstall,
)
from openoyster.schemas import TextAnalysis
from openoyster.services import deliberation, flip_monitoring, opencrab_packs
from openoyster.services.llm_judges import stub_query_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
MISSION_PATH = PROJECT_ROOT / "tests/fixtures/deliberation_d1/mission_happy.json"

EVIDENCE_BODY = (
    "Estimated recovery time is under two hours for the primary path and "
    "requires operator confirmation."
)


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
    opencrab_packs.scan_installed_pack(session, result.pack_install_id)
    install = session.get(PackInstall, result.pack_install_id)
    assert install is not None
    return install


class PredicateDecisionProvider(LLMProvider):
    name = "predicate-stub"

    def __init__(self, *, query_terms: list[str] | None = None) -> None:
        self.query_terms = query_terms or ["recovery time"]
        self.calls: list[str] = []

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.calls.append(stage)
        payload = stub_query_json(prompt, stage)
        if stage == "deliberation_decision":
            flips = payload.get("flip_conditions")
            if isinstance(flips, list) and flips:
                flips[0]["predicate"] = {
                    "query_terms": list(self.query_terms),
                    "note": "re-check if recovery time evidence arrives",
                }
        return payload


class ControllableConfirmProvider(LLMProvider):
    """Flip-confirm test double with controllable related/quote/error behaviour."""

    name = "confirm-control"

    def __init__(
        self,
        *,
        related: bool = True,
        quote: str | None = "auto",
        raise_exc: Exception | None = None,
    ) -> None:
        self.related = related
        self.quote = quote
        self.raise_exc = raise_exc
        self.calls: list[str] = []

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("unused")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.calls.append(stage)
        if self.raise_exc is not None:
            raise self.raise_exc
        if stage != "flip_confirm":
            return stub_query_json(prompt, stage)
        quote = self.quote
        if quote == "auto":
            # Real quote from first evidence body in the prompt.
            import re

            match = re.search(
                r"\[EVIDENCE id=(?P<id>[^\]]+)\]\n(?P<body>.*?)\n\[/EVIDENCE\]",
                prompt,
                re.S,
            )
            assert match is not None
            body = match.group("body").strip()
            quote = body if len(body) <= 120 else body[:120]
        return {"related": self.related, "quote": quote}


def _run_completed(
    session: Session,
    settings: Settings,
    *,
    pack_id: str,
    idempotency_key: str,
) -> int:
    run = deliberation.run_deliberation(
        session,
        _load_mission(),
        pack_ids=[pack_id],
        impact_baseline_pack_ids=[pack_id],
        idempotency_key=idempotency_key,
        provider=PredicateDecisionProvider(),
        settings=settings,
        allow_compatible_packs=True,
    )
    session.commit()
    assert run.status == "completed"
    return run.id


def _setup_watching(
    session: Session,
    settings: Settings,
    tmp_path: Path,
    *,
    key: str,
) -> DeliberationFlipWatch:
    base = _install_fixture(
        session,
        settings,
        tmp_path,
        MINIMAL_FIXTURE,
        dirname=f"{key}-base",
        pack_id=f"pack.{key}-base",
    )
    run_id = _run_completed(
        session,
        settings,
        pack_id=base.pack_id,
        idempotency_key=f"flip-confirm-{key}",
    )
    watch = session.scalar(
        select(DeliberationFlipWatch).where(DeliberationFlipWatch.run_id == run_id)
    )
    assert watch is not None
    assert watch.status == flip_monitoring.WATCH_STATUS_WATCHING
    return watch


def test_flip_confirm_provider_defaults_to_none(temp_settings: Settings) -> None:
    assert temp_settings.flip_confirm_provider == "none"
    assert flip_confirm_provider_from_settings(temp_settings) is None


def test_provider_none_leaves_confirmation_none_and_no_llm_call(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(a) Default provider=none: confirm never called, confirmation stays none."""
    assert temp_settings.flip_confirm_provider == "none"
    confirm_calls: list[str] = []

    def _boom_confirm(*_a: Any, **_k: Any) -> None:
        confirm_calls.append("called")
        raise AssertionError("confirm_trigger must not run when provider is none")

    monkeypatch.setattr(flip_monitoring, "confirm_trigger", _boom_confirm)

    with session_factory() as session:
        watch = _setup_watching(session, temp_settings, tmp_path, key="none")
        matching = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="none-match",
            pack_id="pack.none-match",
            evidence_text=EVIDENCE_BODY,
        )
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE
        triggers = session.scalars(
            select(DeliberationFlipTrigger).where(DeliberationFlipTrigger.watch_id == watch.id)
        ).all()
        assert len(triggers) == 1
        assert triggers[0].pack_install_id == matching.id
        assert triggers[0].confirmation == "none"
        assert triggers[0].confirmation_anchors_json == [] or triggers[0].confirmation_anchors_json is None or list(
            triggers[0].confirmation_anchors_json or []
        ) == []
        assert confirm_calls == []
        payload = flip_monitoring.trigger_public_payload(triggers[0], watch)
        assert payload["confirmation"] == "none"
        assert payload["confirmation_anchors"] == []


def test_stub_provider_related_true_with_real_quote_is_llm_supported(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """(b) stub related=true + real quote → llm_supported + anchors."""
    temp_settings.flip_confirm_provider = "stub"
    with session_factory() as session:
        watch = _setup_watching(session, temp_settings, tmp_path, key="supported")
        status_before_match = watch.status
        assert status_before_match == flip_monitoring.WATCH_STATUS_WATCHING

        matching = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="supported-match",
            pack_id="pack.supported-match",
            evidence_text=EVIDENCE_BODY,
        )
        # Re-scan with settings so flip_confirm_provider=stub is used.
        # install path uses get_settings(); inject via explicit scan.
        # First install may have used default none — reset and rescan if needed.
        session.refresh(watch)
        triggers = list(
            session.scalars(
                select(DeliberationFlipTrigger).where(
                    DeliberationFlipTrigger.watch_id == watch.id
                )
            ).all()
        )
        if not triggers or triggers[0].confirmation == "none":
            # Explicit scan path with settings (install scan may see cached settings).
            if triggers:
                # Already triggered; call confirm directly with stub provider.
                provider = flip_confirm_provider_from_settings(temp_settings)
                assert provider is not None
                flip_monitoring.confirm_trigger(session, triggers[0], provider)
                session.commit()
            else:
                flip_monitoring.scan_pack_install(
                    session, matching.id, settings=temp_settings
                )
                session.commit()
        session.refresh(watch)
        triggers = session.scalars(
            select(DeliberationFlipTrigger).where(DeliberationFlipTrigger.watch_id == watch.id)
        ).all()
        assert len(triggers) == 1
        trigger = triggers[0]
        assert trigger.confirmation == "llm_supported"
        anchors = list(trigger.confirmation_anchors_json or [])
        assert len(anchors) == 1
        assert "evidence_id" in anchors[0]
        assert anchors[0]["quote"]
        assert anchors[0]["quote"] in EVIDENCE_BODY
        # Watch stays triggered_candidate — no auto-confirm transition.
        assert watch.status == flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE
        assert watch.status != flip_monitoring.WATCH_STATUS_CONFIRMED


def test_related_false_or_fake_quote_is_llm_unsupported(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """(c) related=false or unverified quote → llm_unsupported."""
    with session_factory() as session:
        watch = _setup_watching(session, temp_settings, tmp_path, key="unsup")
        matching = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="unsup-match",
            pack_id="pack.unsup-match",
            evidence_text=EVIDENCE_BODY,
        )
        session.refresh(watch)
        triggers = session.scalars(
            select(DeliberationFlipTrigger).where(DeliberationFlipTrigger.watch_id == watch.id)
        ).all()
        assert len(triggers) == 1
        trigger = triggers[0]
        assert trigger.confirmation == "none"

        false_provider = ControllableConfirmProvider(related=False, quote=None)
        flip_monitoring.confirm_trigger(session, trigger, false_provider)
        session.commit()
        session.refresh(trigger)
        assert trigger.confirmation == "llm_unsupported"
        assert list(trigger.confirmation_anchors_json or []) == []
        assert false_provider.calls == ["flip_confirm"]

        # Reset and try fake quote path.
        trigger.confirmation = "none"
        trigger.confirmation_note = None
        trigger.confirmation_anchors_json = []
        session.commit()
        fake = ControllableConfirmProvider(
            related=True, quote="this quote is not present in the evidence body at all"
        )
        flip_monitoring.confirm_trigger(session, trigger, fake)
        session.commit()
        session.refresh(trigger)
        assert trigger.confirmation == "llm_unsupported"
        assert list(trigger.confirmation_anchors_json or []) == []
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE
        assert matching.id == trigger.pack_install_id


def test_provider_exception_sets_error_preserves_scan_and_watch(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """(d) provider exception → error; scan/install success; watch status unchanged."""
    with session_factory() as session:
        watch = _setup_watching(session, temp_settings, tmp_path, key="err")
        matching = _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="err-match",
            pack_id="pack.err-match",
            evidence_text=EVIDENCE_BODY,
        )
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE
        triggers = session.scalars(
            select(DeliberationFlipTrigger).where(DeliberationFlipTrigger.watch_id == watch.id)
        ).all()
        assert len(triggers) == 1
        trigger = triggers[0]
        assert matching.status == "active"

        boom = ControllableConfirmProvider(raise_exc=RuntimeError("simulated timeout"))
        flip_monitoring.confirm_trigger(session, trigger, boom)
        session.commit()
        session.refresh(trigger)
        session.refresh(watch)
        assert trigger.confirmation == "error"
        assert trigger.confirmation_note is not None
        assert "RuntimeError" in trigger.confirmation_note
        assert list(trigger.confirmation_anchors_json or []) == []
        assert watch.status == flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE
        assert watch.status != flip_monitoring.WATCH_STATUS_CONFIRMED
        # Install still durable.
        install = session.get(PackInstall, matching.id)
        assert install is not None
        assert install.status == "active"


def test_confirm_never_auto_transitions_watch_to_confirmed(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """(e) Even llm_supported leaves watch at triggered_candidate."""
    with session_factory() as session:
        watch = _setup_watching(session, temp_settings, tmp_path, key="noauto")
        _install_fixture(
            session,
            temp_settings,
            tmp_path,
            MINIMAL_FIXTURE,
            dirname="noauto-match",
            pack_id="pack.noauto-match",
            evidence_text=EVIDENCE_BODY,
        )
        session.refresh(watch)
        triggers = session.scalars(
            select(DeliberationFlipTrigger).where(DeliberationFlipTrigger.watch_id == watch.id)
        ).all()
        assert len(triggers) == 1
        provider = ControllableConfirmProvider(related=True, quote="auto")
        flip_monitoring.confirm_trigger(session, triggers[0], provider)
        session.commit()
        session.refresh(watch)
        session.refresh(triggers[0])
        assert triggers[0].confirmation == "llm_supported"
        assert watch.status == flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE
        assert watch.status != flip_monitoring.WATCH_STATUS_CONFIRMED


def test_scan_hook_calls_confirm_when_provider_configured(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """scan_pack_install with settings.flip_confirm_provider invokes confirm once."""
    temp_settings.flip_confirm_provider = "stub"
    with session_factory() as session:
        watch = _setup_watching(session, temp_settings, tmp_path, key="hook")
        # Install without auto-scan path: install + explicit scan with settings.
        pack_dir = _copy_fixture(MINIMAL_FIXTURE, tmp_path / "hook-match")
        manifest = pack_dir / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["pack_id"] = "pack.hook-match"
        manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        evidence_path = pack_dir / "evidence" / "index.jsonl"
        row = json.loads(evidence_path.read_text(encoding="utf-8").splitlines()[0])
        row["text"] = EVIDENCE_BODY
        evidence_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        result = opencrab_packs.install_pack(
            session,
            pack_dir,
            workspace=temp_settings.workspace,
            profile="compatible",
        )
        session.commit()
        # Explicit scan with settings (not get_settings cache).
        created = flip_monitoring.scan_pack_install(
            session, result.pack_install_id, settings=temp_settings
        )
        session.commit()
        assert len(created) == 1
        assert created[0].confirmation == "llm_supported"
        session.refresh(watch)
        assert watch.status == flip_monitoring.WATCH_STATUS_TRIGGERED_CANDIDATE
