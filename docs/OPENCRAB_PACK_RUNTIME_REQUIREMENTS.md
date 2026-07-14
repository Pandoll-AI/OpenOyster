# OpenOyster Pack-Native Cognitive Runtime Requirements

상태: MVP Baseline v0.2
작성 기준일: 2026-07-13
대상: OpenOyster `0.4.0` 이후 재설계
입력 계약: OpenCrab Pack v1

## 0. 현재 구현 기준

첫 제품 완성 범위는 **신뢰된 로컬 directory 형태의 OpenCrab Pack을 잘 사용하는
런타임**이다. 아래의 전체 목표 중 ZIP admission, 자동 update, revision diff,
멀티모달 재분석, watcher, Derived Pack export는 후속 범위다.

현재 필수 수직 경로는 다음과 같다.

1. source Pack을 수정하지 않고 compatible 또는 strict profile로 검증한다.
2. manifest, graph node, graph edge, evidence를 손실 없이 namespaced record로 설치한다.
3. 여러 active Pack을 서로 섞지 않고 검색한다.
4. graph와 evidence를 함께 사용해 질문의 근거 문맥을 찾는다.
5. 답변의 모든 사실 주장에 pack, revision, node/edge/evidence provenance를 붙인다.
6. Pack 근거가 없거나 생성 결과의 citation을 검증할 수 없으면 `unknown`으로 닫는다.

초기 입력은 사용자가 신뢰한다고 명시한 local directory로 제한한다. ZIP과 외부
Pack은 현재 production admission 대상이 아니다. P0-F3 공격 fixture는 삭제하지
않고 후속 ZIP admission의 회귀 자산으로 보존한다.

이 절은 현재 MVP의 범위와 순서를 정한다. 뒤의 요구사항은 최종 제품 방향을
보존하지만, 이 절에서 후속으로 분류한 항목은 현재 MVP 완료를 막지 않는다.

## 1. 결정 요약

OpenOyster의 지식 입력은 OpenCrab Pack으로 한정한다.

OpenOyster는 원시 웹 페이지, 일반 파일, RSS, GitHub 응답을 직접 지식으로
수용하지 않는다. 외부 수집기와 CrabHarness가 원시 자료를 Pack으로 만들고,
OpenOyster는 검증된 Pack을 설치하여 여러 Pack의 지식과 evidence를 연결하고,
근거 있는 답변을 생성한다. 변경 감지, 믿음 수정, 판단·계획은 이 기반 위의 후속
계층이다.

제품 정의는 다음과 같다.

> OpenOyster is a pack-native cognitive runtime that turns OpenCrab Packs into
> grounded answers with verifiable graph and evidence provenance.

OpenCrab Pack은 단순 KG 덤프가 아니다. Pack v1 문서가 정의하는 Pack은 다음을
함께 보존하는 검증·승격 산출물이다.

- canonical graph 계획본
- graph node와 edge를 뒷받침하는 evidence index
- 원본·파싱·OCR·CLIP 산출물의 provenance
- 품질 보고서와 human review 결과
- Neo4j import 재현 자료
- Neo4j에서 다시 추출한 검증 후 graph snapshot
- 배포와 검색을 위한 설명·예시 질의·community report

`PromotionPackage`는 Pack이 아니다. 이는 CrabHarness가 OpenCrab store에 node와
edge를 쓰기 위한 경량 DTO이며, Pack v1의 evidence index, 품질 계약, Neo4j
snapshot, 멀티모달 산출물을 포함하지 않는다. OpenOyster의 입력 계약으로
사용하지 않는다.

## 2. 조사 근거와 권위 순서

이번 명세는 다음 로컬 소스를 직접 대조하여 작성했다.

1. `../OpenCrab/docs/opencrab-pack-v1.md`
   - Pack v1의 의도와 전체 파일 구조를 정의한다.
2. `../OpenCrab/opencrab/pack/validation.py`
   - 현재 코드가 실제로 강제하는 최소 계약이다.
3. `../OpenCrab/tests/test_pack_validation.py`
   - validator의 현재 보장 범위를 확인한다.
4. `../OpenCrab/opencrab/pack/neo4j_export.py`
   - Neo4j 검증 후 snapshot의 실제 출력 형태를 확인한다.
5. `../OpenCrab/docs/localcrab-factory-workflow.md`
   - raw, parsed, OCR, image, CLIP 자료가 evidence로 연결되는 생산 과정을
     설명한다.
6. `../OpenCrab/docs/ontology-authoring-guide.md`
   - evidence-first graph 작성과 promotion 규칙을 설명한다.
7. `../OpenCrab/crabharness/schemas/promotion-package.schema.json`
   - PromotionPackage가 Pack과 다른 계약임을 확인한다.
