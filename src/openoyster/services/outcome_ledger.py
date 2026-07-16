"""Decision Outcome Ledger — append-only usage records + deterministic calibration.

Epistemic boundary (HARD): outcomes are usage records, not evidence.
This module must never feed outcomes into prompts, retrieval, or policies.
Calibration is a human-readable aggregate only — no LLM calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.models import DeliberationOutcome, DeliberationRun, utcnow

OUTCOME_LABELS = frozenset(
    {
        "adopted",
        "adopted_modified",
        "not_adopted",
        "reversed",
        "expired",
    }
)

SCENARIO_STATUSES = frozenset(
    {
        "materialized",
        "partially",
        "not_materialized",
        "unknown",
    }
)

ABSTENTION_ASSESSMENTS = frozenset(
    {
        "abstention_was_right",
        "information_arrived_late",
        "should_have_selected",
    }
)

DEFAULT_MIN_SAMPLE = 5

# Stable error codes for CLI/API
ERROR_RUN_NOT_FOUND = "outcome_run_not_found"
ERROR_RUN_NOT_COMPLETED = "outcome_run_not_completed"
ERROR_INVALID_LABEL = "outcome_invalid_label"
ERROR_INVALID_SCENARIO = "outcome_invalid_scenario_assessment"
ERROR_INVALID_ABSTENTION = "outcome_invalid_abstention_assessment"


class OutcomeLedgerError(Exception):
    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _normalize_scenario_assessments(
    raw: dict[str, Any] | list[Any] | None,
) -> dict[str, str]:
    """Accept mapping scenario_key -> status, or list of {key,status} / 'key=status'."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        out: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not key.strip():
                raise OutcomeLedgerError(
                    ERROR_INVALID_SCENARIO,
                    "scenario assessment keys must be non-empty strings",
                )
            if not isinstance(value, str) or value not in SCENARIO_STATUSES:
                raise OutcomeLedgerError(
                    ERROR_INVALID_SCENARIO,
                    f"invalid scenario status for {key!r}: {value!r}",
                )
            out[key.strip()] = value
        return out
    if isinstance(raw, list):
        out = {}
        for item in raw:
            if isinstance(item, str) and "=" in item:
                key, _, status = item.partition("=")
                key = key.strip()
                status = status.strip()
            elif isinstance(item, dict):
                key = str(item.get("key") or item.get("scenario") or "").strip()
                status = str(item.get("status") or item.get("assessment") or "").strip()
            else:
                raise OutcomeLedgerError(
                    ERROR_INVALID_SCENARIO,
                    f"unsupported scenario assessment item: {item!r}",
                )
            if not key or status not in SCENARIO_STATUSES:
                raise OutcomeLedgerError(
                    ERROR_INVALID_SCENARIO,
                    f"invalid scenario assessment: key={key!r} status={status!r}",
                )
            out[key] = status
        return out
    raise OutcomeLedgerError(
        ERROR_INVALID_SCENARIO,
        "scenario_assessments must be a mapping or list",
    )


def record_outcome(
    session: Session,
    run_id: int,
    *,
    outcome_label: str,
    scenario_assessments: dict[str, Any] | list[Any] | None = None,
    abstention_assessment: str | None = None,
    note: str | None = None,
    noted_by: str = "user",
    idempotency_key: str | None = None,
) -> DeliberationOutcome:
    """Append one outcome row for a completed run. Idempotent on key when set."""
    if idempotency_key is not None and idempotency_key.strip():
        key = idempotency_key.strip()
        existing = session.scalar(
            select(DeliberationOutcome).where(DeliberationOutcome.idempotency_key == key)
        )
        if existing is not None:
            return existing
    else:
        key = None

    run = session.get(DeliberationRun, run_id)
    if run is None:
        raise OutcomeLedgerError(ERROR_RUN_NOT_FOUND, f"run {run_id} not found")
    if run.status != "completed":
        raise OutcomeLedgerError(
            ERROR_RUN_NOT_COMPLETED,
            f"run {run_id} status is {run.status!r}, must be completed",
        )

    if outcome_label not in OUTCOME_LABELS:
        raise OutcomeLedgerError(
            ERROR_INVALID_LABEL,
            f"outcome_label must be one of {sorted(OUTCOME_LABELS)}",
        )

    assessments = _normalize_scenario_assessments(scenario_assessments)

    abstention: str | None = None
    if abstention_assessment is not None and abstention_assessment.strip():
        abstention = abstention_assessment.strip()
        if abstention not in ABSTENTION_ASSESSMENTS:
            raise OutcomeLedgerError(
                ERROR_INVALID_ABSTENTION,
                f"abstention_assessment must be one of {sorted(ABSTENTION_ASSESSMENTS)}",
            )

    row = DeliberationOutcome(
        run_id=run_id,
        outcome_label=outcome_label,
        scenario_assessments=assessments,
        abstention_assessment=abstention,
        note=note,
        noted_at=utcnow(),
        noted_by=(noted_by or "user").strip() or "user",
        idempotency_key=key,
    )
    session.add(row)
    session.flush()
    return row


def list_outcomes(session: Session, run_id: int) -> list[DeliberationOutcome]:
    return list(
        session.scalars(
            select(DeliberationOutcome)
            .where(DeliberationOutcome.run_id == run_id)
            .order_by(DeliberationOutcome.noted_at.asc(), DeliberationOutcome.id.asc())
        ).all()
    )


def outcome_public_payload(row: DeliberationOutcome) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "outcome_label": row.outcome_label,
        "scenario_assessments": dict(row.scenario_assessments or {}),
        "abstention_assessment": row.abstention_assessment,
        "note": row.note,
        "noted_at": row.noted_at.isoformat() if row.noted_at else None,
        "noted_by": row.noted_by,
    }


