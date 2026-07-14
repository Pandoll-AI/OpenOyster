# P0-F1 Minimal OpenCrab Pack Fixture 위임 브리프

상태: Root 승인 후 실행 중
작성일: 2026-07-13

## 위임 계약

```text
unit_id: P0-F1-minimal
importance: high
assigned_model_tool_and_effort: Codex Terra / gpt-5.6-terra / high
role: terra_implementation
outcome: OpenCrab Pack v1 최소 4파일 fixture를 공식 validator로 원본 변경 없이 검증한다.
inputs:
  - docs/OPENCRAB_PACK_RUNTIME_REQUIREMENTS.md
  - docs/CODING_DELEGATION_CONTRACT.md
  - ../OpenCrab/docs/opencrab-pack-v1.md
  - ../OpenCrab/opencrab/pack/validation.py
  - ../OpenCrab/tests/test_pack_validation.py
owned_paths:
  - tests/fixtures/opencrab_pack_runtime/p0-f1-minimal/**
  - tests/test_opencrab_pack_fixtures.py
  - docs/delegation/P0-F1_MINIMAL_FIXTURE_BRIEF.md
forbidden_paths:
  - src/openoyster/**
  - pyproject.toml
  - README.md
  - docs/OPENCRAB_PACK_RUNTIME_REQUIREMENTS.md
  - docs/CODING_DELEGATION_CONTRACT.md
  - openoyster.db*
constraints:
  - fixture는 manifest.json, graph/nodes.jsonl, graph/edges.jsonl, evidence/index.jsonl의 최소 계약을 고정한다.
  - 공식 OpenCrab validate_pack_static을 write_report=False로 호출한다.
  - source fixture의 파일 목록과 SHA-256 digest는 검증 전후 동일해야 한다.
  - importorskip, skip, xfail, 테스트 삭제, coverage 하향, 오류 무시는 금지한다.
  - sibling ../OpenCrab이 없으면 조용히 건너뛰지 말고 필요한 경로를 포함한 명확한 실패를 낸다.
acceptance_criteria:
  - fixture가 없을 때 의도된 RED를 production 변경 전에 관찰한다.
  - test 이름이 현재 validator의 4파일 계약과 문서상 full layout의 차이를 드러낸다.
  - 공식 validator report의 status와 핵심 check가 pass다.
  - 검증 전후 source fixture의 파일 목록과 SHA-256 digest가 같다.
  - P0-F1 대상 테스트와 make check가 성공한다.
exact_commands:
  - PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q
  - PATH="$PWD/.venv/bin:$PATH" make check
timeout: 1800 seconds
stop_conditions:
  - OpenCrab validator API가 조사 근거와 다르다.
  - owned_paths 밖 변경이 필요하다.
  - model 또는 effort가 model_lock과 다르다.
  - source fixture가 검증 중 변경된다.
return_format: CODING_DELEGATION_CONTRACT v1 standard return
model_lock: gpt-5.6-terra/high
model_lock_verification: Codex CLI 실행 헤더의 model과 reasoning effort를 권위 있는 실행 증거로 사용한다.
no_model_substitution: true
```

## 실행 증거

Terra는 RED, GREEN, REFACTOR 단계별로 정확한 명령, 종료 코드, 핵심 결과를 이
절에 기록한다. Root는 Grok 감사와 Opus 리뷰가 끝난 뒤에만 최종 인수한다.

### RED

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q`
- exit code: `1`
- key result: `test_current_validator_accepts_minimal_four_file_pack_without_pack_v1_full_layout`
  failed only because `tests/fixtures/opencrab_pack_runtime/p0-f1-minimal` was missing.

### GREEN

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q`
- exit code: `0`
- key result: the P0-F1 fixture passed the official validator with `write_report=False`;
  the test verified all four source paths and SHA-256 digests were unchanged and
  `quality/report.json` was absent.

