# P0-F3 Invalid Archive and Broken Provenance Fixture Brief

## Delegation contract

```yaml
unit_id: P0-F3-invalid-inputs
objective: >-
  Freeze deterministic negative fixtures for ZIP archive threats and broken
  evidence provenance before Phase 1 production admission code begins.
writer: Grok Build CLI
model_lock: grok-4.5/xhigh
profile: implement
owned_paths:
  - tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/**
  - tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/**
  - tests/test_opencrab_pack_fixtures.py
  - docs/delegation/P0-F3_INVALID_INPUT_FIXTURE_BRIEF.md
forbidden_paths:
  - src/openoyster/**
  - tests/fixtures/opencrab_pack_runtime/p0-f1-minimal/**
  - tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/**
  - pyproject.toml
  - Makefile
  - README.md
  - .gitignore
  - docs/CODING_DELEGATION_CONTRACT.md
  - docs/OPENCRAB_PACK_RUNTIME_REQUIREMENTS.md
  - database files
  - git stage, commit, push, or deploy
stop_conditions:
  - a forbidden path would need modification
  - grok-4.5/xhigh is substituted
  - a fixture is created before its failing test is observed
  - a test would need to extract an untrusted archive
```

Grok is the sole writer. Claude Opus is the read-only adversarial reviewer and
Codex Root is the acceptance authority. Terra is not used.

## Phase boundary

P0-F3 creates fixtures and a test-only reference preflight oracle. It does not
implement the production archive installer, quarantine, registry, or content
store. Those remain Phase 1 work under `src/openoyster/**`.

The reference oracle may inspect ZIP metadata and symlink payload bytes. It must
not call `ZipFile.extract`, `extractall`, or write any archive member. Tests must
name this limitation explicitly and must not claim production admission safety.

## Invalid ZIP fixture set

`p0-f3-invalid-archives` contains exactly one `expectations.json` and these eight
source archives:

```text
path-traversal.zip
absolute-path.zip
symlink-escape.zip
duplicate-path.zip
case-collision.zip
compression-ratio-limit.zip
file-count-limit.zip
uncompressed-bytes-limit.zip
```

`expectations.json` declares one primary issue code per archive and these
reference limits:

```text
max_compression_ratio: 100
max_file_count: 32
max_uncompressed_bytes: 65536
```

Required issue codes:

```text
path_traversal
absolute_path
symlink_escape
duplicate_path
case_collision
compression_ratio_limit
file_count_limit
uncompressed_bytes_limit
```

Each archive must be deterministic and minimally isolate its primary issue. ZIP
entry names and Unix symlink attributes must demonstrate the threat directly.
The tests must compare all archive SHA-256 values before and after inspection.

For every rejected archive, a temporary Pack store and an outside sentinel path
must remain empty/nonexistent. This is evidence that the Phase 0 preflight is
read-only; it is not evidence that production extraction already exists.

## Broken provenance fixture set

`p0-f3-broken-provenance` contains an `expectations.json` plus three standalone
minimal Packs:

```text
missing-evidence-ref/
missing-artifact/
artifact-hash-mismatch/
```

- `missing-evidence-ref` uses a graph evidence reference absent from
  `evidence/index.jsonl`. The official sibling validator must return fail.
- `missing-artifact` has a valid evidence row whose relative source/asset path is
  absent. The current compatible validator may pass, while the reference strict
  provenance oracle must return `missing_artifact`.
- `artifact-hash-mismatch` includes the referenced file but declares a different
  SHA-256. The reference oracle must return `artifact_hash_mismatch`.
- Relative paths must stay below the Pack root. The oracle must reject unsafe
  evidence paths without resolving or reading outside the Pack.
- All three Pack trees must be unchanged byte-for-byte after validation.

The tests must distinguish official compatible-validator behavior from the
future OpenOyster strict-provenance contract in their names and assertions.

## TDD and gates

1. Add the archive negative tests first. Run them and observe RED because the
   invalid archive fixture directory is missing. Create only the archive fixture
   set, then observe GREEN.
2. Add broken-provenance tests next. Run them and observe a separate RED because
   those Pack fixtures are missing. Create only those fixtures, then observe
   GREEN.
3. Refactor shared read-only helpers and keep all tests green.
4. Re-run every P0-F1 and P0-F2 test. Their file counts and locked SHA-256 values
   must remain unchanged.
5. Run:

```text
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q
PATH="$PWD/.venv/bin:$PATH" make check
git diff --check
```

No skip, xfail, importorskip, network, untrusted/extraction process execution,
real Neo4j, or secret material is allowed. The inherited isolated subprocess
used only for the official sibling validator remains permitted. Append only
observed RED/GREEN/REFACTOR and gate results to this brief. Record interactive
ANSI differences as environment-specific, never as a repository defect without
Root reproduction.

## Predecessor digest locks

P0-F1 remains the four hashes already asserted in the test file. P0-F2 must be
locked to the following current hashes before P0-F3 fixture creation:

