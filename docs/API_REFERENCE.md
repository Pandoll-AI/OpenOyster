# OpenOyster API Reference

Run the service:

```bash
openoyster serve --host 127.0.0.1 --port 8080
```

Interactive OpenAPI documentation is available at `/docs` and the schema at `/openapi.json`.

## 1. Authentication

Read endpoints do not require authentication. Mutation endpoints require the header configured by `OPENOYSTER_API_KEY_HEADER`, defaulting to `X-OpenOyster-Key`.

```bash
-H 'X-OpenOyster-Key: YOUR_KEY'
```

Behaviour:

- API key configured, missing/wrong value: `401`.
- No API key and unsafe mode disabled: `503`; write API is intentionally unavailable.
- No API key and `OPENOYSTER_API_ALLOW_UNSAFE_NO_KEY=true`: writes are open. This is development-only.

The built-in mechanism is one shared secret. Put the API behind TLS and an identity-aware gateway for real deployments.

## 2. Health

### `GET /health`

Liveness response:

```json
{"status":"ok","version":"0.4.0"}
```

### `GET /ready`

Checks a database query and active policy. Returns `200` with:

```json
{"status":"ready"}
```

## 3. Dashboard

### `GET /`

Returns a read-only HTML dashboard with object counts and recent hypotheses/artifacts. Stored titles and claims are HTML-escaped.

## 4. Status and execution

### `GET /v1/status`

Returns durable object counts and the active policy version.

### `POST /v1/run-cycle`

Authentication required. Runs one supervisor cycle and returns serialised loop results. This endpoint can be expensive and should not be exposed to untrusted callers.

## 5. URL ingestion

### `POST /v1/ingest-url`

Authentication required.

Request:

```json
{"url":"https://example.org/report"}
```

The connector validates public addressing, redirects, response size, and content type. Existing identical ingest keys return the existing document.

## Trusted OpenCrab Pack runtime

The MVP-P1 Pack surface accepts only a trusted local directory already visible to the service process. It never accepts ZIP uploads, archive extraction, remote URLs, or raw asset bodies.

### `POST /v1/packs/validate`

Authentication required. Validates a directory without modifying it. Authentication prevents unauthorised server-local path inspection and validation work.

```json
{"path":"/trusted/packs/example","profile":"compatible"}
```

`profile` is `compatible` (four validator files) or `strict` (the documented eleven-file layout). Responses include admission status, Pack identity, digest, and sanitized issue codes; they do not echo local paths or Pack content.

### `POST /v1/packs/install`

Authentication required. Uses the same request shape, validates first, copies immutable bytes into the Pack workspace, and registers the active revision. Non-directory input returns `422`; a same Pack/version with a different digest returns `409`. The response omits source and storage paths.

### `GET /v1/packs` and `GET /v1/packs/{pack_id}`

Return active Pack registry metadata only: identity, declared version, digest, profile, status, and record counts. Raw assets and local paths are not returned.

### `POST /v1/packs/query`

Authentication required because this operation may invoke the configured LLM. It accepts `{"question":"...","packs":["optional-pack-id"],"top_k":20}` and returns `supported` only when every citation is a retrieved global evidence id. No evidence or an unverified citation returns `unknown`.

Deferred Pack capabilities: ZIP/quarantine admission, automatic updates/diffs/rollback, and OCR/CLIP/audio/video analysis.

## 6. Events

### `GET /v1/events?limit=50&offset=0`

Newest first. `limit` is clamped to `OPENOYSTER_API_MAX_PAGE_SIZE`.

## 7. Documents

### `GET /v1/documents?limit=50&offset=0`

Returns document provenance, parser/version state, status, failure count, timestamps, and metadata. Raw text is included by the current response model; treat this endpoint as sensitive.

## 8. Hypotheses

### `GET /v1/hypotheses?limit=50&offset=0`

Returns hypotheses ordered by most recent update.

### `GET /v1/hypotheses/{hypothesis_id}`

Returns one hypothesis or `404`.

