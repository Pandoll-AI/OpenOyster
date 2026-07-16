"""Deterministic visitor: stage payload → (path, assertion, role-tagged anchors)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from openoyster.deliberation_contracts import (
    BeliefsStagePayload,
    CitationAnchor,
    CriticStagePayload,
    DecisionStagePayload,
    NarrativeAssertion,
    OptionsStagePayload,
    ScenariosStagePayload,
    StrictModel,
)

CITATION_ROLE_STATEMENT = "statement"
CITATION_ROLE_SUPPORTING = "supporting"
CITATION_ROLE_OPPOSING = "opposing"
CITATION_ROLES = frozenset(
    {CITATION_ROLE_STATEMENT, CITATION_ROLE_SUPPORTING, CITATION_ROLE_OPPOSING}
)


@dataclass(frozen=True, slots=True)
class RoleAnchor:
    """One citation to persist, tagged with DeliberationCitation.role."""

    anchor: CitationAnchor
    role: str


@dataclass(frozen=True, slots=True)
class AssertionVisit:
    """One NarrativeAssertion plus every anchor that must be stored under it."""

    path: str
    assertion: NarrativeAssertion
    anchors: tuple[RoleAnchor, ...]


def _statement_anchors(assertion: NarrativeAssertion) -> tuple[RoleAnchor, ...]:
    return tuple(
        RoleAnchor(anchor=anchor, role=CITATION_ROLE_STATEMENT)
        for anchor in assertion.anchors
    )


def _visit_assertion(path: str, assertion: NarrativeAssertion) -> AssertionVisit:
    return AssertionVisit(
        path=path,
        assertion=assertion,
        anchors=_statement_anchors(assertion),
    )


def iter_stage_assertions(model: StrictModel) -> Iterator[AssertionVisit]:
    """Yield every assertion the stage payload owns, in deterministic order.

    Path format matches the historical persist layout and extends it for
    previously omitted fields:
      beliefs.{key}.statement
      beliefs.{key}.assumptions[i]
      beliefs.{key}.gaps[i]
      options.{key}.label | .expected_outcome | .risks[i]
      options.{key}.exclusion_reason
      options.{key}.constraint_judgements[i].rationale
      scenarios.{key}.projected_outcome
      scenarios.{key}.items[i]   # facts + inferences + assumptions, stable concat order
      critic.findings[i]
      decision.rationale
      decision.flip_conditions.{key}

    Belief supporting_anchors / opposing_anchors attach to the statement visit
    as role-tagged anchors (not separate assertion rows).
    """
    if isinstance(model, BeliefsStagePayload):
        for belief in model.beliefs:
            statement_path = f"beliefs.{belief.local_key}.statement"
            anchors = (
                *_statement_anchors(belief.statement),
                *(
                    RoleAnchor(anchor=a, role=CITATION_ROLE_SUPPORTING)
                    for a in belief.supporting_anchors
                ),
                *(
                    RoleAnchor(anchor=a, role=CITATION_ROLE_OPPOSING)
                    for a in belief.opposing_anchors
                ),
            )
            yield AssertionVisit(
                path=statement_path,
                assertion=belief.statement,
                anchors=anchors,
            )
            for idx, item in enumerate(belief.assumptions):
                yield _visit_assertion(
                    f"beliefs.{belief.local_key}.assumptions[{idx}]", item
                )
            for idx, item in enumerate(belief.gaps):
                yield _visit_assertion(f"beliefs.{belief.local_key}.gaps[{idx}]", item)
        return

    if isinstance(model, OptionsStagePayload):
        for option in model.options:
            yield _visit_assertion(f"options.{option.local_key}.label", option.label)
            yield _visit_assertion(
                f"options.{option.local_key}.expected_outcome", option.expected_outcome
            )
            for risk_idx, risk in enumerate(option.risks):
                yield _visit_assertion(
                    f"options.{option.local_key}.risks[{risk_idx}]", risk
                )
            for cj_idx, judgement in enumerate(option.constraint_judgements):
                yield _visit_assertion(
                    f"options.{option.local_key}.constraint_judgements[{cj_idx}].rationale",
                    judgement.rationale,
                )
            if option.exclusion_reason is not None:
                yield _visit_assertion(
                    f"options.{option.local_key}.exclusion_reason",
                    option.exclusion_reason,
                )
        return

    if isinstance(model, ScenariosStagePayload):
        for scenario in model.scenarios:
            yield _visit_assertion(
                f"scenarios.{scenario.local_key}.projected_outcome",
                scenario.projected_outcome,
            )
            for idx, item in enumerate(
                scenario.facts + scenario.inferences + scenario.assumptions
            ):
                yield _visit_assertion(
                    f"scenarios.{scenario.local_key}.items[{idx}]", item
                )
        return

    if isinstance(model, CriticStagePayload):
        for idx, finding in enumerate(model.findings):
            yield _visit_assertion(f"critic.findings[{idx}]", finding)
        return

    if isinstance(model, DecisionStagePayload):
        yield _visit_assertion("decision.rationale", model.rationale)
        for flip in model.flip_conditions:
            yield _visit_assertion(
                f"decision.flip_conditions.{flip.local_key}", flip.condition
            )
        return

    raise TypeError(f"unsupported stage payload type: {type(model)!r}")


def count_stage_units(model: StrictModel) -> tuple[int, int]:
    """Return (assertion_count, citation_count) for parity checks after persist."""
    assertions = 0
    citations = 0
    for visit in iter_stage_assertions(model):
        assertions += 1
        citations += len(visit.anchors)
    return assertions, citations
