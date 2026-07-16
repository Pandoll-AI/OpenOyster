"""Flip Condition Monitoring D3 — deterministic Pack-evidence watch scans.

Creates watches for completed decisions with structured flip predicates,
scans new Pack installs via lexical matching, and records candidate triggers.
Never re-runs deliberation. Optional one-shot LLM confirmation of triggers is
available when ``flip_confirm_provider`` is enabled; it never auto-transitions
watch status and never re-deliberates.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from openoyster.deliberation_contracts import (
    MAX_ACTIVE_FLIP_WATCHES,
    MIN_QUOTE_CHARS,
    CitationAnchor,
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

if TYPE_CHECKING:
    from openoyster.config import Settings
    from openoyster.llm import LLMProvider

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

# Optional LLM confirmation bounds (prompt size guard).
CONFIRM_MAX_EVIDENCE_ITEMS = 8
CONFIRM_MAX_EVIDENCE_CHARS = 8000
CONFIRM_STAGE = "flip_confirm"

CONFIRMATION_NONE = "none"
CONFIRMATION_LLM_SUPPORTED = "llm_supported"
CONFIRMATION_LLM_UNSUPPORTED = "llm_unsupported"
CONFIRMATION_ERROR = "error"

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
    """Load evidence with SQL LIMIT + column projection; return (rows, truncated).

    Fetches at most ``max_evidence_rows + 1`` rows (the extra row detects row-cap
    truncation) and only the columns needed for predicate matching. Full-table
    ``.all()`` + Python post-cap is intentionally avoided so large packs cannot
    inflate install-time scan memory/transaction footprint.
    """
    if max_evidence_rows < 1:
        return [], True

    # LIMIT max+1: if we get the extra row, the pack exceeds the hard row bound.
    fetch_limit = max_evidence_rows + 1
    rows = list(
        session.scalars(
            select(PackEvidence)
            .options(
                load_only(
                    PackEvidence.id,
                    PackEvidence.pack_install_id,
                    PackEvidence.local_evidence_id,
                    PackEvidence.global_evidence_id,
                    PackEvidence.text,
                )
            )
            .where(PackEvidence.pack_install_id == pack_install_id)
            .order_by(PackEvidence.id.asc())
            .limit(fetch_limit)
        ).all()
    )
    if not rows:
        return [], False

    row_truncated = len(rows) > max_evidence_rows
    candidate_rows = rows[:max_evidence_rows]

    bounded: list[PackEvidence] = []
    char_total = 0
    char_truncated = False
    for row in candidate_rows:
        text_len = len(row.text or "")
        if char_total + text_len > max_evidence_chars and bounded:
            char_truncated = True
            break
        if char_total + text_len > max_evidence_chars and not bounded:
            # Single oversized first row: still include it so tiny installs work,
            # but mark truncated so callers know further rows were skipped.
            bounded.append(row)
            char_total += text_len
            if len(candidate_rows) > 1 or row_truncated:
                char_truncated = True
            break
        bounded.append(row)
        char_total += text_len
    if len(bounded) < len(candidate_rows):
        char_truncated = True
    truncated = row_truncated or char_truncated
    return bounded, truncated


def scan_pack_install(
    session: Session,
    pack_install_id: int,
    *,
    max_evidence_rows: int = DEFAULT_SCAN_MAX_EVIDENCE_ROWS,
    max_evidence_chars: int = DEFAULT_SCAN_MAX_EVIDENCE_CHARS,
    settings: Settings | None = None,
) -> list[DeliberationFlipTrigger]:
    """Scan all watching predicates against evidence from one Pack install.

    On match: atomically claim the watch (watching → triggered_candidate),
    append a trigger row (unique on watch+install; IntegrityError is idempotent),
    and emit ``flip_trigger_candidate``. When ``flip_confirm_provider`` is set,
    each new trigger is confirmed once via LLM; watch status is never advanced
    by confirmation.

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

    confirm_provider = _resolve_confirm_provider(settings)

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
                    confirmation=CONFIRMATION_NONE,
                    confirmation_anchors_json=[],
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
        if confirm_provider is not None:
            confirm_trigger(session, trigger, confirm_provider)
        created.append(trigger)
    return created


