"""Type-specific Knowledge Request verifiers and machine-readable export.

Claimed fulfillments are checked by a registry of verifiers. Specialized
verifiers match by request shape (currently gap_ref); unmatched claims fall
through to ``none_available_v1`` so the transition record is honest about
what was actually checked.

Semantic relevance (question↔evidence) is an *optional* second-model gate:
when ``critic2`` is configured, ``verify_claimed_requests`` injects
``SemanticRelevanceVerifier`` ahead of ``AddedCitedEvidenceV1``. Without a
provider the built-in chain is unchanged. True cross-vendor independence holds
only for ``claude-cli``; ``codex``/``stub`` are self-consistency checks.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openoyster.llm import LLMProvider

EXPORT_SCHEMA = "openoyster.knowledge_request_export/v1"

# Caps for the optional semantic-relevance prompt (question + evidence bodies).
_SEMANTIC_MAX_EVIDENCE = 8
_SEMANTIC_MAX_CHARS = 8000
_SEMANTIC_STAGE = "kr_semantic"


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
        provider: LLMProvider | None = None,
        evidence_text_by_id: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return status / verification_method / verification_evidence_ids.

        Optional ``provider`` / ``evidence_text_by_id`` are ignored by built-in
        structural verifiers; only ``SemanticRelevanceVerifier`` uses them.
        """
        raise NotImplementedError


def _unverified(method_id: str) -> dict[str, Any]:
    return {
        "status": "claimed_unverified",
        "verification_method": method_id,
        "verification_evidence_ids": [],
    }


def _bound_cited_added(
    added_evidence_ids: list[str],
    child_cited_evidence_ids: Collection[str],
) -> list[str]:
    cited = set(child_cited_evidence_ids)
    return sorted({eid for eid in added_evidence_ids if eid in cited})


