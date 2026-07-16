"""Persisted parent-to-child cognitive transition for Deliberation D1."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.config import get_settings
from openoyster.deliberation_contracts import payload_digest
from openoyster.llm import critic2_provider_from_settings
from openoyster.models import (
    DeliberationArtifact,
    DeliberationAssertion,
    DeliberationCitation,
    DeliberationEvidenceSnapshot,
    DeliberationPackScope,
    DeliberationRun,
)
from openoyster.services.knowledge_request_verifiers import verify_claimed_requests

if TYPE_CHECKING:
    from openoyster.llm import LLMProvider

METHOD = "cognitive_transition_v2"


def _artifact_payloads(session: Session, run_id: int) -> dict[str, dict[str, Any]]:
    rows = session.scalars(
        select(DeliberationArtifact)
        .where(DeliberationArtifact.run_id == run_id)
        .order_by(DeliberationArtifact.id)
    ).all()
    return {row.kind: dict(row.payload_json or {}) for row in rows}


def _keyed_values(
    payload: dict[str, Any] | None, collection: str, value: str
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    items = payload.get(collection)
    if not isinstance(items, list):
        return {}
    return {
        item["local_key"]: item.get(value)
        for item in items
        if isinstance(item, dict) and isinstance(item.get("local_key"), str)
    }


def _changes(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: {"from": parent.get(key), "to": child.get(key)}
        for key in sorted(set(parent) | set(child))
        if parent.get(key) != child.get(key)
    }


def _decision(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    return {
        "outcome": payload.get("outcome"),
        "selected_option_key": payload.get("selected_option_key"),
        "abstention_reasons": list(payload.get("abstention_reasons") or []),
    }


def _used_global_evidence_ids(session: Session, run_id: int) -> list[str]:
    rows = session.scalars(
        select(DeliberationEvidenceSnapshot.global_evidence_id)
        .select_from(DeliberationCitation)
        .join(DeliberationAssertion, DeliberationCitation.assertion_id == DeliberationAssertion.id)
        .join(DeliberationArtifact, DeliberationAssertion.artifact_id == DeliberationArtifact.id)
        .join(
            DeliberationEvidenceSnapshot,
            DeliberationCitation.evidence_snapshot_id == DeliberationEvidenceSnapshot.id,
        )
        .where(DeliberationArtifact.run_id == run_id)
        .order_by(DeliberationEvidenceSnapshot.global_evidence_id)
    ).all()
    return sorted(set(rows))


def _cited_pack_install_ids(session: Session, run_id: int) -> set[int]:
    rows = session.scalars(
        select(DeliberationEvidenceSnapshot.pack_install_id)
        .select_from(DeliberationCitation)
        .join(DeliberationAssertion, DeliberationCitation.assertion_id == DeliberationAssertion.id)
        .join(DeliberationArtifact, DeliberationAssertion.artifact_id == DeliberationArtifact.id)
        .join(
            DeliberationEvidenceSnapshot,
            DeliberationCitation.evidence_snapshot_id == DeliberationEvidenceSnapshot.id,
        )
        .where(DeliberationArtifact.run_id == run_id)
    ).all()
    return set(rows)


def _primary_pack_install_ids(session: Session, run_id: int) -> set[int]:
    rows = session.scalars(
        select(DeliberationPackScope.pack_install_id).where(
            DeliberationPackScope.run_id == run_id,
            DeliberationPackScope.role == "primary",
        )
    ).all()
    return set(rows)


def _knowledge_requests(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("knowledge_requests")
    return [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _merge_knowledge_requests(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """First-wins merge by local_key across ordered groups."""
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for request in group:
            key = request.get("local_key")
            if isinstance(key, str) and key not in merged:
                merged[key] = dict(request)
    return list(merged.values())


def _evidence_text_by_ids(
    session: Session,
    run_id: int,
    evidence_ids: Collection[str],
) -> dict[str, str]:
    """Map global_evidence_id → prompt-visible text for the child run snapshots."""
    ids = [eid for eid in evidence_ids if isinstance(eid, str) and eid]
    if not ids:
        return {}
    rows = session.scalars(
        select(DeliberationEvidenceSnapshot).where(
            DeliberationEvidenceSnapshot.run_id == run_id,
            DeliberationEvidenceSnapshot.global_evidence_id.in_(ids),
        )
    ).all()
    out: dict[str, str] = {}
    for row in rows:
        payload = row.prompt_visible_payload_json or {}
        text = payload.get("text") if isinstance(payload, dict) else None
        if isinstance(text, str) and text.strip():
            # First non-empty text wins if multiple snapshots share an id.
            out.setdefault(row.global_evidence_id, text)
    return out


def _verify_claimed_requests(
    requests: list[dict[str, Any]],
    *,
    claimed_keys: set[str],
    added_evidence_ids: list[str],
    child_cited_evidence_ids: set[str] | None = None,
    provider: LLMProvider | None = None,
    evidence_text_by_id: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Delegate claimed-KR verification to the type-specific registry."""
    return verify_claimed_requests(
        requests,
        claimed_keys=claimed_keys,
        added_evidence_ids=added_evidence_ids,
        child_cited_evidence_ids=child_cited_evidence_ids or set(),
        provider=provider,
        evidence_text_by_id=evidence_text_by_id,
    )


