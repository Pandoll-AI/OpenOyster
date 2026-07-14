# MVP-P1 Trusted OpenCrab Pack Runtime — Final Acceptance

Date: 2026-07-13
Status: accepted for the deliberately narrow local MVP

## Product boundary

OpenOyster initially consumes trusted server-local OpenCrab Pack directories well. The implemented product surface is Pack validation, immutable installation, registry inspection, namespaced multi-Pack retrieval, and evidence-grounded question answering that fails closed outside Pack knowledge.

ZIP/quarantine admission, remote Pack ingestion, automatic updates and rollback, multimodal analysis, Neo4j execution, and large-scale retrieval indexing remain deferred. They are not hidden dependencies of this MVP.

## Accepted behavior

- `compatible` admission validates the four-file OpenCrab validator layout.
- `strict` admission validates the documented eleven-file layout.
- Validation computes the source digest before and after reading and never mutates the source.
- Installation copies digest-addressed bytes, verifies the copied digest, and persists Pack, file, node, edge, and evidence records.
- Reinstalling the same digest is idempotent. A different digest for the same Pack/version conflicts without replacing the active Pack.
- Global node, edge, and evidence ids include Pack identity, version, digest, kind, and encoded local id.
- Retrieval is restricted to active Pack revisions and preserves Pack/evidence provenance.
- No retrieved evidence, provider failure, malformed output, missing citations, local-only citations, or invented global citations returns `unknown`.
- Pack content and identifiers are treated as untrusted prompt data. ASCII and Unicode line-separator boundary injection is escaped before generation.
- CLI commands are available at `openoyster pack validate|install|list|show|query`.
- API resources are available at `/v1/packs/*`. Validate, install, and query require the configured API key. Responses omit Pack bodies and server-local source/storage paths.

## Executed delegation route

- Grok core writer: native `grok-4.5`, `xhigh`. Implemented persistence, validation/install, retrieval, grounded answering, and core tests.
- Codex integration writer: `gpt-5.6-terra`, `high`. Implemented CLI/API integration, metadata/collision/prompt fixes, docs, and focused tests.
- Codex remediation writer: `gpt-5.6-terra`, `high`. Reproduced the raw identifier boundary failure and added the initial header escaping regression.
- Root orchestrator: closed API authentication/error semantics and Unicode separator hardening with observed RED/GREEN tests; integrated and reran host gates.
- Final reviewer: `claude-opus-4-8`, `high`, read-only. It reviewed the complete implementation, requested no release-blocking fixes, and returned `MERGE_OK` with Critical 0, Major 0, Minor 0 after the final remediation.

Cursor was not used. Terra did not serve as the release reviewer.

## Final verification evidence

```bash
PATH="$PWD/.venv/bin:$PATH" make check
```

Observed on the host:

- Ruff: pass.
- mypy: pass over 64 source files.
- pytest: 110 passed, with one existing Starlette TestClient deprecation warning.
- Package build: `openoyster-0.4.0.tar.gz` and `openoyster-0.4.0-py3-none-any.whl` built successfully.

Focused Pack verification passed 35 tests across fixture compatibility, runtime behavior, and CLI/API integration. A separate temporary-workspace CLI smoke run validated, installed, listed, and queried the minimal Pack. Source fixture digests were identical before and after that run.

## Residual MVP debt

- An authenticated writer can select any server-readable directory. This matches the trusted-local-operator boundary but should become a configured trusted-root allow-list before broader exposure.
- Retrieval currently loads active Pack records into memory and scores them in Python. It is correct for the MVP but not the future large-Pack architecture.
- A database commit failure after the digest-addressed copy can leave an unregistered storage directory. The database view stays rolled back and a retry self-heals, but storage reconciliation belongs in the next durability pass.
- The current Starlette TestClient deprecation warning should be removed when the dependency stack adopts its replacement.

## Repository state

No commit, stage, push, deployment, or production service mutation was performed as part of this acceptance.
