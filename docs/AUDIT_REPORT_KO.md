# OpenOyster 실제 작동성 하드코어 감사 보고서

## 결론

초기 제공본은 **겉모양은 오픈소스 제품이었지만 실제로는 데모에 가까웠습니다.** README, Dockerfile, API, 여러 루프 이름은 있었지만, 가장 중요한 자율성·평가·튜닝이 상당 부분 자기기만적이었고 이벤트 처리와 데이터 무결성에도 치명적인 구멍이 있었습니다. 초기 종합 점수는 **3.1/10**입니다.

이번 `0.2.0` 수정본은 이벤트 처리, 트랜잭션, 실제 원격 모델 응답 사용, 내부 도구 실행, 사람 피드백 라벨, replay/shadow 정책 튜닝, 대전제 검토, 마이그레이션, API 인증, SSRF/XSS 방어, 운영 문서와 테스트를 다시 설계했습니다. 수정 후 점수는 **7.4/10**입니다. 공유 가능한 상품 지향 알파/레퍼런스 구현이지만, 엔터프라이즈 production-ready라고 부르면 과장입니다.

## 1. 초기 버전 점수

| 영역 | 점수 | 냉정한 평가 |
|---|---:|---|
| 데모 실행성 | 5.0 | 작은 예제로는 돌아가지만 성공 경로만 보여줌. |
| 데이터 무결성/복구 | 2.0 | rollback과 cursor 처리 때문에 성공 작업 취소나 이벤트 유실 가능. |
| 자율 작업 루프 | 2.5 | 이름은 많지만 실행은 정적 brief 생성 수준. |
| 하이퍼파라미터 최적화 | 1.0 | 같은 내부 점수로 자신을 평가하고 즉시 승격하는 사실상 가짜 최적화. |
| 대전제 검토 | 2.0 | 실제 시스템 행태보다 형식적 문서 생성에 가까움. |
| 보안/운영 | 2.0 | 쓰기 인증, XSS, SSRF, migration, 동시성, 운영 경계가 빈약. |
| 문서/레포 외형 | 7.0 | 구조와 설명은 그럴듯했음. 이 점이 오히려 실제 완성도를 과대평가하게 만듦. |
| **종합** | **3.1** | **“제품처럼 보이는 프로토타입”** |

## 2. 초기 버전에서 가장 심각했던 문제

### 2.1 수집 루프의 rollback 오염

중복 처리 중 `session.rollback()`을 호출해 같은 트랜잭션에서 앞서 성공한 문서 저장까지 되돌릴 수 있었습니다. 파일 하나의 중복이 다른 정상 수집을 망칠 수 있는 구조였습니다.

### 2.2 이벤트 cursor 유실/정체

관심 없는 이벤트가 많으면 cursor가 충분히 전진하지 못하거나, 실행 루프가 중간에 멈춘 뒤 뒤 이벤트까지 ack할 위험이 있었습니다. 자율 시스템에서 이벤트 유실은 “가끔 답이 틀림”이 아니라 시스템 기억이 끊기는 결함입니다.

### 2.3 원격 LLM을 호출하고도 결과를 버림

설정상 remote provider를 지원한다고 해놓고 실제 응답을 분석에 사용하지 않고 휴리스틱 결과를 반환했습니다. 비용과 개인정보는 외부로 보내면서 품질은 로컬 휴리스틱인 최악의 조합이었습니다.

### 2.4 실행 루프가 실제 실행이 아님

가설을 검증하기 위한 탐색·반증·비교 도구가 아니라 정적 문서를 렌더링하는 수준이었습니다. “Execution Loop”라는 이름이 기능을 과장했습니다.

### 2.5 평가가 자기평가

문서 길이, heading, 완료 상태 같은 내부 산출 특성을 품질로 간주했습니다. 시스템이 자기 문장을 길게 쓰고 스스로 높은 점수를 주는 Goodhart’s law의 교과서적 구조였습니다.

### 2.6 최적화가 실험이 아님

