from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "generated"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def manifest(*, pack_id: str, title: str, description: str, counts: dict[str, int]) -> dict[str, Any]:
    return {
        "pack_id": pack_id,
        "title": title,
        "version": "1.0.0",
        "format_version": "opencrab-pack-v1",
        "grammar_version": "1.0.0",
        "created_at": "2026-07-14T00:00:00Z",
        "created_by": "OpenOyster practical Pack experiment",
        "source": {
            "mode": "public_repository_docs",
            "label": "AlexAI-MCP public GitHub repositories",
            "description": description,
        },
        "counts": counts,
        "quality": {
            "promotion_status": "validated",
            "parsing_completeness": 1.0,
            "evidence_coverage": 1.0 if counts["evidence"] else 0.0,
            "graph_reference_integrity": 1.0,
        },
        "artifacts": {
            "nodes": "graph/nodes.jsonl",
            "edges": "graph/edges.jsonl",
            "evidence_index": "evidence/index.jsonl",
        },
    }


def build_gap_pack() -> None:
    root = GENERATED / "alexai-ecosystem-gap"
    write_json(
        root / "manifest.json",
        manifest(
            pack_id="alexai-ecosystem-gap",
            title="AlexAI Ecosystem Decision Gap",
            description="A valid empty baseline used to prove evidence-aware abstention.",
            counts={"nodes": 0, "edges": 0, "evidence": 0, "documents": 0},
        ),
    )
    write_jsonl(root / "graph/nodes.jsonl", [])
    write_jsonl(root / "graph/edges.jsonl", [])
    write_jsonl(root / "evidence/index.jsonl", [])


