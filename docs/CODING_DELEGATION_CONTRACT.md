# 코딩 위임 계약

상태: 재사용 가능 운영 계약 v1.0
적용 기준: OpenOyster 및 OpenCrab Pack Runtime 작업
최종 인수 권한: Root Orchestrator만 보유

## 1. 목적

이 계약은 코딩 위임의 책임, 변경 경계, 검증 증거, 모델 고정을 명확히 한다.
목표는 빠른 결과가 아니라 재현 가능하고 감사 가능한 결과다. 모든 구현은 의도된
TDD 실패, 제한된 파일 소유권, 독립 리뷰, 전역 품질 게이트를 거쳐야 한다.

## 2. 적용 범위와 비목표

이 계약은 OpenOyster 저장소에서 코드, 테스트, fixture, 문서, 자동화 변경을
위임할 때 적용한다. 특히 `docs/OPENCRAB_PACK_RUNTIME_REQUIREMENTS.md`를
따르는 Pack Runtime 작업에는 필수다.

다음은 이 계약의 비목표다.

- 승인되지 않은 제품 방향이나 API 계약을 결정하는 일
- source Pack을 편의상 수정하거나 자동 보정하는 일
- 테스트를 통과시키기 위한 우회나 증거 없는 완료 선언
- 사용자 명시 승인 없는 commit, push, 배포, 원격 상태 변경
- Root Orchestrator 외 주체의 최종 인수 또는 병합 승인

Root Orchestrator만 최종 인수한다. 구현자, 통합자, 감사자, Opus 리뷰어는
자신의 작업을 인수할 수 없고, 구현자와 리뷰어는 같은 사람이거나 같은 실행
주체일 수 없다.

## 3. 역할과 분리

역할은 겹치지 않는다. 한 위임 단위에는 한 역할만 지정한다.

- Cursor CLI는 명확히 경계 지어진 구현만 수행한다. 승인된 `owned_paths` 밖을
  읽기·수정하지 않으며, 통합 판단이나 최종 리뷰를 하지 않는다.
- Grok CLI는 적대 감사와 테스트 갭 탐지만 수행한다. 실패 경로, 우회 가능성,
  누락된 경계 조건을 찾고 증거를 남긴다. 구현 완료를 선언하거나 최종 인수하지
  않는다.
- Codex Terra는 통합 구현과 수정의 책임자다. Cursor 산출물을 포함해 승인된
  변경을 순서대로 통합하고, 감사 지적을 수정하며, 재검증한다. Terra는 자신이
  수정한 단위의 리뷰어가 될 수 없다.
- Claude Opus는 read-only 최종 코드 리뷰만 수행한다. 파일을 수정하거나 테스트
  기대값을 바꾸거나 구현을 대체하지 않는다. 심각도와 병합 판정을 기록한다.

한 사람이 여러 도구를 사용해도 역할 분리는 유지해야 한다. 같은 변경 단위에서
Cursor 또는 Terra가 구현했다면 Grok 또는 Opus가 독립 리뷰를 맡아야 한다.

## 4. 승인, writer, 병렬 실행

같은 checkout에서는 한 번에 한 writer만 허용한다. writer는 코드, 테스트,
fixture, 문서, 설정을 새로 쓰거나 변경하는 모든 주체다. 읽기 전용 조사와
Opus의 read-only 리뷰만 writer slot 없이 병렬로 할 수 있다.

기본 실행은 순차다.

1. Root Orchestrator가 브리프와 writer 순서를 승인한다.
2. 현재 writer가 자신의 `owned_paths`만 변경하고 반환한다.
3. 다음 writer는 이전 산출물을 삽입한 뒤 scope, 단위 테스트, 필요한 전체
   게이트를 재검증한다.
4. Root Orchestrator가 최종 검증 기록과 Opus 결과를 보고 인수한다.

실제 병렬 구현은 다음 모두를 만족할 때만 허용한다.

