# Final Acceptance — Deliberation Integrity Hardening

acceptance_id: 2026-07-16-deliberation-integrity-hardening
root_orchestrator: main session (Fable), authorised by user (Pandoll-AI)
base_commit: bc25146
final_decision: **ACCEPTED** (uncommitted; awaiting user commit approval)

## Scope

Adversarial review of Autonomous Deliberation D1 and Decision Continuity D2,
followed by fixes for every confirmed defect and a set of bounded features the
review motivated. Two independent review rounds plus one verification round were
run by Codex (gpt-5.6-sol, high effort, read-only); the orchestrator reproduced
the security- and integrity-critical findings directly.

## Writer history (one unit = one writer, sequential inserts)

| Unit | Content | Writer |
|---|---|---|
| U1 | Fail-closed selection gate; quote length floor; adversarial gate tests | grok-4.5/xhigh |
| U2 | Full assertion/anchor persistence (visitor + parity, role citations, mig 0006); unclaimed-KR preservation; scope-shrink flag | grok-4.5/xhigh |
| U3 | Pydantic error sanitize; idempotency fingerprint + atomic race (mig 0007); new-pack + parent-integrity checks; real model/effort provenance; one bounded retry | grok-4.5/xhigh |
| U4 | No-evidence cause split (`pack_has_no_evidence` vs `no_match_in_pack_evidence`) | grok-4.5/xhigh |
| U5a | KR verifier registry (honest method labels); machine-readable KR export | grok-4.5/xhigh |
| U5b | Optional second critic (default off); replay recomputes impact/transition; golden replay test | grok-4.5/xhigh |
| U6a | Round-2 fixes: critic2 provider_stage split; scenario-complete alternative counting; secondary persist + KR merge; provider error sanitize; NFKC quote floor; constraint pointer alignment | grok-4.5/xhigh |
| U6b | Round-2 fixes: immutable fulfilled-keys column (mig 0008) breaks replay circularity; version-aware replay; KR verification bound to child-cited evidence | grok-4.5/xhigh |
| U7 | Verification-round fixes: provider/factory exception boundaries; atomic conditional fingerprint fill; distrust legacy transition `claimed`; self-digest before version skip; gate-error code-only disclosure; downgrade documentation | grok-4.5/xhigh |
| docs | README, API_REFERENCE, CHANGELOG, D2 limits, two draft requirement docs, this record | Root (Fable) |

## Review rounds

| Round | Reviewer | Codex session | Result |
|---|---|---|---|
| 1 (find) | Codex adversarial | 019f6873 | 8 findings on original D2 |
| 2 (find on fixes) | Codex adversarial | 019f68bc | 8 major + 2 minor on fix wave |
| 3 (verify) | Codex verification | 019f68e0 | NEEDS_REWORK: 5 CLOSED, 5 PARTIAL, 1 regression |
| 4 (re-verify) | Codex verification | 019f68f9 | **MERGE_OK: 0 Critical, 0 Major, all CLOSED, no regressions** |

Orchestrator direct reproductions: Pydantic input leak (CONFIRMED → NOT
REPRODUCED after fix); selection-gate fake-alternative (#2); KR unrelated-evidence
verification (#8); gate-error model-text disclosure (#7); atomic fingerprint fill.

## Gate results

- make check: **PASS** — ruff + mypy + 204 tests + build, exit 0 (155 → 204 tests; +49, 0 failing).
- security: Pydantic and provider/gate disclosure paths closed and reproduced clean.
- documentation: README/API_REFERENCE/CHANGELOG/D2 updated; links resolve.
- source Pack digest: not applicable (no Pack fixture source mutation; new empty-evidence fixture added under tests/).
- final review: Codex `MERGE_OK`, Critical/Major 0.

## Honest residual limitations (documented, not defects)

- Knowledge Request verification binds to child-cited evidence but does not prove
  semantic relevance to the question (single-model constraint) — see D2 §8.1.
- Legacy pre-migration continuation runs cannot recover trustworthy fulfilled keys;
  replay marks their transition `legacy_fulfilled_keys_unrecoverable` rather than
  claiming a match.
- Lazy fingerprint fill accepts the first post-upgrade visitor's fingerprint once
  for legacy NULL rows; strict afterward.
- Second critic remains self-consistency unless configured with a distinct provider.

## Commit status

commit_push_deploy: **not_performed** — awaiting explicit user approval.
