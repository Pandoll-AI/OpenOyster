from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, assert_never
from uuid import uuid4

import httpx

from .config import Settings, get_settings
from .llm_contracts import ExtractionUnavailable
from .schemas import TextAnalysis
from .services.codex_config import load_codex_stage_config
from .services.llm_judges import stub_query_json
from .services.llm_runtime import (
    JsonAttempt,
    JsonResponseError,
    codex_subprocess_env,
    extract_json_payload,
    is_usage_dict,
    repair_validation_error,
    timeout_output,
    validate_batch_attempt,
)
from .services.llm_stub import stub_analysis
from .services.prompts import T1_CONSTRAINT_BLOCK, build_extract_user_prompt
from .utils import sha256_text

# Claude CLI isolation flags for critic-only text judgments on untrusted Pack
# evidence prompts. Never add --dangerously-skip-permissions.
CLAUDE_CLI_SAFE_FLAGS: tuple[str, ...] = (
    "-p",
    "--output-format",
    "json",
    "--tools",
    "",  # disable all tools (text judgment only)
    "--no-session-persistence",  # do not write session history
    "--bare",  # skip CLAUDE.md / hooks / plugins / MCP / skills / custom
    "--strict-mcp-config",
    "--mcp-config",
    "{}",  # double-block MCP
    "--permission-mode",
    "dontAsk",  # auto-deny even if tools appear
)

# Minimal env for Claude CLI auth (HOME → ~/.claude) without Pack/DB/secret bleed.
_CLAUDE_CLI_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "ANTHROPIC_API_KEY",
    }
)


