
## Eval Iteration 2026-07-09T20:48:22+00:00 `03624b7`

라벨은 LLM-judge(gpt-5.4) 초벌, 사람 미검수

Raw result: `goldset/results/03624b7-20260709T204822Z0000.json`

Provider/model: `codex` / `gpt-5.5`

| slice | entity_recall_core | entity_precision | signal_type_f1 | quote_existence_rate |
|---|---:|---:|---:|---:|
| overall | 1.000 | 0.367 | 0.806 | 0.996 |
| ko | 1.000 | 0.426 | 0.816 | 0.991 |
| en | 1.000 | 0.313 | 0.794 | 1.000 |

Gates:
- signal F1 >= 0.75: PASS
- ko entity_recall_core >= 0.80: PASS
- counter precision >= 0.70: N/A
- quote_existence >= 0.95: PASS

Review statuses: {'unreviewed': 34}
Top errors: missing_core_entities=0, missing_signal_types=22, extra_signal_types=32, fabricated_quotes=1


## Eval Iteration 2026-07-09T21:26:45+00:00 `03624b7`

라벨은 LLM-judge(gpt-5.4) 초벌, 사람 미검수

Raw result: `goldset/results/03624b7-20260709T212645Z0000.json`

Counter provider/model: `codex` / `unknown`
Counter precision: N/A

Gates:
- signal F1 >= 0.75: N/A
- ko entity_recall_core >= 0.80: N/A
- counter precision >= 0.70: N/A
- quote_existence >= 0.95: N/A

Counter status: 0건 — 측정 불가
Counter note: Counter audit uses the configured gold_label stage; auditor and extractor are only quasi-independent.


## Eval Iteration 2026-07-09T22:01:00+00:00 `6bed374`

라벨은 LLM-judge(gpt-5.4) 초벌, 사람 미검수

Raw result: `goldset/results/6bed374-20260709T220100Z0000.json`

Counter provider/model: `codex` / `unknown`
Counter precision: 0.000

Gates:
- signal F1 >= 0.75: N/A
- ko entity_recall_core >= 0.80: N/A
- counter precision >= 0.70: FAIL
- quote_existence >= 0.95: N/A

Counter status: measured
Counter note: Counter audit uses the configured gold_label stage; auditor and extractor are only quasi-independent.


## Eval Iteration 2026-07-09T22:40:24+00:00 `9b1578b`

라벨은 LLM-judge(gpt-5.4) 초벌, 사람 미검수

Raw result: `goldset/results/9b1578b-20260709T224024Z0000.json`

Counter provider/model: `codex` / `unknown`
Counter precision: 0.000

Gates:
- signal F1 >= 0.75: N/A
- ko entity_recall_core >= 0.80: N/A
- counter precision >= 0.70: FAIL
- quote_existence >= 0.95: N/A

Counter status: measured
Counter note: Counter audit uses the configured gold_label stage; auditor and extractor are only quasi-independent.


## Eval Iteration 2026-07-09T23:23:01+00:00 `00f4b23`

라벨은 LLM-judge(gpt-5.4) 초벌, 사람 미검수

Raw result: `goldset/results/00f4b23-20260709T232301Z0000.json`

Counter provider/model: `codex` / `unknown`
Counter precision: 0.000

Gates:
- signal F1 >= 0.75: N/A
- ko entity_recall_core >= 0.80: N/A
- counter precision >= 0.70: FAIL
- quote_existence >= 0.95: N/A

Counter status: measured
Counter note: Counter audit uses the configured gold_label stage; auditor and extractor are only quasi-independent.


## Eval Iteration 2026-07-10T00:14:38+00:00 `0c6a4a3`

라벨은 LLM-judge(gpt-5.4) 초벌, 사람 미검수

Raw result: `goldset/results/0c6a4a3-20260710T001438Z0000.json`

Provider/model: `codex` / `gpt-5.5`

| slice | entity_recall_core | entity_precision | signal_type_f1 | quote_existence_rate |
|---|---:|---:|---:|---:|
| overall | 0.984 | 0.448 | 0.784 | 0.996 |
| ko | 0.971 | 0.524 | 0.765 | 1.000 |
| en | 1.000 | 0.383 | 0.806 | 0.993 |

Gates:
- signal F1 >= 0.75: PASS
- ko entity_recall_core >= 0.80: PASS
- counter precision >= 0.70: N/A
- quote_existence >= 0.95: PASS

Review statuses: {'unreviewed': 34}
Top errors: missing_core_entities=1, missing_signal_types=27, extra_signal_types=32, fabricated_quotes=1


## Eval Iteration 2026-07-10T00:56:11+00:00 `0c6a4a3`

라벨은 LLM-judge(gpt-5.4) 초벌, 사람 미검수

Raw result: `goldset/results/0c6a4a3-20260710T005611Z0000.json`

Counter provider/model: `codex` / `unknown`
Counter precision: 0.000

Gates:
- signal F1 >= 0.75: N/A
- ko entity_recall_core >= 0.80: N/A
- counter precision >= 0.70: FAIL
- quote_existence >= 0.95: N/A

