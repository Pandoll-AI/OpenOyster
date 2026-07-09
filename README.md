# OpenOyster

[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/Pandoll-AI/OpenOyster)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11--3.13-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg?logo=fastapi&logoColor=white)](docs/API_REFERENCE.md)
[![Database](https://img.shields.io/badge/database-SQLite%20%7C%20PostgreSQL-336791.svg)](docs/OPERATIONS.md)
[![Language](https://img.shields.io/badge/language-Korean%20%7C%20English-lightgrey.svg)](README-en.md)

> 한국어 우선 문서입니다. English documentation is available in [README-en.md](README-en.md).

<p align="center">
  <img src="assets/hero.png" alt="OpenOyster가 신호, 가설, 이벤트, 산출물을 근거 그래프로 연결하는 인텔리전스 런타임 hero image" width="100%">
</p>

**OpenOyster는 문서와 피드에서 근거 있는 신호를 뽑고, 가설과 반증 후보를 추적 가능한 형태로 남기는 알파 단계의 인텔리전스 런타임입니다.**

현재 구현은 LLM-first 파이프라인입니다. 기본 추출기는 `codex` CLI를 배치로 호출하고, 구조 검증에 실패하거나 모델을 쓸 수 없으면 저품질 대체 결과를 만들지 않고 deferred 상태와 실패 사유를 남깁니다. 검색은 SQLite FTS5를 사용하고, 가설 병합과 방향성 반증 판정은 LLM 판정자를 통합니다.

## 현재 실제 동작

- 파일, URL, RSS, GitHub 릴리즈/이슈를 durable DB에 수집합니다.
- 문서를 청크로 나누고 codex CLI 기반 LLM 추출로 entity, claim, signal을 만듭니다.
- 추출 실패는 chunk 단위로 retry/deferred 상태와 오류를 기록합니다.
- SQLite FTS5로 관련 청크를 찾고 matched terms와 provenance를 남깁니다.
- LLM 병합 판정으로 유사한 scoped claim을 같은 hypothesis로 합칩니다.
- counter-evidence 평가는 방향성을 구분하고 verbatim quote가 있는 반대 근거만 인정하도록 설계했습니다.
- gold set 평가 하네스가 core entity recall, signal type F1, quote existence, counter-evidence precision을 측정합니다.
- API, read-only dashboard, Typer CLI, Alembic migration, Docker Compose가 있습니다.

OpenOyster는 범용 자율 에이전트가 아닙니다. 고위험 의사결정을 자동화하는 제품도 아닙니다. 지금의 목표는 **LLM 추출 결과를 근거, 이벤트, 평가 기록과 함께 운영 가능한 형태로 남기는 reference implementation**입니다.

## 5분 데모

Python 3.11-3.13을 지원합니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

openoyster init
openoyster ingest examples/inbox
openoyster run --cycles 4 --sleep 0
openoyster status
openoyster serve --host 127.0.0.1 --port 8080
```

기본값은 `OPENOYSTER_LLM_PROVIDER=codex`입니다. 실제 추출 데모에는 `codex` CLI와 `.codex-llm/models.json`, `.codex-llm/pipeline.json` 설정이 필요합니다.

```bash
openoyster doctor
```

codex 설정 없이 흐름만 확인하려면 stub provider를 쓰십시오. Stub 결과는 기능 점검용이며 평가 수치로 해석하면 안 됩니다.

```bash
OPENOYSTER_LLM_PROVIDER=stub openoyster run --cycles 4 --sleep 0
```

대시보드: `http://127.0.0.1:8080`

OpenAPI: `http://127.0.0.1:8080/docs`

로컬 장기 실행 개발 런처:

```bash
./run.sh start
./run.sh stop
```

`run.sh`는 로컬 개발용입니다. 원격 또는 장기 운영에는 launchd, systemd, 컨테이너, 또는 서버 배포 방식을 별도로 구성해야 합니다.

`run.sh`는 Tailscale 접근을 위해 `0.0.0.0:3377`에 바인딩합니다. 읽기 API와 대시보드는 API 키로 보호되지 않으므로 신뢰할 수 없는 네트워크에서 실행하지 마십시오. `stop`은 개발 편의를 위해 3377 포트를 점유한 프로세스를 종료합니다.

## RSS 예시

```bash
openoyster ingest-rss examples/feeds.yaml
```

`examples/feeds.yaml`에는 AI Times, Byline Network, TechCrunch AI RSS가 들어 있습니다.

## 평가 상태

1차 gold set 실행 수치입니다. 라벨은 아직 사람이 검수하지 않았습니다.

| Metric | Value | Note |
|---|---:|---|
| Korean core entity recall | 1.000 | unreviewed labels |
| Signal type F1 | 0.806 | unreviewed labels |
| Quote existence | 0.996 | unreviewed labels |
| Counter-evidence precision | see `docs/EVAL_REPORT.md` | judge independence and label limits apply |

이 평가는 “모델이 스스로 만든 라벨로 자기 점수를 매기는” 순환 평가를 피하려는 하네스입니다. 다만 현재 counter-evidence judge는 완전히 독립된 외부 심사자가 아니라 같은 런타임 계열의 LLM 판정자입니다. 수치는 운영 품질의 증거이지, 제품 안정성 보증은 아닙니다.

## CLI 지도

```text
openoyster init
openoyster ingest PATH
openoyster ingest-url URL
openoyster ingest-rss examples/feeds.yaml
openoyster ingest-github owner/repo --kind releases
openoyster ingest-github owner/repo --kind issues
openoyster run [--cycles N | --forever]
openoyster serve
openoyster status
openoyster doctor
openoyster doctor-dev
openoyster feedback ARTIFACT_ID --verdict useful
openoyster hypothesis show HYPOTHESIS_ID --evidence
openoyster artifact show ARTIFACT_ID --provenance
openoyster eval gold [--limit N]
openoyster eval counter [--cycles N]
openoyster gold review
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
  loops/        Eight event-driven loops
  migrations/   Alembic environment and versioned schema
  services/     LLM runtime, extraction, retrieval, judging, evaluation, tools

tests/          Unit, API, CLI, retrieval, LLM, event, and evaluation tests
docs/           User, contributor, architecture, operations, security docs
examples/       Demo documents, policy override, mission example, RSS feeds
```

## 현재 한계

- Gold labels are marked as unreviewed.
- Counter-evidence quality still depends on a quasi-independent LLM judge.
- SQLite mode is best treated as local or single-host.
- No default vector index.
- No RBAC, multi-tenancy, or secret-manager integration.
- No browser-scale crawler.
- No external write-action SDK.
- No load, chaos, or security certification.

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

## 기여

기여 전 [CONTRIBUTING.md](CONTRIBUTING.md)와 [docs/CONTRIBUTOR_MANUAL.md](docs/CONTRIBUTOR_MANUAL.md)를 읽어주세요.

PR은 다음 조건을 만족해야 합니다.

- 감사 가능한 event/model 흐름 유지.
- 관련 테스트 추가.
- lint/typecheck/test 통과.
- 새 event, policy key, command, endpoint, connector 문서화.
- 외부 write capability를 추가할 경우 명시적인 approval boundary 포함.
