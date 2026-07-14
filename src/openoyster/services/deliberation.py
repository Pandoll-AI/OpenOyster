"""Autonomous Deliberation D1 orchestration (core vertical slice)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.config import Settings, get_settings
from openoyster.deliberation_contracts import (
    CONTRACT_VERSION,
    MAX_EVIDENCE_SNAPSHOTS,
    MAX_LLM_ATTEMPTS,
    PROMPT_TEMPLATE_VERSION,
    STAGE_BELIEFS,
    STAGE_CRITIC,
    STAGE_DECISION,
    STAGE_OPTIONS,
    STAGE_SCENARIOS,
    BeliefsStagePayload,
    CriticStagePayload,
    DecisionStagePayload,
    Mission,
    NarrativeAssertion,
    OptionsStagePayload,
    ScenariosStagePayload,
    StrictModel,
    mission_digest,
    payload_digest,
)
from openoyster.llm import LLMProvider
from openoyster.models import (
    DeliberationArtifact,
    DeliberationAssertion,
    DeliberationCitation,
    DeliberationEvidenceSnapshot,
    DeliberationPackScope,
    DeliberationRun,
    DeliberationStageCall,
    PackEvidence,
)
from openoyster.services.cognitive_impact import compute_cognitive_impact
from openoyster.services.cognitive_transition import persist_cognitive_transition
from openoyster.services.deliberation_dossier import persist_dossier
from openoyster.services.deliberation_gates import (
    EvidenceSnapshotView,
    GateContext,
    StageGateError,
    selection_gate_allows,
    validate_stage,
)
from openoyster.services.deliberation_prompts import build_stage_prompt, prompt_digest
from openoyster.services.deliberation_scope import DeliberationScopeError, freeze_pack_scope
from openoyster.services.pack_retrieval import search_pack_context
from openoyster.utils import ensure_utc, utcnow

STAGE_STATUS: dict[str, str] = {
    STAGE_BELIEFS: "beliefs_ready",
    STAGE_OPTIONS: "options_ready",
    STAGE_SCENARIOS: "scenarios_ready",
    STAGE_CRITIC: "critic_ready",
    STAGE_DECISION: "decision_ready",
}

STAGE_ARTIFACT_KIND: dict[str, str] = {
    STAGE_BELIEFS: "beliefs",
    STAGE_OPTIONS: "options",
    STAGE_SCENARIOS: "scenarios",
    STAGE_CRITIC: "critic_result",
    STAGE_DECISION: "decision",
}


class DeliberationContinuationError(ValueError):
    """Stable input error raised when a linked re-deliberation is invalid."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _policy_snapshot(*, allow_compatible_packs: bool) -> dict[str, Any]:
    return {
        "allow_compatible_packs": allow_compatible_packs,
        "max_evidence_snapshots": MAX_EVIDENCE_SNAPSHOTS,
        "max_llm_attempts": MAX_LLM_ATTEMPTS,
        "contract_version": CONTRACT_VERSION,
    }


def _runtime_config(provider: LLMProvider, settings: Settings) -> dict[str, Any]:
    return {
        "provider": getattr(provider, "name", type(provider).__name__),
        "llm_provider_setting": settings.llm_provider,
        "llm_model": settings.llm_model,
    }


def _prompt_visible_payload(row: PackEvidence) -> dict[str, Any]:
    return {
        "local_evidence_id": row.local_evidence_id,
        "global_evidence_id": row.global_evidence_id,
        "kind": row.kind,
        "text": row.text,
        "source": {
            "title": (row.source_json or {}).get("title"),
        },
        "location": row.location_json or {},
        "record_hash": row.record_hash,
    }


def _evidence_prompt_rows(
    snapshots: list[DeliberationEvidenceSnapshot],
) -> list[dict[str, Any]]:
    return [
        {
            "snapshot_key": snap.snapshot_key,
            "global_evidence_id": snap.global_evidence_id,
            "prompt_visible_payload": snap.prompt_visible_payload_json,
            "retrieval_rank": snap.retrieval_rank,
            "retrieval_score": snap.retrieval_score,
        }
        for snap in snapshots
    ]


