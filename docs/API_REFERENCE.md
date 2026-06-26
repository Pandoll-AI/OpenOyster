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
{"status":"ok","version":"0.3.0"}
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

## 11. Premise review

### `POST /v1/premise-review`

Authentication required. Queues a manual `premise.review_requested` event and returns its ID.

## 12. Policies

### `GET /v1/policies`

Returns active, candidate, shadow, and archived policies.

### `POST /v1/policies/{policy_id}/promote`

Authentication required. Validates and manually promotes the selected policy, archives the previous active policy, and emits `policy.promoted`.

## 13. Loop telemetry

### `GET /v1/loop-runs?limit=50&offset=0`

Returns loop owner, status, timing, consumed/emitted counts, created-record counts, notes, and errors.

## 14. Example calls

```bash
curl http://127.0.0.1:8080/v1/status

curl -X POST http://127.0.0.1:8080/v1/run-cycle \
  -H 'X-OpenOyster-Key: YOUR_KEY'

curl -X POST http://127.0.0.1:8080/v1/premise-review \
  -H 'X-OpenOyster-Key: YOUR_KEY'
```

## 15. API limitations

- No RBAC or user identity.
- No cursor-token pagination; list endpoints use offset/limit.
- No rate limiter in the application.
- No CORS policy configuration because the default dashboard is same-origin and read-only.
- Raw document responses may expose sensitive text.
- Evidence/provenance endpoints expose bounded excerpts and source metadata, not full raw document bodies.
- No stable compatibility guarantee before `1.0`.
