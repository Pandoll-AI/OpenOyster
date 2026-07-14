# D1 Autonomous Deliberation — Implementation Brief

## Outcome

Implement the complete vertical slice specified by
`docs/AUTONOMOUS_DELIBERATION_D1_REQUIREMENTS.md`.

The stopping condition is an end-to-end Mission + frozen OpenCrab Pack execution that
persists and exposes beliefs, options, scenarios, critic, selection/abstention, flip
conditions, knowledge requests, cognitive impact, dossier, and deterministic audit
replay.

## Ownership and sequence

1. Grok 4.5 xhigh owns the core TDD implementation: contracts, schema/migration,
   frozen retrieval, evidence anchors, five-stage orchestration, impact, dossier, replay,
   deterministic stub, and focused tests.
2. Codex Terra high owns CLI/API integration, authorization/sanitization, documentation
   alignment, and integration tests after the core is accepted.
3. Root orchestrator owns diff inspection, local remediation, complete gates, README and
   hero integration, commit, and push.
4. Claude Opus owns the final read-only release review. Terra must not be the final reviewer.

Only one writer may operate in this checkout at a time.

## Non-negotiable constraints

- Strict RED before production changes for every implementation unit.
- Preserve every pre-existing dirty file and change.
- Do not edit migration `0003`; add `0004`.
- Do not use web, external APIs, recursive agents, or dependency installation.
- Do not implement Pack creation/update/diff/rollback, multimodal processing, or Neo4j.
- Do not commit, stage, push, deploy, bind ports, or touch secrets.
- Use only Pack evidence snapshots as factual citation targets.
- Mission is control input, never evidence.
- Replay never calls the LLM.
- Cognitive Impact is citation-scope projection, not Pack diff.

## Required implementation evidence

- Record the first observed RED command and failure for each unit.
- Record focused GREEN commands and results.
- List changed files.
- State every intentional deviation from the requirements.
- Do not claim a gate that was not actually run.

## Core acceptance

- Exact Pack install IDs are frozen before the first LLM call.
- D1 retrieval accepts install IDs and does not re-resolve active Packs.
- Exact quote or JSON pointer anchors are validated deterministically.
- Strict contracts reject extra fields and unclassified narratives.
- Happy path uses five calls; no-evidence path uses zero calls.
- Selection and abstention gates match the requirements.
- Knowledge Requests are inert persisted records.
- Dossier JSON/Markdown, Cognitive Impact, and audit replay persist and round-trip.
- SQLite migration test passes and schema remains PostgreSQL-portable.
- Existing Pack behavior remains green.

## Integration acceptance

- CLI commands and API endpoints in the requirements exist.
- API key and `Idempotency-Key` behavior is tested.
- Responses omit Pack bodies, prompts, paths, storage URIs, and secrets.
- Korean user docs explain the complete flow with a fixture example.
- Existing CLI/API behavior remains green.

## Final gate

```bash
git diff --check
PATH="$PWD/.venv/bin:$PATH" make check
```

Then a read-only Claude Opus review must report Critical 0 and Major 0 after any fixes.