8. `src/openoyster/models.py`, `src/openoyster/events.py`
   - OpenOyster가 현재 보유한 영속 모델과 event 처리 기반을 확인한다.

관찰된 검증 결과:

- OpenCrab test suite: `154 passed, 3 skipped`
- live Neo4j 검증은 기본 test suite에서 실행되지 않았다.
- 저장소에는 실제 완성형 Pack ZIP fixture가 없다.
- Pack validator test는 테스트 중 생성하는 최소 directory fixture를 사용한다.

따라서 이 명세는 Pack v1 문서와 현재 validator를 모두 지원하되, 문서만으로
보장되지 않는 update·멀티팩·멀티모달 세부 계약은 OpenOyster의 별도 admission
policy로 명시한다.

## 3. Pack v1 실제 계약

### 3.1 문서상 전체 layout

Pack v1 문서는 다음 파일을 required layout으로 정의한다.

```text
manifest.json
graph/nodes.jsonl
graph/edges.jsonl
evidence/index.jsonl
quality/report.json
neo4j/import.cypher
neo4j/opencrab_ingest.jsonl
neo4j/export_status.json
README.md
sample_queries.json
community_reports.json
```

다음 directory와 파일은 optional 또는 recommended이다.

```text
raw/
parsed/
ocr/
images/
clip/
scripts/import_to_neo4j.py
neo4j/import_status.json
```

### 3.2 현재 validator가 강제하는 최소 layout

현재 `validate_pack_static`이 required로 강제하는 파일은 네 개뿐이다.

```text
manifest.json
graph/nodes.jsonl
graph/edges.jsonl
evidence/index.jsonl
```

현재 validator가 실제로 확인하는 항목은 다음과 같다.

- `format_version == opencrab-pack-v1`
- node id 중복과 누락
- canonical MetaOntology space와 node type
- 등록된 type schema가 있을 때 일부 required field와 enum
- edge id 중복과 누락
- edge endpoint 존재 여부
- edge가 주장하는 space와 실제 node space의 일치
- canonical meta-edge relation
- validated/promoted record의 evidence refs 존재 여부
- node·edge evidence refs가 evidence index에서 해소되는지 여부
- 낮은 confidence claim·edge와 parser 불확실성에 대한 human review 후보

### 3.3 문서에는 있으나 현재 validator가 강제하지 않는 항목

- manifest의 pack version, grammar version, created_at, created_by
- license와 source metadata
- manifest counts와 실제 record 수 일치
- file size와 SaaS split/staged-ingest limits
- nodes, edges, evidence, 전체 Pack hash
- hash 계산의 canonical serialization 규칙
- `quality/report.json`의 원래 status와 상세 metric
- parsing, OCR, CLIP, chunk, relationship, multi-hop coverage threshold
- evidence row 자체의 schema, hash, source, parser, location 완전성
- evidence가 가리키는 raw·parsed·image·OCR·CLIP 파일의 실제 존재와 hash
- `neo4j/import.cypher` 존재와 성공 여부
- canonical graph와 `neo4j/opencrab_ingest.jsonl`의 count·내용 일치
- README, sample queries, community reports
- ZIP 안전성, ZIP path traversal, symlink, zip bomb
- publisher signature와 trust chain

현재 validator는 `quality/report.json`을 새로 쓴다. OpenOyster는 source Pack을
불변으로 취급해야 하므로 validator를 source directory에 write mode로 실행하면
안 된다. 원본 품질 보고서와 OpenOyster admission report는 별도로 보존한다.

### 3.4 graph와 evidence의 관계

`graph/nodes.jsonl`과 `graph/edges.jsonl`은 canonical ingest graph이다.

`evidence/index.jsonl`은 단순 검색용 text 모음이 아니다. source artifact,
parser output, OCR region, CLIP context, chunk, graph node, graph edge 사이의
traceability index이다.

Pack 안에서 evidence가 graph endpoint로 쓰이면 다음 두 표현이 모두 필요하다.

- `graph/nodes.jsonl`의 `evidence` space node
- `evidence/index.jsonl`의 상세 provenance row

두 표현은 같은 역할이 아니다. graph node는 reasoning topology를 제공하고,
evidence row는 원본 추적과 asset 해석을 제공한다.

### 3.5 canonical graph와 verified snapshot

Pack v1에는 graph가 두 번 나타난다.

- `graph/nodes.jsonl`, `graph/edges.jsonl`: LocalCrab이 계획한 canonical graph
- `neo4j/opencrab_ingest.jsonl`: Neo4j가 실제로 적재한 후 다시 추출한 graph

OpenOyster는 두 graph를 자동으로 같은 것으로 간주하면 안 된다. 차이가 있으면
Pack의 품질 상태와 reasoning 결과에 반영해야 한다.

