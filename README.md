# OpenOyster

[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/Pandoll-AI/OpenOyster)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11--3.13-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg?logo=fastapi&logoColor=white)](docs/API_REFERENCE.md)
[![Database](https://img.shields.io/badge/database-SQLite%20%7C%20PostgreSQL-336791.svg)](docs/OPERATIONS.md)
[![Open Source](https://img.shields.io/badge/open%20source-Apache--2.0-blueviolet.svg)](CONTRIBUTING.md)
[![Language](https://img.shields.io/badge/language-Korean%20%7C%20English-lightgrey.svg)](README-en.md)

> 한국어 우선 문서입니다. English documentation is available in [README-en.md](README-en.md).

> 먼저 읽기: [OpenOyster 목표 지향 로드맵](docs/GOAL_ROADMAP.md) — 최종 목적, 사용자 가치, 단계별 목표를 정리한 기준 문서입니다.

<p align="center">
  <img src="assets/hero.png" alt="OpenOyster가 신호, 가설, 이벤트, 산출물을 근거 그래프로 연결하는 인텔리전스 런타임 hero image" width="100%">
</p>

**OpenOyster는 문서를 읽고, 중요한 변화 신호를 찾고, 검증 가능한 가설과 근거 기반 산출물을 만드는 오픈소스 인텔리전스 런타임입니다.**

파일, URL, RSS, GitHub 이슈/릴리즈처럼 흩어진 자료를 넣으면 OpenOyster는 다음 일을 합니다.

1. 문서를 안전하게 수집하고 청크로 나눕니다.
2. 주장, 시그널, 리스크, 기회를 추출합니다.
3. 검증 가능한 가설을 만들고 지지/반대 근거를 연결합니다.
4. 내부 도구로 brief, counter-evidence scan, decision memo를 생성합니다.
5. 사람 피드백을 받아 policy replay/shadow 평가로 운영 기준을 조정합니다.
6. 시스템 전체가 잘못된 범위를 보고 있지 않은지 meta-premise review를 수행합니다.

```text
Sources -> Documents -> Claims / Signals -> Hypotheses -> Triggers -> Tasks -> Artifacts
   ^                                                                     |
   |----- Meta-premise review <- Policy replay/shadow tuning <- Evaluation
```

OpenOyster는 범용 자율 에이전트가 아닙니다. 사람이 검토할 수 있는 근거 그래프, 이벤트 로그, 정책 기록을 남기는 **감사 가능한 intelligence OS의 알파 구현**입니다.

## 왜 만들었나

많은 AI agent demo는 멋진 답변을 만들지만, 다음 질문에 약합니다.

- 이 판단은 어떤 문서에서 왔나?
- 반대 근거는 찾았나?
- 왜 이 작업이 실행됐나?
- 모델 실패나 추출 보류(deferred) 사유는 기록됐나?
- 좋은 결과와 나쁜 결과를 어떻게 학습하나?
- 시간이 지나며 시스템이 엉뚱한 자료만 보고 있지는 않나?

OpenOyster는 이 질문들을 제품의 중심에 둡니다. 답변보다 **증거, 추적성, 재실행 가능성, 운영 경계**를 먼저 설계합니다.

## 이런 경우에 적합합니다

- 리서치 문서, 정책 자료, 릴리즈 노트, 시장 신호를 지속적으로 관찰하고 싶을 때.
- “요약”보다 “근거가 연결된 가설과 반증 후보”가 필요할 때.
- 자율 agent를 만들되 DB, event log, idempotency, retry, policy tuning을 처음부터 갖추고 싶을 때.
- LLM 결과를 운영 시스템으로 쓰기 전에 감사 가능한 reference architecture가 필요할 때.
- 한국어/영어 문서를 섞어 다루는 오픈소스 intelligence workflow를 실험하고 싶을 때.

## 5분 데모

Python 3.11-3.13을 지원합니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

cp .env.example .env
# API를 외부에 노출하기 전 OPENOYSTER_API_KEY를 설정하세요.

openoyster init
openoyster ingest examples/inbox
openoyster run --cycles 4 --sleep 0
openoyster status
openoyster serve --host 127.0.0.1 --port 8080
```

대시보드: `http://127.0.0.1:8080`

OpenAPI: `http://127.0.0.1:8080/docs`

근거를 직접 확인하려면:

```bash
openoyster hypothesis show HYPOTHESIS_ID --evidence
openoyster artifact show ARTIFACT_ID --provenance
openoyster eval fixtures examples/eval
```

## 무엇이 들어있나

- **Durable runtime**: SQL event stream, checkpoint, idempotency key, DB-backed worker lease.
- **10개 루프**: intake, maintenance, extraction, hypothesis, planning, execution, utilisation, evaluation, optimisation, meta-premise review.
- **추적 가능한 지식 그래프**: document, chunk, claim, signal, hypothesis, evidence edge, task, run, artifact, feedback, policy, experiment, mission charter.
- **수집 경로**: filesystem, guarded HTTP, RSS, GitHub releases/issues.
- **LLM provider**: codex CLI batch extractor, OpenAI-compatible structured JSON provider, test-only stub provider, deferred-on-failure recording.
- **근거 도구**: hypothesis brief, support scan, counter-evidence scan, corpus baseline.
- **검색/근거 품질**: SQLite FTS5/PostgreSQL full-text auto retrieval, matched terms, source-diversity cap, stance-judge evidence filtering.
- **운영 표면**: FastAPI, read-only dashboard, API-key protected writes, Typer CLI, Alembic migration, Docker Compose.
- **검증 표면**: pytest, ruff, mypy, CI, fixture evaluation, release checklist, threat model.

## 입력 예시

```bash
openoyster ingest ./research-notes
openoyster ingest-url https://example.org/report
openoyster ingest-rss feeds.yaml
openoyster ingest-github owner/repo --kind releases
openoyster ingest-github owner/repo --kind issues
```

GitHub 토큰은 선택 사항입니다. 필요하면 `OPENOYSTER_GITHUB_TOKEN`으로만 주입하고, 문서 metadata나 event payload에는 저장하지 않습니다.

## CLI 지도

```text
openoyster init
openoyster ingest PATH
openoyster ingest-url URL
openoyster ingest-rss feeds.yaml
openoyster ingest-github owner/repo --kind releases
openoyster run [--cycles N | --forever]
openoyster serve
openoyster status
openoyster doctor
openoyster doctor-dev
openoyster feedback ARTIFACT_ID --verdict useful
openoyster hypothesis show HYPOTHESIS_ID --evidence
openoyster artifact show ARTIFACT_ID --provenance
openoyster eval fixtures examples/eval
openoyster premise-review
openoyster export --output FILE
openoyster policy show
openoyster policy list
openoyster policy create examples/policy.sample.yaml
openoyster policy promote POLICY_ID
openoyster db upgrade
```

## Docker Compose

```bash
cp .env.example .env
# OPENOYSTER_API_KEY와 OPENOYSTER_POSTGRES_PASSWORD를 설정하세요.
mkdir -p workspace/inbox
cp examples/inbox/* workspace/inbox/
docker compose up --build
```

Compose는 PostgreSQL, one-shot migration, API, worker를 분리해 실행합니다.

## 프로젝트 구조

```text
src/openoyster/
  api/          FastAPI application and escaped read-only dashboard
  connectors/   Filesystem, HTTP, RSS, GitHub ingestion
  loops/        Ten independent event-driven loops
  migrations/   Alembic environment and versioned schema
  services/     Parsing, retrieval, inspection, evaluation, tools, artifacts

tests/          Unit, API, migration, optimiser, retry, CLI, E2E tests
docs/           User, contributor, architecture, operations, security, audit docs
examples/       Demo documents, policy override, mission example, eval fixtures
```

## 현재 상태와 한계

현재 릴리스는 `0.3.0` 알파입니다. 오픈소스 reference implementation으로 사용할 수 있지만, 고위험 의사결정을 자동화하는 완제품은 아닙니다.

아직 포함하지 않습니다.

- 기본 vector index.
- RBAC, multi-tenancy.
- secret manager 통합.
- browser-scale crawler.
- 외부 시스템을 변경하는 write-action SDK.
- distributed broker.
- load, chaos, security certification.

## 설계 원칙

1. Event graph가 시스템의 spine입니다.
2. Knowledge, hypothesis, action은 서로 다른 객체입니다.
3. Autonomy는 bounded여야 합니다.
4. Optimisation에는 실제 label이 필요합니다.
5. Global loop는 local loop의 전제를 의심할 수 있어야 합니다.
6. 추출 백엔드가 불가하면 저품질 대체 분석을 내지 않고 보류 사유를 기록합니다.

## 문서

- [English README](README-en.md)
- [목표 지향 로드맵](docs/GOAL_ROADMAP.md)
- [사용자 매뉴얼](docs/USER_MANUAL_KO.md)
- [User Manual](docs/USER_MANUAL.md)
- [Contributor Manual](docs/CONTRIBUTOR_MANUAL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Operations](docs/OPERATIONS.md)
- [Policy Tuning](docs/POLICY_TUNING.md)
- [API Reference](docs/API_REFERENCE.md)
- [Connectors](docs/CONNECTORS.md)
- [Threat Model](docs/THREAT_MODEL.md)

## Open Source Tags

`open-source` `python` `fastapi` `sqlalchemy` `alembic` `postgresql` `sqlite` `llm` `agents` `event-driven` `retrieval` `rss` `github-api` `korean` `english`

## 기여

기여 전 [CONTRIBUTING.md](CONTRIBUTING.md)와 [docs/CONTRIBUTOR_MANUAL.md](docs/CONTRIBUTOR_MANUAL.md)를 읽어주세요.

PR은 다음 조건을 만족해야 합니다.

- 감사 가능한 event/model 흐름 유지.
- 관련 테스트 추가.
- lint/typecheck/test 통과.
- 새 event, policy key, command, endpoint, connector 문서화.
- 외부 write capability를 추가할 경우 명시적인 approval boundary 포함.

## 라이선스

Apache License 2.0. 자세한 내용은 [LICENSE](LICENSE)와 [NOTICE](NOTICE)를 확인하세요.