- 각 구현자가 별도 격리 worktree를 사용한다.
- 각 worktree의 `owned_paths`가 서로 완전히 disjoint하다.
- 공유 생성물, lockfile, migration, 같은 테스트 파일, 같은 문서 섹션을 동시에
  소유하지 않는다.
- Root Orchestrator가 삽입 순서와 충돌 해결 owner를 사전에 기록한다.

삽입은 승인된 순서대로 한 단위씩 한다. 각 삽입 뒤에는 `git diff --check`,
변경 경로 검사, 해당 단위 테스트를 다시 실행한다. 이전 삽입의 테스트가
실패하면 다음 삽입을 멈추고 해당 owner에게 돌린다. 병렬 결과를 한 번에
합치거나, 삽입 순서를 바꾸거나, 충돌 해결 중 타인의 변경을 되돌리면 FAIL이다.

## 5. 위임 브리프 필수 필드

모든 위임은 시작 전에 아래 필드를 빠짐없이 가진다. 모호한 값은 브리프 미완성
이며 실행하지 않는다.

```text
importance: low | medium | high | critical
assigned_model_tool_and_effort: 예) Codex Terra / high
role: cursor_implementation | terra_integration | grok_adversarial_audit | opus_read_only_review
outcome: 검증 가능한 한 문장 결과
inputs: 읽을 문서, 기준 commit, 기존 증거
owned_paths: 수정 가능한 정확한 경로 목록
forbidden_paths: 절대 수정하지 않을 정확한 경로 목록
constraints: 계약, 호환성, 보안, 금지된 행동
acceptance_criteria: PASS가 되기 위한 관찰 가능한 조건
exact_commands: 순서와 인자를 고정한 실행 명령 목록
timeout: 단위별 제한 시간
stop_conditions: 즉시 중지·보고할 조건
return_format: 이 계약의 반환 형식 버전
model_lock: 지정 모델/도구와 effort의 정확한 값
no_model_substitution: true
```

`assigned_model_tool_and_effort`와 `model_lock`는 서로 동일해야 한다. 모델,
도구, effort 어느 하나라도 달라지면 실행하지 않는다. 더 강한 모델, 더 낮은
effort, 다른 CLI, 수동 편집으로의 묵시적 대체도 허용하지 않는다.

브리프의 `exact_commands`에는 적어도 preflight, RED, GREEN, 단위 테스트,
해당 시 `make check`, source Pack digest 검사, 종료 검사를 명시한다. 경로,
테스트 selector, timeout, 기대 상태를 생략한 명령은 exact command가 아니다.

## 6. TDD와 증거

동작 변경은 RED → GREEN → REFACTOR 순서를 지킨다.

1. RED: production code를 바꾸기 전에 의도된 동작을 검증하는 테스트를 만들고
   실패를 실제로 관찰한다. 테스트 이름, 정확한 명령, 실패 요약, 실행 시각을
   증거에 남긴다.
2. GREEN: 최소 production 변경으로 같은 테스트를 통과시킨다. 명령과 통과
   요약을 남긴다.
3. REFACTOR: 동작을 바꾸지 않는 정리를 한 뒤 관련 테스트와 필요한 전역
   게이트를 다시 통과시킨다.

문서 전용 변경처럼 production 동작이 바뀌지 않는 작업은 RED/GREEN 적용
대상이 아니다. 이 경우 브리프에 `tdd: not_applicable_documentation_only`와
문서 검증 명령을 명시한다. fixture·테스트·production 동작 변경은 예외가 아니다.

다음은 금지다.

- `skip`, `xfail`, 테스트 삭제로 실패를 숨기는 행위
- coverage 기준 하향
- `|| true`, 오류 무시, 실패한 명령 뒤의 성공 선언
- RED 관찰 전 production code 변경
- 다른 실패를 RED 증거로 재사용

## 7. 자동화 가능한 품질 게이트

각 게이트는 PASS 또는 FAIL만 낸다. 증거가 없거나 명령이 실행되지 않았으면
FAIL이다. `make check`는 모든 작업의 전역 게이트다.

### 7.1 Preflight gate

PASS 조건:

- 기준 commit과 `git status --short`를 기록했다.
- 사용자 소유의 기존 변경과 untracked 파일을 식별하고 `forbidden_paths`에
  넣었다.
- 브리프의 모델 고정, writer slot, 경로 소유권, timeout을 확인했다.
- 필요한 인증 상태를 비밀값을 출력하지 않고 확인했다.

FAIL 조건:

- 작업트리가 예상과 다르거나 다른 writer가 같은 checkout을 쓰고 있다.
- 브리프가 불완전하거나 모델/effort가 일치하지 않는다.
- 인증 또는 도구 상태가 작업을 막는다.

### 7.2 Scope gate

PASS 조건: `git diff --check`가 통과하고 변경 경로가 해당 `owned_paths` 안에만
있으며, `forbidden_paths`에는 변경이 없다.

FAIL 조건: 범위 밖 변경, 공백 오류, 사용자 소유 변경의 수정·삭제, 소유권 충돌이
하나라도 있다.

### 7.3 Unit-test gate

PASS 조건: 브리프에 적힌 정확한 테스트 selector가 timeout 안에 성공하고
RED/GREEN 증거가 완전하다.

FAIL 조건: 테스트 실패, timeout, skip/xfail/삭제/우회, 또는 증거 누락이다.

### 7.4 Full `make check` gate

PASS 조건: 저장소 루트에서 정확히 `make check`를 실행해 lint, typecheck, test,
build가 모두 성공한다.

FAIL 조건: 하위 단계 하나라도 실패하거나, 명령이 실행되지 않거나, `|| true`로
실패를 감쌌다.

### 7.5 Source Pack 불변성 digest gate

Pack fixture·validator·admission 관련 작업에서는 source Pack 검사 전후 digest를
비교한다. directory는 다음 형식의 명령을 브리프의 실제 경로로 고정해 사용한다.

```bash
find "$SOURCE_PACK" -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 > "$EVIDENCE_DIR/source-pack.before.sha256"
# validate/install/query/analyze exact commands
find "$SOURCE_PACK" -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 > "$EVIDENCE_DIR/source-pack.after.sha256"
diff -u "$EVIDENCE_DIR/source-pack.before.sha256" "$EVIDENCE_DIR/source-pack.after.sha256"
```

ZIP source는 같은 위치의 archive 파일에 대해 전후 `shasum -a 256`을 비교한다.
PASS는 diff 출력이 없고 source 파일이 새로 생기거나 사라지지 않는 것이다.
변경이 하나라도 있으면 FAIL이며 source Pack을 복구하려고 수정하지 않는다.

### 7.6 Security gate

PASS 조건: 입력 검증 작업은 path traversal, absolute path, symlink escape,
duplicate path, case collision, compression ratio, file count, uncompressed byte
제한의 관련 음성 fixture를 실행하고, 실패 archive가 Pack store 밖에 쓰지 않은
증거를 남긴다. 비밀값은 로그·diff·증거에 없다.

FAIL 조건: 안전하지 않은 archive가 부분 설치되거나, 시크릿이 기록되거나,
관련 보안 fixture가 누락된다.

### 7.7 Documentation gate

PASS 조건: 변경한 public 계약, path, 명령, 상태가 실제 코드·fixture·명령과
일치하고 Markdown 링크와 코드 블록이 깨지지 않는다.

FAIL 조건: 구현과 문서가 다르거나, 문서만으로 검증 절차를 재현할 수 없다.

### 7.8 Opus review gate

PASS 조건: Claude Opus의 read-only 리뷰가 완료되고 `MERGE_OK`이며 Critical과
Major가 각각 0개다. Minor와 Nit도 owner, resolution, recheck가 모두 기록돼야
한다.

