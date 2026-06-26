from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
from pydantic import ValidationError

from .config import Settings, get_settings
from .services.text import TextAnalysis, analyse_text

_SYSTEM_PROMPT = """You extract decision-relevant intelligence from one document chunk.
Return ONLY one valid JSON object with these keys:
- entities: string[]
- claims: objects with text, subject, predicate, object, confidence, metadata_json
- signals: objects with entity, signal_type, summary, novelty_score, impact_score, confidence, stance, metadata_json
- hypotheses: objects with claim, scope, confidence, evidence_signal_summary, stance, metadata_json
All scores must be between 0 and 1. Hypotheses must be falsifiable and cautious. Include counter-evidence as stance=oppose. Do not invent facts not present in the text.
"""


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def analyse(self, text: str, policy: dict | None = None) -> TextAnalysis:
        raise NotImplementedError


class LocalHeuristicProvider(LLMProvider):
    name = "local-heuristic"

    def analyse(self, text: str, policy: dict | None = None) -> TextAnalysis:
        return analyse_text(text, policy=policy)


def _extract_json_payload(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        )
    if not isinstance(content, str):
        raise ValueError("Remote model content is not a string")
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Remote model did not return a JSON object")
    payload = json.loads(stripped[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Remote model JSON must be an object")
    return payload


class OpenAICompatibleProvider(LLMProvider):
    name = "openai-compatible"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _request(self, text: str) -> TextAnalysis:
        if not self.settings.llm_api_key:
            raise RuntimeError("OPENOYSTER_LLM_API_KEY is required for the remote provider")
        url = f"{self.settings.llm_base_url}/chat/completions"
        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
                    response = client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {self.settings.llm_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.settings.llm_model,
                            "temperature": 0,
                            "response_format": {"type": "json_object"},
                            "messages": [
                                {"role": "system", "content": _SYSTEM_PROMPT},
                                {"role": "user", "content": text},
                            ],
                        },
                    )
                    response.raise_for_status()
                    raw = response.json()
                content = raw["choices"][0]["message"]["content"]
                payload = _extract_json_payload(content)
                analysis = TextAnalysis.model_validate(
                    {
                        **payload,
                        "provider": self.name,
                        "model": raw.get("model", self.settings.llm_model),
                        "usage": raw.get("usage", {}),
                        "warnings": [],
                        "metadata": {"remote": True},
                    }
                )
                return analysis
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
                last_error = exc
                if attempt < self.settings.llm_max_retries:
                    time.sleep(min(0.5 * (2**attempt), 4.0))
        assert last_error is not None
        raise RuntimeError(f"Remote LLM analysis failed: {last_error}") from last_error

    def analyse(self, text: str, policy: dict | None = None) -> TextAnalysis:
        try:
            return self._request(text)
        except RuntimeError as exc:
            if not self.settings.llm_fallback_to_local:
                raise
            fallback = analyse_text(text, policy=policy)
            fallback.warnings.append(f"Remote provider failed; explicit local fallback used: {exc}")
            fallback.metadata.update(
                {
                    "fallback_from": self.name,
                    "remote_error": str(exc),
                }
            )
            return fallback


def provider_from_settings(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if settings.llm_provider == "openai-compatible":
        return OpenAICompatibleProvider(settings)
    return LocalHeuristicProvider()
