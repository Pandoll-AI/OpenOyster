# MVP-P1 Core Acceptance Evidence

Role covered: sole core implementation writer
Scope: persistence, trusted-directory validation/install, namespaced retrieval,
grounded answering, LLM citation fail-closed
Date: 2026-07-13

## Authorized paths changed

- `src/openoyster/models.py`
- `src/openoyster/migrations/versions/0003_opencrab_pack_runtime.py`
- `src/openoyster/services/opencrab_packs.py`
- `src/openoyster/services/pack_retrieval.py`
- `src/openoyster/services/pack_answering.py`
- `src/openoyster/services/prompts.py`
- `src/openoyster/services/llm_judges.py`
- `.codex-llm/pipeline.json`
- `tests/test_pack_runtime.py`
- `docs/delegation/MVP-P1_CORE_ACCEPTANCE.md`

## TDD RED/GREEN evidence

### Cycle 1 — install minimal fixture and retrieve claim

1. Added `test_install_minimal_fixture_and_retrieve_supported_claim` only.
2. RED command:

```bash
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_pack_runtime.py::test_install_minimal_fixture_and_retrieve_supported_claim -q --no-cov
```

3. Observed RED: `ImportError: cannot import name 'PackEdge' from 'openoyster.models'`.
4. Implemented models + validate/install + retrieval.
5. GREEN: same command → `.` (1 passed).

### Cycle 2 — remaining core scenarios

Added and greened:

| Test | Behavior |
|------|----------|
| `test_colliding_local_ids_get_distinct_global_ids` | Two packs, same local ids, distinct global ids |
| `test_reinstall_same_digest_is_noop` | Same digest reinstall does not duplicate records |
| `test_same_version_different_digest_is_conflict` | Mutated copy conflicts; active install intact |
| `test_strict_rejects_minimal_and_accepts_full_layout` | Strict profile layout gate |
| `test_query_returns_pack_evidence_provenance` | Supported answer with Pack/evidence provenance |
| `test_unrelated_query_returns_unknown_without_generation` | No LLM call when retrieval empty |
| `test_generator_unknown_evidence_citation_fails_closed` | Invented citation → `unknown` |
| `test_source_fixture_digest_and_file_count_unchanged` | Source fixtures 4/11 digests preserved |
| `test_alembic_upgrade_creates_pack_tables` | Migration creates pack tables |

Command:

```bash
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_pack_runtime.py -q --no-cov
```

Observed: `..........` (10 passed).

### Cycle 3 — mypy nullable JSON narrowing (2026-07-13 follow-up)

Root independently verified 10 focused tests + Ruff, but mypy reported 8
errors in `src/openoyster/services/opencrab_packs.py` (~248, 650-651, 698,
728-729, 732, 735).

**Cause:** ternary expressions re-called `record.get(key)` twice, so mypy did
not narrow `Any | dict | None` to `dict` before `.get()` / `dict(...)`.

**Fix (runtime-correct narrowing, no `# type: ignore` / cast):**

- `_mapping_field(record, key) -> dict[str, Any]` — single `.get`, `isinstance`
  narrow, then `dict(value)` or `{}`
- `_optional_mapping_field(record, key) -> dict[str, Any] | None` — same for
  optional OCR/vision objects
- Install path uses these helpers for properties/quality/source/parser/location

Commands and observed results:

```bash
PATH="$PWD/.venv/bin:$PATH" mypy src/openoyster
# Success: no issues found in 64 source files
# MYPY_EXIT=0

PATH="$PWD/.venv/bin:$PATH" pytest tests/test_pack_runtime.py -q --no-cov
# ..........
# PYTEST_EXIT=0

PATH="$PWD/.venv/bin:$PATH" make check
# ruff: All checks passed!
# mypy: Success: no issues found in 64 source files
# pytest: 3 failed, 97 passed, 1 warning
# CHECK_EXIT=2
```

`make check` remaining failures (not fixed in this core writer unit):

| Failure | Path | Relation to core scope |
|---------|------|------------------------|
| `test_cli_local_lifecycle` | `tests/test_cli_lifecycle.py` | Pre-existing: CLI output embeds Rich ANSI (`Copied \x1b[1m1\x1b[0m file`); not authorized; unrelated to Pack runtime |
| `test_eval_gold_cli_smoke_with_stub` | `tests/test_goldset_cli.py` | Pre-existing: same Rich markup mismatch (`Gold documents evaluated: \x1b...`); not authorized |
| `test_repository_codex_config_uses_single_model_with_graded_effort` | `tests/test_llm_codex.py` | Expects exact 5 pipeline stages; core unit added authorized `pack_answer` stage in `.codex-llm/pipeline.json` (required by `load_codex_stage_config` for Codex `pack_answer`). Test update is outside authorized paths |

Core scope gate status after typing fix:

- Focused Pack tests: **PASS** (10/10)
- Ruff: **PASS**
- mypy: **PASS** (0 issues)
- Full `make check`: **FAIL** (3 external/out-of-scope tests as above)

## Root integration recheck

The failures above record the core writer's bounded handoff, not the final repository state. The integration pass updated the Pack pipeline-stage assertion and reran the complete gate on the host environment.

```bash
PATH="$PWD/.venv/bin:$PATH" make check
```

Final observed result: Ruff passed, mypy passed for 64 source files, all 106 tests passed, and both the sdist and wheel built successfully. The two ANSI failures recorded by the writer did not reproduce in the final host run.

## Out of this writer scope

- CLI `pack validate|install|list|show|query`
- API resources
- ZIP admission
- Commits / push / deploy
- Updating `tests/test_llm_codex.py` stage lock for `pack_answer`
- Fixing pre-existing Rich ANSI CLI assertion failures

## Product invariants checked in core tests

- Source fixtures not modified (digest + file count lock).
- Global ids include pack id, version, digest, kind, percent-encoded local id.
- Missing manifest version stored as `unversioned`.
- Same digest reinstall is no-op.
- Same `(pack_id, version)` different digest is conflict.
- Retrieval scope is active installs only.
- No retrieval context → `unknown` without generation.
- Unverified citation → `unknown`.
- Pack content wrapped as untrusted data in prompt.
