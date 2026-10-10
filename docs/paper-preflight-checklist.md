# PAPER 모의투자 사전 점검표

모의투자(PAPER)를 처음 시작하기 전에 확인할 것, 기동 순서, 매일 볼 것, 문제가 생겼을 때 할 일을 정리했습니다. 설계와 설정의 근거는 [Luna PAPER Runtime](luna-paper-runtime.md)에, 바뀐 내용은 [변경 내역 7장](ai-data-changes-2026-09.md#7-10월-paper-준비-작업-2026-10-09)에 있습니다.

## 0. 관찰 기간의 원칙

- **PAPER 전용입니다.** REAL 키와 REAL 주문 경로는 거부됩니다.
- **관찰 기간(20거래일) 동안 프롬프트·모델·설정을 바꾸지 않습니다.** 바꾸면 그날부터 다시 셉니다. 실제 모델 식별자를 기록합니다.
- **모의투자 계좌에서 직접 매매하거나 입출금·초기화하지 않습니다.** 일일 손익 기준선은 입출금을 반영하지 않고, 직접 산 주식은 `partially_managed`로 보고되며 보호되지 않습니다.
- **모니터는 하나만 띄웁니다.** 여러 개를 띄워도 KIS 호출 한도는 늘지 않습니다.
- **수집 데이터, 감사 DB(`paper_audit.sqlite3`), 예산 원장(`llm_budget.sqlite3`)은 Git에 올리지 않습니다.** 감사 기록에는 계좌 정보가 들어 있으니 접근을 제한합니다.

## 1. 키와 설정

| 항목 | 쓰는 곳 | 확인할 것 |
|---|---|---|
| `OPENAI_API_KEY` | AI 서버 | `gpt-5.6-luna` 사용 가능. 비용 집계를 위해 전용 프로젝트 권장 |
| `HQA_LLM_MONTHLY_BUDGET_USD` / `HQA_LLM_OPERATING_TARGET_USD` | AI 서버 | 기본 100 / 90달러. 일반 분석은 90에서 멈추고 보유 종목 보호는 100까지 |
| `HQA_LLM_RPM` / `HQA_LLM_TPM` | AI 서버 | OpenAI 프로젝트의 실제 한도로 설정. 기본 120 RPM·200,000 TPM은 보수적이라, 사이클마다 전문가 입력이 최대 약 80만 토큰이면 첫 사이클이 몇 분 늦어짐(공식 Build 등급 기본 5,000 RPM·2,000,000 TPM) |
| `HQA_INTERNAL_TOKEN` | AI 서버, 백엔드, 모니터, 스케줄러 | 세 곳 모두 같은 값, 앞뒤 공백 없이 |
| `HQA_KIS_ENC_KEY` | 백엔드 | KIS 자격증명 암호화 키. 바꾸면 저장된 계좌 지문을 다시 검토해야 함 |
| 사용자별 KIS 모의투자 앱키·시크릿·계좌 | 백엔드 자격증명 등록 | 사용자 하나에 계좌 하나, 앱키 공유 금지. AI 서버는 증권사 자격증명을 받지 않음(Compose는 AI 쪽 컨테이너에서 KIS 키와 `HQA_KIS_ENC_KEY`를 빈 값으로 덮어씀) |
| `DART_API_KEY` | 수집 | OpenDART 키 |
| `KRX_OPEN_API_KEY` | 수집 | 일별 시세와 지수(시장 맥락) 서비스 승인. 승인 전에는 일봉을 `kis_chart`로 받음([3장](#3-데이터-준비)) |
| `KIS_PAPER_APP_KEY` / `KIS_PAPER_APP_SECRET` | 수집(`kis_chart`) | KRX 승인 전 일봉용. 백엔드에 등록한 앱키와 같으면 초당 호출 한도를 함께 쓰므로, 수집기는 1.1초 간격으로 하루 한 번만 부름. 계좌번호는 쓰지 않음 |
| `BACKEND_INTERNAL_BASE_URL` | AI 서버, 모니터, 운영 도구 | 보통 `http://localhost:8000`(Compose에서는 `http://backend:8000`) |
| `BACKEND_SIGNAL_URL` | AI 서버 | `<백엔드>/api/v1/internal/trading/signals`. 없으면 계획 게시가 명시적으로 실패 |
| `AI_SERVER_URL` | 백엔드, 스케줄러 | 보통 `http://localhost:8001` |
| `HQA_KRX_CLOSED_DATES` | 백엔드 | 비워 두면 기본값(2026-10-05 ~ 2027-09-16의 평일 휴장 14일) 사용. 설정하면 기본값 전체를 대체 |
| `HQA_KRX_SPECIAL_SESSIONS` | 백엔드 | KRX 공지가 나온 특별 세션만(`2026-11-19@10:00-16:30` 형식). [8장](#8-수능일-2026-11-19) 참고 |
| `HQA_PAPER_AUDIT_PATH`, `HQA_LLM_BUDGET_PATH` | AI 서버, 모니터 | 영구 볼륨에 둠. 지우거나 초기화하지 않음 |

## 2. 백엔드 준비

1. DB를 백업하고 Flyway가 V9(PAPER 주문 수명주기)와 V10(분석 결과 저장)까지 적용됐는지 확인합니다.
2. 예전 계획과 주문을 정리합니다. 같은 종목의 활성 계획이 둘 이상이면 먼저 해소해야 합니다.
3. KIS 호출 설정은 초기값을 유지합니다. 실제 KIS 한도를 확인하기 전에는 바꾸지 않습니다.

   ```properties
   hqa.kis-paper-requests-per-second=1
   hqa.paper-account-reserved-requests-per-second=0.5
   hqa.paper-lifecycle-poll-ms=20000
   hqa.paper-reconciliation-poll-ms=20000
   ```

4. 백엔드 테스트를 돌립니다(JDK 17). PostgreSQL 테스트 6개(분석 결과 기록 2개, 동시 게시·주문 4개)는 `HQA_TEST_DATABASE_URL`, `HQA_TEST_DATABASE_USERNAME`, `HQA_TEST_DATABASE_PASSWORD`를 줄 때만 실행됩니다. 운영 DB가 아닌 시험용 DB를 쓰세요. 동시성 테스트는 실제로 커밋한 뒤 자기 행을 지웁니다.

   ```bash
   mvn -f backend/pom.xml test
   ```

   로컬 PostgreSQL(예: Homebrew `postgresql@16`)의 시험용 DB로 돌리는 예입니다.

   ```bash
   HQA_TEST_DATABASE_URL=jdbc:postgresql://127.0.0.1:5432/hqa_test HQA_TEST_DATABASE_USERNAME=$USER HQA_TEST_DATABASE_PASSWORD= mvn -f backend/pom.xml test
   ```

5. 모니터, 계획 게시, 주문 수명주기를 바꿨다면 종단 시뮬레이션도 돌립니다. 실제 Python 모니터와 게시 코드를 실제 백엔드 주문 로직(운영과 같은 JSON 설정, 메모리 저장소, 모의 KIS)에 붙여 손절·익절 단계·예정 청산·진입·만료·KIS 한도·계획 게시 시나리오 16개를 돌립니다. 키와 DB 없이 1분 안에 끝나고, 결과는 `backend/target/paper-lifecycle-sim.txt`에 남습니다.

   ```bash
   cd backend && HQA_SIM_PYTHON=$PWD/../venv/bin/python mvn -q test -Dtest=PaperLifecycleSimulationTest
   ```

## 3. 데이터 준비

가격 선별에는 종목마다 완료된 일봉이 151개 이상 필요합니다. 수집 기간은 전날(KST)에 끝나도록 설계돼 있습니다.

**일봉 출처는 KRX(`chart`)가 기본이지만, KRX 키 승인 전까지는 `kis_chart`를 씁니다.** 승인 전 KRX Open API는 모든 서비스에 `401 Unauthorized API Call`을 돌려주고, 그러면 `chart` 수집이 실패해 빌드가 막힙니다. `kis_chart`는 KIS 모의투자 일봉에서 원주가(`FID_ORG_ADJ_PRC=1`)를 받아 KRX와 같은 무수정 기준으로 저장합니다. 분석은 정확한 KIS 엔드포인트와 무수정 기준이 붙은 KIS 봉만 받고, 근거 없는 봉과 한 창에 섞이면 그 종목을 거부합니다. 자세한 내용은 [변경 내역 7.11](ai-data-changes-2026-09.md#711-krx-승인-전-kis-일봉과-종목-확장-2026-10-10)에 있습니다.

- `chart`와 `kis_chart`는 둘 중 하나만 켭니다. 함께 켜면 수집기가 거부하고, `chart`가 실패해도 KIS로 몰래 바꾸지 않습니다.
- `KIS_PAPER_APP_KEY`와 `KIS_PAPER_APP_SECRET`이 필요합니다. 이 앱키는 백엔드 주문과 함께 쓰므로 호출을 1.1초 간격으로 보내고, KIS는 하루 한 번만 부릅니다. 그날 받은 결과에 마지막 거래일 봉이 있으면 같은 날의 다음 수집은 KIS를 다시 부르지 않으므로, 30분 루프가 장중에 백엔드의 호출 한도를 쓰지 않습니다.
- 처음 수집하면 종목당 550일(약 370거래일, 호출 4회 안팎)을 받아 분석 창 300거래일을 한 출처로 채웁니다. 이후에는 최근 7일만 다시 봅니다.
- 수집 데이터 위치는 `HQA_DATA_DIR`입니다(기본 `./data`). 로컬에서 Git에 올리지 않는 폴더에 모으려면 수집, 루프, AI 서버를 모두 같은 값으로 띄웁니다(예: `HQA_DATA_DIR=$PWD/.local/data`). 루프는 `--data-dir`, `--corp-codes-csv`를 넘기지 않으므로, 루프가 쓰는 기업코드 목록은 기본값 `./corp_codes.csv`입니다.

```bash
venv/bin/python -m scripts.data.corp_codes
venv/bin/python -m scripts.data.collect --theme 2차전지 --theme-key 2차전지 --enabled-sources news,dart,financials,kis_chart
venv/bin/python -m scripts.data.build --theme-key 2차전지 --stats
```

KRX 승인 뒤에는 `chart`로 돌아가고 지수(시장 맥락)도 함께 모읍니다. 두 출처 모두 무수정 기준이라 한 창에 KIS 봉과 KRX 봉이 섞여도 됩니다(같은 날짜는 나중 관측이 이깁니다).

```bash
venv/bin/python -m scripts.data.collect --theme 2차전지 --theme-key 2차전지 --enabled-sources news,dart,financials,chart
venv/bin/python -m scripts.data.market_context --from-date 20250901 --to-date 20261008
```

- 기업코드 목록은 기본으로 `./corp_codes.csv`이고, 수집기가 7일마다 자동으로 갱신합니다(`DART_API_KEY` 필요). OpenDART 점검(`status=800`) 중에는 기존 파일을 그대로 씁니다.
- 분석 대상 테마는 `data/raw/theme_targets/<key>.jsonl`입니다. 10-10에 수익률이 아닌 데이터 기준(20일 평균 거래대금 10억 원 이상, DART 기업코드, 2차전지와 겹치지 않는 업종)으로 2차전지 8종목, 자율주행차 8종목, 5G 8종목을 정했습니다. 규칙과 결과는 [변경 내역 7.11](ai-data-changes-2026-09.md#711-krx-승인-전-kis-일봉과-종목-확장-2026-10-10)에 있습니다. `scripts.data.discover`는 기본으로 카탈로그에만 저장하고, `--as-targets`일 때만 대상 목록을 바꿉니다. 다만 Naver가 테마 페이지를 새 사이트(`stock.naver.com`, 브라우저에서 그리는 화면)로 옮겨, `discover`와 `collect --refresh-targets`의 파서는 지금 테마를 찾지 못합니다. 저장된 대상 목록으로 수집하는 기본 동작에는 영향이 없습니다.
- 운영 중에는 수집 루프를 띄워 둡니다. `--themes` 없이 실행하면 저장된 테마 전부를 수집합니다. KRX 승인 전에는 출처를 `kis_chart`로 넘기고, 승인 뒤에는 기본 출처(`chart`)에 `--market-context`를 더해 08:00 이후 하루 한 번 지수를 갱신합니다.

  ```bash
  venv/bin/python -m scripts.data.loop --enabled-sources news,dart,financials,kis_chart
  venv/bin/python -m scripts.data.loop --market-context
  ```

## 4. 연동 확인 (처음 한 번)

아래 단계는 아직 실제 키로 돌려 보지 못한 부분입니다. 순서대로 확인하고, 실패하면 다음 단계로 넘어가지 않습니다.

1. **KIS 모의투자 연결:** `.env`의 `KIS_PAPER_APP_KEY`, `KIS_PAPER_APP_SECRET`, `KIS_PAPER_ACCOUNT_NO`(8자리 또는 8-2자리)로 조회만 합니다. 토큰과 시세는 앱키·시크릿만으로 확인하고, 잔고·매수 가능 금액·당일 주문 조회는 계좌번호까지 씁니다. 요청 형식과 거래 코드(tr_id)는 백엔드와 같습니다. 계좌번호 형식이 틀리면 건너뛰지 않고 오류로 알립니다. 키·토큰·계좌번호는 출력하지 않습니다.

   ```bash
   RUN_KIS_LIVE_TESTS=1 venv/bin/python -m pytest -q tests/test_kis_paper_trading.py
   ```

   주문 경로까지 보려면 `RUN_KIS_ORDER_TEST=1`을 더합니다. 시세보다 10% 낮은 1주 지정가 매수를 넣고 바로 취소합니다(장중에만). 취소가 실패하면 KIS 앱에서 주문을 확인해 직접 취소하세요. `venv/bin/python tests/test_kis_paper_trading.py`로 실행하면 짧은 보고서가 나옵니다. KIS는 토큰을 앱키당 1분에 한 번만 발급하므로, 연달아 실행하면 1분을 기다린 뒤 진행합니다.

2. **백엔드 내부 API:** 등록한 PAPER 사용자로 계좌 스냅샷(`/api/v1/internal/trading/account-snapshots`)과 시세(`/api/v1/internal/market/price-snapshots`)가 `success=true`, `source=kis`로 오는지 봅니다.
3. **AI 서버:** 워커 하나로 띄우고 `/health`의 `calendar_warnings`와 `/internal/status`(내부 토큰)의 예산 상태를 확인합니다.

   ```bash
   venv/bin/python -m uvicorn ai_server.app:app --host 127.0.0.1 --port 8001 --workers 1
   ```

4. **OpenAI 호출:** 대시보드나 `POST /runtime/stock-preview`로 종목 하나를 미리보기 합니다. 계좌·주문 없이 세 전문가가 모두 결과를 내야 합니다.
5. **모니터 1회:** 장중에 실행해 `session`이 `open`이고 `errors`가 비었는지 봅니다. 장 밖에서는 평가만 하고 아무것도 보내지 않습니다(`session: closed`).

   ```bash
   venv/bin/python -m src.runner.signal_monitor --once
   ```

6. **분석 사이클 1회:** `--forever` 없이 실행하면 한 번만 돕니다. 계좌별 `submitted`, `failed`, `error`, `skipped_plans`, `rejected_plans`, `omitted_candidates`를 확인합니다. 원격 클라이언트는 900초까지만 기다리므로, 그보다 오래 걸리면 결과는 AI 서버의 `/runtime/tasks/<id>`에서 봅니다.

   ```bash
   AI_SERVER_URL=http://localhost:8001 venv/bin/python -m src.runner.analysis_scheduler
   ```

7. **주문 왕복:** 작은 금액으로 계획 게시 → 진입 → 체결 → 손절·청산(또는 미체결 취소)까지 한 번을 실제로 확인합니다. 이것이 롤아웃 조건의 "PAPER quote/order/fill/cancel integration"입니다.

## 5. 기동 순서

1. PostgreSQL, Redis
2. 백엔드
3. AI 서버(워커 1개)
4. 수집 루프(`scripts.data.loop --market-context`)
5. 시그널 모니터(`src.runner.signal_monitor`) — 장 밖에서는 백엔드·KIS를 부르지 않고 대기합니다.
6. 분석 스케줄러(`AI_SERVER_URL=... src.runner.analysis_scheduler --forever`) — 검증된 장중에만 15분마다 돕니다.

Docker Compose에서는 `docker compose --profile paper up`이 5·6(모니터, 스케줄러)을 켭니다. 기본 Compose는 켜지 않고, 수집 루프는 Compose 서비스가 없어 따로 실행합니다.

## 6. 매일 점검

**장 전 (08:00–09:00)**

- 수집 루프 로그: 테마별 `[COLLECT] ... 완료`, `[MARKET] 지수 갱신 완료`. 한도 초과(`status=020`/`status=429`)면 다음 날까지 쉽니다.
- 전 거래일 봉: 수집 기간이 전날에 끝나므로 전 거래일 봉은 자정(KST)이 지나야 들어옵니다. 00:00부터 09:00 전 사이에 루프가 한 번은 돌아야 합니다(밤에 컴퓨터가 잠들면 깨어난 뒤 첫 수집). KRX 승인 전에는 `reports/<테마>_ingestion_report.json`의 `kis_chart`가 `success`이고, 분석 로더 오류에 `stale_daily_prices`가 없는지 봅니다.
- `/health`의 `calendar_warnings`가 비었는지(10월 29일부터는 수능일 경고가 뜹니다).
- 예산과 결과 불명 주문:

  ```bash
  venv/bin/python -m scripts.llm_budget status
  venv/bin/python -m scripts.paper_orders unknown
  ```

  `unknown` 목록은 비어 있어야 합니다. 있으면 [7장](#7-문제가-생겼을-때)대로 처리합니다.

**장중**

- 모니터 로그(`signal_monitor {...}`): `slo_met`, `errors`, `uncovered_holdings`의 `reason`, `max_quote_age_seconds`, `elapsed_seconds`(목표 30초 이내).
- 분석 사이클 요약: 실패한 계좌의 `error`, 건너뛴 계획(`skipped_plans`), 거부된 계획(`rejected_plans`), 검토하지 못한 후보(`omitted_candidates`). 보유 종목 계획이 거부되면 그 종목은 이전 계획을 유지합니다. `omitted_candidates`에 `risk_manager_input_budget`이 자주 보이면 RiskManager 입력이 한도에 차 있다는 뜻이니, 감사 기록의 `risk_manager_input`(추정치와 예산)을 확인합니다.

**장 후**

```bash
venv/bin/python -m backtesting paper-runtime --audit data/paper_audit.sqlite3 --budget data/llm_budget.sqlite3
```

완료율과 거부 건수를 지연 시간과 함께 기록합니다. 모든 요청을 거부해서 빨라진 것은 개선이 아닙니다.

## 7. 문제가 생겼을 때

| 보이는 것 | 뜻 | 할 일 |
|---|---|---|
| `missing_protection` / `no_active_plan` | 보유 종목에 활성 계획이 없음 | 다음 분석 사이클에서 그 종목의 HOLD 계획이 나오는지 확인. 계좌가 `failed`면 그 `error`부터 |
| `missing_protection` / `protection_blocked:order_without_broker_id` | KIS 주문 호출이 시간 초과·실패해 주문번호가 없음. 그 계획의 모든 트리거가 보류됨 | 아래 결과 불명 주문 처리 |
| `protection_blocked:BROKER_ORDER_ID_NOT_UNIQUE_OR_MISSING`, `BROKER_ORDER_IDENTITY_MISMATCH`, `BROKER_FILL_...` | 재조정이 증권사 기록과 맞지 않음 | 백엔드 로그와 KIS 주문내역 대조. 자동 해결 없음, 개발자 확인 필요 |
| `entry_fill_unrecorded`(60초 넘게) | 진입이 체결됐는데 백엔드가 기록하지 못함 | 백엔드 재조정(20초 주기) 로그 확인 |
| `partially_managed:<관리>/<보유>` | 계좌에서 누군가 직접 매매함 | 관찰 규칙 위반으로 기록. 다음 분석 사이클에서 관리 수량이 다시 맞춰짐 |
| `monitor_capacity_exceeded:<n>/<한도>` | 보유+계획 종목이 계정당 한도(10) 초과 | 신규 진입이 막힘. 보유 종목은 계속 감시됨 |
| `rejections`의 `PRICE_DRIFT_EXCEEDED`, `ENTRY_...` | 진입 거부(매매 판단) | 정상. 반복되면 계획의 진입 범위 확인 |
| KIS 잔고·주문 조회 `OPSQ2000 INVALID_CHECK_ACNO` | 계좌번호 형식이 틀렸거나(앞 8자리+상품코드 2자리가 아님), 앱키가 다른 모의투자 계좌에 연결됨. 모의투자 앱키는 특정 모의투자 계좌에 묶여 발급됨 | 계좌번호 8자리 확인. 모의투자를 새로 신청했다면 그 계좌로 API 서비스를 다시 신청해 받은 앱키·시크릿 사용 |
| 매도 거부 `KIS_RATE_LIMITED`, `ORDER_NOT_SENT_RATE_QUEUE_FULL` | KIS 초당 한도 | 다음 폴링에서 다시 보냄. 계속되면 보유 종목 수와 분석 사이클 시각 확인 |
| 매도 거부 `ORDER_RECONCILIATION_REQUIRED`(1~2회) | 같은 계획의 다른 매도 주문(일부 매도, 다른 청산 그룹)이 아직 걸려 있어 백엔드가 취소하고, 취소가 확인되면 이 청산이 나감 | 정상. 2분 넘게 이어지면 계획의 `unresolvedOrders`를 보고 UNKNOWN이면 아래 결과 불명 주문 처리 |
| 사이클 요약의 게시 실패 `HTTP_409 ... ORDER_RECONCILIATION_REQUIRED_BEFORE_PLAN_UPDATE` | 그 종목에 주문이 걸려 있는 동안에는 백엔드가 계획 교체를 받지 않음. 기존 계획이 계속 보호함 | 정상. 주문이 끝난 뒤 다음 사이클에서 갱신됨. 같은 종목에서 계속되면 `unresolvedOrders` 확인 |
| `MARKET_CLOSED` 오류(검증된 장중) | Python과 백엔드 캘린더 불일치 | `HQA_KRX_CLOSED_DATES`/`HQA_KRX_SPECIAL_SESSIONS`와 `trading_calendar.py` 비교 |
| 예산 차단, 미정산 요청 | LLM 예산 원장 | `scripts.llm_budget`(`settle`/`release`/`acknowledge-overrun`). 원장은 지우지 않음 |
| 모니터 `status: failed`, `consecutive_failures` 증가 | 백엔드·네트워크 장애 | 모니터는 계속 재시도함. 백엔드 상태 확인 |

**결과 불명(UNKNOWN) 주문 처리**

1. 목록에서 날짜, 종목, 매수/매도, 수량, 시각을 확인합니다.

   ```bash
   venv/bin/python -m scripts.paper_orders unknown
   ```

2. KIS 모의투자 앱이나 HTS의 주문내역에서 그 주문을 찾습니다.
3. 있으면 주문번호를 연결합니다. 백엔드가 KIS 주문내역과 종목·방향·수량을 대조한 뒤에만 연결하고, 이후 체결·취소 재조정이 다시 돕니다.

   ```bash
   venv/bin/python -m scripts.paper_orders adopt <execution_id> <주문번호> --note "KIS 앱 주문내역에서 확인"
   ```

4. 없으면 미접수로 기록합니다. KIS 주문내역에 이 주문일 수 있는 건이 있으면 백엔드가 거부합니다(그 경우 3번으로).

   ```bash
   venv/bin/python -m scripts.paper_orders not-submitted <execution_id> --note "주문내역에 없음 확인"
   ```

## 8. 수능일 (2026-11-19)

KRX의 거래시간 공지는 보통 2주 전에 나옵니다. 공지 전까지 시스템은 다음처럼 동작합니다.

- 분석은 그날 멈춥니다(fail closed). 10월 29일부터 `/health`와 스케줄러 로그에 경고가 뜹니다.
- 모니터는 09:00–16:30 동안 보호 트리거만 보내고 진입은 보내지 않습니다.
- 백엔드는 설정이 없으면 09:00–15:30으로 판단해, 15:30 이후의 손절·청산을 `MARKET_CLOSED`로 거부합니다.

공지가 나오면 두 곳을 함께 고칩니다.

1. `src/runner/trading_calendar.py`의 `SPECIAL_CLOSES`에 공지의 개장·폐장 시각, 공지 시각, 출처 URL을 넣고 테스트를 돌린 뒤 배포합니다.
2. 백엔드에 `HQA_KRX_SPECIAL_SESSIONS=2026-11-19@10:00-16:30`(공지 시간대로)을 설정하고 재시작합니다.

## 9. 알려진 한계

- **보호 매도 가격:** 현재가 지정가로 매도합니다. 급락 중에는 체결되지 않고 2분 뒤 취소·재주문될 수 있으니 체결률을 관찰합니다.
- **KIS 호출 예산:** 계정당 초당 1회입니다. 분석 사이클의 시세 조회(보유+신규 최대 15종목)와 모니터의 20초 주기 조회가 겹치면 일부 시세가 실패할 수 있습니다. 보유 종목이 10개에 가까우면 관찰이 필요합니다.
- **로컬 모델:** Ollama 소형 모델은 RiskManager 출력이 반복·잘려 계좌가 실패로 닫히는 경우가 많습니다(주문은 나가지 않음). PAPER 판단 근거로 쓰지 않습니다.
- **미지원:** 영숫자 종목코드(분석에서 제외하고 보고만 함), REAL 주문, 피라미딩(보유 종목 추가 매수).