class AddedCitedEvidenceV1(KnowledgeRequestVerifier):
    """Verify ``evidence:no_evidence`` gaps via newly cited global evidence.

    Requires the intersection of ``added_evidence_ids`` and
    ``child_cited_evidence_ids`` to be non-empty. This binds verification to
    evidence the child run actually cited in beliefs/assertions — not merely
    pack-scoped or globally present IDs.

    Limitation: question↔evidence *semantic* relevance is not checked by this
    verifier alone. When critic2 is configured, ``SemanticRelevanceVerifier``
    is injected ahead of this class and may strengthen the gate. True
    independent judgement requires ``claude-cli``; ``codex``/``stub`` are
    self-consistency only. See docs/DECISION_CONTINUITY_D2_REQUIREMENTS.md §8.1.
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
        provider: LLMProvider | None = None,
        evidence_text_by_id: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        del request, provider, evidence_text_by_id
        bound = _bound_cited_added(added_evidence_ids, child_cited_evidence_ids)
        if bound:
            return {
                "status": "verified_fulfilled",
                "verification_method": self.method_id,
                "verification_evidence_ids": bound,
            }
        return _unverified(self.method_id)


class SemanticRelevanceVerifier(KnowledgeRequestVerifier):
    """Optional 2nd-model gate: does child-cited new evidence answer the KR?

    Matches the same ``evidence:no_evidence`` shape as ``AddedCitedEvidenceV1``.
    Not listed in ``BUILTIN_VERIFIERS``; ``verify_claimed_requests`` inserts
    this instance only when ``provider`` is non-None. Without a provider the
    built-in chain falls through to ``AddedCitedEvidenceV1``.

    Conservative on failure: empty intersection, missing evidence text,
    provider exception, non-JSON, or ``related`` not true → claimed_unverified
    (never promotes to verified).
    """

    name = "semantic_relevance"
    version = "v1"

    def matches(self, request: dict[str, Any]) -> bool:
        return request.get("gap_ref") == "evidence:no_evidence"

    def verify(
        self,
        request: dict[str, Any],
        added_evidence_ids: list[str],
        *,
        child_cited_evidence_ids: Collection[str] = (),
        provider: LLMProvider | None = None,
        evidence_text_by_id: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        # Provider must be injected for this verifier to apply; registry wiring
        # already gates on non-None, but re-check so a bare instance stays safe.
        if provider is None:
            return _unverified(self.method_id)

        bound = _bound_cited_added(added_evidence_ids, child_cited_evidence_ids)
        if not bound:
            return _unverified(self.method_id)

        texts = evidence_text_by_id or {}
        selected = bound[:_SEMANTIC_MAX_EVIDENCE]
        bodies: list[tuple[str, str]] = []
        for eid in selected:
            text = texts.get(eid)
            if not isinstance(text, str) or not text.strip():
                return _unverified(self.method_id)
            bodies.append((eid, text.strip()))

        prompt = _build_semantic_relevance_prompt(
            question=str(request.get("question") or ""),
            evidence_bodies=bodies,
        )
        try:
            raw = provider.query_json(prompt, _SEMANTIC_STAGE)
        except Exception:
            return _unverified(self.method_id)

        if not isinstance(raw, dict) or raw.get("related") is not True:
            return _unverified(self.method_id)

        return {
            "status": "verified_fulfilled",
            "verification_method": self.method_id,
            "verification_evidence_ids": bound,
        }


def _build_semantic_relevance_prompt(
    *,
    question: str,
    evidence_bodies: list[tuple[str, str]],
) -> str:
    """Build a capped prompt; reason field must not echo source text."""
    lines = [
        "Decide whether the cited evidence actually answers the knowledge request.",
        "Return JSON only: {\"related\": true|false, \"reason\": \"short safe summary\"}.",
        "Do not quote or copy evidence wording into reason.",
        f"KNOWLEDGE_REQUEST: {question}",
        "EVIDENCE:",
    ]
    budget = _SEMANTIC_MAX_CHARS - sum(len(line) + 1 for line in lines)
    for eid, text in evidence_bodies:
        header = f"[id={eid}] "
        # Reserve a newline after each body.
        room = budget - len(header) - 1
        if room <= 0:
            break
        body = text if len(text) <= room else text[:room]
        lines.append(header + body)
        budget -= len(header) + len(body) + 1
    return "\n".join(lines)


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
        provider: LLMProvider | None = None,
        evidence_text_by_id: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        del request, added_evidence_ids, child_cited_evidence_ids
        del provider, evidence_text_by_id
        return _unverified(self.method_id)


# Specialized first; fallback last. Order is the registry contract.
# SemanticRelevanceVerifier is intentionally *not* here — injected dynamically
# when verify_claimed_requests receives a non-None provider.
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


def _resolve_registry(
    registry: tuple[KnowledgeRequestVerifier, ...] | list[KnowledgeRequestVerifier] | None,
    provider: LLMProvider | None,
) -> tuple[KnowledgeRequestVerifier, ...] | list[KnowledgeRequestVerifier]:
    if registry is not None:
        return registry
    if provider is not None:
        # Semantic gate first; structural AddedCitedEvidence remains as fallback
        # for non-matching shapes only (same gap_ref means semantic owns the claim).
        return (SemanticRelevanceVerifier(), *BUILTIN_VERIFIERS)
    return BUILTIN_VERIFIERS


def verify_claimed_requests(
    requests: list[dict[str, Any]],
    *,
    claimed_keys: set[str],
    added_evidence_ids: list[str],
    child_cited_evidence_ids: Collection[str] = (),
    provider: LLMProvider | None = None,
    evidence_text_by_id: dict[str, str] | None = None,
    registry: tuple[KnowledgeRequestVerifier, ...] | list[KnowledgeRequestVerifier] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition parent KRs into claimed / verified / unverified / unclaimed.

    Returns ``(claimed, verified, unverified, unclaimed)``. Claimed items keep
    status ``claimed_fulfilled`` regardless of verification outcome. Verified
    and unverified items carry ``verification_method`` and
    ``verification_evidence_ids`` from the selected verifier.

    When ``provider`` is non-None and no explicit ``registry`` is passed, a
    ``SemanticRelevanceVerifier`` is prepended so ``evidence:no_evidence``
    claims are judged for question↔evidence relatedness.
    """
    chain = _resolve_registry(registry, provider)
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
        result = select_verifier(request, registry=chain).verify(
            request,
            added_evidence_ids,
            child_cited_evidence_ids=child_cited_evidence_ids,
            provider=provider,
            evidence_text_by_id=evidence_text_by_id,
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
