# Flip Condition Monitoring D3 요구사항

상태: 요구사항 초안 (구현 전)
작성일: 2026-07-16

## 1. 제품 정의

Flip Condition Monitoring D3는 완료된 deliberation run의 flip condition을 감시
가능한 1급 객체로 승격한다. 새 OpenCrab Pack이 설치될 때마다 시스템이 저장된
flip condition을 결정적으로 스캔해 "촉발 후보"를 만들고 사용자에게 알린다.

D3는 결정을 자동으로 뒤집지 않는다. 재숙의(`deliberate continue` 또는 새 run)는
항상 사용자가 명시적으로 시작한다. D3의 산출물은 알림과 제안이며, 실행이 아니다.

한 문장으로: **dossier를 일회성 문서에서 지속 감시되는 결정으로 바꾼다.**

## 2. 입력 경계

- 감시 입력은 설치된 OpenCrab Pack evidence뿐이다. 웹 검색, 모델 사전 지식,
  사용자 채팅은 촉발 근거가 될 수 없다.
- 감시 대상은 `status=completed`인 run의 flip condition artifact뿐이다.
- Pack 설치는 기존 admission 경로를 그대로 사용한다. D3는 Pack을 만들거나
  갱신하지 않는다.

## 3. 구조화 Flip Predicate 계약

기존 flip condition은 산문(NarrativeAssertion)이다. D3는 decision stage 계약에
선택 필드 `predicate`를 추가한다.

```json
{
  "local_key": "flip_1",
  "condition": { "text": "...", "classification": "proposal", ... },
  "predicate": {
    "query_terms": ["복구 시간", "recovery time"],
    "expected_change": "new_evidence_matching_terms",
    "note": "예상 복구 시간에 대한 새 근거가 들어오면 재검토"
  }
}
```

- `predicate`는 선택이다. 없으면 해당 flip condition은 감시되지 않고 dossier에만
  남는다 (기존 동작 보존).
- `query_terms`는 FTS 질의로 결정적으로 실행 가능해야 한다.
- predicate는 LLM이 생성하지만, 저장 전에 기존 stage gate와 동일하게 검증한다
  (빈 term 거부, term 수 상한).

## 4. 감시 흐름

```text
새 Pack install 완료
  ↓
FlipWatch 스캔: 감시 중인 predicate의 query_terms를
새 install의 evidence에 대해 FTS 실행 (결정적)
  ↓ 매치 있음
FlipTrigger 후보 생성 (status=candidate, 매치 evidence ID 기록)
  ↓ (선택) 확인 stage: 유계 LLM 판정 — 후보 evidence가 실제로
  condition 텍스트와 관련되는지. 인용 게이트 동일 적용.
  ↓
사용자 알림 (CLI/API 조회 + 이벤트 로그)
  ↓
사용자가 confirm(→ deliberate continue/new run 수동 실행) 또는 dismiss
```

- 결정적 스캔이 1차 필터다. LLM 확인은 설정으로 끌 수 있고, 꺼져 있으면
  candidate 상태까지만 만든다.
- LLM 확인 stage의 출력도 quote/pointer anchor를 요구하며, anchor가 검증
  실패하면 candidate는 승격되지 않는다.

## 5. 데이터 계약

- `deliberation_flip_watches`: run_id, flip_local_key, predicate_json,
  status(`watching|triggered_candidate|confirmed|dismissed|expired`),
  created_at, updated_at.
- `deliberation_flip_triggers`: watch_id, pack_install_id,
  matched_evidence_ids, confirmation(`none|llm_supported|llm_unsupported`),
  confirmation_anchors, created_at.
- 두 테이블 모두 immutable append + status 전이만 허용. 삭제 없음.

## 6. CLI/API 계약

```text
openoyster deliberate watch list [--status ...]
openoyster deliberate watch scan [--pack-install ID]   # 수동 스캔
openoyster deliberate watch show WATCH_ID
openoyster deliberate watch dismiss WATCH_ID --reason TEXT
```

- `GET /v1/deliberations/{id}/flip-watches`
- `GET /v1/flip-triggers?status=candidate`
- `POST /v1/flip-watches/{id}/dismiss`

API 응답은 D1 sanitizer를 통과하며 raw Pack body, prompt, 경로를 포함하지
않는다. trigger 알림은 기존 events 테이블에 `flip_trigger_candidate` kind로
기록한다.

## 7. 안전 원칙

- 읽기(스캔)와 해석(LLM 확인)과 제안(알림)만 자동화한다.
- 재숙의, Pack 갱신, 외부 전송은 자동화하지 않는다.
- 스캔은 새 install 이벤트당 1회이며 감시 predicate 수에 비례하는 상한을 둔다
  (기본 예: watch 200개 초과 시 오래된 것부터 expired 처리 + 경고).

## 8. 범위 밖

- 자동 `deliberate continue` 실행
- Pack content diff 기반 촉발 (D2와 동일하게 citation/FTS scope만)
- 외부 알림 채널 (이벤트 로그·CLI·API 조회까지만; 채널 연동은 별도)
- 시계열 예측, 확률 추정

## 9. 수용 기준

- predicate 없는 기존 run·dossier는 동작이 변하지 않는다.
- 새 Pack 설치 후 매치되는 watching predicate가 candidate로 전이되고 매치
  evidence ID가 기록된다.
- 매치 없는 설치는 어떤 상태도 바꾸지 않는다.
- LLM 확인이 꺼진 설정에서 LLM 호출이 0회다.
- LLM 확인의 anchor 검증 실패는 승격을 막고 실패 코드를 남긴다.
- dismiss/confirm 전이는 감사 가능하게 기록된다.
- replay는 D3 테이블을 변경하지 않는다.
