# OpenCrab Pack → OpenOyster 결정 연속성 실험

이 실험은 빈 증거 Pack에서 기권한 판단이 실제 공개 문서 근거를 담은 새 Pack을 받은 뒤
어떻게 변하는지 확인한다.

## 실험 단계

1. `generate_packs.py`로 Pack A와 Pack B를 생성한다.
2. 두 Pack을 OpenCrab 정적 validator로 검사한다.
3. 격리된 OpenOyster DB와 workspace에 Pack A를 설치한다.
4. `mission.json`으로 최초 판단을 실행하고 Knowledge Request를 확인한다.
5. Pack B를 설치하고 `kr_no_evidence`를 충족 대상으로 지정해 이어서 판단한다.
6. cognitive transition, dossier, citations, replay 결과를 기록한다.

## Pack 역할

- `alexai-ecosystem-gap`: 형식은 유효하지만 Evidence가 없는 기준 Pack이다.
- `alexai-mission-handoff`: OpenCrab 책임 경계와 CrabHarness Mission 흐름을 뒷받침하는
  공개 GitHub 문서 Evidence를 포함한다.

생성물에는 절대 로컬 경로나 비밀값을 기록하지 않는다. Pack은 OpenCrab의 현재 최소
공식 계약인 네 파일 레이아웃을 사용하므로 OpenOyster 실행 시 compatible profile을
명시한다.

실제 실행 결과와 다음 요구사항은 [RESULTS.md](RESULTS.md)에 기록한다.
