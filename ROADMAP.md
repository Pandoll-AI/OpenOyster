# OpenOyster Roadmap

The roadmap prioritises evidence quality and operational trust over adding more agent theatre.

## 0.3 — open-source evidence quality alpha

Shipped in `0.3.0`:

- Retrieval metadata, source-diversity caps, counter-evidence query mode, and optional PostgreSQL full-text mode.
- Evidence and artifact provenance inspection through CLI and read APIs.
- Deterministic domain evaluation fixtures for signal type, counter-evidence, and traceability regressions.
- RSS and GitHub release/issue read connectors with bounded fetches and provenance metadata.
- Development toolchain diagnosis through `openoyster doctor-dev`.

Deferred from the original 0.3 ambition:

- PostgreSQL/pgvector hybrid retrieval with lexical fallback.
- Entity- and time-aware retrieval plus re-ranking.
- Prediction/falsification tracking and calibration metrics.
- Rich evidence inspection UI.
- RSS/GitHub scheduling, authenticated feeds, issue comments, and crawler behaviour.

## 0.4 — governance and actions

- User identity and RBAC.
- Approval queue for external actions and mission/policy changes.
- Signed approval/audit records and secret-manager adapters.
- Connector/tool plugin SDK with capability manifests.
- Cost, latency, missed-signal, and diversity-aware policy objectives.
- Policy replay and shadow comparison UI.

## 0.5 — distributed operations

- PostgreSQL notification or broker-backed worker wake-up.
- Redis/NATS/Kafka transport adapter while preserving lineage.
- Multi-worker race, kill, timeout, and network-fault test suite.
- OpenTelemetry metrics/traces and reference dashboards.
- Backup/restore and rolling-upgrade integration tests.
- SBOM, signed images, and dependency lock strategy.

## Before 1.0

- Stable event and plugin contracts.
- Multi-tenant isolation or explicit single-tenant commitment.
- Retention/deletion workflows.
- Formal threat review and penetration test.
- Large-corpus benchmark and capacity envelope.
- End-to-end domain validation with real downstream users.
- Documented compatibility and deprecation policy.