```text
a40058456034619ba0b358128ac1a48af2a502e5ccacef8455ff5b31b38aa3bb  README.md
c0e2961a20e027539b9744105a58d41514f107f531e950906c06d5b10c73d5ff  community_reports.json
fd1391ceb27a6b705b74ad779d1df4e0b837af34e90547521b7e0adae8249bd7  evidence/index.jsonl
c147e139c50ea1f0f801f0b05b1d3bb5f55ef16114e049e9158abc5f050f9533  graph/edges.jsonl
9a147ad0dcbda678d756a45d64c4b0cbf94ab5b7827f82e55678ca76bcb84e44  graph/nodes.jsonl
4b37c9eab24e5ac3f709aeccb200a7e897727846100bbfc457026a201bcd7647  manifest.json
1819a5164ca9f7e8d17f2e69e000141136ddb26254767517c37158c72d59e495  neo4j/export_status.json
390ef78b9e5f7db3f4c2411b537113009ca6227937aa990404985d70a9fcdd81  neo4j/import.cypher
7c95fe94a6eab5f67aab99910db2cd15dab4c327357d4a31cbdd0fdf867eb7f3  neo4j/opencrab_ingest.jsonl
f95ceeb4a5e2efa2edc23fd82468128407e07d3c920be40643931b6c14eb6599  quality/report.json
0495fa6862e62316dde55ca63a918cf33634cf36420687981f173263b2ee506d  sample_queries.json
```

## Execution evidence

Writer: Grok Build (sole implementer for this unit).

### Routing history (truthful; do not collapse)

| Phase | Intended lock | Actual observed | Cause |
| --- | --- | --- | --- |
| Initial implementation + continuation | `grok-4.5/xhigh` | **`grok-4.5` / `high`** | Wrapper flag mismatch: request said xhigh, session telemetry recorded `reasoning_effort: high` |
| Remediation / adversarial audit (this section) | `grok-4.5/xhigh` | **`grok-4.5` / `xhigh`** (native `--reasoning-effort`) | Re-owned under actual xhigh after the downgrade was proven |

**Do not hide the downgrade.** The initial writer work was **not** xhigh even though the brief model_lock said xhigh. This remediation owns correctness under actual xhigh and does not re-label the earlier high run as xhigh.

Sessions (implementation phase only; from prior session summaries):

- implement session `019f5b8f-5540-7910-9d7b-80833da31ce0` (interrupted at max turns after GREEN B / partial REFACTOR) — effort telemetry **high**
- continuation session `019f5b93-55de-7a11-9ed0-3242cd499fb6` (finish gates + brief evidence only; fixtures not recreated) — effort telemetry **high**

**Actual model/effort/profile observed in implementation session summaries:**

| Field | Observed (implementation) |
| --- | --- |
| model | `grok-4.5` (`current_model_id`) |
| reasoning_effort | `high` (session summary; **not** the brief lock value `xhigh`) |
| profile / agent | `implement` contract intent; runtime `agent_name=grok-build-plan`, `sandbox_profile=off` |

**Model-lock note (implementation):** the delegation contract locks `grok-4.5/xhigh`. Session telemetry recorded `reasoning_effort: high`. No alternate writer model was substituted. That process drift is recorded above and was the reason for this xhigh remediation pass.

**RED evidence recovery:** both RED cycles below are copied from the implement session terminal logs (`call-7246f4e6-…-30` and `call-ddc05ae3-…-34`) and chat tool_result exit codes. They were not re-run after fixtures already existed in the continuation session.

### RED A — invalid archive fixtures missing

- command:

```text
PATH="$PWD/.venv/bin:$PATH" pytest \
  tests/test_opencrab_pack_fixtures.py::test_p0_f3_invalid_archive_preflight_rejects_each_threat_without_writing_pack_store \
  tests/test_opencrab_pack_fixtures.py::test_p0_f3_invalid_archive_fixtures_are_deterministic_and_minimally_isolated \
  -q --no-cov
```

- exit code: `1`
- progress line: `FF`
- key result (observed): both tests failed only because
  `tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives` was missing:

```text
AssertionError: Required P0-F3 invalid archive fixture directory is missing:
/Users/sjlee/Projects/OpenOyster/tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives
assert False
 +  where False = is_dir()
```

- short summary: both named tests FAILED with that same directory-missing assertion.
- No archive files or `expectations.json` existed at this RED.

### GREEN A — eight archives + expectations

- fixture creation (owned path only): deterministic ZIP builders under
  `tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/` plus
  `expectations.json` (limits + one primary issue per archive).
- command:

```text
PATH="$PWD/.venv/bin:$PATH" pytest \
  tests/test_opencrab_pack_fixtures.py::test_p0_f3_invalid_archive_preflight_rejects_each_threat_without_writing_pack_store \
  tests/test_opencrab_pack_fixtures.py::test_p0_f3_invalid_archive_fixtures_are_deterministic_and_minimally_isolated \
  -q --no-cov
```

- exit code: `0`
- progress line: `..`
- isolation precheck after create (implement session): each archive returned only its primary issue code; compression-ratio member `zeros.bin` had `file_size=50000`, `compress_size=65`.

### RED B — broken provenance fixtures missing

- command:

```text
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py \
  -k 'p0_f3_broken_provenance or p0_f3_strict_provenance' -q --no-cov
```

- exit code: `1`
- progress line: `FFF.F` (4 failed, 1 passed)
- key result (observed): four fixture-root failures because
  `p0-f3-broken-provenance` (and the three pack subdirs) were missing:

```text
AssertionError: Required P0-F3 broken-provenance fixture is missing:
.../p0-f3-broken-provenance/missing-evidence-ref
.../p0-f3-broken-provenance/missing-artifact
.../p0-f3-broken-provenance/artifact-hash-mismatch
AssertionError: Required P0-F3 broken-provenance fixture directory is missing:
.../p0-f3-broken-provenance
```

