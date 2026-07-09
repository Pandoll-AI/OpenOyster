from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

from openoyster.config import Settings
from openoyster.llm import CodexProvider, ExtractionUnavailable, OpenAICompatibleProvider


class FakeResponse:
    def __init__(self, payload, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


class FakeClient:
    payloads: ClassVar[list] = []
    posts: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        self.posts.append({"args": args, "kwargs": kwargs})
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return FakeResponse(payload)


def _write_codex_config(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "models.json").write_text(
        json.dumps(
            {
                "chat": "gpt-5.4-mini",
                "fast_complex": "gpt-5.3-codex-spark",
                "code": "gpt-5.5",
                "reasoning": "gpt-5.5",
                "secondary": "gpt-5.4",
            }
        ),
        encoding="utf-8",
    )
    (path / "pipeline.json").write_text(
        json.dumps(
            {
                "stages": [
                    {"name": "extract", "tier": "T1", "model_type": "reasoning"},
                    {"name": "merge_judge", "tier": "T1", "model_type": "chat"},
                ],
                "prod_connector": "openai-compatible",
            }
        ),
        encoding="utf-8",
    )


def _payload(*indexes: int) -> str:
    return json.dumps(
        {
            "results": [
                {
                    "chunk_index": index,
                    "entities": [{"name": f"Acme {index}", "kind": "organisation"}],
                    "claims": [{"text": f"Acme {index} shipped a governed platform.", "confidence": 0.8}],
                    "signals": [
                        {
                            "entity": f"Acme {index}",
                            "signal_type": "strategy",
                            "summary": f"Acme {index} shipped a governed platform.",
                            "novelty_score": 0.7,
                            "impact_score": 0.8,
                            "confidence": 0.75,
                            "stance": "support",
                        }
                    ],
                    "hypotheses": [
                        {
                            "claim": f"Acme {index} may be operationalising governance.",
                            "scope": f"Acme {index}",
                            "confidence": 0.55,
                            "evidence_signal_summary": f"Acme {index} shipped a governed platform.",
                            "stance": "support",
                            "quoted_evidence": f"Acme {index} shipped a governed platform.",
                        }
                    ],
                }
                for index in indexes
            ]
        }
    )


def _settings(tmp_path: Path) -> Settings:
    config_dir = tmp_path / "codex-config"
    _write_codex_config(config_dir)
    return Settings(
        workspace=tmp_path / "workspace",
        llm_provider="codex",
        codex_config_dir=config_dir,
        codex_binary="codex-test",
        codex_timeout_seconds=30,
    )


def test_codex_provider_parses_batch_json_and_preserves_order(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    envs: list[dict[str, str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        envs.append(kwargs["env"])
        return subprocess.CompletedProcess(cmd, 0, stdout=_payload(1, 0), stderr="")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    analyses = CodexProvider(_settings(tmp_path)).analyse_batch(
        ["Acme 0 shipped a governed platform.", "Acme 1 shipped a governed platform."],
        policy={"extraction": {"max_claims_per_chunk": 3}},
    )

    assert [analysis.entities[0].name for analysis in analyses] == ["Acme 0", "Acme 1"]
    assert calls[0][:2] == [
        "codex-test",
        "exec",
    ]
    assert "--ephemeral" in calls[0]
    assert "--ignore-user-config" in calls[0]
    assert "--ignore-rules" in calls[0]
    assert "--skip-git-repo-check" in calls[0]
    assert calls[0][calls[0].index("--sandbox") + 1] == "read-only"
    assert calls[0][calls[0].index("--model") + 1] == "gpt-5.5"
    assert calls[0][calls[0].index("-c") + 1] == 'approval_policy="never"'
    assert "OPENOYSTER_API_KEY" not in envs[0]
    prompt = calls[0][-1]
    assert "[CHUNK 0]" in prompt
    assert "[/CHUNK 1]" in prompt
    assert "Do not create, modify, or delete files." in prompt


def test_codex_provider_repairs_malformed_json_once(monkeypatch, tmp_path):
    responses = ["```json\nnot-json\n```", _payload(0)]
    prompts: list[str] = []

    def fake_run(cmd, **kwargs):
        prompts.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, stdout=responses.pop(0), stderr="")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    analyses = CodexProvider(_settings(tmp_path)).analyse_batch(["Acme 0 shipped a governed platform."])

    assert analyses[0].claims[0].text == "Acme 0 shipped a governed platform."
    assert len(prompts) == 2
    assert "[INVALID RESPONSE]" in prompts[1]


def test_codex_provider_raises_unavailable_after_repair_failure(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="not-json", stderr="")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)

    with pytest.raises(ExtractionUnavailable, match="schema repair"):
        CodexProvider(_settings(tmp_path)).analyse_batch(["Acme shipped a platform."])


def test_codex_provider_raises_unavailable_on_nonzero_exit(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 2, stdout="secret stdout", stderr="secret stderr")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)

    with pytest.raises(ExtractionUnavailable, match="codex exited with 2"):
        CodexProvider(_settings(tmp_path)).analyse_batch(["Acme shipped a platform."])

    log_file = next((tmp_path / "codex-config" / "logs" / "extract").glob("*.json"))
    log_text = log_file.read_text(encoding="utf-8")
    assert "secret stdout" not in log_text
    assert "secret stderr" not in log_text


def test_codex_provider_raises_unavailable_on_timeout(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30, output="partial")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)

    with pytest.raises(ExtractionUnavailable, match="timed out"):
        CodexProvider(_settings(tmp_path)).analyse_batch(["Acme shipped a platform."])