FAIL 조건: 리뷰 누락, `MERGE_BLOCKED`, `NEEDS_REWORK`, 미해결 Critical/Major,
또는 지적사항의 증거·owner·resolution·recheck 누락이다.

### 7.9 Closure gate

PASS 조건: 모든 선행 gate가 PASS이고, 반환 형식이 완전하며, Root Orchestrator가
최종 인수를 명시한다.

FAIL 조건: 부분 성공을 완료로 표시하거나, 인수권자가 아닌 주체가 완료·병합을
선언한다.

## 8. 중지와 예외 처리

다음 상황에서는 즉시 작업을 멈추고 `STOPPED`로 보고한다.

- 인증 실패, keychain 잠김, 필요한 권한 또는 API 접근 불가
- 사용자 또는 Root Orchestrator의 취소
- 브리프 timeout 초과
- 도구 오류, wrapper 오류, 재현 불가능한 CLI 실패
- model lock, 도구, effort 불일치
- writer slot 또는 `owned_paths` 충돌
- 시크릿 노출 가능성, source Pack 변경, 범위 밖 변경

중지 보고에는 마지막 성공 단계, 정확한 실패 명령과 요약, 변경된 경로, 남은
작업, 재개에 필요한 권한 또는 입력만 적는다. 시크릿 값은 적지 않는다.

부분 작업은 보존한다. `git reset --hard`, 광범위한 checkout 복원, 타인의
untracked 파일 삭제, 생성물 일괄 정리는 금지한다. stop은 다른 모델 또는 다른
도구로 자동 재시작할 근거가 아니다. Root Orchestrator가 새 브리프를 승인하기
전에는 묵시적 대체·상향·하향을 하지 않는다.

## 9. Claude Opus 최종 리뷰 형식

Opus는 read-only로 다음 스키마를 반환한다. 각 finding은 하나의 독립 항목이다.

```text
reviewer: Claude Opus
mode: read_only
status: MERGE_OK | MERGE_BLOCKED | NEEDS_REWORK
findings:
  - id: OPUS-001
    severity: Critical | Major | Minor | Nit
    path: 정확한 파일 경로
    evidence: 재현 명령, 관찰 결과, 또는 코드 근거
    owner: Cursor | Terra | Root Orchestrator
    resolution: 수정 내용 또는 수용 불가 사유
    recheck: 수정 뒤 실행한 정확한 명령과 결과
residual_risks: 없음 또는 구체적 목록
```

Critical 또는 Major가 하나라도 있으면 Root Orchestrator는 인수할 수 없다.
해결된 Critical/Major도 recheck가 성공하기 전까지는 미해결이다. Minor와 Nit는
병합 차단 기준은 아니지만, 누락 없이 owner, resolution, recheck를 가져야
`MERGE_OK`가 될 수 있다. Opus가 코드를 고치거나 테스트를 바꾸면 이 계약의
read-only 리뷰가 아니며 리뷰는 무효다.

## 10. 표준 반환 형식

모든 역할은 아래 형식으로 반환한다. 감사와 리뷰는 `changed_paths`에
`none_read_only`를 쓴다.

```text
status: PASS | FAIL | STOPPED | GROK_AUDIT_COMPLETE
role: 브리프 role
model_lock_observed: 지정 모델/도구/effort 또는 mismatch
changed_paths: 정확한 경로 목록
forbidden_paths_check: PASS | FAIL
commands_run:
  - command: 정확한 명령
    result: exit code와 짧은 출력 요약
tdd_evidence:
  red: 명령·실패 요약 또는 not_applicable_documentation_only
  green: 명령·성공 요약 또는 not_applicable_documentation_only
  refactor: 명령·성공 요약 또는 not_applicable_documentation_only
gates: preflight, scope, unit, make_check, digest, security, documentation, opus, closure의 상태
findings_or_blockers: 없음 또는 id와 증거
partial_work_preserved: yes | no
next_required_action: Root Orchestrator가 판단할 한 문장
```

