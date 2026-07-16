"""Citation-scope Cognitive Impact for Autonomous Deliberation D1."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.deliberation_contracts import (
    AssertionClass,
    payload_digest,
)
from openoyster.models import (
    DeliberationArtifact,
    DeliberationAssertion,
    DeliberationCitation,
    DeliberationCognitiveImpact,
    DeliberationEvidenceSnapshot,
    DeliberationPackScope,
    DeliberationRun,
)

METHOD = "citation_scope_projection_v2"

# Belief statement support uses the union of statement + supporting + opposing
# citation install ids attached to that assertion row.
_BELIEF_STATEMENT_PATH_SUFFIX = ".statement"
_BELIEF_STATEMENT_PATH_PREFIX = "beliefs."


def _baseline_install_ids(session: Session, run_id: int) -> set[int]:
    rows = session.scalars(
        select(DeliberationPackScope).where(
            DeliberationPackScope.run_id == run_id,
            DeliberationPackScope.role == "impact_baseline",
        )
    ).all()
    return {row.pack_install_id for row in rows}


def _primary_install_ids(session: Session, run_id: int) -> set[int]:
    rows = session.scalars(
        select(DeliberationPackScope).where(
            DeliberationPackScope.run_id == run_id,
            DeliberationPackScope.role == "primary",
        )
    ).all()
    return {row.pack_install_id for row in rows}


def _snapshot_install_map(session: Session, run_id: int) -> dict[int, int]:
    rows = session.scalars(
        select(DeliberationEvidenceSnapshot).where(
            DeliberationEvidenceSnapshot.run_id == run_id
        )
    ).all()
    return {row.id: row.pack_install_id for row in rows}


def _classify_support(
    cited_install_ids: set[int], baseline_install_ids: set[int]
) -> str:
    if not cited_install_ids:
        # No citation → cannot project onto baseline.
        return "unsupported"
    if cited_install_ids.issubset(baseline_install_ids) and cited_install_ids:
        return "retained"
    if cited_install_ids & baseline_install_ids:
        return "partially_supported"
    return "unsupported"


def _aggregate_decision_support(supports: list[str]) -> str:
    if not supports:
        return "retained"
    if all(item == "retained" for item in supports):
        return "retained"
    if all(item == "unsupported" for item in supports):
        return "lost"
    return "weakened"


def _is_belief_statement_path(path: str) -> bool:
    return path.startswith(_BELIEF_STATEMENT_PATH_PREFIX) and path.endswith(
        _BELIEF_STATEMENT_PATH_SUFFIX
    )


def build_cognitive_impact_payload(session: Session, run: DeliberationRun) -> dict[str, Any]:
    """Pure (read-only) projection of grounded citations onto the impact baseline.

    Session is used only for SELECT; callers persist the result separately.
    """
    baseline = _baseline_install_ids(session, run.id)
    primary = _primary_install_ids(session, run.id)
    snap_installs = _snapshot_install_map(session, run.id)

    assertions = session.scalars(
        select(DeliberationAssertion)
        .join(DeliberationArtifact, DeliberationAssertion.artifact_id == DeliberationArtifact.id)
        .where(DeliberationArtifact.run_id == run.id)
        .order_by(DeliberationAssertion.id)
    ).all()

    grounded_rows: list[dict[str, Any]] = []
    supports: list[str] = []
    for assertion in assertions:
        if assertion.classification not in {
            AssertionClass.grounded_fact.value,
            AssertionClass.grounded_inference.value,
        }:
            continue
        citations = session.scalars(
            select(DeliberationCitation).where(
                DeliberationCitation.assertion_id == assertion.id
            )
        ).all()
        # Belief statements: union of statement + supporting + opposing installs.
        # Other grounded assertions: all attached citations (role defaults statement).
        if _is_belief_statement_path(assertion.path):
            relevant = [
                c
                for c in citations
                if (c.role or "statement") in {"statement", "supporting", "opposing"}
            ]
        else:
            relevant = list(citations)
        cited = {
            snap_installs[c.evidence_snapshot_id]
            for c in relevant
            if c.evidence_snapshot_id in snap_installs
        }
        support = _classify_support(cited, baseline)
        supports.append(support)
        grounded_rows.append(
            {
                "assertion_id": assertion.id,
                "path": assertion.path,
                "text": assertion.text,
                "classification": assertion.classification,
                "support": support,
                "cited_pack_install_ids": sorted(cited),
                "baseline_pack_install_ids": sorted(baseline),
            }
        )

    # Conservatively aggregates support across every grounded assertion that
    # contributes to the stored deliberation, not only the final rationale.
    decision_supports = list(supports)

    decision_support = _aggregate_decision_support(decision_supports)
    primary_only = sorted(primary - baseline)

    return {
        "method": METHOD,
        "decision_support": decision_support,
        "grounded_assertions": grounded_rows,
        "primary_only_pack_install_ids": primary_only,
        "baseline_pack_install_ids": sorted(baseline),
        "primary_pack_install_ids": sorted(primary),
        "limitation": (
            "citation_scope_projection_v2 measures citation dependence across every "
            "persisted grounded assertion (including exclusion_reason and constraint "
            "rationale); belief-statement support is the union of statement + "
            "supporting + opposing citation install ids. It does not diff Pack "
            "records or discover baseline-only inferences"
        ),
    }


def compute_cognitive_impact(session: Session, run: DeliberationRun) -> DeliberationCognitiveImpact:
    """Project grounded citations onto the frozen impact baseline scope.

    v2 aggregates every grounded assertion the visitor persisted (including
    exclusion_reason / constraint rationale) and, for belief statements, uses
    the union of statement + supporting + opposing citation install ids.
    This measures citation dependence only — it does not diff Pack records
    and does not discover inferences that a baseline-only re-run might produce.
    """
    existing = session.scalar(
        select(DeliberationCognitiveImpact).where(DeliberationCognitiveImpact.run_id == run.id)
    )
    if existing is not None:
        return existing

    impact_json = build_cognitive_impact_payload(session, run)
    row = DeliberationCognitiveImpact(
        run_id=run.id,
        method=METHOD,
        impact_json=impact_json,
        impact_digest=payload_digest(impact_json),
    )
    session.add(row)
    session.flush()
    return row
