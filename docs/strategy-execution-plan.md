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

## M3~M9

각 마일스톤 시작 전에 이 문서에 같은 형식(산출물, 명세, 완료 조건)으로 상세를 추가한 뒤 진행합니다.
