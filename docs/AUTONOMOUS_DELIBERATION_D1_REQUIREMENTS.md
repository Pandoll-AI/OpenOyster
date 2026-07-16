# Autonomous Deliberation D1 요구사항

상태: 구현 기준선
버전: D1
작성일: 2026-07-14

D2 continuation 계약은 [Decision Continuity D2 요구사항](DECISION_CONTINUITY_D2_REQUIREMENTS.md)을
참조한다. D2도 설치된 OpenCrab Pack만 사실 입력으로 사용하며, Pack 생성·갱신이나
Pack 내용 diff를 추가하지 않는다.

## 1. 제품 정의

OpenOyster D1은 OpenCrab Pack을 사실 입력으로만 사용하는 자율 숙의 실행기다.
사용자가 Mission과 Pack 범위를 주면 한 번의 실행으로 다음 결과를 만든다.

1. Belief State
2. 대안과 제약 판정
3. 기본·불리한 시나리오
4. 독립 비판 결과
5. 선택 또는 기권
6. 결정을 뒤집는 조건
7. OpenCrab에 전달할 Knowledge Request
8. Pack 범위에 따른 Cognitive Impact
9. 감사 가능한 Decision Dossier
10. 결정적 audit replay

OpenCrab은 지식을 만든다. OpenOyster는 그 지식으로 숙고하고, 결정하고, 무엇을
더 알아야 하는지 명시한다.

## 2. 절대 경계

- 사실 입력은 설치된 OpenCrab Pack evidence로 한정한다.
- Mission의 목표·질문·제약·선호·기한은 제어 입력이다. 증거가 아니다.
- 모델의 사전 지식, 웹 검색, 도구 호출, 로컬 문서 수집을 사실 근거로 사용하지 않는다.
- OpenOyster는 Pack을 만들거나 자동으로 갱신하지 않는다.
- Pack revision/file/record diff를 구현하지 않는다.
- Knowledge Request는 기록·내보내기만 한다. 실행하지 않는다.
- 원본 Pack과 설치된 Pack record를 수정하지 않는다.
- 범용 agent framework나 작업 실행기를 만들지 않는다.

## 3. Goal 완료 조건

다음 문장이 실제 코드와 테스트로 참이어야 한다.

> 하나의 Mission과 명시한 OpenCrab Pack 집합을 입력하면, OpenOyster가 Pack 근거
> 안에서 믿음·대안·시나리오·반론을 구성하고 선택 또는 기권하며, 전환 조건과
> 지식 요청을 포함한 Decision Dossier를 저장·조회·감사 재생한다.

## 4. Mission 계약

Mission은 YAML, JSON 또는 API 객체로 받는다.

- `goal`: 달성하려는 상태. 필수.
- `decision_question`: 이번 실행이 답할 결정 질문. 필수.
- `constraints`: 위반하면 선택할 수 없는 조건. 기본 빈 목록.
- `preferences`: 대안을 비교할 때 사용하는 선호. 기본 빈 목록.
- `deadline`: 선택적 ISO-8601 시각.
- `context`: 제어용 배경. 사실 근거로 승격할 수 없다.
- `mission_charter_id`: 선택적 기존 Mission Charter 참조.

Mission은 canonical JSON과 SHA-256 digest로 실행에 동결한다.

## 5. 실행 aggregate

D1의 source of truth는 `DeliberationRun` aggregate다. 기존 `Hypothesis`,
`Artifact`, `DecisionTrace`, `Experiment`를 D1의 원장으로 재사용하지 않는다.

### 5.1 Run

- 유일한 `idempotency_key`
- Mission, policy, runtime config의 snapshot과 digest
- 계약 버전과 prompt template 버전
- primary Pack scope와 선택적 impact baseline scope digest
- 상태, 현재 stage, outcome
- 실패·degraded·indeterminate 정보
- stage lease와 timestamps

### 5.2 Frozen Pack scope

첫 LLM 호출 전에 선택 Pack을 정확한 `PackInstall.id`로 고정한다.

