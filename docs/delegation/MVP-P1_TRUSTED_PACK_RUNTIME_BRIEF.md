# MVP-P1 Trusted OpenCrab Pack Runtime Brief

## Outcome

신뢰된 local OpenCrab Pack directory를 설치하고, 여러 Pack을 namespace로 분리해
graph와 evidence를 검색하며, Pack 근거가 검증된 답변만 반환하는 수직 경로를
완성한다.

## Product invariant

- OpenOyster의 cognition input은 OpenCrab Pack record다.
- source Pack은 어떤 validate/install/query 단계에서도 수정하지 않는다.
- 같은 local id라도 Pack revision이 다르면 global id가 다르다.
- 사실 답변은 하나 이상의 installed Pack evidence citation을 가진다.
- 검색 근거가 없거나 LLM citation을 검증할 수 없으면 `unknown`이다.

## Current scope

- trusted directory input only
- compatible and strict profiles
- immutable source digest and installed content copy
- PackInstall, PackFile, PackNode, PackEdge, PackEvidence persistence
- one active revision per pack id; multiple pack ids may be active together
- deterministic Pack-aware lexical retrieval with graph expansion
- grounded answer generation through the configured LLM provider
- deterministic fail-closed fallback
- CLI: `pack validate|install|list|show|query`
- API: validate/install/list/show/query resources

## Deferred

- ZIP extraction and quarantine
- feed watcher and automatic refresh
- revision diff, rollback, tombstone semantics
- identity assertion and automatic cross-Pack canonicalization
- OCR/CLIP/audio/video analysis
- Neo4j execution
- autonomous decision/action loops

## Required behavior

1. Compatible validation requires the four OpenCrab validator files and valid
   JSON/JSONL, graph endpoints, unique ids, and resolvable evidence refs.
2. Strict validation additionally requires the documented eleven-file layout.
3. Validation calculates a deterministic directory digest and proves the source
   digest is unchanged after validation.
4. Install copies validated bytes into a digest-addressed workspace path and
   writes registry plus records in one database transaction.
5. Reinstalling the same digest is a no-op. A different digest for the same
   `(pack_id, version)` is rejected as a conflict without replacing active data.
6. Missing manifest version is stored as `unversioned` in compatible mode.
7. Global ids contain pack id, declared version, source digest, record kind, and
   percent-encoded local id.
8. Default query scope is all active Pack installations. A Pack filter narrows
   it; it never broadens to inactive or uninstalled content.
9. Retrieval searches node labels/properties and evidence text/source/location,
   then expands supporting edges and evidence refs.
10. The answer response includes status, answer text, citations, Pack scope, and
    retrieval diagnostics.
11. Every supported answer claim cites evidence ids present in the retrieved
    context. Unknown or invented ids fail closed.
12. Pack content is wrapped as untrusted data in the LLM prompt. Instructions in
    Pack content cannot override the output/citation contract.

## TDD acceptance scenarios

- RED then GREEN: install the minimal four-file fixture and retrieve its claim.
- RED then GREEN: install two copied Packs with colliding local ids and observe
  distinct global ids.
- RED then GREEN: reinstall the same digest and observe no duplicate records.
- RED then GREEN: mutate a copied fixture after first install and observe a
  same-version conflict while the first active install remains intact.
- RED then GREEN: strict mode rejects the minimal fixture and accepts full layout.
- RED then GREEN: query returns Pack/evidence provenance for supported content.
- RED then GREEN: unrelated query returns `unknown` without invoking generation.
- RED then GREEN: generator cites an unknown evidence id and result fails closed.
- RED then GREEN: source fixture digest and file count remain unchanged.
- RED then GREEN: CLI and API exercise install, list/show, and query end to end.
- RED then GREEN: Alembic upgrade creates the Pack tables.

## Quality gates

- no production code before an observed failing test
- no skip/xfail or weakened existing assertions
- `git diff --check`
- focused Pack tests
- `PATH="$PWD/.venv/bin:$PATH" make check`
- Claude Opus read-only review: Critical 0, Major 0
- Root Orchestrator independently verifies files, commands, and source digest
- no commit, push, deploy, or external mutation
