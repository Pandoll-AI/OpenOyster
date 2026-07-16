# Changelog

All notable changes are documented here. OpenOyster is pre-`1.0`; compatibility may change between minor releases.

## Unreleased — deliberation integrity hardening

Adversarial review of D1/D2 (two Codex passes) surfaced integrity and disclosure
defects; this wave closes them and adds bounded features.

### Fixed

- Made the selection gate fail-closed: fabricated belief/option references, incomplete
  or duplicate constraint coverage, `pass` critic verdicts that contradict their own
  findings, and viable options lacking expected/adverse scenarios can no longer produce
  a `select` decision.
- Persisted every gated assertion and anchor (including opposing evidence, exclusion
  reasons, and constraint rationales) with a role-tagged citation and a post-persist
  parity check, so Cognitive Impact and cognitive transition no longer see a subset.
- Stopped leaking LLM/provider text: Pydantic validation errors record `loc`/`type`
  only, and provider exceptions record the exception class plus a digest, never raw text.
- Bound idempotency to a request fingerprint and made run creation atomic against a
  concurrent unique-key race; mismatched reuse returns `idempotency_request_mismatch`.
- Recorded the real per-stage model and effort (and duration) instead of a hardcoded
  default, and added exactly one bounded retry per stage on invalid/gate failures.
- Preserved unclaimed parent Knowledge Requests in the transition's remaining list and
  flagged when a child scope drops a Pack the parent had cited.
- Required continuation to add at least one new Pack (`no_new_pack_scope`) and to match
  the stored parent Mission digest (`parent_integrity_mismatch`).
- Split no-evidence abstention into `pack_has_no_evidence` vs `no_match_in_pack_evidence`.
- Bound Knowledge Request verification to evidence the child actually cited, replaced the
  dishonest verification method label with a per-gap verifier registry, and made replay
  recompute Cognitive Impact/transition from source (version-aware, no false mismatch)
  using an immutable fulfilled-keys column instead of the artifact under audit.

### Added

- Optional second-pass critic (`OPENOYSTER_CRITIC2_PROVIDER`, default off) that runs the
  critic on another provider and combines verdicts conservatively; the primary critic
  artifact stays immutable.
- Machine-readable Knowledge Request export (`--format export` / `?format=export`) for
  OpenCrab or human consumption as a collection request.
- Golden replay CI test that pins dossier digests to guard contract drift.
- Draft requirements for Flip Condition Monitoring (D3) and a Decision Outcome Ledger.

## 0.4.0 — LLM-first rebuild

### Removed

- Removed keyword rule-engine extraction as the product claim for intelligence quality.
- Removed circular evaluation paths that judged extraction quality against self-generated outputs.
- Removed self-label policy tuning as a claimed quality loop.
- Removed documentation claims for shipped autonomous policy tuning and system-wide scope-review behavior that the current code does not provide.

### Added

- Added codex CLI based batch extraction with structured schema validation, bounded JSON repair, and deferred failure recording.
- Added SQLite FTS5 retrieval over chunks with matched-term/provenance inspection.
- Added LLM-based hypothesis merge decisions for similar scoped claims.
- Added directional counter-evidence evaluation that requires opposing posture and verbatim quotes.
- Added the gold-set evaluation harness for core entity recall, signal type F1, quote existence, and counter-evidence precision.
- Added SQL-side event polling by wanted event type and max-id checkpoint advancement for sparse streams.
- Added local development `run.sh` launcher and real AI/tech RSS examples.

### Notes

- Gold-set labels are still marked unreviewed.
- Counter-evidence precision should be read with the judge-independence caveat in `docs/EVAL_REPORT.md`.

> Correction: the 0.1.0-0.3.0 entries below were reconstructed retroactively in a single initial commit. Treat them as historical reconstruction, not as a verified sequence of tagged release notes.

## 0.3.0 — open-source evidence quality alpha

### Retrieval and evidence

- Added retrieval result metadata, matched terms, SQLite FTS5/PostgreSQL full-text auto retrieval, source-diversity caps, and stance-judge evidence filtering.
- Added evidence/provenance inspection helpers, CLI commands, and read API endpoints.
- Dashboard now shows evidence counts, source diversity, and provenance availability.

### Connectors and evaluation

- Added read-only RSS ingestion from YAML feed lists.
- Added read-only GitHub release and issue ingestion with optional `OPENOYSTER_GITHUB_TOKEN`.
- Added deterministic fixture evaluation for signal type, counter-evidence, and traceability regressions.
- Added sample evaluation fixtures under `examples/eval`.

### Operations and documentation

- Added `openoyster doctor-dev` for local development toolchain checks.
- Updated README, API, connector, and policy documentation for the new 0.3 surfaces.

### Known limitations

- PostgreSQL full-text mode is optional; vector retrieval is still not included by default.
- GitHub issue comments, RSS article crawling, authenticated feeds, RBAC, approval queues, and external write tools remain out of scope.

## 0.2.0 — audited product-oriented alpha

### Reliability

- Replaced unsafe duplicate rollback behaviour with per-item durable intake state.
- Added safe filtered event checkpoints, partial-ack protection, idempotent event emission, loop leases, and loop-run telemetry.
- Added chunk/task retry state and post-commit archive maintenance.
- Added Alembic schema and migration command.

### Intelligence loops

- Split planning from execution and maintenance from intake.
- Added registered support, counter-evidence, baseline, and hypothesis-brief tools.
- Added evidence-derived hypothesis confidence, contradiction, staleness, and decision traces.
- Added grounded utilisation records and system-behaviour telemetry.

### Providers and connectors

- Remote OpenAI-compatible provider now parses and uses structured remote output.
- Remote fallback records provider identity and warnings.
- Added PDF, DOCX, HTML, YAML, JSONL, TSV, and guarded public HTTP ingestion.

### Evaluation and policy records

- Replaced prose-length self-rating with evidence posture and verified completion metrics.
- Added explicit artifact feedback and trace outcome labels.
- Added policy expiry, promotion, rejection, and experiment records.

### Security and operations

- Added default-disabled mutation API, shared-key auth, HTML escaping, SSRF controls, readiness/doctor checks, non-root container, PostgreSQL Compose deployment, and migration service.
- Added detailed user, Korean user, contributor, architecture, operations, policy, API, connector, threat-model, audit, and release documentation.
- Added Ruff, mypy, CI build verification, CLI lifecycle tests, migration tests, and 81% measured statement coverage in the packaged release.

### Known limitations

- Lexical bounded retrieval, no vector index.
- No RBAC, multi-tenancy, secret-manager integration, or formal regulatory validation.
- No distributed broker or load/chaos certification.
- Limited built-in source and action connectors.

## 0.1.0 — initial prototype

- Initial event-loop scaffold, SQLite persistence, CLI/API/dashboard, local heuristics, and basic documentation.
- This version was subsequently judged demo-grade because event safety, provider behaviour, evaluation, policy records, security, migrations, and tests were insufficient.