- 역할은 `primary` 또는 `impact_baseline`이다.
- Pack ID, 선언 버전, source digest, admission profile을 snapshot한다.
- 실행 중 active Pack이 바뀌어도 frozen scope는 바뀌지 않는다.
- D1 retrieval은 active status가 아니라 frozen install ID로 조회한다.
- impact baseline은 primary의 부분집합이어야 한다. 명시적 빈 집합은 허용한다.

### 5.3 Evidence snapshot

LLM에 노출한 evidence만 별도 snapshot한다.

- Pack evidence FK와 global evidence ID
- record hash
- prompt-visible payload와 digest
- retrieval rank와 score
- 최대 24개

D1의 모든 grounded citation은 이 snapshot만 참조할 수 있다.

### 5.4 Stage call

- stage, attempt number, status
- provider, model, effort, template version
- prompt/config/input manifest digest
- 검증된 response JSON과 digest
- raw response digest와 길이
- usage, duration, error, timestamps

raw Pack 본문과 전체 prompt는 API 응답이나 dossier에 노출하지 않는다.

### 5.5 Artifact와 assertion

Artifact kind는 다음으로 닫는다.

- `beliefs`
- `options`
- `scenarios`
- `critic_result`
- `decision`
- `flip_conditions`
- `knowledge_requests`

모든 LLM narrative 문자열은 assertion으로 분류한다.

- `grounded_fact`: Pack evidence anchor 필수
- `grounded_inference`: Pack evidence anchor 필수
- `mission_control`: 유효한 Mission JSON pointer 필수
- `proposal`: Mission pointer 또는 artifact reference 필수
- `assumption`: assumption 표시와 verification question 필수
- `gap`: unresolved question 필수
- `structural`: 닫힌 issue code와 artifact reference만 허용

분류되지 않은 narrative 문자열은 stage 전체를 거부한다.

### 5.6 Citation anchor

evidence ID만으로는 부족하다. 다음 중 하나가 필요하다.

- `quote`: evidence text에 정확히 존재하는 비어 있지 않은 문자열
- `json_pointer`: evidence snapshot payload에서 해석되는 포인터와 값 digest

local-only ID, scope 밖 ID, 미조회 ID, quote 불일치, pointer 불일치, Mission을
evidence로 사용한 경우에는 artifact를 저장하지 않는다.

## 6. 다섯 개의 유계 LLM stage

자동 수정 loop와 자동 재시도는 없다. 정상 happy path는 정확히 5회 호출한다.

### 6.1 `deliberation_beliefs`

- 최대 20개 atomic belief
- 상태: `supported`, `contested`, `unknown`, `invalidated`
- supporting/opposing citation anchors
- assumptions, gaps, invalidation conditions
- 숫자형 truth confidence 금지

### 6.2 `deliberation_options`

- 최대 5개 대안
- 가능하면 최소 2개 viable option
- `do_nothing`, `defer`, `acquire_information`을 필요할 때 고려
- 모든 Mission constraint를 각각 판정
- hard constraint 위반은 점수 감점이 아니라 제외
- supporting/opposing belief reference, 위험, 가역성, 예상 결과

### 6.3 `deliberation_scenarios`

- 대안마다 expected와 adverse 시나리오 필수
- 대안마다 최대 3개
- projected outcome은 grounded inference이며 anchor 필수
- 사실·추론·가정을 구분

### 6.4 `deliberation_critic`

- verdict: `pass`, `revise`, `abstain`
- issue는 닫힌 code와 artifact reference만 사용
- 누락 대안, 증거 편향, 반대 근거 누락, 제약 오해, Pack 밖 사실,
  과도한 주장, 근거 없는 결과를 검사
- `pass`가 아니면 선택할 수 없다. D1은 revision loop를 돌지 않는다.

### 6.5 `deliberation_decision`

- outcome: `select` 또는 `abstain`
- 선택은 기존 viable option만 참조
- expected/adverse scenario가 모두 있어야 한다.
- flip condition을 최소 1개 만든다.
- unresolved critical gap마다 Knowledge Request를 만든다.
- invalid payload는 버리고 결정적 abstention으로 대체한다.

