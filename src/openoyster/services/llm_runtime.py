from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..llm_contracts import ExtractionUnavailable
from ..schemas import BatchAnalysisResponse, TextAnalysis
from .prompts import build_json_repair_prompt


@dataclass(frozen=True, slots=True)
class JsonAttempt:
    payload: dict[str, Any]
    raw_response: str
    model: str
    usage: dict[str, int | float]


class JsonResponseError(RuntimeError):
    def __init__(self, reason: str, raw_response: str):
        self.reason = reason
        self.raw_response = raw_response
        super().__init__(reason)


def content_to_text(content: object) -> str:
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        )
    if isinstance(content, str):
        return content
    return repr(content)


def extract_json_payload(content: object) -> tuple[dict[str, Any], str]:
    raw = content_to_text(content)
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end < start:
        raise JsonResponseError("model did not return a JSON object", raw)
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JsonResponseError(f"model returned invalid JSON: {exc}", raw) from exc
    if not isinstance(payload, dict):
        raise JsonResponseError("model JSON must be an object", raw)
    return payload, raw


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtractionUnavailable(f"cannot load LLM config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExtractionUnavailable(f"LLM config {path} must contain a JSON object")
    return payload


def validate_batch_attempt(attempt: JsonAttempt, texts: list[str], provider: str) -> list[TextAnalysis]:
    try:
        response = BatchAnalysisResponse.model_validate(attempt.payload)
    except ValidationError as exc:
        raise JsonResponseError(str(exc), attempt.raw_response) from exc
    by_index = {item.chunk_index: item for item in response.results}
    analyses: list[TextAnalysis] = []
    for index, text in enumerate(texts):
        item = by_index.get(index)
        if item is None:
            analyses.append(
                TextAnalysis(
                    entities=[],
                    claims=[],
                    signals=[],
                    hypotheses=[],
                    provider=provider,
                    model=attempt.model,
                    usage=attempt.usage,
                    warnings=[f"missing result for chunk_index {index}"],
                    metadata={"chunk_index": index, "missing_chunk_index": True},
                )
            )
            continue
        analyses.append(
            TextAnalysis(
                entities=item.entities,
                claims=item.claims,
                signals=item.signals,
                hypotheses=item.hypotheses,
                provider=provider,
                model=attempt.model,
                usage={**attempt.usage, "input_characters": len(text)},
                metadata={"chunk_index": index},
            )
        )
    return analyses


def repair_validation_error(
    *,
    provider_name: str,
    texts: list[str],
    prompt: str,
    broken_response: str,
    error: str,
    repair_call: Callable[[str], JsonAttempt],
) -> list[TextAnalysis]:
    repair_prompt = build_json_repair_prompt(
        original_prompt=prompt,
        raw_response=broken_response,
        validation_error=error,
    )
    try:
        repaired = repair_call(repair_prompt)
        return validate_batch_attempt(repaired, texts, provider_name)
    except (ExtractionUnavailable, JsonResponseError, ValidationError) as exc:
        reason = f"extract response failed schema repair: {exc}"
        raise ExtractionUnavailable(reason) from exc


def timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def is_usage_dict(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(item, int | float) for item in value.values())
