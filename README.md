# OpenOyster

[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/Pandoll-AI/OpenOyster)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11--3.13-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg?logo=fastapi&logoColor=white)](docs/API_REFERENCE.md)
[![Database](https://img.shields.io/badge/database-SQLite%20%7C%20PostgreSQL-336791.svg)](docs/OPERATIONS.md)
[![Open Source](https://img.shields.io/badge/open%20source-Apache--2.0-blueviolet.svg)](CONTRIBUTING.md)
[![Language](https://img.shields.io/badge/language-Korean%20%7C%20English-lightgrey.svg)](README-en.md)

> 한국어 우선 문서입니다. English documentation is available in [README-en.md](README-en.md).

**OpenOyster**는 문서를 지속적으로 관찰하고, 시그널과 검증 가능한 가설을 만들며, 내부 작업을 실행하고, 산출물과 피드백을 바탕으로 정책을 조정하는 **오픈소스 인텔리전스 런타임 알파**입니다.

```text
Sources -> Documents -> Claims / Signals -> Hypotheses -> Triggers -> Tasks -> Artifacts
   ^                                                                     |
   |----- Meta-premise review <- Policy replay/shadow tuning <- Evaluation
```

OpenOyster는 “마법 같은 범용 자율 에이전트”가 아닙니다. 아직 엔터프라이즈 production-ready 플랫폼도 아닙니다. 대신 실행, 테스트, 감사, 확장이 가능한 **상품 지향 오픈소스 레퍼런스 구현**을 목표로 합니다.

## 핵심 기능

- SQL 기반 durable event stream, loop별 checkpoint, idempotency key, DB-backed worker lease.
- intake, maintenance, extraction, hypothesis, planning, execution, utilisation, evaluation, optimisation, meta-premise review로 나뉜 10개 독립 루프.
- 문서, 청크, 주장, 시그널, 가설, 근거, 작업, 실행, 산출물, 피드백, 정책, 실험, 미션 charter의 추적 가능한 영속 모델.
- 로컬 deterministic extractor와 OpenAI-compatible structured JSON provider.
- 파일시스템, guarded HTTP, RSS, GitHub releases/issues 읽기 전용 수집.
- hypothesis brief, support scan, counter-evidence scan, corpus baseline 등 evidence-aware 내부 도구.
- matched terms, source-diversity cap, counter-evidence query mode, optional PostgreSQL full-text retrieval.
- raw document 전체를 기본 노출하지 않는 evidence/provenance CLI 및 read API.
- signal type, counter-evidence, traceability 회귀를 잡는 deterministic eval fixtures.
- FastAPI, read-only dashboard, API-key-protected mutation endpoints, Typer CLI, Alembic migration, Docker Compose, CI, 문서와 테스트.

## 현재 상태

현재 릴리스는 `0.3.0`입니다.

초점:

- 오픈소스 설치 가능성;
- 근거 품질과 출처 추적;
- 안전한 읽기 connector;
- CLI/API 기반 inspection;
- 작은 팀이나 개인 리서치용 reference runtime.

아직 포함하지 않는 것:

- 기본 vector index;
- RBAC, multi-tenancy;
- secret manager 통합;
- browser-scale crawler;
- 외부 시스템을 변경하는 write-action SDK;
- distributed broker;
- load, chaos, security certification.

## 빠른 시작

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
openoyster doctor
openoyster serve --host 127.0.0.1 --port 8080
```

대시보드: `http://127.0.0.1:8080`

OpenAPI: `http://127.0.0.1:8080/docs`

## Docker Compose

```bash
cp .env.example .env
# OPENOYSTER_API_KEY와 OPENOYSTER_POSTGRES_PASSWORD를 교체하세요.
mkdir -p workspace/inbox
cp examples/inbox/* workspace/inbox/
docker compose up --build
```

Compose는 PostgreSQL, one-shot migration, API, worker를 분리해 실행합니다.

## 주요 CLI

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

## 설계 원칙

1. Event graph가 시스템의 spine입니다.
2. Knowledge, hypothesis, action은 서로 다른 객체입니다.
3. Autonomy는 bounded여야 합니다.
4. Optimisation에는 실제 label이 필요합니다.
5. Global loop는 local loop의 전제를 의심할 수 있어야 합니다.
6. Fallback은 숨기지 않고 기록합니다.

## 문서

- [English README](README-en.md)
- [사용자 매뉴얼](docs/USER_MANUAL_KO.md)
- [User Manual](docs/USER_MANUAL.md)
- [Contributor Manual](docs/CONTRIBUTOR_MANUAL.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Operations](docs/OPERATIONS.md)
- [Policy Tuning](docs/POLICY_TUNING.md)
- [API Reference](docs/API_REFERENCE.md)
- [Connectors](docs/CONNECTORS.md)
- [Threat Model](docs/THREAT_MODEL.md)
- [Audit Report KO](docs/AUDIT_REPORT_KO.md)

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
