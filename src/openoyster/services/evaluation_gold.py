from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..llm import LLMProvider
from ..llm_contracts import ExtractionUnavailable
from ..schemas import EntityDraft, SignalDraft
from ..utils import normalise_text
from .chunking import chunk_text
from .evaluation_common import f1, json_path_for_id, load_documents, most_common, safe_div
from .evaluation_models import (
    GoldDocDetail,
    GoldEvalReport,
    GoldLabel,
    MetricBlock,
    SkippedDocument,
)


@dataclass
class _Predictions:
    entities: list[EntityDraft] = field(default_factory=list)
    signals: list[SignalDraft] = field(default_factory=list)
    quotes_total: int = 0
    quotes_verified: int = 0
    fabricated_quotes: list[str] = field(default_factory=list)
    models: Counter[str] = field(default_factory=Counter)


@dataclass
class _MetricAccumulator:
    docs: int = 0
    core_entities_matched: int = 0
    core_entities_total: int = 0
    predicted_entities_matched: int = 0
    predicted_entities_total: int = 0
    signal_type_tp: int = 0
    signal_type_fp: int = 0
    signal_type_fn: int = 0
    quotes_verified: int = 0
    quotes_total: int = 0

    def add(self, detail: GoldDocDetail) -> None:
        self.docs += 1
        self.core_entities_matched += detail.core_entities_matched
        self.core_entities_total += detail.core_entities_total
        self.predicted_entities_matched += detail.predicted_entities_matched
        self.predicted_entities_total += detail.predicted_entities_total
        self.signal_type_tp += detail.signal_type_tp
        self.signal_type_fp += detail.signal_type_fp
        self.signal_type_fn += detail.signal_type_fn
        self.quotes_verified += detail.quotes_verified
        self.quotes_total += detail.quotes_total

    def to_block(self) -> MetricBlock:
        signal_precision = safe_div(self.signal_type_tp, self.signal_type_tp + self.signal_type_fp)
        signal_recall = safe_div(self.signal_type_tp, self.signal_type_tp + self.signal_type_fn)
        return MetricBlock(
            docs=self.docs,
            entity_recall_core=safe_div(self.core_entities_matched, self.core_entities_total),
            entity_precision=safe_div(self.predicted_entities_matched, self.predicted_entities_total),
            signal_type_f1=f1(signal_precision, signal_recall),
            quote_existence_rate=safe_div(self.quotes_verified, self.quotes_total),
            core_entities_matched=self.core_entities_matched,
            core_entities_total=self.core_entities_total,
            predicted_entities_matched=self.predicted_entities_matched,
            predicted_entities_total=self.predicted_entities_total,
            signal_type_tp=self.signal_type_tp,
            signal_type_fp=self.signal_type_fp,
            signal_type_fn=self.signal_type_fn,
            quotes_verified=self.quotes_verified,
            quotes_total=self.quotes_total,
        )


def evaluate_goldset(
    provider: LLMProvider,
    *,
    docs_dir: Path,
    labels_dir: Path,
    policy: dict[str, Any],
    doc_limit: int | None = None,
) -> GoldEvalReport:
    settings = get_settings()
    documents = load_documents(docs_dir)
    if doc_limit is not None:
        documents = documents[:doc_limit]

    accumulators = {key: _MetricAccumulator() for key in ("overall", "ko", "en")}
    skipped: list[SkippedDocument] = []
    per_doc: list[GoldDocDetail] = []
    review_counts: Counter[str] = Counter()
    labeler_models: Counter[str] = Counter()
    analysis_models: Counter[str] = Counter()

    for document in documents:
        try:
            label_path = json_path_for_id(labels_dir, document.id)
        except ValueError:
            skipped.append(SkippedDocument(doc_id=document.id, reason="invalid doc_id"))
            continue
        if not label_path.exists():
            skipped.append(SkippedDocument(doc_id=document.id, reason="missing label"))
            continue
        label = _load_label(label_path)
        review_counts[label.review_status] += 1
        if label.labeler_model:
            labeler_models[label.labeler_model] += 1
        try:
            predictions = _predict_document(provider, document.text, policy, settings.codex_batch_size)
        except ExtractionUnavailable as exc:
            skipped.append(SkippedDocument(doc_id=document.id, reason=f"provider unavailable: {exc.reason}"))
            continue

        analysis_models.update(predictions.models)
        detail = _score_document(document.id, document.title, document.language, label, predictions)
        per_doc.append(detail)
        accumulators["overall"].add(detail)
        if detail.language in {"ko", "en"}:
            accumulators[detail.language].add(detail)

    return GoldEvalReport(
        provider=getattr(provider, "name", provider.__class__.__name__),
        model=most_common(analysis_models),
        docs_seen=len(documents),
        docs_evaluated=len(per_doc),
        skipped_documents=skipped,
        metrics={key: accumulator.to_block() for key, accumulator in accumulators.items()},
        per_doc=per_doc,
        review_status_counts=dict(review_counts),
        labeler_model_counts=dict(labeler_models),
    )


