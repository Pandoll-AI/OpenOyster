from __future__ import annotations

from openoyster.models import Chunk, Document
from openoyster.policies import DEFAULT_POLICY, set_nested
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
    assert hits[0].retrieval_mode == "lexical"
    assert "governance" in hits[0].matched_terms


def test_counter_mode_uses_counter_terms(session_factory):
    with session_factory() as session:
        _document(
            session,
            title="Counterpoint",
            source="rss",
            text="The audit team found no evidence that model quality is the blocker.",
        )
        session.commit()
        hits = search_chunks(session, "model quality blocker", policy=DEFAULT_POLICY, mode="counter")
    assert hits
    assert {"no", "evidence"} & set(hits[0].matched_terms)
