from __future__ import annotations

import re
from typing import Final

from ..utils import normalise_text

_SENTENCE_BOUNDARY: Final = re.compile(r"(?<=[.!?。\uFF01\uFF1F])\s+|\n+")


def split_sentences(text: str) -> list[str]:
    return [
        normalise_text(sentence) for sentence in _SENTENCE_BOUNDARY.split(text) if normalise_text(sentence)
    ]


def chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        msg = "chunk_size must be positive and overlap must be smaller"
        raise ValueError(msg)
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        proposed_end = min(start + chunk_size, len(text))
        end = proposed_end
        if proposed_end < len(text):
            window = text[start:proposed_end]
            boundaries = [window.rfind(marker) for marker in (". ", "? ", "! ", "\n", "。")]
            boundary = max(boundaries)
            if boundary >= int(chunk_size * 0.55):
                end = start + boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks
