# Changelog

All notable changes are documented here. OpenOyster is pre-`1.0`; compatibility may change between minor releases.

## 0.3.0 — open-source evidence quality alpha

### Retrieval and evidence

- Added retrieval result metadata, matched terms, SQLite FTS5/PostgreSQL full-text auto retrieval, source-diversity caps, and stance-judge evidence filtering.
- Added evidence/provenance inspection helpers, CLI commands, and read API endpoints.
- Dashboard now shows evidence counts, source diversity, and provenance availability.

### Connectors and evaluation

- Added read-only RSS ingestion from YAML feed lists.
- Added read-only GitHub release and issue ingestion with optional `OPENOYSTER_GITHUB_TOKEN`.
- Added deterministic fixture evaluation for signal type, counter-evidence, and traceability regressions.
- Added sample evaluation fixtures under `examples/eval`.

### Operations and documentation

- Added `openoyster doctor-dev` for local development toolchain checks.
- Updated README, API, connector, and policy documentation for the new 0.3 surfaces.

### Known limitations

- PostgreSQL full-text mode is optional; vector retrieval is still not included by default.
- GitHub issue comments, RSS article crawling, authenticated feeds, RBAC, approval queues, and external write tools remain out of scope.

## 0.2.0 — audited product-oriented alpha

### Reliability

- Replaced unsafe duplicate rollback behaviour with per-item durable intake state.
- Added safe filtered event checkpoints, partial-ack protection, idempotent event emission, loop leases, and loop-run telemetry.
- Added chunk/task retry state and post-commit archive maintenance.
- Added Alembic schema and migration command.

### Intelligence loops

- Split planning from execution and maintenance from intake.
- Added registered support, counter-evidence, baseline, and hypothesis-brief tools.
- Added evidence-derived hypothesis confidence, contradiction, staleness, and decision traces.
- Added grounded utilisation and system-behaviour premise review.

### Providers and connectors

- Remote OpenAI-compatible provider now parses and uses structured remote output.
- Remote fallback records provider identity and warnings.
- Added PDF, DOCX, HTML, YAML, JSONL, TSV, and guarded public HTTP ingestion.

### Evaluation and optimisation

- Replaced prose-length self-rating with evidence posture and verified completion metrics.
- Added explicit artifact feedback and trace outcome labels.
- Added bounded labelled replay, fresh-label shadow evaluation, policy expiry, promotion, rejection, and experiment records.

### Security and operations

- Added default-disabled mutation API, shared-key auth, HTML escaping, SSRF controls, readiness/doctor checks, non-root container, PostgreSQL Compose deployment, and migration service.
- Added detailed user, Korean user, contributor, architecture, operations, policy, API, connector, threat-model, audit, and release documentation.
- Added Ruff, mypy, CI build verification, CLI lifecycle tests, migration tests, and 81% measured statement coverage in the packaged release.

### Known limitations

- Lexical bounded retrieval, no vector index.
- No RBAC, multi-tenancy, secret-manager integration, or formal regulatory validation.
- No distributed broker or load/chaos certification.
- Limited built-in source and action connectors.

## 0.1.0 — initial prototype

- Initial event-loop scaffold, SQLite persistence, CLI/API/dashboard, local heuristics, and basic documentation.
- This version was subsequently judged demo-grade because event safety, provider behaviour, evaluation, optimisation, security, migrations, and tests were insufficient.