def build_cognitive_transition_payload(
    session: Session,
    *,
    parent_run: DeliberationRun,
    child_run: DeliberationRun,
    fulfilled_knowledge_request_keys: set[str],
) -> dict[str, Any]:
    """Pure (read-only) parent→child transition payload.

    Session is used only for SELECT; callers persist the result separately.
    """
    parent = _artifact_payloads(session, parent_run.id)
    child = _artifact_payloads(session, child_run.id)
    parent_knowledge_requests = _knowledge_requests(parent.get("knowledge_requests"))
    parent_beliefs = _keyed_values(parent.get("beliefs"), "beliefs", "status")
    child_beliefs = _keyed_values(child.get("beliefs"), "beliefs", "status")
    parent_options = _keyed_values(parent.get("options"), "options", "viable")
    child_options = _keyed_values(child.get("options"), "options", "viable")
    parent_critic = parent.get("critic_result") or {}
    child_critic = child.get("critic_result") or {}
    parent_evidence = _used_global_evidence_ids(session, parent_run.id)
    # Child-cited global evidence ids (belief/assertion citations only).
    child_evidence = _used_global_evidence_ids(session, child_run.id)
    added_evidence = sorted(set(child_evidence) - set(parent_evidence))
    # Optional semantic gate: only when critic2 is configured (default off).
    provider = critic2_provider_from_settings(get_settings())
    evidence_text_by_id: dict[str, str] | None = None
    if provider is not None:
        # added_evidence is already child-cited minus parent-cited (intersection).
        evidence_text_by_id = _evidence_text_by_ids(session, child_run.id, added_evidence)
    claimed, verified, unverified, unclaimed = _verify_claimed_requests(
        parent_knowledge_requests,
        claimed_keys=fulfilled_knowledge_request_keys,
        added_evidence_ids=added_evidence,
        child_cited_evidence_ids=set(child_evidence),
        provider=provider,
        evidence_text_by_id=evidence_text_by_id,
    )
    # remaining = unverified claimed + unclaimed parent (as-is) + child requests
    remaining = _merge_knowledge_requests(
        unverified,
        unclaimed,
        _knowledge_requests(child.get("knowledge_requests")),
    )

    parent_cited_installs = _cited_pack_install_ids(session, parent_run.id)
    child_primary_installs = _primary_pack_install_ids(session, child_run.id)
    missing_parent_cited = sorted(parent_cited_installs - child_primary_installs)

    return {
        "method": METHOD,
        "parent_run_id": parent_run.id,
        "child_run_id": child_run.id,
        "claimed_knowledge_requests": claimed,
        "verified_fulfilled_knowledge_requests": verified,
        "unverified_claimed_knowledge_requests": unverified,
        "fulfilled_knowledge_requests": verified,
        "belief_changes": _changes(parent_beliefs, child_beliefs),
        "option_changes": _changes(parent_options, child_options),
        "critic_verdict_change": {
            "from": parent_critic.get("verdict"),
            "to": child_critic.get("verdict"),
        },
        "decision_change": {
            "from": _decision(parent.get("decision")),
            "to": _decision(child.get("decision")),
        },
        "citation_scope_changes": {
            "parent_global_evidence_ids": parent_evidence,
            "child_global_evidence_ids": child_evidence,
            "added_global_evidence_ids": added_evidence,
            "removed_global_evidence_ids": sorted(set(parent_evidence) - set(child_evidence)),
        },
        "parent_cited_pack_install_ids_missing_from_child_scope": missing_parent_cited,
        "parent_citation_scope_dropped": bool(missing_parent_cited),
        "remaining_knowledge_requests": remaining,
    }


def persist_cognitive_transition(
    session: Session,
    *,
    parent_run: DeliberationRun,
    child_run: DeliberationRun,
    fulfilled_knowledge_request_keys: set[str],
) -> DeliberationArtifact:
    """Store the immutable artifact that explains a linked re-deliberation.

    The comparison is artifact- and citation-based. It intentionally does not
    inspect or compare Pack content.
    """
    existing = session.scalar(
        select(DeliberationArtifact).where(
            DeliberationArtifact.run_id == child_run.id,
            DeliberationArtifact.kind == "cognitive_transition",
            DeliberationArtifact.local_key == "cognitive_transition",
        )
    )
    if existing is not None:
        return existing

    payload = build_cognitive_transition_payload(
        session,
        parent_run=parent_run,
        child_run=child_run,
        fulfilled_knowledge_request_keys=fulfilled_knowledge_request_keys,
    )
    artifact = DeliberationArtifact(
        run_id=child_run.id,
        stage_call_id=None,
        kind="cognitive_transition",
        local_key="cognitive_transition",
        payload_json=payload,
        payload_digest=payload_digest(payload),
    )
    session.add(artifact)
    session.flush()
    return artifact
