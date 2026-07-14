# OpenOyster User Manual

This manual covers OpenOyster `0.4.0`: an alpha runtime that ingests documents, extracts structured signals with a codex CLI based LLM pipeline, connects evidence with FTS5 retrieval and LLM judges, and records the resulting hypotheses, artifacts, feedback, and evaluation data.

OpenOyster is not a finished autonomous agent or enterprise platform. Generated hypotheses remain decision support. Extraction backend failures leave chunks deferred with recorded reasons instead of silently substituting a lower-quality heuristic analyzer.

## 1. Requirements

- Python 3.11, 3.12, or 3.13.
- SQLite for local/single-host use; PostgreSQL for service deployment.
- A writable workspace.
- An API key before exposing mutation endpoints.
- codex CLI for the default extraction backend, or OpenAI-compatible model credentials for the remote provider.

## 2. Local installation

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

`init` applies Alembic migrations, creates the default policy and mission charter, and prepares the workspace.

## 3. Configuration

OpenOyster uses environment variables prefixed with `OPENOYSTER_`.

### Core settings

| Variable | Default | Description |
|---|---|---|
| `OPENOYSTER_DB_URL` | `sqlite:///./openoyster.db` | SQLAlchemy database URL. |
| `OPENOYSTER_WORKSPACE` | `./workspace` | Runtime files and archive root. |
| `OPENOYSTER_INBOX_DIR` | `<workspace>/inbox` | Filesystem intake directory. |
| `OPENOYSTER_ARCHIVE_DIR` | `<workspace>/archive` | Optional processed-file archive. |
| `OPENOYSTER_MAX_EVENTS_PER_LOOP` | `100` | Maximum selected events per loop cycle. |
| `OPENOYSTER_EVENT_SCAN_MULTIPLIER` | `20` | Deprecated compatibility setting; event polling now filters wanted event types in SQL. |
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
| `OPENOYSTER_LLM_PROVIDER` | `codex` | `codex`, `openai-compatible`, or `stub`. Use `stub` only for tests and pipeline smoke checks. |
| `OPENOYSTER_LLM_API_KEY` | empty | Remote provider key. |
| `OPENOYSTER_LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible base URL. |
| `OPENOYSTER_LLM_MODEL` | `gpt-4.1-mini` | Configured remote model identifier. |
| `OPENOYSTER_LLM_TIMEOUT_SECONDS` | `45` | Remote request timeout. |
| `OPENOYSTER_LLM_MAX_RETRIES` | `2` | Remote retry count. |
| `OPENOYSTER_CODEX_BINARY` | `codex` | codex CLI executable used by the default provider. |
| `OPENOYSTER_CODEX_BATCH_SIZE` | `5` | Extraction chunks per codex batch. |
| `OPENOYSTER_CODEX_TIMEOUT_SECONDS` | `300` | codex CLI subprocess timeout. |
| `OPENOYSTER_CODEX_CONFIG_DIR` | `.codex-llm` | Model and pipeline catalog directory. |

Remote extraction expects a chat-completions-compatible endpoint returning structured JSON. A malformed or failed model response leaves the affected chunk deferred with a reason.

## 4. Ingesting documents

```bash
openoyster ingest ./research
openoyster ingest ./memo.docx
openoyster ingest-url https://example.org/report
openoyster ingest-rss examples/feeds.yaml
openoyster ingest-github owner/repo --kind releases
openoyster ingest-github owner/repo --kind issues
```

Supported formats:

```text
.txt .md .markdown .json .jsonl .csv .tsv .log .yaml .yml .html .htm .pdf .docx
```

URL ingestion rejects non-HTTP schemes, embedded credentials, private/loopback/link-local/reserved addresses, unsupported content types, oversized responses, and excessive redirects. RSS and GitHub ingestion are read-only. GitHub can use `OPENOYSTER_GITHUB_TOKEN` for API limits, and tokens are not persisted in document metadata.

Raw extracted text is stored in the database. Do not ingest sensitive material unless the database, backups, logs, operator access, and retention policy are appropriate for it.

## Trusted OpenCrab Pack directories

MVP-P1 accepts only trusted local OpenCrab Pack directories. Validation and installation never modify the source directory; installation copies validated bytes into the workspace and queries use active Pack evidence only.

```bash
openoyster pack validate /trusted/packs/example
openoyster pack validate /trusted/packs/example --profile strict
openoyster pack install /trusted/packs/example
openoyster pack list
openoyster pack show PACK_ID
openoyster pack query "What does this Pack support?" --packs PACK_ID
```

The commands print JSON suitable for automation. `compatible` requires the four validator files; `strict` additionally requires the documented eleven-file layout. A supported answer always cites global evidence ids; missing retrieval evidence, an ambiguous/local citation, or an invented citation returns `unknown`.

The API mirrors the surface at `POST /v1/packs/validate`, `POST /v1/packs/install`, `GET /v1/packs`, `GET /v1/packs/{pack_id}`, and `POST /v1/packs/query`. The normal write API key is required for validation, installation, and query because they inspect a server-local path, change state, or may invoke the configured LLM. API errors omit local paths and Pack content.

This is not an archive or remote-ingestion interface. ZIP extraction/quarantine, automatic update/diff/rollback, and OCR/CLIP/audio/video analysis are deferred.

## 5. Running the system

Bounded cycles:

```bash
openoyster run --cycles 4 --sleep 0
```

Long-running worker:

```bash
openoyster run --forever --sleep 30
```

Local development launcher:

```bash
./run.sh start
./run.sh stop
```

`run.sh` starts the API on `0.0.0.0:3377` and a worker using `.venv/bin/openoyster`, with logs under `workspace/logs/`. It is a local development helper, not a production service manager.

Read endpoints and the dashboard are not protected by the API key. Do not use the `0.0.0.0` launcher on an untrusted network.

Inspect status and health:

```bash
openoyster status
openoyster doctor
openoyster doctor-dev
```

`doctor` verifies workspace write access, database connectivity, policy validity, provider configuration, and API write posture. `doctor-dev` checks whether the local verification toolchain is importable.

## 6. Inspecting outputs

```bash
openoyster hypothesis show HYPOTHESIS_ID --evidence
openoyster artifact show ARTIFACT_ID --provenance
```

Evidence/provenance inspection returns source metadata and bounded chunk excerpts, not full raw document bodies by default.

Default internal tool artifact types include:

- `hypothesis_brief`
- `support_evidence_scan`
- `oppose_evidence_scan`
- `baseline_comparison`
- `utilisation_memo`

Execution is limited to tools in the registry. Unknown tool types fail visibly, create a failed run, and enter the maintenance retry path up to the policy limit.

## 7. Human feedback

```bash
openoyster feedback 12 --verdict useful --score 0.9 --comment "Used in weekly review"
openoyster feedback 13 --verdict rejected --comment "Evidence too narrow"
```

Allowed verdicts are `used`, `useful`, `rejected`, `stale`, and `not_useful`. Feedback updates artifact evaluation state and labels matching trigger decision traces when available.

API equivalent:

```bash
curl -X POST http://127.0.0.1:8080/v1/artifacts/12/feedback \
  -H 'Content-Type: application/json' \
  -H 'X-OpenOyster-Key: YOUR_KEY' \
  -d '{"verdict":"useful","score":0.9,"comment":"adopted"}'
