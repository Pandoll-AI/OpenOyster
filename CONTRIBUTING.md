# Contributing to OpenOyster

Thank you for helping make autonomous intelligence systems more inspectable, evidence-aware, and safe. Read `docs/CONTRIBUTOR_MANUAL.md` before opening a substantial pull request.

## Required quality gate

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make check
```

A pull request must:

1. explain the user or reliability problem;
2. include tests for success and failure paths;
3. preserve event, task, run, evidence, and policy auditability;
4. add an Alembic migration for schema changes;
5. document new events, settings, policy keys, endpoints, or connectors;
6. include explicit approval and idempotency boundaries for any external write action;
7. update `CHANGELOG.md` for user-visible behaviour.

## Non-negotiable design rules

- Do not hide cross-loop side effects in direct calls.
- Do not treat generated prose as source evidence.
- Do not add policy knobs that no code path consumes.
- Do not auto-promote policy from self-rated output alone.
- Do not expose arbitrary shell/code execution.
- Do not silently downgrade from a remote provider without provenance.
- Do not weaken default authentication or network protections for convenience.

## Commit examples

```text
feat(extraction): add bounded RSS connector
fix(events): preserve checkpoint after partial work
feat(policy): add labelled cost-aware objective
test(api): reject unauthorised policy promotion
docs(threat): document connector credential boundary
```

Use the issue tracker or project discussion channel configured by the repository owner. Do not include sensitive documents or exploit details in a public issue.
