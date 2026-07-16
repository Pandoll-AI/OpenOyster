"""Flip Condition Monitoring D3 — deterministic Pack-evidence watch scans.

Creates watches for completed decisions with structured flip predicates,
scans new Pack installs via lexical matching, and records candidate triggers.
Never re-runs deliberation; never calls an LLM in this module.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openoyster.deliberation_contracts import (
    MAX_ACTIVE_FLIP_WATCHES,
    FlipPredicate,
)
from openoyster.events import bus
from openoyster.models import (
    DeliberationArtifact,
    DeliberationFlipTrigger,
    DeliberationFlipWatch,
    DeliberationRun,
    PackEvidence,
    PackInstall,
    utcnow,
)

logger = logging.getLogger(__name__)

WATCH_STATUS_WATCHING = "watching"
WATCH_STATUS_TRIGGERED_CANDIDATE = "triggered_candidate"
WATCH_STATUS_CONFIRMED = "confirmed"
WATCH_STATUS_DISMISSED = "dismissed"
WATCH_STATUS_EXPIRED = "expired"

WATCH_STATUSES = frozenset(
    {
        WATCH_STATUS_WATCHING,
        WATCH_STATUS_TRIGGERED_CANDIDATE,
        WATCH_STATUS_CONFIRMED,
        WATCH_STATUS_DISMISSED,
        WATCH_STATUS_EXPIRED,
    }
)

# dismiss is only legal from these statuses (confirmed must not be overwritten).
DISMISSABLE_STATUSES = frozenset(
    {
        WATCH_STATUS_WATCHING,
        WATCH_STATUS_TRIGGERED_CANDIDATE,
    }
)

EVENT_FLIP_TRIGGER_CANDIDATE = "flip_trigger_candidate"
EVENT_FLIP_WATCH_DISMISSED = "flip_watch_dismissed"
EVENT_FLIP_WATCHES_EXPIRED = "flip_watches_expired"
EVENT_FLIP_SCAN_FAILED = "flip_scan_failed"
EVENT_FLIP_SCAN_BOUNDED = "flip_scan_bounded"

# Bounded install-time scan defaults (DoS / install latency guard).
DEFAULT_SCAN_MAX_EVIDENCE_ROWS = 2000
DEFAULT_SCAN_MAX_EVIDENCE_CHARS = 2_000_000

_WS_RE = re.compile(r"\s+")


class FlipWatchError(Exception):
    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _predicate_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    raw = item.get("predicate")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return FlipPredicate.model_validate(raw).model_dump(mode="json")
    except Exception:
        return None


def create_watches_for_completed_run(session: Session, run: DeliberationRun) -> list[DeliberationFlipWatch]:
    """Create watching rows for flip conditions that declare a valid predicate.

    Idempotent on (run_id, flip_local_key). No-op when the run is not completed
    or when no predicates are present (legacy dossier-only flips).
    """
    if run.status != "completed":
        return []

    artifact = session.scalar(
        select(DeliberationArtifact).where(
            DeliberationArtifact.run_id == run.id,
            DeliberationArtifact.kind == "flip_conditions",
        )
    )
    if artifact is None or not isinstance(artifact.payload_json, dict):
        return []
    items = artifact.payload_json.get("flip_conditions")
    if not isinstance(items, list):
        return []

    created: list[DeliberationFlipWatch] = []
    now = utcnow()
    for item in items:
        if not isinstance(item, dict):
            continue
        local_key = item.get("local_key")
        if not isinstance(local_key, str) or not local_key.strip():
            continue
        predicate = _predicate_from_item(item)
        if predicate is None:
            continue
        existing = session.scalar(
            select(DeliberationFlipWatch).where(
                DeliberationFlipWatch.run_id == run.id,
                DeliberationFlipWatch.flip_local_key == local_key,
            )
        )
        if existing is not None:
            continue
        watch = DeliberationFlipWatch(
            run_id=run.id,
            flip_local_key=local_key,
            predicate_json=predicate,
            status=WATCH_STATUS_WATCHING,
            created_at=now,
            updated_at=now,
        )
        session.add(watch)
        created.append(watch)

    if created:
        session.flush()
        expire_excess_watches(session)
    return created


def expire_excess_watches(session: Session, *, limit: int = MAX_ACTIVE_FLIP_WATCHES) -> int:
    """Expire oldest watching rows when the active watching count exceeds ``limit``."""
    watching = list(
        session.scalars(
            select(DeliberationFlipWatch)
            .where(DeliberationFlipWatch.status == WATCH_STATUS_WATCHING)
            .order_by(DeliberationFlipWatch.created_at.asc(), DeliberationFlipWatch.id.asc())
        ).all()
    )
    excess = len(watching) - limit
    if excess <= 0:
        return 0
    now = utcnow()
    expired_ids: list[int] = []
    for watch in watching[:excess]:
        watch.status = WATCH_STATUS_EXPIRED
        watch.updated_at = now
        expired_ids.append(watch.id)
    session.flush()
    if expired_ids:
        logger.warning(
            "flip watch limit exceeded: expired %s oldest watching rows (limit=%s)",
            len(expired_ids),
            limit,
        )
        bus.emit(
            session,
            EVENT_FLIP_WATCHES_EXPIRED,
            {
                "expired_watch_ids": expired_ids,
                "limit": limit,
                "remaining_watching": limit,
            },
            source_loop="flip_monitoring",
            idempotency_key=f"flip-watches-expired:{expired_ids[0]}:{expired_ids[-1]}:{len(expired_ids)}",
        )
    return len(expired_ids)


def _normalize_phrase_surface(text: str) -> str:
    """Casefold + collapse whitespace for deterministic full-phrase matching."""
    return _WS_RE.sub(" ", text.casefold()).strip()


def _phrase_in_body(phrase: str, body: str) -> bool:
    """True iff the full phrase matches as a normalised substring of body text."""
    norm_phrase = _normalize_phrase_surface(phrase)
    if not norm_phrase:
        return False
    return norm_phrase in _normalize_phrase_surface(body)


def _match_predicate_against_evidence(
    predicate: dict[str, Any],
    evidence_rows: list[PackEvidence],
) -> list[str]:
    """Match query_terms against evidence *body text only*.

    Semantics: OR of full-phrase matches (not OR of individual tokens).
    A multi-word query_term must appear as a contiguous normalised phrase in
    ``PackEvidence.text``. Source/location provenance metadata is never searched.
    """
    terms = predicate.get("query_terms")
    if not isinstance(terms, list) or not terms:
        return []
    matched: list[str] = []
    seen: set[str] = set()
    for row in evidence_rows:
        body = row.text or ""
        if not body.strip():
            continue
        hit = False
        for term in terms:
            if not isinstance(term, str) or not term.strip():
                continue
            if _phrase_in_body(term, body):
                hit = True
                break
        if hit:
            evidence_id = row.global_evidence_id or row.local_evidence_id
            if evidence_id not in seen:
                seen.add(evidence_id)
                matched.append(evidence_id)
    return matched


def _load_evidence_bounded(
    session: Session,
    pack_install_id: int,
    *,
    max_evidence_rows: int,
    max_evidence_chars: int,
) -> tuple[list[PackEvidence], bool]:
    """Load evidence rows with hard row/char caps; return (rows, truncated)."""
    rows = list(
        session.scalars(
            select(PackEvidence)
            .where(PackEvidence.pack_install_id == pack_install_id)
            .order_by(PackEvidence.id.asc())
        ).all()
    )
    if not rows:
        return [], False

    bounded: list[PackEvidence] = []
    char_total = 0
    truncated = False
    for row in rows:
        if len(bounded) >= max_evidence_rows:
            truncated = True
            break
        text_len = len(row.text or "")
        if char_total + text_len > max_evidence_chars and bounded:
            truncated = True
            break
        if char_total + text_len > max_evidence_chars and not bounded:
            # Single oversized first row: still include it so tiny installs work,
            # but mark truncated so callers know further rows were skipped.
            bounded.append(row)
            char_total += text_len
            if len(rows) > 1:
                truncated = True
            break
        bounded.append(row)
        char_total += text_len
    if len(bounded) < len(rows):
        truncated = True
    return bounded, truncated


def scan_pack_install(
    session: Session,
    pack_install_id: int,
    *,
    max_evidence_rows: int = DEFAULT_SCAN_MAX_EVIDENCE_ROWS,
    max_evidence_chars: int = DEFAULT_SCAN_MAX_EVIDENCE_CHARS,
) -> list[DeliberationFlipTrigger]:
    """Scan all watching predicates against evidence from one Pack install.

    On match: atomically claim the watch (watching → triggered_candidate),
    append a trigger row (unique on watch+install; IntegrityError is idempotent),
    and emit ``flip_trigger_candidate``. LLM confirmation is out of this scope.

    Evidence scan is hard-bounded by row count and total text chars so install-time
    scans cannot unbounded-walk large packs.
    """
    install = session.get(PackInstall, pack_install_id)
    if install is None:
        raise FlipWatchError("pack_install_not_found", f"pack_install_id={pack_install_id}")

    expire_excess_watches(session)

    evidence_rows, truncated = _load_evidence_bounded(
        session,
        pack_install_id,
        max_evidence_rows=max_evidence_rows,
        max_evidence_chars=max_evidence_chars,
    )
    if truncated:
        logger.warning(
            "flip scan bounded: pack_install_id=%s max_rows=%s max_chars=%s scanned_rows=%s",
            pack_install_id,
            max_evidence_rows,
            max_evidence_chars,
            len(evidence_rows),
        )
        bus.emit(
            session,
            EVENT_FLIP_SCAN_BOUNDED,
            {
                "pack_install_id": pack_install_id,
                "max_evidence_rows": max_evidence_rows,
                "max_evidence_chars": max_evidence_chars,
                "scanned_rows": len(evidence_rows),
            },
            source_loop="flip_monitoring",
            idempotency_key=f"flip-scan-bounded:{pack_install_id}:{max_evidence_rows}:{max_evidence_chars}",
        )

    watches = list(
        session.scalars(
            select(DeliberationFlipWatch)
            .where(DeliberationFlipWatch.status == WATCH_STATUS_WATCHING)
            .order_by(DeliberationFlipWatch.id.asc())
        ).all()
    )
    if not watches or not evidence_rows:
        return []

    created: list[DeliberationFlipTrigger] = []
    now = utcnow()
    for watch in watches:
        matched_ids = _match_predicate_against_evidence(watch.predicate_json or {}, evidence_rows)
        if not matched_ids:
            continue

        # Atomic claim: only one scanner may move watching → triggered_candidate.
        claim = session.execute(
            update(DeliberationFlipWatch)
            .where(
                DeliberationFlipWatch.id == watch.id,
                DeliberationFlipWatch.status == WATCH_STATUS_WATCHING,
            )
            .values(status=WATCH_STATUS_TRIGGERED_CANDIDATE, updated_at=now)
        )
        if int(getattr(claim, "rowcount", 0) or 0) != 1:
            # Lost race or already not watching — do not re-trigger.
            continue

        trigger: DeliberationFlipTrigger | None = None
        try:
            with session.begin_nested():
                trigger = DeliberationFlipTrigger(
                    watch_id=watch.id,
                    pack_install_id=pack_install_id,
                    matched_evidence_ids=matched_ids,
                    created_at=now,
                )
                session.add(trigger)
                session.flush()
        except IntegrityError:
            # Concurrent insert for same (watch_id, pack_install_id): idempotent success.
            existing = session.scalar(
                select(DeliberationFlipTrigger).where(
                    DeliberationFlipTrigger.watch_id == watch.id,
                    DeliberationFlipTrigger.pack_install_id == pack_install_id,
                )
            )
            if existing is not None:
                session.refresh(watch)
                continue
            raise

        session.refresh(watch)
        bus.emit(
            session,
            EVENT_FLIP_TRIGGER_CANDIDATE,
            {
                "watch_id": watch.id,
                "run_id": watch.run_id,
                "flip_local_key": watch.flip_local_key,
                "pack_install_id": pack_install_id,
                "trigger_id": trigger.id,
                "matched_evidence_ids": matched_ids,
            },
            source_loop="flip_monitoring",
            idempotency_key=f"flip-trigger-candidate:{watch.id}:{pack_install_id}",
        )
        created.append(trigger)
    return created


def safe_scan_pack_install(
    session: Session,
    pack_install_id: int,
    *,
    max_evidence_rows: int = DEFAULT_SCAN_MAX_EVIDENCE_ROWS,
    max_evidence_chars: int = DEFAULT_SCAN_MAX_EVIDENCE_CHARS,
) -> list[DeliberationFlipTrigger]:
    """Run ``scan_pack_install`` without ever aborting the caller's transaction.

    Used by Pack install admission: scan failures are logged + evented; the
    install result is unchanged. A savepoint isolates scan side-effects on error.
    """
    try:
        with session.begin_nested():
            return scan_pack_install(
                session,
                pack_install_id,
                max_evidence_rows=max_evidence_rows,
                max_evidence_chars=max_evidence_chars,
            )
    except Exception:
        logger.exception(
            "flip monitoring scan failed; pack install preserved pack_install_id=%s",
            pack_install_id,
        )
        try:
            bus.emit(
                session,
                EVENT_FLIP_SCAN_FAILED,
                {"pack_install_id": pack_install_id},
                source_loop="flip_monitoring",
                idempotency_key=f"flip-scan-failed:{pack_install_id}",
            )
        except Exception:
            logger.exception(
                "failed to emit %s for pack_install_id=%s",
                EVENT_FLIP_SCAN_FAILED,
                pack_install_id,
            )
        return []


def list_watches(
    session: Session,
    *,
    run_id: int | None = None,
    status: str | None = None,
    mission_charter_id: int | None = None,
) -> list[DeliberationFlipWatch]:
    stmt = select(DeliberationFlipWatch).order_by(DeliberationFlipWatch.id.asc())
    if run_id is not None:
        stmt = stmt.where(DeliberationFlipWatch.run_id == run_id)
    if status is not None:
        if status not in WATCH_STATUSES:
            raise FlipWatchError("invalid_watch_status", status)
        stmt = stmt.where(DeliberationFlipWatch.status == status)
    watches = list(session.scalars(stmt).all())
    if mission_charter_id is None:
        return watches
    # Optional filter via parent run mission snapshot (scan logic unchanged).
    from openoyster.models import DeliberationRun

    run_ids = {w.run_id for w in watches}
    if not run_ids:
        return []
    runs = {
        r.id: r
        for r in session.scalars(
            select(DeliberationRun).where(DeliberationRun.id.in_(run_ids))
        ).all()
    }

    def _run_charter_id(run: DeliberationRun | None) -> int | None:
        if run is None or not isinstance(run.mission_snapshot_json, dict):
            return None
        raw = run.mission_snapshot_json.get("mission_charter_id")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    target = int(mission_charter_id)
    return [w for w in watches if _run_charter_id(runs.get(w.run_id)) == target]


def get_watch(session: Session, watch_id: int) -> DeliberationFlipWatch | None:
    return session.get(DeliberationFlipWatch, watch_id)


def list_triggers(
    session: Session,
    *,
    status: str | None = None,
    watch_id: int | None = None,
) -> list[tuple[DeliberationFlipTrigger, DeliberationFlipWatch]]:
    """Return triggers joined with their watches; optional watch-status filter.

    ``status`` filters the parent watch status (e.g. ``triggered_candidate`` for
    the public API ``?status=candidate`` alias).
    """
    stmt = (
        select(DeliberationFlipTrigger, DeliberationFlipWatch)
        .join(
            DeliberationFlipWatch,
            DeliberationFlipTrigger.watch_id == DeliberationFlipWatch.id,
        )
        .order_by(DeliberationFlipTrigger.id.asc())
    )
    if watch_id is not None:
        stmt = stmt.where(DeliberationFlipTrigger.watch_id == watch_id)
    if status is not None:
        mapped = _map_trigger_status_filter(status)
        stmt = stmt.where(DeliberationFlipWatch.status == mapped)
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def _map_trigger_status_filter(status: str) -> str:
    """Map public API aliases onto watch status values."""
    if status == "candidate":
        return WATCH_STATUS_TRIGGERED_CANDIDATE
    if status in WATCH_STATUSES:
        return status
    raise FlipWatchError("invalid_trigger_status", status)


def dismiss_watch(session: Session, watch_id: int, *, reason: str) -> DeliberationFlipWatch:
    reason_text = reason.strip()
    if not reason_text:
        raise FlipWatchError("dismiss_reason_required")
    watch = session.get(DeliberationFlipWatch, watch_id)
    if watch is None:
        raise FlipWatchError("watch_not_found", f"watch_id={watch_id}")
    if watch.status not in DISMISSABLE_STATUSES:
        raise FlipWatchError(
            "invalid_watch_transition",
            f"cannot dismiss watch in status={watch.status}",
        )
    now = utcnow()
    previous = watch.status
    # Conditional update so concurrent transitions cannot silently overwrite.
    result = session.execute(
        update(DeliberationFlipWatch)
        .where(
            DeliberationFlipWatch.id == watch_id,
            DeliberationFlipWatch.status.in_(tuple(DISMISSABLE_STATUSES)),
        )
        .values(
            status=WATCH_STATUS_DISMISSED,
            dismiss_reason=reason_text,
            updated_at=now,
        )
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        session.refresh(watch)
        raise FlipWatchError(
            "invalid_watch_transition",
            f"cannot dismiss watch in status={watch.status}",
        )
    session.refresh(watch)
    bus.emit(
        session,
        EVENT_FLIP_WATCH_DISMISSED,
        {
            "watch_id": watch.id,
            "run_id": watch.run_id,
            "flip_local_key": watch.flip_local_key,
            "previous_status": previous,
            "reason": reason_text,
        },
        source_loop="flip_monitoring",
        idempotency_key=f"flip-watch-dismissed:{watch.id}:{watch.updated_at.isoformat()}",
    )
    return watch


def watch_public_payload(watch: DeliberationFlipWatch) -> dict[str, Any]:
    return {
        "id": watch.id,
        "run_id": watch.run_id,
        "flip_local_key": watch.flip_local_key,
        "predicate": watch.predicate_json,
        "status": watch.status,
        "dismiss_reason": watch.dismiss_reason,
        "created_at": watch.created_at.isoformat() if watch.created_at else None,
        "updated_at": watch.updated_at.isoformat() if watch.updated_at else None,
    }


def trigger_public_payload(
    trigger: DeliberationFlipTrigger,
    watch: DeliberationFlipWatch,
) -> dict[str, Any]:
    return {
        "id": trigger.id,
        "watch_id": trigger.watch_id,
        "run_id": watch.run_id,
        "flip_local_key": watch.flip_local_key,
        "pack_install_id": trigger.pack_install_id,
        "matched_evidence_ids": list(trigger.matched_evidence_ids or []),
        "watch_status": watch.status,
        "created_at": trigger.created_at.isoformat() if trigger.created_at else None,
    }


def watching_count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(DeliberationFlipWatch)
            .where(DeliberationFlipWatch.status == WATCH_STATUS_WATCHING)
        )
        or 0
    )
