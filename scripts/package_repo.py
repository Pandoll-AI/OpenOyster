from __future__ import annotations

import hashlib
import os
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "pyproject.toml").open("rb") as stream:
    VERSION = tomllib.load(stream)["project"]["version"]

OUT = Path(os.environ.get("OPENOYSTER_PACKAGE_OUT", ROOT.parent / f"OpenOyster-{VERSION}-audited.zip"))
CHECKSUM = OUT.with_suffix(OUT.suffix + ".sha256")
ROOT_NAME = "OpenOyster"
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "workspace",
    "dist",
    "build",
}
EXCLUDE_FILES = {".coverage", ".env", OUT.name, CHECKSUM.name}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3"}


def included(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDE_DIRS for part in relative.parts):
        return False
    if path.name in EXCLUDE_FILES or path.suffix.casefold() in EXCLUDE_SUFFIXES:
        return False
    return path.is_file()


files = sorted((path for path in ROOT.rglob("*") if included(path)), key=lambda item: item.as_posix())
OUT.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        info = zipfile.ZipInfo(f"{ROOT_NAME}/{relative}", date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o644 & 0xFFFF) << 16
        archive.writestr(info, path.read_bytes())

digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
CHECKSUM.write_text(f"{digest}  {OUT.name}\n", encoding="utf-8")
print(f"Wrote {OUT}")
print(f"SHA256 {digest}")
print(f"Wrote {CHECKSUM}")
