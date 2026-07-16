"""First-class deliberation charters (sustained concern grouping).

Epistemic boundary (HARD): charters are Mission control-plane grouping only.
Never inject title/description into stage prompts, retrieval, or Pack evidence.
Only mission_charter_id (integer) may travel with the Mission snapshot.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.models import DeliberationCharter, utcnow

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
CHARTER_STATUSES = frozenset({STATUS_ACTIVE, STATUS_ARCHIVED})

ERROR_UNKNOWN_CHARTER = "unknown_charter"
ERROR_CHARTER_ARCHIVED = "charter_archived"
ERROR_INVALID_TITLE = "charter_invalid_title"
ERROR_INVALID_STATUS = "charter_invalid_status"


class CharterError(Exception):
    """Stable charter validation / lifecycle error."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def create_charter(
    session: Session,
    *,
    title: str,
    description: str | None = None,
) -> DeliberationCharter:
    cleaned = (title or "").strip()
    if not cleaned:
        raise CharterError(ERROR_INVALID_TITLE, "title is required")
    row = DeliberationCharter(
        title=cleaned,
        description=description,
        status=STATUS_ACTIVE,
    )
    session.add(row)
    session.flush()
    return row


def list_charters(
    session: Session,
    *,
    status: str | None = None,
) -> list[DeliberationCharter]:
    stmt = select(DeliberationCharter).order_by(DeliberationCharter.id.asc())
    if status is not None:
        if status not in CHARTER_STATUSES:
            raise CharterError(ERROR_INVALID_STATUS, f"invalid status filter: {status!r}")
        stmt = stmt.where(DeliberationCharter.status == status)
    return list(session.scalars(stmt).all())


def get_charter(session: Session, charter_id: int) -> DeliberationCharter | None:
    return session.get(DeliberationCharter, charter_id)


def archive_charter(session: Session, charter_id: int) -> DeliberationCharter:
    row = session.get(DeliberationCharter, charter_id)
    if row is None:
        raise CharterError(ERROR_UNKNOWN_CHARTER, f"charter {charter_id} not found")
    if row.status != STATUS_ARCHIVED:
        row.status = STATUS_ARCHIVED
        row.updated_at = utcnow()
        session.flush()
    return row


def require_active_charter(session: Session, charter_id: int | None) -> None:
    """Validate mission.mission_charter_id before freeze.

    None is unconstrained (legacy / ungrouped missions). Non-null must exist
    and be active; archived ids fail with charter_archived.
    """
    if charter_id is None:
        return
    row = session.get(DeliberationCharter, int(charter_id))
    if row is None:
        raise CharterError(
            ERROR_UNKNOWN_CHARTER,
            f"charter {charter_id} does not exist",
        )
    if row.status != STATUS_ACTIVE:
        raise CharterError(
            ERROR_CHARTER_ARCHIVED,
            f"charter {charter_id} is archived",
        )


def require_charter_exists(session: Session, charter_id: int) -> DeliberationCharter:
    """Existence check for read-side filters (calibration etc.). Active not required."""
    row = session.get(DeliberationCharter, int(charter_id))
    if row is None:
        raise CharterError(
            ERROR_UNKNOWN_CHARTER,
            f"charter {charter_id} does not exist",
        )
    return row


def charter_public_payload(row: DeliberationCharter) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
