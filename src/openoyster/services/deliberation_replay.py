"""LLM-free audit replay for Autonomous Deliberation D1."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.deliberation_contracts import (
    CONTRACT_VERSION,
    PROMPT_TEMPLATE_VERSION,
    BeliefsStagePayload,
    CriticStagePayload,
    DecisionStagePayload,
    Mission,
    OptionsStagePayload,
    ScenariosStagePayload,
    payload_digest,
)
from openoyster.models import (
    DeliberationArtifact,
    DeliberationCognitiveImpact,
    DeliberationDossier,
    DeliberationEvidenceSnapshot,
    DeliberationReplayResult,
    DeliberationRun,
    DeliberationStageCall,
)
from openoyster.services.cognitive_impact import build_cognitive_impact_payload
from openoyster.services.cognitive_transition import build_cognitive_transition_payload
from openoyster.services.deliberation_dossier import recompute_dossier_digests
from openoyster.services.deliberation_gates import (
    EvidenceSnapshotView,
    GateContext,
    StageGateError,
    validate_stage,
)

IMPACT_METHOD_V2 = "citation_scope_projection_v2"
# v3 freezes semantic verdicts at creation; replay recomputes LLM-free with
# stored semantic_verdicts. Legacy v2 (and other) methods skip recompute.
TRANSITION_METHOD_V3 = "cognitive_transition_v3"


def _gate_context(session: Session, run: DeliberationRun) -> GateContext:
    mission = Mission.model_validate(run.mission_snapshot_json)
    snaps = session.scalars(
        select(DeliberationEvidenceSnapshot)
        .where(DeliberationEvidenceSnapshot.run_id == run.id)
        .order_by(DeliberationEvidenceSnapshot.retrieval_rank, DeliberationEvidenceSnapshot.id)
    ).all()
    views: dict[str, EvidenceSnapshotView] = {}
    for snap in snaps:
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


def _artifact_payload(session: Session, run_id: int, kind: str) -> dict[str, Any] | None:
    row = session.scalar(
        select(DeliberationArtifact).where(
            DeliberationArtifact.run_id == run_id,
            DeliberationArtifact.kind == kind,
        )
    )
    return None if row is None else dict(row.payload_json or {})


def _verify_evidence_snapshot_integrity(
    session: Session, run: DeliberationRun
) -> dict[str, Any]:
    """Recompute prompt-visible snapshot digests from the stored payload bytes."""
    snapshots = session.scalars(
        select(DeliberationEvidenceSnapshot)
        .where(DeliberationEvidenceSnapshot.run_id == run.id)
        .order_by(DeliberationEvidenceSnapshot.retrieval_rank, DeliberationEvidenceSnapshot.id)
    ).all()
    mismatches: list[dict[str, Any]] = []
    for snapshot in snapshots:
        payload = dict(snapshot.prompt_visible_payload_json or {})
        recomputed = payload_digest(payload)
        reasons: list[str] = []
        if recomputed != snapshot.payload_digest:
            reasons.append("payload_digest")
        if payload.get("record_hash") != snapshot.record_hash:
            reasons.append("record_hash")
        if reasons:
            mismatches.append(
                {
                    "snapshot_key": snapshot.snapshot_key,
                    "reasons": reasons,
                    "stored_payload_digest": snapshot.payload_digest,
                    "recomputed_payload_digest": recomputed,
                }
            )
    return {
        "matched": not mismatches,
        "snapshot_count": len(snapshots),
        "mismatches": mismatches,
    }


def _revalidate_stages(session: Session, run: DeliberationRun) -> dict[str, Any]:
    """Re-run deterministic gates over stored stage artifacts/responses."""
    ctx = _gate_context(session, run)
    details: dict[str, Any] = {"stages": {}, "errors": []}

    stage_map = [
        ("deliberation_beliefs", "beliefs", BeliefsStagePayload),
        ("deliberation_options", "options", OptionsStagePayload),
        ("deliberation_scenarios", "scenarios", ScenariosStagePayload),
        ("deliberation_critic", "critic_result", CriticStagePayload),
        ("deliberation_decision", "decision", DecisionStagePayload),
    ]
    for stage_name, kind, _model in stage_map:
        payload = _artifact_payload(session, run.id, kind)
        call = session.scalar(
            select(DeliberationStageCall)
            .where(
                DeliberationStageCall.run_id == run.id,
                DeliberationStageCall.stage == stage_name,
            )
            .order_by(DeliberationStageCall.attempt_number.desc())
        )
        entry: dict[str, Any] = {
            "artifact_present": payload is not None,
            "stage_call_status": call.status if call is not None else None,
            "response_digest": call.response_digest if call is not None else None,
        }
        if payload is None:
            entry["validated"] = False
            details["stages"][stage_name] = entry
            continue
        try:
            validate_stage(stage_name, payload, ctx)
            entry["validated"] = True
            entry["payload_digest"] = payload_digest(payload)
        except StageGateError as exc:
            entry["validated"] = False
            entry["error"] = {"code": exc.code, "message": exc.message}
            details["errors"].append({"stage": stage_name, "code": exc.code, "message": exc.message})
        details["stages"][stage_name] = entry
    return details


def _verify_cognitive_impact_digest(
    session: Session, run: DeliberationRun
) -> dict[str, Any]:
    """Recompute impact payload and compare against the stored digest (read-only).

    Self-digest of the stored payload is checked first (detects in-place
    tampering even when method is legacy and recompute would be skipped).
    Only ``citation_scope_projection_v2`` is recomputed. Legacy/other methods
    skip recompute so version skew does not produce a false mismatch.
    """
    stored = session.scalar(
        select(DeliberationCognitiveImpact).where(DeliberationCognitiveImpact.run_id == run.id)
    )
    if stored is None:
        return {"present": False, "matched": True}

    stored_payload = dict(stored.impact_json or {})
    self_digest = payload_digest(stored_payload)
    if self_digest != stored.impact_digest:
        return {
            "present": True,
            "matched": False,
            "mismatch_reason": "cognitive_impact_stored_digest",
            "stored_digest": stored.impact_digest,
            "payload_self_digest": self_digest,
            "stored_method": stored.method,
        }

    if stored.method != IMPACT_METHOD_V2:
        return {
            "present": True,
            "matched": True,
            "recompute_skipped": "method_version_mismatch",
            "stored_method": stored.method,
        }
    recomputed = build_cognitive_impact_payload(session, run)
    recomputed_digest = payload_digest(recomputed)
    return {
        "present": True,
        "matched": recomputed_digest == stored.impact_digest,
        "stored_digest": stored.impact_digest,
        "recomputed_digest": recomputed_digest,
    }


def _verify_cognitive_transition_digest(
    session: Session, run: DeliberationRun
) -> dict[str, Any]:
    """Recompute transition from parent/child artifacts (read-only).

    Fulfilled knowledge-request keys come from the immutable run column
    ``fulfilled_request_keys_json``, not from the stored transition's claimed
    list (that would be circular self-validation).

    Legacy continuation runs may have empty fulfilled keys (0008 no longer
    trusts transition claimed for backfill). When claimed exists on the stored
    transition but the run column is empty, recompute is skipped as
    unrecoverable rather than producing a false match or a noisy mismatch.
    """
    stored_payload = _artifact_payload(session, run.id, "cognitive_transition")
    if stored_payload is None:
        return {"present": False, "matched": True}

    stored_row = session.scalar(
        select(DeliberationArtifact).where(
            DeliberationArtifact.run_id == run.id,
            DeliberationArtifact.kind == "cognitive_transition",
        )
    )
    stored_digest = stored_row.payload_digest if stored_row is not None else None
    self_digest = payload_digest(stored_payload)
    if stored_digest is not None and self_digest != stored_digest:
        return {
            "present": True,
            "matched": False,
            "mismatch_reason": "cognitive_transition_stored_digest",
            "stored_digest": stored_digest,
            "payload_self_digest": self_digest,
            "stored_method": stored_payload.get("method"),
        }

    stored_method = stored_payload.get("method")
    if stored_method != TRANSITION_METHOD_V3:
        return {
            "present": True,
            "matched": True,
            "recompute_skipped": "method_version_mismatch",
            "stored_method": stored_method,
        }

    fulfilled_keys = {
        key
        for key in (run.fulfilled_request_keys_json or [])
        if isinstance(key, str)
    }
    claimed = stored_payload.get("claimed_knowledge_requests") or []
    has_claimed = isinstance(claimed, list) and len(claimed) > 0
    if (
        not fulfilled_keys
        and run.parent_run_id is not None
        and has_claimed
    ):
        # Legacy continuation: fulfilled keys cannot be trusted-restored from
        # transition claimed (see migration 0008 + D2 verification limits).
        return {
            "present": True,
            "matched": True,
            "recompute_skipped": "legacy_fulfilled_keys_unrecoverable",
            "stored_method": stored_method,
        }

    if run.parent_run_id is None:
        return {"present": True, "matched": False, "error": "missing_parent_run_id"}
    parent = session.get(DeliberationRun, run.parent_run_id)
    if parent is None:
        return {"present": True, "matched": False, "error": "parent_run_not_found"}

    # Replay is LLM-free: re-apply stored frozen semantic_verdicts only.
    stored_frozen = stored_payload.get("semantic_verdicts")
    frozen_semantic = stored_frozen if isinstance(stored_frozen, dict) else {}
    recomputed = build_cognitive_transition_payload(
        session,
        parent_run=parent,
        child_run=run,
        fulfilled_knowledge_request_keys=fulfilled_keys,
        frozen_semantic=frozen_semantic,
    )
    recomputed_digest = payload_digest(recomputed)
    return {
        "present": True,
        "matched": recomputed_digest == stored_digest,
        "stored_digest": stored_digest,
        "recomputed_digest": recomputed_digest,
    }


def replay_deliberation(session: Session, run_id: int) -> DeliberationReplayResult:
    """Replay a completed run without calling the LLM.

    Rebuilds dossier digests from frozen artifacts/scopes and compares against
    the stored dossier digests. Also revalidates stage gates and recomputes
    cognitive impact/transition digests when those rows exist.
    """
    run = session.get(DeliberationRun, run_id)
    if run is None:
        raise ValueError(f"deliberation run not found: {run_id}")

    dossier = session.scalar(
        select(DeliberationDossier).where(DeliberationDossier.run_id == run_id)
    )
    if dossier is None:
        raise ValueError(f"dossier not found for run {run_id}")

    recomputed_json_digest, recomputed_md_digest, _payload = recompute_dossier_digests(
        session, run
    )
    gate_details = _revalidate_stages(session, run)
    snapshot_integrity = _verify_evidence_snapshot_integrity(session, run)
    impact_integrity = _verify_cognitive_impact_digest(session, run)
    transition_integrity = _verify_cognitive_transition_digest(session, run)

    stored_dossier = dict(dossier.dossier_json or {})
    stored_contract = stored_dossier.get("contract_version")
    stored_prompt = stored_dossier.get("prompt_template_version")
    dossier_version_mismatch = (
        stored_contract != CONTRACT_VERSION or stored_prompt != PROMPT_TEMPLATE_VERSION
    )

    mismatches: list[str] = []
    dossier_recompute_skipped: str | None = None
    # Self-digest first: detect payload tampering even when template/version
    # would otherwise skip recompute comparison.
    dossier_self_digest = payload_digest(stored_dossier)
    if dossier_self_digest != dossier.json_digest:
        mismatches.append("dossier_stored_digest")
    elif dossier_version_mismatch:
        # Legacy dossiers written under older template/contract versions may
        # lack newer optional fields; do not treat that as tampering.
        dossier_recompute_skipped = "template_version_mismatch"
    else:
        if recomputed_json_digest != dossier.json_digest:
            mismatches.append("dossier_json_digest")
        if recomputed_md_digest != dossier.markdown_digest:
            mismatches.append("dossier_markdown_digest")
    if gate_details.get("errors"):
        mismatches.append("stage_gate")
    if not snapshot_integrity["matched"]:
        mismatches.append("evidence_snapshot_digest")
    if not impact_integrity["matched"]:
        reason = impact_integrity.get("mismatch_reason") or "cognitive_impact_digest"
        mismatches.append(str(reason))
    if not transition_integrity["matched"]:
        reason = transition_integrity.get("mismatch_reason") or "cognitive_transition_digest"
        mismatches.append(str(reason))

    matched = not mismatches
    result_json: dict[str, Any] = {
        "matched": matched,
        "mismatches": mismatches,
        "stored_dossier_json_digest": dossier.json_digest,
        "recomputed_dossier_json_digest": recomputed_json_digest,
        "stored_dossier_markdown_digest": dossier.markdown_digest,
        "recomputed_dossier_markdown_digest": recomputed_md_digest,
        "gate_revalidation": gate_details,
        "evidence_snapshot_integrity": snapshot_integrity,
        "cognitive_impact_integrity": impact_integrity,
        "cognitive_transition_integrity": transition_integrity,
        "llm_called": False,
    }
    if dossier_recompute_skipped is not None:
        result_json["dossier_recompute_skipped"] = dossier_recompute_skipped
    row = DeliberationReplayResult(
        run_id=run_id,
        matched=matched,
        result_json=result_json,
        stored_dossier_digest=dossier.json_digest,
        recomputed_dossier_digest=recomputed_json_digest,
    )
    session.add(row)
    session.flush()
    return row
