# OpenOyster Architecture

## 1. Purpose

OpenOyster is a durable signal-hypothesis-action runtime. It ingests source material, extracts structured claims and signals with an LLM-first pipeline, retrieves supporting or opposing evidence, and records each step in SQL-backed audit state.

The implementation is intentionally ordinary infrastructure: transactions, append-only events by convention, loop leases, explicit retries, provenance, and evaluation records. It is not one giant prompt and it is not a distributed broker.

## 2. Layer model

```text
L6  Evaluation      gold-set and counter-evidence harnesses, human feedback records
L5  Utilisation     grounded decision artifacts from sufficiently supported hypotheses
L4  Action          bounded task planning, registered tools, runs, artifacts
L3  Cognition       hypothesis merge, evidence posture, trigger decisions
L2  Perception      parsing, chunks, LLM extraction, claims, signals
L1  Memory          SQL event stream and auditable intelligence graph
L0  Runtime         scheduler, transactions, leases, configuration, API/CLI
```

## 3. Runtime diagram

```text
 Sources
   │
   ▼
 Intake ──► Maintenance
   │             │
   ▼             │
 Extraction ◄────┘
   │
   ▼
 Hypothesis ──► Planning ──► Execution ──► Utilisation ──► Evaluation
   ▲                                                          │
   └──────────────── durable SQL event stream ───────────────┘
```

Each loop owns a cursor in the event stream. Loops can emit events that wake later loops, but durable database rows are the source of truth.

## 4. Event-stream semantics

`events` is append-only by convention. Each consumer owns an `event_cursors` row. `EventBus.poll` first captures a max-id horizon, then filters wanted event types in SQL inside that horizon:

```python
horizon = select(func.max(Event.id))
select(Event)
    .where(Event.id > last_event_id, Event.id <= horizon, Event.event_type.in_(wanted))
    .order_by(Event.id.asc())
    .limit(limit)
```

The checkpoint rule is:

- If the result count equals `limit`, the checkpoint is the last returned wanted event id.
- If the result count is less than `limit`, the loop has consumed every wanted event visible at the horizon, so the checkpoint advances to that horizon.
- If no wanted event is returned and the horizon is greater than the previous cursor, the checkpoint still advances to that horizon so unrelated event types do not cause repeated scans.

This preserves wanted events inserted by another process after the horizon read and before the cursor commit: their id is greater than the checkpoint and they remain visible to the next poll. PostgreSQL `EventBus.emit` holds a transaction-scoped advisory lock until commit or rollback; the poll horizon/select window uses the same lock, so EventBus writers cannot commit with older sequence ids after the horizon is chosen. Direct writes to `events` bypass that contract. The invariant is covered by event bus tests rather than relying on a code comment.

`scan_multiplier` remains in the `poll` signature for compatibility with existing loop calls, but SQL-side filtering makes it operationally unused. `EventBatch.scanned_count` now means the number of wanted events returned.

Events can be emitted with an `idempotency_key`. A unique constraint plus nested transaction makes concurrent duplicate emission resolve to the existing event.

This design provides durable at-least-once processing characteristics. It does not provide a global distributed exactly-once guarantee. Side effects must remain idempotent.

## 5. Worker leases and transactions

Before a loop runs, the supervisor acquires a row-level database lease in `loop_leases`. One lease exists per loop name. Each loop executes in its own database transaction and writes a `loop_runs` telemetry record in separate supervisor transactions.

Consequences:

- a failure in one loop does not roll back earlier loop transactions;
- a crash before commit leaves the event cursor unchanged, so work is retried;
- a crash after commit but before telemetry completion can leave an incomplete run record, but durable work remains;
- a lease is coordination, not a substitute for idempotency;
- SQLite is suitable for local or small single-host use; PostgreSQL is preferred for multiple processes.

## 6. Core persistent model

Runtime and audit state:

- `events`: immutable state-transition stream by convention.
- `event_cursors`: per-loop checkpoint.
- `loop_leases`: lease owner and expiry.
- `loop_runs`: start/end status, duration, counts, error.

Observation graph:

- `sources`: configurable source identity.
- `source_items`: stable source item, fingerprint, status, last document.
- `documents`: parsed raw text and provenance.
- `chunks`: retryable extraction unit and FTS5 search substrate.
- `entities`: normalised named objects.
- `claims`: extracted atomic claims.
- `signals`: material changes, risks, opportunities, or observations.

Hypothesis and action graph:

