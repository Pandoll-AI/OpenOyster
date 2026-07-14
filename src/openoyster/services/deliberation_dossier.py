"""Decision Dossier builder for Autonomous Deliberation D1."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.deliberation_contracts import CONTRACT_VERSION, PROMPT_TEMPLATE_VERSION, payload_digest
from openoyster.models import (
    DeliberationArtifact,
    DeliberationCognitiveImpact,
    DeliberationDossier,
    DeliberationEvidenceSnapshot,
    DeliberationPackScope,
    DeliberationRun,
    DeliberationStageCall,
)
from openoyster.utils import sha256_text


def _artifacts_by_kind(session: Session, run_id: int) -> dict[str, dict[str, Any]]:
    rows = session.scalars(
        select(DeliberationArtifact)
        .where(DeliberationArtifact.run_id == run_id)
        .order_by(DeliberationArtifact.id)
    ).all()
    return {row.kind: row.payload_json for row in rows}


def _pack_scopes(session: Session, run_id: int) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(DeliberationPackScope)
        .where(DeliberationPackScope.run_id == run_id)
        .order_by(DeliberationPackScope.role, DeliberationPackScope.pack_install_id)
    ).all()
    return [
        {
            "role": row.role,
            "pack_install_id": row.pack_install_id,
            "pack_id": row.pack_id,
            "declared_version": row.declared_version,
            "source_digest": row.source_digest,
            "admission_profile": row.admission_profile,
            "snapshot": row.snapshot_json,
        }
        for row in rows
    ]


def _citations_summary(session: Session, run_id: int) -> list[dict[str, Any]]:
    snaps = session.scalars(
        select(DeliberationEvidenceSnapshot)
        .where(DeliberationEvidenceSnapshot.run_id == run_id)
        .order_by(DeliberationEvidenceSnapshot.retrieval_rank, DeliberationEvidenceSnapshot.id)
    ).all()
    return [
        {
            "snapshot_key": snap.snapshot_key,
            "global_evidence_id": snap.global_evidence_id,
            "pack_install_id": snap.pack_install_id,
            "record_hash": snap.record_hash,
            "payload_digest": snap.payload_digest,
            "retrieval_rank": snap.retrieval_rank,
            "retrieval_score": snap.retrieval_score,
        }
        for snap in snaps
    ]


def _stage_call_summaries(session: Session, run_id: int) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(DeliberationStageCall)
        .where(DeliberationStageCall.run_id == run_id)
        .order_by(DeliberationStageCall.id)
    ).all()
    return [
        {
            "stage": row.stage,
            "attempt_number": row.attempt_number,
            "status": row.status,
            "provider": row.provider,
            "model": row.model,
            "effort": row.effort,
            "template_version": row.template_version,
            "prompt_digest": row.prompt_digest,
            "response_digest": row.response_digest,
            "error": row.error,
        }
        for row in rows
    ]


def build_dossier_payload(session: Session, run: DeliberationRun) -> dict[str, Any]:
    """Assemble canonical dossier JSON from persisted run state (LLM-free)."""
    artifacts = _artifacts_by_kind(session, run.id)
    impact = session.scalar(
        select(DeliberationCognitiveImpact).where(DeliberationCognitiveImpact.run_id == run.id)
    )
    return {
        "contract_version": run.contract_version or CONTRACT_VERSION,
        "prompt_template_version": run.prompt_template_version or PROMPT_TEMPLATE_VERSION,
        "run_id": run.id,
        "parent_run_id": run.parent_run_id,
        "idempotency_key": run.idempotency_key,
        "status": run.status,
        "outcome": run.outcome,
        "mission": run.mission_snapshot_json,
        "mission_digest": run.mission_digest,
        "policy_digest": run.policy_digest,
        "runtime_config_digest": run.runtime_config_digest,
        "primary_scope_digest": run.primary_scope_digest,
        "impact_baseline_scope_digest": run.impact_baseline_scope_digest,
        "pack_scopes": _pack_scopes(session, run.id),
        "beliefs": artifacts.get("beliefs"),
        "options": artifacts.get("options"),
        "scenarios": artifacts.get("scenarios"),
        "critic_result": artifacts.get("critic_result"),
        "decision": artifacts.get("decision"),
        "flip_conditions": artifacts.get("flip_conditions"),
        "knowledge_requests": artifacts.get("knowledge_requests"),
        "cognitive_transition": artifacts.get("cognitive_transition"),
        "cognitive_impact": impact.impact_json if impact is not None else None,
        "evidence_snapshots": _citations_summary(session, run.id),
        "stage_calls": _stage_call_summaries(session, run.id),
        "llm_attempt_count": run.llm_attempt_count,
        "failure_code": run.failure_code,
        "failure_detail": run.failure_detail,
    }


def render_dossier_markdown(payload: dict[str, Any]) -> str:
    """Render a human-readable Markdown dossier (no secrets/prompts/paths)."""
    lines = [
        f"# Decision Dossier — run {payload.get('run_id')}",
        "",
        f"- Outcome: `{payload.get('outcome')}`",
        f"- Status: `{payload.get('status')}`",
        f"- Contract: `{payload.get('contract_version')}`",
        f"- Prompt template: `{payload.get('prompt_template_version')}`",
        f"- Mission digest: `{payload.get('mission_digest')}`",
        f"- Parent run: `{payload.get('parent_run_id')}`" if payload.get("parent_run_id") is not None else "- Parent run: (none)",
        "",
        "## Mission",
    ]
    mission = payload.get("mission") or {}
    if isinstance(mission, dict):
        lines.append(f"- Goal: {mission.get('goal', '')}")
        lines.append(f"- Decision question: {mission.get('decision_question', '')}")
        constraints = mission.get("constraints") or []
        if constraints:
            lines.append("- Constraints:")
            for item in constraints:
                lines.append(f"  - {item}")
    lines.extend(["", "## Pack scopes"])
    for scope in payload.get("pack_scopes") or []:
        lines.append(
            f"- `{scope.get('role')}` install={scope.get('pack_install_id')} "
            f"pack={scope.get('pack_id')} v={scope.get('declared_version')} "
            f"digest={scope.get('source_digest')}"
        )
    lines.extend(["", "## Decision"])
    decision = payload.get("decision") or {}
    if isinstance(decision, dict):
        lines.append(f"- Outcome: `{decision.get('outcome')}`")
        if decision.get("selected_option_key"):
            lines.append(f"- Selected option: `{decision.get('selected_option_key')}`")
        reasons = decision.get("abstention_reasons") or []
        if reasons:
            lines.append(f"- Abstention reasons: {', '.join(reasons)}")
        rationale = decision.get("rationale") or {}
        if isinstance(rationale, dict) and rationale.get("text"):
            lines.append(f"- Rationale: {rationale['text']}")
    lines.extend(["", "## Flip conditions"])
    flips = payload.get("flip_conditions") or {}
    flip_items = flips.get("flip_conditions") if isinstance(flips, dict) else flips
    if isinstance(flip_items, list):
        for item in flip_items:
            if isinstance(item, dict):
                cond = item.get("condition") or {}
                text = cond.get("text") if isinstance(cond, dict) else str(item)
                lines.append(f"- {item.get('local_key', 'flip')}: {text}")
    if not flip_items:
        lines.append("- (none)")
    lines.extend(["", "## Knowledge requests"])
    krs = payload.get("knowledge_requests") or {}
    kr_items = krs.get("knowledge_requests") if isinstance(krs, dict) else krs
    if isinstance(kr_items, list) and kr_items:
        for item in kr_items:
            if isinstance(item, dict):
                lines.append(
                    f"- [{item.get('priority', 'critical')}] {item.get('question', '')} "
                    f"(gap={item.get('gap_ref', '')})"
                )
    else:
        lines.append("- (none)")
    transition = payload.get("cognitive_transition")
    if isinstance(transition, dict):
        lines.extend(["", "## Cognitive transition"])
        lines.append(f"- Method: `{transition.get('method')}`")
        lines.append(f"- Parent run: `{transition.get('parent_run_id')}`")
        claimed = transition.get("claimed_knowledge_requests") or []
        verified = transition.get("verified_fulfilled_knowledge_requests") or []
        unverified = transition.get("unverified_claimed_knowledge_requests") or []
        lines.append(f"- Claimed knowledge requests: {len(claimed)}")
        lines.append(f"- Verified fulfilled knowledge requests: {len(verified)}")
        lines.append(f"- Unverified claimed knowledge requests: {len(unverified)}")
    lines.extend(["", "## Cognitive Impact"])
    impact = payload.get("cognitive_impact") or {}
    if isinstance(impact, dict):
        lines.append(f"- Method: `{impact.get('method')}`")
        lines.append(f"- Decision support: `{impact.get('decision_support')}`")
        grounded = impact.get("grounded_assertions") or []
        lines.append(f"- Grounded assertions projected: {len(grounded)}")
    lines.extend(["", "## Evidence snapshots"])
    snaps = payload.get("evidence_snapshots") or []
    if snaps:
        for snap in snaps:
            lines.append(
                f"- `{snap.get('snapshot_key')}` global={snap.get('global_evidence_id')} "
                f"rank={snap.get('retrieval_rank')}"
            )
    else:
        lines.append("- (none)")
    lines.append("")
    return "\n".join(lines)


def persist_dossier(session: Session, run: DeliberationRun) -> DeliberationDossier:
    """Build and persist dossier JSON + Markdown for a completed run."""
    existing = session.scalar(
        select(DeliberationDossier).where(DeliberationDossier.run_id == run.id)
    )
    if existing is not None:
        return existing

    payload = build_dossier_payload(session, run)
    markdown = render_dossier_markdown(payload)
    row = DeliberationDossier(
        run_id=run.id,
        dossier_json=payload,
        dossier_markdown=markdown,
        json_digest=payload_digest(payload),
        markdown_digest=sha256_text(markdown),
    )
    session.add(row)
    session.flush()
    return row


def recompute_dossier_digests(session: Session, run: DeliberationRun) -> tuple[str, str, dict[str, Any]]:
    """Rebuild dossier from artifacts and return (json_digest, markdown_digest, payload)."""
    payload = build_dossier_payload(session, run)
    markdown = render_dossier_markdown(payload)
    return payload_digest(payload), sha256_text(markdown), payload
