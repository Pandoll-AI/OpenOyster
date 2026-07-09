from __future__ import annotations

from sqlalchemy import text

from openoyster.models import Chunk, Document, SystemState
from openoyster.policies import DEFAULT_POLICY, set_nested
from openoyster.services import retrieval
from openoyster.services.retrieval import search_chunks
from openoyster.utils import sha256_text, stable_hash


def _document(session, *, title: str, source: str, text: str) -> Document:
    document = Document(
        source=source,
        source_uri=f"https://example.com/{stable_hash(title)}",
        title=title,
        content_hash=sha256_text(text),
        ingest_key=stable_hash(source, title, text),
        raw_text=text,
        status="processed",
    )
    session.add(document)
    session.flush()
    chunk = Chunk(
        document_id=document.id,
        chunk_index=0,
        text=text,
        text_hash=sha256_text(text),
        status="processed",
    )
    session.add(chunk)
    session.flush()
    return document


def test_search_chunks_adds_metadata_and_limits_source_diversity(session_factory):
    policy = set_nested(DEFAULT_POLICY, "retrieval.source_diversity_cap", 1)
    with session_factory() as session:
        _document(
            session,
            title="Acme governance",
            source="rss",
            text="Acme governance approval delay creates a strategic risk for adoption.",
        )
        _document(
            session,
            title="Acme audit",
            source="rss",
            text="Acme governance audit delay creates another strategic risk for adoption.",
        )
        _document(
            session,
            title="Beta policy",
            source="github:beta/project",
            text="Beta governance release describes approval controls and strategic adoption risk.",
        )
        session.commit()

        hits = search_chunks(session, "governance approval strategic risk", policy=policy)

    assert len(hits) >= 2
    assert len([hit for hit in hits[:2] if hit.source == "rss"]) == 1
    assert hits[0].retrieval_mode == "sqlite_fts5"
    assert "governance" in hits[0].matched_terms


def test_sqlite_fts_finds_korean_text_and_records_tokenizer(session_factory):
    with session_factory() as session:
        _document(
            session,
            title="병원 거버넌스",
            source="rss",
            text="병원 데이터 거버넌스 승인 절차가 지연되어 배포 일정에 위험이 생겼다.",
        )
        session.commit()

        hits = search_chunks(session, "거버넌스 승인 위험", policy=DEFAULT_POLICY)
        tokenizer = session.get(SystemState, "chunks_fts_tokenizer")

    assert hits
    assert hits[0].retrieval_mode == "sqlite_fts5"
    assert hits[0].document_title == "병원 거버넌스"
    assert tokenizer is not None
    assert tokenizer.value_json["tokenizer"] in {"trigram", "unicode61"}


def test_sqlite_fts_trigram_matches_korean_substring_when_available(session_factory):
    with session_factory() as session:
        _document(
            session,
            title="부분 검색",
            source="rss",
            text="병원 데이터 거버넌스 승인 절차가 강화됐다.",
        )
        session.commit()
        tokenizer = session.get(SystemState, "chunks_fts_tokenizer")
        hits = search_chunks(session, "버넌스", policy=DEFAULT_POLICY)

    if tokenizer is not None and tokenizer.value_json["tokenizer"] == "trigram":
        assert hits
        assert hits[0].document_title == "부분 검색"


def test_sqlite_fts_bm25_orders_stronger_matches_first(session_factory):
    with session_factory() as session:
        _document(
            session,
            title="Weak",
            source="rss",
            text="Governance was mentioned in a routine update.",
        )
        _document(
            session,
            title="Strong",
            source="rss",
            text="Governance approval risk delayed governance approval risk mitigation.",
        )
        session.commit()

        hits = search_chunks(session, "governance approval risk", policy=DEFAULT_POLICY)

    assert hits
    assert hits[0].document_title == "Strong"
    assert hits[0].score >= hits[-1].score


def test_sqlite_fts_query_escapes_quotes_and_special_characters(session_factory):
    with session_factory() as session:
        _document(
            session,
            title="Quoted",
            source="rss",
            text='Governance "approval" risk remains unresolved despite review.',
        )
        session.commit()
        hits = search_chunks(session, '"governance" OR (risk*)', policy=DEFAULT_POLICY)

    assert hits
    assert hits[0].document_title == "Quoted"


def test_search_chunks_uses_lexical_fallback_when_fts_table_is_unavailable(session_factory):
    with session_factory() as session:
        _document(
            session,
            title="Fallback",
            source="rss",
            text="Acme governance approval delay creates a strategic risk for adoption.",
        )
        session.execute(text("DROP TABLE chunks_fts"))
        session.commit()

        hits = search_chunks(session, "governance approval strategic risk", policy=DEFAULT_POLICY)

    assert hits
    assert hits[0].retrieval_mode == "lexical_fallback"


def test_search_chunks_does_not_lexical_fallback_when_postgres_full_text_has_zero_hits(
    monkeypatch,
    session_factory,
):
    policy = set_nested(DEFAULT_POLICY, "retrieval.mode", "postgres_full_text")

    def no_postgres_hits(*args, **kwargs):
        del args, kwargs
        return [], True

    monkeypatch.setattr(retrieval, "_postgres_full_text_hits", no_postgres_hits)
    with session_factory() as session:
        _document(
            session,
            title="Should not fallback",
            source="rss",
            text="Acme governance approval delay creates a strategic risk for adoption.",
        )
        session.commit()

        hits = search_chunks(session, "governance approval strategic risk", policy=policy)

    assert hits == []
