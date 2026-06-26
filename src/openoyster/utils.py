from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

_WHITESPACE = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def normalise_name(text: str) -> str:
    return normalise_text(text).casefold()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def stable_hash(*parts: Any) -> str:
    serialised = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return sha256_text(serialised)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)
