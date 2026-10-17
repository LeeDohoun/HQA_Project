# 전략 시스템 실행 계획

[수익 검증 로드맵](strategy-research-roadmap.md)을 에이전트가 실행할 수 있는 마일스톤으로 나눈 문서입니다.

- **역할**: Claude가 오케스트레이션과 검토를 맡고, Codex(`codex exec -s workspace-write`, 네트워크 차단)가 구현합니다.
- **완료 판정**: 마일스톤마다 아래 완료 조건을 Claude가 직접 테스트로 확인해야 끝납니다.
- **멈춤 규칙**: "사람 확인"이 있는 마일스톤은 사용자 승인 전까지 다음 단계로 넘어가지 않습니다.

## 공통 규칙 (모든 마일스톤)

- 새 파일과 각 마일스톤에 명시된 파일만 변경합니다. 다른 미커밋 작업은 보존합니다.
- 외부 API, 모델, 수집기, 스케줄러, 주문 monitor를 실행하지 않습니다. 테스트는 고정 입력(fixture)으로 오프라인에서 돌립니다.
- `.env*`, 계정, 토큰을 읽지 않습니다.
- 판정 기준 숫자(로드맵 3.4절)를 바꾸지 않습니다. 바꿔야 할 것 같으면 멈추고 보고합니다.
- 커밋, 푸시, 서비스 등록은 사용자가 따로 지시할 때만 합니다.
- 테스트 명령:

```bash
OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false venv/bin/python -m pytest -q <tests>
```

## 마일스톤 목록

| ID | 내용 | 외부 호출 | 사람 확인 |
| --- | --- | --- | --- |
| M0 | 실험 규칙 장치: 비용 모델, 사전 등록·실험 대장, 보류 구간 차단 | 없음 | 판정 기준 최종 승인 |
| M1 | 전종목 일봉 수집기 (코드) | 없음 (구현만) | 백필 실행 승인 (KRX API 사용) |
| M2 | 평가 도구: IC, 분위, 무작위 대조군, 유동성 구간, 용량, 이벤트 연구 | 없음 | — |
| M3 | DART 신규 공시 폴러 (코드) | 없음 (구현만) | 서비스 가동 승인 |
| M4 | 분봉 수집 경로: 백엔드 내부 API + Python 저장기 (코드) | 없음 (구현만) | 백필·일일 수집 승인 |
| M5 | 전략 계약과 1순위 전략 (D 팩터, B1~B2 공시 이벤트, A1·A2 제외 필터) | 없음 | — |
| M6 | 1순위 전략 실험 실행 (사전 등록 → 설계·검증 구간) | 데이터 필요 (M1 백필 후) | **D1 판정** |
| M7 | 배분, 비중·손절, 계좌 한도 모듈 | 없음 | — |
| M8 | 매매 경로 연결: 기존 LLM 판단 경로 분리, 섀도 모드 | 없음 (구현만) | 섀도 가동 승인 |
| M9 | 보류 구간 평가, PAPER 운영, 주간 보고 | 있음 | **D2, D3 판정** |
| T1~T3 | 장중 트랙: 분봉 백필, H2·H3 시뮬레이션, H1 | M4 이후 | 소액 실측 승인 |

## M0 실험 규칙 장치

### 산출물

| 파일 | 내용 |
| --- | --- |
| `backtesting/cost_model.py` | 비용 모델 |
| `backtesting/experiment_registry.py` | 사전 등록 검증과 실험 대장 |
| `backtesting/holdout.py` | 보류 구간 접근 차단 |
| `research/experiments/README.md` | 사용법 |
| `research/experiments/TEMPLATE/preregistration.md` | 사전 등록 양식 |
| `research/experiments/registry.csv` | 헤더만 있는 실험 대장 |
| `tests/test_backtesting_cost_model.py`, `tests/test_backtesting_experiment_registry.py`, `tests/test_backtesting_holdout.py` | 테스트 |

### 비용 모델 명세

