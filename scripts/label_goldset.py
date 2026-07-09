#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pydantic>=2.8,<3",
#     "typer>=0.12,<1",
# ]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/label_goldset.py [--only DOC_ID] [--force]
# 3. Or run with the project venv:
#      .venv/bin/python scripts/label_goldset.py [--only DOC_ID] [--force]
# ──────────────────

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Literal

import typer
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ROOT: Final = Path(__file__).resolve().parents[1]
DOCS_DIR: Final = ROOT / "goldset" / "docs"
LABELS_DIR: Final = ROOT / "goldset" / "labels"
MODELS_PATH: Final = ROOT / ".codex-llm" / "models.json"
LABELED_AT: Final = "2026-07-10"
MAX_BATCH_CHARS: Final = 12_000
BATCH_SIZE: Final = 3
CODEX_TIMEOUT_SECONDS: Final = 480

EntityKind = Literal["organisation", "person", "product", "technology", "regulation", "place", "other"]
EntitySalience = Literal["core", "peripheral"]
SignalType = Literal[
    "hiring",
    "product_release",
    "funding",
    "regulation",
    "incident",
    "risk",
    "governance",
    "strategy",
    "demand",
    "research",
    "other",
]
Language = Literal["ko", "en"]
ReviewStatus = Literal["unreviewed"]

LABEL_PROMPT: Final = """
You are building the answer key to AUDIT an extraction system. List what a careful analyst must not miss, and only what is genuinely present.
Return one label per input doc_id. Entity names must use the original source spelling; Korean source names stay Korean. Use core salience only for central subject entities.
Signals must cover important events, claims, risks, governance choices, strategy shifts, demand signals, research findings, releases, hiring, funding, regulations, or incidents. Each anchor_quote must be a short verbatim substring from the document text.
Return only valid JSON, no markdown, no commentary, matching this schema:
{"labels":[{"doc_id":"string","expected_entities":[{"name":"string","kind":"organisation|person|product|technology|regulation|place|other","salience":"core|peripheral"}],"expected_signals":[{"signal_type":"hiring|product_release|funding|regulation|incident|risk|governance|strategy|demand|research|other","summary":"string","anchor_quote":"verbatim source substring"}],"notes":"string"}]}
""".strip()


class ModelsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    secondary: str = Field(min_length=1)


