# OpenOyster Architecture

## 1. Purpose

OpenOyster implements a durable signal–hypothesis–action system as a set of independent loops. The architecture rejects two brittle designs: one giant “agent prompt” and one externally defined task tree. Work emerges from persisted observations and state transitions.

## 2. Layer model

```text
L8  Meta-governance     mission alignment, scope drift, approval boundaries
L7  Optimisation        labelled replay, shadow policy, promotion/rollback
L6  Evaluation          rule metrics, explicit feedback, decision outcomes
L5  Utilisation         decision memos and review artifacts
L4  Action              task planning, registered tools, runs, artifacts
L3  Cognition           hypothesis merge, evidence posture, trigger decisions
L2  Perception          parsing, chunks, claims, signals, candidate hypotheses
L1  Memory              SQL event log and auditable intelligence graph
L0  Runtime             scheduler, transactions, leases, configuration, API/CLI
```

## 3. Runtime diagram

```text
                 ┌─────────────────────────────────────┐
                 │ Meta-Premise Review                 │
                 │ source/signal concentration, drift  │
                 └──────────────────▲──────────────────┘
                                    │
                         premise events / policy events
                                    │
 ┌──────────────────┐      ┌────────┴────────┐      ┌──────────────────┐
 │ Optimisation     │◀─────│ Evaluation      │◀─────│ Utilisation      │
 │ replay + shadow  │      │ rules + humans  │      │ grounded outputs │
 └────────▲─────────┘      └────────▲────────┘      └────────▲─────────┘
          │                         │                         │
          └─────────────────────────┼─────────────────────────┘
                                    │
                 ┌──────────────────┴──────────────────┐
                 │ Durable SQL event stream           │
                 │ checkpoints, idempotency, leases   │
                 └──────────────────▲──────────────────┘
                                    │
 ┌──────────────────┐  ┌────────────┴───────┐  ┌─────────────────────┐
 │ Intake/Maintain  │→ │ Extract/Hypothesise│→ │ Plan/Execute         │
 │ discover/retry   │  │ evidence + triggers│  │ bounded tool actions │
 └──────────────────┘  └────────────────────┘  └─────────────────────┘
```

## 4. Event-stream semantics

`events` is append-only by convention. Each consumer owns an `event_cursors` row. `EventBus.poll` scans forward from that cursor, selects wanted event types, and returns a safe checkpoint that stops before unselected wanted work would be dropped. `ack` advances only after loop work has been persisted in the supervisor transaction.

Events can be emitted with an `idempotency_key`. A unique constraint plus nested transaction makes concurrent duplicate emission resolve to the existing event.

This design provides durable at-least-once processing characteristics. It does **not** provide a global distributed exactly-once guarantee. Side effects must remain idempotent.

## 5. Worker leases and transactions

Before a loop runs, the supervisor acquires a row-level database lease in `loop_leases`. One lease exists per loop name. Each loop executes in its own database transaction and writes a `loop_runs` telemetry record in separate supervisor transactions.

Consequences:

- a failure in one loop does not roll back earlier loop transactions;
- a crash before commit leaves the event cursor unchanged, so work is retried;
- a crash after commit but before telemetry completion can leave an incomplete run record, but durable work remains;
- a lease is coordination, not a substitute for idempotency;
- SQLite is suitable for local/small single-host use; PostgreSQL is preferred for multiple processes.

## 6. Core persistent model

### Runtime/audit graph

| Table | Role |
|---|---|
| `events` | Immutable state-transition stream. |
| `event_cursors` | Per-loop checkpoint. |
| `loop_leases` | Lease owner and expiry. |
| `loop_runs` | Start/end status, duration, counts, error. |

### Observation graph

| Table | Role |
|---|---|
| `sources` | Configurable source identity. |
| `source_items` | Stable source item, fingerprint, status, last document. |
| `documents` | Parsed raw text and provenance. |
| `chunks` | Retryable extraction unit. |
| `entities` | Normalised named objects. |
| `claims` | Extracted atomic claims. |
| `signals` | Material changes, risks, opportunities, or observations. |

### Hypothesis/action graph