def _load_label(path: Path) -> GoldLabel:
    return GoldLabel.model_validate_json(path.read_text(encoding="utf-8"))


def _predict_document(
    provider: LLMProvider,
    text: str,
    policy: dict[str, Any],
    batch_size: int,
) -> _Predictions:
    extraction = policy["extraction"]
    chunks = chunk_text(
        text,
        chunk_size=int(extraction["chunk_size"]),
        overlap=int(extraction["chunk_overlap"]),
    )
    predictions = _Predictions()
    for index in range(0, len(chunks), batch_size):
        batch = chunks[index : index + batch_size]
        analyses = provider.analyse_batch(batch, policy=policy)
        if len(analyses) != len(batch):
            raise ExtractionUnavailable(f"provider returned {len(analyses)} analyses for {len(batch)} chunks")
        for chunk_source, analysis in zip(batch, analyses, strict=True):
            predictions.entities.extend(analysis.entities)
            predictions.signals.extend(analysis.signals)
            predictions.models[analysis.model] += 1
            for hypothesis in analysis.hypotheses:
                quote = hypothesis.quoted_evidence or ""
                predictions.quotes_total += 1
                if quote and quote in chunk_source:
                    predictions.quotes_verified += 1
                else:
                    predictions.fabricated_quotes.append(quote)
    return predictions


def _score_document(
    doc_id: str,
    title: str,
    fallback_language: str,
    label: GoldLabel,
    predictions: _Predictions,
) -> GoldDocDetail:
    gold_core = [item.name for item in label.expected_entities if item.salience == "core"]
    gold_all = [item.name for item in label.expected_entities]
    predicted_entities = _unique(entity.name for entity in predictions.entities)
    missing_core = [name for name in gold_core if not _matches_any(name, predicted_entities)]
    predicted_matched = sum(1 for name in predicted_entities if _matches_any(name, gold_all))

    gold_signal_types = {signal.signal_type for signal in label.expected_signals}
    predicted_signal_types = {signal.signal_type for signal in predictions.signals}
    missing_signal_types = sorted(gold_signal_types - predicted_signal_types)
    extra_signal_types = sorted(predicted_signal_types - gold_signal_types)
    true_positives = len(gold_signal_types & predicted_signal_types)

    return GoldDocDetail(
        doc_id=doc_id,
        language=label.language or fallback_language,
        title=title,
        missing_core_entities=missing_core,
        missing_signal_types=missing_signal_types,
        extra_signal_types=extra_signal_types,
        fabricated_quotes=predictions.fabricated_quotes,
        core_entities_matched=len(gold_core) - len(missing_core),
        core_entities_total=len(gold_core),
        predicted_entities_matched=predicted_matched,
        predicted_entities_total=len(predicted_entities),
        signal_type_tp=true_positives,
        signal_type_fp=len(extra_signal_types),
        signal_type_fn=len(missing_signal_types),
        quotes_verified=predictions.quotes_verified,
        quotes_total=predictions.quotes_total,
    )


def _matches_any(candidate: str, names: list[str]) -> bool:
    normalised = _normalise_for_match(candidate)
    return any(_name_matches(normalised, _normalise_for_match(name)) for name in names)


def _name_matches(left: str, right: str) -> bool:
    return bool(left and right and (left == right or left in right or right in left))


def _normalise_for_match(text: str) -> str:
    return normalise_text(text).casefold()


def _unique(names: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        key = _normalise_for_match(str(name))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(str(name))
    return result
