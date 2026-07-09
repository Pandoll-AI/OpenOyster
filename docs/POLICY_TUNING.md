# OpenOyster Policy and Hyperparameter Tuning

## 1. Policy model

Operating parameters are stored as versioned JSON in `policies`. A policy can be `active`, `candidate`, `shadow`, or `archived`. Every trigger decision records the policy version, features, score, threshold, and later outcome label where available.

Policy is not code self-modification. It is a bounded, inspectable control surface.

## 2. Policy sections

### Retrieval

| Key | Purpose |
|---|---|
| `mode` | `auto`, `lexical`, or `postgres_full_text`. `auto` uses SQLite FTS5 on SQLite, PostgreSQL full text on PostgreSQL, and lexical fallback only when the indexed path is unavailable. |
| `top_k` | Maximum returned chunk hits. |
| `max_scan_chunks` | Bounded retrieval scan. |
| `recency_weight` | Preference for newer documents. |
| `minimum_similarity` | Reject weak retrieval hits after scoring. |
| `source_diversity_cap` | Optional per-source cap before filling remaining result slots. `0` disables the cap. |

### Extraction

Controls chunk size/overlap and maximum claims/signals/hypotheses per chunk. These affect cost, context coherence, and duplicate rate. Overlap must be smaller than chunk size.

### Trigger

```text
score = weighted(novelty, impact, contradiction, evidence_gap, staleness)
```

`fire_threshold` controls work creation. `high_alert_threshold` controls approval-required alert candidates. Weight changes alter prioritisation; they do not improve truth by themselves.

### Hypothesis

Controls LLM merge-candidate count, evidence thresholds, staleness, maturity requirements, source diversity, and confidence priors.

### Planning/execution

Controls task depth/count, exploration, retries, daily cost limit, tool-call bounds, and candidate evidence count. Evidence scans retrieve by the hypothesis claim, then use the stance judge to separate support, opposition, and unrelated chunks. The default implementation uses internal zero-cost tools; cost fields are already persisted for remote tools.

### Utilisation/evaluation

Controls when grounded hypotheses become memos and how evidence quality and downstream feedback are interpreted.

### Optimisation

Controls label minimum, replay window, mutation step, minimum improvement, shadow-label minimum, candidate expiry, promotion permission, and cooldown.

### Meta-review/maintenance/safety

Controls review cadence, drift thresholds, source concentration, adoption/failure limits, retry timing, maximum failures, maximum policy delta, and approval requirements.

## 3. Manual policy workflow

Create a small YAML override:

```yaml
trigger:
  fire_threshold: 0.55
planning:
  max_tasks_per_cycle: 10
optimisation:
  allow_auto_promotion: false
```

Validate and store it:

```bash
openoyster policy create policy.yaml --version cautious-001
openoyster policy list
```

Review the merged policy, then promote:

```bash
openoyster policy promote POLICY_ID
```

`--activate` exists for controlled environments, but candidate-first review is recommended.

## 4. Automatic optimisation algorithm

OpenOyster performs a deliberately narrow search.

### Replay stage

1. Load labelled trigger decision traces from the policy window.
2. Compute baseline binary metrics and utility.
3. Generate one-parameter candidates around `fire_threshold`, `impact_weight`, `contradiction_weight`, and `evidence_gap_weight`.
4. Clamp each change and cap mutation size with `safety.max_policy_change_per_run`.
5. Validate candidate policy.
6. Replay the same labelled traces.
7. Reject unless improvement exceeds `optimisation.min_improvement`.
8. Persist the best candidate as `shadow` with baseline label IDs and an experiment record.

### Shadow stage

1. Wait for labelled traces not used in replay.
2. Require `shadow_min_new_labels`.
3. Compare base and candidate on only the new labels.
4. Require minimum improvement, auto-promotion enabled, and the safety label minimum.
5. Promote or archive; persist metrics and emit an event.

This prevents immediate promotion on the same sample used to select a candidate. It does not eliminate small-sample bias, label leakage, non-stationarity, or poor objectives.

## 5. Labels and objective quality

The current high-value labels come from explicit artifact feedback. Rule evaluations are useful diagnostics but are not sufficient evidence of downstream value. A deployment should add outcome labels such as:

- artifact used in a decision;
- recommendation accepted/rejected;
- forecast later verified/falsified;
- investigation saved time;
- alert was actionable/noisy;
- missed signal discovered retrospectively.

A bad objective makes self-tuning confidently bad. Do not optimise output volume, prose length, or self-rated fluency.

## 6. Safe tuning procedure

1. Freeze mission and source universe for the experiment window.
2. Collect enough explicit labels.
3. Inspect class balance and source concentration.
4. Disable automatic promotion for initial deployments.
5. Run candidate replay and review mutation rationale.
6. Allow shadow operation without changing active behaviour.
7. Review new-label performance.
8. Promote manually.
9. Monitor alert/task volume, cost, adoption, and missed signals.
10. Roll back on degradation and retain experiment records.

## 7. Common failure modes

### Lower threshold appears “better” because more artifacts receive feedback

This is selection bias. Untriggered cases need retrospective labels or controlled sampling.

### The optimiser oscillates

Increase cooldown/minimum labels, reduce mutation step, or add hysteresis. Check for source drift before changing optimiser mechanics.

### Weight changes do nothing

Verify the relevant feature varies and the policy key is actually consumed. Dead knobs are a defect, not a tuning opportunity.

### Shadow never completes

Generate new explicit feedback after shadow start. Baseline label IDs are excluded intentionally.

### Candidate improves utility but worsens operational cost

The current binary utility is limited. Add a multi-objective evaluator or hard constraints before enabling automatic promotion for expensive tools.

## 8. Why the score is not reinforcement learning

The optimiser is constrained policy search over labelled historical decisions. It does not update model weights, autonomously rewrite code, or explore arbitrary actions. This is intentional: policy evolution is easier to audit, test, replay, and roll back.