- 매수·매도 각각의 비용 비율을 계산합니다. 입력: 가격, 시장(KOSPI·KOSDAQ), 거래일, 20일 평균 거래대금, 방향(buy·sell).
- 구성 요소:
  - 수수료: 설정값, 기본 0.015%
  - 매도 거래세: 연도·시장별 표. 2023 0.20%, 2024 0.18%, 2025 0.15%, 2026 0.20% (코스피는 농어촌특별세 포함 합계, 코스닥은 단일). 표에 없는 연도는 오류입니다.
  - 스프레드: 가격대별 호가 단위(2023-01-25 이후 통합 기준) 1틱 / 가격. 경계값은 표로 두고 출처를 주석에 적습니다.
  - 유동성 슬리피지: 20일 평균 거래대금 구간별 가산 (1억 미만 0.30%, 1~10억 0.15%, 10~100억 0.05%, 100억 이상 0.02%). 설정으로 바꿀 수 있습니다.
- 왕복 비용 함수와 배수 인자(`multiplier`, 비용 민감도 1.0·1.5·2.0용)를 제공합니다.

### 사전 등록·실험 대장 명세

- `preregistration.md`는 YAML 머리말을 가집니다. 필드: `experiment_id`, `sleeve`, `hypothesis`, `counterparty`, `data_periods`, `metrics`, `pass_criteria`, `variants_planned`, `uses_llm`, `uses_holdout`.
- `verify_preregistration(experiment_id, repo_root)`:
  - git에 커밋된 파일이어야 합니다.
  - 마지막 커밋 이후 수정되지 않아야 합니다 (작업 트리·스테이징 모두).
  - 필수 필드가 있어야 합니다.
  - 통과하면 해당 파일의 마지막 커밋 해시와 시각을 반환합니다. 실패 시 예외입니다.
- `record_trial(experiment_id, variant, metrics, verdict, ...)`: `registry.csv`에 한 줄을 추가합니다. 사전 등록 검증을 먼저 통과해야 합니다. 기존 줄은 수정·삭제하지 않습니다.
- `trial_count(experiment_id)`, `required_t_stat(experiment_id)`: 변형 시도 수가 20 이하면 2.0, 넘으면 3.0.
- 테스트는 임시 git 저장소를 만들어 미커밋, 커밋 후 수정, 정상 세 경우를 확인합니다.

### 보류 구간 차단 명세

- 보류 구간: 2026-01-01 ~ 2026-06-30 (설정 상수, 로드맵 3.1절).
- `guard_period(from_date, to_date, *, experiment_id=None, ledger_path=...)`:
  - 보류 구간과 겹치지 않으면 통과합니다.
  - 겹치면 `experiment_id`가 필요합니다. 해당 실험의 사전 등록에 `uses_holdout: true`가 있어야 합니다.
  - 같은 실험이 이미 보류 구간을 썼다면 거부합니다.
  - 허용할 때는 원장(`research/experiments/holdout_ledger.jsonl`)에 실험 ID, 시각, 구간을 추가합니다.
- 같은 실행 안에서 여러 번 조회할 수 있도록 `HoldoutSession` 컨텍스트로 한 번 열고 닫는 방식을 지원합니다. 닫힌 뒤 다시 열 수 없습니다.

### 완료 조건

- 새 테스트와 기존 `tests/test_backtesting_*.py`가 모두 통과합니다.
- 새 모듈은 네트워크·모델을 쓰지 않습니다.

## M1 전종목 일봉 수집기

### 산출물

- `src/ingestion/krx_market.py`:
  - 기존 `KRXChartCollector`의 날짜별 시장 전체 조회와 검증 로직을 재사용합니다. 그날의 **모든 종목** 행을 저장합니다.
  - 저장 위치: `data/market/krx_daily/<YYYY>/<YYYYMMDD>.jsonl`. 한 줄에 한 종목. 필드: 종목코드, 종목명, 시장, OHLCV, 거래대금, 시가총액, 상장주식수 (API 제공 시), `trade_date`, `bar_at`, `collected_at`, `available_at`, `version`.
  - 이미 저장된 날짜는 건너뜁니다. 같은 날짜가 다른 내용으로 다시 오면 새 버전으로 보존합니다 (`storage.py` 관례).
  - 빈 응답(휴장일 가능성)은 저장하지 않고 상태 파일에 기록합니다.
  - 백필 진입점 `python -m scripts.data.krx_market --from YYYYMMDD --to YYYYMMDD --dry-run`. `--dry-run`이 기본이며, 실제 호출은 `--execute`를 줄 때만 합니다.
