"""Decision Outcome Ledger — record, calibration, API, epistemic isolation."""

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
    DeliberationDossier,
    DeliberationOutcome,
    DeliberationRun,
    DeliberationStageCall,
)
from openoyster.schemas import TextAnalysis
from openoyster.services import deliberation, opencrab_packs, outcome_ledger
from openoyster.services.deliberation_prompts import build_stage_prompt, prompt_digest
from openoyster.services.llm_judges import stub_query_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
MISSION_PATH = PROJECT_ROOT / "tests/fixtures/deliberation_d1/mission_happy.json"


def _load_mission(**overrides: Any) -> Mission:
    payload = json.loads(MISSION_PATH.read_text(encoding="utf-8"))
    payload.update(overrides)
    return Mission.model_validate(payload)


def _seed_run(
    session: Session,
    *,
    status: str = "completed",
    outcome: str | None = "select",
    mission_charter_id: int | None = None,
    idempotency_key: str = "seed-run",
) -> DeliberationRun:
    mission: dict[str, Any] = {
        "goal": "g",
        "decision_question": "q",
        "constraints": [],
        "preferences": [],
    }
    if mission_charter_id is not None:
        mission["mission_charter_id"] = mission_charter_id
    run = DeliberationRun(
        idempotency_key=idempotency_key,
        mission_snapshot_json=mission,
        mission_digest="a" * 64,
        policy_snapshot_json={},
        runtime_config_json={},
        policy_digest="b" * 64,
        runtime_config_digest="c" * 64,
        contract_version="deliberation-d1-v1",
        prompt_template_version="deliberation-prompts-d1-v1",
        primary_scope_digest="d" * 64,
        impact_baseline_scope_digest="e" * 64,
        status=status,
        outcome=outcome,
    )
    session.add(run)
    session.flush()
    return run


class RecordingStubProvider(LLMProvider):
    """Stub LLM that records every stage prompt for epistemic assertions."""

    name = "recording-stub"

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.prompts.append((stage, prompt))
        return stub_query_json(prompt, stage)


def _install_minimal(
    session: Session, settings: Settings, tmp_path: Path, *, pack_id: str
) -> str:
    dest = tmp_path / pack_id
    shutil.copytree(MINIMAL_FIXTURE, dest)
    manifest = dest / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["pack_id"] = pack_id
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result = opencrab_packs.install_pack(
        session,
        dest,
        workspace=settings.workspace,
        profile="compatible",
    )
    session.commit()
    return result.pack_id


