# OpenOyster Operations Guide

## 1. Deployment profiles

### Embedded local

- SQLite database.
- One CLI worker and optional local API.
- Suitable for development, personal research, and small corpora.
- Do not share the SQLite file over network filesystems.

### Service deployment

- PostgreSQL.
- Separate migration, API, and worker processes.
- TLS and network access control in front of the API.
- Centralised logs and database backups.
- Recommended for a small team or persistent server.

### Not yet supported as a turnkey profile

- Multi-tenant SaaS.
- Horizontally scaled high-throughput broker-backed deployment.
- Regulated production deployment with formal validation and RBAC.

## 2. Initialisation and migration

Local:

```bash
openoyster init
```

Managed database:

```bash
openoyster db upgrade head
```

Run migration as a one-shot deployment step before starting new application instances. Do not allow every replica to race migrations. The supplied Compose file uses a dedicated `migrate` service.

Before an upgrade:

1. back up the database;
2. review the release changelog and migration;
3. stop or drain workers if the migration is not backwards-compatible;
4. apply migration;
5. run `openoyster doctor`;
6. start API/workers;
7. verify readiness, loop runs, and error rate.

## 3. Process model

API:

```bash
openoyster serve --host 0.0.0.0 --port 8080
```

Worker:

```bash
openoyster run --forever --sleep 30
```

Each supervisor cycle runs loops sequentially in one process, but each loop has its own transaction and lease. Multiple worker processes may coexist; a loop lease prevents simultaneous ownership of the same loop name.

## 4. Health checks

- `GET /health`: process is serving.
- `GET /ready`: database query and active policy succeed.
- `openoyster doctor`: workspace, DB, policy, provider, and write-auth posture.

A healthy process can still produce low-value intelligence. Operational monitoring must include data and outcome metrics, not just HTTP status.

## 5. Recommended metrics

### Runtime

- loop duration and failure count by loop;
- lease acquisition skips;
- event backlog (`max(events.id) - cursor.last_event_id` per loop);
- failed/pending task count and age;
- failed chunk count and attempts;
- API latency/error rate;
- database connection and lock wait time.

### Intelligence quality

- signals per document and source;
- hypothesis support/opposition/source diversity;
- artifact evidence-quality score;
- human feedback rate and value score;
- adopted artifact rate;
- trigger positive rate and labelled utility;
- source/signal concentration;
- stale open hypothesis count;
- policy promotion/rejection history.

## 6. Logging

Set:

```bash
OPENOYSTER_LOG_LEVEL=INFO
OPENOYSTER_LOG_JSON=true
```

The runtime records structured database telemetry even when application logging is minimal. Do not add raw document text, prompts, API keys, or full model responses to logs without a reviewed redaction policy.

## 7. Backup and restore

### SQLite

Stop all writers. Back up:

- the SQLite database file;
- WAL/SHM only if copying a live database with a SQLite-aware backup method;
- workspace and archive;
- `.env` through a secure secret backup process, not the normal repository backup.

Restore into an isolated directory, run `doctor`, and perform an export plus sample query.

### PostgreSQL

Use `pg_dump` or platform-native snapshots. Back up the workspace volume separately. Restoration test:

1. create an isolated database;
2. restore dump;
3. set a temporary `OPENOYSTER_DB_URL`;
4. run `openoyster db upgrade head`;
5. run `openoyster doctor`;
6. compare counts and selected evidence/artifacts;
7. run one no-op cycle and confirm no duplicate explosion.

## 8. Retention and deletion

The default repository does not include a retention engine or privacy deletion workflow. Operators must define retention for:

- raw documents and chunks;
- event/audit history;
- model metadata;
- artifacts and human feedback;
- backups.

Deleting a source document may cascade some child records but can also invalidate hypothesis provenance. Production deletion requires a reviewed policy and audit event.

## 9. Incident response

### Loop failure spike

1. stop the worker if failures are destructive or costly;
2. preserve `loop_runs`, failed tasks/chunks, and related events;
3. identify first failing policy/version/provider;
4. disable the affected connector/tool or optimisation in policy;
5. patch and test against a copy of the incident data;
6. resume with bounded cycles;
7. document root cause and recovery.

### Bad policy promotion

1. stop workers or disable optimisation;
2. inspect `policies`, `experiments`, and labelled decision traces;
3. manually promote the previous validated policy;
4. run bounded cycles and compare behaviour;
5. preserve the rejected policy for audit rather than deleting it.

### Remote model degradation

Inspect chunk metadata for provider warnings and fallback identity. Decide whether to pause intake, disable fallback, switch model endpoint, or accept local degraded mode. Never assume a fluent output proves the configured remote model ran.

### Suspected SSRF or connector abuse

Disable URL ingestion, retain request/audit logs, rotate relevant credentials, inspect DNS/network telemetry, and verify that no private address was reached. The built-in guard reduces risk but is not a substitute for network egress policy.

## 10. Scaling guidance

Use PostgreSQL before adding workers. Increase `max_events_per_loop` carefully and monitor transaction duration. For large corpora, replace bounded SQL lexical scanning with a dedicated retrieval index. For high event throughput, introduce a broker while preserving the SQL audit log or an equivalent durable lineage store.

Do not scale by simply increasing model calls. Quality labels and utilisation should grow before autonomous budget grows.

## 11. Security checklist

- TLS at ingress.
- Strong API key, stored in a secret manager.
- Database credentials with least privilege.
- Network policy limiting model and connector egress.
- No public database port.
- Read-only inbox mount where practical.
- Non-root container.
- Regular dependency and container scanning.
- Tested backup restoration.
- Approval boundary for every external write tool.