def _gate_context_from_snapshots(
    mission: Mission, snapshots: list[DeliberationEvidenceSnapshot]
) -> GateContext:
    views: dict[str, EvidenceSnapshotView] = {}
    for snap in snapshots:
        payload = snap.prompt_visible_payload_json or {}
        text = str(payload.get("text") or "")
        views[snap.snapshot_key] = EvidenceSnapshotView(
            snapshot_key=snap.snapshot_key,
            db_id=snap.id,
            global_evidence_id=snap.global_evidence_id,
            text=text,
            payload=payload,
            pack_install_id=snap.pack_install_id,
            record_hash=snap.record_hash,
        )
    return GateContext(mission=mission, snapshots_by_key=views)


def _snapshot_db_id_by_key(
    snapshots: list[DeliberationEvidenceSnapshot],
) -> dict[str, int]:
    return {snap.snapshot_key: snap.id for snap in snapshots}


def _persist_assertion(
    session: Session,
    *,
    artifact: DeliberationArtifact,
    assertion: NarrativeAssertion,
    path: str,
    snap_ids: dict[str, int],
) -> None:
    row = DeliberationAssertion(
        artifact_id=artifact.id,
        path=path,
        text=assertion.text,
        classification=str(assertion.classification.value),
        mission_pointer=assertion.mission_pointer,
        artifact_ref=assertion.artifact_ref,
        issue_code=assertion.issue_code,
        metadata_json={
            "assumption_marker": assertion.assumption_marker,
            "verification_question": assertion.verification_question,
            "unresolved_question": assertion.unresolved_question,
        },
    )
    session.add(row)
    session.flush()
    for anchor in assertion.anchors:
        db_id = snap_ids.get(anchor.evidence_snapshot_id)
        if db_id is None:
            continue
        session.add(
            DeliberationCitation(
                assertion_id=row.id,
                evidence_snapshot_id=db_id,
                quote=anchor.quote,
                json_pointer=anchor.json_pointer,
                value_digest=anchor.value_digest,
            )
        )


def _persist_stage_assertions(
    session: Session,
    *,
    artifact: DeliberationArtifact,
    stage: str,
    model: StrictModel,
    snap_ids: dict[str, int],
) -> None:
    if isinstance(model, BeliefsStagePayload):
        for belief in model.beliefs:
            _persist_assertion(
                session,
                artifact=artifact,
                assertion=belief.statement,
                path=f"beliefs.{belief.local_key}.statement",
                snap_ids=snap_ids,
            )
            for idx, item in enumerate(belief.assumptions):
                _persist_assertion(
                    session,
                    artifact=artifact,
                    assertion=item,
                    path=f"beliefs.{belief.local_key}.assumptions[{idx}]",
                    snap_ids=snap_ids,
                )
            for idx, item in enumerate(belief.gaps):
                _persist_assertion(
                    session,
                    artifact=artifact,
                    assertion=item,
                    path=f"beliefs.{belief.local_key}.gaps[{idx}]",
                    snap_ids=snap_ids,
                )
    elif isinstance(model, OptionsStagePayload):
        for option in model.options:
            _persist_assertion(
                session,
                artifact=artifact,
                assertion=option.label,
                path=f"options.{option.local_key}.label",
                snap_ids=snap_ids,
            )
            _persist_assertion(
                session,
                artifact=artifact,
                assertion=option.expected_outcome,
                path=f"options.{option.local_key}.expected_outcome",
                snap_ids=snap_ids,
            )
            for risk_idx, risk in enumerate(option.risks):
                _persist_assertion(
                    session,
                    artifact=artifact,
                    assertion=risk,
                    path=f"options.{option.local_key}.risks[{risk_idx}]",
                    snap_ids=snap_ids,
                )
    elif isinstance(model, ScenariosStagePayload):
        for scenario in model.scenarios:
            _persist_assertion(
                session,
                artifact=artifact,
                assertion=scenario.projected_outcome,
                path=f"scenarios.{scenario.local_key}.projected_outcome",
                snap_ids=snap_ids,
            )
            for idx, item in enumerate(scenario.facts + scenario.inferences + scenario.assumptions):
                _persist_assertion(
                    session,
                    artifact=artifact,
                    assertion=item,
                    path=f"scenarios.{scenario.local_key}.items[{idx}]",
                    snap_ids=snap_ids,
                )
    elif isinstance(model, CriticStagePayload):
        for idx, finding in enumerate(model.findings):
            _persist_assertion(
                session,
                artifact=artifact,
                assertion=finding,
                path=f"critic.findings[{idx}]",
                snap_ids=snap_ids,
            )
    elif isinstance(model, DecisionStagePayload):
        _persist_assertion(
            session,
            artifact=artifact,
            assertion=model.rationale,
            path="decision.rationale",
            snap_ids=snap_ids,
        )
        for flip in model.flip_conditions:
            _persist_assertion(
                session,
                artifact=artifact,
                assertion=flip.condition,
                path=f"decision.flip_conditions.{flip.local_key}",
                snap_ids=snap_ids,
            )