현재 Neo4j exporter는 node와 edge를 출력한다. 문서가 허용하는
`kind: evidence` record는 현재 exporter가 생성하지 않는다.

### 3.6 멀티모달의 현재 의미

Pack v1이 구체적으로 설명하는 멀티모달 범위는 image, OCR, CLIP 중심이다.

- OCR evidence: engine, confidence, page/region, low-confidence spans, pass
- CLIP evidence: image hash, embedding id, caption/tags, related chunks
- optional asset directory: `ocr/`, `images/`, `clip/`

audio와 video에 대한 표준 evidence schema는 현재 Pack v1에 없다. OpenOyster가
audio와 video를 지원하려면 Pack v1의 자유 형식 evidence row를 해석하는
OpenOyster profile을 추가하거나 OpenCrab Pack vNext를 제안해야 한다.

### 3.7 update와 멀티팩의 현재 한계

Pack v1 manifest에는 `pack_id`와 `version`이 있지만 다음 의미가 정의되지 않았다.

- 이전 version과의 parent lineage
- full replacement인지 partial delta인지 여부
- 삭제된 node·edge를 나타내는 tombstone
- record의 valid-from, valid-to
- 다른 Pack에 대한 dependency
- cross-Pack stable identity
- publisher signature와 authority

node id는 문서상 “Pack 안에서 안정적”이면 충분하다. 따라서 서로 다른 Pack의
같은 문자열 id를 동일 entity로 합쳐서는 안 된다.

### 3.8 schema pack 관련 주의점

문서는 설치된 schema pack이 추가 node type을 허용할 수 있다고 설명한다.
그러나 현재 `validate_node`는 static MetaOntology manifest의 node type만
허용한다. schema pack 설치는 type schema file을 생성하지만 canonical grammar의
허용 node type을 확장하지 않는다.

OpenOyster는 “schema pack이 설치됐으므로 custom node type이 유효하다”고
추론하면 안 된다. 이 동작은 OpenCrab upstream에서 먼저 명확해져야 한다.

## 4. 제품 범위

### 4.1 포함

- Pack directory와 Pack ZIP admission
- Pack format·integrity·quality 검증
- source Pack 불변 저장과 content-addressed 식별
- 동일 pack_id의 여러 version 관리
- version diff와 영향 전파
- 여러 Pack의 namespace 분리와 federated query
- cross-Pack identity 후보와 conflict 보존
- text, structured evidence, image, OCR, CLIP 처리
- Pack이 선언한 audio·video asset 처리
- graph와 evidence를 함께 사용하는 retrieval
- Pack evidence만을 근거로 하는 belief·hypothesis·answer·decision
- stale 또는 missing evidence에 대한 Pack refresh request
- derived knowledge와 결과의 OpenCrab-compatible Pack export

### 4.2 제외

- 임의 URL, RSS, GitHub, inbox file을 core cognition input으로 직접 수용
- 일반 crawler 또는 검색 엔진
- provenance 없는 model memory를 사실로 저장
- label이 같다는 이유만으로 cross-Pack entity 자동 병합
- source Pack 내부 파일 수정
- Pack evidence 없이 외부 모델 지식을 canonical belief로 승격
- OpenCrab marketplace와 private SaaS 구현
- 초기 단계의 무제한 자율 실행

기존 raw connector는 migration 동안 `legacy-ingest` 경계 뒤에 둘 수 있다. Pack
runtime이 완성된 뒤 core mode에서는 비활성화한다.

## 5. 핵심 용어

- Source Pack: 외부 producer가 만든 원본 Pack. 설치 후 수정하지 않는다.
- Pack Installation: source Pack 한 개를 content digest로 식별한 설치 record.
- Pack Revision: 같은 pack_id에 속한 특정 version과 digest.
- Active Revision: 현재 world view에 사용되는 Pack revision.
- Pack-local ID: source Pack 내부의 node, edge, evidence id.
- Global Record ID: Pack revision namespace를 포함한 OpenOyster 내부 id.
- Federated View: 여러 active Pack revision을 논리적으로 함께 질의하는 view.
- Identity Assertion: 서로 다른 Pack record가 동일하거나 관련됐다는 별도 주장.
- Belief: 하나 이상의 Pack record와 evidence에서 파생된 수정 가능한 판단 상태.
- Invalidation: source record 변경으로 derived belief를 재검토 대상으로 만드는 것.
- Refresh Request: 부족하거나 오래된 evidence를 새 Pack으로 요청하는 산출물.
- Derived Pack: OpenOyster가 reasoning 결과를 OpenCrab Pack 계약으로 내보낸 것.

## 6. 기능 요구사항