### `GET /v1/hypotheses/{hypothesis_id}/evidence`

Returns evidence summary, evidence edges, source metadata, and bounded chunk excerpts for one hypothesis. It does not include full raw document bodies.

## 9. Tasks

### `GET /v1/tasks?limit=50&offset=0`

Returns planned task state, priority, retry information, budget, and links.

## 10. Artifacts and feedback

### `GET /v1/artifacts?limit=50&offset=0`

Returns newest artifacts.

### `GET /v1/artifacts/{artifact_id}`

Returns one artifact or `404`.

### `GET /v1/artifacts/{artifact_id}/provenance`

Returns artifact metadata, linked task metadata, and linked hypothesis evidence when available. It is intended for inspection and downstream tooling without exposing full source documents by default.

### `POST /v1/artifacts/{artifact_id}/feedback`

Authentication required.

```json
{
  "verdict": "useful",
  "score": 0.9,
  "comment": "Used in the weekly strategy review",
  "source": "human-api"
}
```

`score` is optional and must be in `[0,1]`. The event is evaluated on a later worker cycle.

## 11. Policies

### `GET /v1/policies`

Returns active, candidate, shadow, and archived policies.

### `POST /v1/policies/{policy_id}/promote`

Authentication required. Validates and manually promotes the selected policy, archives the previous active policy, and emits `policy.promoted`.

## 12. Loop telemetry

### `GET /v1/loop-runs?limit=50&offset=0`

Returns loop owner, status, timing, consumed/emitted counts, created-record counts, notes, and errors.

## 13. Example calls

```bash
curl http://127.0.0.1:8080/v1/status

curl -X POST http://127.0.0.1:8080/v1/run-cycle \
  -H 'X-OpenOyster-Key: YOUR_KEY'
```

## 14. API limitations

- No RBAC or user identity.
- No cursor-token pagination; list endpoints use offset/limit.
- No rate limiter in the application.
- No CORS policy configuration because the default dashboard is same-origin and read-only.
- Raw document responses may expose sensitive text.
- Evidence/provenance endpoints expose bounded excerpts and source metadata, not full raw document bodies.
- No stable compatibility guarantee before `1.0`.
- Pack endpoints are trusted-directory MVP surfaces, not a remote upload API.

## 15. Autonomous Deliberation D1

Autonomous Deliberation uses already-installed OpenCrab Packs as its only factual input. A Mission supplies control input, not evidence. The service freezes exact Pack install IDs before the first model call, persists a Decision Dossier, and can replay the audit deterministically without calling the model again.

Every D1 endpoint requires a configured API key, including reads. Unlike legacy write routes, D1 remains unavailable (`503`) when `OPENOYSTER_API_KEY` is absent, even if unsafe legacy-write mode is enabled. Send the configured key header on every request.

### `POST /v1/deliberations`

Requires both the API key and a non-empty `Idempotency-Key` header. Reusing the same key returns the existing run without another model execution.

```bash
curl -X POST http://127.0.0.1:8080/v1/deliberations \
  -H 'Content-Type: application/json' \
  -H 'X-OpenOyster-Key: YOUR_KEY' \
  -H 'Idempotency-Key: review-2026-07-14' \
  -d '{
    "mission": {
      "goal": "Choose a reversible response",
      "decision_question": "Which supported option should we choose?",
      "constraints": ["Do not introduce facts outside Pack evidence"],
      "preferences": ["Prefer reversible options"],
      "context": "Control background only"
    },
    "packs": ["pack-a", "pack-b"],
    "impact_baseline_packs": ["pack-a"],
    "allow_compatible_packs": false
  }'
```

`mission.goal` and `mission.decision_question` are required. `constraints` and `preferences` default to empty lists; `deadline`, `context`, and `mission_charter_id` are optional. `packs` contains installed Pack IDs. `impact_baseline_packs`, when present, must be a subset of `packs`. Strict Pack admission is the default; `allow_compatible_packs` is explicit opt-in.

