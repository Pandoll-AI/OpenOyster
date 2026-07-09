from __future__ import annotations

from collections import Counter
from pathlib import Path

from .evaluation_models import GoldDocument


def load_documents(docs_dir: Path) -> list[GoldDocument]:
    return sorted(
        (GoldDocument.model_validate_json(path.read_text(encoding="utf-8")) for path in docs_dir.glob("*.json")),
        key=lambda item: item.id,
    )


def json_path_for_id(base_dir: Path, item_id: str) -> Path:
    if not item_id or Path(item_id).name != item_id or "\\" in item_id:
        raise ValueError(f"Invalid id for JSON filename: {item_id}")
    root = base_dir.resolve(strict=False)
    path = (root / f"{item_id}.json").resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"JSON path escapes base directory: {item_id}") from exc
    return path


def safe_child_path(base_dir: Path, child_path: Path) -> Path:
    root = base_dir.resolve(strict=False)
    path = child_path.resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes base directory: {child_path}") from exc
    return path


def safe_div(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def f1(precision: float, recall: float) -> float:
    return (2 * precision * recall / (precision + recall)) if precision + recall else 0.0


def most_common(counter: Counter[str]) -> str | None:
    return counter.most_common(1)[0][0] if counter else None