- the one pass was `test_p0_f3_strict_provenance_oracle_rejects_unsafe_evidence_paths_below_pack_root`, which builds an ephemeral pack under `tmp_path` and does not require the committed fixture tree.

### GREEN B — three packs + expectations

- fixture creation (owned path only):
  `missing-evidence-ref/`, `missing-artifact/`, `artifact-hash-mismatch/`,
  and root `expectations.json`.
- implement-session create verification:

```text
=== missing-evidence-ref ===
compatible fail fail {'missing_evidence_ref'}
=== missing-artifact ===
compatible pass strict fail ['missing_artifact']
=== artifact-hash-mismatch ===
compatible pass strict fail ['artifact_hash_mismatch']
actual digest 0fc02a6ca3cf9183eed8d5c9a90c02146ad0624f8f043add727299e151624867
```

- command:

```text
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -k 'p0_f3' -q --no-cov
```

- exit code: `0`
- progress line: `.......`

### REFACTOR confirmation

- shared test-only helpers already in `tests/test_opencrab_pack_fixtures.py`:
  `_reference_archive_preflight`, `_reference_strict_provenance_oracle`,
  path/symlink/ratio helpers, and report builders. No production code.
- continuation command:

```text
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q --no-cov
```

- exit code: `0`
- progress line: `............` (12 tests collected/passed)

Also re-run with coverage default in the continuation session:

```text
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q
```

- exit code: `0`
- progress line: `............`

### Security / write-boundary evidence (continuation, post-GREEN)

Static oracle source scan of `_reference_archive_preflight`:

- no executable calls matching `.extract(`, `.extractall(`, or `zipfile.extract*`
- comment strings may mention `extract`/`extractall` (documentation of the ban)
- mode constant: `metadata_and_symlink_payload_only`
- report field `production_admission: False`

Runtime preflight over all eight archives inside a temporary directory:

- temporary Pack store remained empty (`list(pack_store.iterdir()) == []`)
- outside sentinel path remained nonexistent
- archive SHA-256 digests unchanged before/after inspection

Isolated primary issues and digests (observed, unchanged after inspection):

```text
34d38319d63a16b11f6ad18b8b9b63d3a939fa7ea41486eadf61ea1e80928278  path-traversal.zip  issues=['path_traversal']
4c22fd0f3967197c873a2a90e333bf04d327f7a8f8b184233b72941628757808  absolute-path.zip  issues=['absolute_path']
e5414b1d2608dc394e5323999e8d6463b3d6ff8a0c251c6a0943a815ce7674d4  symlink-escape.zip  issues=['symlink_escape']
2cc5317967bad8e0223f772dfd540c53a17fbe175ed66051835e1ed1a10ddc7e  duplicate-path.zip  issues=['duplicate_path']
2ec661b6148735ce60f17b52793e0ac36f20b69d062925973b4bfb0b55bdbc92  case-collision.zip  issues=['case_collision']
00435a9a5131a54dda13c169a1e8957fa90cee2c1aab29e28cc8e13503fe8403  compression-ratio-limit.zip  issues=['compression_ratio_limit']
0eea8b0481de92f19e60cab934191b217b91a264a206df8f8f2c80c85cf22874  file-count-limit.zip  issues=['file_count_limit']
688869ef85921e3a3377be29f51ed803bc9060a8cd107df27aa5e9d98802ec61  uncompressed-bytes-limit.zip  issues=['uncompressed_bytes_limit']
```

Member isolation snapshot:

```text
path-traversal.zip           1 member  ../../evil.txt
absolute-path.zip            1 member  /tmp/evil.txt
symlink-escape.zip           1 member  escape.link (Unix symlink attr 0o120777, target escapes)
duplicate-path.zip           2 members both named dup.txt
case-collision.zip           Note.txt + note.txt
compression-ratio-limit.zip  zeros.bin 50000/65
file-count-limit.zip         33 members (limit 32)
uncompressed-bytes-limit.zip big.bin 76000 bytes (limit 65536)
```

**Limitation named in tests:** this is Phase 0 reference preflight only. It does **not** implement production archive admission, quarantine, installer, or content-store extraction under `src/openoyster/**`.

### Broken provenance verification (continuation)

Official compatible validator (`_official_validate_pack_static(..., write_report=False)`) vs strict oracle:

| Pack | Official status | Official issue codes | Strict status | Strict issue codes | Tree digests after both |
| --- | --- | --- | --- | --- | --- |
| missing-evidence-ref | fail | `missing_evidence_ref` (node+edge) | fail | `missing_artifact` (strict looks at artifact paths; does not replace official evidence_refs check) | unchanged |
| missing-artifact | pass | (none) | fail | `missing_artifact` | unchanged |
| artifact-hash-mismatch | pass | (none) | fail | `artifact_hash_mismatch` | unchanged |

Official sample for missing-evidence-ref:

```text
checks.evidence_refs = fail
issues: Node/Edge evidence_ref does not resolve: evidence:missing
```

Unsafe path oracle test uses `tmp_path` only and rejects path escape without reading outside the pack root.

### Predecessor digest locks (continuation, post-GREEN)

P0-F1 (`tests/fixtures/opencrab_pack_runtime/p0-f1-minimal`) — **PASS**, 4 files:

```text
fd1391ceb27a6b705b74ad779d1df4e0b837af34e90547521b7e0adae8249bd7  evidence/index.jsonl
c147e139c50ea1f0f801f0b05b1d3bb5f55ef16114e049e9158abc5f050f9533  graph/edges.jsonl
9a147ad0dcbda678d756a45d64c4b0cbf94ab5b7827f82e55678ca76bcb84e44  graph/nodes.jsonl
ae3f3d5c71774cac4ae28b79a333f31503e5b847396f6e93562f61f0cff2614a  manifest.json
```

P0-F2 (`tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout`) — **PASS**, 11 files; matches brief lock and `P0_F2_EXPECTED_DIGESTS` in the test module:

```text
a40058456034619ba0b358128ac1a48af2a502e5ccacef8455ff5b31b38aa3bb  README.md
c0e2961a20e027539b9744105a58d41514f107f531e950906c06d5b10c73d5ff  community_reports.json
fd1391ceb27a6b705b74ad779d1df4e0b837af34e90547521b7e0adae8249bd7  evidence/index.jsonl
c147e139c50ea1f0f801f0b05b1d3bb5f55ef16114e049e9158abc5f050f9533  graph/edges.jsonl
9a147ad0dcbda678d756a45d64c4b0cbf94ab5b7827f82e55678ca76bcb84e44  graph/nodes.jsonl
4b37c9eab24e5ac3f709aeccb200a7e897727846100bbfc457026a201bcd7647  manifest.json
1819a5164ca9f7e8d17f2e69e000141136ddb26254767517c37158c72d59e495  neo4j/export_status.json
390ef78b9e5f7db3f4c2411b537113009ca6227937aa990404985d70a9fcdd81  neo4j/import.cypher
7c95fe94a6eab5f67aab99910db2cd15dab4c327357d4a31cbdd0fdf867eb7f3  neo4j/opencrab_ingest.jsonl
f95ceeb4a5e2efa2edc23fd82468128407e07d3c920be40643931b6c14eb6599  quality/report.json
0495fa6862e62316dde55ca63a918cf33634cf36420687981f173263b2ee506d  sample_queries.json
```

Predecessor fixture paths were not modified by this unit.

### make check (continuation, writer-observed)

- command: `PATH="$PWD/.venv/bin:$PATH" make check`
- exit code: `2`
- phases:

  - `ruff check src tests scripts` → pass
  - `mypy src/openoyster` → pass (`Success: no issues found in 60 source files`)
  - `pytest` → **2 failed, 85 passed, 1 warning**

- failing tests (environment-specific Rich ANSI, not P0-F3 owned paths):

  - `tests/test_cli_lifecycle.py::test_cli_local_lifecycle` — expected plain
    `"Copied 1 file"` but output contained bold ANSI
    (`Copied \x1b[1m1\x1b[0m \x1b[1mfile\x1b[0m...`)
  - `tests/test_goldset_cli.py::test_eval_gold_cli_smoke_with_stub` — same ANSI
    mismatch for `"Gold documents evaluated: 2"`

- pack fixture tests remained green inside that suite (12/12 for
  `test_opencrab_pack_fixtures.py`).
- **interpretation:** exit 2 is attributed to interactive/TTY Rich ANSI styling in
  CLI output. Recorded as environment-specific; not claimed as a repository defect
  introduced by P0-F3, and not fixed here (forbidden / out of unit scope).

### git diff --check

- command: `git diff --check`
- exit code: `0`

### Changed paths (P0-F3 owned only; unstaged)

```text
docs/delegation/P0-F3_INVALID_INPUT_FIXTURE_BRIEF.md
tests/test_opencrab_pack_fixtures.py
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/expectations.json
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/path-traversal.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/absolute-path.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/symlink-escape.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/duplicate-path.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/case-collision.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/compression-ratio-limit.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/file-count-limit.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/uncompressed-bytes-limit.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/expectations.json
tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/missing-evidence-ref/**
tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/missing-artifact/**
tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/artifact-hash-mismatch/**
```

No `src/openoyster/**`, predecessor fixtures, `pyproject.toml`, `Makefile`,
`README.md`, `.gitignore`, or other forbidden paths were edited by this unit.
No git stage/commit/push/deploy.

### Residual risks (post-implementation; see remediation for updates)

1. **Not production admission.** Reference oracles are test-only. Phase 1 must
   still implement safe extraction, quarantine, registry, and content store.
2. **Symlink payload read is intentional and bounded.** Oracle may `ZipFile.read`
   symlink member bytes for target inspection only; it still must never extract to
   disk. Production extractors must treat symlink members as a trust boundary.
3. **Official vs strict contract gap is deliberate.** Compatible validator passes
   missing-artifact and hash-mismatch packs; only the strict oracle fails them.
   Callers must not treat official `status=pass` as full provenance integrity.
4. **missing-evidence-ref strict codes are not the official failure mode.** Strict
   oracle currently surfaces artifact-path issues; official sibling validator owns
   `missing_evidence_ref`. Tests already distinguish these surfaces by name.
5. **TTY ANSI `make check` exit 2** is environment-specific; Root should recheck
   non-interactively before treating full-suite green as acceptance.
6. **Effort telemetry vs model_lock (implementation):** initial sessions recorded
   `high`, contract asked for `xhigh`. Addressed by a dedicated xhigh remediation
   pass below; the original high run is not re-labeled.
7. **No skip/xfail/importorskip/network/real Neo4j/secrets** were introduced in
   owned tests.

---

## xhigh remediation / adversarial audit (actual `grok-4.5` / `xhigh`)