```

## 8. Policy management

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

Manual promotion is an operator decision. Keep overrides small and retain rejected policies for audit.

## 9. Evaluation

```bash
openoyster eval gold --limit 5
openoyster eval counter --cycles 1
openoyster gold review
```

The gold-set harness measures core entity recall, signal type F1, and quote existence. The counter-evidence harness checks directional opposition quality. Current gold labels are still marked unreviewed. The judge, verifier, and auditor all use `gpt-5.6-sol`, separated only by role prompts and reasoning effort, so counter precision is a self-consistency measure rather than independent confirmation.

## 10. API and dashboard

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

## 11. Docker Compose deployment

```bash
cp .env.example .env
# Replace OPENOYSTER_API_KEY and OPENOYSTER_POSTGRES_PASSWORD.
mkdir -p workspace/inbox
cp examples/inbox/* workspace/inbox/
docker compose up --build
```

Compose runs PostgreSQL, a one-shot migration service, the API, and a worker. The host inbox is mounted read-only.

## 12. Export and backup

```bash
openoyster export --output openoyster-export.json
```

The export contains policy identity, hypotheses, and artifact content. It is not a full backup because it omits raw event history and some provenance.

For SQLite, stop writers and back up the database plus workspace. For PostgreSQL, use `pg_dump` and back up the workspace volume. Validate restoration regularly.

## 13. Troubleshooting

### `doctor` reports write API auth failure

Set `OPENOYSTER_API_KEY`. Keeping writes disabled is secure, but `doctor` treats incomplete service configuration as a failed readiness check.

### Documents stay pending

Run at least one worker cycle. Check `status`, `loop_runs`, and chunk errors. Confirm the format is supported and under `OPENOYSTER_MAX_FILE_BYTES`.

### No useful hypotheses appear

Inspect chunk `last_error`, deferred events, and provider metadata. A backend outage leaves chunks deferred; fix the codex CLI or OpenAI-compatible endpoint, then let maintenance requeue deferred chunks after the cooldown.

### Too many tasks

Raise `trigger.fire_threshold`, lower `planning.max_tasks_per_cycle`, or reduce `planning.exploration_rate`. Do not tune solely for fewer objects; monitor adopted artifacts and missed signals.

### A worker appears stuck

Inspect `loop_leases` and `loop_runs`. Leases expire, but a transaction blocked by the database may still require operational intervention. Do not manually delete audit records without preserving incident evidence.

## 14. Safe usage checklist

- Keep mutation endpoints behind TLS and network access controls.
- Use a strong API key and rotate it operationally.
- Prefer PostgreSQL for multiple services/workers.
- Review policy promotions and evidence quality.
- Collect explicit downstream feedback.
- Back up raw documents, database, and policy versions.
- Never attach irreversible tools without an approval gate and audit trail.

## 15. Autonomous Deliberation D1

Autonomous Deliberation turns one Mission plus installed OpenCrab Packs into a stored Decision Dossier. Packs provide the only factual evidence. Mission fields are control input and are never upgraded to evidence. A run produces beliefs, options, expected/adverse scenarios, an independent critic result, a selection or abstention, flip conditions, inert Knowledge Requests, Cognitive Impact, and a deterministic replay record.

Use the fixture Mission and Pack for a local stub-provider walkthrough:

```bash
openoyster pack install tests/fixtures/opencrab_pack_runtime/p0-f1-minimal

openoyster deliberate run tests/fixtures/deliberation_d1/mission_happy.json \
  --packs p0-f1-minimal \
  --impact-baseline-packs p0-f1-minimal \
  --allow-compatible-packs \
  --idempotency-key manual-d1-001
```

The command prints JSON containing the run `id`. Use that ID for inspection:

```bash
openoyster deliberate show RUN_ID
openoyster deliberate dossier RUN_ID --format json
openoyster deliberate dossier RUN_ID --format markdown
openoyster deliberate impact RUN_ID
openoyster deliberate knowledge-requests RUN_ID
openoyster deliberate replay RUN_ID
```

The normal path makes exactly five bounded stage calls. With no retrieved Pack evidence, it completes an abstention without a model call. Reusing an idempotency key returns the persisted run instead of creating another execution. `replay` never calls the LLM; it validates stored artifacts and compares reconstructed dossier digests.

CLI exit codes are `0` for completed selection or abstention, `1` for database/indeterminate/unrecoverable execution failure, and `2` for Mission, Pack-scope/profile, or argument errors. Output is sanitized: it does not include raw Pack records, full prompts, server paths, storage URIs, runtime configuration, or secrets.

For API usage, see the Autonomous Deliberation D1 section in `docs/API_REFERENCE.md`. Every D1 API endpoint, including reads, needs a configured API key. Knowledge Requests are records for a human or external workflow to act on; OpenOyster does not execute them or update Packs.

## 16. Decision Continuity D2

D2 continues a completed abstaining parent run after its persisted Knowledge Requests have been fulfilled. The user or OpenCrab installs the new Pack; OpenOyster does not discover external facts or update Packs. The continuation request explicitly names the installed Pack IDs and the fulfilled parent Knowledge Request `local_key` values.

```bash
openoyster deliberate continue PARENT_RUN_ID \
  --packs new-pack-id \
  --fulfills kr_no_evidence \
  --idempotency-key manual-d2-001

openoyster deliberate transition CHILD_RUN_ID
```

The child reuses the frozen parent Mission snapshot and records `parent_run_id`. Its immutable `cognitive_transition_v2` artifact separates claimed, verified fulfilled, and unverified claimed requests. `evidence:no_evidence` is verified only when the child cites newly added evidence. Critic gap findings are promoted to new Knowledge Requests.

Concrete flow: the first run abstains because evidence for “field recovery time” is missing and records `kr_no_evidence`. OpenCrab supplies a new Pack, and the user continues with `--fulfills kr_no_evidence`. The transition then makes the exact changes in beliefs, viable options, critic verdict, decision (`abstain` to `select`, when the gates allow it), and cited global evidence visible. Unfulfilled requests remain listed.

The parent is immutable. Reusing an idempotency key for the same parent returns the existing child state without another execution; using it for a different parent returns `idempotency_key_conflict`. Continuation input errors are returned as CLI exit `2` and API `422`: `parent_run_not_found`, `parent_run_not_completed_abstain`, `parent_knowledge_requests_missing`, `fulfilled_knowledge_request_keys_empty`, and `fulfilled_knowledge_request_keys_unknown` are also stable codes. Provider/runtime failures are `failed_execution`, not epistemic abstention: CLI exit `1`, API `502`. A failed child still consumes its idempotency key, so use a new key to retry execution after restoring the provider/runtime. See the D2 section in `docs/API_REFERENCE.md` for the complete request and response contract.