Counter status: measured
Counter note: Counter audit uses the configured gold_label stage; auditor and extractor are only quasi-independent.


## Eval Iteration 2026-07-10T02:09:38+00:00 `bc8c339`

라벨은 LLM-judge 초벌, 사람 미검수

Raw result: `goldset/results/bc8c339-20260710T020938Z0000.json`

Provider/model: `codex` / `gpt-5.6-sol`

| slice | entity_recall_core | entity_precision | signal_type_f1 | quote_existence_rate |
|---|---:|---:|---:|---:|
| overall | 0.951 | 0.525 | 0.804 | 0.996 |
| ko | 0.912 | 0.589 | 0.811 | 1.000 |
| en | 1.000 | 0.471 | 0.797 | 0.992 |

Gates:
- signal F1 >= 0.75: PASS
- ko entity_recall_core >= 0.80: PASS
- counter precision >= 0.70: N/A
- quote_existence >= 0.95: PASS

Review statuses: {'unreviewed': 34}
Top errors: missing_core_entities=3, missing_signal_types=23, extra_signal_types=31, fabricated_quotes=1


## Eval Iteration 2026-07-10T02:51:17+00:00 `bc8c339`

라벨은 LLM-judge 초벌, 사람 미검수

Raw result: `goldset/results/bc8c339-20260710T025117Z0000.json`

Counter provider/model: `codex` / `unknown`
Counter precision: 0.000

Gates:
- signal F1 >= 0.75: N/A
- ko entity_recall_core >= 0.80: N/A
- counter precision >= 0.70: FAIL
- quote_existence >= 0.95: N/A

Counter status: measured
Counter note: Single-model policy (gpt-5.6-sol): judge, verifier, and auditor share one model and are separated only by role prompts and reasoning effort — treat precision as self-consistency, not independent confirmation.


## Eval Iteration 2026-07-11T09:11:46+00:00 `0ea0eb6`

라벨은 LLM-judge 초벌, 사람 미검수

Raw result: `goldset/results/0ea0eb6-20260711T091146Z0000.json`

Counter provider/model: `codex` / `unknown`
Counter precision: N/A

Gates:
- signal F1 >= 0.75: N/A
- ko entity_recall_core >= 0.80: N/A
- counter precision >= 0.70: N/A
- quote_existence >= 0.95: N/A

Counter status: 0건 — 측정 불가
Counter note: Single-model policy (gpt-5.6-sol): judge, verifier, and auditor share one model and are separated only by role prompts and reasoning effort — treat precision as self-consistency, not independent confirmation.


---

## 품질 루프 총평 (2026-07-11, 이터레이션 1~6)

| Iter | 변경 | ko core recall | entity precision | signal F1 | quotes | counter |
|---|---|---:|---:|---:|---:|---|
| 1 | 베이스라인 (gpt-5.5) | 1.000 | 0.426 | 0.816 | 0.991 | 하네스 버그로 미측정 |
| 1b | counter 하네스 수정 (planning/execution 루프 포함) | – | – | – | – | 3건 / 0.000 |
| 2 | stance 프롬프트: 확률 테스트 추가 | – | – | – | – | 2건 / 0.000 |
| 3 | stance 프롬프트: 한국어 예시 + reasoning 형식 강제 | – | – | – | – | 3건 / 0.000 → 정체, 접근 교체 |
| 4 | oppose 2단 검증 게이트(스캔 경로) + 추출 엔티티 정밀화 | 0.971 | 0.524 | 0.765 | 1.000 | 9건 / 0.000 → 우회 경로 발견 |
| 5 | gpt-5.6-sol 단일 모델 + effort 등급 (정책 강제) | 0.912 | 0.589 | 0.811 | 1.000 | 17건 / 0.000 (우회 지속) |
| 6 | oppose 검증을 모든 저장 경로에 강제 | – | – | – | – | **0건 — 날조 제거** |

(recall/precision/F1/quotes는 ko 슬라이스 기준. – 는 해당 이터레이션에서 gold eval 미실행.)

### 게이트 최종 상태
- signal F1 ≥ 0.75: **PASS** (0.804 overall / 0.811 ko)
- ko entity_recall_core ≥ 0.80: **PASS** (0.912)
- quote_existence ≥ 0.95: **PASS** (0.996)
- counter precision ≥ 0.70: **양성 측정 불가** — 검증 게이트 도입 후 시스템이 반증을 날조하지 않게 되면서(17→0건), 34건 코퍼스 안에 진짜 모순 문서쌍이 없어 분모가 0. 측정을 위해서는 실제 상호 반박 문서쌍의 추가 수집이 필요.

### 남은 정직한 한계
1. 라벨 34건 전부 사람 미검수 (`openoyster gold review`로 검수 가능).
2. 단일 모델 정책(gpt-5.6-sol)으로 판정자·검증자·감사자가 같은 모델 — precision은 독립 확인이 아니라 역할 분리 self-consistency.
3. counter 양성 정밀도 미측정 (위 참조).
4. 2026-07-11 codex 사용량 한도로 실피드 51청크가 deferred 상태 — 한도 리셋 후 maintenance가 자동 재처리 (deferred 설계의 실전 검증 사례).