def safe_scan_pack_install(
    session: Session,
    pack_install_id: int,
    *,
    max_evidence_rows: int = DEFAULT_SCAN_MAX_EVIDENCE_ROWS,
    max_evidence_chars: int = DEFAULT_SCAN_MAX_EVIDENCE_CHARS,
    settings: Settings | None = None,
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
                settings=settings,
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


def _resolve_confirm_provider(settings: Settings | None) -> LLMProvider | None:
    from openoyster.llm import flip_confirm_provider_from_settings

    return flip_confirm_provider_from_settings(settings)


def _flip_condition_text(session: Session, watch: DeliberationFlipWatch) -> str:
    """Resolve flip condition text from the decision flip_conditions artifact."""
    artifact = session.scalar(
        select(DeliberationArtifact).where(
            DeliberationArtifact.run_id == watch.run_id,
            DeliberationArtifact.kind == "flip_conditions",
        )
    )
    if artifact is None or not isinstance(artifact.payload_json, dict):
        predicate = watch.predicate_json or {}
        note = predicate.get("note")
        if isinstance(note, str) and note.strip():
            return note.strip()
        return watch.flip_local_key
    items = artifact.payload_json.get("flip_conditions")
    if not isinstance(items, list):
        return watch.flip_local_key
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("local_key") != watch.flip_local_key:
            continue
        condition = item.get("condition")
        if isinstance(condition, dict):
            text = condition.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        item_predicate = item.get("predicate")
        if isinstance(item_predicate, dict):
            note = item_predicate.get("note")
            if isinstance(note, str) and note.strip():
                return note.strip()
        break
    predicate = watch.predicate_json or {}
    note = predicate.get("note")
    if isinstance(note, str) and note.strip():
        return note.strip()
    return watch.flip_local_key


def _load_matched_evidence_bodies(
    session: Session,
    pack_install_id: int,
    matched_evidence_ids: list[Any],
) -> list[tuple[str, str]]:
    """Return (evidence_id, body) pairs for matched ids, prompt-bounded."""
    if not matched_evidence_ids:
        return []
    wanted = {str(eid) for eid in matched_evidence_ids if eid is not None}
    if not wanted:
        return []
    rows = list(
        session.scalars(
            select(PackEvidence)
            .options(
                load_only(
                    PackEvidence.id,
                    PackEvidence.pack_install_id,
                    PackEvidence.local_evidence_id,
                    PackEvidence.global_evidence_id,
                    PackEvidence.text,
                )
            )
            .where(PackEvidence.pack_install_id == pack_install_id)
            .order_by(PackEvidence.id.asc())
        ).all()
    )
    selected: list[tuple[str, str]] = []
    char_total = 0
    for row in rows:
        eid = row.global_evidence_id or row.local_evidence_id
        if eid not in wanted and row.local_evidence_id not in wanted:
            continue
        body = row.text or ""
        if not body.strip():
            continue
        if len(selected) >= CONFIRM_MAX_EVIDENCE_ITEMS:
            break
        if char_total + len(body) > CONFIRM_MAX_EVIDENCE_CHARS and selected:
            break
        if char_total + len(body) > CONFIRM_MAX_EVIDENCE_CHARS and not selected:
            # Truncate first oversized body to stay inside the prompt bound.
            body = body[:CONFIRM_MAX_EVIDENCE_CHARS]
        selected.append((str(eid), body))
        char_total += len(body)
    return selected


def _build_flip_confirm_prompt(
    condition_text: str,
    evidence_items: list[tuple[str, str]],
) -> str:
    parts = [
        "You are confirming whether matched Pack evidence is meaningfully related "
        "to a flip condition. Reply with JSON only: "
        '{"related": bool, "quote": str|null}.',
        "If related is true, quote must be a verbatim substring of one evidence body "
        f"(at least {MIN_QUOTE_CHARS} characters).",
        "",
        "Flip condition:",
        condition_text,
        "",
        "Matched evidence:",
    ]
    for eid, body in evidence_items:
        parts.append(f"[EVIDENCE id={eid}]")
        parts.append(body)
        parts.append("[/EVIDENCE]")
        parts.append("")
    return "\n".join(parts)


def _quote_in_evidence(quote: str, evidence_items: list[tuple[str, str]]) -> str | None:
    """Return evidence_id if quote is a valid verbatim substring of that body."""
    from openoyster.services.deliberation_gates import (
        EvidenceSnapshotView,
        StageGateError,
        validate_anchor,
    )

    stripped = quote.strip()
    if len(stripped) < MIN_QUOTE_CHARS:
        return None
    for eid, body in evidence_items:
        snap = EvidenceSnapshotView(
            snapshot_key=eid,
            db_id=0,
            global_evidence_id=eid,
            text=body,
            payload={},
            pack_install_id=0,
            record_hash="",
        )
        try:
            validate_anchor(
                CitationAnchor(evidence_snapshot_id=eid, quote=quote),
                {eid: snap},
            )
        except (StageGateError, ValueError):
            continue
        return eid
    return None


def confirm_trigger(
    session: Session,
    trigger: DeliberationFlipTrigger,
    provider: LLMProvider,
) -> None:
    """Optionally LLM-confirm a deterministic trigger candidate.

    Never changes watch status. Never re-runs deliberation. Exceptions are
    swallowed into confirmation='error' so scan/install isolation holds.
    """
    try:
        watch = session.get(DeliberationFlipWatch, trigger.watch_id)
        if watch is None:
            trigger.confirmation = CONFIRMATION_ERROR
            trigger.confirmation_note = "watch_missing"
            trigger.confirmation_anchors_json = []
            session.flush()
            return

        condition_text = _flip_condition_text(session, watch)
        evidence_items = _load_matched_evidence_bodies(
            session,
            trigger.pack_install_id,
            list(trigger.matched_evidence_ids or []),
        )
        if not evidence_items:
            trigger.confirmation = CONFIRMATION_LLM_UNSUPPORTED
            trigger.confirmation_note = "no_matched_evidence_body"
            trigger.confirmation_anchors_json = []
            session.flush()
            return

        prompt = _build_flip_confirm_prompt(condition_text, evidence_items)
        try:
            # stage_profile is provider-boundary metadata; failures are non-fatal.
            try:
                provider.stage_profile(CONFIRM_STAGE)
            except Exception:
                logger.debug("flip_confirm stage_profile failed; continuing", exc_info=True)
            raw = provider.query_json(prompt, CONFIRM_STAGE)
        except Exception as exc:
            logger.warning(
                "flip_confirm provider failed trigger_id=%s: %s",
                trigger.id,
                type(exc).__name__,
            )
            trigger.confirmation = CONFIRMATION_ERROR
            trigger.confirmation_note = f"provider_{type(exc).__name__}"[:120]
            trigger.confirmation_anchors_json = []
            session.flush()
            return

        if not isinstance(raw, dict):
            trigger.confirmation = CONFIRMATION_ERROR
            trigger.confirmation_note = "non_object_response"
            trigger.confirmation_anchors_json = []
            session.flush()
            return

        related = raw.get("related")
        quote = raw.get("quote")
        if related is not True:
            trigger.confirmation = CONFIRMATION_LLM_UNSUPPORTED
            trigger.confirmation_note = "related_false" if related is False else "related_missing"
            trigger.confirmation_anchors_json = []
            session.flush()
            return

        if not isinstance(quote, str) or not quote.strip():
            trigger.confirmation = CONFIRMATION_LLM_UNSUPPORTED
            trigger.confirmation_note = "quote_missing"
            trigger.confirmation_anchors_json = []
            session.flush()
            return

        matched_eid = _quote_in_evidence(quote, evidence_items)
        if matched_eid is None:
            trigger.confirmation = CONFIRMATION_LLM_UNSUPPORTED
            trigger.confirmation_note = "quote_unverified"
            trigger.confirmation_anchors_json = []
            session.flush()
            return

        trigger.confirmation = CONFIRMATION_LLM_SUPPORTED
        trigger.confirmation_note = None
        trigger.confirmation_anchors_json = [
            {"evidence_id": matched_eid, "quote": quote.strip()}
        ]
        session.flush()
    except Exception as exc:
        logger.exception(
            "flip_confirm unexpected failure trigger_id=%s",
            getattr(trigger, "id", None),
        )
        try:
            trigger.confirmation = CONFIRMATION_ERROR
            trigger.confirmation_note = f"unexpected_{type(exc).__name__}"[:120]
            trigger.confirmation_anchors_json = []
            session.flush()
        except Exception:
            logger.exception("flip_confirm failed to persist error confirmation")


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
        "confirmation": getattr(trigger, "confirmation", None) or CONFIRMATION_NONE,
        "confirmation_anchors": list(getattr(trigger, "confirmation_anchors_json", None) or []),
        "confirmation_note": getattr(trigger, "confirmation_note", None),
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
