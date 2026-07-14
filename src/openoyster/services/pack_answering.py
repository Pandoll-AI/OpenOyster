"""Grounded Pack answering with citation fail-closed behaviour."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from openoyster.llm import LLMProvider
from openoyster.services.pack_retrieval import PackRetrievalResult, search_pack_context
from openoyster.services.prompts import build_pack_answer_prompt

AnswerStatus = Literal["supported", "unknown"]


@dataclass(frozen=True)
class PackAnswer:
    status: AnswerStatus
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    pack_scope: list[dict[str, str]] = field(default_factory=list)
    retrieval: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None


def _citation_records(
    raw_citations: list[Any],
    retrieval: PackRetrievalResult,
) -> tuple[list[dict[str, Any]], list[str]]:
    allowed = {row.global_evidence_id: row for row in retrieval.evidence}

    resolved: list[dict[str, Any]] = []
    unknown: list[str] = []
    for item in raw_citations:
        if isinstance(item, dict):
            candidate = str(
                item.get("global_evidence_id")
                or item.get("evidence_id")
                or item.get("id")
                or ""
            ).strip()
        else:
            candidate = str(item).strip()
        if not candidate:
            unknown.append(str(item))
            continue
        row = allowed.get(candidate)
        if row is None:
            unknown.append(candidate)
            continue
        install = next(
            (
                scope
                for scope in retrieval.pack_scope
                if scope["source_digest"] in row.global_evidence_id
            ),
            None,
        )
        resolved.append(
            {
                "evidence_id": row.local_evidence_id,
                "global_evidence_id": row.global_evidence_id,
                "pack_id": install["pack_id"] if install else None,
                "declared_version": install["declared_version"] if install else None,
                "source_digest": install["source_digest"] if install else None,
                "text": row.text,
                "source": row.source_json,
            }
        )
    return resolved, unknown


def _unknown_answer(
    *,
    reason: str,
    pack_scope: list[dict[str, str]],
    retrieval: PackRetrievalResult | None = None,
) -> PackAnswer:
    diagnostics = retrieval.diagnostics if retrieval is not None else {"reason": reason}
    return PackAnswer(
        status="unknown",
        answer="unknown",
        citations=[],
        pack_scope=pack_scope,
        retrieval={
            "diagnostics": diagnostics,
            "hit_count": len(retrieval.hits) if retrieval is not None else 0,
            "evidence_ids": [
                row.global_evidence_id for row in (retrieval.evidence if retrieval else [])
            ],
        },
        reason=reason,
    )


def answer_pack_query(
    session: Session,
    question: str,
    provider: LLMProvider,
    *,
    pack_ids: list[str] | None = None,
    top_k: int = 20,
) -> PackAnswer:
    """Answer using only retrieved Pack evidence; fail closed on bad citations.

    If retrieval finds no supporting context, returns ``unknown`` without calling
    the LLM provider.
    """
    retrieval = search_pack_context(session, question, pack_ids=pack_ids, top_k=top_k)
    if not retrieval.has_context or not retrieval.evidence:
        return _unknown_answer(
            reason="no_retrieval_context" if not retrieval.has_context else "no_evidence_in_context",
            pack_scope=retrieval.pack_scope,
            retrieval=retrieval,
        )

    prompt = build_pack_answer_prompt(question=question, retrieval=retrieval)
    try:
        payload = provider.query_json(prompt, "pack_answer")
    except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
        return _unknown_answer(
            reason=f"generation_failed:{exc.__class__.__name__}",
            pack_scope=retrieval.pack_scope,
            retrieval=retrieval,
        )

    if not isinstance(payload, dict):
        return _unknown_answer(
            reason="invalid_generation_payload",
            pack_scope=retrieval.pack_scope,
            retrieval=retrieval,
        )

    raw_status = str(payload.get("status") or "").strip().lower()
    answer_text = str(payload.get("answer") or "").strip()
    raw_citations = payload.get("citations") or payload.get("evidence_ids") or []
    if not isinstance(raw_citations, list):
        raw_citations = [raw_citations]

    if raw_status in {"unknown", "insufficient_evidence"}:
        return _unknown_answer(
            reason="model_declared_unknown",
            pack_scope=retrieval.pack_scope,
            retrieval=retrieval,
        )

    citations, unknown_ids = _citation_records(raw_citations, retrieval)
    if unknown_ids:
        return _unknown_answer(
            reason=f"unverified_citations:{','.join(unknown_ids)}",
            pack_scope=retrieval.pack_scope,
            retrieval=retrieval,
        )
    if not citations:
        return _unknown_answer(
            reason="missing_citations",
            pack_scope=retrieval.pack_scope,
            retrieval=retrieval,
        )
    if not answer_text or answer_text.casefold() == "unknown":
        return _unknown_answer(
            reason="empty_or_unknown_answer",
            pack_scope=retrieval.pack_scope,
            retrieval=retrieval,
        )

    return PackAnswer(
        status="supported",
        answer=answer_text,
        citations=citations,
        pack_scope=retrieval.pack_scope,
        retrieval={
            "diagnostics": retrieval.diagnostics,
            "hit_count": len(retrieval.hits),
            "evidence_ids": [row.global_evidence_id for row in retrieval.evidence],
            "node_ids": [node.global_node_id for node in retrieval.nodes],
        },
        reason=None,
    )