- 생존편향 없는 유니버스 로더 `load_universe(as_of)`: 그 날짜에 거래된 종목만 돌려줍니다.
- 테스트: 가짜 세션(fixture)으로 2개 시장, 휴장일, 중복·충돌, 재실행 건너뛰기, 상장폐지 종목이 과거 날짜 유니버스에 남는지를 확인합니다.

## M2 평가 도구

### 산출물

- `backtesting/signal_eval.py`
  - 입력: 날짜 × 종목 점수표, 날짜 × 종목 이후 수익률표 (1·5·20일), 유동성 표.
  - 출력:
    - 날짜별 순위 IC와 평균, t-통계 (Newey-West 또는 비중첩 표본), 연도별 IC
    - 5분위 비용 차감 수익률
    - 상하위 차이
    - 유동성 구간 4개별 같은 지표
    - 무작위 대조군 (같은 날 점수를 섞은 점수 N회)의 분포와 백분위
  - 비용은 `cost_model`을 씁니다. 기간 입력은 `holdout.guard_period`를 통과해야 합니다.
- `backtesting/event_study.py`
  - 입력: 이벤트 목록 (종목, `available_at`, 이벤트 특징값), 일봉.
  - 진입: `available_at` 이후 첫 거래일 시가. 날짜만 있는 공시는 다음 거래일 시가입니다.
  - 출력:
    - 1·3·5·20일 시장 대비 초과수익
    - 특징값 구간별 평균과 날짜 군집 t-통계
    - 비용 차감 결과
    - 무작위 대조군 (같은 날짜 분포에서 무작위 종목·날짜 선택)
- `backtesting/capacity.py`: 주문 금액 ≤ 20일 평균 거래대금의 1% 조건에서 동시 보유 종목 수를 반영한 전략 최대 운용 금액.
- 테스트:
  - 신호를 일부러 심은 합성 데이터에서 IC와 이벤트 효과를 검출하는지
  - 순수 잡음에서 대조군 백분위가 극단으로 가지 않는지
  - 미래 수익률이 입력 시점에 섞이지 않는지 (시점 위반 시 오류)

## M8 전략 실행 경로 (섀도·PAPER 일괄 리밸런싱)

연구를 통과한 전략을 사전 등록 규칙 그대로 매매합니다. 기존 LLM 소수 종목 경로(계획·감시·체결 대조)는 그대로 두고, 전략 전용 PAPER 계좌에서 **일괄 리밸런싱** 경로를 따로 둡니다.

### 전제 (2026-10-17 확인)

- 대상 전략 HF001 `f_both`와 AV001 `av_all`은 매월 수백 종목을 동일 비중으로 담습니다 (2026-09-30 리허설에서 586·588 종목). 로드맵 6.2절의 10~20종목 기본값보다 사전 등록이 우선합니다. 종목 수를 줄이면 새 전략이므로 새 사전 등록이 필요합니다.
- 기존 v2 계획 경로는 계좌당 감시 종목 10개, 대기 신규 계획 5개, 하루 매수 20건, 분석 후 15분 진입 유효로 설계돼 있어 이 전략을 실행할 수 없습니다.
- 결정은 FW001 기록기가 쓰는 봉인된 결정 기록(`data/forward/fw001/decisions/YYYYMM.json`, `label == "forward"`)을 **읽기만** 합니다. 실행 경로가 결정을 다시 계산하지 않습니다 (원칙 7, 같은 코드).
- 로드맵 8장 단계: 섀도는 보류 구간 통과 후, PAPER는 섀도 4주 이상·운영 사고 0건·신호 일치 후입니다. 2026-10-17 현재 보류 구간을 통과한 전략은 없으므로 두 전략 모두 `observe`(섀도 전 관찰)에서 시작합니다.

### 산출물