- `hypotheses`: testable interpretation with revision and confidence.
- `evidence_edges`: support/opposition linked to source provenance.
- `decision_traces`: trigger features, score, threshold, policy, outcome label.
- `tasks`: bounded planned work with retry and budget state.
- `runs`: actual registered-tool execution record.
- `artifacts`: persisted output with hypothesis/task link.
- `artifact_feedback`: explicit downstream human label.
- `evaluations`: computed or human quality metric.

Policy and scheduler state:

- `policies`: active/candidate/shadow/archived policy versions.
- `experiments`: stored comparison records for policy candidates.
- `mission_charters`: mission, domains, anti-goals, success criteria.
- `scheduler_states`: durable cadence/heartbeat state.

## 7. Loop contracts

### Intake

Scans supported filesystem items, compares source fingerprints, parses changed items, persists a new content-versioned document, updates `source_items`, and emits `doc.fetched`. Parsing errors are recorded without rolling back already successful items.

### Maintenance

Runs time-driven work without requiring a user event. It emits heartbeats, retries eligible failed tasks and chunks, marks stale hypotheses, and archives source files only after the document transaction is durable.

### Extraction

Consumes `doc.fetched` and retry events. It chunks text, invokes the configured provider, persists entities/claims/signals, emits candidate hypotheses, records provider/model/usage/warnings, and marks retryable failures at chunk granularity.

The default provider is codex CLI based. It runs batch extraction, validates schema-shaped output, attempts bounded JSON repair, and records deferred failures when extraction is unavailable.

### Hypothesis

Merges exact or sufficiently similar scoped claims, deduplicates evidence edges, recomputes evidence-derived confidence, records trigger decision traces, emits hypothesis updates, and fires work triggers according to the active policy.

Similarity merge decisions use an LLM judge.

### Planning

Turns one trigger into a bounded set of registered tool tasks. It respects per-trigger and per-cycle limits. Task idempotency includes hypothesis revision and tool type.

### Execution

Claims available tasks, enforces retry/budget state, invokes only registered tools, persists runs/artifacts/evidence candidates, and emits completion/failure events. It never executes arbitrary model-generated code.

### Utilisation

Promotes sufficiently grounded hypotheses into decision-oriented artifacts according to evidence count, source diversity, confidence, and policy thresholds. It is separate from extraction so producing a hypothesis and using a hypothesis remain distinct.

### Evaluation

Scores evidence posture and verified completion, aggregates explicit human feedback, updates artifact status, and writes outcome labels onto matching trigger decision traces. The separate gold-set harness measures extraction and evidence behavior against fixture labels and writes reproducible result artifacts.

## 8. Retrieval and evidence

SQLite deployments use FTS5 over chunks. PostgreSQL deployments use database full-text search. Retrieval records matched terms and source provenance so evidence can be inspected from the CLI or API.

Directional counter-evidence requires opposition rather than merely topical relevance. Accepted opposing evidence must include a verbatim quote. This is tested through the counter-evidence evaluation harness, but the current judge is still an LLM judge and should be treated as quasi-independent.

## 9. Scoring boundaries

Trigger score combines novelty, impact, contradiction, evidence gap, and staleness using active-policy weights. Inputs and final score are clamped. The score is a prioritisation function, not a truth probability.

Hypothesis confidence uses support/opposition strength and priors. It is not statistically calibrated unless a deployment adds calibration data and evaluation.

## 10. Security boundaries

- The dashboard escapes stored content and is read-only.
- Mutation endpoints require a configured API key unless unsafe mode is explicitly enabled.
- HTTP ingestion resolves and blocks non-public addresses before every redirect.
- Execution is limited to a code-defined registry.
- External writes are outside the automatic default path.
- Secrets and raw documents are not intentionally logged, but operators must still configure log handling and database access appropriately.

See `THREAT_MODEL.md`.

## 11. Extension strategy

Stable extension points are providers, connectors, tools, evaluators, and loops. A future broker or vector store can replace implementation details, but must preserve event contracts, provenance, idempotency, and audit records.

## 12. Known architectural limits

- Gold-set labels are currently marked as unreviewed.
- Counter-evidence precision depends on a quasi-independent LLM judge.
- SQLite mode is local or single-host oriented; it is not a cluster coordination layer.
- SQL polling is intentionally simple and will not match a dedicated broker at high throughput.
- Lexical retrieval scans a bounded chunk set and is not large-corpus semantic search.
- One API key is not organisation-grade identity or authorisation.
- The mission charter is stored but has no rich approval workflow UI.
- There is no multi-tenant isolation model.