## 11. OpenCrab Pack Runtime 최초 적용

이 절은 `docs/OPENCRAB_PACK_RUNTIME_REQUIREMENTS.md`에 이 계약을 처음 적용하는
순서다. P0-F1부터 P0-F3까지는 fixture 계약을 고정했다. 최신 제품 결정에 따라
P0-F4부터 P0-F6은 선행 필수 단계가 아니며, trusted-directory Pack Runtime을
먼저 구현한다.

### P0-F1: minimal fixture

첫 작업은 Pack v1 minimal fixture다. 현재 validator가 강제하는 네 파일,
`manifest.json`, `graph/nodes.jsonl`, `graph/edges.jsonl`,
`evidence/index.jsonl`만으로 compatible 검증 계약을 고정한다.

- owned paths: `tests/fixtures/opencrab_pack_runtime/p0-f1-minimal/**`,
  `tests/test_opencrab_pack_fixtures.py`
- forbidden paths: `src/openoyster/**`, 기존 fixture와 production 설정
- 완료 증거: source 변경 없이 validator를 실행한 RED/GREEN 증거와 digest 비교

### P0-F2: full-layout fixture

P0-F1 다음에 문서상 full Pack layout fixture를 만든다. required layout의
`quality/report.json`, Neo4j 자료, README, sample queries, community report를
포함하고 strict와 compatible의 차이를 테스트 이름으로 드러낸다.

- owned paths: `tests/fixtures/opencrab_pack_runtime/p0-f2-full-layout/**`,
  `tests/test_opencrab_pack_fixtures.py`
- forbidden paths: `src/openoyster/**`, P0-F1 fixture
- 완료 증거: F1 재검증, full-layout 단위 테스트, source digest PASS

### P0-F3: invalid archives와 broken provenance

P0-F2 다음에 path traversal, absolute path, symlink escape, duplicate path,
case collision, zip bomb 경계와 broken provenance fixture를 만든다. 실패 archive는
어떤 source file도 Pack store 밖에 쓰지 못해야 한다.

- owned paths: `tests/fixtures/opencrab_pack_runtime/p0-f3-invalid-archives/**`,
  `tests/fixtures/opencrab_pack_runtime/p0-f3-broken-provenance/**`,
  `tests/test_opencrab_pack_fixtures.py`
- forbidden paths: `src/openoyster/**`, P0-F1·P0-F2 fixture
- 완료 증거: 음성 테스트, 쓰기 경계 증거, source digest PASS

### P0-F4: multimodal image/OCR/CLIP fixture

상태: 후속 선택 단계

P0-F3 다음에 image, OCR, CLIP provenance와 asset hash·region traceability를
검증하는 fixture를 만든다. 이 단계는 audio/video를 구현하지 않는다.

- owned paths: `tests/fixtures/opencrab_pack_runtime/p0-f4-multimodal/**`,
  `tests/test_opencrab_pack_fixtures.py`
- forbidden paths: `src/openoyster/**`, P0-F1·P0-F2·P0-F3 fixture
- 완료 증거: asset hash와 region을 역추적하는 테스트, source digest PASS

### P0-F5: audio/video evidence profile

상태: 후속 선택 단계

P0-F4 다음에 OpenOyster audio/video evidence profile 초안을 문서와 schema
fixture로 고정한다. Pack v1에 표준 audio/video schema가 없다는 사실을 바꾸지
않으며, 구현 계약을 확정하지 않는다.

- owned paths: `docs/OPENCRAB_AUDIO_VIDEO_EVIDENCE_PROFILE.md`,
  `tests/fixtures/opencrab_pack_runtime/p0-f5-audio-video-profile/**`,
  `tests/test_opencrab_pack_fixtures.py`