| 파일 | 내용 |
| --- | --- |
| `config/strategy_book.yaml` | 운용 대상 전략 목록 (전략 ID, 실험·변형, 보류 판정 행, 결정 출처, 실행 방식, 요청 단계, 전용 계좌) |
| `config/strategy_approvals.yaml` | 사람의 단계 승인 기록 (PAPER 승격에 필요). 처음에는 빈 목록 |
| `src/live/__init__.py` | 패키지 |
| `src/live/book.py` | 전략 목록과 승인 기록 읽기·검증 |
| `src/live/gates.py` | 실험 대장·섀도 장부·승인으로 허용 단계를 계산 (보수적으로 실패) |
| `src/live/decisions.py` | FW001 결정 기록 읽기 (읽기 전용, `forward` 기록만) |
| `src/live/rebalance.py` | 목표 비중과 현재 장부로 주문 목록 생성 (순수 함수) |
| `src/live/shadow.py` | 섀도 장부: 실제 시가·종가와 비용 모델로 가상 체결, 불변 기록 |
| `src/live/backend_client.py` | 백엔드 내부 API 호출 (일괄 주문, 상태 게시, 계좌 조회) |
| `src/live/runtime.py` | 하루 작업 조합 (`status`, `plan`, `shadow-fill`, `submit`, `publish-status`) |
| `scripts/ops/strategy_runtime.py` | 명령줄 진입점. 모든 쓰기는 `--execute`가 있을 때만 |
| `backend/.../db/migration/V15__strategy_runtime.sql` | 전략 계좌, 일괄 주문, 주문 행, 상태 스냅숏, 계좌 고점, 중단 스위치 테이블 |
| `backend/.../entity`, `repository`, `service`, `controller`의 `Strategy*` 클래스 | 전략 계좌·일괄 주문·위험 한도·실행 worker·API |
| `backend/.../service/AutoTradeTargetService.java`, `PaperTradeStore.java` | 전략 계좌를 LLM 대상에서 제외, 전략 계좌에 LLM 계획 저장 거부 (최소 변경) |
| `backend/.../service/KisClient.java` | 동시호가용 PAPER 주문 메서드 추가 (기존 메서드 유지) |
| `frontend/src/app/(workspace)/strategies/page.tsx` 와 탐색·API·타입 | 전략 현황 화면 |
| `docs/strategy-runtime.md` | 운영 절차, 계좌 연결, KIS 확인 목록, 미확인 사항 |
| `tests/test_live_*.py`, `backend/src/test/.../Strategy*Test.java` | 테스트 |

### 단계와 승격 명세

- 단계는 `observe < shadow < paper` 세 가지입니다. 실제 단계 = min(요청 단계, 증거가 허용하는 단계).
- `observe`: 결정 기록이 있으면 주문 목록과 가상 체결을 만들지만 섀도 증거로 세지 않습니다.
- `shadow` 허용: 실험 대장에서 그 변형의 설계·검증 최신 판정이 `pass`이고, 지정한 보류 판정 행(예: AV002의 `holdout_av_all`)의 최신 판정이 `pass`입니다. 판정 행이 없거나 다르면 허용하지 않습니다.
- `paper` 허용: `shadow` 허용 조건에 더해,
  - 섀도 장부가 섀도 단계에서 20거래일 이상, 달력 28일 이상 이어졌고,
  - 그 기간 운영 사고 0건 (결정 마감 누락, 자료 누락, 계획 실패, 기록 충돌),
  - 섀도 목표 종목이 결정 기록의 목표와 모두 일치하고 가상 체결 가격이 KRX 일봉 시가·종가와 일치하며,
  - `config/strategy_approvals.yaml`에 그 전략의 `paper` 승인(승인자, 시각, 근거)이 있습니다.
- 로드맵 3.4절 기준과 사전 등록의 판정 기준은 읽기만 하고 바꾸지 않습니다.

### 주문 목록 명세

- 실행 방식 `basket_20` (HF001 `f_both`): 결정 다음 거래일 시가에 목표 종목을 동일 비중으로 매수하고, 진입일을 1일로 센 20번째 거래일 종가에 전량 매도합니다. 매수가 안 된 종목 몫은 현금으로 둡니다.
- 실행 방식 `continuous` (AV001 `av_all`): 결정 다음 거래일 시가에 바뀐 종목만 거래합니다. 매도 먼저, 매수는 가용 현금에 비례해 줄이며, 순자산의 0.1% 미만 차이는 거래하지 않습니다 (전량 청산 제외). HC003 회계 규칙과 같습니다.
- 수량은 기준가(결정일 종가)로 계산해 정수 주로 내림하고, 예상 비용(`cost_model`)만큼 현금을 남깁니다.
- 주문 금액이 20일 평균 거래대금의 1%를 넘으면 경고로 기록합니다 (6.2절). 사전 등록 규칙을 바꾸지 않으므로 주문을 줄이지 않습니다.