각 요구사항의 우선순위는 다음을 사용한다.

- P0: 안전한 Pack 입력을 위해 구현 전에 반드시 필요
- P1: 첫 유용한 pack-native runtime에 필요
- P2: 확장 단계

### 6.1 Pack admission

#### FR-ADM-001 Pack input

우선순위: P0

시스템은 local directory와 ZIP을 입력으로 받아야 한다. ZIP은 임시 격리
directory에 안전하게 풀고 검증 후에만 content store로 이동한다.

#### FR-ADM-002 archive safety

우선순위: P0

시스템은 absolute path, `..` traversal, symlink escape, duplicate path,
case-collision, 과도한 compression ratio, file count, uncompressed bytes를
검사해야 한다. 실패한 archive는 quarantine하고 일부 file도 설치하지 않는다.

#### FR-ADM-003 immutable source

우선순위: P0

source Pack은 byte-level digest로 식별하고 설치 후 수정하지 않는다. 공식
OpenCrab validator를 호출할 때도 `write_report=False` 또는 격리 복사본을
사용한다.

#### FR-ADM-004 admission profiles

우선순위: P0

시스템은 최소 세 admission profile을 제공해야 한다.

- strict: Pack v1 문서의 전체 required layout과 OpenOyster integrity gate 통과
- compatible: 현재 OpenCrab validator가 강제하는 네 파일과 static gate 통과
- quarantine: 읽기·검사만 허용하고 reasoning world view에는 포함하지 않음

compatible Pack은 품질이 낮다는 뜻이 아니라 검증 정보가 부족하다는 뜻이다.
정책이 별도로 허용하지 않는 한 autonomous decision에는 사용할 수 없다.

#### FR-ADM-005 manifest validation

우선순위: P0

시스템은 format_version, pack_id, version, grammar_version, created_at,
created_by, license, source, counts, limits, quality, hashes, artifacts를 파싱하고
검증해야 한다. 누락 field는 profile별로 fail 또는 degraded 상태를 만든다.

#### FR-ADM-006 content integrity

우선순위: P0

시스템은 다음을 계산해야 한다.

- 전체 입력 archive 또는 normalized directory digest
- 각 graph/evidence/asset file digest
- 실제 file count와 byte count
- 실제 node, edge, evidence count

manifest에 hash나 count가 선언돼 있으면 실제 값과 비교한다. Pack v1 문서에는
`pack_sha256`의 canonical 계산법이 없으므로, source가 선언한 값과 별개로
OpenOyster digest를 항상 저장한다.

#### FR-ADM-007 graph and evidence validation

우선순위: P0

공식 OpenCrab static validator 결과를 보존하고 다음 OpenOyster strict check를
추가해야 한다.

- evidence row id 중복
- evidence required field와 kind별 schema
- asset path와 hash 해소
- graph node·edge의 evidence reference 완전성
- quality report와 manifest quality 일치
- canonical graph와 Neo4j snapshot count·record 차이
- grammar version 호환성
- declared schema pack 존재와 호환성

#### FR-ADM-008 original quality preservation

우선순위: P0

Pack에 포함된 `quality/report.json`, human review, final decision을 원본 그대로
보존해야 한다. OpenOyster가 생성한 admission report는 별도 table과 artifact로
저장한다.

#### FR-ADM-009 transactional installation

우선순위: P0

Pack registry, file inventory, graph records, evidence records, admission result는
하나의 설치 transaction으로 commit되어야 한다. 실패 시 active world view에
일부 record가 노출되면 안 된다.

#### FR-ADM-010 idempotent reinstallation

우선순위: P0

동일 content digest를 다시 설치하면 no-op이어야 한다. 같은 pack_id와 version에
다른 digest가 들어오면 충돌로 기록하고 자동 교체하지 않는다.

### 6.2 Pack registry와 version update

#### FR-VER-001 revision identity

우선순위: P0

revision identity는 최소 `(pack_id, version, source_digest)`로 구성한다. version
문자열만으로 content identity를 결정하지 않는다.

#### FR-VER-002 activation and rollback

우선순위: P1

같은 pack_id에서 한 revision만 active 상태가 될 수 있어야 한다. 새 revision을
검증한 후 원자적으로 활성화하며 이전 revision으로 rollback할 수 있어야 한다.

#### FR-VER-003 deterministic diff

우선순위: P1

같은 pack_id의 revision 사이에서 node, edge, evidence, asset의 added, modified,
missing을 계산해야 한다. 비교는 pack-local stable id와 normalized record hash를
사용한다.

#### FR-VER-004 missing is not deletion

우선순위: P0