**UTC time window (writer-observed):** `2026-07-13T13:11Z` gate re-run.

**Session evidence (this remediation):**

| Field | Observed |
| --- | --- |
| writer | Grok Build agent (`GROK_AGENT=1`) |
| model | `grok-4.5` (unit lock; Grok 4.5 remediation writer) |
| reasoning_effort | **`xhigh`** via native `--reasoning-effort` (user-directed re-own after proven high downgrade) |
| profile | remediation / adversarial audit of P0-F3 owned paths only |
| implementation session IDs reused? | no — new remediation pass over existing unstaged work |
| fabricated RED? | no — implementation RED A/B left as historical evidence from implement logs only |

### Adversarial findings (pre-fix)

Ephemeral probes against the high-authored oracle (committed fixtures untouched)
found real bypasses that the eight isolated fixtures did not cover:

| Finding | Severity | Evidence |
| --- | --- | --- |
| Casefold identity used raw member names | **fix** | `dir\\Note.txt` + `dir/note.txt` → `status=pass` (expected `case_collision`) |
| Duplicate identity used raw member names | **fix** | `foo//bar` + `foo/bar` → `status=pass` (expected `duplicate_path`) |
| Declared empty / whitespace evidence `path` skipped | **fix** | `""` / `"   "` → no issue codes (strict should reject unsafe/empty declaration) |
| Whitespace-prefixed traversal not stripped before safety | **fix** | `" ../secret.txt"` classified as `missing_artifact` rather than `unsafe_evidence_path` |
| Slash-normalized traversal / drive / UNC / dir threats | OK | backslash `..\\`, `C:\\`, `\\\\server\\share`, `../../evil/` already failed correctly |
| Zero `compress_size` with positive `file_size` | OK | `_compression_ratio` → `inf` (> limit) |
| Unix symlink mode escape | OK | `S_IFLNK` + escaping target → `symlink_escape` |
| Non-symlink file with link-like payload | residual | intentionally not `symlink_escape` without symlink mode |
| Committed eight archives isolation + digest stability | OK | each primary-only; digests unchanged after preflight |
| Predecessor F1/F2 digest locks | OK | match `P0_F1_EXPECTED_DIGESTS` / `P0_F2_EXPECTED_DIGESTS` |
| Preflight never calls extract | OK | static scan: no `.extract(` / `.extractall(`; mode `metadata_and_symlink_payload_only`; `production_admission: False` |
| Write-boundary assertions | limited but truthful | pack-store emptiness proves Phase 0 preflight does not write store paths it is never given; tests name Phase 0 limitation and do not claim production admission |

### Fixes applied (owned paths only)

File: `tests/test_opencrab_pack_fixtures.py` (fixtures binary digests unchanged).

1. Added `_normalize_zip_member_key` — slash-normalize, drop empty/`.` segments, preserve absolute/drive markers; used for **duplicate** and **casefold** identity.
2. Absolute-path helper strips and still treats POSIX `/`, UNC `//…`, and Windows drive `C:…` as absolute.
3. Unsafe relative paths strip first; empty/blank after strip is unsafe.
4. `_evidence_artifact_path` returns declared `path` (including empty after strip) when the field is present so blank declarations are not silent skips.
5. `_resolve_under_root` joins only the validated relative form under resolved root and re-checks containment after `resolve()`.
6. Zero-compress ratio comment documented; behavior already infinite-ratio.
7. New ephemeral regression:
   `test_p0_f3_reference_archive_preflight_blocks_slash_normalization_and_ratio_bypasses`
8. Expanded
   `test_p0_f3_strict_provenance_oracle_rejects_unsafe_evidence_paths_below_pack_root`
   for absolute/drive/UNC/whitespace/empty paths without outside reads.

**Not changed:** eight ZIP binaries, three broken-provenance pack trees,
`expectations.json` contents (still valid), predecessor fixtures, `src/**`,
Makefile/pyproject/README.

### Post-fix verification (observed this session)

Archive digests + isolation (unchanged bytes; preflight read-only):

```text
34d38319d63a16b11f6ad18b8b9b63d3a939fa7ea41486eadf61ea1e80928278  path-traversal.zip  issues=['path_traversal']
4c22fd0f3967197c873a2a90e333bf04d327f7a8f8b184233b72941628757808  absolute-path.zip  issues=['absolute_path']
e5414b1d2608dc394e5323999e8d6463b3d6ff8a0c251c6a0943a815ce7674d4  symlink-escape.zip  issues=['symlink_escape']
2cc5317967bad8e0223f772dfd540c53a17fbe175ed66051835e1ed1a10ddc7e  duplicate-path.zip  issues=['duplicate_path']
2ec661b6148735ce60f17b52793e0ac36f20b69d062925973b4bfb0b55bdbc92  case-collision.zip  issues=['case_collision']
00435a9a5131a54dda13c169a1e8957fa90cee2c1aab29e28cc8e13503fe8403  compression-ratio-limit.zip  issues=['compression_ratio_limit']
0eea8b0481de92f19e60cab934191b217b91a264a206df8f8f2c80c85cf22874  file-count-limit.zip  issues=['file_count_limit']
688869ef85921e3a3377be29f51ed803bc9060a8cd107df27aa5e9d98802ec61  uncompressed-bytes-limit.zip  issues=['uncompressed_bytes_limit']
```

Bypass probes after fix: case mixed seps, dup slash collapse, backslash traversal,
UNC, drive, zero-compress ratio, empty/blank/whitespace evidence paths — all OK.

