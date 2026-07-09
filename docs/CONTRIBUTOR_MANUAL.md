# OpenOyster Contributor Manual

OpenOyster accepts contributions that improve reliability, observability, evidence quality, safety, and extensibility. This guide defines the engineering contract for contributors.

## 1. Development environment

```bash
git clone <your-fork>
cd OpenOyster
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
make check
```

`make check` runs Ruff, mypy, pytest with coverage, and a package build. Python 3.11–3.13 are supported. The CI matrix checks all three versions.

## 2. Repository conventions

- Source code lives under `src/openoyster`.
- Tests live under `tests` and should mirror the affected subsystem.
- Database changes require an Alembic revision.
- New events, policy keys, commands, endpoints, or connectors require documentation.
- User-facing behaviour changes require `CHANGELOG.md` and manual updates.
- Do not commit runtime databases, workspaces, secrets, private documents, caches, or generated coverage files.

## 3. Architectural invariants

### 3.1 Persist meaningful state transitions

A loop may call pure helpers directly, but meaningful cross-loop state changes must be persisted and announced through events.

Good:

```text
ExtractionLoop persists Signal
→ emits signal.detected
→ HypothesisLoop consumes the event
```

Bad:

```text
ExtractionLoop reaches into UtilisationLoop and calls a private method
```

### 3.2 Separate evidence from interpretation

- `Document`, `Chunk`, `Claim`, and `Signal` represent observations.
- `Hypothesis` represents an interpretation that may be falsified.
- `EvidenceEdge` records support or opposition with provenance.
- `Task`, `Run`, and `Artifact` represent action and output.
- `Evaluation` and `ArtifactFeedback` represent quality or downstream value.

Never persist generated prose as if it were source evidence unless its provenance explicitly says it is synthetic.

### 3.3 Every autonomous action must be inspectable

At least one of the following must explain why an action happened:

- event payload and parent/correlation relationship;
- decision trace with features, threshold, and policy version;
- task/run record;
- artifact provenance;
- evaluation or human feedback;
- policy experiment and mutation set.

### 3.4 Idempotency is mandatory

Workers can retry after a crash. Event emission and durable writes must use stable idempotency keys or uniqueness constraints. Never rely on a cursor alone to prevent duplicate side effects.

### 3.5 External writes require approval

A connector or tool that can send, modify, delete, deploy, trade, publish, or otherwise create irreversible effects must:

1. default to disabled;
2. create an explicit approval object or task;
3. record intended parameters before execution;
4. validate the approver and approval freshness;
5. persist the external response and correlation identifier;
6. support safe retry semantics;
7. document rollback or irreversibility.

The default runtime contains no external write tools.

## 4. Adding an event

1. Choose a past-tense or state-transition name, such as `document.parsed` or `artifact.feedback.recorded`.
2. Include stable identifiers rather than entire large objects.
3. Include an idempotency key when the event represents a unique transition.
4. Set `source_loop`, `parent_event_id`, and correlation data where available.
5. Add the event to the consumer loop contract.
6. Test retry and cursor behaviour.
7. Document it in `docs/ARCHITECTURE.md`.

Event payloads are currently JSON dictionaries. Treat their shape as a compatibility contract.

## 5. Adding a loop

Subclass `BaseLoop` and define:

```python
class ExampleLoop(BaseLoop):
    name = "example"
    consumes = ("some.event",)

    def run(self, session: Session, limit: int = 50) -> LoopResult:
        ...
```

Loop rules:

- The supervisor owns the transaction boundary.
- Poll with the durable event bus.
- Acknowledge only after all selected work has been persisted.
- Make output idempotent.
- Keep one loop responsibility narrow.
- Return `LoopResult` counts and actionable notes.
- Do not sleep or perform unbounded work inside `run`.
- Put network timeouts and response-size limits on all I/O.
- Add loop telemetry and failure-path tests.

Register the loop in `Supervisor` only after its contract and tests are complete.

## 6. Adding an internal execution tool

Tools live in `services/tools.py` and return `ToolResult`.

