from __future__ import annotations

import json
from typing import ClassVar

import pytest

from openoyster.config import Settings
from openoyster.llm import ExtractionUnavailable, OpenAICompatibleProvider, StubProvider


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


def test_stub_provider_merge_judge_matches_only_normalized_equal_claims():
    prompt = (
        "[NEW CLAIM]\n"
        "scope: Acme\n"
        "claim: Acme governance improves adoption.\n"
        "[/NEW CLAIM]\n\n"
        "[CANDIDATE 0]\n"
        "id: 1\n"
        "scope: Acme\n"
        "claim: Acme governance improves adoption.\n"
        "[/CANDIDATE 0]"
    )

    payload = StubProvider().query_json(prompt, "merge_judge")

    assert payload["relation"] == "same"
    assert payload["match_index"] == 0


def test_stub_provider_stance_judge_uses_chunk_text_markers():
    prompt = (
        "[CHUNK 0]\n"
        "Acme has no evidence that model quality is the blocker. Governance remains under review.\n"
        "[/CHUNK 0]"
    )

    payload = StubProvider().query_json(prompt, "stance_judge")

    assert payload["judgements"][0]["stance"] == "oppose"
    assert payload["judgements"][0]["quoted_evidence"].startswith("Acme has no evidence")
