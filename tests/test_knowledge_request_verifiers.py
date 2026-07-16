"""Knowledge Request verifier registry — honesty and matching semantics."""

from __future__ import annotations

from typing import Any

from openoyster.services.cognitive_transition import _verify_claimed_requests
from openoyster.services.knowledge_request_verifiers import (
    BUILTIN_VERIFIERS,
    SemanticRelevanceVerifier,
    select_verifier,
    verify_claimed_requests,
)


class _RelatedProvider:
    """Minimal LLMProvider double for semantic relevance tests."""

    name = "test-related"

    def __init__(self, *, related: bool | None = True, error: Exception | None = None) -> None:
        self.related = related
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[Any]:
        del texts, policy
        raise NotImplementedError

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        self.calls.append((prompt, stage))
        if self.error is not None:
            raise self.error
        if self.related is None:
            return {"not_related_key": True}  # non-conforming JSON shape
        return {"related": self.related, "reason": "stub"}


_NO_EVIDENCE_REQ = {
    "local_key": "kr_no_evidence",
    "question": "What is the field recovery time SLA?",
    "gap_ref": "evidence:no_evidence",
    "priority": "critical",
}

_EVIDENCE_TEXTS = {
    "ev-a": "Field recovery time SLA is 4 hours for severity-1 incidents.",
    "ev-b": "Backup pack mentions inventory only.",
}


def test_fallback_claimed_critic_gap_is_honestly_none_available() -> None:
    """Claimed KRs with no matching specialized verifier must not lie about method.

    Critic-promoted gaps (e.g. gap_ref='options') are not covered by
    added_cited_evidence_v1. Claiming them fulfilled must surface
    verification_method='none_available_v1', not the specialized method name.
    """
    requests = [
        {
            "local_key": "kr_critic_1",
            "question": "What handoff schema and integrity fields are supported?",
            "gap_ref": "options",
            "priority": "important",
        }
    ]
    claimed, verified, unverified, unclaimed = _verify_claimed_requests(
        requests,
        claimed_keys={"kr_critic_1"},
        # Even with new evidence present, a non-matching gap must not be
        # mislabeled as verified via added_cited_evidence_v1.
        added_evidence_ids=["global-ev-new-1"],
        child_cited_evidence_ids={"global-ev-new-1"},
    )
    assert claimed == [
        {
            "local_key": "kr_critic_1",
            "question": "What handoff schema and integrity fields are supported?",
            "gap_ref": "options",
            "priority": "important",
            "status": "claimed_fulfilled",
        }
    ]
    assert verified == []
    assert unclaimed == []
    assert len(unverified) == 1
    assert unverified[0]["status"] == "claimed_unverified"
    assert unverified[0]["verification_method"] == "none_available_v1"
    assert unverified[0]["verification_evidence_ids"] == []


def test_added_cited_evidence_verifies_no_evidence_gap() -> None:
    """Verified only when added evidence is also child-cited (U5a tightened).

    Pre-#8 tests treated any non-empty added_evidence_ids as sufficient. That
    allowed pack-scoped IDs the child never cited. New bar: intersection with
    child_cited_evidence_ids must be non-empty; verification_evidence_ids is
    that intersection only.
    """
    requests = [
        {
            "local_key": "kr_no_evidence",
            "question": "Need evidence",
            "gap_ref": "evidence:no_evidence",
            "priority": "critical",
            "retrieval_status": "pack_has_no_evidence",
        }
    ]
    claimed, verified, unverified, unclaimed = verify_claimed_requests(
        requests,
        claimed_keys={"kr_no_evidence"},
        added_evidence_ids=["ev-a", "ev-b", "ev-uncited"],
        child_cited_evidence_ids={"ev-a", "ev-b"},
    )
    assert claimed[0]["status"] == "claimed_fulfilled"
    assert unverified == []
    assert unclaimed == []
    assert verified == [
        {
            "local_key": "kr_no_evidence",
            "question": "Need evidence",
            "gap_ref": "evidence:no_evidence",
            "priority": "critical",
            "retrieval_status": "pack_has_no_evidence",
            "status": "verified_fulfilled",
            "verification_method": "added_cited_evidence_v1",
            "verification_evidence_ids": ["ev-a", "ev-b"],
        }
    ]


def test_added_cited_evidence_requires_child_citation_binding() -> None:
    """RED→GREEN #8: unrelated added evidence the child never cited stays unverified."""
    requests = [
        {
            "local_key": "kr_no_evidence",
            "question": "Need evidence",
            "gap_ref": "evidence:no_evidence",
            "priority": "critical",
        }
    ]
    # Global/pack-scoped evidence present, but child did not cite it.
    _, verified, unverified, _ = verify_claimed_requests(
        requests,
        claimed_keys={"kr_no_evidence"},
        added_evidence_ids=["ev-unrelated"],
        child_cited_evidence_ids=set(),
    )
    assert verified == []
    assert unverified[0]["status"] == "claimed_unverified"
    assert unverified[0]["verification_evidence_ids"] == []

    # Same evidence becomes verifying only once the child actually cites it.
    _, verified2, unverified2, _ = verify_claimed_requests(
        requests,
        claimed_keys={"kr_no_evidence"},
        added_evidence_ids=["ev-unrelated"],
        child_cited_evidence_ids={"ev-unrelated"},
    )
    assert unverified2 == []
    assert verified2[0]["status"] == "verified_fulfilled"
    assert verified2[0]["verification_evidence_ids"] == ["ev-unrelated"]


