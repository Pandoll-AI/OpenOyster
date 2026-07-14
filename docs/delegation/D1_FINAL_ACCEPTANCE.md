# Autonomous Deliberation D1 최종 인수 기록

작성일: 2026-07-14
상태: 승인

## Goal

OpenCrab Pack만 사실 입력으로 사용해 Mission → Belief → 대안 → 시나리오 → 반론 →
결정/보류 → 전환 조건 → Knowledge Request → Decision Dossier를 한 번의 실행으로
완결하고, 저장·조회·재현할 수 있는 Autonomous Deliberation D1을 구현·검증한다.

## 구현 결과

- Mission과 정확한 Pack install ID를 실행 전에 동결한다.
- Pack evidence snapshot만 grounded fact와 grounded inference의 근거로 허용한다.
- belief, option, expected/adverse scenario, critic, decision/abstention을 닫힌 계약으로 실행한다.
- flip condition, Knowledge Request, Cognitive Impact, JSON/Markdown Dossier를 저장한다.
- CLI와 API에서 실행·조회·dossier·impact·Knowledge Request·replay를 제공한다.
- replay는 LLM을 호출하지 않고 artifact, gate, dossier, evidence snapshot digest를 재검증한다.
- LLM stage의 durable started marker, lease, atomic result commit을 적용한다.
- 응답 후 저장 전 중단이 의심되면 같은 idempotency key로 재호출하지 않고
  `indeterminate`로 닫는다.

## 실제 위임 경로

- Sol `gpt-5.6-sol`, effort `xhigh`: D1 경계와 aggregate, stage, crash/replay 계약 설계.
- Grok 4.5, effort `xhigh`: core 구현 위임. 최대 turn에 도달한 부분은 오케스트레이터가
  인수해 완성하고 검증했다.
- Terra `gpt-5.6-terra`, effort `high`: CLI/API/문서 통합과 공개 surface 테스트.
- Codex host: 전체 오케스트레이션, TDD 보강, 트랜잭션 수정, 문서·hero 통합, 최종 검증.
- Claude Opus `claude-opus-4-8`, effort `high`: 두 차례 최종 코드 리뷰.

Cursor는 사용하지 않았다.

## Opus 품질 판정

첫 최종 리뷰는 `CHANGES_REQUIRED`였다.

- 메타데이터가 exact quote로 오인될 수 있는 경로를 제거했다.
- stage 시작 원장과 lease가 durable하지 않던 문제를 단계별 transaction으로 수정했다.
- pointer mismatch, compatible Pack opt-in, scope 밖 citation, prompt limit, crash,
  artifact tamper 음성 테스트를 추가했다.

수정 후 전체 리뷰 결과는 `APPROVED`, Critical 0, Major 0이었다. 남은 Minor도 닫은 뒤
delta 재리뷰를 수행했고 다시 `APPROVED`, Critical 0, Major 0, 신규 Minor 0을 받았다.

## 최종 품질 게이트

실행 명령:

```bash
git diff --check
PATH="$PWD/.venv/bin:$PATH" make check
```

관찰 결과:

- Ruff 통과
- mypy 73개 source file 통과
- pytest 145개 통과
- 전체 coverage 83%
- SQLite migration upgrade/downgrade 테스트 통과
- sdist와 wheel build 통과
- 비밀 패턴 검사에서 검출 없음

Starlette TestClient의 upstream deprecation warning 1건만 남아 있으며 기능 실패는 아니다.

## 인수 판정

Goal의 D1 수직 기능, Pack-only 근거 경계, 공개 CLI/API, crash 안전성, 감사 replay,
문서와 검증 증거가 모두 준비됐다. 최종 인수 기준을 충족한다.
