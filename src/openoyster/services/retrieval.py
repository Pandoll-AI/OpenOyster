from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..models import Chunk, Document
from ..scoring import clamp, tokenize
from ..utils import ensure_utc


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: int
    document_id: int
    document_title: str
    source: str
    text: str
    score: float
    fetched_at: datetime
    retrieval_mode: str = "lexical"
    matched_terms: list[str] = field(default_factory=list)


def _lexical_similarity(query: str, text: str) -> float:
    query_tokens = tokenize(query)
    text_tokens = tokenize(text)
    if not query_tokens or not text_tokens:
        return 0.0
    coverage = len(query_tokens & text_tokens) / len(query_tokens)
    jaccard = len(query_tokens & text_tokens) / len(query_tokens | text_tokens)
    return clamp(0.75 * coverage + 0.25 * jaccard)


def _matched_terms(query: str, text: str) -> list[str]:
    query_tokens = tokenize(query)
    text_tokens = tokenize(text)
    return sorted(query_tokens & text_tokens)


def _entity_boost(query: str, document_title: str, source: str) -> float:
    query_tokens = tokenize(query)
    surface_tokens = tokenize(f"{document_title} {source}")
    if not query_tokens or not surface_tokens:
        return 0.0
    return min(len(query_tokens & surface_tokens) * 0.035, 0.12)


def _counter_query(query: str, terms: list[str]) -> str:
    return " ".join([query, *terms])


def _limit_source_diversity(hits: list[RetrievalHit], cap: int, top_k: int) -> list[RetrievalHit]:
    if cap <= 0:
        return hits[:top_k]
    selected: list[RetrievalHit] = []
    per_source: dict[str, int] = {}
    deferred: list[RetrievalHit] = []
    for hit in hits:
        count = per_source.get(hit.source, 0)
        if count < cap:
            selected.append(hit)
            per_source[hit.source] = count + 1
        else:
            deferred.append(hit)
        if len(selected) >= top_k:
            return selected
    for hit in deferred:
        selected.append(hit)
        if len(selected) >= top_k:
            break
    return selected


def _postgres_full_text_hits(
    session: Session,
    query: str,
    *,
    max_scan: int,
    minimum: float,
    recency_weight: float,
    excluded: set[int],
) -> list[RetrievalHit]:
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return []
    rows = session.execute(
        select(
            Chunk,
            Document,
            func.ts_rank_cd(
                func.to_tsvector("simple", Chunk.text),
                func.plainto_tsquery("simple", query),
            ).label("rank"),
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.status == "processed",
            func.to_tsvector("simple", Chunk.text).op("@@")(func.plainto_tsquery("simple", query)),
        )
        .order_by(text("rank DESC"))
        .limit(max_scan)
    ).all()
    now = datetime.now(UTC)
    hits: list[RetrievalHit] = []
    for chunk, document, rank in rows:
        if chunk.id in excluded:
            continue
        lexical = _lexical_similarity(query, chunk.text)
        rank_score = clamp(float(rank or 0.0))
        if max(lexical, rank_score) < minimum:
            continue
        age_days = max((now - ensure_utc(document.fetched_at)).total_seconds() / 86_400, 0.0)
        recency = 1 / (1 + age_days / 30)
        score = clamp((1 - recency_weight) * max(lexical, rank_score) + recency_weight * recency)
        hits.append(
            RetrievalHit(
                chunk_id=chunk.id,
                document_id=document.id,
                document_title=document.title,
                source=document.source,
                text=chunk.text,
                score=score,
                fetched_at=document.fetched_at,
                retrieval_mode="postgres_full_text",
                matched_terms=_matched_terms(query, chunk.text),
            )
        )
    return hits


def search_chunks(
    session: Session,
    query: str,
    *,
    policy: dict,
    exclude_chunk_ids: set[int] | None = None,
    mode: Literal["support", "counter", "neutral"] = "neutral",
) -> list[RetrievalHit]:
    config = policy.get("retrieval", {})
    top_k = int(config.get("top_k", 12))
    max_scan = int(config.get("max_scan_chunks", 5000))
    minimum = float(config.get("minimum_similarity", 0.08))
    recency_weight = float(config.get("recency_weight", 0.15))
    source_diversity_cap = int(config.get("source_diversity_cap", 0))
    retrieval_mode = str(config.get("mode", "lexical"))
    counter_terms = [str(item) for item in config.get("counter_evidence_terms", [])]
    excluded = exclude_chunk_ids or set()
    search_query = _counter_query(query, counter_terms) if mode == "counter" else query

    if retrieval_mode in {"postgres_full_text", "auto"}:
        postgres_hits = _postgres_full_text_hits(
            session,
            search_query,
            max_scan=max_scan,
            minimum=minimum,
            recency_weight=recency_weight,
            excluded=excluded,
        )
        if postgres_hits:
            postgres_hits.sort(key=lambda hit: (hit.score, hit.fetched_at), reverse=True)
            return _limit_source_diversity(postgres_hits, source_diversity_cap, top_k)

    criteria = [Chunk.status == "processed"]
    if excluded:
        criteria.append(Chunk.id.not_in(excluded))
    rows = session.execute(
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(*criteria)
        .order_by(Chunk.id.desc())
        .limit(max_scan)
    ).all()
    now = datetime.now(UTC)
    hits: list[RetrievalHit] = []
    for chunk, document in rows:
        if chunk.id in excluded:
            continue
        lexical = _lexical_similarity(search_query, chunk.text)
        if lexical < minimum:
            continue
        age_days = max((now - ensure_utc(document.fetched_at)).total_seconds() / 86_400, 0.0)
        recency = 1 / (1 + age_days / 30)
        score = clamp((1 - recency_weight) * lexical + recency_weight * recency)
        score = clamp(score + _entity_boost(search_query, document.title, document.source))
        hits.append(
            RetrievalHit(
                chunk_id=chunk.id,
                document_id=document.id,
                document_title=document.title,
                source=document.source,
                text=chunk.text,
                score=score,
                fetched_at=document.fetched_at,
                retrieval_mode="lexical",
                matched_terms=_matched_terms(search_query, chunk.text),
            )
        )
    hits.sort(key=lambda hit: (hit.score, hit.fetched_at), reverse=True)
    return _limit_source_diversity(hits, source_diversity_cap, top_k)
