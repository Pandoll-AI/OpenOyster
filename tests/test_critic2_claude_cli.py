"""Secondary critic Claude CLI provider — unit tests with no real CLI binary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from openoyster.config import Settings
from openoyster.llm import (
    CLAUDE_CLI_SAFE_FLAGS,
    ClaudeCliProvider,
    ExtractionUnavailable,
    claude_cli_subprocess_env,
    critic2_provider_from_settings,
)
from openoyster.utils import sha256_text


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {
        "workspace": workspace,
        "critic2_provider": "claude-cli",
        "claude_binary": "claude-test",
        "claude_timeout_seconds": 30,
        "claude_model": None,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def _claude_wrapped(inner: dict | str) -> str:
    """Simulate ``claude -p --output-format json`` envelope."""
    text = inner if isinstance(inner, str) else json.dumps(inner)
    return json.dumps({"type": "result", "result": text, "is_error": False})


def test_query_json_parses_wrapped_claude_response(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    inputs: list[str] = []
    payload = {"verdict": "revise", "severity": "medium", "notes": "cross-vendor check"}

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        inputs.append(kwargs["input"])
        return subprocess.CompletedProcess(cmd, 0, stdout=_claude_wrapped(payload), stderr="")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    provider = ClaudeCliProvider(_settings(tmp_path))
    result = provider.query_json("review this decision pack", "deliberation_critic_secondary")

    assert result == payload
    assert calls[0][:4] == ["claude-test", "-p", "--output-format", "json"]
    assert "--model" not in calls[0]
    assert "review this decision pack" in inputs[0]


def test_query_json_passes_model_flag_when_configured(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_claude_wrapped({"ok": True}), stderr=""
        )

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    provider = ClaudeCliProvider(_settings(tmp_path, claude_model="claude-opus-4-6"))
    assert provider.query_json("x", "deliberation_critic_secondary") == {"ok": True}
    assert "--model" in calls[0]
    assert calls[0][calls[0].index("--model") + 1] == "claude-opus-4-6"


def test_query_json_falls_back_to_raw_stdout_json(monkeypatch, tmp_path: Path) -> None:
    """When envelope is absent, extract_json_payload on full stdout still works."""

    def fake_run(cmd, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout='{"plain": true}', stderr="")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    result = ClaudeCliProvider(_settings(tmp_path)).query_json("x", "deliberation_critic_secondary")
    assert result == {"plain": True}


def test_nonzero_exit_raises_unavailable(monkeypatch, tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(cmd, 2, stdout="oops", stderr="fail")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    with pytest.raises(ExtractionUnavailable, match="claude-cli exited with 2"):
        ClaudeCliProvider(_settings(tmp_path)).query_json("x", "deliberation_critic_secondary")


def test_timeout_raises_unavailable(monkeypatch, tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        del kwargs
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30, output="partial")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    with pytest.raises(ExtractionUnavailable, match="timed out"):
        ClaudeCliProvider(_settings(tmp_path)).query_json("x", "deliberation_critic_secondary")


def test_log_stores_sha256_not_raw_prompt(monkeypatch, tmp_path: Path) -> None:
    secret_prompt_marker = "mission evidence secret marker 9f3a"

    def fake_run(cmd, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_claude_wrapped({"ok": True}), stderr=""
        )

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    settings = _settings(tmp_path)
    ClaudeCliProvider(settings).query_json(secret_prompt_marker, "deliberation_critic_secondary")

    log_dir = Path(settings.workspace) / "claude-cli-logs" / "deliberation_critic_secondary"
    log_file = next(log_dir.glob("*.json"))
    log_text = log_file.read_text(encoding="utf-8")
    record = json.loads(log_text)

    assert secret_prompt_marker not in log_text
    assert "prompt_preview" not in log_text
    assert "prompt_sha256" in record
    assert len(record["prompt_sha256"]) == 64
    # Hash covers prepared prompt (T1 block + user text), not the bare marker alone.
    assert record["prompt_sha256"] != sha256_text(secret_prompt_marker)
    assert record["prompt_length"] > len(secret_prompt_marker)


def test_critic2_factory_returns_claude_cli_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path, critic2_provider="claude-cli")
    provider = critic2_provider_from_settings(settings)
    assert isinstance(provider, ClaudeCliProvider)
    assert provider.name == "claude-cli"


def test_command_omits_dangerous_permission_flags(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_claude_wrapped({"ok": True}), stderr=""
        )

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    ClaudeCliProvider(_settings(tmp_path)).query_json("x", "deliberation_critic_secondary")

    cmd = calls[0]
    assert "--dangerously-skip-permissions" not in cmd
    assert not any("dangerously" in str(part) for part in cmd)
    assert not any("skip-permissions" in str(part) for part in cmd)
    # Prompt is stdin, never argv.
    assert "x" not in cmd


def test_claude_cli_isolation_flags_cwd_env_and_stdin(monkeypatch, tmp_path: Path) -> None:
    """#2 RED/GREEN: safe flags, isolated cwd, allowlisted env, prompt on stdin."""
    calls: list[tuple[list[str], dict]] = []
    secret_prompt = "untrusted pack evidence injection marker 7c2e"

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), dict(kwargs)))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_claude_wrapped({"ok": True}), stderr=""
        )

    monkeypatch.setenv("OPENOYSTER_DATABASE_URL", "postgresql://secret-db")
    monkeypatch.setenv("OPENOYSTER_WORKSPACE", "/tmp/openoyster-secret-ws")
    monkeypatch.setenv("DATABASE_URL", "postgresql://other-secret")
    monkeypatch.setenv("MY_API_SECRET", "super-secret-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)

    ClaudeCliProvider(_settings(tmp_path)).query_json(
        secret_prompt, "deliberation_critic_secondary"
    )

    assert len(calls) == 1
    cmd, kwargs = calls[0]

    # (a) isolation flag set present
    assert cmd[0] == "claude-test"
    for flag in (
        "--tools",
        "--no-session-persistence",
        "--bare",
        "--strict-mcp-config",
        "--mcp-config",
        "--permission-mode",
    ):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--mcp-config") + 1] == "{}"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    for flag in CLAUDE_CLI_SAFE_FLAGS:
        assert flag in cmd

    # (b) never skip permissions
    assert "--dangerously-skip-permissions" not in cmd
    assert not any("dangerously" in str(part) for part in cmd)

    # (c) isolated temp cwd — not the repository root
    repo_root = Path(__file__).resolve().parents[1]
    cwd = kwargs.get("cwd")
    assert cwd is not None
    assert Path(cwd).resolve() != repo_root.resolve()
    assert "openoyster-claude-cli-" in str(cwd)

    # (d) env allowlist only — no Pack/DB/OPENOYSTER/secret bleed
    env = kwargs.get("env")
    assert isinstance(env, dict)
    assert "OPENOYSTER_DATABASE_URL" not in env
    assert "OPENOYSTER_WORKSPACE" not in env
    assert "DATABASE_URL" not in env
    assert "MY_API_SECRET" not in env
    assert env.get("ANTHROPIC_API_KEY") == "test-anthropic-key"
    for key in env:
        assert key in {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "ANTHROPIC_API_KEY"} or key.startswith(
            "LC_"
        )

    # (e) prompt via stdin (input=), never argv
    assert secret_prompt in kwargs.get("input", "")
    assert secret_prompt not in cmd


def test_claude_cli_subprocess_env_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.setenv("OPENOYSTER_FOO", "nope")
    monkeypatch.setenv("DATABASE_URL", "nope")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = claude_cli_subprocess_env()
    assert "PATH" in env
    assert "HOME" in env
    assert "OPENOYSTER_FOO" not in env
    assert "DATABASE_URL" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_stage_profile_reports_claude_cli(tmp_path: Path) -> None:
    settings = _settings(tmp_path, claude_model="claude-sonnet-4-6")
    profile = ClaudeCliProvider(settings).stage_profile("deliberation_critic_secondary")
    assert profile == {"provider": "claude-cli", "model": "claude-sonnet-4-6", "effort": None}


def test_analyse_batch_is_critic_only(tmp_path: Path) -> None:
    with pytest.raises(ExtractionUnavailable, match="critic-only"):
        ClaudeCliProvider(_settings(tmp_path)).analyse_batch(["not used"])


def test_critic2_default_remains_none() -> None:
    """Regression: enabling claude-cli option must not flip the default off-path."""
    assert Settings.model_fields["critic2_provider"].default == "none"
