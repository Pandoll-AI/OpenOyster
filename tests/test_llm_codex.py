from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from openoyster.config import Settings
from openoyster.llm import CodexProvider, ExtractionUnavailable


def _write_codex_config(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "models.json").write_text(
        json.dumps(
            {
                "chat": "gpt-5.6-sol",
                "fast_complex": "gpt-5.6-sol",
                "code": "gpt-5.6-sol",
                "reasoning": "gpt-5.6-sol",
                "secondary": "gpt-5.6-sol",
            }
        ),
        encoding="utf-8",
    )
    (path / "pipeline.json").write_text(
        json.dumps(
            {
                "stages": [
                    {"name": "extract", "tier": "T1", "model_type": "reasoning", "effort": "medium"},
                    {"name": "stance_judge", "tier": "T1", "model_type": "reasoning", "effort": "xhigh"},
                    {"name": "oppose_verify", "tier": "T1", "model_type": "chat", "effort": "max"},
                    {"name": "merge_judge", "tier": "T1", "model_type": "chat", "effort": "medium"},
                    {"name": "gold_label", "tier": "T1", "model_type": "secondary", "effort": "xhigh"},
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
    inputs: list[str] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        envs.append(kwargs["env"])
        inputs.append(kwargs["input"])
        return subprocess.CompletedProcess(cmd, 0, stdout=_payload(1, 0), stderr="")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    analyses = CodexProvider(_settings(tmp_path)).analyse_batch(
        ["Acme 0 shipped a governed platform.", "Acme 1 shipped a governed platform."],
        policy={"extraction": {"max_claims_per_chunk": 3}},
    )

    assert [analysis.entities[0].name for analysis in analyses] == ["Acme 0", "Acme 1"]
    assert calls[0][:2] == ["codex-test", "exec"]
    assert "--ephemeral" in calls[0]
    assert "--ignore-user-config" in calls[0]
    assert "--ignore-rules" in calls[0]
    assert "--skip-git-repo-check" in calls[0]
    assert calls[0][calls[0].index("--sandbox") + 1] == "read-only"
    assert calls[0][calls[0].index("--model") + 1] == "gpt-5.6-sol"
    assert calls[0][calls[0].index("-c") + 1] == 'approval_policy="never"'
    assert 'model_reasoning_effort="medium"' in calls[0]
    assert calls[0][-1] == "-"
    assert "OPENOYSTER_API_KEY" not in envs[0]
    assert not any("[CHUNK 0]" in part for part in calls[0])
    assert "[CHUNK 0]" in inputs[0]
    assert "[/CHUNK 1]" in inputs[0]
    log_file = next((tmp_path / "codex-config" / "logs" / "extract").glob("*.json"))
    assert json.loads(log_file.read_text(encoding="utf-8"))["effort"] == "medium"


def test_codex_provider_repairs_malformed_json_once(monkeypatch, tmp_path):
    responses = ["```json\nnot-json\n```", _payload(0)]
    calls: list[list[str]] = []
    prompts: list[str] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        prompts.append(kwargs["input"])
        return subprocess.CompletedProcess(cmd, 0, stdout=responses.pop(0), stderr="")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)
    analyses = CodexProvider(_settings(tmp_path)).analyse_batch(["Acme 0 shipped a governed platform."])

    assert analyses[0].claims[0].text == "Acme 0 shipped a governed platform."
    assert len(prompts) == 2
    assert "[INVALID RESPONSE]" in prompts[1]
    assert all('model_reasoning_effort="medium"' in call for call in calls)


def test_codex_provider_omits_reasoning_effort_when_stage_has_no_effort(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    pipeline_path = Path(settings.codex_config_dir) / "pipeline.json"
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    next(stage for stage in pipeline["stages"] if stage["name"] == "stance_judge").pop("effort")
    pipeline_path.write_text(json.dumps(pipeline), encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)

    CodexProvider(settings).query_json("judge this", "stance_judge")

    assert not any(arg.startswith("model_reasoning_effort=") for arg in calls[0])


def test_codex_provider_rejects_invalid_reasoning_effort_before_subprocess(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    pipeline_path = Path(settings.codex_config_dir) / "pipeline.json"
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    next(stage for stage in pipeline["stages"] if stage["name"] == "extract")["effort"] = "ultra"
    pipeline_path.write_text(json.dumps(pipeline), encoding="utf-8")

    def fake_run(cmd, **kwargs):
        del cmd, kwargs
        pytest.fail("subprocess must not run for an invalid effort")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)

    with pytest.raises(ExtractionUnavailable, match=r"invalid reasoning effort.*ultra"):
        CodexProvider(settings).analyse_batch(["Acme shipped a platform."])


def test_repository_codex_config_uses_graded_two_model_policy():
    """Judgement/verification/reasoning stay on gpt-5.6-sol; only the bounded
    generative deliberation model_type may use gpt-5.6-terra.

    Rationale: the 2026-07-14 D2 live run deliberately routed beliefs/options/
    scenarios to Terra while Sol kept critic/decision/verification
    (experiments/opencrab_pack_decision/RESULTS.md). This supersedes the
    2026-07-10 single-model policy commit 73e4a86.
    """
    root = Path(__file__).parents[1]
    models = json.loads((root / ".codex-llm" / "models.json").read_text(encoding="utf-8"))
    pipeline = json.loads((root / ".codex-llm" / "pipeline.json").read_text(encoding="utf-8"))

    routing = {key: value for key, value in models.items() if not key.startswith("_")}
    assert set(routing.values()) <= {"gpt-5.6-sol", "gpt-5.6-terra"}
    assert {key for key, value in routing.items() if value == "gpt-5.6-terra"} <= {"deliberation"}
    assert models["_policy"].startswith("graded two-model policy: gpt-5.6-sol")
    assert {stage["name"]: stage["effort"] for stage in pipeline["stages"]} == {
        "extract": "medium",
        "stance_judge": "xhigh",
        "oppose_verify": "max",
        "merge_judge": "medium",
        "gold_label": "xhigh",
        "pack_answer": "medium",
        "retrieval_query_expansion": "low",
        "deliberation_beliefs": "high",
        "deliberation_options": "high",
        "deliberation_scenarios": "high",
        "deliberation_critic": "high",
        "deliberation_decision": "high",
    }
    assert next(stage for stage in pipeline["stages"] if stage["name"] == "pack_answer")[
        "model_type"
    ] == "reasoning"
    for name in ("deliberation_critic", "deliberation_decision"):
        stage = next(item for item in pipeline["stages"] if item["name"] == name)
        assert stage["model_type"] == "reasoning", f"{name} must stay on the sol judgement tier"


def test_codex_provider_raises_unavailable_after_repair_failure(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="not-json", stderr="")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)

    with pytest.raises(ExtractionUnavailable, match="schema repair"):
        CodexProvider(_settings(tmp_path)).analyse_batch(["Acme shipped a platform."])


def test_codex_provider_raises_unavailable_on_nonzero_exit(monkeypatch, tmp_path):
    secret_prompt_marker = "retrieved chunk secret marker"
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 2, stdout="secret stdout", stderr="secret stderr")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)

    with pytest.raises(ExtractionUnavailable, match="codex exited with 2"):
        CodexProvider(_settings(tmp_path)).analyse_batch([secret_prompt_marker])

    log_file = next((tmp_path / "codex-config" / "logs" / "extract").glob("*.json"))
    log_text = log_file.read_text(encoding="utf-8")
    assert not any(secret_prompt_marker in part for part in calls[0])
    assert "secret stdout" not in log_text
    assert "secret stderr" not in log_text
    assert secret_prompt_marker not in log_text
    assert "prompt_preview" not in log_text
    assert "prompt_sha256" in log_text


def test_codex_provider_query_json_logs_hash_not_raw_prompt(monkeypatch, tmp_path):
    secret_prompt_marker = "stance judge chunk secret marker"
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)

    payload = CodexProvider(_settings(tmp_path)).query_json(secret_prompt_marker, "stance_judge")

    assert payload == {"ok": True}
    log_file = next((tmp_path / "codex-config" / "logs" / "stance_judge").glob("*.json"))
    log_text = log_file.read_text(encoding="utf-8")
    assert not any(secret_prompt_marker in part for part in calls[0])
    assert secret_prompt_marker not in log_text
    assert "prompt_preview" not in log_text
    assert "prompt_sha256" in log_text


def test_codex_provider_raises_unavailable_on_timeout(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30, output="partial")

    monkeypatch.setattr("openoyster.llm.subprocess.run", fake_run)

    with pytest.raises(ExtractionUnavailable, match="timed out"):
        CodexProvider(_settings(tmp_path)).analyse_batch(["Acme shipped a platform."])