def test_openai_compatible_provider_parses_batch_json(monkeypatch, tmp_path):
    FakeClient.payloads = [
        {
            "model": "remote-test",
            "choices": [{"message": {"content": _payload(0)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
    ]
    FakeClient.posts = []
    monkeypatch.setattr("openoyster.llm.httpx.Client", FakeClient)
    settings = Settings(
        workspace=tmp_path / "workspace",
        llm_provider="openai-compatible",
        llm_api_key="secret",
        llm_max_retries=0,
    )

    analyses = OpenAICompatibleProvider(settings).analyse_batch(["Acme 0 shipped a governed platform."])

    assert analyses[0].provider == "openai-compatible"
    assert analyses[0].model == "remote-test"
    assert analyses[0].entities[0].name == "Acme 0"
    request = FakeClient.posts[0]["kwargs"]["json"]
    assert request["response_format"] == {"type": "json_object"}
    assert "Do not create, modify, or delete files." in request["messages"][0]["content"]


def test_openai_compatible_provider_repairs_invalid_json(monkeypatch, tmp_path):
    FakeClient.payloads = [
        {"model": "remote-test", "choices": [{"message": {"content": "not-json"}}]},
        {"model": "remote-test", "choices": [{"message": {"content": _payload(0)}}]},
    ]
    FakeClient.posts = []
    monkeypatch.setattr("openoyster.llm.httpx.Client", FakeClient)
    settings = Settings(
        workspace=tmp_path / "workspace",
        llm_provider="openai-compatible",
        llm_api_key="secret",
        llm_max_retries=0,
    )

    analyses = OpenAICompatibleProvider(settings).analyse_batch(["Acme 0 shipped a governed platform."])

    assert analyses[0].claims[0].text == "Acme 0 shipped a governed platform."
    assert len(FakeClient.posts) == 2
    assert "[INVALID RESPONSE]" in FakeClient.posts[1]["kwargs"]["json"]["messages"][0]["content"]


def test_openai_compatible_provider_failure_is_unavailable_without_fallback(monkeypatch, tmp_path):
    FakeClient.payloads = [{"choices": []}]
    FakeClient.posts = []
    monkeypatch.setattr("openoyster.llm.httpx.Client", FakeClient)
    settings = Settings(
        workspace=tmp_path / "workspace",
        llm_provider="openai-compatible",
        llm_api_key="secret",
        llm_max_retries=0,
    )

    with pytest.raises(ExtractionUnavailable, match="openai-compatible request failed"):
        OpenAICompatibleProvider(settings).analyse_batch(["Acme shipped a platform."])