Pack v1에는 tombstone 규칙이 없다. 새 revision에서 record가 보이지 않는다는
이유만으로 삭제 사실을 추론하지 않는다. 기본 상태는 `missing_in_revision`이다.
명시적 replacement policy 또는 향후 tombstone extension이 있을 때만 retired로
전환한다.

#### FR-VER-005 invalidation propagation

우선순위: P1

변경되거나 missing 상태가 된 node, edge, evidence를 사용한 belief, hypothesis,
answer, decision artifact를 stale 또는 review-required로 표시해야 한다.

#### FR-VER-006 Pack feed watcher

우선순위: P1

시스템은 local directory 또는 Pack registry에서 새로운 Pack artifact를 감지할
수 있다. watcher는 Pack만 내려받을 수 있으며 raw source를 cognition input으로
우회해서는 안 된다.

### 6.3 멀티팩 federation

#### FR-FED-001 namespace isolation

우선순위: P0

모든 node, edge, evidence id는 내부적으로 Pack revision namespace를 포함해야
한다. 예시는 다음과 같다.

```text
opencrab://{pack_id}@{version}/{source_digest}/node/{local_id}
```

source id는 표시와 round-trip export를 위해 별도로 보존한다.

#### FR-FED-002 no implicit label merge

우선순위: P0

동일 label, 동일 local id, 유사 embedding만으로 record를 합치지 않는다.

#### FR-FED-003 identity assertions

우선순위: P1

cross-Pack identity는 별도 assertion으로 저장한다. assertion에는 relation,
confidence, method, supporting Pack evidence, reviewer status가 있어야 한다.

허용 초기 relation:

- same_as
- probably_same_as
- related_to
- supersedes
- conflicts_with
- independent_of

#### FR-FED-004 conflict preservation

우선순위: P1

서로 다른 Pack의 상충 claim을 하나의 정리된 claim으로 덮어쓰지 않는다.
각 claim, evidence, source Pack, 관찰 시점, authority를 유지한 채 conflict set을
구성한다.

#### FR-FED-005 federated view policy

우선순위: P1

query와 reasoning은 사용할 Pack, active revision, license, quality profile,
trust policy를 명시한 federated view에서 실행되어야 한다.

### 6.4 evidence와 멀티모달 처리

#### FR-EVD-001 evidence-first reasoning

우선순위: P0

OpenOyster가 생성하는 사실 주장, belief, hypothesis, answer는 하나 이상의
namespaced Pack evidence reference를 가져야 한다. evidence가 없으면 fact가
아니라 assumption 또는 question으로 표시한다.

#### FR-EVD-002 asset resolver

우선순위: P0

evidence row가 참조하는 asset은 Pack root 안의 상대 경로 또는 hash가 포함된
content-addressed URI로만 해소한다. hash가 일치하지 않는 asset은 사용하지 않는다.

#### FR-EVD-003 evidence schema normalization

우선순위: P0

최소 normalized evidence model은 다음 정보를 보존해야 한다.

- evidence id와 kind
- source URL·path·title
- content hash와 media type
- collected/created timestamp
- parser status, method, version, warning
- OCR engine, pass, confidence, region, low-confidence spans
- CLIP 또는 vision model, embedding id, caption, tag
- document, page, section, region, chunk, row, frame, time range
- linked node ids와 edge ids
- 원본 evidence와 derived evidence의 lineage

#### FR-EVD-004 multimodal analyzers

우선순위: P1

analyzer는 다음 modality adapter를 제공해야 한다.

- text와 structured record
- PDF page와 document region
- image와 OCR
- image semantic description 또는 CLIP-compatible context
- audio segment와 ASR
- video frame·segment와 temporal transcript

Pack v1에 명시되지 않은 audio·video field는 OpenOyster profile로 validation하고,
원본 evidence row를 손실 없이 보존한다.

#### FR-EVD-005 derived evidence provenance

우선순위: P0

OpenOyster가 asset을 재분석하면 결과를 source Pack에 쓰지 않는다. 내부 derived
evidence에 input digest, model, model version, prompt/config digest, output digest,
생성 시각을 기록한다.

#### FR-EVD-006 modality disagreement

우선순위: P1

native text, OCR passes, caption, ASR, vision analysis가 상충하면 하나를 조용히
선택하지 않는다. disagreement와 선택 근거를 기록하고 중요 claim은 review 또는
추가 Pack 요청 대상으로 만든다.

#### FR-EVD-007 bounded processing

우선순위: P0

Pack과 modality별 bytes, pages, pixels, duration, frames, model calls, cost, timeout
한도를 적용해야 한다.

### 6.5 graph, retrieval, reasoning

#### FR-RSN-001 dual graph views

우선순위: P1

canonical graph와 Neo4j verified snapshot을 별도 view로 저장한다. 차이가 없음을
확인한 경우에만 하나의 verified view로 표시한다.

