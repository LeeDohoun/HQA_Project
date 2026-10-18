**Round 24 P1은 해결됐습니다. 신규 P1은 발견하지 못했으며, 검토한 변경은 구현 완료로 커밋해도 됩니다.**

- 배포기는 원자료 읽기 전과 완료 게시 전에 코드·의존성을 검사하고, 생성 당시 해시를 manifest에 남깁니다. [distribute.py:75](/home/are01/src/HQA_Project/HQA_lh003/scripts/lh003/distribute.py:75)
- 독자는 자신의 고정 기준과 생성 당시 기록을 대조합니다. 코드 원복 후에도 변형 배포물을 거부하며, H1 주입 사례가 회귀 테스트에 포함됐습니다. [distribution.py:62](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/distribution.py:62), [테스트:117](/home/are01/src/HQA_Project/HQA_lh003/tests/test_experiments_lh003_round24.py:117)
- 허용된 세율·달력 개정은 동일한 개정 이력으로 처리됩니다. 고정 참조의 68개 파일 해시도 현재 파일과 일치했습니다. [테스트:155](/home/are01/src/HQA_Project/HQA_lh003/tests/test_experiments_lh003_round24.py:155)

`66f5893` 대비 FW001 inventory·봉인 파일 변경은 없습니다. 이번 검토는 읽기와 해시 확인만 했으며, **148개 통과는 제출된 보고서의 결과**입니다.

첫 실제자료 호출 전 남은 일은 다음과 같습니다.

1. **배포 고정:** 구현·v3 프롬프트·`distribution_code_reference.json`을 함께 커밋하고 hqa-server에서 동일 해시를 확인합니다. 고정 참조는 자료 게시 작업에서 재생성하지 않습니다. 모델 `gpt-6-luna`, CLI `0.160.0`, 달력 패키지 `4.13.2`도 확인합니다.
2. **이전 후 환경 확정:** FW001 봉인 코드·상태를 보존하고, LH003 연구 계정·체크아웃·venv·상태 경로를 분리합니다. 실행 머신 ID와 **정본 registry/holdout 원장 및 공통 잠금의 운영 경로**를 확정합니다. [사전등록:155](/home/are01/src/HQA_Project/HQA_lh003/research/experiments/LH003_llm_leader_forward/preregistration.md:155)
3. **자료 전달 검증:** 필요한 가격·지수 이력, 재무, 보완 목록을 동기화하고 전체 범위의 해시→별도 배포→완료 manifest 고정까지 검증합니다. 주말·휴일에도 자동으로 이어져야 합니다.
4. **hqa-server 실환경 검증:** 합성 입력의 실제 형식 호출 6회로 인증·출력을 확인하고, 공식 호출 예산에 포함합니다. 실제 입력 생성과 자식 프로세스를 포함한 메모리, 최악 6회 호출 시간, 재부팅·절전·로그아웃 복구, 중복 결정 방지와 시도 수 보존을 기록합니다. 현재 메모리 게이트는 **1536 MiB 미만**입니다. [environment.py:50](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/environment.py:50)
5. **시작 봉인:** 최신 AV001·EV001·AV002 해제 조건과 파일 사전 점검을 통과한 뒤, LH003 단일 세션에서 H1 스냅샷과 실제 입력 주기 검증을 완료합니다. **`start.json`까지 11월 13일 장마감 전에** 봉인해야 합니다. [workflow.py:101](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/workflow.py:101)

11월 13일은 **결정일**입니다. 현재 수집 일정에서는 실제 호출은 통상 **11월 14일 수집·배포 완료 후**이며, 다음 거래일 08:59 전에 확정해야 합니다. [수집 일정:122](/home/are01/src/HQA_Project/HQA_lh003/docs/collector-server.md:122) 11월 19일 특수장·2027년 달력은 해당 날짜가 필요한 실행 전에, 2027년 세율은 비용 평가 전에 공식 근거로 개정하면 됩니다.
