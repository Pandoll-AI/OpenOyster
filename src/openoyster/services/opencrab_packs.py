"""Trusted local OpenCrab Pack validation and installation.

Source Pack directories are never modified. Install copies validated bytes into a
digest-addressed workspace path and persists registry records transactionally.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.models import PackEdge, PackEvidence, PackFile, PackInstall, PackNode, utcnow
from openoyster.utils import stable_hash

AdmissionProfile = Literal["compatible", "strict"]
SUPPORTED_FORMAT_VERSION: Final = "opencrab-pack-v1"
UNVERSIONED: Final = "unversioned"

COMPATIBLE_REQUIRED_FILES: Final[frozenset[str]] = frozenset(
    {
        "manifest.json",
        "graph/nodes.jsonl",
        "graph/edges.jsonl",
        "evidence/index.jsonl",
    }
)
STRICT_REQUIRED_FILES: Final[frozenset[str]] = frozenset(
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

_FILE_ROLES: Final[dict[str, str]] = {
    "manifest.json": "manifest",
    "graph/nodes.jsonl": "graph_nodes",
    "graph/edges.jsonl": "graph_edges",
    "evidence/index.jsonl": "evidence_index",
    "quality/report.json": "quality_report",
    "neo4j/import.cypher": "neo4j_import",
    "neo4j/opencrab_ingest.jsonl": "neo4j_snapshot",
    "neo4j/export_status.json": "neo4j_export_status",
    "README.md": "readme",
    "sample_queries.json": "sample_queries",
    "community_reports.json": "community_reports",
}


class PackValidationError(ValueError):
    """Raised when a Pack fails admission validation."""

    def __init__(self, message: str, *, issues: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


class PackConflictError(ValueError):
    """Raised when a different digest claims the same (pack_id, version)."""

    def __init__(
        self,
        message: str,
        *,
        pack_id: str,
        declared_version: str,
        existing_digest: str,
        incoming_digest: str,
    ) -> None:
        super().__init__(message)
        self.pack_id = pack_id
        self.declared_version = declared_version
        self.existing_digest = existing_digest
        self.incoming_digest = incoming_digest


@dataclass(frozen=True)
class PackValidationResult:
    status: Literal["pass", "fail"]
    profile: AdmissionProfile
    pack_id: str | None
    declared_version: str
    format_version: str | None
    grammar_version: str | None
    source_digest: str
    digest_verified: bool
    issues: list[dict[str, Any]] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    file_digests: dict[str, str] = field(default_factory=dict)
    quality: dict[str, Any] | None = None


@dataclass(frozen=True)
class PackInstallResult:
    status: str
    pack_id: str
    declared_version: str
    source_digest: str
    pack_install_id: int
    noop: bool
    storage_uri: str
    admission_report: dict[str, Any] = field(default_factory=dict)


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    path: str | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if path is not None:
        payload["path"] = path
    if record_id is not None:
        payload["record_id"] = record_id
    return payload


def _diagnose_retrieval_hints(manifest: dict[str, Any], issues: list[dict[str, Any]]) -> list[str]:
    """Accept optional manifest.retrieval_hints as a string array (lenient).

    Invalid shapes/elements are ignored with a non-error diagnostic; admission
    is never rejected for this field. Hints are search routing aids only.
    """
    if "retrieval_hints" not in manifest:
        return []
    raw = manifest.get("retrieval_hints")
    if raw is None:
        return []
    if not isinstance(raw, list):
        issues.append(
            _issue(
                "ignored_retrieval_hints",
                "info",
                "manifest.retrieval_hints ignored: expected a string array",
                path="manifest.json",
            )
        )
        return []
    accepted: list[str] = []
    ignored_elements = False
    for item in raw:
        if isinstance(item, str):
            text = item.strip()
            if text:
                accepted.append(text)
            # empty strings are silently dropped
        else:
            ignored_elements = True
    if ignored_elements:
        issues.append(
            _issue(
                "ignored_retrieval_hints_elements",
                "info",
                "manifest.retrieval_hints non-string elements ignored",
                path="manifest.json",
            )
        )
    return accepted


def _iter_pack_files(root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*") if path.is_file()), key=lambda p: p.relative_to(root).as_posix())


def compute_directory_digest(root: Path) -> tuple[str, dict[str, str]]:
    """Deterministic directory digest: path + NUL + bytes + newline per file."""
    hasher = hashlib.sha256()
    file_digests: dict[str, str] = {}
    for path in _iter_pack_files(root):
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        file_digests[relative] = hashlib.sha256(payload).hexdigest()
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(payload)
        hasher.update(b"\n")
    return hasher.hexdigest(), file_digests


def global_record_id(
    *,
    pack_id: str,
    declared_version: str,
    source_digest: str,
    kind: Literal["node", "edge", "evidence"],
    local_id: str,
) -> str:
    encoded_local = quote(local_id, safe="")
    return f"opencrab://{pack_id}@{declared_version}/{source_digest}/{kind}/{encoded_local}"


def _read_json(path: Path, rel: str, issues: list[dict[str, Any]]) -> dict[str, Any]:
    if not path.is_file():
        issues.append(_issue("missing_file", "error", f"Required file is missing: {rel}", path=rel))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        issues.append(
            _issue("invalid_json", "error", f"{rel} is not valid JSON: {exc}", path=rel)
        )
        return {}
    if not isinstance(payload, dict):
        issues.append(
            _issue("invalid_json_type", "error", f"{rel} must contain a JSON object.", path=rel)
        )
        return {}
    return payload


def _read_jsonl(path: Path, rel: str, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not path.is_file():
        issues.append(_issue("missing_file", "error", f"Required file is missing: {rel}", path=rel))
        return []
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(
                _issue(
                    "invalid_jsonl",
                    "error",
                    f"{rel}:{line_no} is not valid JSON: {exc}",
                    path=rel,
                )
            )
            continue
        if not isinstance(payload, dict):
            issues.append(
                _issue(
                    "invalid_jsonl_record",
                    "error",
                    f"{rel}:{line_no} must contain a JSON object.",
                    path=rel,
                )
            )
            continue
        records.append(payload)
    return records


def _record_id(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def _evidence_id(record: dict[str, Any]) -> str:
    return _record_id(record, "evidence_id", "id")


def _evidence_refs(record: dict[str, Any]) -> list[str]:
    refs = record.get("evidence_refs") or record.get("evidence_ids") or []
    if isinstance(refs, str):
        return [refs]
    if isinstance(refs, list):
        return [str(ref) for ref in refs if ref]
    return []


def _mapping_field(record: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a JSON object field as a plain dict, or {} when missing/non-object."""
    value = record.get(key)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _optional_mapping_field(record: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Return a JSON object field, or None when missing/non-object."""
    value = record.get(key)
    if isinstance(value, dict):
        return dict(value)
    return None


def _normalise_evidence_links(record: dict[str, Any]) -> dict[str, Any]:
    """Store supported link spellings in one lossless JSON object."""
    raw_links = record.get("links")
    if raw_links is None:
        raw_links = record.get("link")
    if raw_links is None:
        raw_links = _mapping_field(record, "source").get("links")
    if isinstance(raw_links, dict):
        return dict(raw_links)
    if isinstance(raw_links, str) and raw_links.strip():
        return {"links": [raw_links]}
    if isinstance(raw_links, list):
        return {"links": list(raw_links)}
    return {}


def _normalise_evidence_content_hash(record: dict[str, Any]) -> str | None:
    """Accept the OpenCrab ``hash`` alias while preferring ``content_hash``."""
    for key in ("content_hash", "hash"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _normalise_evidence_vision(record: dict[str, Any]) -> dict[str, Any] | None:
    """Preserve both legacy CLIP metadata and current vision metadata."""
    vision = _optional_mapping_field(record, "vision")
    clip = _optional_mapping_field(record, "clip")
    payload: dict[str, Any] = {}
    if vision is not None:
        payload["vision"] = vision
    if clip is not None:
        payload["clip"] = clip
    return payload or None


def _promotion_status(record: dict[str, Any]) -> str:
    quality = _mapping_field(record, "quality")
    props = _mapping_field(record, "properties")
    return str(quality.get("promotion_status") or props.get("status") or "").lower()


def _media_type_for(path: str) -> str | None:
    if path.endswith(".json"):
        return "application/json"
    if path.endswith(".jsonl"):
        return "application/x-ndjson"
    if path.endswith(".md"):
        return "text/markdown"
    if path.endswith(".cypher"):
        return "application/x-cypher-query"
    return None


def validate_pack_directory(
    pack_dir: str | Path,
    *,
    profile: AdmissionProfile = "compatible",
) -> PackValidationResult:
    """Validate a trusted Pack directory without modifying source bytes."""
    root = Path(pack_dir)
    if not root.is_dir():
        raise PackValidationError(f"Pack path is not a directory: {root}")

    source_digest_before, file_digests = compute_directory_digest(root)
    issues: list[dict[str, Any]] = []
    required = STRICT_REQUIRED_FILES if profile == "strict" else COMPATIBLE_REQUIRED_FILES
    for rel in sorted(required):
        if rel not in file_digests:
            issues.append(
                _issue("missing_file", "error", f"Required file is missing: {rel}", path=rel)
            )

    # Reject unsafe absolute/traversal paths inside the tree listing.
    for relative in file_digests:
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            issues.append(
                _issue(
                    "unsafe_path",
                    "error",
                    f"Unsafe relative path inside Pack: {relative}",
                    path=relative,
                )
            )

    manifest = _read_json(root / "manifest.json", "manifest.json", issues)
    nodes = _read_jsonl(root / "graph/nodes.jsonl", "graph/nodes.jsonl", issues)
    edges = _read_jsonl(root / "graph/edges.jsonl", "graph/edges.jsonl", issues)
    evidence = _read_jsonl(root / "evidence/index.jsonl", "evidence/index.jsonl", issues)

    format_version = str(manifest.get("format_version") or "") or None
    if format_version != SUPPORTED_FORMAT_VERSION:
        issues.append(
            _issue(
                "unsupported_format_version",
                "error",
                f"Unsupported format_version: {format_version!r}",
                path="manifest.json",
            )
        )

    pack_id = str(manifest.get("pack_id") or "").strip() or None
    if not pack_id:
        issues.append(
            _issue("missing_pack_id", "error", "manifest.pack_id is required", path="manifest.json")
        )

    declared_version = str(manifest.get("version") or "").strip() or UNVERSIONED
    grammar_version = (
        str(manifest["grammar_version"]) if manifest.get("grammar_version") is not None else None
    )

    # Optional multi-language retrieval aliases. Lenient: never fail admission.
    _diagnose_retrieval_hints(manifest, issues)

    node_ids: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_id = _record_id(node, "id")
        if not node_id:
            issues.append(
                _issue(
                    "missing_node_id",
                    "error",
                    "Node record is missing id",
                    path="graph/nodes.jsonl",
                )
            )
            continue
        if node_id in node_ids:
            issues.append(
                _issue(
                    "duplicate_node_id",
                    "error",
                    f"Duplicate node id: {node_id}",
                    path="graph/nodes.jsonl",
                    record_id=node_id,
                )
            )
            continue
        node_ids[node_id] = node

    edge_ids: set[str] = set()
    for edge in edges:
        edge_id = _record_id(edge, "id")
        if not edge_id:
            issues.append(
                _issue(
                    "missing_edge_id",
                    "error",
                    "Edge record is missing id",
                    path="graph/edges.jsonl",
                )
            )
            continue
        if edge_id in edge_ids:
            issues.append(
                _issue(
                    "duplicate_edge_id",
                    "error",
                    f"Duplicate edge id: {edge_id}",
                    path="graph/edges.jsonl",
                    record_id=edge_id,
                )
            )
            continue
        edge_ids.add(edge_id)
        from_id = str(edge.get("from_id") or edge.get("from") or "")
        to_id = str(edge.get("to_id") or edge.get("to") or "")
        if from_id not in node_ids or to_id not in node_ids:
            issues.append(
                _issue(
                    "broken_edge",
                    "error",
                    f"Edge endpoints missing: {edge_id} ({from_id} -> {to_id})",
                    path="graph/edges.jsonl",
                    record_id=edge_id,
                )
            )

    evidence_ids: set[str] = set()
    for row in evidence:
        evidence_id = _evidence_id(row)
        if not evidence_id:
            issues.append(
                _issue(
                    "missing_evidence_id",
                    "error",
                    "Evidence record is missing id",
                    path="evidence/index.jsonl",
                )
            )
            continue
        if evidence_id in evidence_ids:
            issues.append(
                _issue(
                    "duplicate_evidence_id",
                    "error",
                    f"Duplicate evidence id: {evidence_id}",
                    path="evidence/index.jsonl",
                    record_id=evidence_id,
                )
            )
            continue
        evidence_ids.add(evidence_id)

    for node_id, node in node_ids.items():
        refs = _evidence_refs(node)
        if _promotion_status(node) in {"validated", "promoted"} and not refs:
            issues.append(
                _issue(
                    "missing_promoted_evidence",
                    "error",
                    f"Validated node {node_id} has no evidence refs",
                    path="graph/nodes.jsonl",
                    record_id=node_id,
                )
            )
        for ref in refs:
            if ref not in evidence_ids:
                issues.append(
                    _issue(
                        "missing_evidence_ref",
                        "error",
                        f"Node {node_id} references missing evidence {ref}",
                        path="graph/nodes.jsonl",
                        record_id=node_id,
                    )
                )

    for edge in edges:
        edge_id = _record_id(edge, "id")
        if not edge_id:
            continue
        for ref in _evidence_refs(edge):
            if ref not in evidence_ids:
                issues.append(
                    _issue(
                        "missing_evidence_ref",
                        "error",
                        f"Edge {edge_id} references missing evidence {ref}",
                        path="graph/edges.jsonl",
                        record_id=edge_id,
                    )
                )

    quality: dict[str, Any] | None = None
    quality_path = root / "quality/report.json"
    if quality_path.is_file():
        quality = _read_json(quality_path, "quality/report.json", issues) or None

    source_digest_after, _ = compute_directory_digest(root)
    digest_verified = source_digest_before == source_digest_after
    if not digest_verified:
        issues.append(
            _issue(
                "source_digest_changed",
                "error",
                "Source Pack digest changed during validation",
                path=str(root),
            )
        )

    error_issues = [issue for issue in issues if issue.get("severity") == "error"]
    status: Literal["pass", "fail"] = "fail" if error_issues else "pass"
    return PackValidationResult(
        status=status,
        profile=profile,
        pack_id=pack_id,
        declared_version=declared_version,
        format_version=format_version,
        grammar_version=grammar_version,
        source_digest=source_digest_before,
        digest_verified=digest_verified,
        issues=issues,
        manifest=manifest,
        nodes=nodes,
        edges=edges,
        evidence=evidence,
        file_digests=file_digests,
        quality=quality,
    )


def pack_store_path(workspace: Path, source_digest: str) -> Path:
    return Path(workspace) / "packs" / source_digest


def _copy_pack_immutable(source: Path, dest: Path) -> None:
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest)


def install_pack(
    session: Session,
    pack_dir: str | Path,
    *,
    workspace: Path,
    profile: AdmissionProfile = "compatible",
) -> PackInstallResult:
    """Validate and install a trusted Pack directory into the registry."""
    validation = validate_pack_directory(pack_dir, profile=profile)
    if validation.status != "pass":
        raise PackValidationError(
            f"Pack validation failed under profile={profile}",
            issues=validation.issues,
        )
    assert validation.pack_id is not None

    existing_by_digest = session.scalar(
        select(PackInstall).where(PackInstall.source_digest == validation.source_digest)
    )
    if existing_by_digest is not None:
        return PackInstallResult(
            status=existing_by_digest.status,
            pack_id=existing_by_digest.pack_id,
            declared_version=existing_by_digest.declared_version,
            source_digest=existing_by_digest.source_digest,
            pack_install_id=existing_by_digest.id,
            noop=True,
            storage_uri=existing_by_digest.storage_uri,
            admission_report={
                "noop": True,
                "reason": "same_digest",
                "profile": profile,
                "source_digest": validation.source_digest,
            },
        )

    conflict = session.scalar(
        select(PackInstall).where(
            PackInstall.pack_id == validation.pack_id,
            PackInstall.declared_version == validation.declared_version,
        )
    )
    if conflict is not None and conflict.source_digest != validation.source_digest:
        raise PackConflictError(
            (
                f"Pack conflict for {validation.pack_id}@{validation.declared_version}: "
                f"existing digest {conflict.source_digest} != incoming {validation.source_digest}"
            ),
            pack_id=validation.pack_id,
            declared_version=validation.declared_version,
            existing_digest=conflict.source_digest,
            incoming_digest=validation.source_digest,
        )

    storage = pack_store_path(workspace, validation.source_digest)
    _copy_pack_immutable(Path(pack_dir), storage)
    # Prove installed copy matches source digest.
    installed_digest, _ = compute_directory_digest(storage)
    if installed_digest != validation.source_digest:
        if storage.exists():
            shutil.rmtree(storage)
        raise PackValidationError(
            "Installed copy digest does not match source digest",
            issues=[
                _issue(
                    "install_digest_mismatch",
                    "error",
                    f"source={validation.source_digest} installed={installed_digest}",
                )
            ],
        )

    now = utcnow()
    # One active revision per pack_id: supersede prior actives for this pack.
    prior_actives = session.scalars(
        select(PackInstall).where(
            PackInstall.pack_id == validation.pack_id,
            PackInstall.status == "active",
        )
    ).all()
    for prior in prior_actives:
        prior.status = "superseded"

    admission_report = {
        "profile": profile,
        "status": "pass",
        "source_digest": validation.source_digest,
        "digest_verified": validation.digest_verified,
        "issues": validation.issues,
        "node_count": len(validation.nodes),
        "edge_count": len(validation.edges),
        "evidence_count": len(validation.evidence),
        "file_count": len(validation.file_digests),
    }
    install = PackInstall(
        pack_id=validation.pack_id,
        declared_version=validation.declared_version,
        format_version=validation.format_version or SUPPORTED_FORMAT_VERSION,
        grammar_version=validation.grammar_version,
        source_digest=validation.source_digest,
        source_type="directory",
        source_location=str(Path(pack_dir).resolve()),
        storage_uri=str(storage),
        admission_profile=profile,
        status="active",
        original_manifest_json=validation.manifest,
        original_quality_json=validation.quality,
        admission_report_json=admission_report,
        created_at=now,
        activated_at=now,
    )
    session.add(install)
    session.flush()

    for relative, digest in sorted(validation.file_digests.items()):
        session.add(
            PackFile(
                pack_install_id=install.id,
                relative_path=relative,
                role=_FILE_ROLES.get(relative, "content"),
                media_type=_media_type_for(relative),
                declared_hash=None,
                computed_hash=digest,
                byte_count=(storage / relative).stat().st_size,
                storage_uri=str(storage / relative),
                validation_status="ok",
            )
        )

    for node in validation.nodes:
        local_id = _record_id(node, "id")
        props = _mapping_field(node, "properties")
        quality = _mapping_field(node, "quality")
        session.add(
            PackNode(
                pack_install_id=install.id,
                local_node_id=local_id,
                global_node_id=global_record_id(
                    pack_id=validation.pack_id,
                    declared_version=validation.declared_version,
                    source_digest=validation.source_digest,
                    kind="node",
                    local_id=local_id,
                ),
                space=str(node["space"]) if node.get("space") is not None else None,
                node_type=str(node["node_type"]) if node.get("node_type") is not None else None,
                label=str(node["label"]) if node.get("label") is not None else None,
                properties_json=props,
                quality_json=quality,
                record_hash=stable_hash(node),
                evidence_refs_json=_evidence_refs(node),
            )
        )

    for edge in validation.edges:
        local_id = _record_id(edge, "id")
        from_id = str(edge.get("from_id") or edge.get("from") or "")
        to_id = str(edge.get("to_id") or edge.get("to") or "")
        props = _mapping_field(edge, "properties")
        confidence_raw = edge.get("confidence")
        confidence: float | None
        try:
            confidence = float(confidence_raw) if confidence_raw is not None else None
        except (TypeError, ValueError):
            confidence = None
        session.add(
            PackEdge(
                pack_install_id=install.id,
                local_edge_id=local_id,
                global_edge_id=global_record_id(
                    pack_id=validation.pack_id,
                    declared_version=validation.declared_version,
                    source_digest=validation.source_digest,
                    kind="edge",
                    local_id=local_id,
                ),
                from_local_id=from_id,
                to_local_id=to_id,
                from_global_id=global_record_id(
                    pack_id=validation.pack_id,
                    declared_version=validation.declared_version,
                    source_digest=validation.source_digest,
                    kind="node",
                    local_id=from_id,
                ),
                to_global_id=global_record_id(
                    pack_id=validation.pack_id,
                    declared_version=validation.declared_version,
                    source_digest=validation.source_digest,
                    kind="node",
                    local_id=to_id,
                ),
                from_space=str(edge["from_space"]) if edge.get("from_space") is not None else None,
                to_space=str(edge["to_space"]) if edge.get("to_space") is not None else None,
                relation=str(edge["relation"]) if edge.get("relation") is not None else None,
                properties_json=props,
                confidence=confidence,
                record_hash=stable_hash(edge),
                evidence_refs_json=_evidence_refs(edge),
            )
        )

    for row in validation.evidence:
        local_id = _evidence_id(row)
        source = _mapping_field(row, "source")
        parser = _mapping_field(row, "parser")
        location = _mapping_field(row, "location")
        text_value: str | None = None
        for key in ("text", "snippet", "content", "body"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                text_value = value
                break
        asset_path = source.get("path")
        session.add(
            PackEvidence(
                pack_install_id=install.id,
                local_evidence_id=local_id,
                global_evidence_id=global_record_id(
                    pack_id=validation.pack_id,
                    declared_version=validation.declared_version,
                    source_digest=validation.source_digest,
                    kind="evidence",
                    local_id=local_id,
                ),
                kind=str(row["kind"]) if row.get("kind") is not None else None,
                source_json=source,
                parser_json=parser,
                ocr_json=_optional_mapping_field(row, "ocr"),
                vision_json=_normalise_evidence_vision(row),
                location_json=location,
                links_json=_normalise_evidence_links(row),
                content_hash=_normalise_evidence_content_hash(row),
                asset_ref=str(asset_path) if asset_path else None,
                text=text_value,
                raw_record_json=dict(row),
                record_hash=stable_hash(row),
            )
        )

    session.flush()
    # D3: deterministic flip-condition scan against newly installed evidence.
    # Never re-runs deliberation; candidate triggers + events only.
    from openoyster.services.flip_monitoring import scan_pack_install

    scan_pack_install(session, install.id)
    session.flush()
    return PackInstallResult(
        status=install.status,
        pack_id=install.pack_id,
        declared_version=install.declared_version,
        source_digest=install.source_digest,
        pack_install_id=install.id,
        noop=False,
        storage_uri=install.storage_uri,
        admission_report=admission_report,
    )


def list_active_installs(session: Session) -> list[PackInstall]:
    return list(
        session.scalars(
            select(PackInstall).where(PackInstall.status == "active").order_by(PackInstall.pack_id)
        ).all()
    )


def get_active_install(session: Session, pack_id: str) -> PackInstall | None:
    return session.scalar(
        select(PackInstall).where(PackInstall.pack_id == pack_id, PackInstall.status == "active")
    )