### REFACTOR

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q`
- exit code: `0`
- key result: after isolating the official validator import in a test helper, the same
  fixture compatibility, source-digest, and no-report assertions passed.

### make check

- command: `PATH="$PWD/.venv/bin:$PATH" make check`
- exit code: `2`
- key result: the first full gate stopped at Ruff `I001`; the only finding was the
  extra blank line in `tests/test_opencrab_pack_fixtures.py`'s import block. No
  fixture or forbidden path was changed to address it.

- retry command: `PATH="$PWD/.venv/bin:$PATH" make check`
- exit code: `0`
- key result: Ruff and mypy passed; pytest reported `76 passed, 1 warning`; the
  package sdist and wheel were built successfully.

### Grok M1/M2 rework

- audit verdict: `NEEDS_REWORK`
- M1: parent-process `sys.modules` could make the helper use a non-sibling
  `opencrab` validator despite inserting the sibling path first.
- M2: the helper permanently changed parent-process `sys.path`.
- fix: validation now runs in a separate Python subprocess whose `PYTHONPATH`
  begins with `../OpenCrab`. The subprocess verifies
  `opencrab.pack.validation.__file__` is beneath `OPENCRAB_ROOT.resolve()`, then
  calls `validate_pack_static(FIXTURE_ROOT, write_report=False)` and emits its
  JSON report. Parent `sys.path` and `sys.modules` are not imported or changed.

#### RED

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q`
- exit code: `1`
- key result: two new regressions failed as intended: the old helper added
  `../OpenCrab` to parent `sys.path`, and a fake parent
  `opencrab.pack.validation` returned status `fake` rather than the official
  sibling validator report.

#### GREEN

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q`
- exit code: `0`
- key result: all three tests passed. The fake parent module tree remained in
  `sys.modules`, parent `sys.path` was unchanged, and the subprocess returned
  the official sibling validator's passing report without writing a report file
  or changing fixture paths or SHA-256 digests.

#### REFACTOR

- command: `PATH="$PWD/.venv/bin:$PATH" pytest tests/test_opencrab_pack_fixtures.py -q`
- exit code: `0`
- key result: the subprocess helper was simplified to a direct validator call;
  all three tests remained green.

#### make check

- command: `PATH="$PWD/.venv/bin:$PATH" make check`
- exit code: `0`
- key result: Ruff and mypy passed; pytest reported `78 passed, 1 warning`; the
  package sdist and wheel were built successfully.

## Root final acceptance

```text
acceptance_id: 2026-07-13-p0-f1-minimal
root_orchestrator: Codex /root
scope:
  - tests/fixtures/opencrab_pack_runtime/p0-f1-minimal/**
  - tests/test_opencrab_pack_fixtures.py
  - docs/delegation/P0-F1_MINIMAL_FIXTURE_BRIEF.md
base_commit: 4c6d0ea055ca5a8a7327564688732d1ddb12e50d
insert_order:
  - Root brief and contract lock
  - Terra RED test
  - Terra minimal fixture and GREEN/REFACTOR
  - Grok adversarial audit
  - Terra M1/M2 RED/GREEN/REFACTOR remediation
  - Claude Opus read-only final review
  - Root independent acceptance gates
writer_history:
  - Root, orchestrator, brief and acceptance record
  - Terra, implementer, gpt-5.6-terra/high, PASS
  - Grok, auditor, grok-4.5/xhigh, NEEDS_REWORK then remediated
  - Claude Opus, reviewer, claude-opus-4-8/high, MERGE_OK
changed_paths:
  - tests/fixtures/opencrab_pack_runtime/p0-f1-minimal/**
  - tests/test_opencrab_pack_fixtures.py
  - docs/delegation/P0-F1_MINIMAL_FIXTURE_BRIEF.md
forbidden_paths: PASS; no src/openoyster, production config, or P0-F1 fixture drift
tdd_evidence: this brief RED/GREEN/REFACTOR and Grok M1/M2 rework sections
gate_results:
  preflight: PASS
  scope: PASS
  unit: PASS
  make_check: PASS; 78 passed, 1 dependency deprecation warning
  source_pack_digest: PASS; exactly four paths and SHA-256 unchanged
  security: NOT_APPLICABLE; no archive extraction or production admission code
  documentation: PASS
  opus_review: PASS
opus_status: MERGE_OK
critical_count: 0
major_count: 0
finding_resolutions:
  - OPUS-001 Note: Root owns worktree separation; no commit was created and
    P0-F1 remains isolated from the pre-existing README, .gitignore, contract,
    and requirements changes; rechecked with git status.
  - OPUS-002 Minor: Terra owns the sibling-missing diagnostic; accepted as
    non-blocking because every path hard-fails without a silent skip; rechecked
    by the three passing target tests. Improve the low-level error text in P0-F2.
commit_push_deploy: not_performed
final_decision: ACCEPTED
root_signature: Codex /root, 2026-07-13T21:18:37+0900
```

The non-code CLI stop/helper hook failure observed after delegated output did
not change files or gate results. Root repeated the target test, digest check,
scope check, and full `make check` independently before acceptance.
