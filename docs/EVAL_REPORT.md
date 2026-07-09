
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

