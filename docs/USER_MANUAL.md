# OpenOyster User Manual

This manual covers installation, configuration, ingestion, operation, feedback, policy management, deployment, and troubleshooting for OpenOyster `0.3.0`.

## 1. Read this first

OpenOyster is a product-oriented alpha. It persists its reasoning trail and can run unattended inside configured boundaries, but its output remains decision support. The local heuristic extractor is useful for deterministic demos and pipeline testing; it is not equivalent to a domain-tuned semantic model. Do not use generated hypotheses as clinical, legal, financial, or operational truth without review.

OpenOyster separates four types of autonomy:

| Type | Behaviour |
|---|---|
| Exploratory | Generates evidence gaps and investigation tasks from signals. |
| Operational | Executes bounded, registered internal tools. |
| Optimisation | Mutates selected policy parameters using labelled replay and shadow evaluation. |
| Strategic | Produces premise-review recommendations when scope or source drift is detected. |

External writes and mission changes are not performed by the default runtime.

## 2. Requirements

- Python 3.11, 3.12, or 3.13.
- SQLite for local/single-node use; PostgreSQL is recommended for service deployment.
- A writable workspace.
- An API key before exposing mutation endpoints.
- Optional OpenAI-compatible model credentials for remote extraction.

## 3. Local installation

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
cp .env.example .env
```

Replace `OPENOYSTER_API_KEY` in `.env` with a long random value. Initialise the database:

```bash
openoyster init
```

`init` applies Alembic migrations, creates the default policy and mission charter, and prepares the workspace. Migration failure is fatal by default. The `--allow-create-all-fallback` option exists only for disposable local recovery and should not be used for a managed database.

## 4. Configuration

OpenOyster uses environment variables prefixed with `OPENOYSTER_`.

### Core settings

| Variable | Default | Description |
|---|---|---|
| `OPENOYSTER_DB_URL` | `sqlite:///./openoyster.db` | SQLAlchemy database URL. |
| `OPENOYSTER_WORKSPACE` | `./workspace` | Runtime files and archive root. |
| `OPENOYSTER_INBOX_DIR` | `<workspace>/inbox` | Filesystem intake directory. |
| `OPENOYSTER_ARCHIVE_DIR` | `<workspace>/archive` | Optional processed-file archive. |
| `OPENOYSTER_MAX_EVENTS_PER_LOOP` | `100` | Maximum selected events per loop cycle. |
| `OPENOYSTER_EVENT_SCAN_MULTIPLIER` | `20` | How far a filtered consumer scans beyond its limit. |
| `OPENOYSTER_LOOP_LEASE_SECONDS` | `300` | Database lease duration for one loop worker. |
| `OPENOYSTER_SCHEDULER_TICK_SECONDS` | `30` | Default scheduler heartbeat interval. |
| `OPENOYSTER_ARCHIVE_PROCESSED_FILES` | `false` | Move successfully ingested files after commit. |

### API security

| Variable | Default | Description |
|---|---|---|
| `OPENOYSTER_API_KEY` | empty | Secret required by mutation endpoints. |
| `OPENOYSTER_API_KEY_HEADER` | `X-OpenOyster-Key` | Request header carrying the secret. |
| `OPENOYSTER_API_ALLOW_UNSAFE_NO_KEY` | `false` | Opens writes without a key; do not enable on a network. |
| `OPENOYSTER_API_MAX_PAGE_SIZE` | `200` | Maximum list endpoint page size. |

With no API key and unsafe mode disabled, read endpoints work and write endpoints return `503`. With a configured key, a missing or invalid header returns `401`.

### Extraction provider

