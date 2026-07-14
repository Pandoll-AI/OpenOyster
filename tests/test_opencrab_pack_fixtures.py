from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENCRAB_ROOT = PROJECT_ROOT.parent / "OpenCrab"
FIXTURE_ROOT = PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f1-minimal"
FULL_LAYOUT_FIXTURE_ROOT = (
    PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout"
)
INVALID_ARCHIVES_ROOT = (
    PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives"
)
BROKEN_PROVENANCE_ROOT = (
    PROJECT_ROOT / "tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance"
)
P0_F2_EXPECTED_DIGESTS = {
    "README.md": (
        "a40058456034619ba0b358128ac1a48af2a502e5ccacef8455ff5b31b38aa3bb"
    ),
    "community_reports.json": (
        "c0e2961a20e027539b9744105a58d41514f107f531e950906c06d5b10c73d5ff"
    ),
    "evidence/index.jsonl": (
        "fd1391ceb27a6b705b74ad779d1df4e0b837af34e90547521b7e0adae8249bd7"
    ),
    "graph/edges.jsonl": (
        "c147e139c50ea1f0f801f0b05b1d3bb5f55ef16114e049e9158abc5f050f9533"
    ),
    "graph/nodes.jsonl": (
        "9a147ad0dcbda678d756a45d64c4b0cbf94ab5b7827f82e55678ca76bcb84e44"
    ),
    "manifest.json": (
        "4b37c9eab24e5ac3f709aeccb200a7e897727846100bbfc457026a201bcd7647"
    ),
    "neo4j/export_status.json": (
        "1819a5164ca9f7e8d17f2e69e000141136ddb26254767517c37158c72d59e495"
    ),
    "neo4j/import.cypher": (
        "390ef78b9e5f7db3f4c2411b537113009ca6227937aa990404985d70a9fcdd81"
    ),
    "neo4j/opencrab_ingest.jsonl": (
        "7c95fe94a6eab5f67aab99910db2cd15dab4c327357d4a31cbdd0fdf867eb7f3"
    ),
    "quality/report.json": (
        "f95ceeb4a5e2efa2edc23fd82468128407e07d3c920be40643931b6c14eb6599"
    ),
    "sample_queries.json": (
        "0495fa6862e62316dde55ca63a918cf33634cf36420687981f173263b2ee506d"
    ),
}
INVALID_ARCHIVE_NAMES = (
    "path-traversal.zip",
    "absolute-path.zip",
    "symlink-escape.zip",
    "duplicate-path.zip",
    "case-collision.zip",
    "compression-ratio-limit.zip",
    "file-count-limit.zip",
    "uncompressed-bytes-limit.zip",
)
ARCHIVE_PRIMARY_ISSUE_BY_NAME = {
    "path-traversal.zip": "path_traversal",
    "absolute-path.zip": "absolute_path",
    "symlink-escape.zip": "symlink_escape",
    "duplicate-path.zip": "duplicate_path",
    "case-collision.zip": "case_collision",
    "compression-ratio-limit.zip": "compression_ratio_limit",
    "file-count-limit.zip": "file_count_limit",
    "uncompressed-bytes-limit.zip": "uncompressed_bytes_limit",
}
DEFAULT_ARCHIVE_LIMITS = {
    "max_compression_ratio": 100,
    "max_file_count": 32,
    "max_uncompressed_bytes": 65536,
}
# Reference archive preflight is metadata/symlink-payload only. It must never call
# ZipFile.extract / extractall or write archive members to a Pack store.
REFERENCE_ARCHIVE_PREFLIGHT_MODE = "metadata_and_symlink_payload_only"
# Compatible validator (opencrab.pack.validation.REQUIRED_FILES) enforces only these four.
COMPATIBLE_VALIDATOR_REQUIRED_FILES = frozenset(
    {
        "manifest.json",
        "graph/nodes.jsonl",
        "graph/edges.jsonl",
        "evidence/index.jsonl",
    }
)
EXPECTED_SOURCE_FILES = COMPATIBLE_VALIDATOR_REQUIRED_FILES
# Documented Pack v1 strict layout (opencrab-pack-v1.md Required Layout).
DOCUMENTED_STRICT_FULL_LAYOUT_FILES = frozenset(
    {
        "manifest.json",
        "graph/nodes.jsonl",
        "graph/edges.jsonl",
        "evidence/index.jsonl",
        "quality/report.json",
        "neo4j/import.cypher",
        "neo4j/opencrab_ingest.jsonl",
        "neo4j/export_status.json",
        "README.md",
        "sample_queries.json",
        "community_reports.json",
    }
)
P0_F1_EXPECTED_DIGESTS = {
    "evidence/index.jsonl": (
        "fd1391ceb27a6b705b74ad779d1df4e0b837af34e90547521b7e0adae8249bd7"
    ),
    "graph/edges.jsonl": (
        "c147e139c50ea1f0f801f0b05b1d3bb5f55ef16114e049e9158abc5f050f9533"
    ),
    "graph/nodes.jsonl": (
        "9a147ad0dcbda678d756a45d64c4b0cbf94ab5b7827f82e55678ca76bcb84e44"
    ),
    "manifest.json": (
        "ae3f3d5c71774cac4ae28b79a333f31503e5b847396f6e93562f61f0cff2614a"
    ),
}
# Official validate_pack_static still reports neo4j_import as skip even when
# documented Neo4j artifacts are present; it does not execute import/check.
COMPATIBLE_VALIDATOR_PASS_CHECKS = {
    "layout": "pass",
    "grammar": "pass",
    "schema": "pass",
    "evidence_refs": "pass",
    "broken_edges": "pass",
    "duplicates": "pass",
    "human_review": "pass",
    "neo4j_import": "skip",
}
# Transparent fixture-local pack digest: every documented required file except
# manifest.json, ordered by POSIX relative path. Per file: UTF-8 path + NUL +
# raw bytes + newline. Avoids self-reference through the digest field.
PACK_SHA256_ALGORITHM = "sha256(path_nul_bytes_newline)"
VALIDATOR_SUBPROCESS = """
import json
import sys
from pathlib import Path

opencrab_root = Path(sys.argv[1]).resolve()
fixture_root = Path(sys.argv[2]).resolve()

from opencrab.pack import validation

module_file = validation.__file__
if module_file is None:
    raise RuntimeError("Official OpenCrab validator has no module file.")
try:
    Path(module_file).resolve().relative_to(opencrab_root)
except ValueError as error:
    raise RuntimeError(
        f"OpenCrab validator was not loaded from {opencrab_root}: {module_file}"
    ) from error

report = validation.validate_pack_static(fixture_root, write_report=False)
print(json.dumps(report))
"""


