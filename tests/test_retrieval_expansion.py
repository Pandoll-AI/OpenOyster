"""Cross-language Pack retrieval: manifest hints + conditional query expansion."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from openoyster.cli import _sanitize_deliberation_value
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
from openoyster.services.deliberation_dossier import (
    build_dossier_payload,
    render_dossier_markdown,
)
from openoyster.services.deliberation_gates import (
    EvidenceSnapshotView,
    StageGateError,
    validate_anchor,
)
from openoyster.services.llm_judges import stub_query_json
from openoyster.utils import sha256_text

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
        assert int(payload.get("expanded_query_count") or 0) > 0
        assert payload.get("used_query_digest")
        assert len(str(payload["used_query_digest"])) == 64
        # #8: public retrieval_trace must not carry raw queries / hints.
        blob = json.dumps(payload, ensure_ascii=False)
        assert KOREAN_DECISION_QUESTION not in blob
        assert "source supports this claim" not in blob
        assert "supports this claim" not in blob
        assert payload.get("original_query") is None
        assert payload.get("expanded_queries") in (None, [])
        assert payload.get("used_query") in (None, "")
        assert "pack_metadata" not in payload or payload.get("pack_metadata") in (None, [])
        assert payload.get("original_query_digest")
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


class RejectOncePerStageProvider(ExpandingProvider):
    """Expansion once; each deliberation stage fails gate once then succeeds."""

    def __init__(self) -> None:
        super().__init__()
        self._stage_attempts: dict[str, int] = {}

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.calls.append(stage)
        if stage == "retrieval_query_expansion":
            return {"queries": ["source supports this claim", "supports this claim"]}
        n = self._stage_attempts.get(stage, 0) + 1
        self._stage_attempts[stage] = n
        if n == 1:
            # Invalid payload → gate reject → one retry.
            return {"not": "a-valid-stage-payload"}
        return stub_query_json(prompt, stage)


def test_expansion_plus_stage_retries_does_not_stuck_run(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """#1: expansion + 1 reject/retry per stage must not raise uncaught budget error."""
    provider = RejectOncePerStageProvider()
    mission = _korean_mission()
    with session_factory() as session:
        install = _install_fixture(session, temp_settings, tmp_path, MINIMAL_FIXTURE)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="budget-reject-retry-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        # 1 expansion (aux) + 5 stages x 2 attempts (core) = 11 total recorded.
        assert run.status == "completed"
        assert run.failure_code is None
        assert run.lease_owner is None
        assert run.completed_at is not None
        assert run.llm_attempt_count == 11
        assert provider.calls[0] == "retrieval_query_expansion"