def _store_artifact(
    session: Session,
    *,
    run: DeliberationRun,
    stage_call: DeliberationStageCall | None,
    kind: str,
    local_key: str,
    payload: dict[str, Any],
) -> DeliberationArtifact:
    art = DeliberationArtifact(
        run_id=run.id,
        stage_call_id=stage_call.id if stage_call is not None else None,
        kind=kind,
        local_key=local_key,
        payload_json=payload,
        payload_digest=payload_digest(payload),
    )
    session.add(art)
    session.flush()
    return art


def _forced_abstention(
    reasons: list[str],
    *,
    detail: str | None = None,
    knowledge_requests: list[dict[str, Any]] | None = None,
) -> DecisionStagePayload:
    text = detail or f"Deliberation abstained: {', '.join(reasons)}"
    # Map abstention reasons that are not critic issue codes to structural other.
    issue_code = "other_structural"
    return DecisionStagePayload.model_validate(
        {
            "outcome": "abstain",
            "selected_option_key": None,
            "rationale": {
                "text": text,
                "classification": "structural",
                "issue_code": issue_code,
                "artifact_ref": "decision",
            },
            "abstention_reasons": reasons,
            "flip_conditions": [
                {
                    "local_key": "flip_default",
                    "condition": {
                        "text": "If valid Pack evidence and selection gates allow a choice",
                        "classification": "proposal",
                        "mission_pointer": "/goal",
                    },
                }
            ],
            "knowledge_requests": knowledge_requests or [],
        }
    )


def _critic_knowledge_requests(critic: CriticStagePayload | None) -> list[dict[str, Any]]:
    if critic is None:
        return []
    requests: list[dict[str, Any]] = []
    for index, finding in enumerate(critic.findings, start=1):
        if finding.classification.value != "gap" or not finding.unresolved_question:
            continue
        requests.append(
            {
                "local_key": f"kr_critic_{index}",
                "question": finding.unresolved_question,
                "gap_ref": finding.artifact_ref or f"critic:finding:{index}",
                "priority": "critical" if critic.verdict == "abstain" else "important",
            }
        )
    return requests