def claude_cli_subprocess_env() -> dict[str, str]:
    """Allowlist env for Claude CLI: PATH/HOME/LANG/LC_* + optional API key."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _CLAUDE_CLI_ENV_ALLOWLIST or key.startswith("LC_"):
            env[key] = value
    return env


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

    def stage_profile(self, stage: str) -> dict[str, Any]:
        """Return provider/model/effort provenance for a deliberation stage call."""
        del stage
        return {"provider": self.name, "model": None, "effort": None}


class CodexProvider(LLMProvider):
    name = "codex"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _write_log(self, *, stage: str, record: dict[str, Any]) -> None:
        log_dir = Path(self.settings.codex_config_dir) / "logs" / stage
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{record['run_id']}.json"
            log_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return

    def _attempt(self, prompt: str, stage: str) -> JsonAttempt:
        stage_config = load_codex_stage_config(Path(self.settings.codex_config_dir), stage)
        model = stage_config.model
        prepared_prompt = f"{T1_CONSTRAINT_BLOCK}\n\n{prompt}"
        run_id = uuid4().hex
        started = time.perf_counter()
        exit_code: int | None = None
        parsing_success = False
        error: str | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="openoyster-codex-") as sandbox_root:
                command = [
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
                ]
                if stage_config.effort is not None:
                    command.extend(["-c", f'model_reasoning_effort="{stage_config.effort}"'])
                command.extend(["--model", model, "-"])
                completed = subprocess.run(
                    command,
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
                    "effort": stage_config.effort,
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

    def stage_profile(self, stage: str) -> dict[str, Any]:
        stage_config = load_codex_stage_config(Path(self.settings.codex_config_dir), stage)
        return {
            "provider": self.name,
            "model": stage_config.model,
            "effort": stage_config.effort,
        }

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


class ClaudeCliProvider(LLMProvider):
    """Cross-vendor secondary critic via Claude Code CLI (``claude -p``).

    Critic-only: ``query_json`` is supported; extraction is intentionally unavailable.
    Dangerous permission-bypass flags are never passed.
    """

    name = "claude-cli"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _write_log(self, *, stage: str, record: dict[str, Any]) -> None:
        # Separate from .codex-llm — critic2 has its own artifact path.
        log_dir = Path(self.settings.workspace) / "claude-cli-logs" / stage
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{record['run_id']}.json"
            log_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return

    def _parse_stdout(self, stdout: str) -> tuple[dict[str, Any], str]:
        """Unwrap Claude ``--output-format json`` envelope, then extract the payload.

        Claude CLI typically wraps model text as ``{"result": "..."}`` or
        ``{"text": "..."}``. Prefer that inner string; fall back to scanning the
        full stdout for the first JSON object.
        """
        stripped = stdout.strip()
        try:
            outer = json.loads(stripped)
        except json.JSONDecodeError:
            return extract_json_payload(stdout)
        if isinstance(outer, dict):
            for key in ("result", "text"):
                inner = outer.get(key)
                if isinstance(inner, dict):
                    return inner, stdout
                if isinstance(inner, str) and inner.strip():
                    try:
                        return extract_json_payload(inner)
                    except JsonResponseError:
                        pass
        return extract_json_payload(stdout)

    def _attempt(self, prompt: str, stage: str) -> JsonAttempt:
        model = self.settings.claude_model
        prepared_prompt = f"{T1_CONSTRAINT_BLOCK}\n\n{prompt}"
        run_id = uuid4().hex
        started = time.perf_counter()
        exit_code: int | None = None
        parsing_success = False
        error: str | None = None
        try:
            # Isolated cwd + allowlisted env: untrusted Pack evidence may appear in
            # the prompt; do not inherit repo CLAUDE.md/hooks/MCP/session or secrets.
            with tempfile.TemporaryDirectory(prefix="openoyster-claude-cli-") as sandbox_root:
                command = [self.settings.claude_binary, *CLAUDE_CLI_SAFE_FLAGS]
                if model:
                    command.extend(["--model", model])
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    input=prepared_prompt,
                    text=True,
                    timeout=self.settings.claude_timeout_seconds,
                    check=False,
                    cwd=sandbox_root,
                    env=claude_cli_subprocess_env(),
                )
            exit_code = completed.returncode
            if completed.returncode != 0:
                error = f"claude-cli exited with {completed.returncode}"
                raise ExtractionUnavailable(error)
            payload, raw_response = self._parse_stdout(completed.stdout)
            parsing_success = True
            return JsonAttempt(
                payload=payload,
                raw_response=raw_response,
                model=model or "claude-cli-default",
                usage={
                    "prompt_characters": len(prepared_prompt),
                    "response_characters": len(completed.stdout),
                },
            )
        except FileNotFoundError as exc:
            error = f"claude binary not found: {self.settings.claude_binary}"
            raise ExtractionUnavailable(error) from exc
        except subprocess.TimeoutExpired as exc:
            error = f"claude-cli timed out after {self.settings.claude_timeout_seconds} seconds"
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
                    "effort": None,
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

    def stage_profile(self, stage: str) -> dict[str, Any]:
        del stage
        return {
            "provider": self.name,
            "model": self.settings.claude_model,
            "effort": None,
        }

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        del texts, policy
        raise ExtractionUnavailable("claude-cli is critic-only")


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

    def stage_profile(self, stage: str) -> dict[str, Any]:
        del stage
        return {
            "provider": self.name,
            "model": self.settings.llm_model,
            "effort": None,
        }


class StubProvider(LLMProvider):
    """Deterministic test double for extraction and configured JSON judgement stages."""

    name = "stub"

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        return [stub_analysis(text, index) for index, text in enumerate(texts)]

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        # Secondary critic reuses the primary critic stub contract.
        if stage == "deliberation_critic_secondary":
            stage = "deliberation_critic"
        return stub_query_json(prompt, stage)

    def stage_profile(self, stage: str) -> dict[str, Any]:
        del stage
        return {"provider": self.name, "model": "stub", "effort": None}


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
            assert_never(other)


def critic2_provider_from_settings(settings: Settings | None = None) -> LLMProvider | None:
    """Optional second-pass critic provider. ``none`` disables the stage entirely."""
    settings = settings or get_settings()
    match settings.critic2_provider:
        case "none":
            return None
        case "codex":
            return CodexProvider(settings)
        case "stub":
            return StubProvider()
        case "claude-cli":
            return ClaudeCliProvider(settings)
        case other:
            assert_never(other)
