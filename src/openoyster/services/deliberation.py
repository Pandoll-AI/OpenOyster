"""Autonomous Deliberation D1 orchestration (core vertical slice)."""

from __future__ import annotations

import json
import time
from collections.abc import Collection
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
    STAGE_RETRIEVAL_QUERY_EXPANSION,
    STAGE_SCENARIOS,
    CriticStagePayload,
    DecisionStagePayload,
    Mission,
    NarrativeAssertion,
    StrictModel,
    mission_digest,
    parse_retrieval_query_expansion,
    payload_digest,
)
from openoyster.llm import LLMProvider, critic2_provider_from_settings
from openoyster.models import (
    DeliberationArtifact,
    DeliberationAssertion,
    DeliberationCitation,
    DeliberationEvidenceSnapshot,
    DeliberationPackScope,
    DeliberationRun,
    DeliberationStageCall,
    PackEvidence,
    PackInstall,
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
from openoyster.services.deliberation_persistence import (
    RoleAnchor,
    count_stage_units,
    iter_stage_assertions,
)
from openoyster.services.deliberation_prompts import build_stage_prompt, prompt_digest
from openoyster.services.deliberation_scope import DeliberationScopeError, freeze_pack_scope
from openoyster.services.pack_retrieval import (
    install_retrieval_hints,
    search_pack_context,
)
from openoyster.utils import ensure_utc, sha256_text, utcnow

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

# Optional second-pass critic (settings.critic2_provider). Prompt/validation reuse
# STAGE_CRITIC; only the stage_call name and artifact kinds are distinct.
STAGE_CRITIC_SECONDARY: str = "deliberation_critic_secondary"
_CRITIC_VERDICT_RANK: dict[str, int] = {"pass": 0, "revise": 1, "abstain": 2}


class DeliberationContinuationError(ValueError):
    """Stable input error raised when a linked re-deliberation is invalid."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class _IdempotencyRaceResolved(Exception):
    """Internal: concurrent INSERT lost unique race; existing run is authoritative."""

    def __init__(self, run: DeliberationRun) -> None:
        self.run = run
        super().__init__(f"idempotency race resolved to run {run.id}")


def compute_request_fingerprint(
    *,
    mission_digest_value: str,
    pack_ids: list[str],
    impact_baseline_pack_ids: list[str] | None,
    allow_compatible_packs: bool,
    parent_run_id: int | None,
    fulfilled_keys: Collection[str] | None,
) -> str:
    """Canonical digest over the identity-bearing request fields for idempotency."""
    return payload_digest(
        {
            "mission_digest": mission_digest_value,
            "pack_ids": sorted(pack_ids),
            "impact_baseline_pack_ids": sorted(impact_baseline_pack_ids or []),
            "allow_compatible_packs": allow_compatible_packs,
            "parent_run_id": parent_run_id,
            "fulfilled_keys": sorted(fulfilled_keys or []),
        }
    )


def _assert_request_fingerprint(
    session: Session, existing: DeliberationRun, fingerprint: str
) -> None:
    """Compare request fingerprints; lazy-backfill legacy NULL once.

    Legacy rows created before request_fingerprint / fulfilled_request_keys_json
    may still be NULL after migration when inputs were incomplete. The first
    post-upgrade call for such a row accepts the presented fingerprint and
    flushes a conditional atomic fill. Transaction commit is owned by the
    caller (run_deliberation / continue / API / CLI). Subsequent calls fail
    closed on mismatch.

    Limitation (legacy only, one-shot): the first visitor's fingerprint is
    accepted without proving it matches the original request. After that fill,
    comparison is strict. See docs/DECISION_CONTINUITY_D2_REQUIREMENTS.md.
    """
    from sqlalchemy import update

    row = session.get(DeliberationRun, existing.id)
    if row is None:
        return
    stored = row.request_fingerprint
    if stored is None:
        # Atomic compare-and-set: only the first visitor fills NULL.
        # No session.commit() here — avoids committing unrelated dirty rows
        # on the same Session; caller owns the transaction boundary.
        result = session.execute(
            update(DeliberationRun)
            .where(
                DeliberationRun.id == existing.id,
                DeliberationRun.request_fingerprint.is_(None),
            )
            .values(request_fingerprint=fingerprint)
        )
        session.flush()
        filled = int(getattr(result, "rowcount", 0) or 0) == 1
        if filled:
            existing.request_fingerprint = fingerprint
            # Keep the identity-map row in sync without expiring the whole session.
            row.request_fingerprint = fingerprint
            return
        # Lost the race: another visitor filled first — re-read and compare.
        session.refresh(row)
        stored = row.request_fingerprint
        existing.request_fingerprint = stored
        if stored is None:
            # Unexpected: still NULL after lost race; treat as mismatch fail-closed.
            raise DeliberationContinuationError(
                "idempotency_request_mismatch",
                "idempotency key is already associated with a different request fingerprint",
            )
    if stored != fingerprint:
        raise DeliberationContinuationError(
            "idempotency_request_mismatch",
            "idempotency key is already associated with a different request fingerprint",
        )


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
    role_anchors: tuple[RoleAnchor, ...] | None = None,
) -> None:
    """Persist one assertion and every role-tagged citation under it.

    After the stage gate has passed, every anchor.evidence_snapshot_id must
    resolve in snap_ids. A missing key is an internal invariant breach.
    """
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

    if role_anchors is None:
        tagged: tuple[RoleAnchor, ...] = tuple(
            RoleAnchor(anchor=anchor, role="statement") for anchor in assertion.anchors
        )
    else:
        tagged = role_anchors

    for role_anchor in tagged:
        anchor = role_anchor.anchor
        db_id = snap_ids.get(anchor.evidence_snapshot_id)
        if db_id is None:
            raise RuntimeError(
                "unknown evidence snapshot key after gate: "
                f"{anchor.evidence_snapshot_id!r} (path={path!r})"
            )
        session.add(
            DeliberationCitation(
                assertion_id=row.id,
                evidence_snapshot_id=db_id,
                quote=anchor.quote,
                json_pointer=anchor.json_pointer,
                value_digest=anchor.value_digest,
                role=role_anchor.role,
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
    """Persist every visitor-emitted assertion/anchor, then enforce row parity."""
    del stage  # stage is retained for call-site clarity; model type drives the visitor
    expected_assertions, expected_citations = count_stage_units(model)
    for visit in iter_stage_assertions(model):
        _persist_assertion(
            session,
            artifact=artifact,
            assertion=visit.assertion,
            path=visit.path,
            snap_ids=snap_ids,
            role_anchors=visit.anchors,
        )
    session.flush()

    stored_assertions = session.scalars(
        select(DeliberationAssertion).where(DeliberationAssertion.artifact_id == artifact.id)
    ).all()
    stored_assertion_count = len(stored_assertions)
    if stored_assertion_count != expected_assertions:
        raise RuntimeError(
            "assertion persist parity failed: "
            f"visitor={expected_assertions} stored={stored_assertion_count} "
            f"artifact_id={artifact.id}"
        )
    assertion_ids = [row.id for row in stored_assertions]
    if not assertion_ids:
        stored_citation_count = 0
    else:
        stored_citation_count = len(
            session.scalars(
                select(DeliberationCitation).where(
                    DeliberationCitation.assertion_id.in_(assertion_ids)
                )
            ).all()
        )
    if stored_citation_count != expected_citations:
        raise RuntimeError(
            "citation persist parity failed: "
            f"visitor={expected_citations} stored={stored_citation_count} "
            f"artifact_id={artifact.id}"
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


def _safe_provider_error_message(exc: BaseException) -> str:
    """Stable public error string: exception class only, never raw provider text."""
    return f"{type(exc).__name__}: provider_error"


def _provider_error_digest(exc: BaseException) -> str:
    """Opaque digest of the raw exception for offline diagnosis."""
    return sha256_text(str(exc))


def _critic_knowledge_requests(
    critic: CriticStagePayload | None,
    *,
    key_prefix: str = "kr_critic_",
) -> list[dict[str, Any]]:
    if critic is None:
        return []
    requests: list[dict[str, Any]] = []
    for index, finding in enumerate(critic.findings, start=1):
        if finding.classification.value != "gap" or not finding.unresolved_question:
            continue
        requests.append(
            {
                "local_key": f"{key_prefix}{index}",
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
            item.model_dump(mode="json", exclude_none=True)
            for item in decision.knowledge_requests
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
    request_fingerprint: str,
    provider: LLMProvider,
    settings: Settings,
    allow_compatible_packs: bool,
    parent_run_id: int | None = None,
    fulfilled_knowledge_request_keys: Collection[str] | None = None,
) -> DeliberationRun:
    scope = freeze_pack_scope(
        session,
        pack_ids,
        impact_baseline_pack_ids,
        allow_compatible_packs=allow_compatible_packs,
    )
    policy = _policy_snapshot(allow_compatible_packs=allow_compatible_packs)
    runtime = _runtime_config(provider, settings)
    fulfilled_keys = (
        sorted(fulfilled_knowledge_request_keys or [])
        if parent_run_id is not None
        else []
    )
    run = DeliberationRun(
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        parent_run_id=parent_run_id,
        fulfilled_request_keys_json=fulfilled_keys,
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
    try:
        with session.begin_nested():
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
    except IntegrityError as exc:
        existing = session.scalar(
            select(DeliberationRun).where(DeliberationRun.idempotency_key == idempotency_key)
        )
        if existing is None:
            raise
        _assert_request_fingerprint(session, existing, request_fingerprint)
        raise _IdempotencyRaceResolved(existing) from exc
    return run


def _pack_control_metadata_for_expansion(
    session: Session, install_ids: list[int]
) -> list[dict[str, Any]]:
    """Title/hints only — never Pack evidence body (control input → query terms)."""
    if not install_ids:
        return []
    installs = list(
        session.scalars(select(PackInstall).where(PackInstall.id.in_(install_ids))).all()
    )
    by_id = {row.id: row for row in installs}
    rows: list[dict[str, Any]] = []
    for install_id in install_ids:
        install = by_id.get(install_id)
        if install is None:
            continue
        manifest = install.original_manifest_json or {}
        title = manifest.get("title")
        rows.append(
            {
                "pack_id": install.pack_id,
                "title": str(title).strip() if isinstance(title, str) and title.strip() else None,
                "retrieval_hints": install_retrieval_hints(install),
            }
        )
    return rows


def _build_retrieval_expansion_prompt(
    mission: Mission, pack_metadata: list[dict[str, Any]]
) -> str:
    """Control-plane expansion prompt: mission + pack title/hints only."""
    packs_blob = json.dumps(pack_metadata, ensure_ascii=False, sort_keys=True)
    return (
        "You generate alternative lexical search queries for Pack evidence retrieval.\n"
        "Cross-language failure mode: the original question may not share tokens with "
        "English Pack evidence. Produce translations and synonyms only.\n"
        "Return a JSON object: {\"queries\": [\"...\", ...]} with at most 5 strings, "
        "each at most 200 characters. Queries are search terms only — not answers.\n"
        "Do not invent Pack evidence text. Do not quote evidence bodies.\n\n"
        f"decision_question: {mission.decision_question}\n"
        f"goal: {mission.goal}\n"
        f"pack_metadata: {packs_blob}\n"
    )


def _attempt_retrieval_query_expansion(
    session: Session,
    *,
    run: DeliberationRun,
    mission: Mission,
    install_ids: list[int],
    provider: LLMProvider,
    settings: Settings,
) -> tuple[list[str], DeliberationStageCall | None, dict[str, Any]]:
    """One optional expansion LLM call. Never kills the run on schema/provider failure."""
    pack_metadata = _pack_control_metadata_for_expansion(session, install_ids)
    prompt = _build_retrieval_expansion_prompt(mission, pack_metadata)
    trace: dict[str, Any] = {
        "original_query": mission.decision_question,
        "expanded_queries": [],
        "used_query": None,
        "matched_via": None,
        "safety_code": None,
        "pack_metadata": pack_metadata,
    }

    try:
        profile = provider.stage_profile(STAGE_RETRIEVAL_QUERY_EXPANSION)
    except Exception as exc:
        trace["safety_code"] = "expansion_profile_unavailable"
        trace["safety_detail"] = _safe_provider_error_message(exc)
        return [], None, trace

    if run.llm_attempt_count >= MAX_LLM_ATTEMPTS:
        trace["safety_code"] = "expansion_budget_exhausted"
        return [], None, trace

    call = DeliberationStageCall(
        run_id=run.id,
        stage=STAGE_RETRIEVAL_QUERY_EXPANSION,
        attempt_number=1,
        status="started",
        provider=profile.get("provider") or getattr(provider, "name", None),
        model=profile.get("model"),
        effort=profile.get("effort"),
        template_version=PROMPT_TEMPLATE_VERSION,
        prompt_digest=prompt_digest(prompt),
        config_digest=run.runtime_config_digest,
        input_manifest_digest=payload_digest(
            {
                "stage": STAGE_RETRIEVAL_QUERY_EXPANSION,
                "mission_digest": run.mission_digest,
                "primary_scope_digest": run.primary_scope_digest,
                "pack_metadata": pack_metadata,
            }
        ),
    )
    session.add(call)
    session.flush()

    run.llm_attempt_count += 1
    run.current_stage = STAGE_RETRIEVAL_QUERY_EXPANSION
    run.lease_owner = f"deliberation:{run.id}:{STAGE_RETRIEVAL_QUERY_EXPANSION}:{uuid4().hex}"
    run.lease_until = utcnow() + timedelta(seconds=settings.loop_lease_seconds)
    run.updated_at = utcnow()
    session.commit()

    started = time.perf_counter()
    try:
        raw = provider.query_json(prompt, STAGE_RETRIEVAL_QUERY_EXPANSION)
    except Exception as exc:
        call.duration_ms = (time.perf_counter() - started) * 1000.0
        call.status = "failed"
        call.error = _safe_provider_error_message(exc)
        call.finished_at = utcnow()
        run.lease_owner = None
        run.lease_until = None
        session.commit()
        trace["safety_code"] = "expansion_provider_error"
        return [], call, trace

    call.duration_ms = (time.perf_counter() - started) * 1000.0
    call.response_json = raw if isinstance(raw, dict) else {"value": raw}
    call.response_digest = payload_digest(call.response_json)
    call.raw_response_digest = call.response_digest
    call.raw_response_length = len(str(raw))
    call.finished_at = utcnow()

    try:
        parsed = parse_retrieval_query_expansion(raw)
    except Exception:
        call.status = "invalid"
        call.error = "gate_rejected: invalid_expansion_payload"
        run.lease_owner = None
        run.lease_until = None
        session.commit()
        trace["safety_code"] = "invalid_expansion_payload"
        return [], call, trace

    call.status = "succeeded"
    run.lease_owner = None
    run.lease_until = None
    session.flush()
    session.commit()
    queries = list(parsed.queries)
    trace["expanded_queries"] = queries
    return queries, call, trace


def _materialize_evidence_snapshots(
    session: Session,
    run: DeliberationRun,
    mission: Mission,
    install_ids: list[int],
    *,
    provider: LLMProvider,
    settings: Settings,
) -> list[DeliberationEvidenceSnapshot]:
    original_query = mission.decision_question
    retrieval = search_pack_context(
        session,
        original_query,
        pack_install_ids=install_ids,
        top_k=MAX_EVIDENCE_SNAPSHOTS,
    )
    used_query = original_query
    expansion_call: DeliberationStageCall | None = None
    retrieval_trace: dict[str, Any] | None = None

    if not retrieval.evidence and install_ids:
        pack_evidence_count = int(
            session.scalar(
                select(func.count())
                .select_from(PackEvidence)
                .where(PackEvidence.pack_install_id.in_(install_ids))
            )
            or 0
        )
        if pack_evidence_count > 0:
            # 2nd defense: conditional LLM query expansion (one call max).
            expanded_queries, expansion_call, retrieval_trace = (
                _attempt_retrieval_query_expansion(
                    session,
                    run=run,
                    mission=mission,
                    install_ids=install_ids,
                    provider=provider,
                    settings=settings,
                )
            )
            retrieval_trace["primary_diagnostics"] = dict(retrieval.diagnostics)
            for alt_query in expanded_queries:
                alt = search_pack_context(
                    session,
                    alt_query,
                    pack_install_ids=install_ids,
                    top_k=MAX_EVIDENCE_SNAPSHOTS,
                )
                if alt.evidence:
                    retrieval = alt
                    used_query = alt_query
                    retrieval_trace["used_query"] = alt_query
                    retrieval_trace["matched_via"] = "query_expansion"
                    retrieval_trace["match_diagnostics"] = dict(alt.diagnostics)
                    break
            else:
                retrieval_trace["used_query"] = None
                if expanded_queries and retrieval_trace.get("safety_code") is None:
                    retrieval_trace["safety_code"] = "expansion_no_match"

            _store_artifact(
                session,
                run=run,
                stage_call=expansion_call,
                kind="retrieval_trace",
                local_key="retrieval_trace",
                payload=retrieval_trace,
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
    # Keep used_query visible on run degraded_json only when expansion ran.
    if retrieval_trace is not None:
        degraded = dict(run.degraded_json or {})
        degraded["retrieval_used_query"] = used_query
        run.degraded_json = degraded
    session.flush()
    return snapshots


def _conservative_critic_verdict(primary: str, secondary: str) -> str:
    """Both must pass for pass; otherwise the stricter verdict wins."""
    if primary == "pass" and secondary == "pass":
        return "pass"
    primary_rank = _CRITIC_VERDICT_RANK.get(primary, 0)
    secondary_rank = _CRITIC_VERDICT_RANK.get(secondary, 0)
    return primary if primary_rank >= secondary_rank else secondary


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
    recorded_stage: str | None = None,
    provider_stage: str | None = None,
) -> tuple[StrictModel | None, DeliberationStageCall | None, StageGateError | None]:
    # Prompt + gate validation use ``stage`` (contract stage).
    # Provider config/query use ``provider_stage`` (defaults to stage) so secondary
    # critic can reuse the primary critic pipeline profile.
    # Durable stage_call.stage uses ``recorded_stage`` when set (audit name).
    call_stage = recorded_stage or stage
    provider_call_stage = provider_stage or stage
    try:
        base_prompt = build_stage_prompt(
            stage,
            mission=mission,
            evidence_snapshots=_evidence_prompt_rows(snapshots),
            prior_artifacts=prior_artifacts,
        )
    except ValueError as exc:
        return None, None, StageGateError("prompt_limit_exceeded", str(exc))

    # stage_profile is a provider-boundary call (config resolution). Failures must
    # surface as provider_error so the run ends failed_execution, never uncaught.
    try:
        profile = provider.stage_profile(provider_call_stage)
    except Exception as exc:
        safe_error = _safe_provider_error_message(exc)
        return None, None, StageGateError("provider_error", safe_error)

    last_call: DeliberationStageCall | None = None
    last_gate_error: StageGateError | None = None

    # Exactly one retry for non-provider invalid/gate failures (attempt_number=2).
    for attempt_number in (1, 2):
        if run.llm_attempt_count >= MAX_LLM_ATTEMPTS:
            raise RuntimeError("llm attempt budget exhausted")

        prompt = base_prompt
        if attempt_number == 2 and last_gate_error is not None:
            # Public/retry surface: stable gate code only — never reflect free-text
            # messages that may embed model-authored keys/pointers.
            prompt = (
                f"{base_prompt}\n\n"
                f"[PREVIOUS ATTEMPT REJECTED] {last_gate_error.code}"
            )

        lease_owner = f"deliberation:{run.id}:{call_stage}:{uuid4().hex}"
        call = DeliberationStageCall(
            run_id=run.id,
            stage=call_stage,
            attempt_number=attempt_number,
            status="started",
            provider=profile.get("provider") or getattr(provider, "name", None),
            model=profile.get("model"),
            effort=profile.get("effort"),
            template_version=PROMPT_TEMPLATE_VERSION,
            prompt_digest=prompt_digest(prompt),
            config_digest=run.runtime_config_digest,
            input_manifest_digest=payload_digest(
                {
                    "stage": call_stage,
                    "provider_stage": provider_call_stage,
                    "mission_digest": run.mission_digest,
                    "primary_scope_digest": run.primary_scope_digest,
                    "evidence_keys": [s.snapshot_key for s in snapshots],
                    "prior_keys": sorted(prior_artifacts.keys()),
                }
            ),
        )
        session.add(call)
        session.flush()
        last_call = call

        run.llm_attempt_count += 1
        run.current_stage = call_stage
        run.lease_owner = lease_owner
        run.lease_until = utcnow() + timedelta(seconds=settings.loop_lease_seconds)
        run.updated_at = utcnow()
        # The durable started marker is committed before the provider call. The
        # provider therefore runs outside a database transaction.
        session.commit()

        started = time.perf_counter()
        try:
            raw = provider.query_json(prompt, provider_call_stage)
        except Exception as exc:
            safe_error = _safe_provider_error_message(exc)
            call.duration_ms = (time.perf_counter() - started) * 1000.0
            call.status = "failed"
            call.error = safe_error
            usage = dict(call.usage_json or {})
            usage["error_digest"] = _provider_error_digest(exc)
            call.usage_json = usage
            call.finished_at = utcnow()
            run.lease_owner = None
            run.lease_until = None
            session.commit()
            # provider_error: no retry — fail closed as failed_execution.
            return None, call, StageGateError("provider_error", safe_error)

        call.duration_ms = (time.perf_counter() - started) * 1000.0
        call.response_json = raw if isinstance(raw, dict) else {"value": raw}
        call.response_digest = payload_digest(call.response_json)
        call.raw_response_digest = call.response_digest
        call.raw_response_length = len(str(raw))
        call.finished_at = utcnow()

        if not isinstance(raw, dict):
            gate_error = StageGateError(
                "invalid_stage_payload", "response is not a JSON object"
            )
            call.status = "invalid"
            # Persist stable code only; free-text may embed model identifiers.
            call.error = f"gate_rejected: {gate_error.code}"
            usage = dict(call.usage_json or {})
            usage["error_message_digest"] = sha256_text(gate_error.message)
            call.usage_json = usage
            run.lease_owner = None
            run.lease_until = None
            session.commit()
            last_gate_error = gate_error
            if attempt_number == 1:
                continue
            return None, call, gate_error

        try:
            model = validate_stage(stage, raw, ctx)
        except StageGateError as exc:
            call.status = "invalid"
            call.error = f"gate_rejected: {exc.code}"
            usage = dict(call.usage_json or {})
            usage["error_message_digest"] = sha256_text(exc.message)
            call.usage_json = usage
            run.lease_owner = None
            run.lease_until = None
            session.commit()
            last_gate_error = exc
            if attempt_number == 1:
                continue
            return None, call, exc

        # Selection gate for decision stage (extra safety beyond validate_decision).
        if isinstance(model, DecisionStagePayload) and model.outcome == "select":
            ok, reasons = selection_gate_allows(ctx, model)
            if not ok:
                gate_error = StageGateError("selection_gate_failed", ",".join(reasons))
                call.status = "invalid"
                call.error = f"gate_rejected: {gate_error.code}"
                usage = dict(call.usage_json or {})
                usage["error_message_digest"] = sha256_text(gate_error.message)
                call.usage_json = usage
                run.lease_owner = None
                run.lease_until = None
                session.commit()
                last_gate_error = gate_error
                if attempt_number == 1:
                    continue
                return None, call, gate_error

        call.status = "succeeded"
        # The caller persists this validated response, its artifact/assertions, and
        # the next run state in one transaction, then clears the lease and commits.
        return model, call, None

    return None, last_call, last_gate_error


def _maybe_run_secondary_critic(
    session: Session,
    *,
    run: DeliberationRun,
    mission: Mission,
    settings: Settings,
    snapshots: list[DeliberationEvidenceSnapshot],
    prior_artifacts: dict[str, Any],
    ctx: GateContext,
    primary: CriticStagePayload,
    snap_ids: dict[str, int],
) -> tuple[str, CriticStagePayload | None]:
    """Optional second critic; returns (effective verdict, secondary model or None).

    Failures of the secondary provider/payload never kill the run: the primary
    verdict is kept and the gap is recorded on critic_effective. Any unexpected
    exception (including factory/config failures) is also swallowed so the
    primary path can continue.
    """
    primary_verdict = primary.verdict
    saved_ctx_verdict = ctx.critic_verdict
    effective: str = primary_verdict
    secondary_out: CriticStagePayload | None = None

    try:
        # Factory/config resolution is inside the failure boundary so a missing
        # Codex/profile setup cannot abort the primary run.
        critic2 = critic2_provider_from_settings(settings)
        if critic2 is None:
            return primary.verdict, None

        secondary_model, secondary_call, secondary_error = _run_stage(
            session,
            run=run,
            mission=mission,
            stage=STAGE_CRITIC,
            provider=critic2,
            settings=settings,
            snapshots=snapshots,
            prior_artifacts=prior_artifacts,
            ctx=ctx,
            # Provider config/query reuse primary critic stage (codex pipeline has no
            # deliberation_critic_secondary entry); only the durable stage name differs.
            provider_stage=STAGE_CRITIC,
            recorded_stage=STAGE_CRITIC_SECONDARY,
        )

        effective_payload: dict[str, Any]
        if secondary_error is not None or secondary_model is None:
            effective = primary_verdict
            effective_payload = {
                "primary_verdict": primary_verdict,
                "secondary_verdict": None,
                "effective_verdict": effective,
                "combination": "conservative_v1",
            }
            if secondary_error is not None and secondary_error.code == "provider_error":
                effective_payload["secondary_error"] = secondary_error.message
            else:
                # Code-only: gate free-text may embed model-authored identifiers.
                code = (
                    secondary_error.code
                    if secondary_error is not None
                    else "secondary_missing_model"
                )
                effective_payload["secondary_invalid"] = code
                if secondary_error is not None:
                    effective_payload["secondary_invalid_digest"] = sha256_text(
                        secondary_error.message
                    )
        else:
            assert isinstance(secondary_model, CriticStagePayload)
            secondary_payload = secondary_model.model_dump(mode="json")
            secondary_art = _store_artifact(
                session,
                run=run,
                stage_call=secondary_call,
                kind="critic_result_secondary",
                local_key="critic_result_secondary",
                payload=secondary_payload,
            )
            _persist_stage_assertions(
                session,
                artifact=secondary_art,
                stage=STAGE_CRITIC,
                model=secondary_model,
                snap_ids=snap_ids,
            )
            secondary_out = secondary_model
            effective = _conservative_critic_verdict(primary_verdict, secondary_model.verdict)
            effective_payload = {
                "primary_verdict": primary_verdict,
                "secondary_verdict": secondary_model.verdict,
                "effective_verdict": effective,
                "combination": "conservative_v1",
            }

        _store_artifact(
            session,
            run=run,
            stage_call=secondary_call,
            kind="critic_effective",
            local_key="critic_effective",
            payload=effective_payload,
        )
    except Exception as exc:
        effective = primary_verdict
        secondary_out = None
        _store_artifact(
            session,
            run=run,
            stage_call=None,
            kind="critic_effective",
            local_key="critic_effective",
            payload={
                "primary_verdict": primary_verdict,
                "secondary_verdict": None,
                "effective_verdict": effective,
                "combination": "conservative_v1",
                "secondary_error": _safe_provider_error_message(exc),
                "secondary_error_digest": _provider_error_digest(exc),
            },
        )
    finally:
        # Secondary validation may have overwritten ctx.critic_verdict; always restore
        # before applying the effective (combined) verdict below.
        ctx.critic_verdict = saved_ctx_verdict

    ctx.critic_verdict = effective
    return effective, secondary_out


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
    fingerprint = compute_request_fingerprint(
        mission_digest_value=mission_digest(mission),
        pack_ids=pack_ids,
        impact_baseline_pack_ids=impact_baseline_pack_ids,
        allow_compatible_packs=allow_compatible_packs,
        parent_run_id=parent_run_id,
        fulfilled_keys=fulfilled_knowledge_request_keys,
    )

    existing = session.scalar(
        select(DeliberationRun).where(DeliberationRun.idempotency_key == idempotency_key)
    )
    if existing is not None:
        _assert_request_fingerprint(session, existing, fingerprint)
        return _existing_run_state(session, existing)

    try:
        run = _freeze_and_create_run(
            session,
            mission,
            pack_ids=pack_ids,
            impact_baseline_pack_ids=impact_baseline_pack_ids,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            provider=provider,
            settings=settings,
            allow_compatible_packs=allow_compatible_packs,
            parent_run_id=parent_run_id,
            fulfilled_knowledge_request_keys=fulfilled_knowledge_request_keys,
        )
    except _IdempotencyRaceResolved as race:
        return _existing_run_state(session, race.run)
    except DeliberationScopeError as exc:
        # Persist a failed_input run when possible; still raise-free for tests
        # that only use valid scopes. Keep a completed-style abstain if freeze
        # fails after identity is known — here we re-raise as ValueError for
        # caller visibility while avoiding partial runs without keys.
        fulfilled_keys = (
            sorted(fulfilled_knowledge_request_keys or [])
            if parent_run_id is not None
            else []
        )
        run = DeliberationRun(
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            parent_run_id=parent_run_id,
            fulfilled_request_keys_json=fulfilled_keys,
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
        try:
            with session.begin_nested():
                session.add(run)
                session.flush()
        except IntegrityError:
            raced = session.scalar(
                select(DeliberationRun).where(DeliberationRun.idempotency_key == idempotency_key)
            )
            if raced is None:
                raise
            _assert_request_fingerprint(session, raced, fingerprint)
            return _existing_run_state(session, raced)
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
    snapshots = _materialize_evidence_snapshots(
        session,
        run,
        mission,
        install_ids,
        provider=provider,
        settings=settings,
    )
    # Freeze scope and prompt-visible evidence before the first LLM call.
    session.commit()
    snap_ids = _snapshot_db_id_by_key(snapshots)
    ctx = _gate_context_from_snapshots(mission, snapshots)

    if not snapshots:
        pack_evidence_count = int(
            session.scalar(
                select(func.count())
                .select_from(PackEvidence)
                .where(PackEvidence.pack_install_id.in_(install_ids))
            )
            or 0
        )
        retrieval_status = (
            "pack_has_no_evidence"
            if pack_evidence_count == 0
            else "no_match_in_pack_evidence"
        )
        decision = _forced_abstention(
            ["no_evidence"],
            detail=(
                "No Pack evidence retrieved for frozen install scope "
                f"(retrieval_status={retrieval_status})"
            ),
            knowledge_requests=[
                {
                    "local_key": "kr_no_evidence",
                    "question": mission.decision_question,
                    "gap_ref": "evidence:no_evidence",
                    "priority": "critical",
                    "retrieval_status": retrieval_status,
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
    critic_secondary_model: CriticStagePayload | None = None
    critic_effective_verdict: str | None = None

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
                _merge_knowledge_request_payloads(
                    _critic_knowledge_requests(critic_model, key_prefix="kr_critic_"),
                    _critic_knowledge_requests(
                        critic_secondary_model, key_prefix="kr_critic2_"
                    ),
                )
                if stage == STAGE_DECISION
                else []
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
            # Primary critic_result is immutable from here; optional secondary
            # critic combines conservatively into the effective verdict.
            critic_effective_verdict, critic_secondary_model = _maybe_run_secondary_critic(
                session,
                run=run,
                mission=mission,
                settings=settings,
                snapshots=snapshots,
                prior_artifacts=prior,
                ctx=ctx,
                primary=model,
                snap_ids=snap_ids,
            )
            if critic_effective_verdict != "pass":
                # Still call decision stage (5-call path) but force abstain if select.
                continue

        if isinstance(model, DecisionStagePayload):
            # Extra critic guard even if model said select (uses effective verdict).
            effective = critic_effective_verdict
            if critic_model is not None and effective != "pass":
                promoted_requests = _merge_knowledge_request_payloads(
                    [
                        item.model_dump(mode="json", exclude_none=True)
                        for item in model.knowledge_requests
                    ],
                    _critic_knowledge_requests(critic_model, key_prefix="kr_critic_"),
                    _critic_knowledge_requests(
                        critic_secondary_model, key_prefix="kr_critic2_"
                    ),
                )
                decision = _forced_abstention(
                    ["critic_non_pass"],
                    detail=f"Critic verdict was {effective}",
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
                        item.model_dump(mode="json", exclude_none=True)
                        for item in model.knowledge_requests
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
    if existing is not None and existing.parent_run_id != parent_run_id:
        raise DeliberationContinuationError(
            "idempotency_key_conflict",
            "idempotency key is already associated with a different parent deliberation run",
        )

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

    try:
        mission = Mission.model_validate(parent.mission_snapshot_json)
    except Exception as exc:
        raise DeliberationContinuationError(
            "parent_integrity_mismatch",
            "parent mission snapshot cannot be reconstructed",
        ) from exc
    if mission_digest(mission) != parent.mission_digest:
        raise DeliberationContinuationError(
            "parent_integrity_mismatch",
            "parent mission snapshot digest does not match stored mission_digest",
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

    # D2 §2: require at least one primary install not present on the parent.
    try:
        child_scope = freeze_pack_scope(
            session,
            pack_ids,
            impact_baseline_pack_ids,
            allow_compatible_packs=allow_compatible_packs,
        )
    except DeliberationScopeError:
        child_scope = None
    if child_scope is not None:
        parent_primary_ids = {
            row.pack_install_id
            for row in session.scalars(
                select(DeliberationPackScope).where(
                    DeliberationPackScope.run_id == parent.id,
                    DeliberationPackScope.role == "primary",
                )
            ).all()
        }
        child_primary_ids = set(child_scope.primary_install_ids)
        if not (child_primary_ids - parent_primary_ids):
            raise DeliberationContinuationError(
                "no_new_pack_scope",
                "continuation requires at least one primary pack install not on the parent",
            )

    fingerprint = compute_request_fingerprint(
        mission_digest_value=mission_digest(mission),
        pack_ids=pack_ids,
        impact_baseline_pack_ids=impact_baseline_pack_ids,
        allow_compatible_packs=allow_compatible_packs,
        parent_run_id=parent.id,
        fulfilled_keys=requested_keys,
    )
    if existing is not None:
        _assert_request_fingerprint(session, existing, fingerprint)
        return _existing_run_state(session, existing)

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
