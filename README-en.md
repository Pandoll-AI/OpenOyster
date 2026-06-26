# OpenOyster

[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/Pandoll-AI/OpenOyster)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11--3.13-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![Framework](https://img.shields.io/badge/API-FastAPI-009688.svg?logo=fastapi&logoColor=white)](docs/API_REFERENCE.md)
[![Database](https://img.shields.io/badge/database-SQLite%20%7C%20PostgreSQL-336791.svg)](docs/OPERATIONS.md)
[![Language](https://img.shields.io/badge/language-English-lightgrey.svg)](README-en.md)
[![Korean README](https://img.shields.io/badge/README-Korean-red.svg)](README.md)

**OpenOyster** is a product-oriented alpha for building durable, self-triggering intelligence systems. It watches heterogeneous documents, extracts signals and falsifiable hypotheses, plans bounded work, executes internal tools, creates decision artifacts, records downstream feedback, tunes guarded policy parameters through replay and shadow evaluation, and periodically reviews whether the whole system is looking at the wrong scope.

```text
Sources -> Documents -> Claims / Signals -> Hypotheses -> Triggers -> Tasks -> Artifacts
   ^                                                                     |
   |----- Meta-premise review <- Policy replay/shadow tuning <- Evaluation
```

OpenOyster is **not** a magical fully autonomous general agent and it is **not yet an enterprise production platform**. It is a working, auditable, open-source foundation that can be run, tested, extended, and deployed as a small service.

## Highlights

- Durable SQL event stream with per-loop checkpoints, idempotency keys, and database-backed worker leases.
- Ten independent loops for intake, maintenance, extraction, hypothesis, planning, execution, utilisation, evaluation, optimisation, and meta-premise review.
- Traceable documents, chunks, claims, signals, hypotheses, evidence edges, tasks, runs, artifacts, feedback, policies, experiments, and mission charters.
- Local deterministic extraction plus an OpenAI-compatible structured JSON provider with visible fallback warnings.
- Filesystem, guarded HTTP, RSS, and read-only GitHub release/issue ingestion.
- Evidence-aware internal tools for hypothesis briefs, support scans, counter-evidence scans, and corpus baselines.
- Retrieval metadata with matched terms, source-diversity caps, counter-evidence query mode, and optional PostgreSQL full-text mode.
- Evidence/provenance inspection through CLI and read APIs without default raw-document body exposure.
- Deterministic fixture evaluation for signal type, counter-evidence, and traceability regressions.
- FastAPI service, read-only dashboard, API-key-protected mutations, Typer CLI, Alembic migrations, Docker Compose, CI, manuals, and tests.

## Status

Release `0.3.0` is a **shareable product-oriented alpha/reference implementation** focused on open-source installability, evidence quality, read connectors, and inspection.

Known boundaries:

- no default vector index;
- no RBAC or multi-tenant model;
- no secret-manager integration;
- no browser-scale crawler;
- no external write-action SDK;
- no distributed broker;
- no load, chaos, or security certification.

## Quick Start

Python 3.11-3.13 is supported.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

cp .env.example .env
# Set OPENOYSTER_API_KEY in .env before exposing the API.

openoyster init
openoyster ingest examples/inbox
openoyster run --cycles 4 --sleep 0
openoyster status
openoyster doctor
openoyster serve --host 127.0.0.1 --port 8080
```

Open the dashboard at `http://127.0.0.1:8080` and OpenAPI at `/docs`.

## Docker Compose

```bash
cp .env.example .env
# Replace OPENOYSTER_API_KEY and OPENOYSTER_POSTGRES_PASSWORD.
mkdir -p workspace/inbox
cp examples/inbox/* workspace/inbox/
docker compose up --build
```

Compose starts PostgreSQL, a one-shot migration service, the API, and a worker.

## CLI

```text
openoyster init
openoyster ingest PATH
openoyster ingest-url URL
openoyster ingest-rss feeds.yaml
openoyster ingest-github owner/repo --kind releases
openoyster run [--cycles N | --forever]
openoyster serve
openoyster status
openoyster doctor
openoyster doctor-dev
openoyster feedback ARTIFACT_ID --verdict useful
openoyster hypothesis show HYPOTHESIS_ID --evidence
openoyster artifact show ARTIFACT_ID --provenance
openoyster eval fixtures examples/eval
openoyster premise-review
openoyster export --output FILE
openoyster policy show
openoyster policy list
openoyster policy create examples/policy.sample.yaml
openoyster policy promote POLICY_ID
openoyster db upgrade
```

## Repository Layout

```text
src/openoyster/
  api/          FastAPI application and escaped read-only dashboard
  connectors/   Filesystem, HTTP, RSS, and GitHub ingestion
  loops/        Ten independent event-driven loops
  migrations/   Alembic environment and versioned schema
  services/     Parsing, retrieval, inspection, evaluation, tools, and artifacts

tests/          Unit, API, migration, optimiser, retry, CLI, and E2E tests
docs/           User, contributor, architecture, operations, security, and audit docs
examples/       Demo documents, policy override, mission example, and eval fixtures
```

## Design Principles

1. The event graph is the spine.
2. Knowledge, hypotheses, and actions are different objects.
3. Autonomy is bounded.
4. Optimisation needs labels.
5. The global loop can challenge local loops.
6. Fallbacks are visible.

## Documentation

- `docs/USER_MANUAL.md` - full operator and user guide.
- `docs/USER_MANUAL_KO.md` - Korean operational guide.
- `docs/CONTRIBUTOR_MANUAL.md` - contributor workflow and extension contracts.
- `docs/ARCHITECTURE.md` - event, data, loop, and transaction architecture.
- `docs/OPERATIONS.md` - deployment, backup, migration, monitoring, and incident handling.
- `docs/POLICY_TUNING.md` - hyperparameters, replay/shadow mechanics, and guardrails.
- `docs/API_REFERENCE.md` - authentication and endpoints.
- `docs/CONNECTORS.md` - parser and connector contracts.
- `docs/THREAT_MODEL.md` - trust boundaries and mitigations.
- `docs/AUDIT_REPORT_KO.md` - harsh audit, score, repairs, and residual risks.

## Contributing

Read `CONTRIBUTING.md` and `docs/CONTRIBUTOR_MANUAL.md`. Pull requests must preserve auditability, add tests, pass lint/type checks, document new events and policy keys, and include an approval boundary for every external write capability.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