과거 데이터 replay도, 별도 shadow window도, 실제 사용자 라벨도 없이 같은 지표로 후보를 만들고 승격했습니다. `min_improvement` 같은 값은 존재했지만 실질적 안전장치가 아니었습니다.

### 2.7 대전제 루프가 먼 스코프를 보지 못함

출처 편중, 실제 산출물 채택률, 오래된 가설, 루프 실패율, 미션과 관찰 세계의 불일치 같은 시스템 행태를 충분히 보지 못했습니다.

### 2.8 상품 운영 기본기가 부족

Alembic migration 부재, write API 인증 부재, dashboard stored XSS, HTTP connector 비연결/SSRF 위험, deprecated startup, 얕은 테스트, 약한 동시성 제어가 있었습니다. 테스트 4개가 통과했지만 그것은 품질 증거로 거의 의미가 없었습니다.

## 3. 이번 수정본에서 실제로 고친 내용

### 이벤트·트랜잭션

- 필터된 consumer가 안전하게 전진하는 checkpoint 계산.
- 부분 ack가 뒤의 미처리 이벤트를 버리지 않도록 수정.
- 이벤트 idempotency unique key와 nested transaction.
- 루프별 DB lease와 독립 transaction.
- loop run telemetry와 실패 기록.
- 파일별 실패가 이전 성공 수집을 rollback하지 않도록 변경.
- 파일 archive는 commit 이후 maintenance 단계에서 수행.

### 실제 인지/실행

- 원격 OpenAI-compatible JSON 응답을 실제 schema로 파싱.
- remote 실패 시 fallback provider와 warning을 metadata에 명시.
- 파일 파서 확대: PDF, DOCX, HTML, YAML, JSONL, TSV 등.
- 내부 도구 registry 도입.
- 지지 근거 scan, 반대 근거 scan, corpus baseline, hypothesis brief 구현.
- task/run/artifact/evidence 생성 경로 분리.

### 평가와 튜닝

- 길이 중심 평가를 evidence coverage, source diversity, counter-evidence, traceability, uncertainty 중심으로 교체.
- 명시적 사람 feedback 저장과 artifact 상태 반영.
- feedback을 해당 hypothesis revision의 trigger decision trace 결과 라벨로 연결.
- 제한된 후보 정책 replay.
- replay에 사용하지 않은 새로운 라벨을 요구하는 shadow 단계.
- 최소 개선폭, mutation bound, label minimum, candidate expiry, rollback 가능한 policy version.

### 대전제 검토

- source concentration.
- signal-type concentration.
- artifact adoption.
- stale open hypotheses.
- loop failure rate.
- mission charter alignment.
- threshold 초과 시 자동 mission 변경 대신 승인 필요 제안.

### 보안·운영

- write API key, 기본 write-disabled posture.
- dashboard HTML escaping.
- URL credential 차단, redirect마다 DNS/IP 재검증, private address 차단, size/content-type/timeout 제한.
- Alembic initial migration.
- SQLite WAL/foreign key/busy timeout과 PostgreSQL Compose.
- non-root Docker image, migration/API/worker 분리.
- `doctor`, readiness, policy import/promotion, export, feedback CLI.
- 사용자·컨트리뷰터·운영·정책·API·connector·threat model 문서.

## 4. 수정본 검증 결과

패키지 생성 시 다음을 실제 실행했습니다.

- `ruff check src tests`: 통과.
- `mypy src/openoyster`: 통과.
- `pytest`: **20 tests passed**.
- statement coverage: **81%**.
- Alembic empty-database migration: 통과.
- Python wheel/sdist build: 통과.
- wheel 내부 migration template/version 포함 확인.
- CLI lifecycle test: init → ingest → run → doctor → policy create → export 통과.
- 별도 smoke run: 예제 2개 문서에서 documents/signals/hypotheses/tasks/runs/artifacts/evaluations 생성, failed loop 0.

이것은 테스트한 경로의 작동성을 의미합니다. 장기 부하, 네트워크 장애, 대형 corpus, 실제 PostgreSQL 다중 worker, 보안 침투 시험까지 검증했다는 뜻은 아닙니다.

