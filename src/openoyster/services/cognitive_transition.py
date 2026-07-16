"""Persisted parent-to-child cognitive transition for Deliberation D1.

Semantic relevance (critic2) is judged only at *creation* time. Verdicts are
frozen into ``semantic_verdicts`` on the payload. ``build_cognitive_transition_payload``
is LLM-free and deterministic given ``frozen_semantic`` — replay must never
construct or call a provider.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.config import Settings, get_settings
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
from openoyster.services.knowledge_request_verifiers import (
    SemanticRelevanceVerifier,
    verify_claimed_requests,
)

if TYPE_CHECKING:
    from openoyster.llm import LLMProvider

# v3: semantic verdicts frozen at creation; replay is LLM-free with frozen input.
METHOD = "cognitive_transition_v3"


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


def _safe_provider_provenance(provider: LLMProvider, settings: Settings) -> dict[str, str]:
    """Safe provider/model labels only — never raw prompt or evidence text."""
    prov: dict[str, str] = {
        "provider": str(getattr(provider, "name", "unknown") or "unknown"),
        "critic2_provider": str(settings.critic2_provider),
    }
    if settings.critic2_provider == "claude-cli" and settings.claude_model:
        prov["model"] = str(settings.claude_model)
    elif settings.critic2_provider in ("codex", "stub"):
        prov["model"] = str(settings.llm_model)
    return prov


def _fail_closed_semantic_entries(
    requests: list[dict[str, Any]],
    *,
    claimed_keys: set[str],
    reason: str,
) -> dict[str, dict[str, Any]]:
    """Mark matching claimed KRs as related=false so structural cannot promote."""
    verifier = SemanticRelevanceVerifier()
    frozen: dict[str, dict[str, Any]] = {}
    for request in requests:
        key = request.get("local_key")
        if not isinstance(key, str) or key not in claimed_keys:
            continue
        if not verifier.matches(request):
            continue
        frozen[key] = {
            "related": False,
            "verification_evidence_ids": [],
            "method": verifier.method_id,
            "safe_provider_provenance": {"provider": "unavailable", "reason": reason},
            "input_digest": payload_digest({"error": reason, "local_key": key}),
        }
    return frozen


def _compute_frozen_semantic(
    session: Session,
    *,
    parent_knowledge_requests: list[dict[str, Any]],
    claimed_keys: set[str],
    added_evidence_ids: list[str],
    child_cited_evidence_ids: set[str],
    child_run_id: int,
    provider: LLMProvider,
    settings: Settings,
) -> dict[str, dict[str, Any]]:
    """Run semantic gate once per matching claimed KR; return freeze map."""
    evidence_text = _evidence_text_by_ids(session, child_run_id, added_evidence_ids)
    verifier = SemanticRelevanceVerifier()
    provenance = _safe_provider_provenance(provider, settings)
    frozen: dict[str, dict[str, Any]] = {}
    for request in parent_knowledge_requests:
        key = request.get("local_key")
        if not isinstance(key, str) or key not in claimed_keys:
            continue
        if not verifier.matches(request):
            continue
        result = verifier.verify(
            request,
            added_evidence_ids,
            child_cited_evidence_ids=child_cited_evidence_ids,
            provider=provider,
            evidence_text_by_id=evidence_text,
        )
        evidence_ids = list(result.get("verification_evidence_ids") or [])
        question = str(request.get("question") or "")[:2000]
        input_payload = {
            "local_key": key,
            "question": question,
            "evidence_ids": evidence_ids,
            "evidence_text_digests": {
                eid: payload_digest({"text": evidence_text.get(eid, "")})
                for eid in evidence_ids
            },
        }
        frozen[key] = {
            "related": result.get("status") == "verified_fulfilled",
            "verification_evidence_ids": evidence_ids,
            "method": result.get("verification_method") or verifier.method_id,
            "safe_provider_provenance": dict(provenance),
            "input_digest": payload_digest(input_payload),
        }
    return frozen


def _verify_claimed_requests(
    requests: list[dict[str, Any]],
    *,
    claimed_keys: set[str],
    added_evidence_ids: list[str],
    child_cited_evidence_ids: set[str] | None = None,
    frozen_semantic: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Delegate claimed-KR verification — LLM-free when using frozen_semantic."""
    return verify_claimed_requests(
        requests,
        claimed_keys=claimed_keys,
        added_evidence_ids=added_evidence_ids,
        child_cited_evidence_ids=child_cited_evidence_ids or set(),
        provider=None,
        evidence_text_by_id=None,
        frozen_semantic=frozen_semantic,
    )