```python
def example_tool(
    session: Session,
    hypothesis: Hypothesis,
    policy: dict,
) -> ToolResult:
    return ToolResult(
        artifact_type="example",
        title="...",
        content="...",
        summary="...",
        evidence_candidates=[],
        metadata={},
    )
```

Register it in `TOOL_REGISTRY`. A tool must be deterministic enough to retry safely or must provide a durable external idempotency key. It must not bypass task/run/artifact persistence.

## 7. Adding an extraction provider

Implement the `LLMProvider` protocol and return validated `TextAnalysis`:

- entities;
- claims;
- signals;
- hypothesis candidates;
- provider/model identity;
- usage metadata;
- warnings and deferred/unavailable metadata.

Provider requirements:

- schema-validate model output;
- cap timeout, retries, and response size;
- never silently present heuristic output as remote output;
- avoid logging prompts or source text by default;
- test malformed JSON, empty output, HTTP failure, subprocess failure, timeout, and deferred behaviour.

## 8. Adding a connector

A read connector should produce a parsed document with:

```text
source
source_uri
title
text
content_hash
ingest_key
parser_version
metadata
```

Connector requirements:

- deterministic ingest key;
- input and output size limits;
- timeout and retry policy;
- content-type and parser validation;
- SSRF/path traversal protections where relevant;
- no secret-bearing URLs in logs;
- provenance metadata;
- unit tests for malformed and malicious inputs.

See `docs/CONNECTORS.md`.

## 9. Policy changes

A new policy key requires:

1. a default value;
2. validation and bounds;
3. an actual code path that reads it;
4. tests proving it changes behaviour;
5. documentation;
6. migration or compatibility handling for stored old policies.

Do not add decorative “tunable” keys that are never consumed. The earlier prototype had this failure mode and it is explicitly rejected.

Policy changes that alter trigger or planning behaviour need an objective, labels or fixtures, evaluation evidence, safety bounds, and rollback semantics.

## 10. Database migrations

After changing models, generate and review an Alembic revision. The generated migration must be deterministic, reversible where practical, and tested against an empty database plus a representative previous schema.

```bash
openoyster db upgrade head
```

Do not replace migration review with `create_all`. `create_all` is retained only for embedded tests and explicit disposable fallback.

## 11. Testing strategy

A contribution should test the failure mode it could introduce.

| Area | Expected tests |
|---|---|
| Event bus | idempotency, filtered cursor advancement, partial checkpoints, lease races. |
| Intake | unchanged files, changed files, parser failure, oversize input, archive after commit. |
| Extraction | valid output, provider failure, retry, malformed structured output. |
| Hypothesis | exact/semantic merge, evidence deduplication, contradiction, revision idempotency. |
| Planning/execution | task bounds, unknown tools, retry, budget, duplicate events. |
| Evaluation | evidence quality, explicit feedback, trace labels. |
| API | authentication, escaping, pagination, validation, error status. |
| CLI | lifecycle and non-zero failure paths. |
| Migrations | fresh install and upgrade. |

Run:

```bash
ruff check src tests
mypy src/openoyster
pytest --cov-fail-under=75
python -m build
```

## 12. Documentation and examples

Code is incomplete until a new user can operate it. Include:

- configuration example;
- command/API example;
- expected event and output;
- safety limitations;
- troubleshooting notes;
- upgrade implications.

Examples must not contain real secrets or private documents.

## 13. Pull request process

1. Open a focused issue or explain the problem in the PR.
2. Keep the change small enough to audit.
3. Add tests before or with implementation.
4. Run the full quality gate.
5. Update changelog and manuals.
6. Explain data-model, event-contract, and safety implications.
7. Include migration and rollback instructions when applicable.

Suggested commit style:

```text
feat(connectors): add bounded RSS ingestion
fix(events): preserve checkpoint on partial failure
test(policy): require labelled promotion evidence
docs(operations): add PostgreSQL restore drill
```

## 14. Release process

Follow `docs/RELEASE_CHECKLIST.md`. A release should not be described as production-ready merely because tests are green. The maintainer must state known gaps and the environments actually exercised.