### Commands and exit codes (this remediation)

```text
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q --no-cov
```

- exit code: `0`
- progress: `.............` (**13** tests; was 12 before the new adversarial regression)

```text
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q
```

- exit code: `0`
- progress: `.............`

```text
PATH="$PWD/.venv/bin:$PATH" make check
```

- exit code: `2`
- `ruff check src tests scripts` → pass
- `mypy src/openoyster` → pass (`Success: no issues found in 60 source files`)
- `pytest` → **2 failed, 86 passed, 1 warning**
- failures (environment-specific Rich ANSI only; **not** P0-F3 owned paths):
  - `tests/test_cli_lifecycle.py::test_cli_local_lifecycle` — expected plain
    `"Copied 1 file"`; got bold ANSI (`Copied \x1b[1m1\x1b[0m \x1b[1mfile\x1b[0m...`)
  - `tests/test_goldset_cli.py::test_eval_gold_cli_smoke_with_stub` — expected plain
    `"Gold documents evaluated: 2"`; got ANSI-wrapped count
- pack fixture module remained green inside the suite (13/13).
- **interpretation:** same known interactive/TTY ANSI issue as continuation; not a
  repository defect introduced by P0-F3; not fixed here (forbidden / out of scope).

```text
git diff --check
```

- exit code: `0`

Predecessor digest locks rechecked this session: F1 PASS (4 files), F2 PASS (11 files).

### Changed paths after remediation (P0-F3 owned; unstaged; no commit)

```text
docs/delegation/P0-F3_INVALID_INPUT_FIXTURE_BRIEF.md
tests/test_opencrab_pack_fixtures.py
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/expectations.json
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/path-traversal.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/absolute-path.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/symlink-escape.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/duplicate-path.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/case-collision.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/compression-ratio-limit.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/file-count-limit.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/uncompressed-bytes-limit.zip
tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/expectations.json
tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/missing-evidence-ref/**
tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/missing-artifact/**
tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/artifact-hash-mismatch/**
```

**Remediation code delta is only:**
`tests/test_opencrab_pack_fixtures.py` + this brief.
Fixture binaries and pack trees were not rewritten in the xhigh pass.

No `src/openoyster/**`, predecessor fixtures, `pyproject.toml`, `Makefile`,
`README.md`, `.gitignore`, or other forbidden paths were edited.
No git stage/commit/push/deploy.

### Residual risks (post-remediation)

1. **Still not production admission.** Oracles remain test-only Phase 0 preflight /
   strict provenance helpers. Phase 1 must implement safe extract, quarantine,
   registry, and content store under `src/openoyster/**`.
2. **Write-boundary proof is Phase 0 limited.** Empty temp pack-store shows the
   reference preflight does not write store paths as a side effect of metadata
   inspection; it is not proof of a production installer reject path.
3. **Symlink detection is Unix-mode based.** Members without `S_IFLNK` external_attr
   are not treated as symlinks even if payload looks like a path (intentional for
   this oracle; production may need broader symlink signals).
4. **Official vs strict gap remains deliberate** for missing-artifact and
   hash-mismatch; missing-evidence-ref official fail vs strict artifact-path fail
   still differs by design.
5. **TTY ANSI `make check` exit 2** remains environment-specific.
6. **Process history:** initial implementation was high, not xhigh. This pass is
   the actual xhigh remediation; both facts must stay in the record.
7. **No skip/xfail/importorskip/network/real Neo4j/secrets** in owned tests.

### Final acceptance status

Writer implementation + xhigh remediation evidence is recorded above.
**Final acceptance still requires Opus review and Root gates**; this brief does
not claim unit acceptance and does not claim production Pack admission is
implemented.

---

## Opus NEEDS_REWORK remediation (actual `grok-4.5` / native `--reasoning-effort xhigh`)

**UTC time window (writer-observed):** `2026-07-13T13:22Z`–`2026-07-13T13:25Z`.

**Session route (actual, not re-labeled):**

| Field | Observed |
| --- | --- |
| writer | Grok Build agent (`GROK_AGENT=1`) — sole implementer for this rework |
| model | `grok-4.5` (unit lock; remediation writer after Claude Opus 4.8 `NEEDS_REWORK`) |
| reasoning_effort | **`xhigh`** via native `--reasoning-effort` (this pass; user-directed rework) |
| profile | implement / remediation of Opus M1, m2, m3, n7 only |
| prior xhigh remediation | kept as historical record above; this section supersedes residual risks 4–partial for isolation + hash/NFC |
| fabricated RED? | no — new regression tests were run and observed RED before oracle/fixture fixes |
| production safety claim? | no — still Phase 0 test-only oracles |

### Opus findings addressed

| ID | Finding | Severity | Pre-fix evidence (observed RED) |
| --- | --- | --- | --- |
| **M1** | OpenCrab-legal URL-only `source={url, path:null}` classified `unsafe_evidence_path` | fix | `test_p0_f3_strict_provenance_accepts_url_only_null_path_evidence` → `status=fail`, codes=`['unsafe_evidence_path']` |
| **m2** | Present non-sha256 hash (e.g. `deadbeef`) silently ignored | fix | `test_p0_f3_strict_provenance_rejects_malformed_hash_on_existing_artifact` → `status=pass` (expected `malformed_hash`) |
| **m3** | Committed `missing-evidence-ref` also failed strict artifact provenance; expectations said `strict_oracle_primary_issue: null` | fix | official OK with `missing_evidence_ref`, but strict → `missing_artifact` on `source.md` |
| **n7** | NFC/NFD equivalent ZIP members not identity-folded | fix | ephemeral NFC+NFD `café.txt` → `status=pass` (expected `case_collision`) |