The response is safe run metadata: run ID, status/outcome, frozen-input digests, contract/template versions, timestamps, and model-attempt count. It omits Mission free text, Pack bodies, prompts, filesystem locations, storage URIs, runtime configuration, and errors with internal detail.

### Run inspection and audit

- `GET /v1/deliberations/{id}` — safe run metadata.
- `GET /v1/deliberations/{id}/dossier?format=json|markdown` — persisted dossier. The default is `json`.
- `POST /v1/deliberations/{id}/replay` — deterministic audit revalidation. It does not call the LLM.
- `GET /v1/deliberations/{id}/cognitive-impact` — citation-scope projection only; it is not a Pack diff or baseline rerun.
- `GET /v1/deliberations/{id}/knowledge-requests` — inert persisted requests. This endpoint never executes retrieval or Pack updates.

All D1 responses sanitize raw Pack bodies, full prompts, filesystem paths, storage URIs, runtime/secret fields, and raw model responses. Citation anchors, Pack identities/digests, assertions, decision data, and replay digest results remain available for audit.

## 16. Decision Continuity D2

D2 continues only a `completed` parent run whose outcome is `abstain` and whose persisted `knowledge_requests` artifact contains the requested local keys. The request supplies already-installed Pack IDs; OpenOyster does not create or update Packs, discover external facts, or compute Pack-content diffs.

### `POST /v1/deliberations/{id}/continue`

Requires the configured D1 API key and a non-empty `Idempotency-Key` header.

```bash
curl -X POST http://127.0.0.1:8080/v1/deliberations/PARENT_RUN_ID/continue \
  -H 'Content-Type: application/json' \
  -H 'X-OpenOyster-Key: YOUR_KEY' \
  -H 'Idempotency-Key: review-d2-001' \
  -d '{
    "packs": ["new-pack-id"],
    "fulfilled_knowledge_request_keys": ["kr_no_evidence"],
    "impact_baseline_packs": ["new-pack-id"],
    "allow_compatible_packs": false
  }'
```

Request fields are `packs` (at least one installed Pack ID), `fulfilled_knowledge_request_keys` (claimed fulfilled keys persisted on the parent), optional `impact_baseline_packs`, and optional `allow_compatible_packs` (default `false`). A claim is verified only by the transition verifier; `evidence:no_evidence` currently requires newly cited evidence. The child keeps the parent Mission snapshot and records `parent_run_id`.

The continuation-specific stable error codes are:

- `idempotency_key_conflict` — key belongs to a different parent.
- `parent_run_not_found` — parent ID does not exist.
- `parent_run_not_completed_abstain` — parent is not a completed abstention.
- `parent_knowledge_requests_missing` — parent has no persisted Knowledge Request artifact.
- `fulfilled_knowledge_request_keys_empty` — no fulfilled key was supplied.
- `fulfilled_knowledge_request_keys_unknown` — a supplied key is not on the parent.

These input/continuation errors return HTTP `422` with `{"detail":{"code":"..."}}`. A child that completes with `failed_execution` because of a provider/runtime failure returns HTTP `502`; this is an execution failure, not an epistemic abstention. An unexpected continuation exception returns HTTP `500` with code `deliberation_execution_failed`. Pack-scope/profile failures are surfaced as the normal failed-input response and code.

### `GET /v1/deliberations/{id}/transition`

Returns the sanitized persisted transition artifact for a child run. Its method is `cognitive_transition_v2`. It separates `claimed_knowledge_requests`, `verified_fulfilled_knowledge_requests`, and `unverified_claimed_knowledge_requests`; `fulfilled_knowledge_requests` is a compatibility alias for the verified subset. Unverified claims and critic-promoted gaps remain in `remaining_knowledge_requests`.

The parent and transition artifact are immutable. Repeating the same continuation with the same idempotency key and parent returns the existing run state; it does not create another child execution. This also applies to a `failed_execution` child, so retrying after provider/runtime recovery requires a new `Idempotency-Key`. Replay remains an LLM-free audit of stored child artifacts and does not compare Pack contents.
