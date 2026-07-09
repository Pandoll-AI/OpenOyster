from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from openoyster.llm import StubProvider
from openoyster.policies import DEFAULT_POLICY
from openoyster.schemas import EntityDraft, HypothesisDraft, SignalDraft, TextAnalysis
from openoyster.services.evaluation import evaluate_counter_evidence, evaluate_goldset
from openoyster.services.evaluation_report import write_eval_outputs

FIXTURE_ROOT = Path(__file__).parent / "goldset_fixtures"
DOCS_DIR = FIXTURE_ROOT / "docs"
LABELS_DIR = FIXTURE_ROOT / "labels"


class ControlledProvider(StubProvider):
    name = "controlled"

    def analyse_batch(self, texts: list[str], policy: dict[str, Any] | None = None) -> list[TextAnalysis]:
        del policy
        return [self._analysis_for(text) for text in texts]

    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        del prompt, stage
        return {"contradicts": False, "reasoning": "unused"}

    def _analysis_for(self, text: str) -> TextAnalysis:
        if "Acme" in text:
            return TextAnalysis(
                entities=[
                    EntityDraft(name="Acme", kind="organisation"),
                    EntityDraft(name="Atlas AI", kind="product"),
                    EntityDraft(name="ExtraCorp", kind="organisation"),
                ],
                signals=[
                    SignalDraft(signal_type="product_release", summary="Acme released Atlas AI."),
                    SignalDraft(signal_type="strategy", summary="Acme is repositioning Atlas AI."),
                ],
                hypotheses=[
                    HypothesisDraft(
                        claim="Atlas AI is newly available.",
                        quoted_evidence="Acme released Atlas AI",
                    ),
                    HypothesisDraft(
                        claim="Atlas AI has an unsupported claim.",
                        quoted_evidence="not in source",
                    ),
                ],
                provider=self.name,
                model="fixture-model",
            )
        return TextAnalysis(
            entities=[
                EntityDraft(name="오픈오이스터", kind="organisation"),
                EntityDraft(name="평가 기능", kind="product"),
                EntityDraft(name="누락된회사", kind="organisation"),
            ],
            signals=[
                SignalDraft(signal_type="strategy", summary="골드셋 평가 기능을 도입했다."),
                SignalDraft(signal_type="governance", summary="검수 절차는 사람 검토 전이다."),
            ],
            hypotheses=[
                HypothesisDraft(
                    claim="골드셋 평가 기능이 도입됐다.",
                    quoted_evidence="골드셋 평가 기능을 도입했다",
                )
            ],
            provider=self.name,
            model="fixture-model",
        )


class StringFalseAuditProvider(StubProvider):
    def query_json(self, prompt: str, stage: str) -> dict[str, Any]:
        if stage == "gold_label":
            return {"contradicts": "false", "reasoning": "malformed auditor bool", "model": "test-double"}
        return super().query_json(prompt, stage)


def _policy() -> dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    policy["extraction"]["chunk_size"] = 2000
    policy["extraction"]["chunk_overlap"] = 100
    return policy


def test_evaluate_goldset_computes_metrics_by_language() -> None:
    report = evaluate_goldset(ControlledProvider(), docs_dir=DOCS_DIR, labels_dir=LABELS_DIR, policy=_policy())

    assert report.docs_evaluated == 2
    assert report.metrics["overall"].entity_recall_core == pytest.approx(1.0)
    assert report.metrics["overall"].entity_precision == pytest.approx(4 / 6)
    assert report.metrics["overall"].signal_type_f1 == pytest.approx(0.75)
    assert report.metrics["overall"].quote_existence_rate == pytest.approx(2 / 3)
    assert report.metrics["en"].signal_type_f1 == pytest.approx(0.5)
    assert report.metrics["ko"].signal_type_f1 == pytest.approx(1.0)
    assert report.review_status_counts["unreviewed"] == 2

    en_detail = next(item for item in report.per_doc if item.doc_id == "fixture_en")
    assert en_detail.missing_signal_types == ["risk"]
    assert en_detail.extra_signal_types == ["strategy"]
    assert en_detail.fabricated_quotes == ["not in source"]


def test_evaluate_goldset_skips_documents_without_labels(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    labels_dir = tmp_path / "labels"
    shutil.copytree(DOCS_DIR, docs_dir)
    shutil.copytree(LABELS_DIR, labels_dir)
    (labels_dir / "fixture_ko.json").unlink()

    report = evaluate_goldset(ControlledProvider(), docs_dir=docs_dir, labels_dir=labels_dir, policy=_policy())

    assert report.docs_evaluated == 1
    assert [(item.doc_id, item.reason) for item in report.skipped_documents] == [
        ("fixture_ko", "missing label")
    ]


def test_eval_report_writes_markdown_and_raw_json(tmp_path: Path) -> None:
    report = evaluate_goldset(ControlledProvider(), docs_dir=DOCS_DIR, labels_dir=LABELS_DIR, policy=_policy())
    report_path = tmp_path / "docs" / "EVAL_REPORT.md"
    results_dir = tmp_path / "goldset" / "results"

    raw_path = write_eval_outputs(gold_report=report, report_path=report_path, results_dir=results_dir)

    assert report_path.exists()
    assert "라벨은 LLM-judge(gpt-5.4) 초벌, 사람 미검수" in report_path.read_text(encoding="utf-8")
    assert raw_path.parent == results_dir
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert payload["gold"]["docs_evaluated"] == 2


def test_evaluate_counter_evidence_audits_oppose_edges(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "counter_doc.json").write_text(
        json.dumps(
            {
                "id": "counter_doc",
                "title": "Counter evidence",
                "url": "https://example.test/counter",
                "source": "fixture",
                "language": "en",
                "kind": "article",
                "collected_at": "2026-07-10T00:00:00Z",
                "text": "No evidence supports immediate hiring. Acme pauses hiring.",
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_counter_evidence(StubProvider(), docs_dir=docs_dir, policy=_policy(), cycles=2)

    assert report.oppose_edges == 1
    assert report.measurable is True
    assert report.precision == pytest.approx(1.0)
    assert report.audit_model_note


def test_counter_audit_requires_json_boolean(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "counter_doc.json").write_text(
        json.dumps(
            {
                "id": "counter_doc",
                "title": "Counter evidence",
                "url": "https://example.test/counter",
                "source": "fixture",
                "language": "en",
                "kind": "article",
                "collected_at": "2026-07-10T00:00:00Z",
                "text": "No evidence supports immediate hiring. Acme pauses hiring.",
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_counter_evidence(StringFalseAuditProvider(), docs_dir=docs_dir, policy=_policy(), cycles=2)

    assert report.oppose_edges == 1
    assert report.precision == pytest.approx(0.0)