def test_llm_attempt_budget_exhausted_is_handled_terminal(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1: core budget exhaustion → failed_execution + llm_attempt_budget_exhausted."""
    # Expansion uses the auxiliary budget; core gate is independent.
    monkeypatch.setattr(deliberation, "CORE_STAGE_MAX_ATTEMPTS", 0)
    provider = ExpandingProvider()
    mission = _korean_mission()
    with session_factory() as session:
        install = _install_fixture(session, temp_settings, tmp_path, MINIMAL_FIXTURE)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="budget-exhausted-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        # Expansion may succeed on aux budget; first core stage hits core gate.
        assert run.status == "failed_execution"
        assert run.failure_code == "llm_attempt_budget_exhausted"
        assert run.lease_owner is None
        assert run.lease_until is None
        assert run.completed_at is not None


class Critic2RejectOnceProvider(LLMProvider):
    """Secondary critic rejects gate once, then passes (uses auxiliary budget)."""

    name = "stub"

    def __init__(self) -> None:
        self.attempts = 0

    def analyse_batch(
        self, texts: list[str], policy: dict[str, Any] | None = None
    ) -> list[TextAnalysis]:
        del texts, policy
        raise AssertionError("unused")

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.attempts += 1
        if self.attempts == 1:
            return {"not": "a-valid-critic-payload"}
        return stub_query_json(prompt, stage)


def test_critic2_plus_expansion_and_stage_retries_does_not_starve_core(
    session_factory: sessionmaker[Session],
    temp_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B: expansion + all core stages 1 reject + critic2 reject must complete.

    Worst-case: 1 expansion + 5x2 core + 2 critic2 = 13 total calls. With a single
    shared MAX_LLM_ATTEMPTS=12 this starved decision; separated budgets finish.
    """
    temp_settings.critic2_provider = "stub"
    critic2 = Critic2RejectOnceProvider()
    monkeypatch.setattr(
        deliberation,
        "critic2_provider_from_settings",
        lambda _settings: critic2,
    )
    provider = RejectOncePerStageProvider()
    mission = _korean_mission()
    with session_factory() as session:
        install = _install_fixture(session, temp_settings, tmp_path, MINIMAL_FIXTURE)
        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="budget-critic2-reject-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()
        assert run.status == "completed"
        assert run.failure_code is None
        assert run.lease_owner is None
        assert run.completed_at is not None
        # Total recorded calls: 1 aux expansion + 10 core + 2 aux critic2 = 13.
        assert run.llm_attempt_count == 13
        assert provider.calls[0] == "retrieval_query_expansion"
        assert critic2.attempts == 2
        assert "deliberation_decision" in provider.calls


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


def test_retrieval_trace_exposes_digests_not_raw_queries_or_hints(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    """#8 RED/GREEN: retrieval_trace + dossier carry digests/counts only."""
    secret_hint = "INTERNAL_ALIAS_secret_hint_zz9"
    expansion_query = "source supports this claim"
    provider = ExpandingProvider()
    mission = _korean_mission()
    with session_factory() as session:
        pack_dir = _copy_fixture(MINIMAL_FIXTURE, tmp_path / "pack-trace-redact")
        manifest_path = pack_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["pack_id"] = "p0-trace-redact"
        manifest["title"] = "Secret Pack Title For Trace"
        manifest["retrieval_hints"] = [secret_hint]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        result = opencrab_packs.install_pack(
            session, pack_dir, workspace=temp_settings.workspace, profile="compatible"
        )
        session.commit()
        install = session.get(PackInstall, result.pack_install_id)
        assert install is not None

        run = deliberation.run_deliberation(
            session,
            mission,
            pack_ids=[install.pack_id],
            impact_baseline_pack_ids=[install.pack_id],
            idempotency_key="trace-redact-1",
            provider=provider,
            settings=temp_settings,
            allow_compatible_packs=True,
        )
        session.commit()

        # Korean DQ + English-only evidence → expansion path (hints do not match KO).
        trace = session.scalar(
            select(DeliberationArtifact).where(
                DeliberationArtifact.run_id == run.id,
                DeliberationArtifact.kind == "retrieval_trace",
            )
        )
        assert trace is not None
        payload = trace.payload_json or {}
        assert payload.get("original_query_digest") == sha256_text(KOREAN_DECISION_QUESTION)
        assert int(payload.get("expanded_query_count") or 0) >= 1
        digests = payload.get("expanded_query_digests") or []
        assert sha256_text(expansion_query) in digests
        assert payload.get("used_query_digest") == sha256_text(expansion_query)

        blob = json.dumps(payload, ensure_ascii=False)
        assert KOREAN_DECISION_QUESTION not in blob
        assert expansion_query not in blob
        assert secret_hint not in blob
        assert "Secret Pack Title For Trace" not in blob
        assert "original_query" not in payload
        assert "expanded_queries" not in payload
        assert "used_query" not in payload
        assert "pack_metadata" not in payload

        summary = payload.get("pack_metadata_summary") or []
        assert summary
        assert summary[0].get("hint_count") == 1
        assert summary[0].get("hint_digests") == [sha256_text(secret_hint)]
        assert summary[0].get("title_digest") == sha256_text("Secret Pack Title For Trace")

        dossier = build_dossier_payload(session, run)
        # Mission snapshot may still carry the decision question (expected);
        # retrieval_trace must not re-expose original/expanded query plaintext.
        # (Evidence body may share lexical tokens with expansion queries — only
        # the retrieval_trace subtree is the leak surface under test here.)
        rt = dossier.get("retrieval_trace") or {}
        rt_blob = json.dumps(rt, ensure_ascii=False)
        assert expansion_query not in rt_blob or rt.get("used_query_digest") == sha256_text(
            expansion_query
        )
        # Stronger: raw fields and secret surfaces must be absent from the trace.
        assert "original_query" not in rt
        assert "expanded_queries" not in rt
        assert "used_query" not in rt
        assert KOREAN_DECISION_QUESTION not in rt_blob
        assert secret_hint not in rt_blob
        assert "Secret Pack Title For Trace" not in rt_blob
        # Exact expanded query string as a standalone JSON string value must not appear.
        assert json.dumps(expansion_query, ensure_ascii=False) not in rt_blob
        sanitized_rt = _sanitize_deliberation_value(rt)
        sanitized_blob = json.dumps(sanitized_rt, ensure_ascii=False)
        assert KOREAN_DECISION_QUESTION not in sanitized_blob
        assert secret_hint not in sanitized_blob
        assert json.dumps(expansion_query, ensure_ascii=False) not in sanitized_blob

        # D: markdown renderer uses digest/count fields only — no empty raw labels.
        md = render_dossier_markdown(dossier)
        assert "## Retrieval trace" in md
        assert "Original query digest:" in md
        assert "Expanded query count:" in md
        assert "Used query digest:" in md
        assert "Original query:" not in md
        assert "Used expanded query:" not in md
        assert "Expanded queries (" not in md
        # Raw query plaintext must not appear in the retrieval-trace markdown section.
        rt_section = md.split("## Retrieval trace", 1)[1].split("## ", 1)[0]
        assert KOREAN_DECISION_QUESTION not in rt_section
        assert expansion_query not in rt_section


def test_retrieval_hints_capped_at_count_and_length() -> None:
    """#8: normalize_retrieval_hints enforces 32x200 caps."""
    long_hint = "x" * 500
    many = [f"hint-{i}" for i in range(50)]
    capped = pack_retrieval.normalize_retrieval_hints([*many, long_hint])
    assert len(capped) == pack_retrieval.MAX_RETRIEVAL_HINTS
    assert all(len(h) <= pack_retrieval.MAX_RETRIEVAL_HINT_CHARS for h in capped)
    # Over-length single hint is truncated, not dropped.
    one = pack_retrieval.normalize_retrieval_hints([long_hint])
    assert one == [long_hint[: pack_retrieval.MAX_RETRIEVAL_HINT_CHARS]]


def test_admission_truncates_oversized_retrieval_hints(
    session_factory: sessionmaker[Session], temp_settings: Settings, tmp_path: Path
) -> None:
    pack_dir = _copy_fixture(MINIMAL_FIXTURE, tmp_path / "huge-hints")
    manifest_path = pack_dir / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["pack_id"] = "p0-huge-hints"
    payload["retrieval_hints"] = [f"h{i}-{'y' * 300}" for i in range(40)]
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    validation = opencrab_packs.validate_pack_directory(pack_dir, profile="compatible")
    assert validation.status == "pass"
    codes = {issue["code"] for issue in validation.issues}
    assert "truncated_retrieval_hints" in codes

    with session_factory() as session:
        result = opencrab_packs.install_pack(
            session, pack_dir, workspace=temp_settings.workspace, profile="compatible"
        )
        session.commit()
        install = session.get(PackInstall, result.pack_install_id)
        assert install is not None
        hints = pack_retrieval.install_retrieval_hints(install)
        assert len(hints) == pack_retrieval.MAX_RETRIEVAL_HINTS
        assert all(len(h) <= pack_retrieval.MAX_RETRIEVAL_HINT_CHARS for h in hints)