| Variable | Default | Description |
|---|---|---|
| `OPENOYSTER_LLM_PROVIDER` | `local` | `local` or `openai-compatible`. |
| `OPENOYSTER_LLM_API_KEY` | empty | Remote provider key. |
| `OPENOYSTER_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible base URL. |
| `OPENOYSTER_LLM_MODEL` | `gpt-4.1-mini` | Configured remote model identifier. |
| `OPENOYSTER_LLM_TIMEOUT_SECONDS` | `45` | Remote request timeout. |
| `OPENOYSTER_LLM_MAX_RETRIES` | `2` | Remote retry count. |
| `OPENOYSTER_LLM_FALLBACK_TO_LOCAL` | `true` | Fall back with explicit warning metadata. |

Remote extraction expects a chat-completions-compatible endpoint returning structured JSON. A malformed or failed remote response is recorded in chunk warnings. It is never labelled as a successful remote analysis.

## 5. Ingesting documents

### Copy a file or directory into the inbox

```bash
openoyster ingest ./research
openoyster ingest ./memo.docx
```

Supported formats:

```text
.txt .md .markdown .json .jsonl .csv .tsv .log .yaml .yml .html .htm .pdf .docx
```

The command copies files; the document-intake loop performs parsing and durable ingestion on the next cycle. Content and parser version contribute to the ingest key. Re-scanning an unchanged source item does not create another document.

### Ingest one public URL

```bash
openoyster ingest-url https://example.org/report
```

The connector rejects non-HTTP schemes, embedded credentials, private/loopback/link-local/reserved addresses, unsupported content types, oversized responses, and excessive redirects. It is a single-resource fetcher, not a crawler.

### Ingest RSS and GitHub sources

```bash
openoyster ingest-rss feeds.yaml
openoyster ingest-github owner/repo --kind releases
openoyster ingest-github owner/repo --kind issues
```

RSS config accepts a YAML list or a `feeds:` list. GitHub ingestion is read-only and can use `OPENOYSTER_GITHUB_TOKEN` for API limits. Tokens are not persisted in document metadata.

### Data handling warning

Raw extracted text is stored in the database. Do not ingest sensitive material unless the database, backups, logs, operator access, and retention policy are appropriate for that material.

## 6. Running the system

### Bounded cycles

```bash
openoyster run --cycles 4 --sleep 0
```

A cycle runs each loop in order, but loops communicate through durable events and use independent transactions. Four cycles are normally enough for a fresh small corpus to settle.

### Long-running worker

```bash
openoyster run --forever --sleep 30
```

The worker acquires a database lease for each loop. A second worker can run, but only one owner executes the same loop while the lease is valid. This is coordination, not distributed exactly-once delivery; all writes still need idempotency.

### Inspect status and health

```bash
openoyster status
openoyster doctor
openoyster doctor-dev
```

`status` shows object counts, failed work, active policy, and recent hypotheses. `doctor` verifies workspace write access, database connectivity, policy validity, provider configuration, and API write posture. `doctor-dev` checks whether the local verification toolchain is importable. Both doctor commands exit non-zero when a critical check fails.

## 7. Understanding outputs

### Hypothesis states

- `active`: currently investigated.
- `mature`: support and source-diversity requirements are met.
- `challenged`: opposition materially weakens the hypothesis.
- `stale`: due for review or refresh.

Confidence is evidence-derived, not a calibrated probability of truth. The evidence graph should be inspected before relying on it.

Inspection commands:

```bash
openoyster hypothesis show HYPOTHESIS_ID --evidence
openoyster artifact show ARTIFACT_ID --provenance
openoyster eval fixtures examples/eval
```

Evidence/provenance inspection returns source metadata and bounded chunk excerpts, not full raw document bodies by default.

### Task types

The default planner can create:

- `hypothesis_brief`
- `support_evidence_scan`
- `counter_evidence_scan`
- `baseline_compare`

Execution is limited to tools in the registry. Unknown tool types fail visibly, create a failed run, and enter the maintenance retry path up to the policy limit.

### Artifact types

- `hypothesis_brief`: claim, evidence posture, uncertainty, and next questions.
- `support_evidence_scan`: candidate support from unlinked corpus chunks.
- `oppose_evidence_scan`: candidate counter-evidence.
- `baseline_comparison`: corpus/source distribution context.
- `utilisation_memo`: decision-oriented output for a sufficiently grounded hypothesis.
- `premise_review`: system-level scope and drift audit.

Artifacts are versioned per task or hypothesis context and retain links to their generating task/hypothesis.

## 8. Human feedback

Feedback is the strongest available optimisation label.

```bash
openoyster feedback 12 --verdict useful --score 0.9 --comment "Used in weekly review"
openoyster feedback 13 --verdict rejected --comment "Evidence too narrow"
```

Allowed verdicts are `used`, `useful`, `rejected`, `stale`, and `not_useful`. The evaluation loop aggregates feedback, changes artifact status, and labels matching trigger decision traces. Policy optimisation will not start until the configured minimum labelled traces exists.

API equivalent:

```bash
curl -X POST http://127.0.0.1:8080/v1/artifacts/12/feedback \
  -H 'Content-Type: application/json' \
  -H 'X-OpenOyster-Key: YOUR_KEY' \
  -d '{"verdict":"useful","score":0.9,"comment":"adopted"}'
