# OpenOyster

[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/Pandoll-AI/OpenOyster)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11--3.13-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-282%20passing-brightgreen.svg)](tests)
[![API](https://img.shields.io/badge/API-FastAPI-009688.svg?logo=fastapi&logoColor=white)](docs/API_REFERENCE.md)

> This is the English overview. The Korean-first README is [README.md](README.md).

<p align="center">
  <img src="assets/hero.png" alt="OpenOyster deliberating over validated OpenCrab Packs and producing an auditable decision dossier" width="100%">
</p>

**OpenOyster is an autonomous deliberation tool whose factual input is limited to OpenCrab Packs.**

It goes beyond answering a question. Given a Mission and selected installed Packs, it builds
beliefs, alternatives, expected and adverse scenarios, an independent critique, a selection or
abstention, flip conditions, and Knowledge Requests. It persists the result as an auditable
Decision Dossier.

> OpenCrab builds knowledge. OpenOyster thinks and decides with that knowledge.

# Product boundary

OpenCrab owns collection, structuring, validation, Pack creation, and Pack updates. OpenOyster
consumes immutable installed Packs and owns the deliberation state.

OpenOyster does not:

- create or automatically update Packs;
- compute Pack record/revision diffs or rollbacks;
- browse the web or promote model prior knowledge to evidence;
- execute Knowledge Requests or external actions.

Mission goals, constraints, preferences, deadlines, and context are control inputs. They are
never evidence.

# Autonomous Deliberation D1

```text
Mission
  → freeze exact Pack install IDs
  → evidence snapshots
  → beliefs
  → options and hard constraints
  → expected/adverse scenarios
  → independent critic
  → selection or abstention
  → flip conditions and Knowledge Requests
  → Cognitive Impact, Decision Dossier, audit replay
```

The happy path uses exactly five bounded LLM stages. No retrieved Pack evidence means a
deterministic abstention with zero model calls. A non-passing critic, too few viable options, or
a hard-constraint violation also prevents selection.

Grounded assertions require an exact quote or a resolvable JSON pointer into a frozen evidence
snapshot. Unknown, local-only, out-of-scope, or mismatched citations fail closed.

Replay never invokes the LLM. It revalidates stored stage payloads and citation anchors,
re-renders the dossier, and compares deterministic hashes. It also recomputes Cognitive Impact
and the cognitive transition from the source assertions and citations rather than trusting the
stored artifact, checks stored-payload self-digests first, and reports `recompute_skipped`
instead of a false mismatch when a stored method or template predates the current version.

An optional second critic (`OPENOYSTER_CRITIC2_PROVIDER`, off by default) reruns the critic on
another provider and combines verdicts conservatively (pass only if both pass); the primary
critic artifact stays immutable. No-evidence abstention distinguishes `pack_has_no_evidence`
from `no_match_in_pack_evidence` so a retrieval miss is not recorded as true absence.

# Decision Continuity D2

A completed abstention can be continued once its Knowledge Requests are claimed fulfilled with a
newly installed Pack. The child freezes the parent Mission, records `parent_run_id`, and a
`cognitive_transition_v3` artifact reports belief/option/critic/decision/citation-scope changes and freezes optional critic2 relevance into `semantic_verdicts`.
A Knowledge Request is verified only when newly added evidence is also cited by a child assertion;
semantic relevance is not proven. Knowledge Requests can be exported in a machine-readable form
(`openoyster deliberate knowledge-requests RUN_ID --format export` / `?format=export`) for
OpenCrab or a human to consume as a collection request. See
[D2 Requirements](docs/DECISION_CONTINUITY_D2_REQUIREMENTS.md).

# Five-minute smoke run

Python 3.11–3.13 is supported.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

openoyster init
openoyster pack install tests/fixtures/opencrab_pack_runtime/p0-f1-minimal

export OPENOYSTER_LLM_PROVIDER=stub
openoyster deliberate run tests/fixtures/deliberation_d1/mission_happy.json \
  --packs p0-f1-minimal \
  --impact-baseline-packs p0-f1-minimal \
  --allow-compatible-packs \
  --idempotency-key demo-d1-001
```

Use the returned run ID:

```bash
openoyster deliberate show RUN_ID
openoyster deliberate dossier RUN_ID --format markdown
openoyster deliberate impact RUN_ID
openoyster deliberate knowledge-requests RUN_ID
openoyster deliberate replay RUN_ID
```

The stub proves the workflow, not decision quality. The default provider is `codex`; real local
generation requires Codex CLI plus the tracked `.codex-llm` configuration.

# Mission format

Mission files can be YAML or JSON.

```yaml
goal: Choose a reversible response
decision_question: Which option is supported by the installed Packs?
constraints:
  - Do not introduce facts outside Pack evidence
preferences:
  - Prefer reversible options
context: Control background only; never evidence
```

`goal` and `decision_question` are required. Constraints, preferences, deadline, context, and
mission charter reference are optional.

# CLI and API

```text
openoyster pack validate|install|list|show|query
openoyster deliberate run|show|dossier|replay|impact|knowledge-requests
openoyster deliberate continue|transition
```

D1/D2 API endpoints:

```text
POST /v1/deliberations
GET  /v1/deliberations/{id}
GET  /v1/deliberations/{id}/dossier
POST /v1/deliberations/{id}/replay
GET  /v1/deliberations/{id}/cognitive-impact
GET  /v1/deliberations/{id}/knowledge-requests
POST /v1/deliberations/{id}/continue
GET  /v1/deliberations/{id}/transition
```

Every D1/D2 endpoint requires the configured API key. Create and continue also require
`Idempotency-Key`, which is bound to a request fingerprint (reuse with different inputs returns
`idempotency_request_mismatch`). Responses omit raw Pack bodies, full prompts, filesystem paths,
storage URIs, secrets, and raw model/validation error text.

# Local service launcher

```bash
./run.sh start
./run.sh stop
```

The temporary development launcher binds to `0.0.0.0:3388` and prints the Tailscale IPv4 URL.
Use macOS `launchd`, containers, or a remote deployment path for formal long-running service.

# Verification

```bash
PATH="$PWD/.venv/bin:$PATH" make check
```

The current gate covers Ruff, mypy, 282 tests, D1/D2 contracts/runtime/migrations/CLI/API,
adversarial gate and persistence-parity suites, a golden replay test, Pack-source immutability,
and sdist/wheel builds.

# Documentation

- [Korean README](README.md)
- [D1 Requirements](docs/AUTONOMOUS_DELIBERATION_D1_REQUIREMENTS.md)
- [D2 Requirements](docs/DECISION_CONTINUITY_D2_REQUIREMENTS.md)
- [Flip Condition Monitoring D3 (draft)](docs/DELIBERATION_FLIP_MONITORING_D3_REQUIREMENTS.md)
- [Decision Outcome Ledger (draft)](docs/DECISION_OUTCOME_LEDGER_REQUIREMENTS.md)
- [Korean User Manual](docs/USER_MANUAL_KO.md)
- [User Manual](docs/USER_MANUAL.md)
- [API Reference](docs/API_REFERENCE.md)
- [OpenCrab Pack Runtime Requirements](docs/OPENCRAB_PACK_RUNTIME_REQUIREMENTS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Operations](docs/OPERATIONS.md)
- [Threat Model](docs/THREAT_MODEL.md)

# Status

OpenOyster is alpha software. Exact anchors prove provenance, not semantic entailment. Humans
should review the dossier and underlying evidence before using it for high-stakes decisions.