## 7. 결정적 gate

### 7.1 입력 gate

- Mission schema 유효
- Pack ID 중복 없음
- 모든 Pack 설치 존재
- 첫 호출 시점에 active revision 정확히 하나
- strict Pack이 기본
- compatible Pack은 명시적 policy switch가 있을 때만 허용
- baseline scope는 primary scope의 부분집합

### 7.2 stage gate

- Pydantic strict validation과 `extra="forbid"`
- 개수 제한과 local key uniqueness
- 모든 Mission/artifact reference 해석
- 모든 evidence anchor 검증
- 한 stage의 call, artifact, assertion, citation, 다음 상태를 한 transaction으로 저장

### 7.3 selection gate

- critic `pass`
- viable option 최소 2개
- 선택 대안 존재
- 해당 대안 expected/adverse scenario 존재
- 위반된 hard constraint 없음
- decision rationale이 grounded 또는 mission-controlled

하나라도 실패하면 선택 대신 abstention을 저장한다.

### 7.4 abstention gate

- 닫힌 reason code 최소 1개
- flip condition 최소 1개
- critical gap마다 Knowledge Request

증거가 전혀 없으면 LLM을 호출하지 않고 deterministic abstention을 완료한다.

## 8. 상태와 crash 계약

정상 상태:

`created → scope_frozen → context_ready → beliefs_ready → options_ready → scenarios_ready → critic_ready → decision_ready → impact_ready → completed`

Decision Dossier는 terminal commit에 함께 저장한다. 명시적 replay는 별도의
`DeliberationReplayResult`를 남기며 이미 완료된 Run 상태를 되돌려 쓰지 않는다.

예외 terminal 상태:

- `failed_input`
- `failed_database`
- `indeterminate`

abstention은 실패가 아니라 정상 outcome이다.

각 LLM stage는 짧은 transaction을 사용한다.

1. lease 획득과 `StageCall(started)` 저장 후 commit
2. DB transaction 밖에서 LLM 호출
3. 메모리에서 전체 payload 검증
4. 결과와 다음 상태를 atomic commit

LLM 응답 뒤 결과 저장 전에 crash가 발생하면 lease 만료 후 `indeterminate`다.
자동 재호출하지 않는다. 새 idempotency key로 새 run을 만들어야 한다.

같은 idempotency key는 중복 실행을 만들지 않는다. 완료된 결과는 LLM 호출 없이
반환하고, 진행 또는 indeterminate 상태는 현재 상태를 반환한다.

## 9. Cognitive Impact

`citation_scope_projection_v1`만 구현한다.

- caller가 primary scope와 baseline scope를 모두 제공한다.
- 각 grounded assertion을 baseline에서 `retained`, `partially_supported`,
  `unsupported`로 분류한다.
- belief, scenario outcome, critic finding, decision rationale에 대한 영향을 집계한다.
- decision support를 `retained`, `weakened`, `lost`로 보고한다.
- primary에만 있는 Pack membership을 기록한다.

Pack record, revision, file, node, evidence 내용은 diff하지 않는다. update/delete의
원인도 추론하지 않는다. 이 기능은 인용 근거 의존성을 측정할 뿐 baseline-only
재실행이 만들 수 있었던 새로운 추론은 발견하지 못한다고 명시한다.

## 10. Dossier와 replay

Decision Dossier는 canonical JSON과 친절한 Markdown을 함께 저장한다.

- Mission snapshot
- 정확한 Pack IDs, versions, install IDs, digests
- beliefs와 모순
- options와 제외 이유
- expected/adverse scenarios
- critic result
- selection 또는 abstention
- flip conditions
- knowledge requests
- Cognitive Impact
- citations와 exact anchors
- model/effort/template/contract 정보

replay는 LLM을 다시 호출하지 않는다.