def _fixture_digests(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_issue(
    code: str,
    severity: str,
    message: str,
    *,
    path: str | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    """Shared structured issue shape for test-only oracles."""
    payload: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if path is not None:
        payload["path"] = path
    if record_id is not None:
        payload["record_id"] = record_id
    return payload


def _error_issue_codes(issues: list[dict[str, Any]]) -> list[str]:
    return [issue["code"] for issue in issues if issue["severity"] == "error"]


def _reference_oracle_report(
    issues: list[dict[str, Any]],
    **extra: Any,
) -> dict[str, Any]:
    """Common Phase 0 oracle envelope. Never claims production admission."""
    error_codes = _error_issue_codes(issues)
    report: dict[str, Any] = {
        "status": "fail" if error_codes else "pass",
        "issues": issues,
        "issue_codes": error_codes,
        "production_admission": False,
    }
    report.update(extra)
    return report


def _path_segments(name: str) -> list[str]:
    return [
        segment
        for segment in name.replace("\\", "/").split("/")
        if segment not in {"", "."}
    ]


def _slash_normalize_zip_member_key(name: str) -> str:
    """Slash-normalize a ZIP member name (no Unicode form folding).

    Backslashes become slashes; empty and '.' segments are dropped; '..' is kept
    so traversal remains a separate primary issue. Leading absolute markers
    (POSIX '/' or Windows drive) are preserved so absolute vs relative names do
    not collapse into the same identity key.
    """
    normalized = name.replace("\\", "/")
    drive_absolute = (
        len(normalized) >= 2
        and normalized[1] == ":"
        and normalized[0].isalpha()
    )
    posix_absolute = normalized.startswith("/")
    segments = _path_segments(normalized)
    if drive_absolute:
        drive = f"{normalized[0].upper()}:"
        # segments still include the drive token when present as 'C:' or 'C:foo'
        if segments and len(segments[0]) >= 2 and segments[0][1] == ":":
            tail = segments[1:]
            rest = "/".join(tail)
            return f"{drive}/{rest}" if rest else f"{drive}/"
        rest = "/".join(segments)
        return f"{drive}/{rest}" if rest else f"{drive}/"
    key = "/".join(segments)
    if posix_absolute:
        return f"/{key}" if key else "/"
    return key


def _normalize_zip_member_key(name: str) -> str:
    """Canonical ZIP member identity: slash-normalize then Unicode NFC.

    NFC is applied before duplicate and casefold checks so NFC/NFD spellings of
    the same path share one identity key while slash/path protections remain.
    """
    return unicodedata.normalize("NFC", _slash_normalize_zip_member_key(name))


def _is_absolute_member_path(name: str) -> bool:
    normalized = name.replace("\\", "/").strip()
    if not normalized:
        return False
    # POSIX absolute, UNC (//host/...), or Windows drive (C:/...).
    if normalized.startswith("/"):
        return True
    return len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha()


def _has_path_traversal(name: str) -> bool:
    return any(segment == ".." for segment in _path_segments(name.strip()))


def _is_unsafe_relative_path(relative_path: str) -> bool:
    """Shared absolute/traversal guard for ZIP members and evidence paths."""
    if relative_path is None:
        return True
    relative_path = relative_path.strip()
    if not relative_path:
        return True
    if _is_absolute_member_path(relative_path):
        return True
    if _has_path_traversal(relative_path):
        return True
    pure = PurePosixPath(relative_path.replace("\\", "/"))
    return pure.is_absolute() or ".." in pure.parts


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def _symlink_target_escapes(target: str) -> bool:
    return _is_unsafe_relative_path(target)


def _compression_ratio(info: zipfile.ZipInfo) -> float:
    # Directory/empty members (file_size<=0) are not zip-bomb candidates.
    # compress_size<=0 with positive file_size is treated as infinite ratio
    # so zero-compressed claimed payloads cannot bypass the limit.
    if info.file_size <= 0:
        return 0.0
    if info.compress_size <= 0:
        return float("inf")
    return info.file_size / info.compress_size


def _resolve_under_root(root: Path, relative_path: str) -> Path | None:
    """Resolve relative_path only if it stays under root. Never reads outside."""
    if _is_unsafe_relative_path(relative_path):
        return None
    safe_relative = relative_path.strip().replace("\\", "/")
    root_resolved = root.resolve()
    # Join only the already-validated relative form; resolve() can still follow
    # existing on-disk symlinks under root, so re-check containment after resolve.
    candidate = (root_resolved / safe_relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


def _reference_archive_preflight(
    archive_path: Path,
    *,
    limits: dict[str, int],
) -> dict[str, Any]:
    """Test-only read-only ZIP preflight oracle (Phase 0).

    Inspects central-directory metadata and symlink payload bytes only.
    Never calls ZipFile.extract / extractall and never writes members to disk.
    This is not production Pack admission.
    """
    issues: list[dict[str, Any]] = []
    max_ratio = int(limits["max_compression_ratio"])
    max_files = int(limits["max_file_count"])
    max_uncompressed = int(limits["max_uncompressed_bytes"])

    with zipfile.ZipFile(archive_path, mode="r") as archive:
        infos = list(archive.infolist())
        raw_names = [info.filename for info in infos]

        if len(infos) > max_files:
            issues.append(
                _reference_issue(
                    "file_count_limit",
                    "error",
                    f"Archive member count {len(infos)} exceeds max_file_count={max_files}.",
                )
            )

        total_uncompressed = sum(max(info.file_size, 0) for info in infos)
        if total_uncompressed > max_uncompressed:
            issues.append(
                _reference_issue(
                    "uncompressed_bytes_limit",
                    "error",
                    (
                        f"Total uncompressed bytes {total_uncompressed} exceeds "
                        f"max_uncompressed_bytes={max_uncompressed}."
                    ),
                )
            )

        # Identity checks use slash+NFC keys so '\\' vs '/', '//', '.' segments,
        # and NFC/NFD Unicode spellings cannot bypass duplicate/case detection.
        # nfc_key -> list of pre-NFC slash keys (to separate true duplicates from
        # Unicode-equivalent spellings that fold via NFC).
        seen_normalized: dict[str, list[str]] = {}
        casefold_owners: dict[str, str] = {}
        for info in infos:
            name = info.filename
            # Directory members are still inspected by name/mode; is_dir() alone
            # is never treated as a free pass for traversal/absolute/symlink.
            slash_key = _slash_normalize_zip_member_key(name)
            norm_key = _normalize_zip_member_key(name)
            seen_normalized.setdefault(norm_key, []).append(slash_key)
            folded = norm_key.casefold()
            owner = casefold_owners.get(folded)
            if owner is None:
                casefold_owners[folded] = norm_key
            elif owner != norm_key:
                issues.append(
                    _reference_issue(
                        "case_collision",
                        "error",
                        (
                            f"Case-folding collision between {owner!r} and "
                            f"{norm_key!r} (from member {name!r})."
                        ),
                        path=name,
                    )
                )

            if _is_absolute_member_path(name):
                issues.append(
                    _reference_issue(
                        "absolute_path",
                        "error",
                        f"Archive member uses an absolute path: {name!r}.",
                        path=name,
                    )
                )
            elif _has_path_traversal(name):
                issues.append(
                    _reference_issue(
                        "path_traversal",
                        "error",
                        f"Archive member path contains traversal segments: {name!r}.",
                        path=name,
                    )
                )

            ratio = _compression_ratio(info)
            if ratio > max_ratio:
                issues.append(
                    _reference_issue(
                        "compression_ratio_limit",
                        "error",
                        (
                            f"Compression ratio {ratio:.2f} for {name!r} "
                            f"exceeds max_compression_ratio={max_ratio}."
                        ),
                        path=name,
                    )
                )

            if _is_zip_symlink(info):
                # Payload read only; never extract to the filesystem.
                target = archive.read(info).decode("utf-8", errors="surrogateescape")
                if _symlink_target_escapes(target):
                    issues.append(
                        _reference_issue(
                            "symlink_escape",
                            "error",
                            (
                                f"Symlink member {name!r} escapes the unpack root "
                                f"via target {target!r}."
                            ),
                            path=name,
                        )
                    )

        for norm_key, slash_keys in seen_normalized.items():
            count = len(slash_keys)
            if count <= 1:
                continue
            distinct_spellings = set(slash_keys)
            if len(distinct_spellings) == 1:
                issues.append(
                    _reference_issue(
                        "duplicate_path",
                        "error",
                        (
                            f"Duplicate archive path {norm_key!r} appears "
                            f"{count} times after slash+NFC normalization."
                        ),
                        path=norm_key,
                    )
                )
            else:
                # Distinct NFC/NFD (or other Unicode-equivalent) spellings fold
                # to one identity key — surface as case_collision so path
                # identity normalization is explicit.
                issues.append(
                    _reference_issue(
                        "case_collision",
                        "error",
                        (
                            f"Unicode-normalized path identity collision for "
                            f"{norm_key!r} across spellings {sorted(distinct_spellings)!r}."
                        ),
                        path=norm_key,
                    )
                )

    return _reference_oracle_report(
        issues,
        mode=REFERENCE_ARCHIVE_PREFLIGHT_MODE,
        archive=archive_path.name,
        member_names=raw_names,
        member_count=len(raw_names),
        total_uncompressed_bytes=total_uncompressed,
    )


def _usable_url(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _evidence_local_artifact_spec(row: dict[str, Any]) -> tuple[str, str | None]:
    """Classify source/asset path declaration for strict local provenance.

    Returns (kind, path_or_detail):
      - ("none", None): no path field to evaluate
      - ("url_only", None): path is null with a nonblank URL; no local resolution
      - ("local", relpath): nonblank relative path to resolve under the Pack root
      - ("invalid", detail): null path without usable URL, blank string, or
        non-string path — caller emits an explicit issue
    """
    for key in ("source", "asset"):
        container = row.get(key)
        if not isinstance(container, dict) or "path" not in container:
            continue
        path = container.get("path")
        has_url = _usable_url(container.get("url"))
        if path is None:
            if has_url:
                return ("url_only", None)
            return ("invalid", "null_path_without_url")
        if not isinstance(path, str):
            return ("invalid", "non_string_path")
        stripped = path.strip()
        if not stripped:
            return ("invalid", "blank_path")
        return ("local", stripped)
    return ("none", None)


def _normalize_declared_sha256(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("sha256:"):
        text = text[len("sha256:") :]
    text = text.strip().lower()
    if len(text) != 64:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return text


def _hash_field_is_declared(value: Any) -> bool:
    """True when a hash field is present and nonblank (must be valid sha256)."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _reference_strict_provenance_oracle(pack_dir: Path) -> dict[str, Any]:
    """Test-only strict provenance oracle for future OpenOyster admission.

    Distinguishes missing artifact, malformed hash, and hash mismatch checks that
    the current official compatible validator does not enforce. URL-only evidence
    (path null + nonblank URL) skips local artifact resolution. Never resolves or
    reads evidence paths outside the Pack root.
    """
    issues: list[dict[str, Any]] = []
    evidence_path = pack_dir / "evidence/index.jsonl"
    if not evidence_path.is_file():
        issues.append(
            _reference_issue(
                "missing_file",
                "error",
                "Required file is missing: evidence/index.jsonl",
                path="evidence/index.jsonl",
            )
        )
        return _reference_oracle_report(issues)

    for line_no, line in enumerate(evidence_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        assert isinstance(row, dict)
        evidence_id = str(row.get("evidence_id") or row.get("id") or f"line:{line_no}")
        kind, relative = _evidence_local_artifact_spec(row)
        if kind in {"none", "url_only"}:
            continue
        if kind == "invalid":
            detail = relative or "invalid_path"
            path_display = "" if detail in {"blank_path", "null_path_without_url"} else detail
            issues.append(
                _reference_issue(
                    "unsafe_evidence_path",
                    "error",
                    (
                        f"Evidence path declaration is invalid ({detail}): "
                        f"null without usable URL or blank local path."
                    ),
                    path=path_display,
                    record_id=evidence_id,
                )
            )
            continue

        assert relative is not None
        candidate = _resolve_under_root(pack_dir, relative)
        if candidate is None:
            issues.append(
                _reference_issue(
                    "unsafe_evidence_path",
                    "error",
                    f"Evidence path escapes or is absolute under Pack root: {relative!r}.",
                    path=relative,
                    record_id=evidence_id,
                )
            )
            continue

        if not candidate.is_file():
            issues.append(
                _reference_issue(
                    "missing_artifact",
                    "error",
                    f"Evidence artifact is missing: {relative}",
                    path=relative,
                    record_id=evidence_id,
                )
            )
            continue

        raw_hash = row.get("hash")
        if not _hash_field_is_declared(raw_hash):
            continue
        declared = _normalize_declared_sha256(raw_hash)
        if declared is None:
            issues.append(
                _reference_issue(
                    "malformed_hash",
                    "error",
                    (
                        f"Evidence hash is present but is not a valid sha256 "
                        f"declaration: {raw_hash!r}."
                    ),
                    path=relative,
                    record_id=evidence_id,
                )
            )
            continue
        actual = _sha256_file(candidate)
        if actual != declared:
            issues.append(
                _reference_issue(
                    "artifact_hash_mismatch",
                    "error",
                    (
                        f"Evidence artifact hash mismatch for {relative}: "
                        f"declared={declared} actual={actual}."
                    ),
                    path=relative,
                    record_id=evidence_id,
                )
            )

    return _reference_oracle_report(issues)


def _documented_pack_sha256(root: Path) -> str:
    """SHA-256 over documented required files excluding manifest.json.

    Files are processed in POSIX relative-path order. For each file the hasher
    receives: UTF-8 relative path, one NUL byte, raw file bytes, one newline.
    """
    hasher = hashlib.sha256()
    for relative_path in sorted(
        path for path in DOCUMENTED_STRICT_FULL_LAYOUT_FILES if path != "manifest.json"
    ):
        payload = (root / relative_path).read_bytes()
        hasher.update(relative_path.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(payload)
        hasher.update(b"\n")
    return hasher.hexdigest()


def _read_jsonl_records(path: Path) -> list[dict]:
    records: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        assert isinstance(payload, dict), f"{path}:{line_no} must be a JSON object"
        records.append(payload)
    return records


def _official_validate_pack_static(pack_dir: Path, *, write_report: bool) -> dict:
    assert write_report is False, (
        "Source fixtures must be validated with write_report=False so producer "
        "quality reports and digests stay immutable."
    )
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(OPENCRAB_ROOT), existing_pythonpath) if part
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                VALIDATOR_SUBPROCESS,
                str(OPENCRAB_ROOT),
                str(pack_dir),
            ],
            capture_output=True,
            check=False,
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
        )
    except OSError as error:
        raise AssertionError(f"Official OpenCrab validator subprocess could not start: {error}") from error

    assert completed.returncode == 0, (
        "Official OpenCrab validator subprocess failed "
        f"with exit code {completed.returncode}.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            "Official OpenCrab validator subprocess returned invalid JSON.\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        ) from error


def test_current_validator_accepts_minimal_four_file_pack_without_pack_v1_full_layout() -> None:
    assert OPENCRAB_ROOT.is_dir(), (
        f"Required OpenCrab sibling repository is missing: {OPENCRAB_ROOT}"
    )
    assert FIXTURE_ROOT.is_dir(), f"Required P0-F1 minimal fixture is missing: {FIXTURE_ROOT}"

    before = _fixture_digests(FIXTURE_ROOT)
    assert {relative_path for relative_path, _ in before} == EXPECTED_SOURCE_FILES
    assert dict(before) == P0_F1_EXPECTED_DIGESTS
    assert not (FIXTURE_ROOT / "quality/report.json").exists()

    report = _official_validate_pack_static(FIXTURE_ROOT, write_report=False)

    assert report["status"] == "pass"
    assert report["checks"] == COMPATIBLE_VALIDATOR_PASS_CHECKS
    assert _fixture_digests(FIXTURE_ROOT) == before
    assert not (FIXTURE_ROOT / "quality/report.json").exists()


def test_official_validator_helper_does_not_mutate_parent_sys_path() -> None:
    parent_sys_path = list(sys.path)

    _official_validate_pack_static(FIXTURE_ROOT, write_report=False)

    assert sys.path == parent_sys_path


def test_official_validator_helper_ignores_parent_fake_opencrab_modules(monkeypatch) -> None:
    fake_opencrab = ModuleType("opencrab")
    fake_pack = ModuleType("opencrab.pack")
    fake_validation = ModuleType("opencrab.pack.validation")

    def fake_validate_pack_static(*args, **kwargs):
        return {"status": "fake"}

    fake_opencrab.pack = fake_pack
    fake_pack.validation = fake_validation
    fake_validation.validate_pack_static = fake_validate_pack_static
    monkeypatch.setitem(sys.modules, "opencrab", fake_opencrab)
    monkeypatch.setitem(sys.modules, "opencrab.pack", fake_pack)
    monkeypatch.setitem(sys.modules, "opencrab.pack.validation", fake_validation)
    parent_sys_path = list(sys.path)

    report = _official_validate_pack_static(FIXTURE_ROOT, write_report=False)

    assert report["status"] == "pass"
    assert sys.path == parent_sys_path
    assert sys.modules["opencrab.pack.validation"] is fake_validation


def test_documented_strict_full_layout_requires_exact_eleven_files_with_cross_file_consistency() -> None:
    """Documented Pack v1 Required Layout is exactly 11 files with coherent ids/counts.

    This is stricter than the current compatible validator, which only requires the
    four canonical graph/evidence files listed in COMPATIBLE_VALIDATOR_REQUIRED_FILES.
    """
    assert OPENCRAB_ROOT.is_dir(), (
        f"Required OpenCrab sibling repository is missing: {OPENCRAB_ROOT}"
    )
    assert FULL_LAYOUT_FIXTURE_ROOT.is_dir(), (
        f"Required P0-F2 full-layout fixture is missing: {FULL_LAYOUT_FIXTURE_ROOT}"
    )

    digests = _fixture_digests(FULL_LAYOUT_FIXTURE_ROOT)
    relative_paths = {relative_path for relative_path, _ in digests}
    assert relative_paths == DOCUMENTED_STRICT_FULL_LAYOUT_FILES
    assert dict(digests) == P0_F2_EXPECTED_DIGESTS
    assert COMPATIBLE_VALIDATOR_REQUIRED_FILES < DOCUMENTED_STRICT_FULL_LAYOUT_FILES
    documented_only = DOCUMENTED_STRICT_FULL_LAYOUT_FILES - COMPATIBLE_VALIDATOR_REQUIRED_FILES
    assert documented_only == {
        "quality/report.json",
        "neo4j/import.cypher",
        "neo4j/opencrab_ingest.jsonl",
        "neo4j/export_status.json",
        "README.md",
        "sample_queries.json",
        "community_reports.json",
    }

    manifest = json.loads((FULL_LAYOUT_FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    nodes = _read_jsonl_records(FULL_LAYOUT_FIXTURE_ROOT / "graph/nodes.jsonl")
    edges = _read_jsonl_records(FULL_LAYOUT_FIXTURE_ROOT / "graph/edges.jsonl")
    evidence = _read_jsonl_records(FULL_LAYOUT_FIXTURE_ROOT / "evidence/index.jsonl")
    quality_report = json.loads(
        (FULL_LAYOUT_FIXTURE_ROOT / "quality/report.json").read_text(encoding="utf-8")
    )
    ingest = _read_jsonl_records(FULL_LAYOUT_FIXTURE_ROOT / "neo4j/opencrab_ingest.jsonl")
    export_status = json.loads(
        (FULL_LAYOUT_FIXTURE_ROOT / "neo4j/export_status.json").read_text(encoding="utf-8")
    )
    sample_queries = json.loads(
        (FULL_LAYOUT_FIXTURE_ROOT / "sample_queries.json").read_text(encoding="utf-8")
    )
    community_reports = json.loads(
        (FULL_LAYOUT_FIXTURE_ROOT / "community_reports.json").read_text(encoding="utf-8")
    )
    import_cypher = (FULL_LAYOUT_FIXTURE_ROOT / "neo4j/import.cypher").read_text(encoding="utf-8")
    readme = (FULL_LAYOUT_FIXTURE_ROOT / "README.md").read_text(encoding="utf-8")

    assert manifest["format_version"] == "opencrab-pack-v1"
    assert manifest["pack_id"] == "p0-f2-full-layout"
    assert manifest["title"]
    assert manifest["version"]
    assert manifest["grammar_version"]
    assert manifest["created_at"]
    assert manifest["created_by"]
    assert isinstance(manifest["license"], dict) and manifest["license"]["name"]
    assert isinstance(manifest["source"], dict) and manifest["source"]["label"]
    assert isinstance(manifest["limits"], dict)
    assert isinstance(manifest["quality"], dict)
    assert isinstance(manifest["retrieval_hints"], dict)
    assert isinstance(manifest["hashes"], dict)
    assert isinstance(manifest["artifacts"], dict)

    node_ids = {str(node["id"]) for node in nodes}
    edge_ids = {str(edge["id"]) for edge in edges}
    evidence_ids = {str(row["evidence_id"]) for row in evidence}
    assert len(nodes) == manifest["counts"]["nodes"] == 3
    assert len(edges) == manifest["counts"]["edges"] == 2
    assert len(evidence) == manifest["counts"]["evidence"] == 1
    assert manifest["counts"]["documents"] == 1
    assert manifest["counts"]["chunks"] == 1
    assert manifest["counts"]["files"] == len(DOCUMENTED_STRICT_FULL_LAYOUT_FILES)
    assert manifest["counts"]["bytes"] == sum(
        (FULL_LAYOUT_FIXTURE_ROOT / relative_path).stat().st_size
        for relative_path in DOCUMENTED_STRICT_FULL_LAYOUT_FILES
    )

    digest_by_path = dict(digests)
    assert manifest["hashes"]["nodes_sha256"] == digest_by_path["graph/nodes.jsonl"]
    assert manifest["hashes"]["edges_sha256"] == digest_by_path["graph/edges.jsonl"]
    assert manifest["hashes"]["evidence_sha256"] == digest_by_path["evidence/index.jsonl"]
    assert manifest["hashes"]["pack_sha256_algorithm"] == PACK_SHA256_ALGORITHM
    expected_pack_sha256 = _documented_pack_sha256(FULL_LAYOUT_FIXTURE_ROOT)
    assert manifest["hashes"]["pack_sha256"] == expected_pack_sha256
    assert "..." not in json.dumps(manifest)

    assert manifest["artifacts"] == {
        "nodes": "graph/nodes.jsonl",
        "edges": "graph/edges.jsonl",
        "evidence_index": "evidence/index.jsonl",
        "quality_report": "quality/report.json",
        "neo4j_cypher": "neo4j/import.cypher",
        "opencrab_ingest": "neo4j/opencrab_ingest.jsonl",
        "neo4j_export_status": "neo4j/export_status.json",
    }

    for edge in edges:
        assert edge["from_id"] in node_ids
        assert edge["to_id"] in node_ids
        for ref in edge["evidence_refs"]:
            assert ref in evidence_ids
    for node in nodes:
        for ref in node["evidence_refs"]:
            assert ref in evidence_ids

    assert quality_report["status"] == "pass"
    assert quality_report["pack_id"] == manifest["pack_id"]
    assert quality_report["summary"]["node_evidence_integrity"] == 1.0
    assert quality_report["summary"]["edge_evidence_integrity"] == 1.0
    assert quality_report["summary"]["graph_reference_integrity"] == 1.0
    assert quality_report["counts"]["nodes"] == len(nodes)
    assert quality_report["counts"]["edges"] == len(edges)
    assert quality_report["counts"]["evidence"] == len(evidence)
    # Synthetic producer report must not claim a live Neo4j import/check ran.
    assert quality_report["checks"]["neo4j_import"] == "skip"
    assert quality_report["issues"] == []

    ingest_by_kind: dict[str, list[dict]] = {"node": [], "edge": [], "evidence": []}
    for row in ingest:
        kind = row["kind"]
        assert kind in ingest_by_kind
        assert isinstance(row["payload"], dict)
        ingest_by_kind[kind].append(row["payload"])
    assert {row["id"] for row in ingest_by_kind["node"]} == node_ids
    assert {row["id"] for row in ingest_by_kind["edge"]} == edge_ids
    assert {row["evidence_id"] for row in ingest_by_kind["evidence"]} == evidence_ids
    assert len(ingest_by_kind["node"]) == len(nodes)
    assert len(ingest_by_kind["edge"]) == len(edges)
    assert len(ingest_by_kind["evidence"]) == len(evidence)

    assert export_status["status"] == "ok"
    assert export_status["pack_id"] == manifest["pack_id"]
    assert export_status["nodes"] == len(nodes)
    assert export_status["edges"] == len(edges)
    assert export_status["evidence"] == len(evidence)
    assert export_status["output"] == "neo4j/opencrab_ingest.jsonl"
    assert export_status["exported_at"]
    # Explicit synthetic marker: counts are fixture metadata, not a live export.
    assert export_status["origin"] == "fixture_synthetic"
    assert export_status["live_neo4j_executed"] is False

    assert "resource:doc:1" in import_cypher
    assert "claim:1" in import_cypher
    assert "evidence:1" in import_cypher
    assert "edge:supports:1" in import_cypher or "supports" in import_cypher

    assert manifest["pack_id"] in readme
    assert "quality/report.json" in readme or "validated" in readme.lower()
    readme_lower = readme.lower()
    assert "no live neo4j import/export was executed" in readme_lower
    assert "structurally consistent synthetic fixture data" in readme_lower

    assert isinstance(sample_queries, list) and sample_queries
    for query in sample_queries:
        assert query["id"]
        assert query["question"]
        assert set(query["focus_node_ids"]) <= node_ids

    assert isinstance(community_reports, list) and community_reports
    for report in community_reports:
        assert report["community_id"]
        assert report["summary"]
        assert set(report["member_node_ids"]) <= node_ids
        assert set(report["evidence_ids"]) <= evidence_ids


def test_current_compatible_validator_accepts_full_layout_without_enforcing_documented_strict_artifacts() -> None:
    """Current compatible validator still only enforces the four-file minimum.

    Documented strict artifacts (quality report, Neo4j snapshot, README, samples,
    community reports) must remain present and byte-identical after validation with
    write_report=False; the official validator does not require them for a pass.
    """
    assert OPENCRAB_ROOT.is_dir(), (
        f"Required OpenCrab sibling repository is missing: {OPENCRAB_ROOT}"
    )
    assert FULL_LAYOUT_FIXTURE_ROOT.is_dir(), (
        f"Required P0-F2 full-layout fixture is missing: {FULL_LAYOUT_FIXTURE_ROOT}"
    )

    before = _fixture_digests(FULL_LAYOUT_FIXTURE_ROOT)
    before_paths = {relative_path for relative_path, _ in before}
    assert before_paths == DOCUMENTED_STRICT_FULL_LAYOUT_FILES
    assert COMPATIBLE_VALIDATOR_REQUIRED_FILES.issubset(before_paths)
    quality_path = FULL_LAYOUT_FIXTURE_ROOT / "quality/report.json"
    assert quality_path.is_file()
    producer_quality_digest = dict(before)["quality/report.json"]
    producer_quality_bytes = quality_path.read_bytes()

    report = _official_validate_pack_static(FULL_LAYOUT_FIXTURE_ROOT, write_report=False)

    assert report["status"] == "pass"
    assert report["checks"] == COMPATIBLE_VALIDATOR_PASS_CHECKS
    # Documented Neo4j artifacts exist, but the compatible validator still skips neo4j_import.
    assert report["checks"]["neo4j_import"] == "skip"

    after = _fixture_digests(FULL_LAYOUT_FIXTURE_ROOT)
    assert after == before
    assert quality_path.read_bytes() == producer_quality_bytes
    assert dict(after)["quality/report.json"] == producer_quality_digest
    assert not (FULL_LAYOUT_FIXTURE_ROOT / "quality/human_review_items.json").exists()


def test_p0_f3_invalid_archive_preflight_rejects_each_threat_without_writing_pack_store(
    tmp_path: Path,
) -> None:
    """Phase 0 reference preflight rejects eight ZIP threats read-only.

    This test proves metadata/symlink-payload inspection only: it does not claim
    production archive admission, quarantine, or installer behavior under
    src/openoyster is implemented.
    """
    assert INVALID_ARCHIVES_ROOT.is_dir(), (
        f"Required P0-F3 invalid archive fixture directory is missing: "
        f"{INVALID_ARCHIVES_ROOT}"
    )

    expectations_path = INVALID_ARCHIVES_ROOT / "expectations.json"
    assert expectations_path.is_file(), f"Missing expectations.json under {INVALID_ARCHIVES_ROOT}"
    expectations = json.loads(expectations_path.read_text(encoding="utf-8"))
    limits = expectations["limits"]
    assert limits == DEFAULT_ARCHIVE_LIMITS
    archive_expectations = expectations["archives"]
    assert set(archive_expectations) == set(INVALID_ARCHIVE_NAMES)
    assert set(ARCHIVE_PRIMARY_ISSUE_BY_NAME) == set(INVALID_ARCHIVE_NAMES)

    pack_store = tmp_path / "pack-store"
    pack_store.mkdir()
    outside_sentinel = tmp_path / "outside-sentinel"
    assert not outside_sentinel.exists()
    store_before = _fixture_digests(pack_store)

    before_digests: dict[str, str] = {}
    for archive_name in INVALID_ARCHIVE_NAMES:
        archive_path = INVALID_ARCHIVES_ROOT / archive_name
        assert archive_path.is_file(), f"Missing archive fixture: {archive_path}"
        before_digests[archive_name] = _sha256_file(archive_path)

    for archive_name, primary_issue in ARCHIVE_PRIMARY_ISSUE_BY_NAME.items():
        archive_path = INVALID_ARCHIVES_ROOT / archive_name
        report = _reference_archive_preflight(archive_path, limits=limits)

        assert report["production_admission"] is False
        assert report["mode"] == REFERENCE_ARCHIVE_PREFLIGHT_MODE
        assert report["status"] == "fail"
        assert primary_issue in report["issue_codes"]
        assert archive_expectations[archive_name]["primary_issue"] == primary_issue
        # Minimal isolation: the fixture's primary code is the only issue code.
        assert report["issue_codes"] == [primary_issue], (
            f"{archive_name} should isolate primary issue {primary_issue}, "
            f"got {report['issue_codes']}"
        )
        assert _sha256_file(archive_path) == before_digests[archive_name]

    # Read-only preflight must leave the temporary Pack store empty and must not
    # create outside sentinels. This is Phase 0 evidence only.
    assert _fixture_digests(pack_store) == store_before
    assert list(pack_store.iterdir()) == []
    assert not outside_sentinel.exists()
    for archive_name, digest in before_digests.items():
        assert _sha256_file(INVALID_ARCHIVES_ROOT / archive_name) == digest


def test_p0_f3_invalid_archive_fixtures_are_deterministic_and_minimally_isolated() -> None:
    """Each invalid archive fixture is present exactly once with stable digests."""
    assert INVALID_ARCHIVES_ROOT.is_dir(), (
        f"Required P0-F3 invalid archive fixture directory is missing: "
        f"{INVALID_ARCHIVES_ROOT}"
    )
    digests = dict(_fixture_digests(INVALID_ARCHIVES_ROOT))
    expected_paths = set(INVALID_ARCHIVE_NAMES) | {"expectations.json"}
    assert set(digests) == expected_paths

    expectations = json.loads(
        (INVALID_ARCHIVES_ROOT / "expectations.json").read_text(encoding="utf-8")
    )
    for archive_name, primary_issue in ARCHIVE_PRIMARY_ISSUE_BY_NAME.items():
        assert expectations["archives"][archive_name]["primary_issue"] == primary_issue
        # Re-hash twice for deterministic fixture bytes.
        first = digests[archive_name]
        second = _sha256_file(INVALID_ARCHIVES_ROOT / archive_name)
        assert first == second
        assert len(first) == 64


def test_p0_f3_reference_archive_preflight_blocks_slash_normalization_and_ratio_bypasses(
    tmp_path: Path,
) -> None:
    """Ephemeral adversarial members: slash/UNC/drive/dir/zero-ratio/symlink mode.

    Committed fixtures remain minimally isolated; this test only builds temporary
    ZIPs under tmp_path and never extracts them. Phase 0 reference oracle only.
    """
    pack_store = tmp_path / "pack-store"
    pack_store.mkdir()
    outside = tmp_path / "outside-sentinel"
    assert not outside.exists()

    def write_zip(name: str, members: list[tuple[str, bytes, dict[str, Any] | None]]) -> Path:
        path = tmp_path / name
        with zipfile.ZipFile(path, mode="w") as archive:
            for member_name, payload, attrs in members:
                info = zipfile.ZipInfo(filename=member_name)
                info.external_attr = (0o100644 << 16)
                if attrs:
                    for key, value in attrs.items():
                        setattr(info, key, value)
                archive.writestr(info, payload)
        return path

    cases: list[tuple[str, list[tuple[str, bytes, dict[str, Any] | None]], str]] = [
        (
            "backslash-traversal.zip",
            [("..\\..\\evil.txt", b"x", None)],
            "path_traversal",
        ),
        (
            "drive-absolute.zip",
            [("C:\\evil.txt", b"x", None)],
            "absolute_path",
        ),
        (
            "unc-absolute.zip",
            [("\\\\server\\share\\x", b"x", None)],
            "absolute_path",
        ),
        (
            "dir-traversal.zip",
            [("../../evil/", b"", None)],
            "path_traversal",
        ),
        (
            "case-mixed-seps.zip",
            [("dir\\Note.txt", b"a", None), ("dir/note.txt", b"b", None)],
            "case_collision",
        ),
        (
            "dup-slash-collapse.zip",
            [("foo//bar", b"a", None), ("foo/bar", b"b", None)],
            "duplicate_path",
        ),
        (
            # NFC vs NFD forms of the same Unicode filename must collide after
            # identity normalization (explicit Unicode path identity).
            "unicode-nfc-nfd-collision.zip",
            [
                (unicodedata.normalize("NFC", "café.txt"), b"a", None),
                (unicodedata.normalize("NFD", "café.txt"), b"b", None),
            ],
            "case_collision",
        ),
        (
            "symlink-mode-escape.zip",
            [
                (
                    "escape.link",
                    b"../../outside",
                    {
                        "external_attr": (stat.S_IFLNK | 0o777) << 16,
                        "create_system": 3,
                    },
                )
            ],
            "symlink_escape",
        ),
    ]

    before_store = _fixture_digests(pack_store)
    for archive_name, members, expected_code in cases:
        archive_path = write_zip(archive_name, members)
        before = _sha256_file(archive_path)
        report = _reference_archive_preflight(archive_path, limits=DEFAULT_ARCHIVE_LIMITS)
        assert report["production_admission"] is False
        assert report["mode"] == REFERENCE_ARCHIVE_PREFLIGHT_MODE
        assert report["status"] == "fail"
        assert expected_code in report["issue_codes"], (
            f"{archive_name} missing {expected_code}: {report['issue_codes']}"
        )
        assert _sha256_file(archive_path) == before

    # Zero compressed size with positive claimed file_size is infinite ratio.
    class _ZeroCompress:
        file_size = 50_000
        compress_size = 0

    assert _compression_ratio(_ZeroCompress()) == float("inf")
    assert _compression_ratio(_ZeroCompress()) > DEFAULT_ARCHIVE_LIMITS["max_compression_ratio"]

    assert _fixture_digests(pack_store) == before_store
    assert list(pack_store.iterdir()) == []
    assert not outside.exists()


def test_p0_f3_broken_provenance_missing_evidence_ref_fails_official_compatible_validator() -> None:
    """Official missing_evidence_ref must be isolated from strict artifact provenance."""
    assert OPENCRAB_ROOT.is_dir(), (
        f"Required OpenCrab sibling repository is missing: {OPENCRAB_ROOT}"
    )
    pack_dir = BROKEN_PROVENANCE_ROOT / "missing-evidence-ref"
    assert pack_dir.is_dir(), (
        f"Required P0-F3 broken-provenance fixture is missing: {pack_dir}"
    )
    expectations = json.loads(
        (BROKEN_PROVENANCE_ROOT / "expectations.json").read_text(encoding="utf-8")
    )
    pack_meta = expectations["packs"]["missing-evidence-ref"]

    before = _fixture_digests(pack_dir)
    report = _official_validate_pack_static(pack_dir, write_report=False)
    strict = _reference_strict_provenance_oracle(pack_dir)

    assert report["status"] == "fail"
    assert report["checks"]["evidence_refs"] == "fail"
    issue_codes = {issue["code"] for issue in report["issues"]}
    assert "missing_evidence_ref" in issue_codes
    # Fixture + expectations isolate the official failure: strict must not also
    # invent missing_artifact / unsafe path issues for this pack.
    assert pack_meta["official_issue_code"] == "missing_evidence_ref"
    assert pack_meta["strict_oracle_primary_issue"] is None
    assert strict["status"] == "pass", strict
    assert "missing_artifact" not in strict["issue_codes"]
    assert "unsafe_evidence_path" not in strict["issue_codes"]
    assert _fixture_digests(pack_dir) == before


def test_p0_f3_strict_provenance_accepts_url_only_null_path_evidence(
    tmp_path: Path,
) -> None:
    """OpenCrab-legal URL-only source={url, path:null} must pass strict provenance."""
    pack_dir = tmp_path / "url-only-pack"
    pack_dir.mkdir()
    (pack_dir / "evidence").mkdir()
    (pack_dir / "evidence" / "index.jsonl").write_text(
        json.dumps(
            {
                "evidence_id": "evidence:url-only",
                "kind": "text_chunk",
                "source": {
                    "url": "https://example.invalid/source",
                    "path": None,
                    "title": "Remote only",
                },
                "parser": {"status": "ok", "method": "native_text"},
                "location": {"document_id": "resource:doc:1", "chunk_index": 1},
                "text": "URL-only evidence requires no local artifact resolution.",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    report = _reference_strict_provenance_oracle(pack_dir)

    assert report["status"] == "pass", report
    assert report["issue_codes"] == []
    assert "unsafe_evidence_path" not in report["issue_codes"]
    assert "missing_artifact" not in report["issue_codes"]
    assert report["production_admission"] is False


def test_p0_f3_strict_provenance_rejects_malformed_hash_on_existing_artifact(
    tmp_path: Path,
) -> None:
    """A present but non-sha256 hash declaration must fail with malformed_hash."""
    pack_dir = tmp_path / "malformed-hash-pack"
    pack_dir.mkdir()
    (pack_dir / "evidence").mkdir()
    artifact = pack_dir / "assets" / "source.md"
    artifact.parent.mkdir()
    artifact.write_text("local artifact bytes\n", encoding="utf-8")
    (pack_dir / "evidence" / "index.jsonl").write_text(
        json.dumps(
            {
                "evidence_id": "evidence:malformed-hash",
                "kind": "text_chunk",
                "source": {"path": "assets/source.md", "title": "Local"},
                "hash": "deadbeef",
                "parser": {"status": "ok", "method": "native_markdown"},
                "location": {"document_id": "resource:doc:1", "chunk_index": 1},
                "text": "Declared hash is present but not a valid sha256.",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    report = _reference_strict_provenance_oracle(pack_dir)

    assert report["status"] == "fail", report
    assert "malformed_hash" in report["issue_codes"]
    assert "artifact_hash_mismatch" not in report["issue_codes"]
    assert report["production_admission"] is False


def test_p0_f3_broken_provenance_missing_artifact_passes_compatible_fails_strict_oracle() -> None:
    """Compatible validator may pass; strict provenance oracle returns missing_artifact."""
    assert OPENCRAB_ROOT.is_dir(), (
        f"Required OpenCrab sibling repository is missing: {OPENCRAB_ROOT}"
    )
    pack_dir = BROKEN_PROVENANCE_ROOT / "missing-artifact"
    assert pack_dir.is_dir(), (
        f"Required P0-F3 broken-provenance fixture is missing: {pack_dir}"
    )

    before = _fixture_digests(pack_dir)
    compatible = _official_validate_pack_static(pack_dir, write_report=False)
    strict = _reference_strict_provenance_oracle(pack_dir)

    assert compatible["status"] == "pass"
    assert compatible["checks"]["evidence_refs"] == "pass"
    assert strict["status"] == "fail"
    assert "missing_artifact" in strict["issue_codes"]
    assert strict["production_admission"] is False
    assert _fixture_digests(pack_dir) == before


def test_p0_f3_broken_provenance_artifact_hash_mismatch_caught_by_strict_oracle() -> None:
    """Strict provenance oracle detects declared vs actual artifact SHA-256 mismatch."""
    assert OPENCRAB_ROOT.is_dir(), (
        f"Required OpenCrab sibling repository is missing: {OPENCRAB_ROOT}"
    )
    pack_dir = BROKEN_PROVENANCE_ROOT / "artifact-hash-mismatch"
    assert pack_dir.is_dir(), (
        f"Required P0-F3 broken-provenance fixture is missing: {pack_dir}"
    )

    before = _fixture_digests(pack_dir)
    compatible = _official_validate_pack_static(pack_dir, write_report=False)
    strict = _reference_strict_provenance_oracle(pack_dir)

    # Official compatible validator does not enforce artifact content hashes.
    assert compatible["status"] == "pass"
    assert strict["status"] == "fail"
    assert "artifact_hash_mismatch" in strict["issue_codes"]
    assert strict["production_admission"] is False
    assert _fixture_digests(pack_dir) == before


def test_p0_f3_strict_provenance_oracle_rejects_unsafe_evidence_paths_below_pack_root(
    tmp_path: Path,
) -> None:
    """Unsafe evidence paths are rejected without reading outside the Pack root."""
    pack_dir = tmp_path / "unsafe-evidence-pack"
    pack_dir.mkdir()
    (pack_dir / "evidence").mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("should-not-be-read\n", encoding="utf-8")

    unsafe_paths = (
        "../outside-secret.txt",
        "/etc/passwd",
        "C:/Windows/system.ini",
        "assets/../../outside-secret.txt",
        " ../outside-secret.txt",  # whitespace-prefixed traversal after strip
        "",  # declared empty path
        "   ",  # whitespace-only path
        "\\\\server\\share\\x",
    )

    for index, unsafe_path in enumerate(unsafe_paths):
        (pack_dir / "evidence/index.jsonl").write_text(
            json.dumps(
                {
                    "evidence_id": f"evidence:unsafe:{index}",
                    "kind": "text_chunk",
                    "source": {"path": unsafe_path, "title": "Unsafe"},
                    "hash": "sha256:" + ("0" * 64),
                    "parser": {"status": "ok", "method": "native_text"},
                    "location": {"document_id": "resource:doc:1", "chunk_index": 1},
                    "text": "unused",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

        outside_before = outside.read_bytes()
        report = _reference_strict_provenance_oracle(pack_dir)

        assert report["status"] == "fail", f"path={unsafe_path!r}"
        assert "unsafe_evidence_path" in report["issue_codes"], (
            f"path={unsafe_path!r} codes={report['issue_codes']}"
        )
        assert "missing_artifact" not in report["issue_codes"]
        assert "artifact_hash_mismatch" not in report["issue_codes"]
        assert outside.read_bytes() == outside_before
        assert {
            p.relative_to(pack_dir).as_posix() for p in pack_dir.rglob("*") if p.is_file()
        } == {"evidence/index.jsonl"}


def test_p0_f3_broken_provenance_fixture_trees_remain_byte_identical_after_validation() -> None:
    """All three broken-provenance Pack trees stay unchanged after every check."""
    assert BROKEN_PROVENANCE_ROOT.is_dir(), (
        f"Required P0-F3 broken-provenance fixture directory is missing: "
        f"{BROKEN_PROVENANCE_ROOT}"
    )
    expectations = json.loads(
        (BROKEN_PROVENANCE_ROOT / "expectations.json").read_text(encoding="utf-8")
    )
    packs = expectations["packs"]
    assert set(packs) == {
        "missing-evidence-ref",
        "missing-artifact",
        "artifact-hash-mismatch",
    }

    for pack_name in packs:
        pack_dir = BROKEN_PROVENANCE_ROOT / pack_name
        before = _fixture_digests(pack_dir)
        _official_validate_pack_static(pack_dir, write_report=False)
        _reference_strict_provenance_oracle(pack_dir)
        assert _fixture_digests(pack_dir) == before