def test_added_cited_evidence_unverified_keeps_own_method() -> None:
    """When the specialized verifier matches but cannot verify, method stays its own."""
    requests = [
        {
            "local_key": "kr_no_evidence",
            "question": "Need evidence",
            "gap_ref": "evidence:no_evidence",
            "priority": "critical",
        }
    ]
    _, verified, unverified, _ = verify_claimed_requests(
        requests,
        claimed_keys={"kr_no_evidence"},
        added_evidence_ids=[],
        child_cited_evidence_ids=set(),
    )
    assert verified == []
    assert unverified[0]["verification_method"] == "added_cited_evidence_v1"
    assert unverified[0]["status"] == "claimed_unverified"


def test_select_verifier_prefers_specialized_over_fallback() -> None:
    no_evidence = {"gap_ref": "evidence:no_evidence"}
    other = {"gap_ref": "options"}
    assert select_verifier(no_evidence).method_id == "added_cited_evidence_v1"
    assert select_verifier(other).method_id == "none_available_v1"


def test_provider_none_does_not_apply_semantic_relevance() -> None:
    """(a) provider=None keeps AddedCitedEvidenceV1 path; result unchanged."""
    # BUILTIN must not include SemanticRelevanceVerifier.
    assert not any(isinstance(v, SemanticRelevanceVerifier) for v in BUILTIN_VERIFIERS)

    requests = [dict(_NO_EVIDENCE_REQ)]
    _, verified, unverified, _ = verify_claimed_requests(
        requests,
        claimed_keys={"kr_no_evidence"},
        added_evidence_ids=["ev-a", "ev-b"],
        child_cited_evidence_ids={"ev-a", "ev-b"},
        provider=None,
        evidence_text_by_id=_EVIDENCE_TEXTS,
    )
    assert unverified == []
    assert verified[0]["status"] == "verified_fulfilled"
    assert verified[0]["verification_method"] == "added_cited_evidence_v1"
    assert verified[0]["verification_evidence_ids"] == ["ev-a", "ev-b"]


def test_semantic_relevance_related_true_verifies() -> None:
    """(b) provider + related=true → verified_fulfilled(semantic_relevance_v1)."""
    provider = _RelatedProvider(related=True)
    requests = [dict(_NO_EVIDENCE_REQ)]
    _, verified, unverified, _ = verify_claimed_requests(
        requests,
        claimed_keys={"kr_no_evidence"},
        added_evidence_ids=["ev-a", "ev-uncited"],
        child_cited_evidence_ids={"ev-a"},
        provider=provider,
        evidence_text_by_id=_EVIDENCE_TEXTS,
    )
    assert unverified == []
    assert verified[0]["status"] == "verified_fulfilled"
    assert verified[0]["verification_method"] == "semantic_relevance_v1"
    assert verified[0]["verification_evidence_ids"] == ["ev-a"]
    assert len(provider.calls) == 1
    assert provider.calls[0][1] == "kr_semantic"


def test_semantic_relevance_related_false_unverified() -> None:
    """(c) related=false → claimed_unverified (no promotion)."""
    provider = _RelatedProvider(related=False)
    requests = [dict(_NO_EVIDENCE_REQ)]
    _, verified, unverified, _ = verify_claimed_requests(
        requests,
        claimed_keys={"kr_no_evidence"},
        added_evidence_ids=["ev-a"],
        child_cited_evidence_ids={"ev-a"},
        provider=provider,
        evidence_text_by_id=_EVIDENCE_TEXTS,
    )
    assert verified == []
    assert unverified[0]["status"] == "claimed_unverified"
    assert unverified[0]["verification_method"] == "semantic_relevance_v1"
    assert unverified[0]["verification_evidence_ids"] == []


def test_semantic_relevance_provider_exception_not_promoted() -> None:
    """(d) provider exception → claimed_unverified; run does not raise."""
    provider = _RelatedProvider(error=RuntimeError("model down"))
    requests = [dict(_NO_EVIDENCE_REQ)]
    _, verified, unverified, _ = verify_claimed_requests(
        requests,
        claimed_keys={"kr_no_evidence"},
        added_evidence_ids=["ev-a"],
        child_cited_evidence_ids={"ev-a"},
        provider=provider,
        evidence_text_by_id=_EVIDENCE_TEXTS,
    )
    assert verified == []
    assert unverified[0]["status"] == "claimed_unverified"
    assert unverified[0]["verification_method"] == "semantic_relevance_v1"


def test_semantic_relevance_empty_intersection_skips_provider() -> None:
    """(e) empty added∩child_cited → claimed_unverified even with provider."""
    provider = _RelatedProvider(related=True)
    requests = [dict(_NO_EVIDENCE_REQ)]
    _, verified, unverified, _ = verify_claimed_requests(
        requests,
        claimed_keys={"kr_no_evidence"},
        added_evidence_ids=["ev-uncited"],
        child_cited_evidence_ids=set(),
        provider=provider,
        evidence_text_by_id=_EVIDENCE_TEXTS,
    )
    assert verified == []
    assert unverified[0]["status"] == "claimed_unverified"
    assert provider.calls == []


def test_semantic_relevance_missing_evidence_text_unverified() -> None:
    """Missing text in evidence_text_by_id → conservative claimed_unverified."""
    provider = _RelatedProvider(related=True)
    requests = [dict(_NO_EVIDENCE_REQ)]
    _, verified, unverified, _ = verify_claimed_requests(
        requests,
        claimed_keys={"kr_no_evidence"},
        added_evidence_ids=["ev-missing"],
        child_cited_evidence_ids={"ev-missing"},
        provider=provider,
        evidence_text_by_id={},  # no text available
    )
    assert verified == []
    assert unverified[0]["status"] == "claimed_unverified"
    assert provider.calls == []
