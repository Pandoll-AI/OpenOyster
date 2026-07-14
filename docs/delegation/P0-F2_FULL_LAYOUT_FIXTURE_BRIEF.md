# P0-F2 Full-layout OpenCrab Pack Fixture 위임 브리프

## 위임 계약

```yaml
unit_id: P0-F2-full-layout
objective: >-
  OpenCrab Pack v1 문서가 required로 정의한 11개 파일을 모두 포함하는 작고
  의미 있는 full-layout fixture를 만들고, documented strict layout과 현재
  compatible validator의 차이를 테스트 이름과 검증으로 고정한다.
writer: Grok Build CLI
model_lock: grok-4.5/xhigh
profile: implement
owned_paths:
  - tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/**
  - tests/test_opencrab_pack_fixtures.py
  - docs/delegation/P0-F2_FULL_LAYOUT_FIXTURE_BRIEF.md
forbidden_paths:
  - src/openoyster/**
  - tests/fixtures/opencrab_pack_runtime/p0-f1-minimal/**
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
  - the sibling ../OpenCrab repository or official validator is missing
  - the requested model or effort is substituted
  - RED is not observed before fixture implementation
```

Grok CLI의 실행 결과에 표시되는 model과 effort를 권위 있는 model-lock
증거로 사용한다. 이 작업에서는 Grok만 writer다.

## 권위 근거

- `../OpenCrab/docs/opencrab-pack-v1.md`
- `../OpenCrab/opencrab/pack/validation.py`
- `docs/OPENCRAB_PACK_RUNTIME_REQUIREMENTS.md` 3.1부터 3.3
- `docs/CODING_DELEGATION_CONTRACT.md` P0-F2

## Required full layout

fixture는 다음 11개 파일만 포함한다.

```text
manifest.json
graph/nodes.jsonl
graph/edges.jsonl
evidence/index.jsonl
quality/report.json
neo4j/import.cypher
neo4j/opencrab_ingest.jsonl
neo4j/export_status.json
README.md
sample_queries.json
community_reports.json
```

## 구현 요구사항

- test first다. 먼저 full-layout 테스트를 작성하고 fixture 부재로 인한 RED를
  실제 실행해 기록한다.
- 테스트 이름은 `documented_strict_full_layout`과
  `current_compatible_validator`의 의미 차이를 명시한다.
- fixture 파일 집합은 위 11개와 정확히 같아야 한다. optional 파일을 추가하지
  않는다.
- graph와 evidence는 P0-F1과 같은 canonical grammar를 사용해도 되지만,
  full-layout의 Neo4j snapshot, counts, quality report, manifest metadata와 서로
  일관돼야 한다.
- `manifest.json`은 문서의 metadata, counts, limits, quality, retrieval hints,
  hashes, artifacts 구조를 실제 값으로 채운다. placeholder `...`는 금지한다.
- `quality/report.json`은 원본 producer report로 취급한다. 테스트는 검증 전후
  전체 fixture의 상대 경로와 SHA-256 digest가 같음을 증명한다.
- `neo4j/opencrab_ingest.jsonl`은 `kind`가 node, edge, evidence인 행을 포함하고
  canonical graph/evidence와 id 및 count가 일관돼야 한다.
- `neo4j/export_status.json`, sample queries, community reports, README,
  import Cypher는 빈 장식 파일이 아니라 서로 참조 가능한 최소 의미를 가진다.
- 현재 공식 validator는 기존 격리 subprocess helper를 통해
  `write_report=False`로만 호출한다.
- 테스트는 원본 `quality/report.json`이 덮어쓰이지 않았음을 명시적으로
  확인한다.
- skip, xfail, importorskip, network, Neo4j service 의존은 금지한다.
- P0-F1 전체 테스트를 함께 재실행한다.

## P0-F1 불변 기준

```text
fd1391ceb27a6b705b74ad779d1df4e0b837af34e90547521b7e0adae8249bd7  evidence/index.jsonl
c147e139c50ea1f0f801f0b05b1d3bb5f55ef16114e049e9158abc5f050f9533  graph/edges.jsonl
9a147ad0dcbda678d756a45d64c4b0cbf94ab5b7827f82e55678ca76bcb84e44  graph/nodes.jsonl
ae3f3d5c71774cac4ae28b79a333f31503e5b847396f6e93562f61f0cff2614a  manifest.json
```

## 필수 실행 증거

```text
PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q
PATH="$PWD/.venv/bin:$PATH" make check
git diff --check
```

브리프 끝에 RED, GREEN, REFACTOR, 전체 게이트의 명령·exit code·핵심 결과와
실제 변경 경로를 기록한다.

## 실행 증거

Writer: Grok Build (sole implementer for this unit).
Model/effort/profile as executed in this session: **grok-4.5 / xhigh / implement**
(session identity; no alternate writer or model substitution).