| Table | Role |
|---|---|
| `hypotheses` | Testable interpretation with revision and confidence. |
| `evidence_edges` | Support/opposition linked to source provenance. |
| `decision_traces` | Trigger features, score, threshold, policy, outcome label. |
| `tasks` | Bounded planned work with retry and budget state. |
| `runs` | Actual tool execution record. |
| `artifacts` | Persisted output with hypothesis/task link. |
| `artifact_feedback` | Explicit downstream human label. |
| `evaluations` | Rule or human quality metric. |

### Governance graph

| Table | Role |
|---|---|
| `policies` | Active/candidate/shadow/archived policy versions. |
| `experiments` | Replay and shadow evidence for a candidate. |
| `mission_charters` | Mission, domains, anti-goals, success criteria. |
| `scheduler_states` | Durable cadence/heartbeat state. |

## 7. Loop contracts

### Document intake

Scans supported filesystem items, compares source fingerprints, parses changed items, persists a new content-versioned document, updates `source_items`, and emits `doc.fetched`. Parsing errors are recorded without rolling back already successful items.

### Maintenance

Runs time-driven work without requiring a user event. It emits heartbeats and scheduled review events, retries eligible failed tasks/chunks, marks stale hypotheses, and archives source files only after the document transaction is already durable.

### Extraction

Consumes `doc.fetched` and retry events. It chunks text, invokes the configured provider, persists entities/claims/signals, emits candidate hypotheses, records provider/model/usage/warnings, and marks retryable failures at chunk granularity.

### Hypothesis

Merges exact or sufficiently similar scoped claims, deduplicates evidence edges, recomputes evidence-derived confidence, records trigger decision traces, emits hypothesis updates, and fires work triggers according to the active policy.

### Planning

Turns one trigger into a bounded set of registered tool tasks. It respects per-trigger and per-cycle limits and exploration policy. Task idempotency includes hypothesis revision and tool type.

### Execution

Claims available tasks, enforces retry/budget state, invokes only registered tools, persists runs/artifacts/evidence candidates, and emits completion/failure events. It never executes arbitrary model-generated code.

### Utilisation

Promotes sufficiently grounded hypotheses into decision-oriented artifacts according to evidence count, source diversity, confidence, and policy thresholds. It is separate from extraction so “producing a hypothesis” and “using a hypothesis” remain distinct.

### Evaluation

Scores evidence posture and verified completion, aggregates explicit human feedback, updates artifact status, and writes outcome labels onto matching trigger decision traces.

### Optimisation

Uses labelled traces from a configured window. It searches a bounded mutation set, rejects candidates that do not improve replay utility, stores the winner as shadow, waits for new labels not used in replay, compares base versus candidate on that shadow window, and only then promotes or archives.

### Meta-premise review

Profiles the system’s own behaviour: source and signal concentration, adoption, stale hypotheses, failure rate, and mission fit. It writes a premise-review artifact and emits an approval-required proposal when drift exceeds policy threshold.

## 8. Scoring boundaries

Trigger score combines novelty, impact, contradiction, evidence gap, and staleness using active-policy weights. Inputs and final score are clamped. The score is a prioritisation function, not truth probability.

Hypothesis confidence uses support/opposition strength and priors. It is also not statistically calibrated unless a deployment adds calibration data and evaluation.

## 9. Security boundaries

- The dashboard escapes stored content and is read-only.
- Mutation endpoints require a configured API key unless unsafe mode is explicitly enabled.
- HTTP ingestion resolves and blocks non-public addresses before every redirect.
- Execution is limited to a code-defined registry.
- Mission changes and external writes are outside the automatic default path.
- Secrets and raw documents are not intentionally logged, but operators must still configure log handling and database access appropriately.

See `THREAT_MODEL.md`.

## 10. Extension strategy

Stable extension points are providers, connectors, tools, evaluators, and loops. A future broker or vector store can replace implementation details, but must preserve event contracts, provenance, idempotency, and audit records.

## 11. Known architectural limits

- SQL polling is intentionally simple and will not match a dedicated broker at high throughput.
- Lexical retrieval scans a bounded chunk set and is not large-corpus semantic search.
- One API key is not organisation-grade identity or authorisation.
- Current policy search is constrained coordinate mutation, not Bayesian optimisation.
- The mission charter is stored but has no rich approval workflow UI.
- There is no multi-tenant isolation model.