def _rate_or_insufficient(count: int, total: int, *, min_sample: int) -> Any:
    if total < min_sample:
        return f"insufficient_sample(n<{min_sample})"
    if total == 0:
        return f"insufficient_sample(n<{min_sample})"
    return round(count / total, 6)


def _mission_charter_id(run: DeliberationRun) -> int | None:
    snap = run.mission_snapshot_json
    if not isinstance(snap, dict):
        return None
    raw = snap.get("mission_charter_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _latest_outcome_by_run(
    rows: list[DeliberationOutcome],
) -> dict[int, DeliberationOutcome]:
    latest: dict[int, DeliberationOutcome] = {}
    for row in rows:
        prev = latest.get(row.run_id)
        if prev is None:
            latest[row.run_id] = row
            continue
        if (row.noted_at, row.id) >= (prev.noted_at, prev.id):
            latest[row.run_id] = row
    return latest


def _aggregate_slice(
    runs: list[DeliberationRun],
    latest: dict[int, DeliberationOutcome],
    all_outcomes: list[DeliberationOutcome],
    *,
    min_sample: int,
) -> dict[str, Any]:
    decision_runs = [r for r in runs if r.outcome == "select"]
    abstain_runs = [r for r in runs if r.outcome == "abstain"]

    decision_with_outcome = [r for r in decision_runs if r.id in latest]
    n_decision = len(decision_with_outcome)
    adopted_n = sum(
        1 for r in decision_with_outcome if latest[r.id].outcome_label == "adopted"
    )
    reversed_n = sum(
        1 for r in decision_with_outcome if latest[r.id].outcome_label == "reversed"
    )

    adverse_total = 0
    adverse_materialized = 0
    run_ids = {r.id for r in runs}
    for row in all_outcomes:
        if row.run_id not in run_ids:
            continue
        assessments = row.scenario_assessments or {}
        if not isinstance(assessments, dict):
            continue
        for key, status in assessments.items():
            key_l = str(key).casefold()
            if "adverse" not in key_l:
                continue
            adverse_total += 1
            if status == "materialized":
                adverse_materialized += 1

    abstain_with = [
        r
        for r in abstain_runs
        if r.id in latest and latest[r.id].abstention_assessment is not None
    ]
    n_abstain = len(abstain_with)
    right_n = sum(
        1
        for r in abstain_with
        if latest[r.id].abstention_assessment == "abstention_was_right"
    )

    return {
        "sample": {
            "completed_runs": len(runs),
            "decision_runs_with_outcome": n_decision,
            "abstain_runs_with_assessment": n_abstain,
            "adverse_scenario_assessments": adverse_total,
            "outcome_rows": sum(1 for o in all_outcomes if o.run_id in run_ids),
        },
        "adopted_rate": _rate_or_insufficient(adopted_n, n_decision, min_sample=min_sample),
        "reversed_rate": _rate_or_insufficient(reversed_n, n_decision, min_sample=min_sample),
        "adverse_materialized_rate": _rate_or_insufficient(
            adverse_materialized, adverse_total, min_sample=min_sample
        ),
        "abstention_was_right_rate": _rate_or_insufficient(
            right_n, n_abstain, min_sample=min_sample
        ),
    }


def calibration_report(
    session: Session,
    *,
    since: datetime | None = None,
    mission_charter_id: int | None = None,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> dict[str, Any]:
    """Deterministic frequency aggregates. Never calls an LLM."""
    # When filtering by charter, require the charter entity to exist so callers
    # get a stable error instead of a silent empty breakdown for typos/orphans.
    if mission_charter_id is not None:
        from openoyster.services.charters import require_charter_exists

        require_charter_exists(session, mission_charter_id)

    outcome_stmt = select(DeliberationOutcome).order_by(
        DeliberationOutcome.noted_at.asc(), DeliberationOutcome.id.asc()
    )
    if since is not None:
        outcome_stmt = outcome_stmt.where(DeliberationOutcome.noted_at >= since)
    outcomes = list(session.scalars(outcome_stmt).all())
    latest = _latest_outcome_by_run(outcomes)

    run_stmt = select(DeliberationRun).where(DeliberationRun.status == "completed")
    runs = list(session.scalars(run_stmt).all())
    # Only runs that appear in the filtered outcome set (or all completed when no outcomes filter).
    # Calibration is over completed runs that have ledger rows in scope; charter filter applies to runs.
    run_ids_with_outcomes = set(latest.keys())
    scoped_runs = [r for r in runs if r.id in run_ids_with_outcomes]
    if mission_charter_id is not None:
        scoped_runs = [
            r for r in scoped_runs if _mission_charter_id(r) == mission_charter_id
        ]

    overall = _aggregate_slice(scoped_runs, latest, outcomes, min_sample=min_sample)

    by_charter: dict[str, Any] = {}
    charter_ids: set[int | None] = set()
    for run in scoped_runs:
        charter_ids.add(_mission_charter_id(run))
    # Only emit breakdown when at least one charter id is present and filter not fixed.
    if mission_charter_id is None and any(cid is not None for cid in charter_ids):
        for cid in sorted(c for c in charter_ids if c is not None):
            subset = [r for r in scoped_runs if _mission_charter_id(r) == cid]
            by_charter[str(cid)] = _aggregate_slice(
                subset, latest, outcomes, min_sample=min_sample
            )
        none_subset = [r for r in scoped_runs if _mission_charter_id(r) is None]
        if none_subset:
            by_charter["null"] = _aggregate_slice(
                none_subset, latest, outcomes, min_sample=min_sample
            )

    return {
        "min_sample": min_sample,
        "since": since.isoformat() if since is not None else None,
        "mission_charter_id": mission_charter_id,
        "overall": overall,
        "by_mission_charter_id": by_charter,
    }
