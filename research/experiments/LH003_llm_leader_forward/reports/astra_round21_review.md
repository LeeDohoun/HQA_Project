**완료본 커밋 전에 수정해야 할 P1이 있습니다.** 정적 검토만 수행했으며 테스트·실험·모델 호출은 실행하지 않았습니다.

- **P1 — 달력 우회로 단 한 번의 봉인이 실패합니다.** [calendar.py:19](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/calendar.py:19)는 원본 XKRX를 사용합니다. [workflow.py:98](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/workflow.py:98)는 세션을 소비한 **뒤** 휴장일인 2026-06-03의 가격 파일을 요구합니다. 2027년 미검증 날짜도 그대로 계획합니다. **수정:** 공통 보정 달력을 사용하고, 필요한 파일의 존재 여부를 claim 전에 검사하십시오. 11-19 특별장·2027년 공식 확인을 강제하되, 미검증 주를 삭제하거나 평가 기간을 연장하면 안 됩니다.

- **P1 — AV002 대기 조건이 없습니다.** [ordering.py:127](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/ordering.py:127)는 AV001·EV001만 검사합니다. AV001 중단과 EV001 설계 실패만으로 LH003 봉인이 가능해져 [AV002 사전등록:129](/home/are01/src/HQA_Project/HQA_Project/research/experiments/AV002_avoidance_holdout_recovery/preregistration.md:129)를 위반합니다. **수정:** 공통 잠금 안에서 부모 결과에 연결된 AV002 두 변형의 종료 행을 검증하십시오. 병합 시 AV002의 공통 잠금·해제 검사와 `c7c0105`의 crash-safe append를 보존해야 합니다.

- **P1 — 정상적인 재무 갱신 뒤 입력 생성이 깨집니다.** [snapshot.py:142](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/snapshot.py:142)는 같은 크기의 재저장을 거부하고, 커진 파일은 과거 바이트 위치부터 읽습니다. 실제 [dart_quarterly.py:332](/home/are01/src/HQA_Project/HQA_lh003/src/ingestion/dart_quarterly.py:332)는 자료를 재정렬해 전체 파일을 저장합니다. 따라서 정상 갱신도 오류·행 누락을 만들고, A 재현 실패로 최종 `insufficient`가 됩니다. **수정:** 봉인본과 별도로 보호 구간 밖 신규 버전의 불변 증분 자료를 제공하십시오. 원본 H1을 다시 읽어 해결해서는 안 됩니다.

- **P1 — 규칙 S의 충돌 버전 제외가 누락됐습니다.** [inputs.py:80](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/inputs.py:80)는 병합 listing의 `conflicts`를 전달하지 않습니다. 본자료·보완자료가 충돌하면 한쪽이 채택되거나 주 전체가 중단되어 A와 후보가 달라집니다. **수정:** [공통 FilingDates 로더:55](/home/are01/src/HQA_Project/HQA_lh003/src/ingestion/dart_financial_date_rules.py:55)처럼 충돌 집합을 공유하고 해당 버전을 제외하되, 결정일 이전 범위는 유지하십시오.

- **P1 — 실환경 검증이 실제 호출 없이 통과합니다.** [environment.py:90](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/environment.py:90)는 실자료에서도 `FakeCodex`를 쓰며, [112행](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/environment.py:112)은 Python 부모 프로세스 메모리만 측정합니다. 실제 계정의 인증·CLI·스키마 실패나 자식 프로세스 메모리 부족을 놓칩니다. **수정:** 허용된 합성 형식 시험으로 실제 호출 경로를 확인하고, 프로세스 전체 최대 메모리와 6회 호출 시간 검증을 시작 조건에 연결하십시오.

- **P1 — 최종 판정 1회 제한이 로컬 파일에만 의존합니다.** [evaluate.py:231](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/evaluate.py:231)는 `evaluation.json`이 없으면 재평가하며, [285행](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/evaluate.py:285)은 동일 해시만 중복 방지합니다. 이전 백업으로 복구하면 정본 대장에 완료 행이 있어도 두 번째 판정이 기록됩니다. **수정:** 계산 전에 정본 대장의 완료 여부를 검사하고, 완료 후에는 기존 결과 해시의 복구만 허용하십시오.

- **P2 — 코드 고정 범위가 불완전합니다.** [runner.py:22](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/runner.py:22)에 `lh001/evaluate.py`·비용 알고리즘이 빠지고, `__init__.py`도 해시에서 제외됩니다. 평가 시 고정 해시 확인도 필요합니다. 법정 세율·달력 자료 갱신과 알고리즘 변경을 분리하십시오.

- **P2 — 최종 평가 메모리가 과도하게 증가합니다.** [evaluate.py:76](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/evaluate.py:76)는 무효 주의 동일한 A를 200경로×6시나리오로 복제합니다. A 500종목·무효 22주면 위치 기록만 약 1,320만 개입니다. A 기록을 공유하고 주별로 저장하며 평가 전체의 메모리를 검증하십시오.

- **P2 — `not_started` 대장 기록 복구가 빠졌습니다.** [workflow.py:39](/home/are01/src/HQA_Project/HQA_lh003/backtesting/experiments/lh003/workflow.py:39)는 파일 봉인 후 대장 추가 전에 중단되면 다음 실행에서 바로 반환합니다. 기존 봉인 해시로 누락된 대장 행을 복구해야 합니다.

3회 전부 유효·1회 재질문·무효 주 A 대체, 52주 NW(4), 무작위 대조군과 비용 공식은 확인 범위에서 사전등록과 일치합니다. 2027년 세율 미확정 시 평가를 명시적으로 중단하는 처리도 적절합니다.