#### FR-RSN-002 Pack-aware retrieval

우선순위: P1

retrieval은 text 검색만이 아니라 graph traversal, evidence resolution,
multimodal derived context, Pack quality와 revision을 함께 사용해야 한다.

#### FR-RSN-003 answer provenance

우선순위: P0

각 answer claim은 사용한 pack_id, version, source digest, node/edge/evidence id를
반환해야 한다. 여러 Pack이 상충하면 상충 사실과 각 근거를 함께 반환한다.

#### FR-RSN-004 belief revision

우선순위: P1

belief는 support, contradict, timestamps edge와 Pack quality, freshness,
cross-Pack independence를 사용해 revision된다. model confidence를 evidence
confidence로 대체해서는 안 된다.

#### FR-RSN-005 knowledge boundary

우선순위: P0

모델의 사전 지식은 query 해석과 가설 생성에는 사용할 수 있으나 canonical fact
근거로 사용할 수 없다. Pack에 근거가 없으면 `unknown`, `assumption`,
`needs_refresh` 중 하나로 응답한다.

#### FR-RSN-006 decision and plan artifacts

우선순위: P2

고수준 판단은 목표, 대안, 제약, support, contradiction, uncertainty, selected
action, policy gate를 포함해야 한다. 모든 중요한 판단 근거는 Pack evidence로
역추적돼야 한다.

### 6.6 refresh와 closed loop

#### FR-REF-001 refresh request

우선순위: P1

OpenOyster는 stale, missing, conflicting evidence를 발견하면 raw fetch를 직접
수행하는 대신 Pack producer가 처리할 refresh request를 생성한다.

request에는 다음이 포함돼야 한다.

- target pack_id와 active version
- 요청 이유와 영향을 받는 belief
- 필요한 source 범위와 modality
- 해결해야 할 질문
- freshness 기대치
- 품질·evidence 요구사항
- idempotency key

#### FR-REF-002 Pack-only feedback

우선순위: P1

collector나 실행 도구의 결과가 새로운 지식으로 사용되려면 Observation Pack
또는 updated Pack으로 돌아와 admission gate를 통과해야 한다.

#### FR-REF-003 derived Pack output

우선순위: P2

OpenOyster는 합성 claim, conflict, decision, outcome을 OpenCrab-compatible Derived
Pack으로 export할 수 있어야 한다. source Pack evidence reference와 derivation
lineage를 보존해야 한다.

## 7. 영속 데이터 요구사항

기존 `Document`, `Chunk`, `Entity`, `Claim` table에 Pack record를 바로 합치지
않는다. source Pack의 namespace와 revision을 잃기 때문이다.

최소 신규 aggregate는 다음과 같다.

### PackInstall

- pack_id
- declared_version
- format_version
- grammar_version
- source_digest
- source_type와 source_location
- admission_profile
- status: quarantined, installed, active, superseded, rejected
- original_manifest_json
- original_quality_json
- admission_report_json
- created_at, activated_at

### PackFile

- pack_install_id
- relative_path
- role
- media_type
- declared_hash
- computed_hash
- bytes
- storage_uri
- validation_status

### PackNodeVersion

- pack_install_id
- local_node_id
- global_node_id
- space
- node_type
- label
- properties_json
- quality_json
- record_hash
- evidence_refs_json

### PackEdgeVersion

- pack_install_id
- local_edge_id
- global_edge_id
- namespaced endpoints
- spaces와 relation
- properties_json
- confidence
- record_hash
- evidence_refs_json

### PackEvidenceVersion

- pack_install_id
- local_evidence_id
- global_evidence_id
- kind
- source_json
- parser_json
- ocr_json
- vision_json
- location_json
- links_json
- content_hash
- asset reference
- raw_record_json

### PackDiff

- previous_install_id와 next_install_id
- added, modified, missing record counts
- detailed change artifact
- activation status

### IdentityAssertion

- left_global_id와 right_global_id
- relation
- confidence
- method
- evidence refs
- review status

### BeliefRevision

- belief identity와 revision
- statement
- status
- supporting·contradicting Pack evidence
- source Pack revisions
- invalidation reason
- reasoning run과 policy

기존 `Hypothesis`, `EvidenceEdge`, `Artifact`, `DecisionTrace`는 이 계층 위에
연결한다. 특히 `EvidenceEdge`에는 Pack evidence FK가 필요하다.

## 8. event 요구사항

최소 event type은 다음과 같다.

```text
pack.received
pack.quarantined
pack.validated
pack.installed
pack.revision_detected
pack.diff_computed
pack.activated
pack.rolled_back
pack.record_changed
pack.identity_candidate_created
pack.conflict_detected
evidence.asset_resolved
evidence.analysis_completed
belief.invalidated
belief.revision_requested
belief.revised
pack.refresh_requested
derived_pack.created
```