def build_cognitive_transition_payload(
    session: Session,
    *,
    parent_run: DeliberationRun,
    child_run: DeliberationRun,
    fulfilled_knowledge_request_keys: set[str],
    frozen_semantic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure (read-only, LLM-free) parent→child transition payload.

    Session is used only for SELECT; callers persist the result separately.
    Never constructs or calls an LLM provider. When ``frozen_semantic`` is a
    dict, those verdicts drive semantic KR outcomes; when None/empty, only the
    deterministic structural verifiers (added_cited fallback) apply.
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

    # frozen_semantic: use exactly as provided for digest stability on replay.
    # None → treat as empty (pure structural); never invent a provider.
    frozen: dict[str, Any] = (
        dict(frozen_semantic) if isinstance(frozen_semantic, dict) else {}
    )

    claimed, verified, unverified, unclaimed = _verify_claimed_requests(
        parent_knowledge_requests,
        claimed_keys=fulfilled_knowledge_request_keys,
        added_evidence_ids=added_evidence,
        child_cited_evidence_ids=set(child_evidence),
        frozen_semantic=frozen if frozen else None,
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
        # Frozen semantic section (empty when critic2 was off at creation).
        "semantic_verdicts": frozen,
    }


def persist_cognitive_transition(
    session: Session,
    *,
    parent_run: DeliberationRun,
    child_run: DeliberationRun,
    fulfilled_knowledge_request_keys: set[str],
    settings: Settings | None = None,
) -> DeliberationArtifact:
    """Store the immutable artifact that explains a linked re-deliberation.

    The comparison is artifact- and citation-based. It intentionally does not
    inspect or compare Pack content.

    Semantic gate (critic2) runs here only, once. Verdicts are frozen into the
    payload. Factory / provider failures fail-closed (no structural promotion
    for matching KRs when the gate was configured).
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

    runtime_settings = settings if settings is not None else get_settings()
    frozen_semantic: dict[str, Any] = {}

    # Gate active when critic2 is configured; separate from provider object.
    if runtime_settings.critic2_provider != "none":
        try:
            provider = critic2_provider_from_settings(runtime_settings)
            if provider is None:
                # Configured but factory returned None — fail-closed.
                parent_payloads = _artifact_payloads(session, parent_run.id)
                frozen_semantic = _fail_closed_semantic_entries(
                    _knowledge_requests(parent_payloads.get("knowledge_requests")),
                    claimed_keys=fulfilled_knowledge_request_keys,
                    reason="provider_unavailable",
                )
            else:
                parent_payloads = _artifact_payloads(session, parent_run.id)
                parent_krs = _knowledge_requests(parent_payloads.get("knowledge_requests"))
                parent_evidence = _used_global_evidence_ids(session, parent_run.id)
                child_evidence = _used_global_evidence_ids(session, child_run.id)
                added_evidence = sorted(set(child_evidence) - set(parent_evidence))
                frozen_semantic = _compute_frozen_semantic(
                    session,
                    parent_knowledge_requests=parent_krs,
                    claimed_keys=fulfilled_knowledge_request_keys,
                    added_evidence_ids=added_evidence,
                    child_cited_evidence_ids=set(child_evidence),
                    child_run_id=child_run.id,
                    provider=provider,
                    settings=runtime_settings,
                )
        except Exception:
            # Factory or semantic execution failure must not break transition;
            # fail-closed so structural cannot promote matching claims.
            parent_payloads = _artifact_payloads(session, parent_run.id)
            frozen_semantic = _fail_closed_semantic_entries(
                _knowledge_requests(parent_payloads.get("knowledge_requests")),
                claimed_keys=fulfilled_knowledge_request_keys,
                reason="semantic_gate_error",
            )

    payload = build_cognitive_transition_payload(
        session,
        parent_run=parent_run,
        child_run=child_run,
        fulfilled_knowledge_request_keys=fulfilled_knowledge_request_keys,
        frozen_semantic=frozen_semantic,
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
