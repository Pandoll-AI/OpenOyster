from __future__ import annotations

import json

from openoyster.config import Settings
from openoyster.llm import OpenAICompatibleProvider


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    payload = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return FakeResponse(self.payload)


def test_remote_provider_parses_remote_json(monkeypatch, tmp_path):
    content = {
        "entities": ["Acme"],
        "claims": [{"text": "Acme hired engineers.", "confidence": 0.8}],
        "signals": [
            {
                "entity": "Acme",
                "signal_type": "hiring",
                "summary": "Acme hired engineers.",
                "novelty_score": 0.7,
                "impact_score": 0.6,
                "confidence": 0.8,
                "stance": "support",
            }
        ],
        "hypotheses": [
            {
                "claim": "Acme may be expanding data capability.",
                "scope": "Acme",
                "confidence": 0.5,
                "evidence_signal_summary": "Acme hired engineers.",
                "stance": "support",
            }
        ],
    }
    FakeClient.payload = {
        "model": "remote-test",
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    monkeypatch.setattr("openoyster.llm.httpx.Client", FakeClient)
    settings = Settings(
        workspace=tmp_path / "w",
        llm_provider="openai-compatible",
        llm_api_key="secret",
        llm_fallback_to_local=False,
    )
    analysis = OpenAICompatibleProvider(settings).analyse("Acme hired engineers.")
    assert analysis.provider == "openai-compatible"
    assert analysis.model == "remote-test"
    assert analysis.hypotheses[0].claim.startswith("Acme may")


def test_remote_failure_is_visible_when_falling_back(monkeypatch, tmp_path):
    FakeClient.payload = {"choices": [{"message": {"content": "not json"}}]}
    monkeypatch.setattr("openoyster.llm.httpx.Client", FakeClient)
    settings = Settings(
        workspace=tmp_path / "w",
        llm_provider="openai-compatible",
        llm_api_key="secret",
        llm_max_retries=0,
        llm_fallback_to_local=True,
    )
    analysis = OpenAICompatibleProvider(settings).analyse("Acme is hiring data platform engineers.")
    assert analysis.provider == "local-heuristic"
    assert analysis.metadata["fallback_from"] == "openai-compatible"
    assert any("Remote provider failed" in warning for warning in analysis.warnings)