모든 event는 pack installation id, correlation id, idempotency key를 가져야 한다.

## 9. CLI와 API 요구사항

MVP-P1 구현 CLI:

```text
openoyster pack validate PATH [--profile strict|compatible]
openoyster pack install PATH [--profile strict|compatible]
openoyster pack list
openoyster pack show PACK_ID
openoyster pack query "QUESTION" --packs PACK_ID,...
```

후속 단계 후보 CLI:

```text
openoyster pack files PACK_ID
openoyster pack diff PACK_ID OLD_VERSION NEW_VERSION
openoyster pack activate PACK_ID VERSION
openoyster pack rollback PACK_ID VERSION
openoyster pack conflicts
openoyster pack refresh-request PACK_ID --reason TEXT
```

MVP-P1 API는 validate, install, list, show, query resource를 제공한다.
validate, install, query는 API key 인증을 요구하며 raw asset body와 로컬 경로를 반환하지 않는다.
후속 asset 접근 API는 권한, license, size limit를 적용한 별도 endpoint로 설계한다.

## 10. 비기능 요구사항

### NFR-001 재현성

동일한 active Pack revision set, policy, model/config digest로 reasoning run을
재현할 수 있어야 한다.

### NFR-002 provenance 완전성

answer와 decision의 모든 중요한 claim은 source Pack evidence까지 역추적 가능해야
한다. provenance가 끊기면 완료가 아니라 degraded 결과다.

### NFR-003 확장성

JSONL은 전체 memory load 없이 streaming ingest해야 한다. Pack v1 문서의 warning
threshold인 100 MB ZIP, 100,000 nodes, 300,000 edges, 500,000 evidence rows,
20,000 files를 최소 설계 기준으로 사용한다.

### NFR-004 transaction과 rollback

Pack install과 activation은 원자적이어야 한다. reasoning 중 active revision이
바뀌면 run은 시작 시점의 revision set을 계속 사용하거나 명시적으로 재시작한다.

### NFR-005 보안

archive extraction, asset parsing, HTML rendering, media decoder를 신뢰 경계로
취급한다. untrusted Pack은 network와 process 권한이 제한된 sandbox에서 처리한다.

### NFR-006 license와 policy

Pack license scope와 source policy를 저장하고 federated view, asset access, derived
Pack export에 적용한다. 알 수 없는 license를 permissive로 해석하지 않는다.

### NFR-007 관찰 가능성

Pack별 ingest duration, bytes, record count, validation issue, modality cost, diff
size, invalidated belief count, query provenance coverage를 기록한다.

### NFR-008 portability

SQLite 단일 host에서 시작하되 대용량 Pack과 병렬 worker를 위해 PostgreSQL과
external object storage로 이동할 수 있어야 한다.

## 11. 수용 기준

현재 MVP의 필수 수용 기준은 AC-001, AC-004의 동일 digest no-op, AC-006,
AC-010, AC-012다. 여기에 다음 수직 경로 기준을 추가한다.

### AC-MVP-001 Pack 질문의 끝단 동작

trusted directory Pack을 설치·활성화한 뒤 CLI와 API에서 질문하면 관련 node,
edge, evidence를 찾고, 답변과 검증 가능한 citation을 한 응답으로 반환한다.

### AC-MVP-002 생성 결과 fail-closed

LLM이 검색 결과에 없는 evidence id를 인용하거나 사실 주장에 evidence를 붙이지
않으면 그 출력을 supported answer로 반환하지 않는다. 결과는 `unknown` 또는
명시적인 degraded 상태가 된다.

### AC-001 최소 Pack 설치

OpenCrab test fixture와 동등한 네 파일 Pack을 compatible mode로 설치하고 node,
edge, evidence를 namespace를 유지한 채 질의할 수 있다.

### AC-002 strict Pack 거부

Neo4j snapshot, quality report, marketplace metadata가 없는 Pack은 strict mode에서
실패하고 compatible 또는 quarantine 전환 이유를 반환한다.

### AC-003 ZIP 공격 방어

path traversal, symlink escape, zip bomb fixture가 어떤 source file도 Pack store
밖에 쓰지 못한다.

### AC-004 중복과 충돌

동일 digest 재설치는 no-op이고, 같은 pack_id/version의 다른 digest는 충돌로
기록된다.

### AC-005 version diff

node property 변경, edge 추가, evidence 수정, record 누락을 정확히 분류한다.
누락 record를 자동 삭제하지 않는다.

### AC-006 멀티팩 namespace

