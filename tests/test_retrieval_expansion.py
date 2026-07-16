"""Cross-language Pack retrieval: manifest hints + conditional query expansion."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from openoyster.config import Settings
from openoyster.deliberation_contracts import CitationAnchor, Mission
from openoyster.llm import LLMProvider
from openoyster.models import (
    DeliberationArtifact,
    DeliberationEvidenceSnapshot,
    DeliberationStageCall,
    PackEvidence,
    PackInstall,
)
from openoyster.schemas import TextAnalysis
from openoyster.services import deliberation, opencrab_packs, pack_retrieval
from openoyster.services.deliberation_gates import (
    EvidenceSnapshotView,
    StageGateError,
    validate_anchor,
)
from openoyster.services.llm_judges import stub_query_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINIMAL_FIXTURE = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
KOREAN_HINTS_FIXTURE = (
    PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f8-korean-hints"
)
MISSION_PATH = PROJECT_ROOT / "tests/fixtures/deliberation_d1/mission_happy.json"

KOREAN_DECISION_QUESTION = "이 주장을 받아들여야 하는가?"


def _copy_fixture(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest)
    return dest


def _load_mission() -> Mission:
    return Mission.model_validate(json.loads(MISSION_PATH.read_text(encoding="utf-8")))


def _korean_mission() -> Mission:
    return _load_mission().model_copy(
        update={
            "decision_question": KOREAN_DECISION_QUESTION,
            "goal": "설치된 Pack의 주장을 의사결정 근거로 수용할지 결정한다",
        }
    )


def _install_fixture(
    session: Session,
    settings: Settings,
    tmp_path: Path,
    fixture: Path,
    *,
    dirname: str = "pack-a",
) -> PackInstall:
    pack_dir = _copy_fixture(fixture, tmp_path / dirname)
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


class CountingProvider(LLMProvider):
    name = "counting-stub"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("deliberation must not call analyse_batch")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.calls.append(stage)
        return stub_query_json(prompt, stage)


class ExpandingProvider(CountingProvider):
    """Expansion returns English lexical queries that match p0-f1 evidence."""

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.calls.append(stage)
        if stage == "retrieval_query_expansion":
            return {"queries": ["source supports this claim", "supports this claim"]}
        return stub_query_json(prompt, stage)


def test_korean_mission_without_hints_abstains_no_match(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """(a) Baseline: Korean DQ x English pack without hints -> no_match abstain."""
    provider = CountingProvider()
    mission = _korean_mission()
    with session_factory() as session:
        install = _install_fixture(session, temp_settings, tmp_path, MINIMAL_FIXTURE)
        evidence_count = int(
            session.scalar(
                select(func.count())
                .select_from(PackEvidence)
                .where(PackEvidence.pack_install_id == install.id)
            )
            or 0
        )
        assert evidence_count > 0

        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[],
            idempotency_key="ko-no-hints-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert run.status == "completed"
        assert run.outcome == "abstain"
        # Expansion may fire once; still no match.
        assert "retrieval_query_expansion" in provider.calls
        assert provider.calls.count("retrieval_query_expansion") == 1
        assert "deliberation_beliefs" not in provider.calls
        knowledge = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "knowledge_requests",
            )
        )
        assert knowledge is not None
        requests = knowledge.payload_json.get("knowledge_requests") or []
        assert requests[0]["retrieval_status"] == "no_match_in_pack_evidence"
        trace = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "retrieval_trace",
            )
        )
        assert trace is not None


def test_korean_mission_with_manifest_hints_finds_evidence(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """(b) Korean DQ x p0-f8 hints -> evidence via manifest_hint, run proceeds."""
    provider = CountingProvider()
    mission = _korean_mission()
    with session_factory() as session:
        install = _install_fixture(
            session, temp_settings, tmp_path, KOREAN_HINTS_FIXTURE, dirname="pack-hints"
        )
        # Direct retrieval should already succeed via hints (no expansion needed).
        hits = pack_retrieval.search_pack_context(
            session,
            mission.decision_question,
            pack_install_ids=[install.id],
        )
        assert hits.evidence
        assert any(hit.matched_via == "manifest_hint" for hit in hits.hits)

        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="ko-hints-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert run.status == "completed"
        # Happy-path deliberation stages only — expansion must not fire.
        assert provider.calls == [
            "deliberation_beliefs",
            "deliberation_options",
            "deliberation_scenarios",
            "deliberation_critic",
            "deliberation_decision",
        ]
        assert run.llm_attempt_count == 5
        snaps = session.scalars(
            select(DeliberationEvidenceSnapshot).where(
                DeliberationEvidenceSnapshot.run_id == run.id
            )
        ).all()
        assert snaps
        # Hints must never land in prompt-visible evidence payload.
        for snap in snaps:
            payload = snap.prompt_visible_payload_json or {}
            blob = json.dumps(payload, ensure_ascii=False)
            for hint in install_retrieval_hints_from_install(install):
                assert hint not in blob


def install_retrieval_hints_from_install(install: PackInstall) -> list[str]:
    return pack_retrieval.install_retrieval_hints(install)


def test_expansion_stub_recovers_without_hints(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """(c) No hints + expansion provider → expansion stage + retrieval_trace + evidence."""
    provider = ExpandingProvider()
    mission = _korean_mission()
    with session_factory() as session:
        install = _install_fixture(session, temp_settings, tmp_path, MINIMAL_FIXTURE)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="ko-expand-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert run.status == "completed"
        assert provider.calls[0] == "retrieval_query_expansion"
        assert provider.calls[1:] == [
            "deliberation_beliefs",
            "deliberation_options",
            "deliberation_scenarios",
            "deliberation_critic",
            "deliberation_decision",
        ]
        assert run.llm_attempt_count == 6
        stage = session.scalar(
            select(DeliberationStageCall).where(
                DeliberationStageCall.run_id == run.id,
                DeliberationStageCall.stage == "retrieval_query_expansion",
            )
        )
        assert stage is not None
        assert stage.status == "succeeded"
        trace = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "retrieval_trace",
            )
        )
        assert trace is not None
        payload = trace.payload_json or {}
        assert payload.get("matched_via") == "query_expansion"
        assert payload.get("expanded_queries")
        assert payload.get("used_query")
        snaps = session.scalars(
            select(DeliberationEvidenceSnapshot).where(
                DeliberationEvidenceSnapshot.run_id == run.id
            )
        ).all()
        assert snaps


def test_happy_path_remains_five_llm_calls(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """(d) Primary retrieval success keeps the 5-call deliberation budget."""
    provider = CountingProvider()
    mission = _load_mission()
    with session_factory() as session:
        install = _install_fixture(session, temp_settings, tmp_path, MINIMAL_FIXTURE)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="happy-5call-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        assert provider.calls == [
            "deliberation_beliefs",
            "deliberation_options",
            "deliberation_scenarios",
            "deliberation_critic",
            "deliberation_decision",
        ]
        assert run.llm_attempt_count == 5
        assert "retrieval_query_expansion" not in provider.calls


def test_manifest_hints_are_not_citable_quote_anchors(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """(e) Quoting a retrieval_hints string against evidence snapshot is rejected."""
    with session_factory() as session:
        install = _install_fixture(
            session, temp_settings, tmp_path, KOREAN_HINTS_FIXTURE, dirname="pack-hints-e"
        )
        hints = pack_retrieval.install_retrieval_hints(install)
        assert hints
        hint_quote = hints[0]
        assert len(hint_quote) >= 12

        hits = pack_retrieval.search_pack_context(
            session,
            KOREAN_DECISION_QUESTION,
            pack_install_ids=[install.id],
        )
        assert hits.evidence
        row = hits.evidence[0]
        payload = {
            "local_evidence_id": row.local_evidence_id,
            "global_evidence_id": row.global_evidence_id,
            "kind": row.kind,
            "text": row.text,
            "source": {"title": (row.source_json or {}).get("title")},
            "location": row.location_json or {},
            "record_hash": row.record_hash,
        }
        # Ensure the hint string is not inside the evidence citation surface.
        assert hint_quote not in json.dumps(payload, ensure_ascii=False)
        assert hint_quote not in (row.text or "")

        snapshot = EvidenceSnapshotView(
            snapshot_key="snap:1",
            db_id=1,
            global_evidence_id=row.global_evidence_id,
            text=str(payload.get("text") or ""),
            payload=payload,
            pack_install_id=row.pack_install_id,
            record_hash=row.record_hash,
        )
        anchor = CitationAnchor.model_validate(
            {"evidence_snapshot_id": "snap:1", "quote": hint_quote}
        )
        with pytest.raises(StageGateError) as caught:
            validate_anchor(anchor, {"snap:1": snapshot})
        assert caught.value.code == "quote_mismatch"


def test_invalid_retrieval_hints_shape_is_ignored_at_admission(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """Non-array retrieval_hints never fails admission (info diagnostic only)."""
    pack_dir = _copy_fixture(MINIMAL_FIXTURE, tmp_path / "bad-hints")
    manifest_path = pack_dir / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["pack_id"] = "p0-bad-hints-shape"
    payload["retrieval_hints"] = {"not": "an-array"}
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    validation = opencrab_packs.validate_pack_directory(pack_dir, profile="compatible")
    assert validation.status == "pass"
    codes = {issue["code"] for issue in validation.issues}
    assert "ignored_retrieval_hints" in codes

    with session_factory() as session:
        result = opencrab_packs.install_pack(
            session, pack_dir, workspace=temp_settings.workspace, profile="compatible"
        )
        session.commit()
        assert result.status == "active"
        install = session.get(PackInstall, result.pack_install_id)
        assert install is not None
        assert pack_retrieval.install_retrieval_hints(install) == []
