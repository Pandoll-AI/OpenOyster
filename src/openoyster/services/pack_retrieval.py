"""Deterministic Pack-aware lexical retrieval with graph expansion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openoyster.models import PackEdge, PackEvidence, PackInstall, PackNode
from openoyster.scoring import clamp, tokenize

# Search routing only — never evidence body / citation surface.
MATCHED_VIA_LEXICAL: str = "lexical"
MATCHED_VIA_MANIFEST_HINT: str = "manifest_hint"

# Admission/search caps for optional manifest retrieval_hints (routing aids only).
MAX_RETRIEVAL_HINTS: int = 32
MAX_RETRIEVAL_HINT_CHARS: int = 200


@dataclass(frozen=True)
class PackRetrievalHit:
    kind: str
    local_id: str
    global_id: str
    pack_id: str
    declared_version: str
    source_digest: str
    pack_install_id: int
    score: float
    text: str
    matched_terms: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    matched_via: str = MATCHED_VIA_LEXICAL


@dataclass
class PackRetrievalResult:
    query: str
    pack_scope: list[dict[str, str]]
    nodes: list[PackNode] = field(default_factory=list)
    edges: list[PackEdge] = field(default_factory=list)
    evidence: list[PackEvidence] = field(default_factory=list)
    hits: list[PackRetrievalHit] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def has_context(self) -> bool:
        return bool(self.nodes or self.edges or self.evidence)


def _lexical_score(query: str, text: str) -> tuple[float, list[str]]:
    query_tokens = tokenize(query)
    text_tokens = tokenize(text)
    if not query_tokens:
        return 0.0, []
    folded = text.casefold()
    matched = sorted(
        token for token in query_tokens if token in text_tokens or token in folded
    )
    if not matched:
        # Substring fallback for short queries / punctuation-heavy labels.
        if query.casefold() in folded:
            return 0.55, [query.casefold()]
        return 0.0, []
    coverage = len(matched) / len(query_tokens)
    jaccard = len(set(matched) & text_tokens) / len(query_tokens | text_tokens) if text_tokens else 0.0
    return clamp(0.75 * coverage + 0.25 * jaccard), matched


def _node_surface(node: PackNode) -> str:
    props = node.properties_json or {}
    parts = [
        node.label or "",
        node.node_type or "",
        node.space or "",
        node.local_node_id,
        str(props.get("statement") or ""),
        str(props.get("title") or ""),
        json_safe_surface(props),
    ]
    return " ".join(part for part in parts if part)


def json_safe_surface(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {json_safe_surface(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(json_safe_surface(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _evidence_surface(row: PackEvidence) -> str:
    source = row.source_json or {}
    location = row.location_json or {}
    parts = [
        row.text or "",
        row.kind or "",
        row.local_evidence_id,
        str(source.get("path") or ""),
        str(source.get("title") or ""),
        str(source.get("url") or ""),
        json_safe_surface(location),
    ]
    return " ".join(part for part in parts if part)


def normalize_retrieval_hints(raw: Any) -> list[str]:
    """Accept only a list of non-empty strings; otherwise return empty.

    Invalid shapes are ignored (admission records diagnostics separately).
    Hints are search routing aids — never citation-grade evidence text.
    Caps: at most MAX_RETRIEVAL_HINTS items, each at most MAX_RETRIEVAL_HINT_CHARS.
    """
    if not isinstance(raw, list):
        return []
    hints: list[str] = []
    for item in raw:
        if len(hints) >= MAX_RETRIEVAL_HINTS:
            break
        if isinstance(item, str):
            text = item.strip()
            if text:
                hints.append(text[:MAX_RETRIEVAL_HINT_CHARS])
    return hints


def install_retrieval_hints(install: PackInstall) -> list[str]:
    manifest = install.original_manifest_json or {}
    return normalize_retrieval_hints(manifest.get("retrieval_hints"))


def _hints_surface(hints: list[str]) -> str:
    return " ".join(hints)


def _active_installs(
    session: Session, pack_ids: list[str] | None
) -> list[PackInstall]:
    stmt = select(PackInstall).where(PackInstall.status == "active")
    if pack_ids:
        stmt = stmt.where(PackInstall.pack_id.in_(pack_ids))
    return list(session.scalars(stmt.order_by(PackInstall.pack_id)).all())


def _installs_by_ids(session: Session, pack_install_ids: list[int]) -> list[PackInstall]:
    if not pack_install_ids:
        return []
    # Preserve caller order while de-duplicating.
    ordered_ids = list(dict.fromkeys(pack_install_ids))
    rows = list(
        session.scalars(select(PackInstall).where(PackInstall.id.in_(ordered_ids))).all()
    )
    by_id = {row.id: row for row in rows}
    return [by_id[item_id] for item_id in ordered_ids if item_id in by_id]


def search_pack_context(
    session: Session,
    query: str,
    *,
    pack_ids: list[str] | None = None,
    pack_install_ids: list[int] | None = None,
    top_k: int = 20,
    minimum_score: float = 0.12,
) -> PackRetrievalResult:
    """Search Pack nodes and evidence, then expand edges and evidence refs.

    Default scope is active installs only (``pack_ids`` narrows that set).
    When ``pack_install_ids`` is provided, retrieval is frozen to those exact
    install rows and does not re-resolve active Packs.
    """
    if pack_install_ids is not None:
        installs = _installs_by_ids(session, pack_install_ids)
        scope_reason = "no_frozen_installs"
        diagnostics_extra: dict[str, Any] = {
            "scope_mode": "frozen_install_ids",
            "requested_install_ids": list(pack_install_ids),
        }
    else:
        installs = _active_installs(session, pack_ids)
        scope_reason = "no_active_packs"
        diagnostics_extra = {"scope_mode": "active_packs"}

    pack_scope = [
        {
            "pack_id": install.pack_id,
            "declared_version": install.declared_version,
            "source_digest": install.source_digest,
            "pack_install_id": str(install.id),
        }
        for install in installs
    ]
    if not installs:
        return PackRetrievalResult(
            query=query,
            pack_scope=[],
            diagnostics={
                "reason": scope_reason,
                "matched_hit_count": 0,
                **diagnostics_extra,
            },
        )

    install_by_id = {install.id: install for install in installs}
    install_ids = list(install_by_id)

    nodes = list(
        session.scalars(select(PackNode).where(PackNode.pack_install_id.in_(install_ids))).all()
    )
    evidence_rows = list(
        session.scalars(
            select(PackEvidence).where(PackEvidence.pack_install_id.in_(install_ids))
        ).all()
    )
    edges = list(
        session.scalars(select(PackEdge).where(PackEdge.pack_install_id.in_(install_ids))).all()
    )

    hits: list[PackRetrievalHit] = []
    matched_node_ids: set[int] = set()
    matched_evidence_ids: set[int] = set()
    matched_local_nodes: set[tuple[int, str]] = set()
    matched_local_evidence: set[tuple[int, str]] = set()

    # 1st defense: optional manifest retrieval_hints as install-level search surface.
    # Hints route to evidence; they never become hit.text / citation body.
    hint_match_by_install: dict[int, tuple[float, list[str], list[str]]] = {}
    for install in installs:
        hints = install_retrieval_hints(install)
        if not hints:
            continue
        score, matched = _lexical_score(query, _hints_surface(hints))
        if score >= minimum_score:
            hint_match_by_install[install.id] = (score, matched, hints)

    for node in nodes:
        score, matched = _lexical_score(query, _node_surface(node))
        if score < minimum_score:
            continue
        install = install_by_id[node.pack_install_id]
        hits.append(
            PackRetrievalHit(
                kind="node",
                local_id=node.local_node_id,
                global_id=node.global_node_id,
                pack_id=install.pack_id,
                declared_version=install.declared_version,
                source_digest=install.source_digest,
                pack_install_id=install.id,
                score=score,
                text=_node_surface(node),
                matched_terms=matched,
                metadata={
                    "node_type": node.node_type,
                    "space": node.space,
                    "matched_via": MATCHED_VIA_LEXICAL,
                },
                matched_via=MATCHED_VIA_LEXICAL,
            )
        )
        matched_node_ids.add(node.id)
        matched_local_nodes.add((node.pack_install_id, node.local_node_id))

    for row in evidence_rows:
        score, matched = _lexical_score(query, _evidence_surface(row))
        if score < minimum_score:
            continue
        install = install_by_id[row.pack_install_id]
        hits.append(
            PackRetrievalHit(
                kind="evidence",
                local_id=row.local_evidence_id,
                global_id=row.global_evidence_id,
                pack_id=install.pack_id,
                declared_version=install.declared_version,
                source_digest=install.source_digest,
                pack_install_id=install.id,
                score=score,
                text=_evidence_surface(row),
                matched_terms=matched,
                metadata={"kind": row.kind, "matched_via": MATCHED_VIA_LEXICAL},
                matched_via=MATCHED_VIA_LEXICAL,
            )
        )
        matched_evidence_ids.add(row.id)
        matched_local_evidence.add((row.pack_install_id, row.local_evidence_id))

    # Hint-only routing: promote pack evidence/nodes when query matches hints.
    for install_id, (hint_score, hint_matched, _hints) in hint_match_by_install.items():
        install = install_by_id[install_id]
        for node in nodes:
            if node.pack_install_id != install_id:
                continue
            if node.id in matched_node_ids:
                continue
            hits.append(
                PackRetrievalHit(
                    kind="node",
                    local_id=node.local_node_id,
                    global_id=node.global_node_id,
                    pack_id=install.pack_id,
                    declared_version=install.declared_version,
                    source_digest=install.source_digest,
                    pack_install_id=install.id,
                    score=hint_score,
                    text=_node_surface(node),
                    matched_terms=list(hint_matched),
                    metadata={
                        "node_type": node.node_type,
                        "space": node.space,
                        "matched_via": MATCHED_VIA_MANIFEST_HINT,
                    },
                    matched_via=MATCHED_VIA_MANIFEST_HINT,
                )
            )
            matched_node_ids.add(node.id)
            matched_local_nodes.add((node.pack_install_id, node.local_node_id))
        for row in evidence_rows:
            if row.pack_install_id != install_id:
                continue
            if row.id in matched_evidence_ids:
                continue
            hits.append(
                PackRetrievalHit(
                    kind="evidence",
                    local_id=row.local_evidence_id,
                    global_id=row.global_evidence_id,
                    pack_id=install.pack_id,
                    declared_version=install.declared_version,
                    source_digest=install.source_digest,
                    pack_install_id=install.id,
                    score=hint_score,
                    # Evidence surface only — never the hint strings themselves.
                    text=_evidence_surface(row),
                    matched_terms=list(hint_matched),
                    metadata={
                        "kind": row.kind,
                        "matched_via": MATCHED_VIA_MANIFEST_HINT,
                    },
                    matched_via=MATCHED_VIA_MANIFEST_HINT,
                )
            )
            matched_evidence_ids.add(row.id)
            matched_local_evidence.add((row.pack_install_id, row.local_evidence_id))

    hits.sort(key=lambda hit: (-hit.score, hit.kind, hit.global_id))
    hits = hits[:top_k]

    # Graph expansion: supporting edges and evidence refs for matched nodes.
    expanded_edge_ids: set[int] = set()
    for edge in edges:
        endpoints = {
            (edge.pack_install_id, edge.from_local_id),
            (edge.pack_install_id, edge.to_local_id),
        }
        if endpoints & matched_local_nodes:
            expanded_edge_ids.add(edge.id)
            matched_local_nodes.add((edge.pack_install_id, edge.from_local_id))
            matched_local_nodes.add((edge.pack_install_id, edge.to_local_id))
            for ref in edge.evidence_refs_json or []:
                matched_local_evidence.add((edge.pack_install_id, ref))

    for node in nodes:
        if node.id in matched_node_ids or (node.pack_install_id, node.local_node_id) in matched_local_nodes:
            matched_node_ids.add(node.id)
            for ref in node.evidence_refs_json or []:
                matched_local_evidence.add((node.pack_install_id, ref))

    for row in evidence_rows:
        if (row.pack_install_id, row.local_evidence_id) in matched_local_evidence:
            matched_evidence_ids.add(row.id)

    selected_nodes = [node for node in nodes if node.id in matched_node_ids]
    selected_edges = [edge for edge in edges if edge.id in expanded_edge_ids]
    selected_evidence = [row for row in evidence_rows if row.id in matched_evidence_ids]

    # Keep deterministic order.
    selected_nodes.sort(key=lambda item: (item.pack_install_id, item.local_node_id))
    selected_edges.sort(key=lambda item: (item.pack_install_id, item.local_edge_id))
    selected_evidence.sort(key=lambda item: (item.pack_install_id, item.local_evidence_id))

    hint_hit_count = sum(
        1 for hit in hits if hit.matched_via == MATCHED_VIA_MANIFEST_HINT
    )
    return PackRetrievalResult(
        query=query,
        pack_scope=pack_scope,
        nodes=selected_nodes,
        edges=selected_edges,
        evidence=selected_evidence,
        hits=hits,
        diagnostics={
            "matched_hit_count": len(hits),
            "node_count": len(selected_nodes),
            "edge_count": len(selected_edges),
            "evidence_count": len(selected_evidence),
            "active_pack_count": len(installs),
            "install_count": len(installs),
            "minimum_score": minimum_score,
            "top_k": top_k,
            "manifest_hint_install_count": len(hint_match_by_install),
            "manifest_hint_hit_count": hint_hit_count,
            **diagnostics_extra,
        },
    )