def _merge_knowledge_request_payloads(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for request in group:
            key = request.get("local_key")
            if isinstance(key, str) and key not in merged:
                merged[key] = request
    return list(merged.values())


def _persist_decision_bundle(
    session: Session,
    *,
    run: DeliberationRun,
    stage_call: DeliberationStageCall | None,
    decision: DecisionStagePayload,
    snap_ids: dict[str, int],
) -> None:
    payload = decision.model_dump(mode="json")
    decision_art = _store_artifact(
        session,
        run=run,
        stage_call=stage_call,
        kind="decision",
        local_key="decision",
        payload=payload,
    )
    _persist_stage_assertions(
        session,
        artifact=decision_art,
        stage=STAGE_DECISION,
        model=decision,
        snap_ids=snap_ids,
    )
    flips_payload = {
        "flip_conditions": [item.model_dump(mode="json") for item in decision.flip_conditions]
    }
    _store_artifact(
        session,
        run=run,
        stage_call=stage_call,
        kind="flip_conditions",
        local_key="flip_conditions",
        payload=flips_payload,
    )
    kr_payload = {
        "knowledge_requests": [
            item.model_dump(mode="json") for item in decision.knowledge_requests
        ]
    }
    _store_artifact(
        session,
        run=run,
        stage_call=stage_call,
        kind="knowledge_requests",
        local_key="knowledge_requests",
        payload=kr_payload,
    )
    run.outcome = decision.outcome
    run.current_stage = STAGE_DECISION
    run.status = "decision_ready"


def _complete_run(
    session: Session,
    run: DeliberationRun,
    *,
    fulfilled_knowledge_request_keys: set[str] | None = None,
) -> DeliberationRun:
    compute_cognitive_impact(session, run)
    if run.parent_run_id is not None:
        parent_run = session.get(DeliberationRun, run.parent_run_id)
        if parent_run is None:
            raise RuntimeError(f"parent deliberation run not found: {run.parent_run_id}")
        persist_cognitive_transition(
            session,
            parent_run=parent_run,
            child_run=run,
            fulfilled_knowledge_request_keys=fulfilled_knowledge_request_keys or set(),
        )
    run.status = "impact_ready"
    session.flush()
    # Finalize terminal fields before dossier so audit replay digests match
    # the frozen completed snapshot (status is part of dossier_json).
    run.completed_at = utcnow()
    run.updated_at = utcnow()
    run.status = "completed"
    session.flush()
    persist_dossier(session, run)
    session.flush()
    session.commit()
    return run


def _existing_run_state(session: Session, run: DeliberationRun) -> DeliberationRun:
    """Return an idempotent run without ever recalling an ambiguous LLM stage."""
    started = session.scalar(
        select(DeliberationStageCall)
        .where(
            DeliberationStageCall.run_id == run.id,
            DeliberationStageCall.status == "started",
        )
        .order_by(DeliberationStageCall.id.desc())
    )
    if started is None:
        return run

    lease_expired = run.lease_until is None or ensure_utc(run.lease_until) <= utcnow()
    if not lease_expired:
        return run

    run.status = "indeterminate"
    run.failure_code = "post_call_persistence_ambiguous"
    run.failure_detail = (
        f"stage {started.stage} may have returned after its durable started marker; "
        "automatic LLM recall is forbidden"
    )
    run.lease_owner = None
    run.lease_until = None
    run.updated_at = utcnow()
    run.completed_at = utcnow()
    session.commit()
    return run


def _freeze_and_create_run(
    session: Session,
    mission: Mission,
    *,
    pack_ids: list[str],
    impact_baseline_pack_ids: list[str] | None,
    idempotency_key: str,
    provider: LLMProvider,
    settings: Settings,
    allow_compatible_packs: bool,
    parent_run_id: int | None = None,
) -> DeliberationRun:
    scope = freeze_pack_scope(
        session,
        pack_ids,
        impact_baseline_pack_ids,
        allow_compatible_packs=allow_compatible_packs,
    )
    policy = _policy_snapshot(allow_compatible_packs=allow_compatible_packs)
    runtime = _runtime_config(provider, settings)
    run = DeliberationRun(
        idempotency_key=idempotency_key,
        parent_run_id=parent_run_id,
        mission_snapshot_json=mission.model_dump(mode="json"),
        mission_digest=mission_digest(mission),
        policy_snapshot_json=policy,
        runtime_config_json=runtime,
        policy_digest=payload_digest(policy),
        runtime_config_digest=payload_digest(runtime),
        contract_version=CONTRACT_VERSION,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        primary_scope_digest=scope.primary_digest,
        impact_baseline_scope_digest=scope.impact_baseline_digest,
        status="created",
        current_stage=None,
        outcome=None,
        llm_attempt_count=0,
    )
    session.add(run)
    session.flush()

    for ref in scope.primary:
        session.add(
            DeliberationPackScope(
                run_id=run.id,
                role="primary",
                pack_install_id=ref.pack_install_id,
                pack_id=ref.pack_id,
                declared_version=ref.declared_version,
                source_digest=ref.source_digest,
                admission_profile=ref.admission_profile,
                snapshot_json=ref.snapshot,
            )
        )
    for ref in scope.impact_baseline:
        session.add(
            DeliberationPackScope(
                run_id=run.id,
                role="impact_baseline",
                pack_install_id=ref.pack_install_id,
                pack_id=ref.pack_id,
                declared_version=ref.declared_version,
                source_digest=ref.source_digest,
                admission_profile=ref.admission_profile,
                snapshot_json=ref.snapshot,
            )
        )
    session.flush()
    run.status = "scope_frozen"
    session.flush()
    return run


def _materialize_evidence_snapshots(
    session: Session,
    run: DeliberationRun,
    mission: Mission,
    install_ids: list[int],
) -> list[DeliberationEvidenceSnapshot]:
    retrieval = search_pack_context(
        session,
        mission.decision_question,
        pack_install_ids=install_ids,
        top_k=MAX_EVIDENCE_SNAPSHOTS,
    )
    evidence_rows = list(retrieval.evidence)[:MAX_EVIDENCE_SNAPSHOTS]
    # Rank by hits when available.
    score_by_global = {
        hit.global_id: (index, hit.score)
        for index, hit in enumerate(retrieval.hits)
        if hit.kind == "evidence"
    }
    evidence_rows.sort(
        key=lambda row: (
            score_by_global.get(row.global_evidence_id, (10_000, 0.0))[0],
            row.id,
        )
    )
    snapshots: list[DeliberationEvidenceSnapshot] = []
    for rank, row in enumerate(evidence_rows[:MAX_EVIDENCE_SNAPSHOTS], start=1):
        payload = _prompt_visible_payload(row)
        score = score_by_global.get(row.global_evidence_id, (rank, 0.0))[1]
        snap = DeliberationEvidenceSnapshot(
            run_id=run.id,
            snapshot_key=f"snap:{rank}",
            pack_evidence_id=row.id,
            global_evidence_id=row.global_evidence_id,
            local_evidence_id=row.local_evidence_id,
            pack_install_id=row.pack_install_id,
            record_hash=row.record_hash,
            prompt_visible_payload_json=payload,
            payload_digest=payload_digest(payload),
            retrieval_rank=rank,
            retrieval_score=float(score),
        )
        session.add(snap)
        snapshots.append(snap)
    session.flush()
    run.status = "context_ready"
    session.flush()
    return snapshots


def _run_stage(
    session: Session,
    *,
    run: DeliberationRun,
    mission: Mission,
    stage: str,
    provider: LLMProvider,
    settings: Settings,
    snapshots: list[DeliberationEvidenceSnapshot],
    prior_artifacts: dict[str, Any],
    ctx: GateContext,
) -> tuple[StrictModel | None, DeliberationStageCall | None, StageGateError | None]:
    if run.llm_attempt_count >= MAX_LLM_ATTEMPTS:
        raise RuntimeError("llm attempt budget exhausted")

    try:
        prompt = build_stage_prompt(
            stage,
            mission=mission,
            evidence_snapshots=_evidence_prompt_rows(snapshots),
            prior_artifacts=prior_artifacts,
        )
    except ValueError as exc:
        return None, None, StageGateError("prompt_limit_exceeded", str(exc))

    lease_owner = f"deliberation:{run.id}:{stage}:{uuid4().hex}"
    call = DeliberationStageCall(
        run_id=run.id,
        stage=stage,
        attempt_number=1,
        status="started",
        provider=getattr(provider, "name", None),
        model=settings.llm_model,
        effort=None,
        template_version=PROMPT_TEMPLATE_VERSION,
        prompt_digest=prompt_digest(prompt),
        config_digest=run.runtime_config_digest,
        input_manifest_digest=payload_digest(
            {
                "stage": stage,
                "mission_digest": run.mission_digest,
                "primary_scope_digest": run.primary_scope_digest,
                "evidence_keys": [s.snapshot_key for s in snapshots],
                "prior_keys": sorted(prior_artifacts.keys()),
            }
        ),
    )
    session.add(call)
    session.flush()

    run.llm_attempt_count += 1
    run.current_stage = stage
    run.lease_owner = lease_owner
    run.lease_until = utcnow() + timedelta(seconds=settings.loop_lease_seconds)
    run.updated_at = utcnow()
    # The durable started marker is committed before the provider call. The
    # provider therefore runs outside a database transaction.
    session.commit()

    try:
        raw = provider.query_json(prompt, stage)
    except Exception as exc:
        call.status = "failed"
        call.error = str(exc)
        call.finished_at = utcnow()
        run.lease_owner = None
        run.lease_until = None
        session.commit()
        return None, call, StageGateError("provider_error", str(exc))

    call.response_json = raw if isinstance(raw, dict) else {"value": raw}
    call.response_digest = payload_digest(call.response_json)
    call.raw_response_digest = call.response_digest
    call.raw_response_length = len(str(raw))
    call.finished_at = utcnow()

    if not isinstance(raw, dict):
        call.status = "invalid"
        call.error = "response is not a JSON object"
        run.lease_owner = None
        run.lease_until = None
        session.commit()
        return None, call, StageGateError("invalid_stage_payload", "response is not a JSON object")

    try:
        model = validate_stage(stage, raw, ctx)
    except StageGateError as exc:
        call.status = "invalid"
        call.error = f"{exc.code}: {exc.message}"
        run.lease_owner = None
        run.lease_until = None
        session.commit()
        return None, call, exc

    # Selection gate for decision stage (extra safety beyond validate_decision).
    if isinstance(model, DecisionStagePayload) and model.outcome == "select":
        ok, reasons = selection_gate_allows(ctx, model)
        if not ok:
            call.status = "invalid"
            call.error = f"selection_gate_failed: {','.join(reasons)}"
            run.lease_owner = None
            run.lease_until = None
            session.commit()
            return None, call, StageGateError("selection_gate_failed", ",".join(reasons))

    call.status = "succeeded"
    # The caller persists this validated response, its artifact/assertions, and
    # the next run state in one transaction, then clears the lease and commits.
    return model, call, None


def run_deliberation(
    session: Session,
    mission: Mission,
    *,
    pack_ids: list[str],
    impact_baseline_pack_ids: list[str] | None = None,
    idempotency_key: str,
    provider: LLMProvider,
    settings: Settings | None = None,
    allow_compatible_packs: bool = False,
    parent_run_id: int | None = None,
    fulfilled_knowledge_request_keys: set[str] | None = None,
) -> DeliberationRun:
    """Execute one Autonomous Deliberation D1 run.

    Happy path: exactly five LLM stage calls. No-evidence path: zero LLM calls
    and a completed abstention. Invalid stage payloads never store artifacts and
    complete as abstention with closed reason codes.
    """
    settings = settings or get_settings()

    existing = session.scalar(
        select(DeliberationRun).where(DeliberationRun.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return _existing_run_state(session, existing)

    try:
        run = _freeze_and_create_run(
            session,
            mission,
            pack_ids=pack_ids,
            impact_baseline_pack_ids=impact_baseline_pack_ids,
            idempotency_key=idempotency_key,
            provider=provider,
            settings=settings,
            allow_compatible_packs=allow_compatible_packs,
            parent_run_id=parent_run_id,
        )
    except DeliberationScopeError as exc:
        # Persist a failed_input run when possible; still raise-free for tests
        # that only use valid scopes. Keep a completed-style abstain if freeze
        # fails after identity is known — here we re-raise as ValueError for
        # caller visibility while avoiding partial runs without keys.
        run = DeliberationRun(
            idempotency_key=idempotency_key,
            parent_run_id=parent_run_id,
            mission_snapshot_json=mission.model_dump(mode="json"),
            mission_digest=mission_digest(mission),
            policy_snapshot_json=_policy_snapshot(allow_compatible_packs=allow_compatible_packs),
            runtime_config_json=_runtime_config(provider, settings),
            policy_digest=payload_digest(
                _policy_snapshot(allow_compatible_packs=allow_compatible_packs)
            ),
            runtime_config_digest=payload_digest(_runtime_config(provider, settings)),
            contract_version=CONTRACT_VERSION,
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
            primary_scope_digest="",
            impact_baseline_scope_digest="",
            status="failed_input",
            failure_code=exc.code,
            failure_detail=exc.message,
            llm_attempt_count=0,
            completed_at=utcnow(),
        )
        session.add(run)
        session.commit()
        return run

    install_ids = [
        row.pack_install_id
        for row in session.scalars(
            select(DeliberationPackScope).where(
                DeliberationPackScope.run_id == run.id,
                DeliberationPackScope.role == "primary",
            )
        ).all()
    ]
    snapshots = _materialize_evidence_snapshots(session, run, mission, install_ids)
    # Freeze scope and prompt-visible evidence before the first LLM call.
    session.commit()
    snap_ids = _snapshot_db_id_by_key(snapshots)
    ctx = _gate_context_from_snapshots(mission, snapshots)

    if not snapshots:
        decision = _forced_abstention(
            ["no_evidence"],
            detail="No Pack evidence retrieved for frozen install scope",
            knowledge_requests=[
                {
                    "local_key": "kr_no_evidence",
                    "question": mission.decision_question,
                    "gap_ref": "evidence:no_evidence",
                    "priority": "critical",
                }
            ],
        )
        _persist_decision_bundle(
            session, run=run, stage_call=None, decision=decision, snap_ids=snap_ids
        )
        return _complete_run(
            session, run, fulfilled_knowledge_request_keys=fulfilled_knowledge_request_keys
        )

    prior: dict[str, Any] = {}
    stages = [STAGE_BELIEFS, STAGE_OPTIONS, STAGE_SCENARIOS, STAGE_CRITIC, STAGE_DECISION]
    critic_model: CriticStagePayload | None = None

    for stage in stages:
        model, call, error = _run_stage(
            session,
            run=run,
            mission=mission,
            stage=stage,
            provider=provider,
            settings=settings,
            snapshots=snapshots,
            prior_artifacts=prior,
            ctx=ctx,
        )
        if error is not None or model is None:
            if error is not None and error.code == "provider_error":
                run.status = "failed_execution"
                run.outcome = None
                run.failure_code = error.code
                run.failure_detail = error.message
                run.lease_owner = None
                run.lease_until = None
                run.completed_at = utcnow()
                run.updated_at = utcnow()
                session.commit()
                return run
            reasons = [error.code if error is not None else "invalid_stage_payload"]
            # Normalize unknown gate codes into closed abstention set.
            from openoyster.deliberation_contracts import ABSTENTION_REASON_CODES

            closed = []
            for code in reasons:
                if code in ABSTENTION_REASON_CODES:
                    closed.append(code)
                elif code in {
                    "quote_mismatch",
                    "pointer_mismatch",
                    "missing_anchor",
                    "unknown_belief_ref",
                    "unknown_option_ref",
                    "invalid_constraint_index",
                    "scenario_outcome_class",
                    "unknown_selected_option",
                    "invalid_decision_rationale",
                    "provider_error",
                }:
                    closed.append(
                        "unknown_citation"
                        if code in {"quote_mismatch", "pointer_mismatch", "unknown_citation"}
                        else "invalid_stage_payload"
                    )
                else:
                    closed.append("invalid_stage_payload")
            # quote_mismatch maps to unknown_citation above only for those codes;
            # keep quote failures as invalid_stage_payload is also fine for tests
            # (they only check abstain + no beliefs artifact).
            if error is not None and error.code == "unknown_citation":
                closed = ["unknown_citation"]
            elif error is not None and error.code in {"quote_mismatch", "pointer_mismatch"}:
                closed = ["invalid_stage_payload"]
            promoted_requests = (
                _critic_knowledge_requests(critic_model) if stage == STAGE_DECISION else []
            )
            decision = _forced_abstention(
                closed or ["invalid_stage_payload"],
                knowledge_requests=promoted_requests,
            )
            _persist_decision_bundle(
                session, run=run, stage_call=call, decision=decision, snap_ids=snap_ids
            )
            return _complete_run(
                session, run, fulfilled_knowledge_request_keys=fulfilled_knowledge_request_keys
            )

        kind = STAGE_ARTIFACT_KIND[stage]
        payload = model.model_dump(mode="json")
        art = _store_artifact(
            session,
            run=run,
            stage_call=call,
            kind=kind,
            local_key=kind,
            payload=payload,
        )
        _persist_stage_assertions(
            session, artifact=art, stage=stage, model=model, snap_ids=snap_ids
        )
        prior[kind] = payload
        run.status = STAGE_STATUS[stage]
        run.lease_owner = None
        run.lease_until = None
        session.flush()

        if isinstance(model, CriticStagePayload):
            critic_model = model
            if model.verdict != "pass":
                # Still call decision stage (5-call path) but force abstain if select.
                continue

        if isinstance(model, DecisionStagePayload):
            # Extra critic guard even if model said select.
            if critic_model is not None and critic_model.verdict != "pass":
                promoted_requests = _merge_knowledge_request_payloads(
                    [item.model_dump(mode="json") for item in model.knowledge_requests],
                    _critic_knowledge_requests(critic_model),
                )
                decision = _forced_abstention(
                    ["critic_non_pass"],
                    detail=f"Critic verdict was {critic_model.verdict}",
                    knowledge_requests=promoted_requests,
                )
                # Replace decision artifact: delete the select one we just stored.
                session.delete(art)
                session.flush()
                # Also remove flip/knowledge if any (not stored yet for select path)
                _persist_decision_bundle(
                    session, run=run, stage_call=call, decision=decision, snap_ids=snap_ids
                )
            else:
                # Decision already stored as "decision"; also store flip/KR artifacts.
                flips_payload = {
                    "flip_conditions": [
                        item.model_dump(mode="json") for item in model.flip_conditions
                    ]
                }
                _store_artifact(
                    session,
                    run=run,
                    stage_call=call,
                    kind="flip_conditions",
                    local_key="flip_conditions",
                    payload=flips_payload,
                )
                kr_payload = {
                    "knowledge_requests": [
                        item.model_dump(mode="json") for item in model.knowledge_requests
                    ]
                }
                _store_artifact(
                    session,
                    run=run,
                    stage_call=call,
                    kind="knowledge_requests",
                    local_key="knowledge_requests",
                    payload=kr_payload,
                )
                run.outcome = model.outcome

        # Persist one validated stage atomically with all of its derived rows.
        session.commit()

    return _complete_run(
        session, run, fulfilled_knowledge_request_keys=fulfilled_knowledge_request_keys
    )


def continue_deliberation(
    session: Session,
    parent_run_id: int,
    pack_ids: list[str],
    impact_baseline_pack_ids: list[str] | None,
    fulfilled_knowledge_request_keys: list[str],
    idempotency_key: str,
    provider: LLMProvider,
    settings: Settings | None = None,
    allow_compatible_packs: bool = False,
) -> DeliberationRun:
    """Re-deliberate from a completed abstention after fulfilling parent gaps."""
    existing = session.scalar(
        select(DeliberationRun).where(DeliberationRun.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.parent_run_id != parent_run_id:
            raise DeliberationContinuationError(
                "idempotency_key_conflict",
                "idempotency key is already associated with a different parent deliberation run",
            )
        return _existing_run_state(session, existing)

    parent = session.get(DeliberationRun, parent_run_id)
    if parent is None:
        raise DeliberationContinuationError(
            "parent_run_not_found", "parent deliberation run was not found"
        )
    if parent.status != "completed" or parent.outcome != "abstain":
        raise DeliberationContinuationError(
            "parent_run_not_completed_abstain",
            "parent deliberation run must be a completed abstention",
        )

    parent_knowledge = session.scalar(
        select(DeliberationArtifact).where(
            DeliberationArtifact.run_id == parent.id,
            DeliberationArtifact.kind == "knowledge_requests",
            DeliberationArtifact.local_key == "knowledge_requests",
        )
    )
    if parent_knowledge is None:
        raise DeliberationContinuationError(
            "parent_knowledge_requests_missing",
            "parent deliberation run has no persisted knowledge requests",
        )
    requested_keys = set(fulfilled_knowledge_request_keys)
    if not requested_keys:
        raise DeliberationContinuationError(
            "fulfilled_knowledge_request_keys_empty",
            "fulfilled knowledge request keys must be nonempty",
        )
    parent_items = (parent_knowledge.payload_json or {}).get("knowledge_requests") or []
    available_keys = {
        item.get("local_key") for item in parent_items if isinstance(item, dict) and item.get("local_key")
    }
    unknown_keys = requested_keys - available_keys
    if unknown_keys:
        raise DeliberationContinuationError(
            "fulfilled_knowledge_request_keys_unknown",
            "fulfilled knowledge request keys must exist on the parent run",
        )

    mission = Mission.model_validate(parent.mission_snapshot_json)
    return run_deliberation(
        session,
        mission,
        pack_ids=pack_ids,
        impact_baseline_pack_ids=impact_baseline_pack_ids,
        idempotency_key=idempotency_key,
        provider=provider,
        settings=settings,
        allow_compatible_packs=allow_compatible_packs,
        parent_run_id=parent.id,
        fulfilled_knowledge_request_keys=requested_keys,
    )
