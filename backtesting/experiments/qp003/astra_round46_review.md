보류 월의 실제 `e_t` 유지, 비용별 독립 운용, −100% 재계산, 대조군 추출 생략, S/O 독립성과 M=168 판정은 사전등록에 부합합니다. 다만 **현재 `--execute`는 보류**가 맞습니다.

- **P1 — 강제 종료 시 실패 시도가 사라집니다.** [runner.py:165](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp003/runner.py:165)의 시작 증거는 메모리에만 있고, `run_id`도 계산 완료 후 생성합니다(:262). [ordering.py:42](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/ordering.py:42)는 Python 예외만 기록하므로, 성과 계산 중 SIGTERM·OOM kill이면 대장 기록 없이 종료될 수 있습니다. **성과 읽기 전에 고정 `run_id`·입력 명세 해시를 포함한 시작 기록을 배타적으로 생성·fsync**하고, 미완료 기록이 있으면 재계산을 막아야 합니다. 다음 진입에서는 같은 ID로 중단 기록 또는 저장 결과의 등록만 복구하도록 연결하세요.

- **P2 — 정식 실행의 메모리 여유는 아직 입증되지 않았습니다.** [검증 보고서:82](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp003/validation_20261020.md:82)의 실제 사전점검과 합성 회계는 별도 프로세스 측정입니다. 사전점검은 월별 선택 상세를 버리지만([qp001/runner.py:286](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp001/runner.py:286)), 정식 실행은 이를 유지합니다. 또한 RSS와 `RLIMIT_AS`는 다른 지표입니다. **실제 선택자료 생성까지만 수행한 뒤 합성 가격·200경로로 이어지는 동일 프로세스 검증**에서 저장·복구까지 상한 내 완료되는지 확인하세요.

P1 수정, 메모리 확인, 변경 코드에 맞춘 사전점검 재봉인 후 **main의 정식 실행을 진행할 수 있습니다.** 테스트는 직접 실행하지 않았습니다.

