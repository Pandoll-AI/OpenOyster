from __future__ import annotations

from pydantic import BaseModel, Field


class GoldDocument(BaseModel):
    id: str
    title: str
    url: str
    source: str
    language: str
    kind: str
    collected_at: str
    text: str


class GoldEntityLabel(BaseModel):
    name: str
    kind: str
    salience: str


class GoldSignalLabel(BaseModel):
    signal_type: str
    summary: str
    anchor_quote: str
    anchor_verified: bool | None = None


class GoldLabel(BaseModel):
    doc_id: str
    language: str
    labeler_model: str | None = None
    labeled_at: str | None = None
    review_status: str = "unreviewed"
    expected_entities: list[GoldEntityLabel] = Field(default_factory=list)
    expected_signals: list[GoldSignalLabel] = Field(default_factory=list)
    notes: str = ""


class MetricBlock(BaseModel):
    docs: int = 0
    entity_recall_core: float = 0.0
    entity_precision: float = 0.0
    signal_type_f1: float = 0.0
    quote_existence_rate: float = 0.0
    core_entities_matched: int = 0
    core_entities_total: int = 0
    predicted_entities_matched: int = 0
    predicted_entities_total: int = 0
    signal_type_tp: int = 0
    signal_type_fp: int = 0
    signal_type_fn: int = 0
    quotes_verified: int = 0
    quotes_total: int = 0


class GoldDocDetail(BaseModel):
    doc_id: str
    language: str
    title: str
    missing_core_entities: list[str] = Field(default_factory=list)
    missing_signal_types: list[str] = Field(default_factory=list)
    extra_signal_types: list[str] = Field(default_factory=list)
    fabricated_quotes: list[str] = Field(default_factory=list)
    core_entities_matched: int = 0
    core_entities_total: int = 0
    predicted_entities_matched: int = 0
    predicted_entities_total: int = 0
    signal_type_tp: int = 0
    signal_type_fp: int = 0
    signal_type_fn: int = 0
    quotes_verified: int = 0
    quotes_total: int = 0


class SkippedDocument(BaseModel):
    doc_id: str
    reason: str


class GoldEvalReport(BaseModel):
    kind: str = "goldset"
    provider: str
    model: str | None = None
    docs_seen: int
    docs_evaluated: int
    skipped_documents: list[SkippedDocument] = Field(default_factory=list)
    metrics: dict[str, MetricBlock]
    per_doc: list[GoldDocDetail] = Field(default_factory=list)
    review_status_counts: dict[str, int] = Field(default_factory=dict)
    labeler_model_counts: dict[str, int] = Field(default_factory=dict)
    label_notice: str = "라벨은 LLM-judge 초벌, 사람 미검수"


class CounterAuditDetail(BaseModel):
    evidence_edge_id: int
    hypothesis_id: int
    contradicts: bool
    reasoning: str
    quoted_evidence: str


class CounterEvalReport(BaseModel):
    kind: str = "counter_evidence"
    provider: str
    model: str | None = None
    cycles: int
    docs_ingested: int
    oppose_edges: int
    audited_edges: int
    precision: float | None = None
    measurable: bool
    status: str
    audit_model_note: str
    audits: list[CounterAuditDetail] = Field(default_factory=list)
