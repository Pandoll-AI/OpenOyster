from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..scoring import clamp, tokenize


@dataclass(frozen=True)
class SqliteFtsRank:
    chunk_id: int
    score: float


def sqlite_fts5_ranked_chunks(
    session: Session,
    query: str,
    *,
    max_scan: int,
    excluded: set[int],
) -> tuple[list[SqliteFtsRank], bool]:
    if session.bind is None or session.bind.dialect.name != "sqlite":
        return [], False
    match_query = _fts_match_query(query)
    if not match_query:
        return [], True
    if not _sqlite_chunks_fts_available(session):
        return [], False
    try:
        rank_rows = [
            (int(row["chunk_id"]), float(row["rank"]))
            for row in session.execute(
                text(
                    """
                    SELECT rowid AS chunk_id, bm25(chunks_fts) AS rank
                    FROM chunks_fts
                    WHERE chunks_fts MATCH :query
                    ORDER BY rank ASC
                    LIMIT :limit
                    """
                ),
                {"query": match_query, "limit": max_scan},
            )
            .mappings()
            .all()
        ]
    except OperationalError:
        return [], False
    rank_rows = [(chunk_id, rank) for chunk_id, rank in rank_rows if chunk_id not in excluded]
    rank_scores = _normalise_bm25(rank_rows)
    return [SqliteFtsRank(chunk_id=chunk_id, score=rank_scores[chunk_id]) for chunk_id, _ in rank_rows], True


def _escape_fts_token(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def _fts_match_query(query: str) -> str:
    tokens = sorted(tokenize(query))
    return " OR ".join(_escape_fts_token(token) for token in tokens)


def _normalise_bm25(rows: list[tuple[int, float]]) -> dict[int, float]:
    if not rows:
        return {}
    ranks = [rank for _, rank in rows]
    best = min(ranks)
    worst = max(ranks)
    if best == worst:
        return {chunk_id: 1.0 for chunk_id, _ in rows}
    return {chunk_id: clamp(1 - ((rank - best) / (worst - best))) for chunk_id, rank in rows}


def _sqlite_chunks_fts_available(session: Session) -> bool:
    if session.bind is None or session.bind.dialect.name != "sqlite":
        return False
    try:
        return bool(
            session.scalar(
                text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chunks_fts'")
            )
        )
    except OperationalError:
        return False
