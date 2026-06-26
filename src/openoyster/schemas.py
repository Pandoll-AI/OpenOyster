from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventOut(BaseModel):
    id: int
    event_type: str
    payload_json: dict[str, Any]
    source_loop: str | None = None
    correlation_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentOut(BaseModel):
    id: int
    source: str
    source_uri: str | None
    title: str
    content_hash: str
    status: str
    failure_count: int
    last_error: str | None
    fetched_at: datetime
    processed_at: datetime | None

    model_config = {"from_attributes": True}


class SignalDraft(BaseModel):
    entity: str | None = None
    signal_type: str = "observation"
    summary: str
    novelty_score: float = Field(default=0.5, ge=0, le=1)
    impact_score: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    stance: Literal["support", "oppose", "neutral"] = "support"
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ClaimDraft(BaseModel):
    text: str
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class HypothesisDraft(BaseModel):
    claim: str
    scope: str = "general"
    confidence: float = Field(default=0.35, ge=0, le=1)
    evidence_signal_summary: str | None = None
    stance: Literal["support", "oppose", "neutral"] = "support"
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class HypothesisOut(BaseModel):
    id: int
    claim: str
    scope: str
    confidence: float
    status: str
    revision: int
    last_reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskOut(BaseModel):
    id: int
    task_type: str
    title: str
    description: str
    priority: float
    status: str
    attempts: int
    last_error: str | None
    available_at: datetime
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class ArtifactOut(BaseModel):
    id: int
    artifact_type: str
    title: str
    content: str
    version: int
    status: str
    linked_hypothesis_id: int | None
    linked_task_id: int | None
    metadata_json: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class ArtifactFeedbackIn(BaseModel):
    verdict: Literal["used", "useful", "rejected", "stale", "not_useful"]
    score: float | None = Field(default=None, ge=0, le=1)
    comment: str | None = Field(default=None, max_length=4000)
    source: str = Field(default="human", max_length=80)


class ArtifactFeedbackOut(BaseModel):
    id: int
    artifact_id: int
    verdict: str
    score: float | None
    comment: str | None
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PolicyOut(BaseModel):
    id: int
    version: str
    parent_policy_id: int | None
    policy_json: dict[str, Any]
    status: str
    score: float | None
    evaluation_json: dict[str, Any]
    created_at: datetime
    promoted_at: datetime | None

    model_config = {"from_attributes": True}


class LoopRunOut(BaseModel):
    id: int
    loop_name: str
    owner: str
    status: str
    consumed_events: int
    emitted_events: int
    created_records_json: dict[str, int]
    notes_json: list[str]
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: float | None

    model_config = {"from_attributes": True}


class PolicyMutation(BaseModel):
    path: str
    old_value: Any
    new_value: Any
    reason: str


class LoopRunSummary(BaseModel):
    loop_name: str
    consumed_events: int = 0
    emitted_events: int = 0
    created_records: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
