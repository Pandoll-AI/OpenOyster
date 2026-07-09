# OpenOyster 사용자 매뉴얼 — 한국어

OpenOyster `0.3.0`은 문서를 지속적으로 관찰하고, 시그널과 검증 가능한 가설을 만들며, 내부 작업을 계획·실행하고, 결과와 사용자 피드백을 평가해 제한된 정책 파라미터를 조정하는 **상품 지향 알파 버전**입니다. 완성된 범용 자율 에이전트나 검증된 엔터프라이즈 플랫폼은 아니며, 추출 백엔드가 불가하면 저품질 휴리스틱으로 강등하지 않고 청크를 보류 상태로 두고 사유를 기록합니다.

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

대시보드는 `http://127.0.0.1:8080`, API 문서는 `/docs`에 있습니다.

## 주요 흐름

```text
문서 수집
→ 청크·주장·시그널 추출
→ 가설 후보 생성 및 병합
→ 지지/반대 근거 연결
→ 내부 트리거 점수 계산
→ 작업 계획
→ 등록된 도구 실행
→ 산출물 생성
→ 규칙 평가 및 사람 피드백
→ 정책 replay/shadow 튜닝
→ 전체 스코프·대전제 검토
```

각 단계는 독립 루프로 분리돼 있고, 루프 간 통신은 데이터베이스의 영속 이벤트를 통해 이뤄집니다. 따라서 재시작 후에도 이력과 체크포인트가 남습니다.

## 문서 입력

```bash
openoyster ingest ./문서폴더
openoyster ingest ./보고서.pdf
openoyster ingest-url https://example.org/report
openoyster ingest-rss feeds.yaml
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

근거와 출처를 확인할 때는 다음 명령을 사용합니다.

```bash
openoyster hypothesis show HYPOTHESIS_ID --evidence
openoyster artifact show ARTIFACT_ID --provenance
openoyster eval fixtures examples/eval
```

## 피드백과 자기 튜닝

```bash
openoyster feedback 12 --verdict useful --score 0.9 --comment "주간 보고서에 사용"
openoyster feedback 13 --verdict rejected --comment "근거 출처가 너무 편중됨"
```

허용 verdict는 `used`, `useful`, `rejected`, `stale`, `not_useful`입니다. 사람 피드백은 산출물 평가뿐 아니라 해당 산출물과 연결된 트리거 결정의 결과 라벨로 사용됩니다. 충분한 라벨이 없으면 하이퍼파라미터 최적화는 시작되지 않습니다.

최적화는 다음 순서를 따릅니다.

```text
기존 라벨로 후보 정책 replay
→ 최소 개선폭을 넘는 후보만 shadow 상태로 저장
→ replay에 쓰이지 않은 새로운 피드백 라벨 대기
→ 기존 정책과 shadow 정책을 새 라벨에서 비교
→ 안전조건과 최소 개선폭을 통과할 때만 승격
```

현재 최적화는 일부 trigger threshold와 weight에 대한 제한된 탐색입니다. Bayesian optimisation이나 RL 기반 자기개조가 아닙니다.

## 정책 관리

```bash
openoyster policy show
openoyster policy list
openoyster policy create examples/policy.sample.yaml --version conservative-001
openoyster policy promote POLICY_ID
```

`policy create`는 현재 정책에 YAML override를 병합하고 검증한 뒤 기본적으로 candidate로 저장합니다. `--activate`를 명시하거나 별도 promote 명령을 실행해야 실제 정책이 바뀝니다.

## 대전제 검토

```bash
openoyster premise-review
openoyster run --cycles 2 --sleep 0
```

대전제 루프는 개별 문서가 아니라 시스템 전체의 행동을 봅니다. 출처 편중, 시그널 유형 편중, 낮은 산출물 채택률, 높은 루프 실패율, 오래 방치된 가설 등을 점검해 `premise_review` 산출물을 만듭니다. 미션과 스코프 변경은 자동 적용하지 않고 승인 필요 제안으로 남깁니다.

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

- 기본 검색은 lexical 검색이며 대규모 벡터 인덱스가 아닙니다. PostgreSQL full-text mode는 선택 기능입니다.
- RBAC, 다중 테넌트, 필드 단위 암호화, 비밀관리자 연동이 없습니다.
- 브라우저·검색엔진·Gmail·Slack 같은 실제 운영 커넥터는 기본 제공하지 않습니다. GitHub는 releases/issues 읽기 전용만 지원합니다.
- 외부 시스템을 변경하는 action connector SDK와 승인 UI가 없습니다.
- Kafka/NATS 수준의 분산 이벤트 전달과 부하·장애 주입 검증이 없습니다.
- 로컬 휴리스틱이 만든 가설의 언어 품질과 의미 정확도는 제한적입니다.

따라서 현재 버전은 **실제로 실행되고 확장 가능한 공유용 알파/레퍼런스 구현**이지, 사람 검토 없이 고위험 의사결정을 맡길 완제품은 아닙니다.