class SourceDoc(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    doc_id: str = Field(alias="id", min_length=1)
    title: str
    url: str
    source: str
    language: Language
    kind: str
    collected_at: str
    text: str = Field(min_length=1)


class ExpectedEntity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = Field(min_length=1)
    kind: EntityKind
    salience: EntitySalience


class ExpectedSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    signal_type: SignalType
    summary: str = Field(min_length=1)
    anchor_quote: str
    anchor_verified: bool = True


class LabelItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    doc_id: str = Field(min_length=1)
    expected_entities: list[ExpectedEntity]
    expected_signals: list[ExpectedSignal]
    notes: str


class BatchLabels(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    labels: list[LabelItem]


class StoredLabel(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    language: Language
    labeler_model: str
    labeled_at: str
    review_status: ReviewStatus
    expected_entities: list[ExpectedEntity]
    expected_signals: list[ExpectedSignal]
    notes: str


@dataclass(frozen=True, slots=True)
class LabelerError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def label_path(doc_id: str) -> Path:
    return LABELS_DIR / f"{doc_id}.json"


def load_model_name() -> str:
    return ModelsConfig.model_validate_json(MODELS_PATH.read_text(encoding="utf-8")).secondary


def load_documents(only: str | None) -> list[SourceDoc]:
    docs = [SourceDoc.model_validate_json(path.read_text(encoding="utf-8")) for path in sorted(DOCS_DIR.glob("*.json"))]
    if only is None:
        return docs
    filtered = [doc for doc in docs if doc.doc_id == only]
    if filtered:
        return filtered
    raise LabelerError(message=f"unknown doc id: {only}")


def select_documents(docs: Sequence[SourceDoc], force: bool) -> list[SourceDoc]:
    if force:
        return list(docs)
    return [doc for doc in docs if not label_path(doc.doc_id).exists()]


def make_batches(docs: Sequence[SourceDoc]) -> list[list[SourceDoc]]:
    batches: list[list[SourceDoc]] = []
    current: list[SourceDoc] = []
    current_chars = 0
    for doc in docs:
        doc_chars = len(doc.text)
        if doc_chars > MAX_BATCH_CHARS:
            if current:
                batches.append(current)
                current = []
                current_chars = 0
            batches.append([doc])
            continue
        should_flush = current and (len(current) >= BATCH_SIZE or current_chars + doc_chars > MAX_BATCH_CHARS)
        if should_flush:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(doc)
        current_chars += doc_chars
    if current:
        batches.append(current)
    return batches


def build_prompt(docs: Sequence[SourceDoc]) -> str:
    docs_json = json.dumps([doc.model_dump(by_alias=True, mode="json") for doc in docs], ensure_ascii=False, indent=2)
    return f"{LABEL_PROMPT}\n\nDocuments:\n{docs_json}"


def remove_code_fences(text: str) -> str:
    return "\n".join(line for line in text.strip().splitlines() if not line.strip().startswith("```"))


def extract_json_object(stdout: str) -> str:
    cleaned = remove_code_fences(stdout)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise LabelerError(message="stdout did not contain a JSON object")
    return cleaned[start : end + 1]


def call_codex(prompt: str, model: str) -> BatchLabels:
    with tempfile.TemporaryDirectory(prefix="goldset-label-") as tmp_dir:
        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            tmp_dir,
            "-c",
            'approval_policy="never"',
            "--model",
            model,
            prompt,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=CODEX_TIMEOUT_SECONDS,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise LabelerError(message=f"codex timed out after {CODEX_TIMEOUT_SECONDS}s") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
        raise LabelerError(message=f"codex exited {result.returncode}: {' '.join(detail)}")
    return BatchLabels.model_validate_json(extract_json_object(result.stdout))


def label_batch(docs: Sequence[SourceDoc], model: str) -> BatchLabels:
    prompt = build_prompt(docs)
    last_error = "unknown failure"
    for attempt in range(1, 3):
        try:
            return call_codex(prompt=prompt, model=model)
        except (LabelerError, ValidationError) as exc:
            last_error = str(exc)
            doc_ids = ", ".join(doc.doc_id for doc in docs)
            print(f"batch failed attempt {attempt}/2 for {doc_ids}: {last_error}")
    raise LabelerError(message=last_error)


def verified_signals(signals: Iterable[ExpectedSignal], text: str) -> list[ExpectedSignal]:
    return [
        signal.model_copy(update={"anchor_verified": bool(signal.anchor_quote) and signal.anchor_quote in text})
        for signal in signals
    ]


def write_label(doc: SourceDoc, item: LabelItem, model: str) -> None:
    stored = StoredLabel(
        doc_id=doc.doc_id,
        language=doc.language,
        labeler_model=model,
        labeled_at=LABELED_AT,
        review_status="unreviewed",
        expected_entities=item.expected_entities,
        expected_signals=verified_signals(item.expected_signals, doc.text),
        notes=item.notes,
    )
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    label_path(doc.doc_id).write_text(stored.model_dump_json(indent=2) + "\n", encoding="utf-8")


def save_batch(batch: Sequence[SourceDoc], labels: BatchLabels, model: str) -> list[str]:
    by_doc_id = {item.doc_id: item for item in labels.labels}
    failed: list[str] = []
    for doc in batch:
        item = by_doc_id.get(doc.doc_id)
        if item is None:
            failed.append(f"{doc.doc_id}: labeler omitted document from response")
            continue
        write_label(doc=doc, item=item, model=model)
        false_count = sum(1 for signal in verified_signals(item.expected_signals, doc.text) if not signal.anchor_verified)
        print(f"wrote {label_path(doc.doc_id)} ({false_count} unverified anchors)")
    return failed


def run(only: str | None, force: bool) -> int:
    model = load_model_name()
    docs = load_documents(only=only)
    pending = select_documents(docs=docs, force=force)
    batches = make_batches(pending)
    failed: list[str] = []
    print(f"labeler model: {model}")
    print(f"documents found: {len(docs)}; documents to label: {len(pending)}; batches: {len(batches)}")
    for index, batch in enumerate(batches, start=1):
        doc_ids = ", ".join(doc.doc_id for doc in batch)
        print(f"processing batch {index}/{len(batches)}: {doc_ids}")
        try:
            labels = label_batch(docs=batch, model=model)
        except LabelerError as exc:
            failed.extend(f"{doc.doc_id}: {exc}" for doc in batch)
            continue
        failed.extend(save_batch(batch=batch, labels=labels, model=model))
    if failed:
        print("skipped/failed documents:")
        for failure in failed:
            print(f"- {failure}")
        return 1
    print("completed without skipped documents")
    return 0


def main(
    only: Annotated[str | None, typer.Option("--only", help="Label only one document id.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Regenerate labels that already exist.")] = False,
) -> None:
    try:
        raise typer.Exit(run(only=only, force=force))
    except LabelerError as exc:
        print(str(exc))
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    typer.run(main)
