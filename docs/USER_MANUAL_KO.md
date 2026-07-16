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

## 신뢰된 OpenCrab Pack 디렉터리

MVP-P1은 서비스 프로세스가 이미 접근할 수 있는 신뢰된 로컬 OpenCrab Pack **디렉터리만** 받습니다. validate/install/query는 source Pack을 수정하지 않으며, install은 검증한 바이트를 workspace에 복사하고 활성 Pack 근거만 질의합니다.

```bash
openoyster pack validate /trusted/packs/example
openoyster pack validate /trusted/packs/example --profile strict
openoyster pack install /trusted/packs/example
openoyster pack list
openoyster pack show PACK_ID
openoyster pack query "이 Pack이 뒷받침하는 내용은?" --packs PACK_ID
```

명령은 자동화 가능한 JSON을 출력합니다. `compatible`은 네 개 validator 파일을, `strict`는 문서화된 열한 개 layout 파일을 요구합니다. supported answer는 항상 global evidence id를 인용합니다. 검색 근거가 없거나 local/모호한 citation 또는 존재하지 않는 citation이 나오면 `unknown`을 반환합니다.

API도 `POST /v1/packs/validate`, `POST /v1/packs/install`, `GET /v1/packs`, `GET /v1/packs/{pack_id}`, `POST /v1/packs/query`로 같은 기능을 제공합니다. 로컬 경로를 읽는 validate, 상태를 바꾸는 install, LLM을 호출할 수 있는 query에는 일반 write API key가 필요합니다. API 오류에는 로컬 경로나 Pack 본문을 넣지 않습니다.

이는 archive 또는 remote ingestion API가 아닙니다. ZIP extraction/quarantine, 자동 update/diff/rollback, OCR/CLIP/audio/video 분석은 후속 범위입니다.

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

Gold set 하네스는 core entity recall, signal type F1, quote existence를 측정합니다. Counter 하네스는 방향성 반증 판정 품질을 봅니다. 현재 gold labels는 아직 사람 검수 전입니다. Judge, verifier, auditor는 모두 `gpt-5.6-sol`을 사용하고 역할 프롬프트와 reasoning effort만 다르므로, counter precision은 독립 확인이 아니라 self-consistency 측정치입니다.

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
- Counter-evidence precision은 `gpt-5.6-sol` 내부의 역할 분리 self-consistency이며 독립 확인이 아닙니다.
- RBAC, 다중 테넌트, 필드 단위 암호화, 비밀관리자 연동이 없습니다.
- 브라우저·검색엔진·Gmail·Slack 같은 실제 운영 커넥터는 기본 제공하지 않습니다. GitHub는 releases/issues 읽기 전용만 지원합니다.
- 외부 시스템을 변경하는 action connector SDK와 승인 UI가 없습니다.
- Kafka/NATS 수준의 분산 이벤트 전달과 부하·장애 주입 검증이 없습니다.

따라서 현재 버전은 **실제로 실행되고 확장 가능한 공유용 알파/레퍼런스 구현**이지, 사람 검토 없이 고위험 의사결정을 맡길 완제품은 아닙니다.

## Autonomous Deliberation D1

Autonomous Deliberation D1은 하나의 Mission과 이미 설치한 OpenCrab Pack으로 Decision Dossier를 만듭니다. 사실 근거는 Pack evidence뿐입니다. Mission의 목표·질문·제약·선호·문맥은 제어 입력이며 사실 근거가 아닙니다. 실행 결과에는 belief, option, 기본/불리 scenario, 독립 critic 결과, 선택 또는 기권, flip condition, 실행하지 않는 Knowledge Request, Cognitive Impact, 결정적 audit replay가 저장됩니다.

stub provider로 fixture 전체 흐름을 확인하는 예시는 다음과 같습니다.

```bash
openoyster pack install tests/fixtures/opencrab_pack_runtime/p0-f1-minimal

openoyster deliberate run tests/fixtures/deliberation_d1/mission_happy.json \
  --packs p0-f1-minimal \
  --impact-baseline-packs p0-f1-minimal \
  --allow-compatible-packs \
  --idempotency-key manual-d1-001
```

첫 명령의 JSON 출력에서 `id`를 확인한 뒤 다음 명령으로 감사 정보를 봅니다.

```bash
openoyster deliberate show RUN_ID
openoyster deliberate dossier RUN_ID --format json
openoyster deliberate dossier RUN_ID --format markdown
openoyster deliberate impact RUN_ID
openoyster deliberate knowledge-requests RUN_ID
openoyster deliberate replay RUN_ID
```

