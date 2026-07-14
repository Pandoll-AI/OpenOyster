"""LLM-free audit replay for Autonomous Deliberation D1."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.deliberation_contracts import (
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
    DeliberationDossier,
    DeliberationEvidenceSnapshot,
    DeliberationReplayResult,
    DeliberationRun,
    DeliberationStageCall,
)
from openoyster.services.deliberation_dossier import recompute_dossier_digests
from openoyster.services.deliberation_gates import (
    EvidenceSnapshotView,
    GateContext,
    StageGateError,
    validate_stage,
)


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


def replay_deliberation(session: Session, run_id: int) -> DeliberationReplayResult:
    """Replay a completed run without calling the LLM.

    Rebuilds dossier digests from frozen artifacts/scopes and compares against
    the stored dossier digests. Also revalidates stage gates.
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

    mismatches: list[str] = []
    if recomputed_json_digest != dossier.json_digest:
        mismatches.append("dossier_json_digest")
    if recomputed_md_digest != dossier.markdown_digest:
        mismatches.append("dossier_markdown_digest")
    if gate_details.get("errors"):
        mismatches.append("stage_gate")
    if not snapshot_integrity["matched"]:
        mismatches.append("evidence_snapshot_digest")

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
        "llm_called": False,
    }
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
