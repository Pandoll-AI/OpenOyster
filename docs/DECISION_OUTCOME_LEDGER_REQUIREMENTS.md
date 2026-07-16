# Decision Outcome Ledger 요구사항

상태: 요구사항 초안 (구현 전)
작성일: 2026-07-16

## 1. 제품 정의

Decision Outcome Ledger는 완료된 deliberation run의 실제 결과를 사용자가
기록하는 append-only 원장이다. 시간이 지나면 시스템의 판단 품질(선택 적중,
기권 적절성, adverse scenario 실현율)을 결정적으로 집계한 calibration 리포트를
제공한다.

GOAL_ROADMAP의 피드백 목표(`decision adopted`, `decision reversed`)를 숙의
런타임에 연결하는 유일한 통로이며, "시간이 지날수록 판단 체계를 개선한다"는
최종 목표 문장의 측정 기반이다.

## 2. 인식론적 경계 (중요)

Outcome 기록은 **사용 기록이지 evidence가 아니다.**

- ledger 항목은 어떤 경로로도 Pack evidence로 승격되지 않는다.
- 이후 run의 belief, option, decision stage 프롬프트에 주입되지 않는다.
- calibration 리포트는 사람이 읽는 산출물이며, 모델의 자동 입력이 아니다.
- 모델 프롬프트·정책을 outcome으로 자동 튜닝하지 않는다. 정책 변경은 항상
  사람이 리포트를 보고 결정한다.

이 경계 덕분에 Pack-only factual boundary는 유지된다.

## 3. 데이터 계약

`deliberation_outcomes` (append-only):

- run_id (FK, 완료 run만)
- outcome_label: `adopted | adopted_modified | not_adopted | reversed | expired`
- scenario_assessments: 선택 option의 expected/adverse scenario별
  `materialized | partially | not_materialized | unknown`
- abstention_assessment (기권 run일 때): `abstention_was_right | information_arrived_late | should_have_selected`
- note: 자유 텍스트 (선택)
- noted_at, noted_by

같은 run에 여러 항목 허용 (시점별 추가 기록). 수정·삭제 없음 — 정정은 새
항목으로 한다.

## 4. Calibration 리포트

결정적 집계만 사용한다 (LLM 호출 없음).

- 결정 run 중 adopted 비율, reversed 비율
- adverse scenario `materialized` 비율 (경고 적중률)
- 기권 run 중 `abstention_was_right` 비율
- Mission charter/도메인별 분해 (mission_charter_id가 있을 때)
- 표본이 적으면 수치 대신 "표본 부족 (n<N)"을 명시한다 — 과신 방지

```text
openoyster deliberate outcome record RUN_ID --label adopted \
  [--scenario expected=materialized --scenario adverse=not_materialized] \
  [--note TEXT]
openoyster deliberate outcome show RUN_ID
openoyster deliberate calibration [--since DATE] [--charter ID]
```

- `POST /v1/deliberations/{id}/outcomes` (API key + Idempotency-Key)
- `GET  /v1/deliberations/{id}/outcomes`
- `GET  /v1/calibration`

## 5. 범위 밖

- 외부 시스템에서 결과 자동 수집 (폴링·웹훅)
- outcome 기반 자동 정책·프롬프트 튜닝
- 점수의 재무적 해석, 벤치마크 비교
- Brier score 등 확률 보정 지표 — belief에 수치 확률이 도입되기 전까지는
  빈도 집계만 제공한다 (도입 시 이 문서를 개정)

## 6. 수용 기준

- ledger 기록이 기존 run·dossier·replay digest를 변경하지 않는다.
- 완료되지 않은 run에는 기록할 수 없다 (안정 오류 코드).
- 같은 Idempotency-Key 재요청은 중복 항목을 만들지 않는다.
- calibration 수치는 같은 ledger 상태에서 항상 동일하다 (결정적).
- 프롬프트 빌더 어디에서도 outcome 데이터를 읽지 않음을 테스트로 고정한다.
