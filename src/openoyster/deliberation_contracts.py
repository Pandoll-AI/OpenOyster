"""Strict typed contracts for Autonomous Deliberation D1."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openoyster.utils import sha256_text

CONTRACT_VERSION: Final = "deliberation-d1-v1"
PROMPT_TEMPLATE_VERSION: Final = "deliberation-prompts-d1-v8"

MAX_BELIEFS: Final = 20
MAX_OPTIONS: Final = 5
MAX_SCENARIOS_PER_OPTION: Final = 3
MAX_EVIDENCE_SNAPSHOTS: Final = 24
MAX_PROMPT_CHARS: Final = 100_000
MAX_LLM_ATTEMPTS: Final = 10
MIN_QUOTE_CHARS: Final = 12
MAX_RETRIEVAL_EXPANSION_QUERIES: Final = 5
MAX_RETRIEVAL_EXPANSION_QUERY_CHARS: Final = 200

STAGE_BELIEFS: Final = "deliberation_beliefs"
STAGE_OPTIONS: Final = "deliberation_options"
STAGE_SCENARIOS: Final = "deliberation_scenarios"
STAGE_CRITIC: Final = "deliberation_critic"
STAGE_DECISION: Final = "deliberation_decision"
STAGE_RETRIEVAL_QUERY_EXPANSION: Final = "retrieval_query_expansion"

DELIBERATION_STAGES: Final[tuple[str, ...]] = (
    STAGE_BELIEFS,
    STAGE_OPTIONS,
    STAGE_SCENARIOS,
    STAGE_CRITIC,
    STAGE_DECISION,
)

ARTIFACT_KINDS: Final[frozenset[str]] = frozenset(
    {
        "beliefs",
        "options",
        "scenarios",
        "critic_result",
        "decision",
        "flip_conditions",
        "knowledge_requests",
        "cognitive_transition",
        "retrieval_trace",
    }
)

BeliefStatus = Literal["supported", "contested", "unknown", "invalidated"]
ScenarioKind = Literal["expected", "adverse"]
CriticVerdict = Literal["pass", "revise", "abstain"]
DecisionOutcome = Literal["select", "abstain"]
PackScopeRole = Literal["primary", "impact_baseline"]
ImpactSupport = Literal["retained", "partially_supported", "unsupported"]
DecisionSupport = Literal["retained", "weakened", "lost"]
RunStatus = Literal[
    "created",
    "scope_frozen",
    "context_ready",
    "beliefs_ready",
    "options_ready",
    "scenarios_ready",
    "critic_ready",
    "decision_ready",
    "impact_ready",
    "completed",
    "failed_input",
    "failed_execution",
    "failed_database",
    "indeterminate",
]

NORMAL_STATUSES: Final[tuple[str, ...]] = (
    "created",
    "scope_frozen",
    "context_ready",
    "beliefs_ready",
    "options_ready",
    "scenarios_ready",
    "critic_ready",
    "decision_ready",
    "impact_ready",
    "completed",
)

TERMINAL_FAILURE_STATUSES: Final[frozenset[str]] = frozenset(
    {"failed_input", "failed_execution", "failed_database", "indeterminate"}
)

ABSTENTION_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "no_evidence",
        "insufficient_viable_options",
        "critic_non_pass",
        "missing_scenarios",
        "hard_constraint_violation",
        "invalid_stage_payload",
        "selection_gate_failed",
        "unknown_citation",
        "scope_error",
        "unresolved_critical_gap",
    }
)

CRITIC_ISSUE_CODES: Final[frozenset[str]] = frozenset(
    {
        "missing_option",
        "evidence_bias",
        "missing_opposing_evidence",
        "constraint_misread",
        "out_of_pack_fact",
        "overclaim",
        "ungrounded_outcome",
        "coverage_ok",
        "insufficient_viable_options",
        "other_structural",
    }
)


class StrictModel(BaseModel):
    """Closed contracts: unknown fields are rejected; typed fields are validated.

    Model-level ``strict=True`` is intentionally omitted so JSON string enums
    (``AssertionClass``, stage literals) coerce as ordinary API/LLM payloads do.
    Structural strictness is enforced via ``extra="forbid"`` and validators.
    """

    model_config = ConfigDict(extra="forbid")


class AssertionClass(StrEnum):
    grounded_fact = "grounded_fact"
    grounded_inference = "grounded_inference"
    mission_control = "mission_control"
    proposal = "proposal"
    assumption = "assumption"
    gap = "gap"
    structural = "structural"


class Mission(StrictModel):
    goal: str = Field(min_length=1)
    decision_question: str = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    deadline: str | None = None
    context: str | None = None
    mission_charter_id: int | None = None

    @field_validator("constraints", "preferences", mode="before")
    @classmethod
    def _default_list(cls, value: Any) -> Any:
        return [] if value is None else value


class CitationAnchor(StrictModel):
    evidence_snapshot_id: str = Field(min_length=1)
    quote: str | None = None
    json_pointer: str | None = None
    value_digest: str | None = None

    @model_validator(mode="after")
    def _require_quote_or_pointer(self) -> Self:
        has_quote = self.quote is not None and self.quote != ""
        has_pointer = self.json_pointer is not None and self.json_pointer != ""
        if has_quote == has_pointer:
            # Exactly one of quote or json_pointer must be present and non-empty.
            if not has_quote and not has_pointer:
                raise ValueError("citation anchor requires quote or json_pointer")
            raise ValueError("citation anchor must provide exactly one of quote or json_pointer")
        if has_pointer and (self.value_digest is None or len(self.value_digest) != 64):
            raise ValueError("json_pointer anchors require a 64-char value_digest")
        if has_quote and self.value_digest is not None:
            raise ValueError("quote anchors must not set value_digest")
        return self


class NarrativeAssertion(StrictModel):
    text: str = Field(min_length=1)
    classification: AssertionClass
    anchors: list[CitationAnchor] = Field(default_factory=list)
    mission_pointer: str | None = None
    artifact_ref: str | None = None
    assumption_marker: bool | None = None
    verification_question: str | None = None
    unresolved_question: str | None = None
    issue_code: str | None = None

    @model_validator(mode="after")
    def _classify_support(self) -> Self:
        cls = self.classification
        if cls in {AssertionClass.grounded_fact, AssertionClass.grounded_inference}:
            if not self.anchors:
                raise ValueError(f"{cls} requires at least one evidence anchor")
        elif cls is AssertionClass.mission_control:
            if not self.mission_pointer:
                raise ValueError("mission_control requires mission_pointer")
        elif cls is AssertionClass.proposal:
            if not self.mission_pointer and not self.artifact_ref:
                raise ValueError("proposal requires mission_pointer or artifact_ref")
        elif cls is AssertionClass.assumption:
            if not self.assumption_marker:
                raise ValueError("assumption requires assumption_marker=true")
            if not self.verification_question:
                raise ValueError("assumption requires verification_question")
        elif cls is AssertionClass.gap:
            if not self.unresolved_question:
                raise ValueError("gap requires unresolved_question")
        elif cls is AssertionClass.structural:
            if not self.issue_code:
                raise ValueError("structural requires issue_code")
            if not self.artifact_ref:
                raise ValueError("structural requires artifact_ref")
        return self


class Belief(StrictModel):
    local_key: str = Field(min_length=1)
    statement: NarrativeAssertion
    status: BeliefStatus
    supporting_anchors: list[CitationAnchor] = Field(default_factory=list)
    opposing_anchors: list[CitationAnchor] = Field(default_factory=list)
    assumptions: list[NarrativeAssertion] = Field(default_factory=list)
    gaps: list[NarrativeAssertion] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)


class BeliefsStagePayload(StrictModel):
    beliefs: list[Belief] = Field(default_factory=list, max_length=MAX_BELIEFS)

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        keys = [item.local_key for item in self.beliefs]
        if len(keys) != len(set(keys)):
            raise ValueError("belief local_key values must be unique")
        return self


class ConstraintJudgement(StrictModel):
    constraint_index: int = Field(ge=0)
    satisfied: bool
    rationale: NarrativeAssertion


class Option(StrictModel):
    local_key: str = Field(min_length=1)
    label: NarrativeAssertion
    viable: bool
    constraint_judgements: list[ConstraintJudgement] = Field(default_factory=list)
    supporting_belief_keys: list[str] = Field(default_factory=list)
    opposing_belief_keys: list[str] = Field(default_factory=list)
    risks: list[NarrativeAssertion] = Field(default_factory=list)
    reversibility: str = Field(min_length=1)
    expected_outcome: NarrativeAssertion
    exclusion_reason: NarrativeAssertion | None = None


class OptionsStagePayload(StrictModel):
    options: list[Option] = Field(default_factory=list, max_length=MAX_OPTIONS)

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        keys = [item.local_key for item in self.options]
        if len(keys) != len(set(keys)):
            raise ValueError("option local_key values must be unique")
        return self


class Scenario(StrictModel):
    local_key: str = Field(min_length=1)
    option_key: str = Field(min_length=1)
    kind: ScenarioKind
    projected_outcome: NarrativeAssertion
    facts: list[NarrativeAssertion] = Field(default_factory=list)
    inferences: list[NarrativeAssertion] = Field(default_factory=list)
    assumptions: list[NarrativeAssertion] = Field(default_factory=list)


class ScenariosStagePayload(StrictModel):
    scenarios: list[Scenario] = Field(default_factory=list)

    @model_validator(mode="after")
    def _limits_and_unique(self) -> Self:
        keys = [item.local_key for item in self.scenarios]
        if len(keys) != len(set(keys)):
            raise ValueError("scenario local_key values must be unique")
        counts: dict[str, int] = {}
        for item in self.scenarios:
            counts[item.option_key] = counts.get(item.option_key, 0) + 1
            if counts[item.option_key] > MAX_SCENARIOS_PER_OPTION:
                raise ValueError(
                    f"option {item.option_key} exceeds {MAX_SCENARIOS_PER_OPTION} scenarios"
                )
        return self


class CriticIssue(StrictModel):
    code: str = Field(min_length=1)
    artifact_ref: str = Field(min_length=1)
    detail: str | None = None

    @field_validator("code")
    @classmethod
    def _closed_code(cls, value: str) -> str:
        if value not in CRITIC_ISSUE_CODES:
            raise ValueError(f"unknown critic issue code: {value}")
        return value


class CriticStagePayload(StrictModel):
    verdict: CriticVerdict
    issues: list[CriticIssue] = Field(default_factory=list)
    findings: list[NarrativeAssertion] = Field(default_factory=list)


class FlipCondition(StrictModel):
    local_key: str = Field(min_length=1)
    condition: NarrativeAssertion


class KnowledgeRequest(StrictModel):
    local_key: str = Field(min_length=1)
    question: str = Field(min_length=1)
    gap_ref: str = Field(min_length=1)
    priority: Literal["critical", "important", "nice_to_have"] = "critical"
    retrieval_status: (
        Literal["no_match_in_pack_evidence", "pack_has_no_evidence"] | None
    ) = None


class DecisionStagePayload(StrictModel):
    outcome: DecisionOutcome
    selected_option_key: str | None = None
    rationale: NarrativeAssertion
    abstention_reasons: list[str] = Field(default_factory=list)
    flip_conditions: list[FlipCondition] = Field(min_length=1)
    knowledge_requests: list[KnowledgeRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def _outcome_consistency(self) -> Self:
        if self.outcome == "select":
            if not self.selected_option_key:
                raise ValueError("select outcome requires selected_option_key")
        elif self.selected_option_key is not None:
            raise ValueError("abstain outcome must not set selected_option_key")
        if self.outcome == "abstain" and not self.abstention_reasons:
            raise ValueError("abstain requires at least one abstention reason code")
        for reason in self.abstention_reasons:
            if reason not in ABSTENTION_REASON_CODES:
                raise ValueError(f"unknown abstention reason: {reason}")
        flip_keys = [item.local_key for item in self.flip_conditions]
        if len(flip_keys) != len(set(flip_keys)):
            raise ValueError("flip_condition local_key values must be unique")
        kr_keys = [item.local_key for item in self.knowledge_requests]
        if len(kr_keys) != len(set(kr_keys)):
            raise ValueError("knowledge_request local_key values must be unique")
        return self


class RetrievalQueryExpansionPayload(StrictModel):
    """LLM-generated alternative lexical queries (search terms only)."""

    queries: list[str] = Field(default_factory=list, max_length=MAX_RETRIEVAL_EXPANSION_QUERIES)

    @field_validator("queries")
    @classmethod
    def _query_bounds(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_RETRIEVAL_EXPANSION_QUERIES:
            raise ValueError(
                f"at most {MAX_RETRIEVAL_EXPANSION_QUERIES} expansion queries allowed"
            )
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("expansion queries must be strings")
            text = item.strip()
            if not text:
                continue
            if len(text) > MAX_RETRIEVAL_EXPANSION_QUERY_CHARS:
                raise ValueError(
                    f"expansion query exceeds {MAX_RETRIEVAL_EXPANSION_QUERY_CHARS} chars"
                )
            cleaned.append(text)
        return cleaned


STAGE_PAYLOAD_TYPES: Final[dict[str, type[StrictModel]]] = {
    STAGE_BELIEFS: BeliefsStagePayload,
    STAGE_OPTIONS: OptionsStagePayload,
    STAGE_SCENARIOS: ScenariosStagePayload,
    STAGE_CRITIC: CriticStagePayload,
    STAGE_DECISION: DecisionStagePayload,
}


def parse_retrieval_query_expansion(payload: Any) -> RetrievalQueryExpansionPayload:
    """Validate expansion response; raises ValueError on schema violations."""
    if not isinstance(payload, dict):
        raise ValueError("retrieval_query_expansion response must be a JSON object")
    return RetrievalQueryExpansionPayload.model_validate(payload)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def mission_digest(mission: Mission) -> str:
    return sha256_text(canonical_json(mission.model_dump(mode="json")))


def payload_digest(payload: Any) -> str:
    data = payload.model_dump(mode="json") if isinstance(payload, StrictModel) else payload
    return sha256_text(canonical_json(data))


def validate_stage_payload(stage: str, payload: dict[str, Any]) -> StrictModel:
    model_type = STAGE_PAYLOAD_TYPES.get(stage)
    if model_type is None:
        raise ValueError(f"unknown deliberation stage: {stage}")
    return model_type.model_validate(payload)
