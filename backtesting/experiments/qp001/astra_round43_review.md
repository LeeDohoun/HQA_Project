**과거 변동성 계산에는 동의합니다. 다만 현재 `--execute`는 아래 P1 수정이 필요합니다.**

Precheck의 허용 경계는 **각 결정일 t까지의 60거래일 입력으로 F_vol·유니버스·ROA·A_t/C_t·집단별 크기를 계산하는 것**입니다. 이후 가격으로 체결 가능 여부나 생존 여부를 판단하거나, 보유기간 수익률·NAV·e_t·대조군 성과를 계산하면 안 됩니다. `PriceBook`과 포트폴리오 계산 경로는 호출하지 않고, 103개월 × S/O의 완전성·실패 사유·입력 해시를 기록하면 됩니다. 현재 외부 변동성 캐시 요구는 이 공통 선정 경로로 대체할 수 있습니다. [runner.py:290](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp001/runner.py:290)

- **P1 — 설계 O 불가가 검증 자료로 가려집니다.** [runner.py:283](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp001/runner.py:283)은 두 구간의 연결 성공·실패를 합산합니다. 설계에서 정정 연결이 전부 실패해 O가 사용 불가여도, 검증에 성공 하나가 있으면 전체가 사용 가능해져 최종 `pass`가 가능합니다. **설계·검증별 `sensitivity_available`을 따로 검사하고, 필요한 구간 모두 가능해야 최종 가능으로 처리**하세요. 설계 실패/검증 성공 회귀 사례가 필요합니다. [사전등록:77](/home/are01/src/HQA_Project/HQA_qp001/research/experiments/QP001_profitability_on_av/preregistration.md:77), [상태표:122](/home/are01/src/HQA_Project/HQA_qp001/research/experiments/QP001_profitability_on_av/preregistration.md:122)

- **P1 — 성과 계산 후 중단된 시도가 대장에서 사라집니다.** 최종 해시 불일치·메모리 부족·직렬화 실패는 종료 메시지만 남깁니다. 결과도 최종 경로에 직접 쓰고 fsync 없이 대장에 연결합니다. [runner.py:416](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp001/runner.py:416), [runner.py:476](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp001/runner.py:476). **기존 `design_attempt` 방식으로 계산 이후 실패를 `design_aborted`로 기록하고, 결과를 원자적으로 저장·동기화한 뒤 대장을 추가**하세요. 저장 성공/대장 실패에는 재계산 없는 등록 복구도 필요합니다. 모든 시도 기록은 [저장소 규칙:10](/home/are01/src/HQA_Project/HQA_qp001/research/experiments/README.md:10)의 요구입니다.

- **P2 — 실자료 AV 재현은 새 가중 회계를 통과하지 않습니다.** 재현 입력에는 `groups`가 없어 기존 HC003으로 바로 위임됩니다. 따라서 보고된 오차는 새 가중 분기의 실자료 검증 근거가 아닙니다. **단일 집단·α=1로 새 분기를 통과시키는 동일 3개월 재현**을 추가하세요. [runner.py:347](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp001/runner.py:347), [portfolio.py:37](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp001/portfolio.py:37)

- **P2 — 전체 실행의 메모리는 아직 미확인입니다.** 대조군 상세를 8벌 × 103개월 × 200경로 유지하고 전체 결과 문자열까지 생성하면서 가상메모리를 1.45GB로 제한합니다. 실제 규모의 합성 입력으로 확인하고, 필요하면 상세 결과를 순차 저장하세요. [portfolio.py:195](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp001/portfolio.py:195), [runner.py:465](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp001/runner.py:465)

그 외 고정 q, 날짜 적용 후 분기화, 전체 CFS/OFS 선택, 집단 목표금액, 비용별 독립 B/Q, 무작위 추출, NW(3)·t/p·NAV·검증 부호·−100% 판정은 정적 검토상 사전등록과 부합합니다.

**수정·커밋과 c497f58 통합 후 `--precheck` → `--execute` 진행 가능합니다.** 현재 구버전 자산 파서는 이미 알려진 요청 범위 밖 행에서 중단됩니다. [financials.py:56](/home/are01/src/HQA_Project/HQA_qp001/backtesting/experiments/qp001/financials.py:56). 같은 동결 입력으로 전수 precheck를 기록해야 하며, 86.4%나 99.9% 연결률은 월별 C_t/A_t 검증을 대신하지 않습니다. 테스트·실험은 실행하지 않았습니다.

