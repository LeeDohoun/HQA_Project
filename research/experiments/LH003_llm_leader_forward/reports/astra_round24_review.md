**기존 네 P1의 수정은 확인했습니다. 다만 새 P1 1건이 있어, 실행 준비 완료로 커밋하는 것은 아직 권하지 않습니다.**

- **달력 해결:** 52개 슬롯을 유지하면서 해당 주의 결정·진입·청산일만 확인합니다. 첫 주에 2027년 검증을 요구하지 않습니다. [calendar.py:36](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/calendar.py:36)
- **배포 연결 해결:** 현재 배포기의 H1 필터와 최신 게시 실패 차단은 맞습니다. 완료 기록을 현재 동기화 해시와 대조하므로, 실패한 갱신을 이전 배포물로 대체하지 않습니다. [distribute.py:81](/home/are01/src/HQA_Project/HQA_lh003/scripts/lh003/distribute.py:81), [distribution.py:50](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/distribution.py:50)
- **B 해결:** 허용 데이터 노드의 형식을 검증하고 나머지 AST의 동일성을 비교합니다. 지정 범위 밖 알고리즘 변경을 허용하는 경로는 발견하지 못했습니다. [pins.py:50](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/pins.py:50)
- **A 생성 전 검사 해결:** 입력 생성 전후 코드 검증과 결정별 코드·개정 해시 기록이 들어갔습니다. [workflow.py:263](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/workflow.py:263)

**새 P1 — 배포물을 생성한 코드가 봉인되지 않습니다.** 게시 manifest에는 자료 해시만 있고 생성 당시 구현 해시가 없습니다. 다른 체크아웃이나 임시 변경된 배포기로 게시한 뒤 코드를 원복하면, 주간 실행의 현재 코드 검사는 통과합니다. 독자는 `scope` 문자열과 파일 해시를 신뢰하며, 배포물의 재무·지수 행을 그대로 합칩니다. 따라서 잘못된 배포기의 H1 혼입이나 다른 재무 필터를 검출하지 못합니다. [distribute.py:117](/home/are01/src/HQA_Project/HQA_lh003/scripts/lh003/distribute.py:117), [distribution.py:33](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/distribution.py:33), [snapshot.py:145](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/snapshot.py:145)

**수정:** 배포기의 원자료 읽기 전·완료 게시 전에 고정 코드와 의존성을 검사하고, 생성 당시 코드·개정 해시를 manifest에 봉인하십시오. 독자도 이를 승인된 기준과 대조해야 합니다. 시작 전 게시에도 사용할 고정 기준이 필요합니다.

`de8ca4c` 대비 FW001 inventory와 봉인 파일 변경은 없었습니다. 위 수정 후에는 실행 머신에서 자료 동기화→배포→입력 생성, 자식 프로세스 포함 메모리·최악 6회 호출 시간, 재부팅·절전·로그아웃 복구를 실측해야 합니다. 순서 조건과 LH003 단일 봉인까지 **11월 13일 장마감 전에** 완료해야 합니다. [사전등록:169](/home/are01/src/HQA_Project/HQA_lh003/research/experiments/LH003_llm_leader_forward/preregistration.md:169), [workflow.py:101](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/workflow.py:101)

이번 검토는 읽기만 수행했습니다. 133개 통과는 구현자 보고입니다.
