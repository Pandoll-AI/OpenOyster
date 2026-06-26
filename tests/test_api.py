from __future__ import annotations

from fastapi.testclient import TestClient

from openoyster.api.app import create_app
from openoyster.models import Artifact, Chunk, Document, EvidenceEdge, Hypothesis
from openoyster.utils import sha256_text, stable_hash


def test_health_readiness_and_write_auth(temp_settings, session_factory):
    app = create_app(settings=temp_settings, session_factory=session_factory)
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/ready").status_code == 200
        assert client.post("/v1/run-cycle").status_code == 401
        response = client.post(
            "/v1/run-cycle",
            headers={temp_settings.api_key_header: temp_settings.api_key},
        )
        assert response.status_code == 200
        assert "results" in response.json()


def test_dashboard_escapes_untrusted_content(temp_settings, session_factory):
    with session_factory() as session:
        session.add(
            Hypothesis(
                claim="<script>alert('x')</script>",
                claim_hash=stable_hash("xss"),
                scope="test",
                confidence=0.5,
            )
        )
        session.commit()
    app = create_app(settings=temp_settings, session_factory=session_factory)
    with TestClient(app) as client:
        body = client.get("/").text
    assert "<script>alert('x')</script>" not in body
    assert "&lt;script&gt;" in body


def test_feedback_endpoint_records_event(temp_settings, session_factory):
    with session_factory() as session:
        artifact = Artifact(
            artifact_type="test",
            title="Artifact",
            content="content",
            content_hash=stable_hash("content"),
            version=1,
        )
        session.add(artifact)
        session.commit()
        artifact_id = artifact.id
    app = create_app(settings=temp_settings, session_factory=session_factory)
    with TestClient(app) as client:
        response = client.post(
            f"/v1/artifacts/{artifact_id}/feedback",
            json={"verdict": "useful", "score": 0.9, "comment": "adopted"},
            headers={temp_settings.api_key_header: temp_settings.api_key},
        )
    assert response.status_code == 200
    assert response.json()["verdict"] == "useful"


def test_evidence_and_provenance_endpoints_avoid_raw_document_body(temp_settings, session_factory):
    with session_factory() as session:
        document = Document(
            source="rss",
            source_uri="https://example.com/a",
            title="Evidence source",
            content_hash=sha256_text("secret raw body with governance risk"),
            ingest_key=stable_hash("doc"),
            raw_text="secret raw body with governance risk",
            status="processed",
        )
        session.add(document)
        session.flush()
        chunk = Chunk(
            document_id=document.id,
            chunk_index=0,
            text="Governance risk appears in the audited excerpt.",
            text_hash=sha256_text("chunk"),
            status="processed",
        )
        session.add(chunk)
        hypothesis = Hypothesis(
            claim="Governance risk may delay adoption.",
            claim_hash=stable_hash("hypothesis"),
            scope="Acme",
            confidence=0.6,
        )
        session.add(hypothesis)
        session.flush()
        session.add(
            EvidenceEdge(
                hypothesis_id=hypothesis.id,
                document_id=document.id,
                chunk_id=chunk.id,
                evidence_hash=stable_hash("edge"),
                stance="support",
                strength=0.7,
                summary="Governance risk appears.",
            )
        )
        artifact = Artifact(
            artifact_type="decision_memo",
            title="Memo",
            content="memo",
            content_hash=stable_hash("memo"),
            version=1,
            linked_hypothesis_id=hypothesis.id,
        )
        session.add(artifact)
        session.commit()
        hypothesis_id = hypothesis.id
        artifact_id = artifact.id

    app = create_app(settings=temp_settings, session_factory=session_factory)
    with TestClient(app) as client:
        evidence = client.get(f"/v1/hypotheses/{hypothesis_id}/evidence")
        provenance = client.get(f"/v1/artifacts/{artifact_id}/provenance")

    assert evidence.status_code == 200
    assert evidence.json()["summary"]["support_count"] == 1
    assert "secret raw body" not in evidence.text
    assert provenance.status_code == 200
    assert provenance.json()["artifact"]["id"] == artifact_id
    assert "hypothesis_evidence" in provenance.json()