### RED

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q --tb=short`
- exit code: `1`
- key result: P0-F1 tests passed; both new P0-F2 tests failed only because
  `tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout` was missing
  (`AssertionError: Required P0-F2 full-layout fixture is missing`).
  No fixture files existed yet.

### GREEN

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q --no-cov`
- exit code: `0`
- key result: `.....` (5 passed). Documented strict layout asserted exact 11
  relative paths, cross-file ids/counts, producer quality report semantics, and
  Neo4j ingest kind=node|edge|evidence consistency. Compatible validator accepted
  the full-layout pack with `write_report=False`, left
  `checks.neo4j_import == "skip"`, and preserved all relative paths plus SHA-256
  digests including `quality/report.json`.

### REFACTOR

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q --no-cov`
- exit code: `0`
- key result: extracted shared `COMPATIBLE_VALIDATOR_PASS_CHECKS`; all 5 pack
  fixture tests remained green. No fixture content rewrite after GREEN.

### P0-F1 digest immutability

- command: SHA-256 of all files under
  `tests/fixtures/opencrab_pack_runtime/p0-f1-minimal`
- result: **PASS** — exact match to brief lock:

```text
fd1391ceb27a6b705b74ad779d1df4e0b837af34e90547521b7e0adae8249bd7  evidence/index.jsonl
c147e139c50ea1f0f801f0b05b1d3bb5f55ef16114e049e9158abc5f050f9533  graph/edges.jsonl
9a147ad0dcbda678d756a45d64c4b0cbf94ab5b7827f82e55678ca76bcb84e44  graph/nodes.jsonl
ae3f3d5c71774cac4ae28b79a333f31503e5b847396f6e93562f61f0cff2614a  manifest.json
```

Also asserted inside
`test_current_validator_accepts_minimal_four_file_pack_without_pack_v1_full_layout`.

### make check (writer-observed)

- command: `PATH="$PWD/.venv/bin:$PATH" make check`
- exit code: `2`
- phases observed by the Grok writer tool run:
  - `ruff check src tests scripts` → pass (exit 0)
  - `mypy src/openoyster` → pass (exit 0)
  - `pytest` → **2 failed, 78 passed, 1 warning**
- failing tests in that run:
  - `tests/test_cli_lifecycle.py::test_cli_local_lifecycle` — expected plain
    `"Copied 1 file"` but CLI output contained Rich ANSI bold sequences
    (`Copied \x1b[1m1\x1b[0m \x1b[1mfile\x1b[0m...`).
  - `tests/test_goldset_cli.py::test_eval_gold_cli_smoke_with_stub` — same ANSI
    styling mismatch for `"Gold documents evaluated: 2"`.
- pack fixture tests remained 5/5 pass in that same suite.
- **interpretation (corrected):** this exit-2 result is attributed to an
  **execution-environment difference**. The delegated interactive writer tool
  run emitted Rich ANSI styling in CLI test output. It is **not** claimed as a
  pre-existing repository defect, and it does **not** prove a codebase-level
  `make check` regression from P0-F2 fixture work.

### make check (Root orchestrator recheck — reported, not writer-observed)

- reporter: Root orchestrator (not re-run by this writer)
- command: `PATH="$PWD/.venv/bin:$PATH" make check` (non-interactive, same worktree)
- exit code: `0` (orchestrator-observed)
- key result reported by Root: Ruff pass, mypy pass, pytest **80 passed** with
  1 warning, package build pass. Root's non-TTY run did not emit the Rich ANSI
  styling that the writer's interactive tool run saw.

### Final acceptance status

Writer implementation evidence is recorded above. **Final acceptance still
requires Opus review and Root gates**; this brief correction does not claim unit
acceptance.

### git diff --check

- command: `git diff --check`
- exit code: `0`

### Changed paths (this unit)

```text
tests/test_opencrab_pack_fixtures.py
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/manifest.json
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/graph/nodes.jsonl
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/graph/edges.jsonl
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/evidence/index.jsonl
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/quality/report.json
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/neo4j/import.cypher
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/neo4j/opencrab_ingest.jsonl
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/neo4j/export_status.json
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/README.md
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/sample_queries.json
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/community_reports.json
docs/delegation/P0-F2_FULL_LAYOUT_FIXTURE_BRIEF.md
```

Forbidden paths not modified by this writer: `src/openoyster/**`,
`tests/fixtures/opencrab_pack_runtime/p0-f1-minimal/**` content,
`pyproject.toml`, `Makefile`, production config, git stage/commit/push/deploy.

### Residual risks

- Writer-tool `make check` can surface Rich ANSI CLI output under an interactive
  TTY-like environment; Root's non-interactive recheck is the authoritative
  orchestrator observation for full-suite status. At the writer stage, Opus and
  Root gates were still pending; both are completed in the acceptance below.
- Official compatible validator still does not enforce the documented 11-file
  layout or Neo4j import; only OpenOyster tests pin that stricter contract.

## Root final acceptance

```text
acceptance_id: 2026-07-13-p0-f2-full-layout
root_orchestrator: Codex /root
scope:
  - tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/**
  - tests/test_opencrab_pack_fixtures.py
  - docs/delegation/P0-F2_FULL_LAYOUT_FIXTURE_BRIEF.md
base_commit: 4c6d0ea055ca5a8a7327564688732d1ddb12e50d
insert_order:
  - Root P0-F2 contract brief
  - Grok RED test and 11-file fixture GREEN/REFACTOR
  - Root reproduction of full gates
  - Claude Opus adversarial review
  - Grok truthfulness remediation RED/GREEN/REFACTOR
  - Claude Opus remediation re-review
  - Root independent final gates
writer_history:
  - Root, orchestrator, brief and acceptance record
  - Grok, sole implementation writer, grok-4.5/xhigh/implement, PASS
  - Claude Opus, read-only reviewer, claude-opus-4-8/high, MERGE_OK
  - Terra, not_used_by_user_direction
changed_paths:
  - tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/**
  - tests/test_opencrab_pack_fixtures.py
  - docs/delegation/P0-F2_FULL_LAYOUT_FIXTURE_BRIEF.md
forbidden_paths: PASS; P0-F1 bytes and production/config paths unchanged
tdd_evidence:
  - initial fixture-missing RED, GREEN, REFACTOR in this brief
  - pack hash and Neo4j truthfulness remediation RED, GREEN, REFACTOR in this brief
gate_results:
  preflight: PASS
  scope: PASS
  unit: PASS; 5 passed
  make_check: PASS; 80 passed, 1 dependency deprecation warning, build pass
  source_pack_digest: PASS; P0-F2 exact 11 paths unchanged across validation
  predecessor_digest: PASS; P0-F1 exact 4 paths unchanged
  security: NOT_APPLICABLE; no archive extraction or production admission code
  documentation: PASS
  opus_review: PASS
opus_status: MERGE_OK
critical_count: 0
major_count: 0
minor_count: 0
finding_resolutions:
  - OPUS-F2-001: invented pack_sha256 replaced by a declared deterministic
    path/NUL/raw-bytes/newline digest over the 10 non-manifest required files;
    Opus independently reproduced the exact digest.
  - OPUS-F2-002: unsupported live Neo4j implication removed; quality reports
    skip, README states no live execution, and export status marks synthetic
    origin with live_neo4j_executed=false; Opus recheck PASS.
  - WRITER-TTY-001: Grok's interactive ANSI-sensitive failure was not treated
    as a repository defect; Root and Opus non-TTY make check both passed.
commit_push_deploy: not_performed
final_decision: ACCEPTED
root_signature: Codex /root, 2026-07-13T21:48:00+0900
```

The delegated CLI/helper stop-hook warnings did not change files or acceptance
criteria. Root repeated target tests, both fixture digest checks, the full gate,
and scope checks independently after the final Opus re-review.

## Review remediation (Opus MERGE_OK truthfulness elevations)

Root elevated two truthfulness findings after Opus `MERGE_OK` (no Critical/Major).
Sole writer: Grok. Owned paths only. P0-F1 bytes preserved.

### RED (tests first, fixture unchanged)

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q --no-cov --tb=line`
- exit code: `1`
- key result: `test_documented_strict_full_layout_requires_exact_eleven_files_with_cross_file_consistency`
  failed with `KeyError: 'pack_sha256_algorithm'` against the prior invented
  pack hash (no algorithm field). New assertions also require:
  - transparent fixture-local
    `pack_sha256_algorithm = sha256(path_nul_bytes_newline)`
  - `pack_sha256` equals SHA-256 over the 10 documented required files excluding
    `manifest.json`, POSIX path order, each as UTF-8 path + NUL + raw bytes + newline
  - producer `quality/report.json` `checks.neo4j_import == "skip"`
  - README states no live Neo4j import/export was executed and the snapshot is
    structurally consistent synthetic fixture data
  - `neo4j/export_status.json` carries `origin=fixture_synthetic` and
    `live_neo4j_executed=false`

### GREEN

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q --no-cov`
- exit code: `0`
- key result: `.....` (5 passed). Updated only P0-F2
  `manifest.json`, `quality/report.json`, `README.md`, and
  `neo4j/export_status.json` (plus recomputed `counts.bytes` /
  `hashes.pack_sha256`). Still exactly 11 fixture files.

### REFACTOR

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q --no-cov`
- exit code: `0`
- key result: README phrase checks use fully lowercased needles against
  `readme.lower()`; no further fixture rewrite. 5/5 still pass.

### P0-F1 digests after remediation

- result: **PASS** — unchanged lock digests (four files).

### make check after remediation (writer-observed)

- command: `PATH="$PWD/.venv/bin:$PATH" make check`
- exit code: `2`
- phases: ruff pass, mypy pass, pytest **2 failed, 78 passed, 1 warning**
- same two CLI tests showed Rich ANSI bold in this writer tool environment.
- **interpretation:** execution-environment / TTY styling difference for this
  interactive tool run; **not** described as a repository defect and **not**
  attributed to P0-F2 fixture content. Pack fixture tests remain green.
  Final acceptance still requires Opus and Root gates (Root previously reported
  non-TTY `make check` exit 0 / 80 passed; that recheck was not re-run by this
  writer during remediation).

### Changed paths (remediation)

```text
tests/test_opencrab_pack_fixtures.py
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/manifest.json
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/quality/report.json
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/README.md
tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/neo4j/export_status.json
docs/delegation/P0-F2_FULL_LAYOUT_FIXTURE_BRIEF.md
```