```

## 9. Policy management

Inspect policies:

```bash
openoyster policy show
openoyster policy list
```

Create a candidate by applying YAML overrides to the active policy:

```bash
openoyster policy create examples/policy.sample.yaml --version conservative-001
```

This validates and stores a `candidate`; it does not silently activate it. To activate immediately after review:

```bash
openoyster policy create examples/policy.sample.yaml \
  --version conservative-002 --activate
```

Or promote a stored candidate:

```bash
openoyster policy promote POLICY_ID
```

Manual promotion is an operator decision. Automatic optimisation only mutates a small allow-list and must clear replay and a later shadow-label window.

## 10. Premise review

Request an immediate global review:

```bash
openoyster premise-review
openoyster run --cycles 2 --sleep 0
```

The review checks, among other things:

- whether one source dominates the document universe;
- whether one signal type dominates extraction;
- whether artifacts are produced but not adopted;
- whether loop failure rates are elevated;
- whether open hypotheses are ageing without resolution;
- whether the mission charter and observed behaviour appear misaligned.

The output proposes action and marks mission/scope changes as requiring human approval. It does not rewrite the charter automatically.

## 11. API and dashboard

```bash
openoyster serve --host 127.0.0.1 --port 8080
```

- `/` — escaped read-only HTML dashboard.
- `/docs` — interactive OpenAPI UI.
- `/health` — process liveness.
- `/ready` — database and active-policy readiness.
- `/v1/hypotheses/{id}/evidence` — hypothesis evidence summary and bounded excerpts.
- `/v1/artifacts/{id}/provenance` — artifact, task, and linked hypothesis provenance.

Use an API gateway/TLS terminator for network deployment. The built-in API key is a minimal single-secret control, not RBAC.

## 12. Docker Compose deployment

```bash
cp .env.example .env
# Replace OPENOYSTER_API_KEY and OPENOYSTER_POSTGRES_PASSWORD.
mkdir -p workspace/inbox
cp examples/inbox/* workspace/inbox/
docker compose up --build
```

Services:

| Service | Function |
|---|---|
| `db` | PostgreSQL 16 with health check and named data volume. |
| `migrate` | One-shot Alembic upgrade. |
| `api` | FastAPI/dashboard, starts after migration succeeds. |
| `worker` | Long-running loop supervisor, starts after migration succeeds. |

The host inbox is mounted read-only. Stop with `docker compose down`; add `-v` only when intentionally deleting database and workspace volumes.

## 13. Export and backup

Portable intelligence export:

```bash
openoyster export --output openoyster-export.json
```

The export contains policy identity, hypotheses, and artifact content. It is not a full backup because it omits raw event history and some provenance.

For SQLite, stop writers and back up the database plus workspace. For PostgreSQL, use `pg_dump` and back up the workspace volume. Validate restoration regularly; a backup that has never been restored is unproven.

## 14. Troubleshooting

### `doctor` reports write API auth failure

Set `OPENOYSTER_API_KEY`. Keeping writes disabled is secure, but `doctor` treats incomplete service configuration as a failed readiness check.

### Documents stay pending

Run at least one worker cycle. Check `status`, `loop_runs`, and chunk errors. Confirm the format is supported and under `OPENOYSTER_MAX_FILE_BYTES`.

### No useful hypotheses appear

The local extractor is conservative and lexical. Try better source material, a domain extractor, or the remote provider. Inspect chunk warnings before assuming a model ran successfully.

### Too many tasks

Raise `trigger.fire_threshold`, lower `planning.max_tasks_per_cycle`, or reduce `planning.exploration_rate`. Do not tune solely for fewer objects; monitor adopted artifacts and missed signals.

### Optimisation never starts

Provide explicit feedback. The optimiser requires labelled trigger traces, and a new shadow policy requires additional labels that were not part of the replay baseline.

### A worker appears stuck

Inspect `loop_leases` and `loop_runs`. Leases expire, but a transaction blocked by the database may still require operational intervention. Do not manually delete audit records without preserving incident evidence.

## 15. Safe usage checklist

- Keep mutation endpoints behind TLS and network access controls.
- Use a strong API key and rotate it operationally.
- Prefer PostgreSQL for multiple services/workers.
- Review premise artifacts and policy promotions.
- Collect explicit downstream feedback.
- Back up raw documents, database, and policy versions.
- Never attach irreversible tools without an approval gate and audit trail.
