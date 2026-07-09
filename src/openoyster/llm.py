from __future__ import annotations

import json
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .config import Settings, get_settings
from .llm_contracts import ExtractionUnavailable
from .schemas import TextAnalysis
from .services.llm_judges import stub_query_json
from .services.llm_runtime import (
    JsonAttempt,
    JsonResponseError,
    codex_subprocess_env,
    extract_json_payload,
    is_usage_dict,
    load_json_object,
    repair_validation_error,
    timeout_output,
    validate_batch_attempt,
)
from .services.llm_stub import stub_analysis
from .services.prompts import T1_CONSTRAINT_BLOCK, build_extract_user_prompt
from .utils import sha256_text


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        raise NotImplementedError

    @abstractmethod
    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        raise NotImplementedError

    def analyse(self, text: str, policy: dict[str, Any] | None = None) -> TextAnalysis:
        return self.analyse_batch([text], policy=policy)[0]


class CodexProvider(LLMProvider):
    name = "codex"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _model_for_stage(self, stage: str) -> str:
        config_dir = Path(self.settings.codex_config_dir)
        pipeline = load_json_object(config_dir / "pipeline.json")
        models = load_json_object(config_dir / "models.json")
        stages = pipeline.get("stages")
        if not isinstance(stages, list):
            raise ExtractionUnavailable("pipeline.json stages must be a list")
        model_type = None
        for item in stages:
            if isinstance(item, dict) and item.get("name") == stage:
                model_type = item.get("model_type")
                break
        if not isinstance(model_type, str):
            raise ExtractionUnavailable(f"pipeline stage is not configured: {stage}")
        model = models.get(model_type)
        if not isinstance(model, str):
            raise ExtractionUnavailable(f"model type is not configured: {model_type}")
        return model

    def _write_log(self, *, stage: str, record: dict[str, Any]) -> None:
        log_dir = Path(self.settings.codex_config_dir) / "logs" / stage
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{record['run_id']}.json"
            log_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return

    def _attempt(self, prompt: str, stage: str) -> JsonAttempt:
        model = self._model_for_stage(stage)
        prepared_prompt = f"{T1_CONSTRAINT_BLOCK}\n\n{prompt}"
        run_id = uuid4().hex
        started = time.perf_counter()
        exit_code: int | None = None
        parsing_success = False
        error: str | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="openoyster-codex-") as sandbox_root:
                completed = subprocess.run(
                    [
                        self.settings.codex_binary,
                        "exec",
                        "--ephemeral",
                        "--ignore-user-config",
                        "--ignore-rules",
                        "--skip-git-repo-check",
                        "--sandbox",
                        "read-only",
                        "--cd",
                        sandbox_root,
                        "-c",
                        'approval_policy="never"',
                        "--model",
                        model,
                        "-",
                    ],
                    capture_output=True,
                    input=prepared_prompt,
                    text=True,
                    timeout=self.settings.codex_timeout_seconds,
                    check=False,
                    env=codex_subprocess_env(),
                )
            exit_code = completed.returncode
            if completed.returncode != 0:
                error = f"codex exited with {completed.returncode}"
                raise ExtractionUnavailable(error)
            payload, raw_response = extract_json_payload(completed.stdout)
            parsing_success = True
            return JsonAttempt(
                payload=payload,
                raw_response=raw_response,
                model=model,
                usage={
                    "prompt_characters": len(prepared_prompt),
                    "response_characters": len(completed.stdout),
                },
            )
        except FileNotFoundError as exc:
            error = f"codex binary not found: {self.settings.codex_binary}"
            raise ExtractionUnavailable(error) from exc
        except subprocess.TimeoutExpired as exc:
            error = f"codex timed out after {self.settings.codex_timeout_seconds} seconds"
            timeout_output(exc.stdout)
            raise ExtractionUnavailable(error) from exc
        except JsonResponseError as exc:
            error = exc.reason
            raise
        finally:
            self._write_log(
                stage=stage,
                record={
                    "run_id": run_id,
                    "stage": stage,
                    "model": model,
                    "prompt_length": len(prepared_prompt),
                    "prompt_sha256": sha256_text(prepared_prompt),
                    "duration_seconds": time.perf_counter() - started,
                    "exit_code": exit_code,
                    "parsing_success": parsing_success,
                    "error": error,
                },
            )

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        try:
            return self._attempt(prompt, stage).payload
        except JsonResponseError as exc:
            raise ExtractionUnavailable(exc.reason) from exc

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        if not texts:
            return []
        prompt = build_extract_user_prompt(texts, policy)
        try:
            attempt = self._attempt(prompt, "extract")
            return validate_batch_attempt(attempt, texts, self.name)
        except JsonResponseError as exc:
            return repair_validation_error(
                provider_name=self.name,
                texts=texts,
                prompt=prompt,
                broken_response=exc.raw_response,
                error=exc.reason,
                repair_call=lambda repair_prompt: self._attempt(repair_prompt, "extract"),
            )


class OpenAICompatibleProvider(LLMProvider):
    name = "openai-compatible"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _attempt(self, prompt: str) -> JsonAttempt:
        if not self.settings.llm_api_key:
            raise ExtractionUnavailable("OPENOYSTER_LLM_API_KEY is required for openai-compatible")
        request_prompt = f"{T1_CONSTRAINT_BLOCK}\n\n{prompt}"
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
                            "messages": [{"role": "user", "content": request_prompt}],
                        },
                    )
                    response.raise_for_status()
                    raw = response.json()
                content = raw["choices"][0]["message"]["content"]
                payload, raw_response = extract_json_payload(content)
                usage = raw.get("usage", {})
                return JsonAttempt(
                    payload=payload,
                    raw_response=raw_response,
                    model=str(raw.get("model", self.settings.llm_model)),
                    usage=usage if is_usage_dict(usage) else {},
                )
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, JsonResponseError) as exc:
                last_error = exc
                if isinstance(exc, JsonResponseError):
                    raise
                if attempt < self.settings.llm_max_retries:
                    time.sleep(min(0.5 * (2**attempt), 4.0))
        raise ExtractionUnavailable(f"openai-compatible request failed: {last_error}") from last_error

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        if not texts:
            return []
        prompt = build_extract_user_prompt(texts, policy)
        try:
            attempt = self._attempt(prompt)
            return validate_batch_attempt(attempt, texts, self.name)
        except JsonResponseError as exc:
            return repair_validation_error(
                provider_name=self.name,
                texts=texts,
                prompt=prompt,
                broken_response=exc.raw_response,
                error=exc.reason,
                repair_call=self._attempt,
            )

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        del stage
        try:
            return self._attempt(prompt).payload
        except JsonResponseError as exc:
            raise ExtractionUnavailable(exc.reason) from exc


class StubProvider(LLMProvider):
    """Deterministic test double for extraction plus merge_judge and stance_judge JSON calls."""

    name = "stub"

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        return [stub_analysis(text, index) for index, text in enumerate(texts)]

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        return stub_query_json(prompt, stage)


def provider_from_settings(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    match settings.llm_provider:
        case "codex":
            return CodexProvider(settings)
        case "openai-compatible":
            return OpenAICompatibleProvider(settings)
        case "stub":
            return StubProvider()
        case other:
            raise ExtractionUnavailable(f"unknown LLM provider: {other}")
