**정적 검토상 차단할 P1은 없습니다. 커밋·메인 통합 후 `fetch → precheck ready → execute` 진행 가능합니다.** 현재는 구조화 자료·원문 미수집으로 `ready=false`이므로 수집 후 재점검해야 합니다.

- **보호 세션·리허설:** 보호 가격은 활성 BB002 세션 없이는 읽지 못하며, 해시 재검증도 세션 내부입니다. 실행마다 같은 코드의 2025H1 전체 리허설을 먼저 수행합니다. 리허설은 registry/ledger를 기록하지 않습니다. [bb002.py:96](/home/are01/src/HQA_Project/HQA_bb002/backtesting/experiments/bb002.py:96), [bb002.py:376](/home/are01/src/HQA_Project/HQA_bb002/backtesting/experiments/bb002.py:376), [bb002.py:409](/home/are01/src/HQA_Project/HQA_bb002/backtesting/experiments/bb002.py:409)
- **표본·판정:** 접수일 범위, 7월 청산, E=2026-07-30, 9월 말 재거래 확인창, 최소 30건·20회사·20진입일 및 `insufficient` 우선순위가 사전등록과 일치합니다. [bb002.py:49](/home/are01/src/HQA_Project/HQA_bb002/backtesting/experiments/bb002.py:49), [bb002.py:260](/home/are01/src/HQA_Project/HQA_bb002/backtesting/experiments/bb002.py:260)
- **BB001·수집:** 공유 변경은 선택적 인자 추가이며 기존 기본값과 수익·매칭·비용 산식은 유지됩니다. 범위별 구조화 캐시 분리, 중첩 접수번호 값 충돌 거부, 기존 원문 재사용 및 기본 dry-run도 확인했습니다. [bb001.py:131](/home/are01/src/HQA_Project/HQA_bb002/backtesting/experiments/bb001.py:131), [dart_buyback.py:287](/home/are01/src/HQA_Project/HQA_bb002/src/ingestion/dart_buyback.py:287), [dart_buyback.py:342](/home/are01/src/HQA_Project/HQA_bb002/src/ingestion/dart_buyback.py:342)
- **중단 기록:** 세션 소비 후 잡힌 예외는 확보된 해시와 함께 `holdout_aborted` 결과·registry 행을 남기고 재개를 거부합니다. [bb002.py:384](/home/are01/src/HQA_Project/HQA_bb002/backtesting/experiments/bb002.py:384), [bb002.py:428](/home/are01/src/HQA_Project/HQA_bb002/backtesting/experiments/bb002.py:428)

**P2 — v1 비교 요약 누락:** BB002가 계승한 BB001 §3에는 v1 비교 보고가 있지만, 현재 집계는 v2 배수 세 개뿐입니다. 이미 계산하는 `v1`을 `cost_sensitivity`에 추가하고 판정에서는 계속 제외하면 됩니다. [BB001 사전등록:111](/home/are01/src/HQA_Project/HQA_bb002/research/experiments/BB001_direct_buyback/preregistration.md:111), [bb002.py:248](/home/are01/src/HQA_Project/HQA_bb002/backtesting/experiments/bb002.py:248)

테스트·실험은 실행하지 않았고 보호 가격 내용은 읽지 않았습니다.