## 5. 수정 후 점수

| 영역 | 점수 | 현재 판단 |
|---|---:|---|
| 로컬 실행/설치 | 8.5 | CLI lifecycle, migration, build, E2E가 자동 테스트됨. |
| 데이터 무결성/재시도 | 7.8 | cursor/idempotency/lease/transaction/retry가 실질적으로 개선됨. |
| 자율 루프의 실체 | 7.2 | 독립 루프와 등록 도구가 실제 동작하지만 도구 생태계는 아직 작음. |
| 하이퍼파라미터 최적화 | 6.6 | replay+fresh shadow label은 진짜지만 탐색 공간과 objective가 단순함. |
| 대전제 검토 | 6.8 | 시스템 행태를 보지만 아직 통계적 drift나 외부 benchmark가 부족함. |
| 보안/운영 | 7.1 | 기본 방어와 문서는 갖췄으나 RBAC/tenant/secret manager가 없음. |
| 테스트/품질 도구 | 8.1 | 20 tests, 81%, Ruff, mypy, build. 부하/chaos/security test는 없음. |
| 문서/공유 가능성 | 8.7 | 실제 설치·운영·기여·위협 경계까지 문서화. |
| **종합** | **7.4** | **“실제로 돌아가는 공유용 상품 지향 알파”** |

## 6. 왜 8점 이상을 주지 않는가

### 6.1 로컬 휴리스틱의 의미 품질이 낮음

현재 기본 provider는 파이프라인 검증에는 좋지만 전략적 가설 품질은 제한적입니다. 실제 도메인에서는 구조화 프롬프트, 모델 평가셋, 추출 정확도 측정이 필요합니다.

### 6.2 retrieval이 대규모용이 아님

bounded lexical SQL scan입니다. 수십만~수백만 chunk에서 사용할 vector/hybrid index, re-ranker, temporal/entity-aware retrieval이 없습니다.

### 6.3 튜닝 objective가 아직 좁음

현재 optimiser는 trigger binary utility 중심입니다. 비용, 탐색 다양성, missed signal, calibration, source fairness, latency를 다목적으로 최적화하지 않습니다.

### 6.4 실제 외부 탐색 생태계가 없음

RSS, GitHub, 검색, 브라우저, 이메일, Drive, Slack, 데이터베이스, 공시 같은 운영 connector가 기본 포함되지 않습니다. 따라서 “세계 탐색”은 현재 입력된 문서와 단일 URL 범위입니다.

### 6.5 조직용 보안이 아님

한 개의 shared API key뿐입니다. RBAC, tenant isolation, audit signer, field encryption, secret manager, SSO가 없습니다.

### 6.6 분산 시스템 검증이 부족함

DB event bus와 lease는 실용적이지만 Kafka/NATS 수준이 아닙니다. 실제 PostgreSQL 다중 worker에서 race, network partition, kill -9, long transaction, clock skew를 넣는 chaos test가 필요합니다.

### 6.7 제품 UI가 최소 수준

dashboard는 읽기 전용 운영 화면입니다. hypothesis graph, evidence inspection, approval queue, policy experiment comparison, premise review action workflow가 없습니다.

## 7. 최종 판단

이제 OpenOyster는 “README만 그럴듯한 코드 묶음”에서는 벗어났습니다. 설치·마이그레이션·입력·다중 루프·작업·산출물·피드백·정책 후보·대전제 리뷰가 실제 데이터베이스 위에서 연결돼 동작합니다. 하지만 아직 제품 시장에 바로 팔 수 있는 완성형 intelligence OS는 아닙니다. 가장 정확한 표현은 다음입니다.

> **OpenOyster 0.2.0은 실제 실행과 확장이 가능한 오픈소스 상품 지향 알파이며, 신뢰 가능한 자율 인텔리전스 제품을 만들기 위한 강한 기반이지만, 엔터프라이즈 운영·대규모 검색·도메인 품질·조직 보안은 추가 개발이 필요하다.**
