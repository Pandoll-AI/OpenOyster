# Decision Continuity D2 요구사항

상태: 구현 기준선
작성일: 2026-07-14

## 1. 제품 정의

Decision Continuity D2는 완료된 D1 기권 run을, 저장된 Knowledge Request가 충족된
뒤 새로 설치된 OpenCrab Pack으로 이어서 숙의하는 기능이다. OpenOyster의 사실 입력은
사용자가 명시한 이미 설치된 OpenCrab Pack뿐이다. OpenOyster는 Pack을 만들거나
갱신하지 않고, 외부 사실을 발견하지 않는다.

첫 실행이 근거 부족으로 기권한 뒤, OpenCrab 또는 사용자가 새 Pack을 준비하고 해당
부모 Knowledge Request의 `local_key`를 명시해 continuation을 요청한다. 결과는 새
child run이며, 부모 Mission snapshot을 동결하고 `parent_run_id`로 연결한다.

## 2. Continuation 입력 경계

continuation이 허용되는 부모는 다음을 모두 만족해야 한다.

- 존재하는 run이다.
- `status`가 `completed`이다.
- `outcome`이 `abstain`이다.
- `knowledge_requests` artifact가 저장되어 있다.
- 요청한 모든 fulfilled key가 부모 artifact의 Knowledge Request `local_key`다.

요청자는 하나 이상의 새 installed Pack ID와 하나 이상의 fulfilled Knowledge Request
local key를 명시한다. 선택적으로 impact baseline Pack ID와
`allow_compatible_packs`를 지정한다. 부모 Mission은 새 요청으로 수정하지 않는다.

## 3. 인지 전환 계약

child run에는 `method: cognitive_transition_v2`인 불변 artifact가 하나 저장된다.
전환은 Pack 바이트, record, revision, file의 diff가 아니다. 부모·자식 run에 저장된
artifact와 citation scope를 비교해 다음 필드를 제공한다.

- `claimed_knowledge_requests`
- `verified_fulfilled_knowledge_requests`
- `unverified_claimed_knowledge_requests`
- `fulfilled_knowledge_requests` (verified subset compatibility alias)
- `belief_changes`
- `option_changes`
- `critic_verdict_change`
- `decision_change`
- `citation_scope_changes`
- `remaining_knowledge_requests`

`citation_scope_changes`는 parent/child의 global evidence ID와 added/removed ID를
보여 준다. 전환은 새 Pack이 어떤 사실을 “발견”했는지 추론하지 않으며, 저장된
숙의 결과가 어떻게 달라졌는지를 설명한다.

## 4. 사용자 가치 예시

첫 실행이 “현장 복구 시간”에 대한 Pack evidence가 없어 `abstain`했다고 하자. 사용자가
OpenCrab에서 그 근거를 담은 새 Pack을 설치하고 `kr_no_evidence`를 fulfilled
key로 지정해 continuation을 실행한다. 사용자는 transition에서 해당 요청의 fulfilled
상태, 새 evidence citation, belief 상태 변화, option의 viable 변화, critic verdict,
decision의 `abstain`→`select` 변화(선택 gate가 통과한 경우), 그리고 남은 요청을
정확히 확인한다. OpenOyster가 Pack을 수정하거나 새 외부 정보를 검색한 결과가 아니다.

## 5. CLI 계약

```text
openoyster deliberate continue PARENT \
  --packs PACK_ID,... \
  --fulfills LOCAL_KEY,... \
  --idempotency-key KEY \
  [--impact-baseline-packs PACK_ID,...] \
  [--allow-compatible-packs]

openoyster deliberate transition CHILD
```

정상 continuation은 child run JSON을 stdout에 출력하고 종료 코드 `0`이다. 입력/continuation
오류는 `{"status":"failed_input","error":{"code":"..."}}` 형태로 출력하고
종료 코드 `2`다. database, provider/runtime, indeterminate 오류는 종료 코드 `1`이다.
provider/runtime 오류는 `failed_execution`이며 epistemic abstention으로 표시하지 않는다.
`transition`은 저장된 sanitized artifact JSON을 출력한다. 없으면
`cognitive_transition_not_found`를 출력하고 종료 코드 `1`이다.

## 6. API 계약

모든 D2 endpoint는 D1과 같은 설정 API key를 요구한다.

- `POST /v1/deliberations/{id}/continue`
  - 필수 header: `Idempotency-Key`
  - request: `packs`, `fulfilled_knowledge_request_keys`, 선택적
    `impact_baseline_packs`, `allow_compatible_packs`
  - 정상: sanitized child run metadata, HTTP `200`
  - continuation 입력 오류: HTTP `422`, `detail.code`
  - provider/runtime으로 child가 `failed_execution`: HTTP `502`, `detail.code`
  - 예기치 않은 실행 예외: HTTP `500`, code `deliberation_execution_failed`
- `GET /v1/deliberations/{id}/transition`
  - 정상: sanitized `cognitive_transition_v2` payload, HTTP `200`
  - artifact 미준비: HTTP `409`, code `cognitive_transition_not_ready`

안정적인 continuation 오류 코드는 다음과 같다.

