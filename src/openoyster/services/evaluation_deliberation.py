"""Deliberation-engine gold-set harness (quality benchmark, not structural gates).

Runs each scenario in an isolated DB: install packs → run_deliberation →
compare outcome / abstention reasons / critic issue codes against scenario.json.

Stub providers exercise harness wiring only; they do not prove judgment quality.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import init_db, make_engine, make_session_factory
from ..deliberation_contracts import Mission
from ..llm import LLMProvider
from ..models import DeliberationArtifact, DeliberationRun, PackInstall
from . import deliberation, opencrab_packs

Verdict = Literal["pass", "fail"]
DEFAULT_SCENARIOS_DIR = Path("tests/fixtures/deliberation_goldset")
JUDGE_NOTE_STUB = (
    "stub provider does not prove judgment quality; structural retrieval "
    "abstentions (no_evidence / no_match) are deterministic, but select / "
    "critic / constraint scenarios require a real model"
)
JUDGE_NOTE_REAL = (
    "real-model scores are run-local only; do not commit measured quality "
    "numbers as permanent acceptance without human review"
)


@dataclass
class PlantedFlaw:
    kind: str
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioSpec:
    scenario_id: str
    description: str
    expected_outcome: Literal["select", "abstain"]
    expected_abstention_reasons: list[str] = field(default_factory=list)
    expected_critic_issue_codes: list[str] = field(default_factory=list)
    expected_retrieval_status: str | None = None
    planted_flaws: list[PlantedFlaw] = field(default_factory=list)
    path: Path | None = None
    mission: Mission | None = None
    pack_dirs: list[Path] = field(default_factory=list)


@dataclass
class ScenarioActual:
    outcome: str | None
    abstention_reasons: list[str] = field(default_factory=list)
    critic_issue_codes: list[str] = field(default_factory=list)
    retrieval_status: str | None = None
    run_status: str | None = None
    run_id: int | None = None


@dataclass
class ScenarioResult:
    scenario_id: str
    expected: dict[str, Any]
    actual: dict[str, Any]
    verdict: Verdict
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeliberationGoldsetReport:
    kind: str = "deliberation_goldset"
    provider: str = ""
    model: str | None = None
    scenarios_seen: int = 0
    scenarios_evaluated: int = 0
    results: list[ScenarioResult] = field(default_factory=list)
    aggregates: dict[str, float] = field(default_factory=dict)
    judge_note: str = JUDGE_NOTE_STUB
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "provider": self.provider,
            "model": self.model,
            "scenarios_seen": self.scenarios_seen,
            "scenarios_evaluated": self.scenarios_evaluated,
            "results": [r.to_dict() for r in self.results],
            "aggregates": self.aggregates,
            "judge_note": self.judge_note,
            "provenance": self.provenance,
        }


def load_scenario(scenario_dir: Path) -> ScenarioSpec:
    """Load one scenario directory: scenario.json + mission.json + packs/*."""
    scenario_dir = scenario_dir.resolve()
    scenario_path = scenario_dir / "scenario.json"
    mission_path = scenario_dir / "mission.json"
    packs_root = scenario_dir / "packs"
    if not scenario_path.is_file():
        raise FileNotFoundError(f"missing scenario.json: {scenario_path}")
    if not mission_path.is_file():
        raise FileNotFoundError(f"missing mission.json: {mission_path}")
    if not packs_root.is_dir():
        raise FileNotFoundError(f"missing packs/: {packs_root}")

    raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    scenario_id = str(raw.get("scenario_id") or scenario_dir.name)
    outcome = raw.get("expected_outcome")
    if outcome not in {"select", "abstain"}:
        raise ValueError(f"{scenario_id}: expected_outcome must be select|abstain")

    planted: list[PlantedFlaw] = []
    for item in raw.get("planted_flaws") or []:
        if not isinstance(item, dict):
            raise ValueError(f"{scenario_id}: planted_flaws entries must be objects")
        kind = str(item.get("kind") or "")
        if not kind:
            raise ValueError(f"{scenario_id}: planted_flaw missing kind")
        extra = {k: v for k, v in item.items() if k not in {"kind", "description"}}
        planted.append(
            PlantedFlaw(
                kind=kind,
                description=str(item.get("description") or ""),
                extra=extra,
            )
        )

    pack_dirs = sorted(
        path
        for path in packs_root.iterdir()
        if path.is_dir() and (path / "manifest.json").is_file()
    )
    if not pack_dirs:
        raise ValueError(f"{scenario_id}: no pack directories with manifest.json under packs/")

    mission = Mission.model_validate(json.loads(mission_path.read_text(encoding="utf-8")))
    retrieval = raw.get("expected_retrieval_status")
    return ScenarioSpec(
        scenario_id=scenario_id,
        description=str(raw.get("description") or ""),
        expected_outcome=outcome,
        expected_abstention_reasons=list(raw.get("expected_abstention_reasons") or []),
        expected_critic_issue_codes=list(raw.get("expected_critic_issue_codes") or []),
        expected_retrieval_status=str(retrieval) if retrieval else None,
        planted_flaws=planted,
        path=scenario_dir,
        mission=mission,
        pack_dirs=pack_dirs,
    )


def discover_scenarios(scenarios_dir: Path) -> list[ScenarioSpec]:
    root = scenarios_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"scenarios directory not found: {root}")
    specs: list[ScenarioSpec] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "scenario.json").is_file():
            specs.append(load_scenario(child))
    return specs


def _secondary_model_for_settings(settings: Settings) -> str | None:
    match settings.critic2_provider:
        case "claude-cli":
            return settings.claude_model
        case "codex":
            return settings.llm_model
        case "stub":
            return "stub"
        case _:
            return None


def _provider_provenance(
    provider: LLMProvider, *, settings: Settings | None = None
) -> dict[str, Any]:
    profile = provider.stage_profile("deliberation_decision")
    payload: dict[str, Any] = {
        "provider": getattr(provider, "name", type(provider).__name__),
        "model": profile.get("model"),
        "effort": profile.get("effort"),
        "stage_profile": profile,
    }
    if settings is not None:
        payload["secondary_provider"] = settings.critic2_provider
        payload["secondary_model"] = _secondary_model_for_settings(settings)
    return payload


def _isolated_settings(base: Settings, work_root: Path) -> Settings:
    workspace = work_root / "workspace"
    inbox = workspace / "inbox"
    archive = workspace / "archive"
    inbox.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    db_path = work_root / "goldset.db"
    return Settings(
        db_url=f"sqlite:///{db_path}",
        workspace=workspace,
        inbox_dir=inbox,
        archive_dir=archive,
        llm_provider=base.llm_provider,
        critic2_provider=base.critic2_provider,
        llm_api_key=base.llm_api_key,
        llm_base_url=base.llm_base_url,
        llm_model=base.llm_model,
        llm_timeout_seconds=base.llm_timeout_seconds,
        llm_max_retries=base.llm_max_retries,
        codex_binary=base.codex_binary,
        codex_batch_size=base.codex_batch_size,
        codex_timeout_seconds=base.codex_timeout_seconds,
        codex_config_dir=base.codex_config_dir,
        # critic2/claude-cli isolation: copy full secondary provider config.
        claude_binary=base.claude_binary,
        claude_timeout_seconds=base.claude_timeout_seconds,
        claude_model=base.claude_model,
        api_key=base.api_key or "goldset-eval",
        api_allow_unsafe_no_key=base.api_allow_unsafe_no_key,
        scheduler_tick_seconds=base.scheduler_tick_seconds,
    )


def _install_packs(
    session: Session,
    settings: Settings,
    pack_dirs: list[Path],
    copy_root: Path,
) -> list[str]:
    pack_ids: list[str] = []
    for index, pack_dir in enumerate(pack_dirs):
        dest = copy_root / f"pack-{index}-{pack_dir.name}"
        shutil.copytree(pack_dir, dest)
        result = opencrab_packs.install_pack(
            session,
            dest,
            workspace=settings.workspace,
            profile="compatible",
        )
        session.commit()
        install = session.get(PackInstall, result.pack_install_id)
        if install is None:
            raise RuntimeError(f"pack install missing after install: {pack_dir}")
        pack_ids.append(install.pack_id)
    return pack_ids


def _artifact_payload(
    session: Session, run_id: int, kind: str
) -> dict[str, Any] | None:
    art = session.scalar(
        select(DeliberationArtifact).where(
            DeliberationArtifact.run_id == run_id,
            DeliberationArtifact.kind == kind,
        )
    )
    if art is None:
        return None
    payload = art.payload_json
    return payload if isinstance(payload, dict) else None


def _extract_actual(session: Session, run: DeliberationRun) -> ScenarioActual:
    decision = _artifact_payload(session, run.id, "decision") or {}
    knowledge = _artifact_payload(session, run.id, "knowledge_requests") or {}
    critic = _artifact_payload(session, run.id, "critic_result") or {}

    reasons = list(decision.get("abstention_reasons") or [])
    outcome = decision.get("outcome") or run.outcome

    critic_codes: list[str] = []
    for issue in critic.get("issues") or []:
        if isinstance(issue, dict) and issue.get("code"):
            critic_codes.append(str(issue["code"]))
    for finding in critic.get("findings") or []:
        if isinstance(finding, dict) and finding.get("issue_code"):
            code = str(finding["issue_code"])
            if code not in critic_codes:
                critic_codes.append(code)

    retrieval_status: str | None = None
    for request in knowledge.get("knowledge_requests") or []:
        if isinstance(request, dict) and request.get("retrieval_status"):
            retrieval_status = str(request["retrieval_status"])
            break

    return ScenarioActual(
        outcome=str(outcome) if outcome is not None else None,
        abstention_reasons=reasons,
        critic_issue_codes=critic_codes,
        retrieval_status=retrieval_status,
        run_status=run.status,
        run_id=run.id,
    )


def compare_scenario(spec: ScenarioSpec, actual: ScenarioActual) -> ScenarioResult:
    """Compare expected vs actual. Abstention/critic lists use any-of match."""
    notes: list[str] = []
    verdict: Verdict = "pass"

    if actual.outcome != spec.expected_outcome:
        verdict = "fail"
        notes.append(
            f"outcome mismatch: expected={spec.expected_outcome!r} actual={actual.outcome!r}"
        )

    if spec.expected_outcome == "abstain" and spec.expected_abstention_reasons:
        if not any(r in actual.abstention_reasons for r in spec.expected_abstention_reasons):
            verdict = "fail"
            notes.append(
                "abstention_reasons miss: expected any of "
                f"{spec.expected_abstention_reasons!r}, actual={actual.abstention_reasons!r}"
            )
        else:
            notes.append(
                f"abstention_reasons ok (any-of {spec.expected_abstention_reasons!r})"
            )

    if spec.expected_critic_issue_codes:
        if not any(c in actual.critic_issue_codes for c in spec.expected_critic_issue_codes):
            verdict = "fail"
            notes.append(
                "critic issue miss: expected any of "
                f"{spec.expected_critic_issue_codes!r}, actual={actual.critic_issue_codes!r}"
            )
        else:
            notes.append(
                f"critic issues ok (any-of {spec.expected_critic_issue_codes!r})"
            )

    if spec.expected_retrieval_status is not None:
        if actual.retrieval_status != spec.expected_retrieval_status:
            verdict = "fail"
            notes.append(
                "retrieval_status mismatch: expected="
                f"{spec.expected_retrieval_status!r} actual={actual.retrieval_status!r}"
            )
        else:
            notes.append(f"retrieval_status ok ({spec.expected_retrieval_status})")

    if actual.run_status not in {None, "completed"} and verdict == "pass":
        # Soft note only when structural fields already match; failed runs rarely pass.
        notes.append(f"run_status={actual.run_status!r}")

    expected_payload = {
        "outcome": spec.expected_outcome,
        "abstention_reasons": list(spec.expected_abstention_reasons),
        "critic_issue_codes": list(spec.expected_critic_issue_codes),
        "retrieval_status": spec.expected_retrieval_status,
    }
    actual_payload = {
        "outcome": actual.outcome,
        "abstention_reasons": list(actual.abstention_reasons),
        "critic_issue_codes": list(actual.critic_issue_codes),
        "retrieval_status": actual.retrieval_status,
        "run_status": actual.run_status,
        "run_id": actual.run_id,
    }
    return ScenarioResult(
        scenario_id=spec.scenario_id,
        expected=expected_payload,
        actual=actual_payload,
        verdict=verdict,
        notes=notes,
    )


def run_scenario(
    spec: ScenarioSpec,
    *,
    provider: LLMProvider,
    settings: Settings,
) -> ScenarioResult:
    """Execute one scenario in a throwaway DB/workspace under settings.workspace parent."""
    if spec.mission is None:
        raise ValueError(f"{spec.scenario_id}: mission not loaded")
    work_root = Path(tempfile.mkdtemp(prefix=f"oo-delib-gold-{spec.scenario_id}-"))
    engine = None
    try:
        isolated = _isolated_settings(settings, work_root)
        isolated.ensure_workspace()
        engine = make_engine(isolated)
        init_db(engine)
        factory = make_session_factory(engine)
        with factory() as session:
            pack_ids = _install_packs(
                session, isolated, spec.pack_dirs, work_root / "pack-src"
            )
            run = deliberation.run_deliberation(
                session,
                spec.mission,
                pack_ids=pack_ids,
                impact_baseline_pack_ids=list(pack_ids),
                idempotency_key=f"goldset-{spec.scenario_id}",
                provider=provider,
                settings=isolated,
                allow_compatible_packs=True,
            )
            session.commit()
            actual = _extract_actual(session, run)
        return compare_scenario(spec, actual)
    except Exception as exc:  # harness reports failure; does not crash the suite
        return ScenarioResult(
            scenario_id=spec.scenario_id,
            expected={
                "outcome": spec.expected_outcome,
                "abstention_reasons": list(spec.expected_abstention_reasons),
                "critic_issue_codes": list(spec.expected_critic_issue_codes),
                "retrieval_status": spec.expected_retrieval_status,
            },
            actual={"error": f"{type(exc).__name__}: {exc}"},
            verdict="fail",
            notes=[f"scenario execution error: {type(exc).__name__}: {exc}"],
        )
    finally:
        if engine is not None:
            engine.dispose()
        shutil.rmtree(work_root, ignore_errors=True)


def _aggregates(results: list[ScenarioResult], specs: list[ScenarioSpec]) -> dict[str, float]:
    abstain_specs = [s for s in specs if s.expected_outcome == "abstain"]
    select_specs = [s for s in specs if s.expected_outcome == "select"]
    critic_specs = [s for s in specs if s.expected_critic_issue_codes]
    result_by_id = {r.scenario_id: r for r in results}

    def _rate(ids: list[ScenarioSpec], predicate) -> float:
        if not ids:
            return 0.0
        hits = sum(1 for s in ids if predicate(result_by_id.get(s.scenario_id), s))
        return hits / len(ids)

    def _passed(result: ScenarioResult | None, _spec: ScenarioSpec) -> bool:
        return result is not None and result.verdict == "pass"

    def _abstain_appropriate(result: ScenarioResult | None, _spec: ScenarioSpec) -> bool:
        if result is None:
            return False
        return result.actual.get("outcome") == "abstain"

    def _select_correct(result: ScenarioResult | None, _spec: ScenarioSpec) -> bool:
        if result is None:
            return False
        return result.actual.get("outcome") == "select" and result.verdict == "pass"

    def _critic_hit(result: ScenarioResult | None, spec: ScenarioSpec) -> bool:
        if result is None:
            return False
        actual_codes = set(result.actual.get("critic_issue_codes") or [])
        return any(code in actual_codes for code in spec.expected_critic_issue_codes)

    pass_count = sum(1 for r in results if r.verdict == "pass")
    evaluated = len(results)
    return {
        "pass_rate": (pass_count / evaluated) if evaluated else 0.0,
        "abstention_appropriateness": _rate(abstain_specs, _abstain_appropriate),
        "critic_hit_rate": _rate(critic_specs, _critic_hit),
        "select_accuracy": _rate(select_specs, _select_correct),
        "scenarios_passed": float(pass_count),
        "scenarios_failed": float(evaluated - pass_count),
    }


def evaluate_deliberation_goldset(
    provider: LLMProvider,
    *,
    scenarios_dir: Path | None = None,
    settings: Settings | None = None,
    scenario_ids: list[str] | None = None,
) -> DeliberationGoldsetReport:
    """Run the deliberation gold set against an injected LLM provider."""
    root = (scenarios_dir or DEFAULT_SCENARIOS_DIR).resolve()
    runtime_settings = settings or get_settings()
    specs = discover_scenarios(root)
    if scenario_ids is not None:
        wanted = set(scenario_ids)
        specs = [s for s in specs if s.scenario_id in wanted]

    provenance = _provider_provenance(provider, settings=runtime_settings)
    provider_name = str(provenance.get("provider") or getattr(provider, "name", "unknown"))
    model = provenance.get("model")
    is_stub = provider_name == "stub" or model == "stub"

    results: list[ScenarioResult] = []
    for spec in specs:
        results.append(run_scenario(spec, provider=provider, settings=runtime_settings))

    return DeliberationGoldsetReport(
        provider=provider_name,
        model=str(model) if model is not None else None,
        scenarios_seen=len(specs),
        scenarios_evaluated=len(results),
        results=results,
        aggregates=_aggregates(results, specs),
        judge_note=JUDGE_NOTE_STUB if is_stub else JUDGE_NOTE_REAL,
        provenance=provenance,
    )
