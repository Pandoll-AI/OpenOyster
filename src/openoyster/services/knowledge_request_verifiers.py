"""Type-specific Knowledge Request verifiers and machine-readable export.

Claimed fulfillments are checked by a registry of verifiers. Specialized
verifiers match by request shape (currently gap_ref); unmatched claims fall
through to ``none_available_v1`` so the transition record is honest about
what was actually checked.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

EXPORT_SCHEMA = "openoyster.knowledge_request_export/v1"


class KnowledgeRequestVerifier:
    """Base type for KR verifiers. Subclasses set ``name`` / ``version``."""

    name: str = ""
    version: str = ""

    @property
    def method_id(self) -> str:
        """Stable id recorded on transition items (``{name}_{version}``)."""
        return f"{self.name}_{self.version}"

    def matches(self, request: dict[str, Any]) -> bool:
        raise NotImplementedError

    def verify(
        self,
        request: dict[str, Any],
        added_evidence_ids: list[str],
        *,
        child_cited_evidence_ids: Collection[str] = (),
    ) -> dict[str, Any]:
        """Return status / verification_method / verification_evidence_ids."""
        raise NotImplementedError


class AddedCitedEvidenceV1(KnowledgeRequestVerifier):
    """Verify ``evidence:no_evidence`` gaps via newly cited global evidence.

    Requires the intersection of ``added_evidence_ids`` and
    ``child_cited_evidence_ids`` to be non-empty. This binds verification to
    evidence the child run actually cited in beliefs/assertions — not merely
    pack-scoped or globally present IDs.

    Limitation: question↔evidence *semantic* relevance is still not checked;
    any child-cited new evidence can verify any ``evidence:no_evidence`` claim.
    See docs/DECISION_CONTINUITY_D2_REQUIREMENTS.md §검증 한계.
    """

    name = "added_cited_evidence"
    version = "v1"

    def matches(self, request: dict[str, Any]) -> bool:
        return request.get("gap_ref") == "evidence:no_evidence"

    def verify(
        self,
        request: dict[str, Any],
        added_evidence_ids: list[str],
        *,
        child_cited_evidence_ids: Collection[str] = (),
    ) -> dict[str, Any]:
        del request
        cited = set(child_cited_evidence_ids)
        bound = sorted({eid for eid in added_evidence_ids if eid in cited})
        if bound:
            return {
                "status": "verified_fulfilled",
                "verification_method": self.method_id,
                "verification_evidence_ids": bound,
            }
        return {
            "status": "claimed_unverified",
            "verification_method": self.method_id,
            "verification_evidence_ids": [],
        }


class NoneAvailableV1(KnowledgeRequestVerifier):
    """Honest fallback when no specialized verifier matches the claim."""

    name = "none_available"
    version = "v1"

    def matches(self, request: dict[str, Any]) -> bool:
        del request
        return True

    def verify(
        self,
        request: dict[str, Any],
        added_evidence_ids: list[str],
        *,
        child_cited_evidence_ids: Collection[str] = (),
    ) -> dict[str, Any]:
        del request, added_evidence_ids, child_cited_evidence_ids
        return {
            "status": "claimed_unverified",
            "verification_method": self.method_id,
            "verification_evidence_ids": [],
        }


# Specialized first; fallback last. Order is the registry contract.
BUILTIN_VERIFIERS: tuple[KnowledgeRequestVerifier, ...] = (
    AddedCitedEvidenceV1(),
    NoneAvailableV1(),
)


def select_verifier(
    request: dict[str, Any],
    registry: tuple[KnowledgeRequestVerifier, ...] | list[KnowledgeRequestVerifier] | None = None,
) -> KnowledgeRequestVerifier:
    """Return the first matching verifier; fallback always matches."""
    chain: tuple[KnowledgeRequestVerifier, ...] | list[KnowledgeRequestVerifier]
    chain = BUILTIN_VERIFIERS if registry is None else registry
    for verifier in chain:
        if verifier.matches(request):
            return verifier
    return NoneAvailableV1()


def verify_claimed_requests(
    requests: list[dict[str, Any]],
    *,
    claimed_keys: set[str],
    added_evidence_ids: list[str],
    child_cited_evidence_ids: Collection[str] = (),
    registry: tuple[KnowledgeRequestVerifier, ...] | list[KnowledgeRequestVerifier] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition parent KRs into claimed / verified / unverified / unclaimed.

    Returns ``(claimed, verified, unverified, unclaimed)``. Claimed items keep
    status ``claimed_fulfilled`` regardless of verification outcome. Verified
    and unverified items carry ``verification_method`` and
    ``verification_evidence_ids`` from the selected verifier.
    """
    claimed: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    unclaimed: list[dict[str, Any]] = []
    for request in requests:
        key = request.get("local_key")
        if key not in claimed_keys:
            unclaimed.append(dict(request))
            continue
        claimed.append({**request, "status": "claimed_fulfilled"})
        result = select_verifier(request, registry=registry).verify(
            request,
            added_evidence_ids,
            child_cited_evidence_ids=child_cited_evidence_ids,
        )
        item = {**request, **result}
        if result.get("status") == "verified_fulfilled":
            verified.append(item)
        else:
            unverified.append(item)
    return claimed, verified, unverified, unclaimed


def build_knowledge_request_export(
    *,
    run_id: int,
    parent_run_id: int | None,
    mission_digest: str,
    decision_question: str,
    knowledge_requests: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the OpenCrab-consumable Knowledge Request export payload."""
    requests_out: list[dict[str, Any]] = []
    for item in knowledge_requests:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {
            "local_key": item.get("local_key"),
            "question": item.get("question"),
            "gap_ref": item.get("gap_ref"),
            "priority": item.get("priority"),
        }
        if "retrieval_status" in item:
            entry["retrieval_status"] = item.get("retrieval_status")
        if "status" in item:
            entry["status"] = item.get("status")
        requests_out.append(entry)
    return {
        "schema": EXPORT_SCHEMA,
        "run_id": run_id,
        "parent_run_id": parent_run_id,
        "mission_digest": mission_digest,
        "decision_question": decision_question,
        "requests": requests_out,
    }