- 저장된 stage payload와 anchor 재검증
- input/output manifest 재계산
- dossier 재렌더링
- 저장 hash와 재구성 hash 비교
- matched/mismatched 결과 저장

새 LLM 실행은 replay가 아니라 linked rerun이다. D1 core의 replay 경로는 새 실행을
만들지 않으며, linked rerun은 별도 [Decision Continuity D2](DECISION_CONTINUITY_D2_REQUIREMENTS.md)
continuation 계약으로 제공한다.

## 11. CLI와 API

CLI:

```text
openoyster deliberate run MISSION.yaml \
  --packs pack-a,pack-b \
  --impact-baseline-packs pack-a \
  --idempotency-key review-2026-07-14

openoyster deliberate show RUN_ID
openoyster deliberate dossier RUN_ID --format json|markdown
openoyster deliberate replay RUN_ID
openoyster deliberate impact RUN_ID
openoyster deliberate knowledge-requests RUN_ID
```

API:

- `POST /v1/deliberations`
- `GET /v1/deliberations/{id}`
- `GET /v1/deliberations/{id}/dossier`
- `POST /v1/deliberations/{id}/replay`
- `GET /v1/deliberations/{id}/cognitive-impact`
- `GET /v1/deliberations/{id}/knowledge-requests`

모든 D1 endpoint는 API key가 필요하다. POST create는 `Idempotency-Key`가 필수다.
응답은 raw Pack body, 전체 prompt, server path, storage URI를 포함하지 않는다.

CLI exit code:

- `0`: selection 또는 abstention으로 완료
- `1`: database, indeterminate, unrecoverable contract failure
- `2`: Mission, scope, Pack profile, argument 오류

## 12. 자원 한도

- evidence snapshot 최대 24개
- 전체 prompt 최대 100,000 characters
- run당 LLM provider attempt 최대 5회
- option 최대 5개
- belief 최대 20개
- option당 scenario 최대 3개
- JSON repair와 stage retry 없음
- D1 호출은 provider transport retry를 사용하지 않음

## 13. 필수 TDD 시나리오

- happy path가 정확히 5회 LLM 호출하고 모든 산출물을 저장한다.
- evidence 없음은 0회 호출과 완료된 abstention을 만든다.
- unknown citation, scope 밖 Pack, quote mismatch, pointer mismatch는 artifact를 만들지 않는다.
- Mission prose만으로 factual assertion을 검증할 수 없다.
- scope freeze 후 active Pack 변경이 run에 영향을 주지 않는다.
- 같은 idempotency key는 하나의 run만 만든다.
- post-call crash는 자동 재호출 없이 `indeterminate`가 된다.
- critic non-pass는 항상 selection을 막는다.
- replay가 dossier/gate hash를 재현한다.
- 저장 데이터 변조는 replay mismatch가 된다.
- 동일 impact scope는 모든 근거를 retained로 분류한다.
- baseline subset은 partial/lost support를 정확히 분류한다.
- 원본 Pack digest는 실행 전후 동일하다.
- SQLite migration upgrade/downgrade가 동작한다.
- PostgreSQL과 호환되는 schema construct만 사용한다.
- CLI/API는 인증, 멱등성, 응답 sanitization을 지킨다.

## 14. 구현 품질 gate

- focused D1 tests 통과
- 기존 Pack tests 통과
- Ruff 통과
- mypy 통과
- 전체 pytest 통과
- sdist와 wheel build 통과
- `git diff --check` 통과
- Claude Opus 최종 코드 리뷰에서 Critical 0, Major 0
- 리뷰 발견사항 수정 후 전체 gate 재실행

## 15. 남는 한계

- exact anchor는 provenance를 증명하지만 의미적 entailment를 수학적으로 보장하지 않는다.
- compatible Pack 품질은 strict Pack보다 약하므로 opt-in이다.
- 동기식 5-call API는 일반 reverse proxy timeout을 넘을 수 있다.
- Pack DB row 불변성은 서비스 계약이며 DB trigger가 아니다. snapshot hash로 사후 변조를 탐지한다.