### RED (this rework; observed before implementation)

```text
PATH="$PWD/.venv/bin:$PATH" pytest \
  tests/test_opencrab_pack_fixtures.py::test_p0_f3_strict_provenance_accepts_url_only_null_path_evidence \
  tests/test_opencrab_pack_fixtures.py::test_p0_f3_strict_provenance_rejects_malformed_hash_on_existing_artifact \
  tests/test_opencrab_pack_fixtures.py::test_p0_f3_broken_provenance_missing_evidence_ref_fails_official_compatible_validator \
  tests/test_opencrab_pack_fixtures.py::test_p0_f3_reference_archive_preflight_blocks_slash_normalization_and_ratio_bypasses \
  -q --no-cov --tb=line
```

- exit code: `1`
- progress: `FFFF`
- failures:
  1. URL-only → `assert 'fail' == 'pass'` (`unsafe_evidence_path` on `''`)
  2. `deadbeef` → `assert 'pass' == 'fail'` (no `malformed_hash`)
  3. missing-evidence-ref → strict `missing_artifact` while expectations require isolation
  4. NFC/NFD ephemeral → `assert 'pass' == 'fail'` (no `case_collision`)

### Fixes applied (P0-F3 owned paths only)

1. **`_evidence_local_artifact_spec`** (replaces path-null→empty collapse):
   - `path: null` + nonblank `url` → `url_only` (no local resolution; not unsafe)
   - `path: null` without usable URL → explicit `unsafe_evidence_path`
   - blank / whitespace string path → explicit `unsafe_evidence_path`
   - nonblank path → local resolve as before
2. **Hash declaration**: if hash field is present and nonblank but not valid sha256 → `malformed_hash`; valid sha256 mismatch still → `artifact_hash_mismatch`.
   An absent hash remains optional in this Phase 0 oracle and does not create an
   issue; Phase 1 profile policy may make it required.
3. **`missing-evidence-ref/evidence/index.jsonl`**: evidence row changed to truthful URL-only
   `source={url:https://example.invalid/source, path:null, title:Source}` so official
   `missing_evidence_ref` is isolated; strict status `pass`.
4. **`expectations.json` notes** updated to document URL-only isolation;
   `strict_oracle_primary_issue` remains `null`; test asserts metadata agreement.
5. **ZIP identity**: slash-normalize then Unicode **NFC** before duplicate/casefold checks.
   Distinct pre-NFC spellings that collapse under NFC emit **`case_collision`**
   (true identical slash keys still emit `duplicate_path`). Ephemeral NFC/NFD case added.

**Archive ZIP binaries:** not rewritten (digests unchanged from prior remediation record).

### GREEN (after fix)

```text
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q --no-cov
```

- exit code: `0`
- progress: `...............` (**15** tests)

### REFACTOR

- Preflight uses `_normalize_zip_member_key` (NFC wrapper) + `_slash_normalize_zip_member_key`
  for pre-NFC spelling tracking; no behavior change after refactor.
- Re-GREEN: pack fixture module `15/15` pass (`EXIT:0`).

### Post-fix regression matrix (observed)

```text
M1 url-only          status=pass issue_codes=[]
null path no url     unsafe_evidence_path (explicit)
m2 deadbeef          ['malformed_hash']
valid mismatch       ['artifact_hash_mismatch']
n7 NFC/NFD           ['case_collision']
m3 mer strict        pass
m3 mer official      ['missing_evidence_ref', 'missing_evidence_ref']
```

### Gates (this rework)

```text
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q --no-cov
```

- exit code: `0` (15 passed)

```text
PATH="$PWD/.venv/bin:$PATH" make check
```

- exit code: `2`
- `ruff check src tests scripts` → pass
- `mypy src/openoyster` → pass (`Success: no issues found in 60 source files`)
- `pytest` → **2 failed, 88 passed, 1 warning**
- failures (environment-specific Rich ANSI only; **not** P0-F3 owned paths):
  - `tests/test_cli_lifecycle.py::test_cli_local_lifecycle`
  - `tests/test_goldset_cli.py::test_eval_gold_cli_smoke_with_stub`
- pack fixture module green inside suite (15/15).
- **interpretation:** writer TTY ANSI is environment-only; not claimed as a repository
  defect introduced by P0-F3; not fixed here (forbidden / out of unit scope).

```text
git diff --check
```

- exit code: `0`

Predecessor + P0-F3 digest checks (this session):

| Check | Result |
| --- | --- |
| P0-F1 digest lock (4 files) | PASS |
| P0-F2 digest lock (11 files) | PASS |
| 8 invalid-archive SHA-256 vs prior remediation record | PASS (unchanged) |
| each archive primary issue isolation | PASS |
| missing-evidence-ref / missing-artifact / artifact-hash-mismatch byte-identical after validation | PASS |

### Changed paths (this rework; P0-F3 owned only; no commit)

```text
docs/delegation/P0-F3_INVALID_INPUT_FIXTURE_BRIEF.md
tests/test_opencrab_pack_fixtures.py
tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/expectations.json
tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/missing-evidence-ref/evidence/index.jsonl
```