def build_mission_handoff_pack() -> None:
    root = GENERATED / "alexai-mission-handoff"
    evidence = [
        {
            "evidence_id": "evidence:opencrab-relationship:1",
            "kind": "text_chunk",
            "source": {
                "title": "LocalCrab and OpenCrab Relationship",
                "url": "https://github.com/AlexAI-MCP/OpenCrab/blob/main/docs/localcrab-opencrab-relationship.md",
            },
            "parser": {"status": "ok", "method": "native_markdown"},
            "location": {"section": "Recommended positioning language"},
            "text": "Build and validate ontology packs locally, then bring them to opencrab.sh for distribution and MCP access.",
        },
        {
            "evidence_id": "evidence:crabharness:1",
            "kind": "text_chunk",
            "source": {
                "title": "CrabHarness README",
                "url": "https://github.com/AlexAI-MCP/OpenCrab/blob/main/crabharness/README.md",
            },
            "parser": {"status": "ok", "method": "native_markdown"},
            "location": {"section": "Overview"},
            "text": "Mission-first control plane for plugin-based data collection.",
        },
        {
            "evidence_id": "evidence:crabharness:2",
            "kind": "text_chunk",
            "source": {
                "title": "CrabHarness README",
                "url": "https://github.com/AlexAI-MCP/OpenCrab/blob/main/crabharness/README.md",
            },
            "parser": {"status": "ok", "method": "native_markdown"},
            "location": {"section": "Architecture"},
            "text": "CrabHarness validates artifacts and emits promotion packages for the OpenCrab ontology graph.",
        },
    ]
    nodes = [
        {
            "id": "resource:opencrab-relationship",
            "label": "OpenCrab relationship document",
            "space": "resource",
            "node_type": "Document",
            "properties": {"title": "LocalCrab and OpenCrab Relationship", "status": "validated"},
            "evidence_refs": ["evidence:opencrab-relationship:1"],
        },
        {
            "id": "resource:crabharness-readme",
            "label": "CrabHarness README",
            "space": "resource",
            "node_type": "Document",
            "properties": {"title": "CrabHarness README", "status": "validated"},
            "evidence_refs": ["evidence:crabharness:1", "evidence:crabharness:2"],
        },
        {
            "id": "evidence:opencrab-relationship:1",
            "label": "Pack build and distribution boundary",
            "space": "evidence",
            "node_type": "Evidence",
            "properties": {"status": "validated"},
            "evidence_refs": ["evidence:opencrab-relationship:1"],
        },
        {
            "id": "evidence:crabharness:1",
            "label": "Mission-first collection control plane",
            "space": "evidence",
            "node_type": "Evidence",
            "properties": {"status": "validated"},
            "evidence_refs": ["evidence:crabharness:1"],
        },
        {
            "id": "evidence:crabharness:2",
            "label": "Validated promotion package output",
            "space": "evidence",
            "node_type": "Evidence",
            "properties": {"status": "validated"},
            "evidence_refs": ["evidence:crabharness:2"],
        },
        {
            "id": "claim:mission-handoff",
            "label": "Knowledge gaps can be handed off as collection missions",
            "space": "claim",
            "node_type": "Claim",
            "properties": {
                "statement": "OpenOyster can hand a structured knowledge-gap mission to CrabHarness while OpenCrab remains responsible for Pack production.",
                "status": "validated",
                "confidence": 0.9,
            },
            "evidence_refs": [
                "evidence:opencrab-relationship:1",
                "evidence:crabharness:1",
                "evidence:crabharness:2",
            ],
        },
    ]
    edges = [
        {
            "id": "edge:contains:relationship",
            "from_id": "resource:opencrab-relationship",
            "to_id": "evidence:opencrab-relationship:1",
            "from_space": "resource",
            "to_space": "evidence",
            "relation": "contains",
            "confidence": 1.0,
            "evidence_refs": ["evidence:opencrab-relationship:1"],
        },
        {
            "id": "edge:contains:harness-1",
            "from_id": "resource:crabharness-readme",
            "to_id": "evidence:crabharness:1",
            "from_space": "resource",
            "to_space": "evidence",
            "relation": "contains",
            "confidence": 1.0,
            "evidence_refs": ["evidence:crabharness:1"],
        },
        {
            "id": "edge:contains:harness-2",
            "from_id": "resource:crabharness-readme",
            "to_id": "evidence:crabharness:2",
            "from_space": "resource",
            "to_space": "evidence",
            "relation": "contains",
            "confidence": 1.0,
            "evidence_refs": ["evidence:crabharness:2"],
        },
        {
            "id": "edge:supports:mission-relationship",
            "from_id": "evidence:opencrab-relationship:1",
            "to_id": "claim:mission-handoff",
            "from_space": "evidence",
            "to_space": "claim",
            "relation": "supports",
            "confidence": 0.9,
            "evidence_refs": ["evidence:opencrab-relationship:1"],
        },
        {
            "id": "edge:supports:mission-harness-1",
            "from_id": "evidence:crabharness:1",
            "to_id": "claim:mission-handoff",
            "from_space": "evidence",
            "to_space": "claim",
            "relation": "supports",
            "confidence": 0.9,
            "evidence_refs": ["evidence:crabharness:1"],
        },
        {
            "id": "edge:supports:mission-harness-2",
            "from_id": "evidence:crabharness:2",
            "to_id": "claim:mission-handoff",
            "from_space": "evidence",
            "to_space": "claim",
            "relation": "supports",
            "confidence": 0.9,
            "evidence_refs": ["evidence:crabharness:2"],
        },
    ]
    write_json(
        root / "manifest.json",
        manifest(
            pack_id="alexai-mission-handoff",
            title="AlexAI Mission Handoff Evidence",
            description="Evidence for choosing the next OpenOyster and OpenCrab integration seam.",
            counts={"nodes": len(nodes), "edges": len(edges), "evidence": len(evidence), "documents": 2},
        ),
    )
    write_jsonl(root / "graph/nodes.jsonl", nodes)
    write_jsonl(root / "graph/edges.jsonl", edges)
    write_jsonl(root / "evidence/index.jsonl", evidence)


def main() -> None:
    if GENERATED.exists():
        shutil.rmtree(GENERATED)
    build_gap_pack()
    build_mission_handoff_pack()
    print(GENERATED.relative_to(ROOT.parent.parent))


if __name__ == "__main__":
    main()