### 섀도 장부 명세

- 체결 가격: 매수는 진입일 시가, 매도는 `basket_20`은 청산일 종가, `continuous`는 리밸런싱일 시가입니다. 비용은 전략 사전 등록의 비용 모델 버전(`f_both` v1, `av_all` v2)을 씁니다.
- 체결 불가: 그날 행이 없거나 거래량 0 (거래정지), 매수 시 시가 상한가, 매도 시 시가 하한가.
- 기록: `data/live/<strategy_id>/shadow/YYYYMMDD.json`. 같은 날짜를 다른 내용으로 다시 쓰지 않습니다. 사고는 `incidents` 필드에 남깁니다.

### 백엔드 명세

- 전략 계좌: `strategy_accounts`에 묶인 사용자는 전략 전용입니다. LLM 자동매매 대상 목록에서 빠지고, v2·v1 계획 저장이 `STRATEGY_ACCOUNT_REJECTS_LLM_PLANS`로 거부됩니다. PAPER 계좌만 허용합니다.
- 일괄 주문 `POST /api/v1/internal/strategies/batches` (내부 토큰): 전략 계좌의 단계가 `paper`이고 활성일 때만 접수합니다. `dryRun`이면 검증만 합니다. 같은 `idempotencyKey`는 같은 결과를 돌려줍니다.
- 위험 한도 (6.4절, 전략 코드와 독립): 일일 손실 2% 초과 또는 고점 대비 낙폭 10% 이상이면 매수를 막고, 15% 이상이면 전체 검토 표시를 남깁니다. 중단 스위치(전역·계좌)가 켜지면 새 주문을 모두 막습니다. 계좌당 하루 주문 수와 취소 비율 상한을 둡니다. 주문 한 건이 순자산의 10%를 넘으면 거부합니다.
- 시간 창: 시가 일괄 주문은 장전 동시호가(기본 08:30~08:59), 종가 일괄 주문은 장 마감 동시호가(기본 15:20~15:29)에만 보냅니다. 매도를 먼저 보냅니다.
- 주문 유형: 설정값(`MARKET` 기본). KIS 모의투자가 동시호가 시장가 주문을 받는지는 `docs/strategy-runtime.md`의 확인 목록으로 사용자가 승인한 실제 호출에서 확인한 뒤에 사용합니다.
- 체결 대조: KIS 주문 내역으로 주문 행의 체결 수량·평균가를 갱신합니다. 접수는 체결이 아닙니다.

### 화면 명세

- 전략별 연구 판정, 요청·실제 단계와 막힌 이유, 최신 결정(결정일·진입일·종목 수), 섀도 장부 순자산과 수익률, 최근 일괄 주문(주문·체결·거부 수, 신호가 대비 체결가 차이), 위험 한도 사용률과 중단 스위치 상태.

### 완료 조건

- 새 Python 테스트와 기존 전체 테스트(병합 전부터 실패하던 테스트 제외)가 통과합니다.
- `mvn -f backend/pom.xml test`와 `npm --prefix frontend run build`가 통과합니다.
- 고정 입력으로 결정 기록 → 주문 목록 → 섀도 체결 → 상태 게시 → 백엔드 일괄 주문(KIS 모의 객체) → 화면 표시까지 오프라인으로 이어집니다.
- 실제 데이터 폴더에 대해 `status`가 읽기 전용으로 동작합니다.
- 사람 확인: 섀도 가동(서버 일정 등록), KIS 동시호가 주문 확인 호출, 전략 계좌 연결, PAPER 승인.

## M3~M9

각 마일스톤 시작 전에 이 문서에 같은 형식(산출물, 명세, 완료 조건)으로 상세를 추가한 뒤 진행합니다.
