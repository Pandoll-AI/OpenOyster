# P0-F2 Full-layout Fixture

Pack id: `p0-f2-full-layout`

This OpenCrab Pack v1 fixture is a documented strict full layout used by
OpenOyster runtime tests. It contains the canonical graph, evidence index,
producer `quality/report.json`, Neo4j-shaped import/export artifacts, and
marketplace metadata.

## Graph

- `resource:doc:1` contains `evidence:1`
- `evidence:1` supports `claim:1`

## Quality

Producer promotion status is **validated** / `quality/report.json` status `pass`.
No live Neo4j import/export was executed. The Neo4j-shaped files under `neo4j/`
are structurally consistent synthetic fixture data for layout and cross-file id
tests, not a service-backed snapshot. `quality/report.json` therefore records
`neo4j_import` as `skip`.

## Usage

`neo4j/import.cypher` is sample Cypher for optional local replay. Prefer the
canonical JSONL graph files for SaaS-style ingest. Treat
`neo4j/export_status.json` as fixture/synthetic metadata
(`origin=fixture_synthetic`, `live_neo4j_executed=false`).
