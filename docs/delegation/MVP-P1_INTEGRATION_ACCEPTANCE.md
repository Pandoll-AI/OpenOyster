# MVP-P1 Trusted Pack Runtime — Integration Acceptance

Role: sole integration writer
Scope: trusted-directory Pack CLI/API surfaces, Pack integrity and fail-closed gaps, Codex Pack-answer stage assertion, and user-facing documentation.
Date: 2026-07-13

## Blind-spot pass and exit proof

The integration risks were API write authorization, local-id collision across active Packs, promotion-dependent evidence validation, metadata loss during evidence persistence, delimiter injection into Pack prompts, and a missing Codex pipeline-stage lock.

Exit proof: focused Pack runtime/CLI/API/Codex tests, static/type checks, diff validation, and the required `make check` gate. The delegated sandbox could not bootstrap its isolated build dependency, then the root host reran the same complete gate successfully.

## TDD RED

Before any CLI/API production edit, the following command was run after adding the focused tests:

```bash
PATH="$PWD/.venv/bin:$PATH" pytest \
  tests/test_pack_runtime.py::test_all_nonempty_evidence_refs_must_resolve_even_without_promotion \
  tests/test_pack_runtime.py::test_evidence_aliases_are_normalised_without_dropping_metadata \
  tests/test_pack_runtime.py::test_colliding_local_citation_fails_closed_across_active_packs \
  tests/test_pack_runtime.py::test_untrusted_pack_content_cannot_close_prompt_data_boundary \
  tests/test_pack_cli_api.py -q --no-cov
```

Observed: **6 failed**.

- Unpromoted node/edge evidence references incorrectly passed validation.
- Evidence `links`, `hash`, and `clip` metadata was lost.
- A colliding local citation (`evidence:1`) returned `supported` across two active Packs.
- Pack text could emit a second `END_UNTRUSTED_PACK_DATA` delimiter.
- `openoyster pack` did not exist.
- `POST /v1/packs/install` was absent (`404`, instead of its write-auth boundary).

## Implemented behavior

- `openoyster pack validate|install|list|show|query` returns one JSON object/array per call.
- API: `POST /v1/packs/validate`, write-authorized `POST /v1/packs/install`, `GET /v1/packs`, `GET /v1/packs/{pack_id}`, and `POST /v1/packs/query`.
- API Pack errors expose stable codes only; source/storage paths and Pack content are omitted.
- Every nonempty node or edge evidence reference must resolve, regardless of promotion status.
- Evidence aliases are normalized losslessly: `links`, `hash`/`content_hash`, and `vision`/`clip`.
- Grounded answers accept retrieved global evidence ids only. Local-id collision therefore fails closed.
- Pack records are JSON-escaped within the untrusted-data boundary, so embedded newlines cannot close the delimiter.
- The Codex configuration test now locks `pack_answer` to `medium` effort and `reasoning` while retaining the repository single-model assertion.

## GREEN

After implementation, the focused command was run:

```bash
PATH="$PWD/.venv/bin:$PATH" pytest \
  tests/test_pack_runtime.py \
  tests/test_pack_cli_api.py \
  tests/test_llm_codex.py -q --no-cov
```

Observed: **25 passed**. The only output was the repository's existing TestClient deprecation warning.

The static/type command was also run:

```bash
git diff --check
PATH="$PWD/.venv/bin:$PATH" ruff check \
  src/openoyster/cli.py src/openoyster/api/app.py \
  src/openoyster/services/opencrab_packs.py \
  src/openoyster/services/pack_answering.py \
  src/openoyster/services/prompts.py \
  tests/test_pack_runtime.py tests/test_pack_cli_api.py tests/test_llm_codex.py
PATH="$PWD/.venv/bin:$PATH" mypy src/openoyster
```

Observed: `git diff --check` clean; Ruff `All checks passed!`; mypy `Success: no issues found in 64 source files`.

## Required full gate

```bash
PATH="$PWD/.venv/bin:$PATH" make check
```

Observed:

- Ruff: passed.
- mypy: passed (`64 source files`).
- pytest: passed (`106 passed, 1 warning`).
- build: **failed**. `python -m build` creates an isolated environment and attempted to bootstrap `hatchling>=1.25,<2`; DNS resolution for `pypi.org` failed under the no-network task constraint.

An offline follow-up was attempted:

```bash
PATH="$PWD/.venv/bin:$PATH" python -m build --no-isolation
```

Observed: **failed** because the existing local environment cannot import `hatchling.build`. No dependency was installed and no external state was changed.

## Deferred by product scope

- ZIP extraction and quarantine.
- Automatic Pack update, revision diff, rollback, and tombstones.
- OCR/CLIP/audio/video analysis and derived multimodal evidence.

## Integration gate status

- Focused behavior, CLI/API authorization, fail-closed citation, prompt-boundary, lint, and type proof: **PASS**.
- Delegated sandbox build: **BLOCKED** by network isolation; this was an environment limitation, not accepted as the final gate.
- Root host `make check`: **PASS** — Ruff, mypy over 64 source files, 106 tests, sdist, and wheel.

## Root security closure

The root audit found that Pack validation could inspect a server-local path and Pack query could invoke the configured LLM without authentication. A new RED test proved both endpoints returned `200` without a key. The API now applies the existing write-auth dependency to `POST /v1/packs/validate` and `POST /v1/packs/query`; the end-to-end API test verifies `401` without the key and success with it. Validation reports with error issues now return sanitized `422 pack_validation_failed` responses instead of HTTP `200`.

The final focused command passed 31 tests across fixture validation, Pack runtime, and CLI/API integration. A separate CLI smoke run validated, installed, listed, and queried the minimal Pack in a temporary workspace, then confirmed the source fixture digests were unchanged.
