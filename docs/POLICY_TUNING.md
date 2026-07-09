# OpenOyster Policy Management

## 1. Policy model

Operating parameters are stored as versioned JSON in `policies`. A policy can be `active`, `candidate`, `shadow`, or `archived`. Trigger decisions record the policy version, features, score, threshold, and later outcome label where available.

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

Controls chunk size, overlap, and maximum claims/signals/hypotheses per chunk. These settings affect cost, context coherence, and duplicate rate. Overlap must be smaller than chunk size.

### Trigger

```text
score = weighted(novelty, impact, contradiction, evidence_gap, staleness)
```

`fire_threshold` controls work creation. `high_alert_threshold` controls approval-required alert candidates. Weight changes alter prioritisation; they do not improve truth by themselves.

### Hypothesis

Controls LLM merge-candidate count, evidence thresholds, staleness, maturity requirements, source diversity, and confidence priors.

### Planning and execution

Controls task depth/count, exploration, retries, daily cost limit, tool-call bounds, and candidate evidence count. Evidence scans retrieve by hypothesis claim, then use the stance judge to separate support, opposition, and unrelated chunks.

### Utilisation and evaluation

Controls when grounded hypotheses become memos and how evidence quality and downstream feedback are interpreted.

## 3. Manual policy workflow

Create a small YAML override:

```yaml
trigger:
  fire_threshold: 0.55
planning:
  max_tasks_per_cycle: 10
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

## 4. Evidence for policy changes

A policy change should have an explicit reason and a bounded proof:

- a fixture or gold-set run for extraction/retrieval changes;
- an event or loop test for cursor, retry, or planning changes;
- human feedback and artifact review for trigger threshold changes;
- before/after counts for task volume, artifact adoption, and failed chunks.

Do not tune for output volume, prose length, or model self-rating. These objectives can make the system confidently worse.

## 5. Safe tuning procedure

1. Freeze the source universe for the experiment window.
2. Collect enough explicit labels or fixture outcomes.
3. Inspect class balance and source concentration.
4. Create a candidate policy with the smallest useful override.
5. Run the relevant evaluation or replay manually.
6. Review task volume, cost, adoption, and missed signals.
7. Promote manually only after review.
8. Roll back on degradation and retain the rejected policy for audit.

## 6. Common failure modes

### Lower threshold appears better because more artifacts receive feedback

This is selection bias. Untriggered cases need retrospective labels or controlled sampling.

### Weight changes do nothing

Verify the relevant feature varies and the policy key is actually consumed. Dead knobs are a defect, not a tuning opportunity.

### Candidate improves one metric but worsens cost

Add hard constraints or a broader evaluation before promotion.

## 7. Why this is not reinforcement learning

Policy management changes persisted thresholds and weights. It does not update model weights, autonomously rewrite code, or explore arbitrary actions. This is intentional: policy evolution is easier to audit, test, replay, and roll back.
