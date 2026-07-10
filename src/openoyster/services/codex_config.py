from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from ..llm_contracts import ExtractionUnavailable
from .llm_runtime import load_json_object

ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]
_REASONING_EFFORTS: Final[dict[str, ReasoningEffort]] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}


@dataclass(frozen=True, slots=True)
class CodexStageConfig:
    model: str
    effort: ReasoningEffort | None


def load_codex_stage_config(config_dir: Path, stage: str) -> CodexStageConfig:
    pipeline = load_json_object(config_dir / "pipeline.json")
    models = load_json_object(config_dir / "models.json")
    stages = pipeline.get("stages")
    if not isinstance(stages, list):
        raise ExtractionUnavailable("pipeline.json stages must be a list")
    for item in stages:
        if isinstance(item, dict) and item.get("name") == stage:
            return _parse_stage_config(item, models, stage)
    raise ExtractionUnavailable(f"pipeline stage is not configured: {stage}")


def _parse_stage_config(
    item: dict[str, Any],
    models: dict[str, Any],
    stage: str,
) -> CodexStageConfig:
    model_type = item.get("model_type")
    if not isinstance(model_type, str):
        raise ExtractionUnavailable(f"pipeline stage has no model type: {stage}")
    model = models.get(model_type)
    if not isinstance(model, str):
        raise ExtractionUnavailable(f"model type is not configured: {model_type}")
    return CodexStageConfig(model=model, effort=_parse_reasoning_effort(item.get("effort"), stage))


def _parse_reasoning_effort(value: Any, stage: str) -> ReasoningEffort | None:
    if value is None:
        return None
    if isinstance(value, str) and value in _REASONING_EFFORTS:
        return _REASONING_EFFORTS[value]
    raise ExtractionUnavailable(f"invalid reasoning effort for stage {stage}: {value!r}")