- `idempotency_key_conflict`
- `idempotency_request_mismatch`
- `parent_run_not_found`
- `parent_run_not_completed_abstain`
- `parent_integrity_mismatch`
- `parent_knowledge_requests_missing`
- `fulfilled_knowledge_request_keys_empty`
- `fulfilled_knowledge_request_keys_unknown`
- `no_new_pack_scope`

## 7. 불변성·멱등성·재생

- 부모 run과 부모 Mission snapshot은 continuation으로 변경하지 않는다.
- child는 부모 ID와 frozen input을 보존한다.
- 같은 idempotency key를 같은 부모에 다시 사용하면 새 실행 없이 기존 상태를 반환한다.
- 같은 key를 다른 부모에 사용하면 `idempotency_key_conflict`다.
- `failed_execution` child도 idempotency key를 소비한다. provider/runtime을 복구한 뒤
  실제로 다시 실행하려면 새 idempotency key를 사용한다.
- cognitive transition artifact는 child당 하나이며 재요청으로 중복 생성하지 않는다.
- `replay`는 저장된 artifact, citation anchor, dossier digest를 재검증하며 LLM을 호출하지 않는다.
- transition은 Pack content diff나 baseline-only 재실행을 제공하지 않는다.

## 8. 범위 밖

다음은 D2에 포함하지 않는다.

- OpenCrab Pack 생성 또는 Pack 업데이트
- Pack content/file/revision diff
- multimodal ingestion
- Neo4j
- 승인 없는 autonomous external execution

## 8.1 검증 한계 (정직 기록)

다음 한계는 의도적으로 남긴 것이며, 구현·리플레이·idempotency가 “증명”하지 않는다.

### Knowledge Request 충족 검증

- `added_cited_evidence_v1`은 child run이 belief/assertion에서 **실제로 인용한**
  global evidence id와 추가 evidence id의 교집합이 비어 있지 않을 때만
  `verified_fulfilled`로 기록한다. 교집합만 `verification_evidence_ids`에 남긴다.
- **질문↔evidence 의미 관련성은 검증하지 않는다.** 단일 모델·결정적 규칙만으로
  “이 증거가 이 질문에 답하는가”를 독립 판정할 수 없으며, 날조하지 않는다.
  따라서 child가 인용한 무관 evidence로도 `evidence:no_evidence` claim이
  verified 될 수 있다. 의미 검증은 사람 또는 별도 판정 체계의 몫이다.

### request_fingerprint lazy backfill

- migration `0008`이 **root run**(`parent_run_id IS NULL`) 중 결정적으로 계산
  가능한 행의 fingerprint만 채운다.
- **legacy continuation run**은 transition `claimed_knowledge_requests`를 신뢰
  출처로 쓰지 않는다. 업그레이드 전 변조된 claimed가 immutable
  `fulfilled_request_keys_json`으로 승격되는 것을 막기 위해, continuation은
  fulfilled keys를 `[]`로 두고 `request_fingerprint`도 NULL로 남겨 lazy-fill에
  넘긴다. 즉 **legacy continuation run은 fulfilled keys를 신뢰 복원할 수 없어
  replay transition 재계산을 skip**한다
  (`recompute_skipped=legacy_fulfilled_keys_unrecoverable`).
- 그래도 `request_fingerprint`가 NULL인 레거시 행은 첫 재사용 시 현재 요청
  fingerprint로 **1회 lazy fill**(원자적 compare-and-set, 호출자 트랜잭션
  소유)한다. 그 첫 방문은 원래 요청과 다를 수 있어도 수용된다(레거시 전용
  완화). 이후 호출부터는 fail-closed로 `idempotency_request_mismatch`를 낸다.

### replay method/version skip

- 재계산 skip 전에 impact/transition/dossier **stored payload self-digest**를
  먼저 대조한다. payload와 저장 digest가 어긋나면 method/version과 무관하게
  mismatch (`cognitive_impact_stored_digest` /
  `cognitive_transition_stored_digest` / `dossier_stored_digest`).
- self-digest가 맞고 impact method가 `citation_scope_projection_v2`가 아니면,
  transition method가 `cognitive_transition_v2`가 아니면 재계산을 건너뛰고
  거짓 mismatch를 내지 않는다.
- 저장된 dossier의 `contract_version`/`prompt_template_version`이 현재 상수와
  다르면 (self-digest 통과 후) dossier digest 엄격 비교를 건너뛴다
  (`dossier_recompute_skipped`).

## 9. 수용 기준

- completed abstaining parent만 continuation할 수 있다.
- 새 Pack ID와 부모에 존재하는 fulfilled Knowledge Request local key를 명시해야 한다.
- child가 부모 Mission을 동결하고 `parent_run_id`를 저장한다.
- `cognitive_transition_v2`가 claimed/verified/unverified와 critic-promoted gap을 저장·조회한다.
- 전환 결과가 belief, option, critic, decision, citation scope의 before/after를 보여 준다.
- 남은 Knowledge Request가 child 결과에 보존된다.
- 같은 parent와 idempotency key의 재요청이 중복 child를 만들지 않는다.
- 지정된 안정 오류 코드, CLI 종료 코드, API HTTP 의미가 유지된다.
- provider/runtime failure가 abstention으로 위장되지 않는다.
- Pack 내용 diff, Pack 수정, 외부 사실 발견이 발생하지 않는다.