- forbidden paths: `src/openoyster/**`, P0-F1부터 P0-F4 fixture
- 완료 증거: profile 문서 검증, fixture schema 테스트, source digest PASS

### P0-F6: upstream confirmation

상태: 후속 선택 단계

마지막으로 OpenCrab upstream에 manifest schema, canonical pack digest, update
semantics, custom schema pack, audio/video schema 경계를 확인한다. 미확정 답을
추정으로 production 계약에 넣지 않는다.

- owned paths: `docs/OPENCRAB_UPSTREAM_CONFIRMATION.md`,
  `docs/OPENCRAB_PACK_RUNTIME_REQUIREMENTS.md`
- forbidden paths: `src/openoyster/**`, 모든 Phase 0 fixture
- 완료 증거: 질문, 권위 있는 응답 또는 미확정 상태, 영향 범위, 다음 결정 owner

P0-F1 → P0-F2 → P0-F3 다음에는 MVP-P1 trusted-directory Pack Runtime으로
진행한다. P0-F4부터 P0-F6은 해당 후속 기능을 시작할 때 다시 연다. ZIP production
admission은 P0-F3 fixture가 있어도 현재 구현 범위에 포함하지 않는다.

### MVP-P1: trusted-directory Pack Runtime

- 입력: 사용자가 신뢰한 local Pack directory
- production scope: registry, namespaced graph/evidence, Pack-aware retrieval,
  grounded answer, knowledge boundary, CLI/API
- 금지: ZIP extraction, raw URL/RSS/GitHub 우회 입력, source Pack 수정,
  implicit cross-Pack merge, unsupported citation 허용
- 완료 증거: AC-MVP-001, AC-MVP-002, source digest, focused tests,
  `make check`, Opus Critical/Major 0

## 12. 자동화 체크리스트

각 항목은 자동화 도구가 결과를 남길 수 있도록 체크한다.

- [ ] `git status --short`와 기준 commit을 evidence에 기록했다.
- [ ] `model_lock`와 실제 모델·도구·effort가 일치한다.
- [ ] 한 checkout의 writer가 하나이며, 병렬이면 worktree와 disjoint
  `owned_paths`가 있다.
- [ ] `git diff --check`가 성공했다.
- [ ] 변경 경로가 `owned_paths` 안에만 있고 `forbidden_paths`는 불변이다.
- [ ] RED 실패를 production 변경 전에 관찰했고 로그 또는 명령/출력 요약이 있다.
- [ ] GREEN과 REFACTOR의 관련 테스트가 성공했다.
- [ ] `skip`, `xfail`, 테스트 삭제, coverage 하향, `|| true`가 없다.
- [ ] Pack 작업이면 전후 source digest가 같고 source 파일 수가 같다.
- [ ] 보안 음성 fixture와 쓰기 경계 검사가 성공했다.
- [ ] 문서와 실제 명령·경로·계약이 일치한다.
- [ ] 저장소 루트의 `make check`가 성공했다.
- [ ] Opus가 read-only로 리뷰했고 Critical/Major는 0개다.
- [ ] commit, push, 배포를 하지 않았거나 사용자 명시 승인을 기록했다.
- [ ] Root Orchestrator가 최종 인수 기록을 작성했다.

## 13. 최종 인수 기록 템플릿

```text
acceptance_id: YYYY-MM-DD-work-item
root_orchestrator: 이름 또는 실행 식별자
scope: 승인된 owned_paths
base_commit: SHA
insert_order: 삽입 단위의 실제 순서
writer_history: writer, role, model_lock, 시작/종료 상태
changed_paths: 실제 변경 경로
forbidden_paths: 검사 결과
tdd_evidence: RED/GREEN/REFACTOR 증거 위치 또는 documentation-only 사유
gate_results:
  preflight: PASS | FAIL
  scope: PASS | FAIL
  unit: PASS | FAIL
  make_check: PASS | FAIL
  source_pack_digest: PASS | FAIL | NOT_APPLICABLE
  security: PASS | FAIL | NOT_APPLICABLE
  documentation: PASS | FAIL
  opus_review: PASS | FAIL
opus_status: MERGE_OK | MERGE_BLOCKED | NEEDS_REWORK
critical_count: 0
major_count: 0
finding_resolutions: finding id, evidence, owner, resolution, recheck
commit_push_deploy: not_performed | user_approved_action
final_decision: ACCEPTED | REJECTED | STOPPED
root_signature: 이름 또는 실행 식별자와 시각
```