정상 경로는 유계 stage를 정확히 다섯 번 호출합니다. 검색된 Pack evidence가 없으면 모델 호출 없이 기권으로 완료합니다. 같은 `--idempotency-key`를 다시 쓰면 새 실행을 만들지 않고 저장된 run을 반환합니다. `replay`는 LLM을 재호출하지 않고 저장된 artifact와 dossier digest를 다시 검증합니다.

CLI 종료 코드는 완료된 선택/기권이면 `0`, database·indeterminate·복구 불가능 실행 오류면 `1`, Mission·Pack scope/profile·인자 오류면 `2`입니다. 출력에는 raw Pack record, 전체 prompt, 서버 파일 경로, storage URI, runtime 설정, secret을 넣지 않습니다.

API는 `POST /v1/deliberations`, `GET /v1/deliberations/{id}`, dossier, replay, cognitive-impact, knowledge-requests endpoint를 제공합니다. **D1 API는 조회를 포함한 모든 endpoint에 설정된 API key가 필요합니다.** 생성 요청에는 `Idempotency-Key` header도 반드시 넣어야 합니다. Knowledge Request는 기록·내보내기만 하며 Pack 갱신, 검색, 외부 작업을 자동 실행하지 않습니다. 요청/응답 형식은 `docs/API_REFERENCE.md`의 Autonomous Deliberation D1 절을 따르세요.

## Decision Continuity D2

D2는 완료된 기권(abstention) 부모 run을, 그 run에 저장된 Knowledge Request가 충족된 뒤 이어서 실행하는 기능입니다. OpenOyster가 사실을 찾거나 Pack을 갱신하는 기능은 아닙니다. OpenCrab 또는 사용자가 새 Pack을 설치한 뒤, 사용자가 새 Pack ID와 충족한 부모 Knowledge Request의 `local_key`를 명시합니다.

```bash
openoyster deliberate continue PARENT_RUN_ID \
  --packs new-pack-id \
  --fulfills kr_no_evidence \
  --idempotency-key manual-d2-001

openoyster deliberate transition CHILD_RUN_ID
```

선택적으로 `--impact-baseline-packs`와 `--allow-compatible-packs`를 지정할 수 있습니다. `--fulfills`는 충족 주장입니다. `evidence:no_evidence` 요청은 새로 인용된 Evidence가 있어야 검증 완료됩니다. 자식에는 `cognitive_transition_v3` artifact가 저장되며 claimed, verified fulfilled, unverified claimed 요청을 분리하고, critic2 관련 판정은 `semantic_verdicts`에 동결합니다. critic의 gap finding도 새 Knowledge Request로 승격됩니다.

예를 들어 첫 실행이 “현장 복구 시간” 근거가 없어 `kr_no_evidence`를 남기고 기권했다고 합시다. 사용자가 OpenCrab에서 해당 근거를 담은 새 Pack을 설치하고 `--fulfills kr_no_evidence`로 계속 실행하면, 전환 결과에서 새 belief의 상태 변화, 기존 option의 viable 변화, critic verdict, 최종 decision의 `abstain`→`select` 변화, 추가된 global evidence ID를 한 번에 확인할 수 있습니다. 여전히 부족한 요청은 `remaining_knowledge_requests`에 남습니다.

부모 run은 변경되지 않으며 전환 artifact도 불변·재사용됩니다. 같은 idempotency key로 같은 부모를 다시 요청하면 새 실행 없이 기존 child 상태를 반환합니다. 다른 부모에 같은 key를 쓰면 `idempotency_key_conflict`입니다. 부모가 없거나 완료된 기권이 아니면 각각 `parent_run_not_found`, `parent_run_not_completed_abstain`입니다. Knowledge Request artifact가 없으면 `parent_knowledge_requests_missing`, fulfills가 비어 있으면 `fulfilled_knowledge_request_keys_empty`, 부모에 없는 key면 `fulfilled_knowledge_request_keys_unknown`입니다. 이 입력 오류는 CLI 종료 코드 `2`, API `422`입니다. provider/runtime 오류는 epistemic abstention이 아니라 `failed_execution`이며 CLI `1`, API `502`입니다. 실패한 child도 idempotency key를 사용하므로 provider/runtime 복구 후 실제로 재시도할 때는 새 key를 지정하세요.

API 계약과 응답 예시는 [API Reference](API_REFERENCE.md)의 D2 절을 참조하세요. D2의 범위 밖에는 Pack 생성·갱신·내용 diff, multimodal ingestion, Neo4j, 자율 외부 실행이 있습니다.
