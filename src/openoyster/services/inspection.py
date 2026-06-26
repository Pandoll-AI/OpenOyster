from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Artifact, Chunk, Document, EvidenceEdge, Hypothesis, Task
from .artifacts import evidence_summary


def _excerpt(text: str, limit: int = 420) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def hypothesis_evidence(session: Session, hypothesis: Hypothesis) -> dict:
    edges = list(
        session.scalars(
            select(EvidenceEdge)
            .where(EvidenceEdge.hypothesis_id == hypothesis.id)
            .order_by(EvidenceEdge.strength.desc(), EvidenceEdge.id.asc())
        )
    )
    items: list[dict] = []
    for edge in edges:
        document = session.get(Document, edge.document_id) if edge.document_id else None
        chunk = session.get(Chunk, edge.chunk_id) if edge.chunk_id else None
        items.append(
            {
                "id": edge.id,
                "stance": edge.stance,
                "strength": edge.strength,
                "summary": edge.summary,
                "provenance": edge.provenance,
                "document_id": edge.document_id,
                "document_title": document.title if document else None,
                "source": document.source if document else None,
                "source_uri": document.source_uri if document else None,
                "chunk_id": edge.chunk_id,
                "chunk_excerpt": _excerpt(chunk.text) if chunk else None,
                "metadata": edge.metadata_json,
                "created_at": edge.created_at.isoformat(),
            }
        )
    return {
        "hypothesis": {
            "id": hypothesis.id,
            "claim": hypothesis.claim,
            "scope": hypothesis.scope,
            "confidence": hypothesis.confidence,
            "status": hypothesis.status,
            "revision": hypothesis.revision,
        },
        "summary": evidence_summary(edges),
        "evidence": items,
    }


def artifact_provenance(session: Session, artifact: Artifact) -> dict:
    hypothesis = (
        session.get(Hypothesis, artifact.linked_hypothesis_id) if artifact.linked_hypothesis_id else None
    )
    task = session.get(Task, artifact.linked_task_id) if artifact.linked_task_id else None
    evidence = hypothesis_evidence(session, hypothesis) if hypothesis else None
    return {
        "artifact": {
            "id": artifact.id,
            "artifact_type": artifact.artifact_type,
            "title": artifact.title,
            "version": artifact.version,
            "status": artifact.status,
            "linked_hypothesis_id": artifact.linked_hypothesis_id,
            "linked_task_id": artifact.linked_task_id,
            "metadata": artifact.metadata_json,
            "created_at": artifact.created_at.isoformat(),
        },
        "task": None
        if task is None
        else {
            "id": task.id,
            "task_type": task.task_type,
            "title": task.title,
            "status": task.status,
            "attempts": task.attempts,
        },
        "hypothesis_evidence": evidence,
    }