두 Pack이 같은 `node:1`을 사용해도 서로 다른 global id로 저장되고, identity
assertion 전에는 자동 병합되지 않는다.

### AC-007 conflict-aware answer

두 Pack이 상충 claim을 제공하면 한쪽을 숨기지 않고 source version과 evidence를
각각 제시한다.

### AC-008 update invalidation

active Pack revision 변경으로 supporting evidence가 수정되면 관련 belief와 answer
artifact가 stale 상태가 된다.

### AC-009 multimodal traceability

image/OCR/CLIP 및 audio/video fixture에서 생성된 derived evidence가 원본 asset
hash와 정확한 region 또는 time range로 역추적된다.

### AC-010 knowledge boundary

Pack에 없는 사실 질문에 모델이 알고 있는 내용을 canonical fact처럼 답하지 않고
unknown 또는 refresh request로 처리한다.

### AC-011 rollback

이전 Pack revision 활성화 시 이전 federated view가 복구되고 해당 revision set으로
reasoning을 재실행할 수 있다.

### AC-012 source immutability

validate, install, query, multimodal analysis 후 source Pack의 모든 file digest가
변하지 않는다.

## 12. 구현 순서

### MVP Phase 0: 계약과 fixture

상태: 완료

- minimal four-file fixture
- full documented-layout fixture
- invalid archive와 broken provenance 회귀 fixture
- source Pack 불변성 검사

### MVP Phase 1: trusted-directory Pack Runtime

우선순위: 현재

- directory-only compatible/strict validation
- immutable digest와 설치 registry
- PackInstall, PackFile, PackNode/Edge/Evidence models
- active Pack과 namespaced ids
- graph+evidence retrieval
- citation 검증이 포함된 grounded answer
- knowledge-boundary fail-closed
- CLI와 API의 install/list/show/query 수직 경로

완료 gate:

- AC-001, AC-004 동일 digest no-op, AC-006, AC-010, AC-012 통과
- AC-MVP-001과 AC-MVP-002 통과
- source digest 전후 동일
- 전체 `make check`와 Opus review 통과

### 후속 Phase A: 안전한 외부 admission과 versioning

- safe ZIP extraction과 quarantine
- revision diff, activation rollback, invalidation
- conflict set과 identity assertion

### 후속 Phase B: multimodal과 continuous revision

- image/OCR/CLIP, audio/video adapter
- derived evidence와 modality disagreement
- watcher, refresh request, belief revision

### 후속 Phase C: 고수준 자율 활용

- decision과 plan artifact
- policy-bounded action
- Derived Pack export

## 13. 구현 전 결정이 필요한 항목

다음 항목은 명세만으로 확정할 수 없으며 upstream 또는 제품 결정이 필요하다.

1. Pack v1 문서의 11개 required file을 OpenCrab 공식 validator도 강제할지
2. Pack version update가 full replacement인지 delta도 허용할지
3. deletion tombstone과 validity interval을 Pack vNext에 추가할지
4. publisher signature와 trust root를 어떤 형식으로 정의할지
5. `pack_sha256`의 canonical 계산 방법
6. custom schema pack이 canonical grammar를 확장하는 정확한 방법
7. audio/video evidence schema를 OpenOyster profile로 먼저 만들지 Pack vNext에서
   공동 정의할지
8. Derived Pack의 creator, license, upstream evidence reference 규칙
9. content-addressed remote asset을 허용할지 embedded asset만 허용할지

이 결정이 끝나기 전에도 MVP Phase 1의 trusted-directory compatible admission과
grounded query는 구현할 수 있다. update 삭제 처리, cross-Pack 자동
canonicalization, fully autonomous decision은 시작하지 않는다.

## 14. 현재 OpenOyster와의 gap

현재 OpenOyster는 durable event processing, lease, retry, hypothesis, evidence,
artifact, evaluation 기반을 갖고 있다. 이 부분은 재사용한다.

새로 필요한 핵심은 다음이다.

- raw document 중심 intake를 Pack admission으로 교체
- Pack source와 revision을 보존하는 별도 영속 모델
- graph node·edge의 lossless 저장
- evidence index와 asset lineage 저장
- Pack-aware retrieval
- multi-Pack namespace와 identity/conflict layer
- Pack diff에 따른 belief invalidation
- multimodal analyzer와 resource budget
- Refresh Request와 Derived Pack output

가장 큰 설계 위험은 OpenCrab node를 기존 OpenOyster `Entity`로, evidence row를
`Document/Chunk`로 즉시 변환하는 것이다. 이 방식은 source record의 lossless
round-trip, Pack version diff, cross-Pack conflict, graph relation, multimodal
lineage를 잃는다. 원본 Pack 계층을 먼저 보존한 뒤 cognition model이 이를
참조해야 한다.