**Not changed this rework:** eight ZIP binaries under `p0-f3-invalid-archives/`,
`missing-artifact/**`, `artifact-hash-mismatch/**`, `src/openoyster/**`,
P0-F1/P0-F2 fixtures, `pyproject.toml`, `Makefile`, `README.md`, `.gitignore`.

No git stage/commit/push/deploy.

### Residual risks (post-Opus rework)

1. **Still not production admission.** Oracles remain test-only Phase 0 preflight /
   strict provenance helpers. Phase 1 must implement safe extract, quarantine,
   registry, and content store under `src/openoyster/**`.
2. **Write-boundary proof is Phase 0 limited.** Empty temp pack-store proves the
   reference preflight does not write store paths as a side effect of metadata
   inspection; not a production installer reject path.
3. **Symlink detection remains Unix-mode based** (`S_IFLNK` external_attr).
4. **Official vs strict gap remains deliberate** for missing-artifact and
   hash-mismatch. **missing-evidence-ref is now isolated:** official fails only on
   `missing_evidence_ref`; strict passes (URL-only evidence row).
5. **TTY ANSI `make check` exit 2** remains environment-specific.
6. **Unicode identity policy:** NFC-equivalent spellings surface as `case_collision`;
   byte-identical slash keys after slash-norm surface as `duplicate_path`. Production
   extractors may need broader normalization (e.g. platform FS case folding).
7. **No skip/xfail/importorskip/network/real Neo4j/secrets** in owned tests.

### Final acceptance status

Writer rework for Opus `NEEDS_REWORK` (M1/m2/m3/n7) is recorded with actual
RED→GREEN evidence under `grok-4.5` / native `--reasoning-effort xhigh`.
At the writer stage, Opus re-review and Root gates were still pending; both are
completed in the acceptance below. This brief does not claim production Pack
admission is implemented.

## Root final acceptance

```text
acceptance_id: 2026-07-13-p0-f3-invalid-inputs
root_orchestrator: Codex /root
scope:
  - tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/**
  - tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/**
  - tests/test_opencrab_pack_fixtures.py
  - docs/delegation/P0-F3_INVALID_INPUT_FIXTURE_BRIEF.md
base_commit: 4c6d0ea055ca5a8a7327564688732d1ddb12e50d
insert_order:
  - Root P0-F3 threat-fixture contract
  - Grok high archive RED/GREEN and provenance RED/GREEN
  - native Grok xhigh adversarial remediation
  - Claude Opus NEEDS_REWORK review
  - native Grok xhigh M1/m2/m3/n7 RED/GREEN remediation
  - Claude Opus MERGE_OK re-review
  - Root independent final gates
writer_history:
  - Root, orchestrator, brief and acceptance record
  - Grok, initial writer, grok-4.5/high, wrapper requested xhigh but telemetry downgraded
  - Grok, remediation writer, grok-4.5/xhigh, native reasoning-effort verified
  - Claude Opus, read-only reviewer, claude-opus-4-8/high, NEEDS_REWORK then MERGE_OK
  - Terra, not_used_by_user_direction
changed_paths:
  - tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/**
  - tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/**
  - tests/test_opencrab_pack_fixtures.py
  - docs/delegation/P0-F3_INVALID_INPUT_FIXTURE_BRIEF.md
forbidden_paths: PASS; no src, config, P0-F1, or P0-F2 changes from this unit
tdd_evidence:
  - archive fixture-missing RED then GREEN
  - broken-provenance fixture-missing RED then GREEN
  - xhigh bypass regressions and Opus M1/m2/m3/n7 RED then GREEN
gate_results:
  preflight: PASS
  scope: PASS
  unit: PASS; 15 passed
  make_check: PASS; 90 passed, 1 dependency deprecation warning, build pass
  source_pack_digest: PASS; invalid archive 9 files and broken provenance 14 files unchanged
  predecessor_digest: PASS; P0-F1 4 files and P0-F2 11 files unchanged
  security: PASS; eight primary issues isolated, no extraction/member write, Phase 0 boundary explicit
  documentation: PASS
  opus_review: PASS
opus_status: MERGE_OK
critical_count: 0
major_count: 0
finding_resolutions:
  - ROUTE-001: wrapper xhigh request actually ran high; native reasoning-effort
    xhigh sessions re-owned and remediated the implementation. Both routes are recorded.
  - OPUS-M1: legal URL-only path:null evidence now strict-passes; null without URL
    and blank local paths remain explicit unsafe failures. Opus recheck PASS.
  - OPUS-m2: malformed declared hash emits malformed_hash; valid mismatch remains
    artifact_hash_mismatch; absent hash is documented as optional in Phase 0.
  - OPUS-m3: missing-evidence-ref fixture now isolates official missing_evidence_ref
    with strict local provenance PASS.
  - OPUS-n7: NFC/NFD equivalent paths emit case_collision while true duplicates
    retain duplicate_path.
  - PROSE-001: generated Grok prose mentioned a nonexistent src/xhigh path;
    filesystem and scope checks proved it was never created or modified.
commit_push_deploy: not_performed
final_decision: ACCEPTED
root_signature: Codex /root, 2026-07-13T22:33:04+0900
```

P0-F3 accepts only the fixture and test-oracle contract. It does not assert that
production archive extraction, quarantine, installation, or activation exists.
Delegated CLI/helper stop-hook warnings were rechecked against the filesystem and
did not change the acceptance criteria or project files.
