# OpenOyster 목표 지향 로드맵

> 목표부터 읽는 로드맵입니다. 이 문서는 현재 코드 구조 설명이 아니라, OpenOyster가 어떤 사용자 가치를 향해 가야 하는지 정의합니다.

## 1. 최종 목표

OpenOyster의 최종 목표는 정보를 많이 모으는 앱이 아니다.

**OpenOyster는 흩어진 정보 흐름을 지속적으로 관찰하고, 중요한 변화 신호를 찾아, 검증 가능한 가설·근거·반대근거·의사결정 산출물로 전환하는 오픈소스 Evidence-to-Decision OS가 되는 것을 목표로 한다.**

사용자는 OpenOyster를 통해 더 빨리 읽고, 더 깊게 의심하고, 더 투명하게 판단하며, 시간이 지날수록 자신의 판단 체계를 개선할 수 있어야 한다.

## 2. 제품 정체성

OpenOyster는 다음 중간 지점을 지향한다.

- Research assistant보다 깊다.
- Autonomous agent보다 안전하다.
- BI dashboard보다 해석적이다.
- RAG chatbot보다 추적 가능하다.
- Workflow automation보다 판단 중심이다.

제품 포지션:

> **Evidence-to-Decision OS**
>
> 흩어진 자료를 판단 가능한 가설, 근거, 반대근거, 결정 산출물로 바꾸는 인텔리전스 런타임.

피해야 할 포지션:

- 범용 챗봇.
- 요약봇.
- 자동 에이전트 프레임워크.
- 단순 RAG 검색 UI.
- 예쁜 대시보드 우선 제품.
- 승인 없는 외부 action executor.

## 3. 핵심 사용자 가치

### 개인 리서처

사용자 목표:

- 읽어야 할 자료를 줄인다.
- 중요한 신호와 반대근거를 놓치지 않는다.
- 가설 중심 리서치 노트를 만든다.

성공 경험:

> “자료 더미가 아니라 검토할 가설 목록이 생겼다.”

### 전략/기획 담당자

사용자 목표:

- 시장, 정책, 기술 변화에서 의사결정 신호를 빠르게 찾는다.
- 경영진이나 팀에 근거 있는 memo를 제공한다.
- 과거 판단의 근거와 결과를 추적한다.

성공 경험:

> “회의 전에 핵심 가설, 근거, 반대근거, 결정 포인트가 이미 정리돼 있다.”

### 엔지니어/오픈소스 빌더

사용자 목표:

- agent 시스템을 만들 때 durable state, event log, retry, evaluation을 처음부터 갖춘다.
- LLM workflow를 운영 가능한 구조로 확장한다.

성공 경험:

> “데모가 아니라 운영 가능한 agent runtime의 뼈대를 얻었다.”

### 팀/조직

사용자 목표:

- 팀의 정보 흐름을 공유 intelligence memory로 만든다.
- 누가 어떤 판단을 왜 했는지 추적한다.
- AI 산출물을 검토, 승인, 피드백, 개선한다.

성공 경험:

> “우리 팀의 판단 과정이 문서, 근거, 피드백과 함께 축적된다.”

## 4. North Star

가장 중요한 지표:

> **실제 의사결정에 사용된 근거 연결 가설 수**
>
> Evidence-backed hypotheses used in real decisions.

보조 지표:

- 가설 중 2개 이상 근거가 연결된 비율.
- 반대근거가 붙은 가설 비율.
- `useful` 또는 `used` 피드백을 받은 artifact 비율.
- stale hypothesis가 제때 review된 비율.
- source diversity가 일정 기준 이상인 artifact 비율.
- false-positive alert 감소율.
- decision memo 생성까지 걸린 시간.
- 사용자가 직접 읽어야 하는 문서 수 감소율.

## 5. 목표 체계

### A. 수집 목표

목표:

> 다양한 정보원을 안전하게 읽고, 모든 입력에 provenance를 남긴다.

단계:

1. 파일, URL, RSS, GitHub.
2. 웹페이지, 검색, 이메일, Drive, Slack.
3. DB, BI, 공시, 논문, 정책 API.
4. 조직별 connector SDK.

원칙:

- source credential은 저장하지 않는다.
- read connector와 write action을 분리한다.
- 모든 document는 source, timestamp, parser version, content hash를 가진다.
- connector는 size, timeout, SSRF/path traversal 방어를 가져야 한다.

### B. 이해 목표

목표:

> 문서 더미를 검토 가능한 지식 그래프로 바꾼다.

핵심 객체:

- claim
- signal
- entity
- risk
- opportunity
- contradiction
- hypothesis
- evidence edge

좋은 이해 결과는 요약문이 아니라, 나중에 검증하고 반박할 수 있는 구조화된 관찰이어야 한다.

### C. 가설 목표

목표:

> 답변이 아니라 가설을 중심 객체로 만든다.

좋은 가설의 조건:

- 검증 가능하다.
- 범위가 있다.
- 지지 근거가 있다.
- 반대근거를 받을 수 있다.
- 시간이 지나면 강화, 약화, 폐기된다.
- 결정이나 추가 작업으로 이어질 수 있다.

### D. 의사결정 목표

목표:

> 예쁜 보고서가 아니라 decision artifact를 만든다.

주요 산출물:

- hypothesis brief
- counter-evidence memo
- decision memo
- risk watchlist
- market signal digest
- policy drift report
- falsification checkpoint

좋은 산출물은 항상 다음을 포함한다.

- 핵심 주장.
- 근거.
- 반대근거.
- 불확실성.
- 다음 확인 지점.
- 추천 행동 또는 보류 사유.

### E. 피드백 목표

목표:

> 시스템이 실제 사용자 결과로 “무엇이 좋은 산출물인가”를 배운다.

피드백 종류:

- useful
- used
- rejected
- stale
- wrong
- too noisy
- missing evidence
- good counterpoint
- decision adopted
- decision reversed

정책 튜닝은 모델의 자기칭찬이 아니라 실제 라벨, replay, shadow evaluation을 기반으로 해야 한다.

### F. 안전 목표

목표:

> 자율성을 갖되, 위험한 자동화를 승인 경계 밖으로 내보내지 않는다.

원칙:

- 읽기는 자동화 가능.
- 해석은 자동화 가능.
- 제안은 자동화 가능.
- 외부 쓰기, 삭제, 전송, 배포, 결제는 승인 필요.
- mission change는 승인 필요.
- policy promotion은 검증 필요.

## 6. 단계별 로드맵

### Phase 1. 신뢰 가능한 개인용 알파

목표:

- 로컬에서 쉽게 실행된다.
- 문서를 넣으면 가설과 근거가 나온다.
- provenance를 확인할 수 있다.
- README만 보고 10분 안에 demo가 된다.

성공 기준:

- 설치 실패율이 낮다.
- 예제 corpus에서 signal, hypothesis, artifact가 생성된다.
- evidence inspection이 가능하다.
- 테스트와 문서가 일치한다.

### Phase 2. 리서처용 실전 도구

목표:

- RSS, GitHub, 웹페이지, 폴더 감시를 안정적으로 지원한다.
- weekly intelligence digest를 만든다.
- 반대근거 탐색을 강화한다.
- 가설 lifecycle을 관리한다.

핵심 기능:

- watchlist
- hypothesis status
- stale hypothesis review
- source diversity scoring
- evidence quality scoring
- recurring digest

성공 기준:

> 사용자가 “읽을 문서 목록”보다 “검토할 가설 목록”을 보게 된다.

### Phase 3. 팀용 Decision Intelligence

목표:

- 여러 사람이 evidence와 artifact에 피드백한다.
- approval queue를 통해 외부 action과 정책 변경을 통제한다.
- 팀 단위 dashboard와 audit history를 제공한다.

핵심 기능:

- user identity
- RBAC
- artifact review workflow
- approval records
- team feedback metrics
- decision outcome tracking

성공 기준:

> 팀 회의와 전략 검토에 OpenOyster artifact가 실제로 사용된다.

### Phase 4. 운영 가능한 Intelligence Platform

목표:

- 장기 실행과 장애 복구를 견딘다.
- 대규모 source와 worker를 처리한다.
- connector ecosystem을 갖춘다.

핵심 기능:

- OpenTelemetry
- backup/restore tests
- distributed worker
- vector/hybrid retrieval
- connector SDK
- deployment templates
- load/chaos tests

성공 기준:

> 실제 조직의 지속 운영에 견딘다.

### Phase 5. Intelligence OS

목표:

> 조직의 정보 흐름, 판단, 피드백, 결과가 하나의 학습 루프로 연결된다.

사용자가 지속적으로 답할 수 있어야 하는 질문:

- 무엇을 알아야 하는가?
- 무엇이 바뀌었는가?
- 무엇을 믿을 수 있는가?
- 무엇을 결정해야 하는가?
- 지난 판단은 맞았는가?

## 7. 사용자 경험 목표

### 첫 사용

> “챗봇이 아니라 내 자료를 판단 가능한 구조로 바꾸는 시스템이구나.”

### 일주일 사용

> “내가 놓쳤을 신호를 잡아주고, 근거와 반대근거까지 같이 준다.”

### 한 달 사용

> “우리 팀의 판단 과정이 축적되고 있다.”

### 장기 사용

> “이 시스템은 단순히 답하는 게 아니라, 우리가 무엇을 보고 믿고 결정했는지 기억한다.”

## 8. 비목표

당분간 목표로 하지 않는다.

- 모든 것을 자동 결정하는 agent.
- 범용 챗봇.
- 단순 RAG 검색 UI.
- 모델 provider wrapper.
- 자동 웹 크롤러.
- 예쁜 대시보드 우선 제품.
- 승인 없는 외부 action executor.

OpenOyster의 핵심은 계속 이것이다.

> **AI 자동화가 아니라, 감사 가능한 판단 인프라.**
