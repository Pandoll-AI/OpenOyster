# OpenOyster

[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/Pandoll-AI/OpenOyster)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11--3.13-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg?logo=fastapi&logoColor=white)](docs/API_REFERENCE.md)
[![Database](https://img.shields.io/badge/database-SQLite%20%7C%20PostgreSQL-336791.svg)](docs/OPERATIONS.md)
[![Open Source](https://img.shields.io/badge/open%20source-Apache--2.0-blueviolet.svg)](CONTRIBUTING.md)
[![Language](https://img.shields.io/badge/language-English%20%7C%20Korean-lightgrey.svg)](README.md)

> This is the English README. The Korean-first README is available in [README.md](README.md).

> Start here: [OpenOyster Goal-Oriented Roadmap](docs/GOAL_ROADMAP.md) — the project’s end goal, user value, and staged product direction.

<p align="center">
  <img src="assets/hero.png" alt="OpenOyster intelligence runtime hero image showing signals, hypotheses, events, and artifacts connected through an evidence graph" width="100%">
</p>

**OpenOyster is an open-source intelligence runtime that reads documents, detects meaningful signals, builds falsifiable hypotheses, and turns evidence into traceable decision artifacts.**

Give it files, URLs, RSS feeds, or GitHub issues/releases. OpenOyster will:

1. ingest documents safely and split them into chunks;
2. extract claims, signals, risks, and opportunities;
3. create falsifiable hypotheses and connect support/counter-evidence;
4. run internal tools for briefs, counter-evidence scans, and decision memos;
5. use human feedback for policy replay and shadow evaluation;
6. periodically review whether the system is looking at the wrong scope.

```text
Sources -> Documents -> Claims / Signals -> Hypotheses -> Triggers -> Tasks -> Artifacts
   ^                                                                     |
   |----- Meta-premise review <- Policy replay/shadow tuning <- Evaluation
```

OpenOyster is not a magic general-purpose autonomous agent. It is an alpha implementation of an **auditable intelligence OS**: evidence graph first, event log first, policy record first.

## Why It Exists

Many AI agent demos can produce fluent answers. They struggle with harder operational questions:

- Which source document supports this judgment?
- Did the system look for counter-evidence?
- Why did this task run?
- Was a model failure or deferred extraction reason recorded?
- How does the system learn from good and bad outcomes?
- Is it slowly drifting toward the wrong source universe?

OpenOyster treats those questions as product requirements. It prioritizes **evidence, traceability, replayability, and operational boundaries** over agent theater.

## Good Fits

- Continuously monitoring research notes, policy material, release notes, and market signals.
- Producing evidence-linked hypotheses instead of plain summaries.
- Building autonomous agent systems with durable DB state, events, retries, idempotency, and policy tuning from day one.
- Studying a reference architecture before turning LLM output into an operational system.
- Experimenting with Korean/English intelligence workflows in one open-source runtime.

## 5-Minute Demo

Python 3.11-3.13 is supported.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

cp .env.example .env
# Set OPENOYSTER_API_KEY before exposing the API.

openoyster init
openoyster ingest examples/inbox
openoyster run --cycles 4 --sleep 0
openoyster status
openoyster serve --host 127.0.0.1 --port 8080
```

Dashboard: `http://127.0.0.1:8080`

OpenAPI: `http://127.0.0.1:8080/docs`

Inspect evidence directly:

```bash
openoyster hypothesis show HYPOTHESIS_ID --evidence
openoyster artifact show ARTIFACT_ID --provenance
openoyster eval fixtures examples/eval
```

## What Is Included

- **Durable runtime**: SQL event stream, checkpoints, idempotency keys, DB-backed worker leases.
- **Ten loops**: intake, maintenance, extraction, hypothesis, planning, execution, utilisation, evaluation, optimisation, meta-premise review.
- **Traceable knowledge graph**: documents, chunks, claims, signals, hypotheses, evidence edges, tasks, runs, artifacts, feedback, policies, experiments, mission charters.
- **Ingestion paths**: filesystem, guarded HTTP, RSS, GitHub releases/issues.
- **LLM providers**: codex CLI batch extractor, OpenAI-compatible structured JSON provider, test-only stub provider, deferred-on-failure recording.
- **Evidence tools**: hypothesis brief, support scan, counter-evidence scan, corpus baseline.
- **Retrieval quality controls**: SQLite FTS5/PostgreSQL full-text auto retrieval, matched terms, source-diversity cap, stance-judge evidence filtering.
- **Operational surfaces**: FastAPI, read-only dashboard, API-key protected writes, Typer CLI, Alembic migrations, Docker Compose.
- **Verification surfaces**: pytest, ruff, mypy, CI, fixture evaluation, release checklist, threat model.

## Ingestion Examples

```bash
openoyster ingest ./research-notes
openoyster ingest-url https://example.org/report
openoyster ingest-rss feeds.yaml
openoyster ingest-github owner/repo --kind releases
openoyster ingest-github owner/repo --kind issues
```

A GitHub token is optional. If needed, inject it through `OPENOYSTER_GITHUB_TOKEN`; it is not stored in document metadata or event payloads.

## CLI Map

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

## Docker Compose

```bash
cp .env.example .env
# Set OPENOYSTER_API_KEY and OPENOYSTER_POSTGRES_PASSWORD.
mkdir -p workspace/inbox
cp examples/inbox/* workspace/inbox/
docker compose up --build
```

Compose runs PostgreSQL, a one-shot migration service, the API, and a worker.

## Repository Layout

```text
src/openoyster/
  api/          FastAPI application and escaped read-only dashboard
  connectors/   Filesystem, HTTP, RSS, GitHub ingestion
  loops/        Ten independent event-driven loops
  migrations/   Alembic environment and versioned schema
  services/     Parsing, retrieval, inspection, evaluation, tools, artifacts

tests/          Unit, API, migration, optimiser, retry, CLI, E2E tests
docs/           User, contributor, architecture, operations, security, audit docs
examples/       Demo documents, policy override, mission example, eval fixtures
```

## Status And Limits

Current release: `0.3.0` alpha. It is suitable as an open-source reference implementation, not as an unreviewed high-stakes decision system.

Not included yet:

- default vector index;
- RBAC or multi-tenancy;
- secret-manager integration;
- browser-scale crawler;
- external write-action SDK;
- distributed broker;
- load, chaos, or security certification.

## Design Principles

1. The event graph is the spine.
2. Knowledge, hypotheses, and actions are different objects.
3. Autonomy must be bounded.
4. Optimisation needs real labels.
5. The global loop can challenge local assumptions.
6. Unavailable extraction backends defer with a reason instead of silently degrading to a lower-quality analyzer.

## Documentation

- [Korean README](README.md)
- [Goal-Oriented Roadmap](docs/GOAL_ROADMAP.md)
- [Korean User Manual](docs/USER_MANUAL_KO.md)
- [User Manual](docs/USER_MANUAL.md)
- [Contributor Manual](docs/CONTRIBUTOR_MANUAL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Operations](docs/OPERATIONS.md)
- [Policy Tuning](docs/POLICY_TUNING.md)
- [API Reference](docs/API_REFERENCE.md)
- [Connectors](docs/CONNECTORS.md)
- [Threat Model](docs/THREAT_MODEL.md)

## Open Source Tags

`open-source` `python` `fastapi` `sqlalchemy` `alembic` `postgresql` `sqlite` `llm` `agents` `event-driven` `retrieval` `rss` `github-api` `korean` `english`

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/CONTRIBUTOR_MANUAL.md](docs/CONTRIBUTOR_MANUAL.md) before opening a PR.

Pull requests should:

- preserve auditable event/model flow;
- add relevant tests;
- pass lint, typecheck, and tests;
- document new events, policy keys, commands, endpoints, or connectors;
- include an explicit approval boundary for any external write capability.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