def test_record_rejects_incomplete_run(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        run = _seed_run(session, status="running", outcome=None, idempotency_key="inc-1")
        session.commit()
        with pytest.raises(outcome_ledger.OutcomeLedgerError) as exc_info:
            outcome_ledger.record_outcome(
                session,
                run.id,
                outcome_label="adopted",
            )
        assert exc_info.value.code == outcome_ledger.ERROR_RUN_NOT_COMPLETED
        assert session.scalar(select(DeliberationOutcome)) is None


def test_record_completed_run_updates_calibration(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        # Below min_sample (5) → insufficient; then add enough decision outcomes.
        for i in range(5):
            run = _seed_run(
                session,
                status="completed",
                outcome="select",
                idempotency_key=f"cal-dec-{i}",
                mission_charter_id=7 if i < 3 else 9,
            )
            label = "adopted" if i < 4 else "reversed"
            outcome_ledger.record_outcome(
                session,
                run.id,
                outcome_label=label,
                scenario_assessments={"adverse": "materialized" if i % 2 == 0 else "not_materialized"},
                idempotency_key=f"cal-out-{i}",
            )
        # Abstain runs with assessments
        for i in range(5):
            run = _seed_run(
                session,
                status="completed",
                outcome="abstain",
                idempotency_key=f"cal-abs-{i}",
                mission_charter_id=7,
            )
            outcome_ledger.record_outcome(
                session,
                run.id,
                outcome_label="not_adopted",
                abstention_assessment=(
                    "abstention_was_right" if i < 3 else "should_have_selected"
                ),
                idempotency_key=f"cal-abs-out-{i}",
            )
        session.commit()

        report = outcome_ledger.calibration_report(session, min_sample=5)
        overall = report["overall"]
        assert overall["adopted_rate"] == pytest.approx(0.8)
        assert overall["reversed_rate"] == pytest.approx(0.2)
        assert overall["abstention_was_right_rate"] == pytest.approx(0.6)
        assert overall["adverse_materialized_rate"] == pytest.approx(0.6)
        assert "7" in report["by_mission_charter_id"]
        assert "9" in report["by_mission_charter_id"]

        charter7 = outcome_ledger.calibration_report(
            session, mission_charter_id=7, min_sample=3
        )
        assert charter7["overall"]["sample"]["decision_runs_with_outcome"] == 3


def test_idempotency_key_returns_existing(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        run = _seed_run(session, idempotency_key="idemp-run")
        session.commit()
        first = outcome_ledger.record_outcome(
            session,
            run.id,
            outcome_label="adopted",
            note="first",
            idempotency_key="same-key",
        )
        session.commit()
        second = outcome_ledger.record_outcome(
            session,
            run.id,
            outcome_label="reversed",
            note="second-should-not-apply",
            idempotency_key="same-key",
        )
        session.commit()
        assert first.id == second.id
        assert second.outcome_label == "adopted"
        assert second.note == "first"
        count = len(outcome_ledger.list_outcomes(session, run.id))
        assert count == 1


def test_insufficient_sample_string(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        for i in range(3):
            run = _seed_run(session, idempotency_key=f"small-{i}")
            outcome_ledger.record_outcome(
                session,
                run.id,
                outcome_label="adopted",
                idempotency_key=f"small-out-{i}",
            )
        session.commit()
        report = outcome_ledger.calibration_report(session, min_sample=5)
        assert report["overall"]["adopted_rate"] == "insufficient_sample(n<5)"
        assert report["overall"]["reversed_rate"] == "insufficient_sample(n<5)"
        assert report["overall"]["sample"]["decision_runs_with_outcome"] == 3


def test_epistemic_isolation_outcomes_never_enter_prompts(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
) -> None:
    """Behavioral: flood outcomes, re-run deliberation — prompts/digests unchanged by ledger."""
    marker_note = "OUTCOME_LEDGER_SECRET_MARKER_SHOULD_NEVER_APPEAR_IN_PROMPT"
    with session_factory() as session:
        pack_id = _install_minimal(
            session, temp_settings, tmp_path, pack_id="pack.outcome-epistemic"
        )
        provider_a = RecordingStubProvider()
        run_a = deliberation.run_deliberation(
            session,
            _load_mission(),
            pack_ids=[pack_id],
            impact_baseline_pack_ids=[pack_id],
            idempotency_key="epistemic-run-a",
            provider=provider_a,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run_a.status == "completed"
        digests_a = {
            call.stage: call.prompt_digest
            for call in session.scalars(
                select(DeliberationStageCall).where(DeliberationStageCall.run_id == run_a.id)
            ).all()
            if call.prompt_digest
        }
        assert digests_a

        # Flood ledger with distinctive notes/labels for this completed run.
        for i in range(12):
            outcome_ledger.record_outcome(
                session,
                run_a.id,
                outcome_label="reversed" if i % 2 else "adopted",
                scenario_assessments={
                    "expected": "materialized",
                    "adverse": "materialized",
                },
                note=f"{marker_note}-{i}",
                idempotency_key=f"epistemic-flood-{i}",
            )
        session.commit()
        assert session.scalar(select(DeliberationOutcome).limit(1)) is not None

        # Pure builder with fixed inputs is invariant (no session / no ledger).
        mission = _load_mission()
        snaps = [
            {
                "snapshot_key": "snap:1",
                "global_evidence_id": "g1",
                "prompt_visible_payload": {"text": "Supported claim evidence text."},
            }
        ]
        before = build_stage_prompt(
            "deliberation_decision",
            mission=mission,
            evidence_snapshots=snaps,
            prior_artifacts={},
        )
        after = build_stage_prompt(
            "deliberation_decision",
            mission=mission,
            evidence_snapshots=snaps,
            prior_artifacts={},
        )
        assert prompt_digest(before) == prompt_digest(after)
        assert marker_note not in before

        provider_b = RecordingStubProvider()
        run_b = deliberation.run_deliberation(
            session,
            _load_mission(),
            pack_ids=[pack_id],
            impact_baseline_pack_ids=[pack_id],
            idempotency_key="epistemic-run-b",
            provider=provider_b,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run_b.status == "completed"
        digests_b = {
            call.stage: call.prompt_digest
            for call in session.scalars(
                select(DeliberationStageCall).where(DeliberationStageCall.run_id == run_b.id)
            ).all()
            if call.prompt_digest
        }
        assert digests_a.keys() == digests_b.keys()
        for stage in digests_a:
            assert digests_a[stage] == digests_b[stage], f"prompt digest drift on {stage}"

        # No outcome marker / ledger fields leak into recorded LLM prompts.
        for stage, prompt in provider_b.prompts:
            assert marker_note not in prompt, f"outcome note leaked into {stage}"
            assert "deliberation_outcomes" not in prompt
            assert "OUTCOME_LEDGER" not in prompt
            assert "adopted_modified" not in prompt  # ledger label not in stage schema

        # Dossier of original run must not gain outcome ledger fields.
        dossier = session.scalar(
            select(DeliberationDossier).where(DeliberationDossier.run_id == run_a.id)
        )
        assert dossier is not None
        dossier_blob = json.dumps(dossier.dossier_json, ensure_ascii=False)
        assert marker_note not in dossier_blob
        assert "outcome_label" not in dossier.dossier_json
        assert "scenario_assessments" not in dossier_blob or "scenario_assessments" not in str(
            dossier.dossier_json.get("outcomes") if isinstance(dossier.dossier_json, dict) else ""
        )


def test_api_auth_and_sanitize(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
) -> None:
    with session_factory() as session:
        run = _seed_run(session, idempotency_key="api-outcome-run")
        session.commit()
        run_id = run.id

    app = create_app(settings=temp_settings, session_factory=session_factory)
    with TestClient(app) as client:
        # Missing API key
        denied = client.post(
            f"/v1/deliberations/{run_id}/outcomes",
            json={"outcome_label": "adopted"},
            headers={"Idempotency-Key": "api-out-1"},
        )
        assert denied.status_code == 401

        headers = {
            temp_settings.api_key_header: temp_settings.api_key or "",
            "Idempotency-Key": "api-out-1",
        }
        created = client.post(
            f"/v1/deliberations/{run_id}/outcomes",
            json={
                "outcome_label": "adopted",
                "scenario_assessments": {"expected": "materialized"},
                "note": "ok note without secrets",
                "noted_by": "tester",
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["outcome"]["outcome_label"] == "adopted"
        assert "idempotency_key" not in body["outcome"]

        # Same idempotency key → no duplicate
        again = client.post(
            f"/v1/deliberations/{run_id}/outcomes",
            json={"outcome_label": "reversed", "note": "should not replace"},
            headers=headers,
        )
        assert again.status_code == 200
        assert again.json()["outcome"]["id"] == body["outcome"]["id"]
        assert again.json()["outcome"]["outcome_label"] == "adopted"

        listed = client.get(
            f"/v1/deliberations/{run_id}/outcomes",
            headers={temp_settings.api_key_header: temp_settings.api_key or ""},
        )
        assert listed.status_code == 200
        assert len(listed.json()["outcomes"]) == 1

        # Incomplete run
        with session_factory() as session:
            incomplete = _seed_run(
                session, status="created", outcome=None, idempotency_key="api-inc"
            )
            session.commit()
            incomplete_id = incomplete.id
        bad = client.post(
            f"/v1/deliberations/{incomplete_id}/outcomes",
            json={"outcome_label": "adopted"},
            headers={
                temp_settings.api_key_header: temp_settings.api_key or "",
                "Idempotency-Key": "api-out-bad",
            },
        )
        assert bad.status_code == 409
        assert bad.json()["detail"]["code"] == "outcome_run_not_completed"

        # Sanitize: storage-like path in note is redacted
        secret_headers = {
            temp_settings.api_key_header: temp_settings.api_key or "",
            "Idempotency-Key": "api-out-secret",
        }
        secret = client.post(
            f"/v1/deliberations/{run_id}/outcomes",
            json={
                "outcome_label": "not_adopted",
                "note": "see file:///Users/secret/ledger.txt",
            },
            headers=secret_headers,
        )
        assert secret.status_code == 200
        assert secret.json()["outcome"]["note"] == "[redacted]"

        cal = client.get(
            "/v1/calibration",
            headers={temp_settings.api_key_header: temp_settings.api_key or ""},
        )
        assert cal.status_code == 200
        assert "overall" in cal.json()
        assert "idempotency_key" not in json.dumps(cal.json())
