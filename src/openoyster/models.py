from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_events_idempotency_key"),
        Index("ix_events_type_id", "event_type", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_loop: Mapped[str | None] = mapped_column(String(120), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(120), index=True)
    parent_event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(250), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class EventCursor(Base):
    __tablename__ = "event_cursors"

    loop_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    last_event_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LoopLease(Base):
    __tablename__ = "loop_leases"

    loop_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    owner: Mapped[str] = mapped_column(String(120))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class LoopRun(Base):
    __tablename__ = "loop_runs"
    __table_args__ = (Index("ix_loop_runs_loop_started", "loop_name", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loop_name: Mapped[str] = mapped_column(String(120), index=True)
    owner: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), index=True)
    consumed_events: Mapped[int] = mapped_column(Integer, default=0)
    emitted_events: Mapped[int] = mapped_column(Integer, default=0)
    created_records_json: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    notes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(250), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(80), default="filesystem")
    uri: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SourceItem(Base):
    __tablename__ = "source_items"
    __table_args__ = (UniqueConstraint("source", "source_uri", name="uq_source_items_source_uri"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(250), index=True)
    source_uri: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="new", index=True)
    last_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("ingest_key", name="uq_documents_ingest_key"),
        Index("ix_documents_source_fetched", "source", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(250), index=True)
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(128), index=True)
    ingest_key: Mapped[str] = mapped_column(String(128), index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    parser_version: Mapped[str] = mapped_column(String(80), default="filesystem-v2")
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chunks: Mapped[list[Chunk]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_doc_idx"),
        Index("ix_chunks_status_attempts", "status", "attempts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("normalised_name", "kind", name="uq_entities_normalised_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(250), index=True)
    normalised_name: Mapped[str] = mapped_column(String(250), index=True)
    kind: Mapped[str] = mapped_column(String(80), default="unknown")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (UniqueConstraint("chunk_id", "claim_hash", name="uq_claims_chunk_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    claim_hash: Mapped[str] = mapped_column(String(128), index=True)
    text: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(250), nullable=True)
    predicate: Mapped[str | None] = mapped_column(String(250), nullable=True)
    object: Mapped[str | None] = mapped_column(String(250), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("chunk_id", "signal_hash", name="uq_signals_chunk_hash"),
        Index("ix_signals_type_created", "signal_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    signal_hash: Mapped[str] = mapped_column(String(128), index=True)
    entity: Mapped[str | None] = mapped_column(String(250), nullable=True, index=True)
    signal_type: Mapped[str] = mapped_column(String(80), index=True)
    summary: Mapped[str] = mapped_column(Text)
    novelty_score: Mapped[float] = mapped_column(Float, default=0.5)
    impact_score: Mapped[float] = mapped_column(Float, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Hypothesis(Base):
    __tablename__ = "hypotheses"
    __table_args__ = (
        UniqueConstraint("scope", "claim_hash", name="uq_hypotheses_scope_hash"),
        Index("ix_hypotheses_status_updated", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim: Mapped[str] = mapped_column(Text)
    claim_hash: Mapped[str] = mapped_column(String(128), index=True)
    scope: Mapped[str] = mapped_column(String(250), default="general", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(80), default="active", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    evidence_edges: Mapped[list[EvidenceEdge]] = relationship(
        back_populates="hypothesis", cascade="all, delete-orphan"
    )


class EvidenceEdge(Base):
    __tablename__ = "evidence_edges"
    __table_args__ = (
        UniqueConstraint("hypothesis_id", "evidence_hash", name="uq_evidence_hypothesis_hash"),
        Index("ix_evidence_hypothesis_stance", "hypothesis_id", "stance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hypothesis_id: Mapped[int] = mapped_column(ForeignKey("hypotheses.id", ondelete="CASCADE"), index=True)
    signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    evidence_hash: Mapped[str] = mapped_column(String(128), index=True)
    stance: Mapped[str] = mapped_column(String(30), default="support")
    strength: Mapped[float] = mapped_column(Float, default=0.5)
    summary: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(80), default="extraction")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    hypothesis: Mapped[Hypothesis] = relationship(back_populates="evidence_edges")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
        Index("ix_tasks_status_priority", "status", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(250), index=True)
    trigger_event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True, index=True)
    hypothesis_id: Mapped[int | None] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_type: Mapped[str] = mapped_column(String(80), default="analysis", index=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(80), default="pending", index=True)
    max_cost: Mapped[float] = mapped_column(Float, default=0.0)
    max_depth: Mapped[int] = mapped_column(Integer, default=2)
    tool_budget: Mapped[int] = mapped_column(Integer, default=5)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    policy_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tools_used_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    input_context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_summary: Mapped[str] = mapped_column(Text)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint(
            "artifact_type", "linked_hypothesis_id", "version", name="uq_artifact_type_hypothesis_version"
        ),
        Index("ix_artifacts_type_created", "artifact_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    linked_hypothesis_id: Mapped[int | None] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    linked_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ArtifactFeedback(Base):
    __tablename__ = "artifact_feedback"
    __table_args__ = (Index("ix_feedback_artifact_created", "artifact_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    verdict: Mapped[str] = mapped_column(String(40), index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(80), default="human")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint(
            "target_type",
            "target_id",
            "metric_name",
            "evaluator_type",
            name="uq_evaluation_target_metric_evaluator",
        ),
        Index("ix_evaluations_metric_created", "metric_name", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(80), index=True)
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    metric_name: Mapped[str] = mapped_column(String(120), index=True)
    score: Mapped[float] = mapped_column(Float)
    evaluator_type: Mapped[str] = mapped_column(String(80), default="rule")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DecisionTrace(Base):
    __tablename__ = "decision_traces"
    __table_args__ = (Index("ix_decision_traces_type_created", "decision_type", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_type: Mapped[str] = mapped_column(String(80), index=True)
    subject_type: Mapped[str] = mapped_column(String(80), index=True)
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    policy_version: Mapped[str] = mapped_column(String(120), index=True)
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    score: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    decision: Mapped[bool] = mapped_column(Boolean)
    outcome_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (UniqueConstraint("version", name="uq_policy_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(120), index=True)
    parent_policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"), nullable=True)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(80), default="active", index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"), nullable=True)
    candidate_policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"), nullable=True)
    replay_result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    shadow_result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision: Mapped[str] = mapped_column(String(80), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MissionCharter(Base):
    __tablename__ = "mission_charters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(120), index=True)
    mission: Mapped[str] = mapped_column(Text)
    domains_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    anti_goals_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    success_criteria_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemState(Base):
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(180), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PackInstall(Base):
    """Installed OpenCrab Pack revision (content-addressed by source_digest)."""

    __tablename__ = "pack_installs"
    __table_args__ = (
        UniqueConstraint("source_digest", name="uq_pack_installs_source_digest"),
        UniqueConstraint(
            "pack_id",
            "declared_version",
            "source_digest",
            name="uq_pack_installs_revision",
        ),
        Index("ix_pack_installs_pack_status", "pack_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(250), index=True)
    declared_version: Mapped[str] = mapped_column(String(120), index=True)
    format_version: Mapped[str] = mapped_column(String(80), default="opencrab-pack-v1")
    grammar_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_digest: Mapped[str] = mapped_column(String(128), index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="directory")
    source_location: Mapped[str] = mapped_column(Text)
    storage_uri: Mapped[str] = mapped_column(Text)
    admission_profile: Mapped[str] = mapped_column(String(40), default="compatible")
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    original_manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    original_quality_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    admission_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    files: Mapped[list[PackFile]] = relationship(back_populates="install", cascade="all, delete-orphan")
    nodes: Mapped[list[PackNode]] = relationship(back_populates="install", cascade="all, delete-orphan")
    edges: Mapped[list[PackEdge]] = relationship(back_populates="install", cascade="all, delete-orphan")
    evidence: Mapped[list[PackEvidence]] = relationship(
        back_populates="install", cascade="all, delete-orphan"
    )


class PackFile(Base):
    __tablename__ = "pack_files"
    __table_args__ = (
        UniqueConstraint("pack_install_id", "relative_path", name="uq_pack_files_install_path"),
        Index("ix_pack_files_hash", "computed_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_install_id: Mapped[int] = mapped_column(
        ForeignKey("pack_installs.id", ondelete="CASCADE"), index=True
    )
    relative_path: Mapped[str] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(80), default="content")
    media_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    declared_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    computed_hash: Mapped[str] = mapped_column(String(128))
    byte_count: Mapped[int] = mapped_column(Integer, default=0)
    storage_uri: Mapped[str] = mapped_column(Text)
    validation_status: Mapped[str] = mapped_column(String(40), default="ok")

    install: Mapped[PackInstall] = relationship(back_populates="files")


class PackNode(Base):
    __tablename__ = "pack_nodes"
    __table_args__ = (
        UniqueConstraint("global_node_id", name="uq_pack_nodes_global_id"),
        UniqueConstraint("pack_install_id", "local_node_id", name="uq_pack_nodes_install_local"),
        Index("ix_pack_nodes_label", "label"),
        Index("ix_pack_nodes_type", "node_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_install_id: Mapped[int] = mapped_column(
        ForeignKey("pack_installs.id", ondelete="CASCADE"), index=True
    )
    local_node_id: Mapped[str] = mapped_column(String(500))
    global_node_id: Mapped[str] = mapped_column(String(1000), index=True)
    space: Mapped[str | None] = mapped_column(String(120), nullable=True)
    node_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    record_hash: Mapped[str] = mapped_column(String(128))
    evidence_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list)

    install: Mapped[PackInstall] = relationship(back_populates="nodes")


class PackEdge(Base):
    __tablename__ = "pack_edges"
    __table_args__ = (
        UniqueConstraint("global_edge_id", name="uq_pack_edges_global_id"),
        UniqueConstraint("pack_install_id", "local_edge_id", name="uq_pack_edges_install_local"),
        Index("ix_pack_edges_relation", "relation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_install_id: Mapped[int] = mapped_column(
        ForeignKey("pack_installs.id", ondelete="CASCADE"), index=True
    )
    local_edge_id: Mapped[str] = mapped_column(String(500))
    global_edge_id: Mapped[str] = mapped_column(String(1000), index=True)
    from_local_id: Mapped[str] = mapped_column(String(500))
    to_local_id: Mapped[str] = mapped_column(String(500))
    from_global_id: Mapped[str] = mapped_column(String(1000))
    to_global_id: Mapped[str] = mapped_column(String(1000))
    from_space: Mapped[str | None] = mapped_column(String(120), nullable=True)
    to_space: Mapped[str | None] = mapped_column(String(120), nullable=True)
    relation: Mapped[str | None] = mapped_column(String(120), nullable=True)
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    record_hash: Mapped[str] = mapped_column(String(128))
    evidence_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list)

    install: Mapped[PackInstall] = relationship(back_populates="edges")


class PackEvidence(Base):
    __tablename__ = "pack_evidence"
    __table_args__ = (
        UniqueConstraint("global_evidence_id", name="uq_pack_evidence_global_id"),
        UniqueConstraint(
            "pack_install_id", "local_evidence_id", name="uq_pack_evidence_install_local"
        ),
        Index("ix_pack_evidence_kind", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pack_install_id: Mapped[int] = mapped_column(
        ForeignKey("pack_installs.id", ondelete="CASCADE"), index=True
    )
    local_evidence_id: Mapped[str] = mapped_column(String(500))
    global_evidence_id: Mapped[str] = mapped_column(String(1000), index=True)
    kind: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    parser_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ocr_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    vision_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    location_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    links_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asset_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_record_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    record_hash: Mapped[str] = mapped_column(String(128))

    install: Mapped[PackInstall] = relationship(back_populates="evidence")


class DeliberationRun(Base):
    """Source-of-truth aggregate for one Autonomous Deliberation D1 execution."""

    __tablename__ = "deliberation_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_deliberation_runs_idempotency_key"),
        Index("ix_deliberation_runs_status", "status"),
        Index("ix_deliberation_runs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(250))
    request_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliberation_runs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    # Immutable claim set for continuation runs; empty for root runs.
    # Replay and fingerprint recomputation read this column — never the
    # transition artifact's claimed list (avoids circular verification).
    fulfilled_request_keys_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    mission_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    mission_digest: Mapped[str] = mapped_column(String(128))
    policy_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    runtime_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy_digest: Mapped[str] = mapped_column(String(128))
    runtime_config_digest: Mapped[str] = mapped_column(String(128))
    contract_version: Mapped[str] = mapped_column(String(80))
    prompt_template_version: Mapped[str] = mapped_column(String(80))
    primary_scope_digest: Mapped[str] = mapped_column(String(128))
    impact_baseline_scope_digest: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(40), default="created")
    current_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    degraded_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    llm_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pack_scopes: Mapped[list[DeliberationPackScope]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    evidence_snapshots: Mapped[list[DeliberationEvidenceSnapshot]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    stage_calls: Mapped[list[DeliberationStageCall]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[DeliberationArtifact]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    dossier: Mapped[DeliberationDossier | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )
    cognitive_impact: Mapped[DeliberationCognitiveImpact | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )
    replay_results: Mapped[list[DeliberationReplayResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class DeliberationPackScope(Base):
    __tablename__ = "deliberation_pack_scopes"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "role",
            "pack_install_id",
            name="uq_deliberation_pack_scopes_run_role_install",
        ),
        Index("ix_deliberation_pack_scopes_run_role", "run_id", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_runs.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(40))
    pack_install_id: Mapped[int] = mapped_column(
        ForeignKey("pack_installs.id", ondelete="RESTRICT"), index=True
    )
    pack_id: Mapped[str] = mapped_column(String(250))
    declared_version: Mapped[str] = mapped_column(String(120))
    source_digest: Mapped[str] = mapped_column(String(128))
    admission_profile: Mapped[str] = mapped_column(String(40))
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    run: Mapped[DeliberationRun] = relationship(back_populates="pack_scopes")


class DeliberationEvidenceSnapshot(Base):
    __tablename__ = "deliberation_evidence_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "snapshot_key",
            name="uq_deliberation_evidence_snapshots_run_key",
        ),
        Index("ix_deliberation_evidence_snapshots_global", "global_evidence_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_runs.id", ondelete="CASCADE"), index=True
    )
    snapshot_key: Mapped[str] = mapped_column(String(120))
    pack_evidence_id: Mapped[int] = mapped_column(
        ForeignKey("pack_evidence.id", ondelete="RESTRICT"), index=True
    )
    global_evidence_id: Mapped[str] = mapped_column(String(1000))
    local_evidence_id: Mapped[str] = mapped_column(String(500))
    pack_install_id: Mapped[int] = mapped_column(Integer)
    record_hash: Mapped[str] = mapped_column(String(128))
    prompt_visible_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload_digest: Mapped[str] = mapped_column(String(128))
    retrieval_rank: Mapped[int] = mapped_column(Integer)
    retrieval_score: Mapped[float] = mapped_column(Float, default=0.0)

    run: Mapped[DeliberationRun] = relationship(back_populates="evidence_snapshots")
    citations: Mapped[list[DeliberationCitation]] = relationship(back_populates="evidence_snapshot")


class DeliberationStageCall(Base):
    __tablename__ = "deliberation_stage_calls"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "stage",
            "attempt_number",
            name="uq_deliberation_stage_calls_run_stage_attempt",
        ),
        Index("ix_deliberation_stage_calls_run_stage", "run_id", "stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_runs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(80))
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="started")
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(40), nullable=True)
    template_version: Mapped[str] = mapped_column(String(80))
    prompt_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    config_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_manifest_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    response_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_response_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_response_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[DeliberationRun] = relationship(back_populates="stage_calls")
    artifacts: Mapped[list[DeliberationArtifact]] = relationship(back_populates="stage_call")


class DeliberationArtifact(Base):
    __tablename__ = "deliberation_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "kind",
            "local_key",
            name="uq_deliberation_artifacts_run_kind_key",
        ),
        Index("ix_deliberation_artifacts_run_kind", "run_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_runs.id", ondelete="CASCADE"), index=True
    )
    stage_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliberation_stage_calls.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(80))
    local_key: Mapped[str] = mapped_column(String(120))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload_digest: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[DeliberationRun] = relationship(back_populates="artifacts")
    stage_call: Mapped[DeliberationStageCall | None] = relationship(back_populates="artifacts")
    assertions: Mapped[list[DeliberationAssertion]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan"
    )


class DeliberationAssertion(Base):
    __tablename__ = "deliberation_assertions"
    __table_args__ = (Index("ix_deliberation_assertions_classification", "classification"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_artifacts.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(250), default="")
    text: Mapped[str] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(String(40))
    mission_pointer: Mapped[str | None] = mapped_column(String(250), nullable=True)
    artifact_ref: Mapped[str | None] = mapped_column(String(250), nullable=True)
    issue_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    artifact: Mapped[DeliberationArtifact] = relationship(back_populates="assertions")
    citations: Mapped[list[DeliberationCitation]] = relationship(
        back_populates="assertion", cascade="all, delete-orphan"
    )


class DeliberationCitation(Base):
    __tablename__ = "deliberation_citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assertion_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_assertions.id", ondelete="CASCADE"), index=True
    )
    evidence_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_evidence_snapshots.id", ondelete="RESTRICT"), index=True
    )
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    json_pointer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    value_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # statement | supporting | opposing — belief role anchors share the statement row.
    role: Mapped[str] = mapped_column(String(20), server_default="statement", default="statement")

    assertion: Mapped[DeliberationAssertion] = relationship(back_populates="citations")
    evidence_snapshot: Mapped[DeliberationEvidenceSnapshot] = relationship(
        back_populates="citations"
    )


class DeliberationDossier(Base):
    __tablename__ = "deliberation_dossiers"
    __table_args__ = (UniqueConstraint("run_id", name="uq_deliberation_dossiers_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_runs.id", ondelete="CASCADE"), index=True
    )
    dossier_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dossier_markdown: Mapped[str] = mapped_column(Text, default="")
    json_digest: Mapped[str] = mapped_column(String(128))
    markdown_digest: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[DeliberationRun] = relationship(back_populates="dossier")


class DeliberationCognitiveImpact(Base):
    __tablename__ = "deliberation_cognitive_impacts"
    __table_args__ = (UniqueConstraint("run_id", name="uq_deliberation_cognitive_impacts_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_runs.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column(String(80), default="citation_scope_projection_v1")
    impact_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    impact_digest: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[DeliberationRun] = relationship(back_populates="cognitive_impact")


class DeliberationReplayResult(Base):
    __tablename__ = "deliberation_replay_results"
    __table_args__ = (Index("ix_deliberation_replay_results_run", "run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_runs.id", ondelete="CASCADE"), index=True
    )
    matched: Mapped[bool] = mapped_column(Boolean, default=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stored_dossier_digest: Mapped[str] = mapped_column(String(128))
    recomputed_dossier_digest: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[DeliberationRun] = relationship(back_populates="replay_results")


class DeliberationFlipWatch(Base):
    """Append-only flip-condition watch; status transitions only (D3)."""

    __tablename__ = "deliberation_flip_watches"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "flip_local_key",
            name="uq_deliberation_flip_watches_run_key",
        ),
        Index("ix_deliberation_flip_watches_status", "status"),
        Index("ix_deliberation_flip_watches_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_runs.id", ondelete="CASCADE"), index=True
    )
    flip_local_key: Mapped[str] = mapped_column(String(120))
    predicate_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="watching")
    dismiss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    triggers: Mapped[list[DeliberationFlipTrigger]] = relationship(
        back_populates="watch", cascade="all, delete-orphan"
    )


class DeliberationFlipTrigger(Base):
    """Append-only match record for a flip watch against a Pack install (D3)."""

    __tablename__ = "deliberation_flip_triggers"
    __table_args__ = (
        UniqueConstraint(
            "watch_id",
            "pack_install_id",
            name="uq_deliberation_flip_triggers_watch_install",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_flip_watches.id", ondelete="CASCADE"), index=True
    )
    pack_install_id: Mapped[int] = mapped_column(
        ForeignKey("pack_installs.id", ondelete="RESTRICT"), index=True
    )
    matched_evidence_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    watch: Mapped[DeliberationFlipWatch] = relationship(back_populates="triggers")


class DeliberationCharter(Base):
    """First-class sustained concern grouping for deliberation missions.

    Control-plane grouping only — never Pack evidence, never injected into
    stage prompts. Soft-delete via status=archived (no hard delete).
    """

    __tablename__ = "deliberation_charters"
    __table_args__ = (
        Index("ix_deliberation_charters_status", "status"),
        Index("ix_deliberation_charters_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(250))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DeliberationOutcome(Base):
    """Append-only user-recorded result for a completed deliberation run.

    Usage record only — never Pack evidence, never injected into prompts.
    """

    __tablename__ = "deliberation_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_deliberation_outcomes_idempotency_key",
        ),
        Index("ix_deliberation_outcomes_noted_at", "noted_at"),
        Index("ix_deliberation_outcomes_outcome_label", "outcome_label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("deliberation_runs.id", ondelete="CASCADE"), index=True
    )
    outcome_label: Mapped[str] = mapped_column(String(40))
    scenario_assessments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    abstention_assessment: Mapped[str | None] = mapped_column(String(80), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    noted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    noted_by: Mapped[str] = mapped_column(String(120), default="user")
    idempotency_key: Mapped[str | None] = mapped_column(String(250), nullable=True)
