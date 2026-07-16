# Final Acceptance — Decision Lifecycle Wave

acceptance_id: 2026-07-16-decision-lifecycle
root_orchestrator: main session (Fable), authorised by user (Pandoll-AI)
base_commit: 82d759b (merged integrity-hardening tip)
final_decision: **ACCEPTED** — user approved push + PR + merge

## Scope

Extends OpenOyster from a deliberation engine toward a decision lifecycle system
(believe → decide/abstain → targeted learning → watch → record outcome →
calibrate), following a vision-and-gap analysis. Implemented across four waves
(A eyes, B lifecycle, C learning loop, D identity), then hardened across three
Codex adversarial-verification rounds plus a self-implemented final fix.

## Feature commits

| Commit | Content |
|---|---|
| 0e81f55 | Cross-language retrieval: manifest hints + bounded query expansion |
| 97d56c1 | Deliberation gold set v1 (verdict-known scenario benchmark) |
| 156c306 | Flip Condition Monitoring D3 (watched decisions) |
| 85de0d1 | Decision Outcome Ledger + calibration |
| ad1a1fe | First-class Charters |
| 1558009 | Cross-vendor second critic (claude-cli) |
| 4f06653 | Roadmap reframed around the decision lifecycle |

## Rework commits (Codex-driven)

| Commit | Content |
|---|---|
| 84986b6 | RW-1: budget/outcome/calibration/charter integrity |
| 74abd63 | RW-2: flip matching, install-scan, trigger races |
| 251368b | RW-3: claude-cli isolation, retrieval-trace disclosure |
| 7c03973 | RW-4: outcome global-unique, budget split, post-commit scan, md renderer |
| ab0134a | RW-5: restore install_pack flush-only transaction contract |

## Review rounds

| Round | Reviewer | Session | Result |
|---|---|---|---|
| 1 (find) | Codex adversarial | 019f6952 | 7 major + 5 minor |
| 2 (verify) | Codex verification | 019f6979 | 5 CLOSED + refinements → 7 major open |
| 3 (verify) | Codex verification | 019f6991 | 3 CLOSED + 1 harmless PARTIAL + 1 new regression |
| final | orchestrator direct | — | regression fixed (RW-5) + reproduced; make check green |

Orchestrator direct reproductions: flip predicate accuracy, strict charter int,
outcome global-unique cross-run block, budget split, and the install_pack
transaction-ownership regression (unrelated pending row not committed).

## Gate results

- make check: **PASS** — ruff + mypy + 282 tests + build, exit 0 (204 → 282; +78, 0 failing).
- security: Claude CLI critic runs isolated (no tools/hooks/MCP/session, allowlisted env),
  retrieval_trace reduced to digests/counts.
- documentation: README, README-en, API reference, CHANGELOG, GOAL_ROADMAP, D3/ledger drafts.
- final review: three Codex rounds; last open item was a self-fixed low-risk revert,
  directly reproduced.

## Honest residual notes

- Item 4 from round 3 (raw query in Markdown) was assessed as a non-issue: the query
  appears only as `mission.decision_question` in the by-design, authenticated Mission
  section; the retrieval_trace itself carries only digests/counts.
- The final RW-5 revert was self-implemented (Grok CLI was transiently unavailable) and
  verified by direct reproduction rather than a fourth full Codex round, per user request.
- Deliberation gold-set and calibration numbers require a real-model run; CI uses stubs.
- Gold labels (34) remain human-unreviewed; counter contradiction corpus still absent.

## Commit status

commit_push_deploy: **committed on agent/decision-lifecycle; pushed and merged to main
with explicit user approval on 2026-07-16.**
