# OpenOyster 사용자 매뉴얼 — 한국어

OpenOyster `0.4.0`은 문서를 수집하고, codex CLI 기반 LLM 추출로 시그널과 가설 후보를 만들며, FTS5 검색과 LLM 판정으로 근거를 연결하는 알파 단계 런타임입니다. 완성된 범용 자율 에이전트나 검증된 엔터프라이즈 플랫폼은 아닙니다. 추출 백엔드가 불가하면 저품질 휴리스틱으로 강등하지 않고 청크를 보류 상태로 두고 사유를 기록합니다.

## 빠른 시작

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

`.env`에서 `OPENOYSTER_API_KEY`를 긴 임의 값으로 바꾼 뒤 실행합니다.

```bash
openoyster init
openoyster ingest examples/inbox
openoyster run --cycles 4 --sleep 0
openoyster status
openoyster doctor
openoyster serve --host 127.0.0.1 --port 8080
```

기본 추출기는 `OPENOYSTER_LLM_PROVIDER=codex`입니다. 실제 추출에는 `codex` CLI와 `.codex-llm/models.json`, `.codex-llm/pipeline.json`가 필요합니다. 흐름만 확인하려면 `OPENOYSTER_LLM_PROVIDER=stub`을 사용할 수 있지만, stub 결과는 품질 평가용이 아닙니다.

대시보드는 `http://127.0.0.1:8080`, API 문서는 `/docs`에 있습니다.

## 주요 흐름

```text
문서 수집
→ 청크·주장·시그널 추출
→ 가설 후보 생성 및 LLM 병합
→ FTS5 검색으로 지지/반대 근거 후보 검색
→ 방향성 반증 판정
→ 내부 트리거 점수 계산
→ 작업 계획
→ 등록된 도구 실행
→ 산출물 생성
→ 평가 및 사람 피드백 기록
```

각 단계는 독립 루프로 분리돼 있고, 루프 간 통신은 데이터베이스의 영속 이벤트를 통해 이뤄집니다. 따라서 재시작 후에도 이력과 체크포인트가 남습니다.

## 문서 입력

```bash
openoyster ingest ./문서폴더
openoyster ingest ./보고서.pdf
openoyster ingest-url https://example.org/report
openoyster ingest-rss examples/feeds.yaml
openoyster ingest-github owner/repo --kind releases
openoyster ingest-github owner/repo --kind issues
```

지원 형식은 텍스트, Markdown, JSON/JSONL, CSV/TSV, 로그, YAML, HTML, PDF, DOCX입니다. URL 입력은 공인 HTTP(S) 주소만 허용하고, 내부 주소·자격증명이 들어간 URL·과도한 리다이렉트·대용량 응답·지원하지 않는 콘텐츠 유형을 차단합니다. RSS와 GitHub 입력은 읽기 전용이며, GitHub 토큰은 `OPENOYSTER_GITHUB_TOKEN`으로만 받습니다.

원문에서 추출한 텍스트가 데이터베이스에 저장되므로, 민감정보를 넣기 전 데이터베이스 암호화, 접근 통제, 백업, 보존기간을 별도로 설계해야 합니다.

## 실행과 상태 확인

```bash
openoyster run --cycles 4 --sleep 0
openoyster run --forever --sleep 30
openoyster status
openoyster doctor
openoyster doctor-dev
```

`doctor`는 작업공간, 데이터베이스, 정책, 모델 설정, API 쓰기 인증을 점검하고, `doctor-dev`는 로컬 검증 도구 설치 상태를 점검합니다. 여러 worker를 실행할 수 있지만, 동일 루프는 DB lease로 한 worker만 실행됩니다. 이는 완전한 exactly-once 보장을 의미하지 않으며, 모든 쓰기는 여전히 idempotency가 필요합니다.

로컬 개발 런처:

```bash
./run.sh start
./run.sh stop
```

`run.sh`는 로컬 개발용입니다. 정식 장기 운영에는 launchd, systemd, 컨테이너, 서버 배포 방식을 별도로 설계하세요.

읽기 API와 대시보드는 API 키로 보호되지 않습니다. 신뢰할 수 없는 네트워크에서는 `0.0.0.0` 개발 런처를 사용하지 마십시오.

## 근거와 산출물 확인

```bash
openoyster hypothesis show HYPOTHESIS_ID --evidence
openoyster artifact show ARTIFACT_ID --provenance
```

기본 산출물 유형:

- `hypothesis_brief`
- `support_evidence_scan`
- `oppose_evidence_scan`
- `baseline_comparison`
- `utilisation_memo`

근거/출처 확인은 source metadata와 제한된 chunk excerpt를 반환합니다. 전체 원문은 별도 데이터 접근 정책에 따라 다뤄야 합니다.

## 피드백

```bash
openoyster feedback 12 --verdict useful --score 0.9 --comment "주간 보고서에 사용"
openoyster feedback 13 --verdict rejected --comment "근거 출처가 너무 편중됨"
```

허용 verdict는 `used`, `useful`, `rejected`, `stale`, `not_useful`입니다. 사람 피드백은 산출물 평가와 연결된 trigger decision trace의 outcome label로 저장됩니다. 정책 변경은 사람이 후보를 만들고 검토한 뒤 수동으로 승격합니다.

## 정책 관리

```bash
openoyster policy show
openoyster policy list
openoyster policy create examples/policy.sample.yaml --version conservative-001
openoyster policy promote POLICY_ID
```

`policy create`는 현재 정책에 YAML override를 병합하고 검증한 뒤 기본적으로 candidate로 저장합니다. `--activate`를 명시하거나 별도 promote 명령을 실행해야 실제 정책이 바뀝니다.

## 평가

```bash
openoyster eval gold --limit 5
openoyster eval counter --cycles 1
openoyster gold review
```

Gold set 하네스는 core entity recall, signal type F1, quote existence를 측정합니다. Counter 하네스는 방향성 반증 판정 품질을 봅니다. 현재 gold labels는 아직 사람 검수 전이며, counter 판정자는 완전히 독립된 외부 심사자가 아닙니다.

## Docker Compose

```bash
cp .env.example .env
# OPENOYSTER_API_KEY와 OPENOYSTER_POSTGRES_PASSWORD를 반드시 변경
mkdir -p workspace/inbox
cp examples/inbox/* workspace/inbox/
docker compose up --build
```

Compose는 PostgreSQL, migration, API, worker를 분리합니다. 호스트의 `workspace/inbox`는 읽기 전용으로 마운트됩니다.

## 현재 한계

- 기본 검색은 lexical/FTS 검색이며 대규모 벡터 인덱스가 아닙니다.
- Gold labels는 아직 검수 전입니다.
- Counter-evidence 품질은 준-독립 LLM 판정자에 의존합니다.
- RBAC, 다중 테넌트, 필드 단위 암호화, 비밀관리자 연동이 없습니다.
- 브라우저·검색엔진·Gmail·Slack 같은 실제 운영 커넥터는 기본 제공하지 않습니다. GitHub는 releases/issues 읽기 전용만 지원합니다.
- 외부 시스템을 변경하는 action connector SDK와 승인 UI가 없습니다.
- Kafka/NATS 수준의 분산 이벤트 전달과 부하·장애 주입 검증이 없습니다.

따라서 현재 버전은 **실제로 실행되고 확장 가능한 공유용 알파/레퍼런스 구현**이지, 사람 검토 없이 고위험 의사결정을 맡길 완제품은 아닙니다.
