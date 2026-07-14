# 실전 실행 결과

실행일은 2026-07-14이다. 격리된 SQLite DB와 workspace를 사용했으며 기존 사용자 DB는
변경하지 않았다.

## 결과 요약

- Pack A와 Pack B 모두 OpenCrab 공식 정적 validator를 통과했다.
- Pack B는 노드 6개, 엣지 6개, Evidence 3개로 구성됐다.
- 두 Pack 모두 OpenOyster compatible admission과 digest 검증을 통과했다.
- Pack A 최초 판단은 LLM 호출 없이 `no_evidence`로 기권했다.
- `kr_no_evidence` Knowledge Request가 생성됐다.
- Pack B를 사용한 최종 D2 실행은 5개 stage가 모두 성공했다.
- Terra가 beliefs, options, scenarios를 생성했다.
- Sol이 critic과 decision을 수행했다.
- replay는 Evidence snapshot과 dossier digest가 모두 일치했다.

## 사용자 체감 변화

Pack A에서는 선택지조차 만들 수 없었다. 답은 “근거가 없어 판단할 수 없음”이었다.

Pack B 이후에는 다음 변화가 생겼다.

- CrabHarness가 Mission-first collection control plane이라는 belief가 생겼다.
- CrabHarness가 검증 후 promotion package를 만든다는 belief가 생겼다.
- Pack의 로컬 생성·검증과 OpenCrab 배포·MCP 접근 책임이 구분됐다.
- promotion package 감사·계약 조사·구현 보류라는 세 선택지가 구성됐다.
- 세 Evidence의 global ID가 cognitive transition에 추가됐다.

최종 판단은 여전히 기권이다. 이유는 `no_evidence`에서 `critic_non_pass`로 바뀌었다.
이는 실패가 아니라 Pack B가 알려 준 것과 아직 알려 주지 않은 것을 구분한 결과다.

Sol critic은 promotion package가 존재한다는 사실만으로 다음을 단정할 수 없다고 판단했다.

- OpenOyster가 package를 실제로 받을 수 있는가
- package schema와 integrity field는 무엇인가
- 승인 상태와 lifecycle은 무엇인가
- Mission control plane이 제공하는 명령과 plugin lifecycle은 무엇인가
- 결정 변화와 provenance를 어떤 계약으로 연결하는가

따라서 다음 실전 입력은 위 내용을 담은 Pack C여야 한다.

## 발견된 제품 결함

1. 한국어 질문과 영어 Pack 사이에서 lexical retrieval이 0건이 됐다. 현재 검색은 교차언어
   의미 검색이 아니다. Mission에 Pack의 영어 기술어를 넣었을 때만 Evidence가 검색됐다.
2. 기존 D1 prompt는 출력 타입 이름만 제공하고 실제 JSON Schema와 Pydantic 불변식을
   제공하지 않았다. 실제 모델은 반복해서 계약 밖 JSON을 반환했다. prompt v7에 schema,
   assertion 규칙, JSON pointer, 폐쇄 코드, citation XOR 규칙을 추가해 해결했다.
3. Knowledge Request는 `--fulfills` 선언만으로 fulfilled가 됐다. Pack B가 원래의 증거 없음은
   해소했지만 critic이 새 핵심 공백을 발견했는데도 transition의 remaining request는 비었다.
   다음 구현은 `claimed_fulfilled`와 `verified_fulfilled`를 분리해야 한다.
4. critic의 gap finding이 자동으로 새 Knowledge Request가 되지 않는다. 이 때문에 정확한
   다음 Pack 요구사항이 dossier에만 남고 실행 가능한 요청 목록에는 남지 않는다.
5. Codex가 잘못된 JSON을 한 번 반환했을 때 deliberation `query_json`에는 schema repair가
   없어 전체 child가 `provider_error`로 종료됐다.
6. stage call DB에는 실제 라우팅 모델 대신 기본 설정값 `gpt-4.1-mini`가 기록됐다. 실제 로그는
   Terra high와 Sol high 실행을 증명하지만 dossier의 model provenance는 부정확하다.
7. OpenCrab validator는 Evidence가 0개인 Pack A도 구조적으로 pass 처리한다. 따라서
   `valid Pack`과 `decision-useful Pack`은 별도 품질 상태로 구분해야 한다.

## 다음 구현 요구사항

우선순위는 다음과 같다.

1. Knowledge Request 증거 충족 검증과 `claimed/verified` 상태 분리
2. critic gap을 새 Knowledge Request로 승격
3. 실제 stage model·effort provenance 저장
4. deliberation JSON schema repair 또는 제한된 동일-stage 재시도
5. Pack manifest의 retrieval hints 또는 다국어 alias를 사용하는 query expansion
6. Pack 구조 검증과 의사결정 적합성 검증 분리

## 2026-07-15 후속 구현

1번과 2번을 구현했다. transition v2는 claimed, verified fulfilled, unverified claimed를
분리한다. `evidence:no_evidence`는 새로 인용된 Evidence가 있을 때만 검증 완료되며,
검증되지 않은 요청은 remaining 목록에 유지된다. critic의 gap finding은 `kr_critic_N`
Knowledge Request로 승격된다.

새 격리 DB smoke test에서도 Pack A의 `abstain/no_evidence`가 Pack B 이후 `select`로
전환됐고, `kr_no_evidence`는 새로 인용된 global Evidence ID 1건과 함께
`verified_fulfilled`로 기록됐다.

## 검증 증거

- OpenCrab static validation: Pack A `pass`, Pack B `pass`
- OpenOyster admission: 두 Pack 모두 `pass`, digest verified
- 최종 실행: run 11, 5 stage 모두 `succeeded`
- 최종 outcome: `abstain`, reason `critic_non_pass`
- transition: Evidence 3개 추가, belief 3개 추가, option 3개 추가
- replay: `matched=true`, snapshot mismatch 0, gate error 0