`ACCEPTED`는 모든 필수 gate가 PASS이고 Critical/Major가 0개이며 Root
Orchestrator가 서명했을 때만 쓸 수 있다. 그렇지 않으면 `REJECTED` 또는
`STOPPED`다.

## 14. 현재 실행 기록 예시 — 영구 상태 아님

이 절은 전체 계약의 영구 상태가 아니라 현재 실행의 분리된 기록 예시다. 다음
상태는 다른 위임, 다른 브랜치, 다음 실행으로 자동 승계되지 않는다.

```text
execution_scope: current-run-example
cursor_cli:
  status: UNSATISFIED
  reason: wrapper 버그와 잠긴 macOS login keychain 때문에 결과물을 만들지 못함
  required_response: STOPPED로 보고하고 partial work만 보존
  substitution: 금지; 다른 모델·도구·effort로 묵시적 대체하지 않음
grok_cli:
  status: GROK_AUDIT_COMPLETE
  meaning: 이번 실행의 적대 감사 반환이 완료됨
  limitation: 구현 인수, Opus 최종 리뷰, Root 최종 인수를 의미하지 않음
routing_decision:
  authorized_by: user
  cursor_required: false
  effective_scope: P0-F1부터 이후 OpenCrab Pack Runtime 구현
  primary_implementer: gpt-5.6-terra/high
  adversarial_auditor: grok-4.5/xhigh
  final_reviewer: claude-opus-4-8/high/read-only
  final_acceptor: Root Orchestrator
  meaning: Cursor 실패를 과거 실행 증거로 보존하되 이후 구현의 blocker로 사용하지 않음
terra_quality_gate:
  model_lock: gpt-5.6-terra/high
  reported_by: Root
  command: PATH="$PWD/.venv/bin:$PATH" make check
  result: exit 0; ruff, mypy, 75 tests, build PASS; coverage 81%
  limitation: 이번 실행의 품질 게이트 사실이며 영구 상태가 아님
atomic_commit_unit:
  paths: README.md, docs/CODING_DELEGATION_CONTRACT.md, docs/OPENCRAB_PACK_RUNTIME_REQUIREMENTS.md
  rule: 사용자 승인 후 staging과 commit은 세 경로를 반드시 함께 포함하며 부분 commit은 금지
  pre_commit_check: git diff --cached --name-only | awk '$0 == "README.md" { readme=1 } $0 == "docs/CODING_DELEGATION_CONTRACT.md" { contract=1 } $0 == "docs/OPENCRAB_PACK_RUNTIME_REQUIREMENTS.md" { requirements=1 } END { exit !(readme && contract && requirements) }'
  current_action: stage와 commit을 수행하지 않음
```

Cursor의 위 상태는 실패를 숨기지 않는 원칙의 예시다. 인증 또는 keychain 문제가
해결되지 않은 동안 Cursor 산출물이 있는 것처럼 기록해서는 안 된다. Grok의
`GROK_AUDIT_COMPLETE` 역시 감사 완료 신호일 뿐, 전체 계약이 영구적으로
완료됐다는 상태가 아니다. `terra_quality_gate`와 `atomic_commit_unit`도 이
current-run example에만 적용되는 기록이며, 다른 실행의 영구 상태나 사용자 승인
없는 staging·commit 권한을 뜻하지 않는다.
