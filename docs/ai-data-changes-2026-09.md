# AI·데이터 변경 내역 (feat/dohoon-changes, 2026-09~10)

`feat/dohoon-changes` 브랜치에서 AI 분석, 데이터 수집, 백테스트를 무엇을, 왜, 어떻게 바꿨는지 정리한 문서입니다. 코드를 열지 않고도 바뀐 동작과 운영 시 주의할 점을 알 수 있게 썼습니다. 각 변경의 자세한 근거는 괄호 안 커밋의 메시지(`git show <커밋>`)에 있습니다.

- **기간:** 2026-09-21 ~ 2026-09-29, 그리고 PAPER 준비 작업 2026-10-09 ([7장](#7-10월-paper-준비-작업-2026-10-09))
- **구성:** `main`에서 분기 → `ai-data-main` 작업 이식 4개 커밋(`498e6e3`~`54e581e`) → 점검·수정 30개 커밋(`2088844`~`8ef0e83`) → 10월 PAPER 준비 9개 커밋(`22db295`~`f4bd145`) → 10월 전체 점검 10개 커밋(`7d2c0f9`~`f5a0e12`, [7.7](#77-전체-점검-2026-10-09-저녁))
- **규모 (9월 수정 30개 커밋):** 64개 파일, +3,142 / −298줄, 그중 테스트 파일 23개. 9월에는 Spring 백엔드와 프론트엔드 코드를 바꾸지 않았습니다.
- **규모 (10월 9개 커밋):** 36개 파일, +2,168 / −173줄, 그중 테스트 파일 14개. Spring 백엔드도 수정했습니다(주문 수명주기, 세션 캘린더, 운영자 도구).
- **테스트:** 9월 기준 오프라인 1,247개 통과, 3개 건너뜀. 10월 작업 뒤 Python 1,344개 통과·3개 건너뜀(실제 KIS 테스트), 백엔드 `mvn test` 117개 실행·실패 0·건너뜀 2. 커밋 각각에서 따로 돌려도 통과합니다.
- **데이터:** 수집 데이터, 가격, 예산 원장, 실험 결과는 커밋하지 않았습니다. 실제 실행은 Git이 추적하지 않는 `.local/`에서 했습니다.

## 진행 순서

1. **작업 이식 (09-21):** `ai-data-main`의 멀티 에이전트 실험 옵션(`AGENT_SCORE_PROFILE`, `AGENT_SCORE_CACHE_ONLY` 등), 네이버 뉴스 기간 파라미터, 연구 스크립트(`scripts/research/`)를 가져왔습니다.
2. **구조 감사 (09-23):** 41개 에이전트로 AI 런타임과 데이터 파이프라인을 점검해 결함 64건을 확인했습니다.
3. **실제 실행 (09-27):** DART·네이버 실데이터와 로컬 Ollama로 전체 흐름을 돌렸습니다. 휴장일 누락, 영숫자 종목코드, 전문가 입력 한도 초과처럼 테스트로는 드러나지 않던 결함을 찾았습니다.
4. **수정 2회 (09-28):** 커밋 18개를 만들었고, 수정마다 실제 실행으로 다시 확인했습니다.
5. **독립 검토 (09-28~29):** 수정한 코드를 런타임·서버, 수집·증거, 백테스트·연구 세 영역으로 나눠 따로 검토했습니다. 확인된 결함 28건과 확인 과정에서 추가로 찾은 1건을 모두 고쳤습니다(커밋 12개). 새로 추가한 테스트는 모두 이전 코드에서는 실패하는 것을 확인했습니다.

## 1. 운영자가 알아야 할 동작 변화

| 항목 | 이전 | 이후 |
|---|---|---|
| 2026-11-01 이후 분석 | 모든 가격 계산이 중단 | 공지가 없는 특별 세션(2026-11-19 수능)만 차단, 21일 전부터 경고 |
| 2026-06-03·07-17 휴장 | 라이브러리에 없어 모든 종목이 "가격 이력 불완전" | 휴장으로 반영 |
| 수능일 거래시간 | 09:00–15:30으로 가정 | KRX 공지대로 10:00–16:30 |
| 한 테마의 데이터 오류 | 모든 테마 로딩 실패 | 그 테마만 제외하고 오류 보고 |
| 모니터·스케줄러 | 첫 오류에 종료 | 실패를 기록하고 계속 동작 |
| LLM 처리 순서 | 정기 사이클 후보가 가장 낮은 순위(채팅보다 뒤) | 보유 종목 → 정기 사이클 → 미리보기·채팅 → 백그라운드 |
| 종목 미리보기 | 백엔드 URL이 없으면 `KeyError` | 백엔드 설정 없이 동작 |
| 전문가 입력 | 실데이터에서 Analyst 입력 15~16k 토큰(한도 12k 초과) | 역할 한도 안에서 우선순위대로 채우고 생략 개수 기록 |
| `/backtest/results` | 인증 없음, 크기 무제한, CORS `*` | 내부 토큰, 16 MiB 상한, CORS 기본 꺼짐 |
| Python의 REAL 주문 | 가능 | 차단(PAPER만 허용) |
| LLM 예산 원장 | 정산·검토 도구 없음, 초과 1건이면 영구 차단 | 운영 CLI와 `GET /internal/status` |
| 수집 루프 | 출력에 "429"나 "한도"만 있어도 다음 날까지 정지 | 실패로 끝난 요청의 `status=020`·`status=429`만 인정 |
| 테마 탐색(`discover`) | 라이브 분석 대상 목록을 덮어씀 | `data/theme_catalog/`에 저장(`--as-targets`일 때만 교체) |
| 증거 generation | 계속 누적 | 최신 8개와 교체 후 24시간 이내 것만 보존 |
| 거래정지 봉 | 라이브: 1년 넘게 해당 종목 분석 불가 / 백테스트: 행을 버림 | 무거래 봉으로 유지, 최근 거래일이 정지면 신규 진입만 금지 |
| 백테스트 시점 | 당일 문서 사용, 데이터가 끊긴 종목 제외 | 전날까지의 문서만 사용, 끊긴 종목도 유지 |
| 연구 재실행 | 이전 결과를 무조건 이어받음 | 엔진·프롬프트 버전이 다르면 다시 실행 |

## 2. 영역별 상세

### 2.1 거래일 캘린더 — `src/runner/trading_calendar.py`

- **기간 전체 차단 제거** (`2088844`): 2026-11-01부터 모든 가격 계산을 막던 일괄 경계를 없앴습니다. 대신 공지가 없는 날(`PENDING_SPECIAL_SESSION_NOTICES`, 현재 2026-11-19)만 막습니다. 검토 기한 `CALENDAR_REVIEWED_THROUGH=2027-09-30`이 지나면 `calendar_review_expired`로 막습니다. `calendar_review_warnings()`가 21일 전부터 경고하고, 경고는 `/health`와 `/internal/status`에 표시됩니다.
- **휴장일 추가** (`2088844`): KRX가 2026-05-20에 발표한 2026-06-03(지방선거)과 2026-07-17(제헌절) 휴장을 `EXCHANGE_HOLIDAY_OVERRIDES`로 반영했습니다. 고정 버전인 exchange-calendars 4.13.2에는 두 날이 없습니다. `CALENDAR_VERSION`은 `…:krx-notices-2024-2026-v2`로 바뀌었습니다.
- **수능일 개장 시각** (`4a97669`): `SPECIAL_CLOSES`에 2024·2025년 수능일 개장(10:00)을 폐장(16:30)과 함께 기록했습니다. 두 날 모두 KRX 공지로 확인했습니다. `daily_session_open`과 `daily_session_close`는 같은 검증을 거치고, 스케줄러는 실제 세션 시간 안에서만 분석합니다. 앞으로 공지를 추가할 때는 개장과 폐장을 모두 넣어야 하며, 빠지면 테스트가 실패합니다.

### 2.2 데이터 수집 — `src/ingestion/`, `scripts/data/`

- **네이버 뉴스** (`219354d`, `892e0f6`)
  - 기간 지정 수집에는 네이버 기간 파라미터를 붙입니다.
  - 첫 페이지가 비었는데 "검색결과가 없습니다" 안내도 없거나 파싱할 수 없으면(차단 페이지) 실패로 처리합니다. 뉴스 없는 generation이 조용히 게시되지 않게 하기 위해서입니다.
  - `YYYY-MM-DD` 날짜는 정규화한 뒤 필터링합니다.
- **DART** (`892e0f6`, `a295896`): 연간 보고서가 없는 종목(모든 연도가 013)은 실패가 아니라 `no_data`로 기록하고 메모 `provider_no_annual_report`를 남깁니다. 기본값인 증분 모드에서도 메모가 유지됩니다.
- **영숫자 종목코드** (`892e0f6`, `870f529`): KRX가 최근 상장 종목에 붙이는 코드(예: `0126Z0`) 63건 때문에 DART 기업코드 목록 전체가 거부되던 문제를 고쳤습니다. 이런 코드는 건너뛰고 건수를 경고로 남깁니다. 지원 자체는 아직 없습니다.
- **요청 한도 감지** (`4a1b723`, `a0fd2b6`): 수집 루프는 최종 실패한 요청이 남긴 `status=020`(OpenDART)과 `status=429`만 한도 초과로 봅니다. 재시도로 회복된 429는 무시하고, 네이버·DART 래퍼는 최종 오류에 HTTP 상태를 보존합니다.
- **KRX 게시 시각** (`870f529`, `a295896`): KRX 일별 데이터는 다음 날 08:00에 게시되므로 그 전에는 전 거래일 봉을 요청하지 않습니다. 전날이 휴장이면 이 제한을 두지 않습니다.
- **테마 탐색** (`870f529`): `scripts.data.discover`가 라이브 분석 대상(`raw/theme_targets`)을 테마당 최대 200종목으로 덮어쓰던 것을 `data/theme_catalog/` 저장으로 바꿨습니다.

### 2.3 증거 인덱스 — `src/evidence/`

- **문서 소스만 인덱싱** (`32d0764`): 재무·테마 소속 행이 빈 문서로 인덱싱되던 문제를 막았습니다. 문서 소스는 `news`, `general_news`, `dart`, `forum`, `report` 허용 목록으로만 판정합니다.
- **generation 정리** (`7acd37f`, `1c116ba`)
  - 빌드마다 쌓이던 generation을 게시 직후 정리합니다. 3종목 기준 generation 하나가 약 0.9 MB이고, 테마 6개를 30분 주기로 돌리면 하루 수 GB가 쌓였습니다.
  - 현재 것, 최신 `HQA_GENERATION_KEEP`개(기본 8), 교체된 지 `HQA_GENERATION_MIN_AGE_HOURS`(기본 24시간)가 지나지 않은 것은 남깁니다.
  - 최소 보존 시간은 교체 시점부터 셉니다. 그래서 실행 중인 분석이 쓰는 generation은 만든 지 오래됐어도 지워지지 않습니다.
  - 잘못된 설정값은 빌드 전에 오류로 알리고, 정리 실패는 경고만 남깁니다.

### 2.4 라이브 분석 — `src/runner/`

- **장애 격리** (`ea94f33`)
  - 테마 하나의 오류가 전체 유니버스를 멈추지 않습니다.
  - 시그널 모니터와 스케줄러는 실패를 감사 기록으로 남기고 계속 동작합니다.
  - `src.runner`를 지연 로딩해, 모니터와 스케줄러가 레거시 에이전트·LLM·KIS 모듈을 불러오지 않습니다.
- **처리 우선순위** (`6224bc2`, `0fff486`)
  - `LLMTaskPriority`에 `SCHEDULED=5`를 추가했습니다. 순서는 보유(0) → 정기 사이클(5) → 미리보기·채팅(10) → 백그라운드(20)입니다.
  - 같은 입력을 계산 중인 호출에는 우선순위가 같거나 높은 호출만 합류합니다. 그래서 보유 종목이 미리보기의 대기 순서나 예산 등급(운영 목표 $90, 보유 보호 $100)을 물려받지 않습니다.
- **세션 판정** (`6224bc2`, `4a97669`): KRX 캘린더를 따르므로 휴장일에는 LLM 예산을 쓰지 않고, 수능일에는 특별 거래시간을 따릅니다.
- **미리보기** (`6224bc2`, `4a97669`): 백엔드 계좌 클라이언트를 처음 필요할 때 만들기 때문에 백엔드 설정 없이도 미리보기가 됩니다. 실패하면 오래된 가격이나 로딩에 실패한 테마처럼 실제 원인을 알려 줍니다.
- **전문가 입력 한도** (`ef8ae99`, `752daf5`)
  - Analyst 이벤트와 Quant 공시를 우선순위대로 역할 예산에 채웁니다. 예산은 한도 12k에서 프롬프트 여유분 1.5k를 뺀 값입니다.
  - 생략한 개수는 `data_gaps`에 기록합니다.
  - 토큰 수는 한국어 뉴스와 DART로 보정한 보수적 추정치를 씁니다. o200k 실측보다 항상 크게 나옵니다.
  - 붙일 생략 메모까지 포함해 크기를 재므로, 한도 경계에서 Analyst 입력이 통째로 빠지지 않습니다.
  - Chartist 입력은 설계상 크기가 제한돼 있어 줄이지 않습니다.
- **요청 계약과 인용** (`aca1ab2`)
  - 종목코드 패턴을 `[0-9]{6}`으로 바꿨습니다. Ollama의 문법 디코더가 `\d`를 거부합니다.
  - 전문가 요청에 기대 역할, 종목코드, 0–100 점수 척도를 명시했습니다. 이전에는 로컬 모델이 세 역할 모두 "analyst"로 답했습니다.
  - 기술 지표와 가격 요인에 인용 가능한 source ID를 붙였습니다.
  - 분석 기간보다 오래된 회계연도는 조용히 건너뜁니다.
  - 런타임 `PROMPT_VERSION`은 9월 기준 `hqa-fixed-dag-v7-role-contract`였고, 10월에 `hqa-fixed-dag-v8-backend-plan-rules`로 바뀌었습니다([7.2](#72-분석과-계획-게시)).
- **증거 오류 요약** (`752daf5`): 사용할 수 없는 증거 행을 한 줄씩 gap으로 넣지 않고, 사유별로 `invalid_evidence:<사유>:count=N:first=<문서ID>` 하나만 남깁니다. 날짜가 상대 표기뿐인 뉴스가 많아도 프롬프트가 한도를 넘지 않습니다.
- **거래정지 봉** (`4b485bf`)
  - KRX와 네이버는 거래정지일을 시가·고가·저가 0, 전일 종가, 거래량 0으로 줍니다. 이런 봉은 종가로 평평한 무거래 봉으로 받습니다.
  - 이전에는 정지일 하나가 300거래일 창에 남아 있는 동안 그 종목을 분석할 수 없었습니다.
  - 최근 거래일이 정지 상태면 `no_trade_latest_session`을 붙여 신규 진입만 막습니다.

### 2.5 AI 서버 — `ai_server/app.py`

- **백테스트 결과 API** (`152f7b8`, `5243eb3`): `POST/GET /backtest/results`는 내부 토큰(`X-HQA-Internal-Token`)이 있어야 하고, 본문은 16 MiB(`HQA_MAX_BACKTEST_RESULT_BYTES`)까지 받으며 작업 ID를 검증합니다. `submit_result`는 토큰을 함께 보냅니다.
- **CORS** (`152f7b8`): 기본으로 꺼져 있습니다. 프론트엔드는 백엔드 `/api/v1`을 거치므로 영향이 없습니다. 브라우저가 AI 서버를 직접 호출할 때만 `HQA_AI_CORS_ORIGINS`를 설정하세요.
- **뉴스·공시 피드** (`3d0b77b`, `0fff486`)
  - 원본 JSONL 전체를 이벤트 루프에서 읽던 방식을, 종목별 표시 필드만 담은 크기 제한 LRU 인덱스로 바꿨습니다.
  - 요청 개수를 채울 때까지 중복을 제거하고, 잘못된 종목코드에는 400을 반환합니다.
  - 피드와 상태 조회는 전용 읽기 스레드 풀에서 처리합니다. 그래서 긴 분석 작업이 기본 스레드를 모두 차지해도 응답합니다. 이전에는 대량 미리보기 중에 백엔드의 30초 제한에 걸려 빈 목록이 나왔습니다.
- **상태 조회** (`152f7b8`, `be65e86`): `/health`에 캘린더 경고가 추가됐습니다. `GET /internal/status`(내부 토큰 필요)는 다음을 보여 주며, 원장 파일을 새로 만들지 않습니다.
  - 예산 스냅샷, 미정산 요청, 검토하지 않은 초과 건
  - 캘린더 경고, 런타임 작업 상태, 테마별로 게시된 generation

### 2.6 LLM 설정·예산·주문 안전

- **REAL 주문 차단** (`3000f07`): `place_domestic_stock_order`와 `KISRealtimeTool.place_order`는 PAPER가 아니면 요청을 만들기 전에 거부합니다. PAPER 주문은 Spring 백엔드가 담당합니다.
- **예산 원장 운영 도구** (`be65e86`, `c40d259`)
  - `scripts.llm_budget`에 `status`, `settle`, `release`, `acknowledge-overrun` 명령을 추가했습니다(3절 참고).
  - 기존 원장에는 `reviewed_at`, `review_note` 열이 자동으로 추가됩니다.
  - 전송 전에 멈춘 `reserved` 요청도 미정산 목록에 보이고, `release`로 해제할 수 있습니다.
- **시간 제한과 로컬 모델** (`6224bc2`, `189f3c2`)
  - RiskManager와 thinking 역할은 기본 제한 시간이 180초입니다. 시간 초과 호출도 과금되기 때문입니다.
  - 역할별 제한 시간은 `HQA_LLM_<역할>_TIMEOUT_SECONDS`로 바꿀 수 있습니다.
  - 로컬 Ollama의 컨텍스트 창은 `OLLAMA_NUM_CTX`(2048 이상)로 정합니다.

### 2.7 백테스트 엔진 — `backtesting/`

- **시점(PIT)** (`28141dc`, `7ad3bd7`): 결정은 기준일 종가에 하므로 문서는 발행 다음 날부터 씁니다. 장 마감 뒤 공시를 포함한 당일 문서는 문서 점수와 LLM 문맥에서 모두 제외합니다. 문서에서 추론한 테마 편입도 첫 문서 다음 날부터 유효합니다.
- **생존 편향** (`28141dc`): 보유 기간은 종목 행 수가 아니라 시장 거래일로 셉니다. 보유 중 데이터가 끊긴 종목도 후보와 벤치마크에 남깁니다. 전체 데이터가 끝난 구간만 평가에서 뺍니다.
- **거래정지** (`28141dc`, `7ad3bd7`)
  - 거래량 0인 정지 봉은 버리지 않고, 정지 중에는 손절·익절을 체결하지 않습니다.
  - 청산일에 거래가 없으면 거래가 재개된 뒤 첫 종가에 청산합니다(`exit_delayed_by_halt`).
  - 데이터가 다시 이어지지 않을 때만 `stock_data_ended`로 청산합니다.
- **체결** (`28141dc`, `7ad3bd7`): 손절선이나 목표가를 갭으로 넘어서면 시가로 체결합니다. 시가가 목표가 이상이면 그날 저가와 무관하게 시가로 익절합니다.
- **가격 기준 변경** (`28141dc`, `7ad3bd7`)
  - 종가가 ±30.5%를 넘게 움직이면 분할·병합 같은 가격 기준 변경으로 봅니다.
  - 해당 구간은 후보와 벤치마크에서 제외하고, `execution.ineligible_counts`에 사유별로 기록합니다.
  - 검사는 실제 청산 봉까지 합니다. 정지 때문에 늦어진 청산도 포함됩니다.
- **점수와 공통 규칙** (`7ad3bd7`)
  - LLM 점수 0은 유효한 점수로 보고 규칙 점수로 바꾸지 않습니다.
  - 기술 기준선과 파라미터 sweep도 같은 규칙을 씁니다.
  - 결과에는 `execution.model_version`이 기록됩니다.

### 2.8 LLM 캐시와 연구 재현성 — `backtesting/llm_signal.py`, `backtesting/proof_validation.py`

- **fallback·캐시 전용** (`5243eb3`): 멀티 에이전트 fallback 점수는 캐시하지 않습니다. `AGENT_SCORE_CACHE_ONLY=1`인데 캐시가 없으면 규칙 점수로 몰래 바꾸지 않고 `LLMCacheMissError`로 실행을 멈춥니다.
- **캐시 키** (`5243eb3`, `28141dc`, `162b4db`)
  - 프롬프트 버전이 단일 LLM은 `temporal_theme_leader_llm_v3_prior_day_evidence`, 멀티 에이전트는 `temporal_theme_leader_multi_agent_v4_prior_day_evidence`로 바뀌었습니다. 그래서 당일 문서로 만든 이전 캐시는 재사용되지 않습니다.
  - 결과를 바꾸는 설정은 키에 포함됩니다: `AGENT_PURE_FEATURES`, `AGENT_FREE_RISK_MANAGER`, 기본값이 아닌 `context_docs`.
  - `AGENT_CACHE_LEGACY_KEYS=1`은 과거 실행 재현용이며, 사용 횟수가 메타데이터 `legacy_cache_hits`에 남습니다.
- **불완전한 응답** (`162b4db`): 점수나 신뢰도 필드가 빠진 LLM 응답은 거부합니다. 이전에는 빠진 값이 기본값 50으로 채워져 진짜 점수처럼 캐시됐습니다.
- **이어받기** (`162b4db`): 연구 실행기는 실행 모델 버전이나 프롬프트 버전이 다르거나 legacy 캐시를 쓴 저장 결과를 이어받지 않고 다시 실행합니다. 검증 현황 화면(`validation-status`)도 현재 프롬프트 버전의 캐시만 사용 가능으로 셉니다.

### 2.9 에이전트 ablation 근거 — `scripts/research/build_agent_architecture_validation.py`

논문용 비교표를 만드는 생성기의 계산 방식을 바로잡았습니다(`01193fa`, `8ef0e83`).

- **캐시 결합**
  - 전체 키(프롬프트 버전, 모델, 테마, 날짜, 종목, 점수, v4부터는 설정)가 정확히 맞을 때만 결합합니다.
  - 모호한 결합은 누락으로 봅니다. 점수가 없는 후보는 채우지 않고 제외합니다.
  - 결합률이 `--min-agent-coverage`(기본 90%)보다 낮은 실행은 비교에서 뺍니다.
- **비교 기준**
  - 에이전트를 하나씩 뺄 때의 기준선은 3-agent 조합이고, RiskManager는 이 조합에 더해서 평가합니다. 이전 기준선은 규칙 점수가 섞인 하이브리드였기 때문에, "RiskManager 제거"가 실제로는 규칙 점수를 뺀 효과를 쟀습니다.
  - 에이전트 수 비교표에는 LLM 에이전트 점수만 씁니다. 하이브리드 변형과 유동성 변형은 따로 표시합니다.
- **계산 규칙**
  - 상위 N개 경계에서 점수가 같은 후보들은 남은 자리를 나눠 가진 것으로 계산합니다. 규칙 점수가 동점 순위를 정하지 않게 하기 위해서입니다.
  - 보관본과 원본이 겹치는 실행은 한 번만 셉니다. 거래비용 0 bps 설정도 그대로 반영합니다.
- **보고서**
  - 결론, 데이터 범위, 다음 실험 방향 문장은 계산된 표에서만 만듭니다.
  - 입력이 없으면 종료 코드 2, 비교 가능한 실행이 없으면 3을 반환합니다.

## 3. 새 설정·명령·API

### 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `HQA_AI_CORS_ORIGINS` | 비어 있음(CORS 꺼짐) | AI 서버를 브라우저에서 직접 부를 때만 origin을 쉼표로 지정 |
| `HQA_MAX_BACKTEST_RESULT_BYTES` | 16777216(최소 1024) | `POST /backtest/results` 본문 상한 |
| `HQA_GENERATION_KEEP` | 8(0이면 정리 안 함) | 보존할 최신 generation 수 |
| `HQA_GENERATION_MIN_AGE_HOURS` | 24 | 교체된 generation의 최소 보존 시간 |
| `HQA_LLM_<ROLE>_TIMEOUT_SECONDS` | RiskManager·thinking 180, 나머지 역할은 `HQA_LLM_TIMEOUT_SECONDS` 또는 45 | 역할별 LLM 호출 제한 시간(초) |
| `OLLAMA_NUM_CTX` | Ollama 기본값 | 로컬 Ollama 컨텍스트 창(2048 이상 정수) |
| `AGENT_CACHE_LEGACY_KEYS` | 꺼짐 | 백테스트에서 v4 이전 캐시로 과거 실행을 재현할 때만 사용 |

### 명령

```bash
venv/bin/python -m scripts.llm_budget status
venv/bin/python -m scripts.llm_budget settle <request_id> --input-tokens N --output-tokens M
venv/bin/python -m scripts.llm_budget release <request_id>
venv/bin/python -m scripts.llm_budget acknowledge-overrun <request_id> --note "수정 내용"
venv/bin/python -m scripts.data.discover --as-targets
venv/bin/python scripts/research/build_agent_architecture_validation.py --source-root <원본 위치> --output-dir <출력 위치>
```

- `settle`은 공급자가 보고한 사용량으로 `sent`·`unknown` 요청을 정산합니다. 사용량 0이면 과금되지 않은 요청으로 해제됩니다.
- `release`는 전송 전에 멈춘 `reserved` 예약만 해제합니다. 기본으로 600초(`--min-age-seconds`) 넘게 지난 예약만 대상입니다.
- `acknowledge-overrun`은 예약보다 비용이 컸던 요청의 검토를 기록하고 차단을 풉니다.
- 원장은 절대 삭제하거나 초기화하지 않습니다.
- `discover`는 기본으로 `data/theme_catalog/`에 저장합니다. `--as-targets`를 줄 때만 라이브 분석 대상 목록을 교체합니다.

### API

| 엔드포인트 | 변경 |
|---|---|
| `GET /internal/status` | 신규. 내부 토큰 필요 |
| `POST/GET /backtest/results` | 내부 토큰 필요, 16 MiB 상한, 작업 ID 검증 |
| `GET /health` | `calendar_warnings` 추가 |
| `GET /stocks/{code}/news`, `GET /stocks/{code}/disclosures` | 6자리 숫자 코드만 허용(아니면 400), 중복 제거 후 요청 개수를 채움 |

## 4. 호환성 주의

- **캐시 재계산:** 런타임 `PROMPT_VERSION`(9월 `hqa-fixed-dag-v7-role-contract`, 10월 `hqa-fixed-dag-v8-backend-plan-rules`)과 백테스트 프롬프트 버전(v3·v4)이 바뀌어 기존 LLM 캐시는 다시 계산됩니다. `CALENDAR_VERSION`도 바뀌었습니다.
- **연구 결과:** `execution.model_version`이 없는 저장 결과는 연구 실행기가 다시 실행합니다. 같은 출력 위치를 써도 이전 수치가 섞이지 않습니다.
- **gap 형식:** 증거 오류 gap은 `invalid_evidence:<사유>:count=N:first=<문서ID>` 형식입니다.
- **예산 원장:** 스키마는 제자리에서 이전되고 기존 기록은 지워지지 않습니다. 미정산 개수에는 `reserved` 요청도 포함됩니다.
- **백엔드 연동:** AI 서버와 백엔드 사이의 API 계약은 그대로입니다. 백테스트 결과를 전송할 때만 내부 토큰이 필요해졌습니다.

## 5. 검증 결과

| 확인 | 결과 |
|---|---|
| 오프라인 테스트 | 1,247개 통과, 3개 건너뜀. 30개 커밋 각각 통과 |
| 실제 수집(DART + 네이버, 2차전지 3종목) | 첫 수집 35초(문서 66건, 가격 1,263행), 증분 9초. 수집·빌드 모두 `done` |
| Analyst 입력 크기(실데이터) | 15.1k–15.9k → 8.6k–9.2k 토큰 |
| 종목 미리보기(Ollama `qwen3:14b`, 백엔드 설정 없음) | 505초. 3개 역할 모두 역할·종목·인용 정상, 오류 0 |
| `/internal/status` | 토큰이 없으면 401, 원장 파일을 만들지 않음 |
| 시그널 모니터(백엔드 50초 중단) | 종료되지 않고 실패 3건을 기록 |
| 실데이터 백테스트(FDR 3종목, 주간 리밸런싱 66회) | 총수익률 21.3% → 13.3%, 벤치마크 40.63% → 37.19% |
| 연구 ablation 재계산(저장 실행 20개, 읽기 전용) | 13개 포함, 결합 1,400/2,180행, 모호한 결합 0 |

에이전트를 하나씩 뺐을 때의 평균 초과수익 차이입니다. 양수는 그 역할을 빼면 성과가 나빠진다는 뜻, 즉 도움이 된다는 뜻입니다.

| 구간 | Analyst | Quant | Chartist | RiskManager |
|---|---:|---:|---:|---:|
| 단타 | +17.44 | +5.30 | +2.36 | 0.00 |
| 장타 | −4.10 | −5.90 | −1.12 | 0.00 |

주의: 이 저장 실행들은 모두 수정 전 v3 버전(당일 문서 포함, 설정 없는 캐시 키)으로 만든 것입니다. 논문 근거로 쓰기 전에 수정된 엔진으로 다시 돌려야 합니다. 동점 처리 편향만 고쳐도 Chartist의 부호가 바뀌었습니다(단타 −0.09 → +2.36, 장타 +0.31 → −1.12).

## 6. 남은 과제

1. **2026-11-19 수능일:** KRX 공지가 나오면 `SPECIAL_CLOSES`에 개장·폐장 시각을 추가해야 합니다. 추가하지 않으면 그날 분석이 멈추고(fail closed), 10월 29일부터 `/health`에 경고가 뜹니다.
2. **연구 수치 재실행:** 수정된 엔진과 v4 프롬프트로 다시 돌려야 하며, LLM 호출이 필요합니다.
3. **실제로 돌려 보지 못한 부분:** 로컬에 KRX·OpenAI·KIS 키가 없어 KRX 가격 수집, 시장 맥락, 실제 백엔드·KIS 연동은 아직 돌려 보지 못했습니다. 10월에 계좌·RiskManager·게시 사이클은 가짜 백엔드로 리허설했습니다([7.5](#75-검증)).
4. **영숫자 종목코드:** 수집, 분석 계약, 백엔드 관심종목 검증(`^\d{6}$`) 어디에서도 아직 지원하지 않습니다.
5. **백엔드(Java) 작업:** PAPER 평가에 필요한 기준선 실행, 수수료, 섹터 데이터, 일별 자산 스냅샷이 없습니다. Docker Compose는 AI 컨테이너에 `.env` 전체를 넘깁니다.
6. **범위 제한:** 재무는 연간 보고서만 씁니다. 수집 기간이 전날에 끝나도록 설계돼 있어, 당일 뉴스는 다음 날 수집 뒤에 반영됩니다.

## 7. 10월 PAPER 준비 작업 (2026-10-09)

모의투자(PAPER)를 돌리기 전에, 분석 → 계획 게시 → 모니터 → 백엔드 주문으로 이어지는 루프를 언어 경계를 넘어 다시 점검했습니다. 기준은 "실행 중 한 곳의 문제가 다른 계좌·종목의 보호(손절·청산)를 막지 않는가"와 "백엔드가 거부할 계획을 만들지 않는가"입니다. 운영 순서와 점검표는 [PAPER 사전 점검표](paper-preflight-checklist.md)에 따로 정리했습니다.

### 7.1 백엔드 주문 수명주기 (`259a473`, `337c86c`, `f4bd145`)

| 항목 | 이전 | 이후 |
|---|---|---|
| 조건이 계속 참인 손절 트리거 | 매 폴링마다 다시 보내면 자신이 낸 매도 주문을 취소 | 진행 중인 매도는 유지하고 중복으로 응답, 진입 매수만 취소 |
| KIS 초당 한도(EGW00201)·큐 초과 | 결과 불명(UNKNOWN)으로 기록되어 운영자 개입 전까지 손절이 막힘 | 거부(REJECTED)로 기록, 일시적 거부는 진입·부분매도 그룹을 소모하지 않음 |
| 부분매도(REDUCE) | 15분마다 계획이 새 버전으로 오면 50% → 25% → 12.5%… 반복 매도 | 같은 내용의 그룹은 버전이 바뀌어도 포지션당 한 번 |
| 보유 포지션의 예정 청산 시각 | 계획이 갱신될 때마다 뒤로 밀림 | 더 늦어지지 않음 |
| 자동매매 끄기 | 손절·청산까지 거부 | 신규 진입만 막음 |
| 주문 가능 시간 | 평일 09:00–15:30 고정, 공휴일도 장중으로 판단 | `HQA_KRX_CLOSED_DATES`(기본값: Python 캘린더의 휴장일)와 `HQA_KRX_SPECIAL_SESSIONS` 반영 |
| 브로커 주문번호 없는 UNKNOWN 주문 | DB를 직접 고치기 전까지 그 계획의 모든 트리거가 보류 | `scripts/paper_orders.py`로 KIS 주문내역과 대조해 연결하거나 미접수 기록 |

- 저장된 계획 하나가 깨져 있어도 `/signals/active` 전체가 400이 되지 않습니다(`INVALID_STORED_CONDITIONS`로 표시).
- 모니터용 활성 계획 응답에 계획별 미해결 주문(`unresolvedOrders`)이 들어갑니다.
- 모든 KIS 호출에 제한 시간이 있고, 토큰 발급은 사용자 전체가 아니라 자격증명별로 직렬화됩니다. 시세 캐시는 10초입니다.

### 7.2 분석과 계획 게시 (`afc0edf`, `b90ef92`)

| 항목 | 이전 | 이후 |
|---|---|---|
| RiskManager 계획 하나가 불량 | 계좌 전체 실패(보유 종목 보호 갱신도 안 나감) | 그 계획만 사유와 함께 `rejected_plans`로, 나머지는 게시 |
| 계획 규칙 위반(손절<진입<목표, 불리한 방향 무효화 등) | JSON 스키마로 표현할 수 없어 strict 디코딩으로도 막히지 않고 계좌 전체가 파싱 실패 | 파싱 단계에서 그 계획만 따로 빼고 사유 기록(모델이 보는 스키마는 그대로) |
| 시세 하나 실패 | 계좌 전체 실패 | 신규 후보는 `omitted_candidates`로 제외, 보유 종목은 계좌 스냅샷의 KIS 가격으로 보호 계획 유지(이 가격으로는 매수 불가) |
| 분석할 수 없는 보유 종목(영숫자 코드 등) | 계좌 스냅샷 전체 실패 | `unsupportedHoldings`로 따로 보고 |
| 백엔드가 거부할 계획 | 요청 전체가 거부됨 | 15분 넘는 진입·만료된 진입은 사유와 함께 건너뜀, 손절이 낮아지는 보유 계획은 기존 손절 유지, SELL은 다음 점검에서 청산 |
| 진입 불가 계좌(진입 자격 없음·모니터 용량 초과) | 신규 후보 5개도 시세 조회·RiskManager 검토 | 보유 종목만 검토(비용·KIS 호출 절약) |

- 계획 규칙: 조건 그룹 ID는 ASCII(`planned-exit`는 예약어), 무효화 조건은 불리한 움직임(`<`, `<=`)만, `pnl_rate`는 보유 포지션에만, BUY 진입 가격 조건은 진입가의 3% 이내(백엔드 가격 이탈 한도). 프롬프트 버전은 `hqa-fixed-dag-v8-backend-plan-rules`입니다.
- 게시 실패는 백엔드의 상태 코드와 본문으로 기록하고, 제한 시간 초과는 한 번 재시도합니다(그래도 실패하면 `outcome_unknown_after_retry`). 스케줄러 요약에 실패 사유와 건너뛴 계획이 남습니다.

### 7.3 시그널 모니터 (`9237f07`)

- **세션:** 검증된 KRX 장중에만 진입을 보냅니다. 시간이 검증되지 않은 평일(공지 전인 2026-11-19 수능일 등)에는 09:00–16:30 동안 보호 트리거만 보내고, 장 밖에서는 백엔드·KIS를 호출하지 않습니다.
- **순서:** 보호 트리거(손절·청산·부분매도·무효화)는 평가 즉시 보내고, 진입은 폴링 끝에 보냅니다.
- **체결 직후:** 진입이 체결됐지만 백엔드가 아직 기록하지 않은 계획(WAITING_ENTRY + 보유 수량)은 손절을 평가하고 두 번째 진입은 보내지 않습니다.
- **반복 억제:** 같은 계획 상태로는 극복할 수 없는 거부(소모된 진입, 오래된 버전 등)는 계획이 바뀔 때까지 다시 보내지 않고, 접수된 트리거는 60초 동안 쉽니다. 진입 거부는 매매 판단으로 `rejections`에만, 매도 거부는 오류로 기록합니다.
- **보호 판정:** 보유 종목은 백엔드가 실제로 매도할 수 있는 OPEN 계획이 있어야 보호된 것으로 봅니다. 아니면 `reason`과 함께 `missing_protection`으로 보고합니다(`no_active_plan`, `entry_fill_unrecorded`(60초 유예), `plan_conditions_unreadable`, `protection_blocked:<사유>`, `plan_without_exit`, `partially_managed:<관리>/<보유>`).
- 용량 초과는 모든 보유 종목을 계속 시세 조회하면서 보고하고, HTTP 오류에는 백엔드 설명이 남고, 감사 기록 실패가 완료된 폴링을 실패로 바꾸지 않습니다.

### 7.4 수집 (`22db295`, `78ca2c5`, `765db51`)

- `scripts.data.loop`는 `--themes`가 없으면 저장된 `raw/theme_targets/<key>.jsonl` 전부를 각자의 키로 수집합니다. 이전 기본값(영문 키워드)은 저장된 목록과 맞지 않아 아무것도 수집하지 못했습니다.
- `--market-context`를 주면 KST 08:00 이후 하루 한 번 KOSPI·KOSDAQ 지수(최근 10일)를 갱신합니다. `KRX_OPEN_API_KEY`가 필요합니다.
- DART 기업코드 갱신 실패 시 원인을 키를 가린 채 기록합니다(10-09에 OpenDART가 `status=800` 점검 중이었음).

### 7.5 검증

| 확인 | 결과 |
|---|---|
| Python 테스트 | 1,317개 통과, 5개 건너뜀(실제 KIS 모의투자 테스트 3개는 `RUN_KIS_LIVE_TESTS=1`일 때만, 토크나이저 테스트 2개는 로컬 tiktoken 캐시가 있을 때만 실행). 커밋 각각 통과 |
| 백엔드 `mvn test`(JDK 17) | 117개 실행, 실패 0, 건너뜀 2(PostgreSQL 연결 테스트) |
| 가짜 백엔드 리허설(실데이터, 2계좌, Ollama `qwen3:14b`) | 9개 전문가 결과 정상. 로컬 RiskManager는 16K 컨텍스트에서 출력이 반복·잘려 두 계좌 모두 실패로 닫힘(주문 0건, 의도대로). 32K에서는 보유 종목 계획은 맞았으나 예정 청산 시각 규칙 위반 → 이 결과로 계획별 분리 파싱을 추가 |
| 결정적 RiskManager로 재생(새 코드, 같은 실데이터) | 2계좌 처리, 계획 3건 게시·실패 0. 진입 밴드 5%로 만든 불량 BUY는 두 계좌 모두 사유와 함께 제외. 게시된 3건 모두 백엔드 저장 규칙(`PaperTradeStore.save`·`TradeConditions.validate`)을 옮긴 검사기 통과 |
| `signal_monitor --once`(가짜 백엔드, 한글날) | 세션 `closed`, 계획 없는 보유 종목을 `missing_protection`/`no_active_plan`으로 보고 |

### 7.6 남은 과제(10월 기준)

1. **2026-11-19 수능일:** KRX 공지가 나오면 Python `SPECIAL_CLOSES`와 백엔드 `HQA_KRX_SPECIAL_SESSIONS` 둘 다에 넣어야 합니다. 10월 29일부터 경고가 뜹니다.
2. **실제 연동 미확인:** KIS 모의투자 주문·체결·취소, OpenAI `gpt-5.6-luna`, KRX Open API는 키가 없어 아직 실제로 돌려 보지 못했습니다. PAPER 시작 전 점검표의 연동 확인 단계를 먼저 거쳐야 합니다.
3. **보호 매도 가격:** 백엔드는 현재가 지정가로 매도합니다. 급락 중에는 체결되지 않고 2분 뒤 취소·재주문될 수 있어, 관찰 기간에 체결률을 확인해야 합니다.
4. **KIS 호출 예산:** 계정당 초당 1회 중 분석 사이클(보유+신규 최대 15종목 시세)과 모니터(20초마다 보유·계획 시세)가 겹치면 큐(20초) 초과로 일부 시세가 실패할 수 있습니다. 보유 10종목 가까이에서 관찰이 필요합니다.
5. 9월 남은 과제의 연구 재실행, 영숫자 종목코드, PAPER 기준선(백엔드) 항목은 그대로입니다.

### 7.7 전체 점검 (2026-10-09 저녁)

구성 전체를 다시 확인하고, 수집과 AI 경로를 실제로 돌려 본 결과입니다.

**점검한 것과 결과**

| 확인 | 결과 |
|---|---|
| 모듈 import(깨끗한 환경, 123개) | 전부 성공, 데이터 폴더에 쓰기 없음 |
| 환경 변수 대조(코드가 읽는 값 vs `.env.example`) | 백엔드 캘린더 변수 2개 누락 → 추가. 나머지는 백엔드 DB 설정·백테스트 전용 |
| 운영 모델 설정(OpenAI 경로, 오프라인 생성) | 전문가 low·1,200토큰·45초, RiskManager medium·12,000토큰·180초, 재시도 0. 문서와 일치 |
| Docker Compose 구성 | 경고 없이 해석됨 |
| 프론트엔드 `next build` | 성공(lint·타입 검사 포함, 경고 없음) |
| 실제 수집(DART + 네이버, 증분) | 27초, `done`, 문서 80건·레코드 262건. 원천 파일 중복 0, 증분 반복 시 행 수 불변 |
| 분석 데이터 적재 | 3종목 모두 300봉(10-08 종가), 증거 8건, 재무 `ready`, 오류 0 |
| 수집 루프 1회(저장된 테마) | 동작. 단, 출력이 파일로 갈 때 버퍼에 갇히는 문제 발견 → 수정 |
| AI 서버(현재 코드) | `/health` 정상, 인증 401·403·404 정상 |
| AI 서버 실제 사이클(Ollama `qwen3:14b`, 가짜 백엔드, 2계좌) | 64분. 전문가 9건 정상. RM 응답 2건 모두 파싱 성공. 로컬 모델의 계획 6건은 계약 위반(예정 청산 = 진입 만료, 가격 0)으로 하나씩 사유와 함께 분리되고, 두 계좌 모두 `completed`(이전에는 계좌 전체 실패). 게시·주문 0건 |
| 종목 미리보기(사이클 직후) | 1초. 사이클의 전문가 결과 캐시 재사용 |
| RM 입력 맞춤(실제 데이터 행, 한도 20,000으로 강제) | 두 계좌 모두 점수가 가장 낮은 신규 후보 1건이 `risk_manager_input_budget`로 빠지고, 보유 종목은 남아 게시까지 정상 |
| 모니터 HTTP 시나리오(가짜 백엔드) | 진입 → 손절 우선 → 무효화 거부(오류) → 60초 휴지 → 소모된 진입 억제, 모두 설계대로 |
| 장후 평가 도구 | 새 모니터 보고 형식을 정상 집계 |
| 재수집 뒤 전문가 입력 | 9개 입력 해시가 모두 동일 → 장중 수집 루프가 돌아도 역할 캐시 재사용(비용 반복 없음) |
| 운영 모델 요청 왕복(가짜 HTTP 전송) | 실제 `AccountDecision`·`SpecialistResult` 스키마가 strict 변환과 래퍼 검사를 통과하고, 규칙 위반 계획은 파싱 단계에서 분리 |
| 로컬 산출물의 비밀값 | 로그·감사 DB·리포트 188개 파일에서 내부 토큰·DART 키 0건 |
| 실제 토크나이저(o200k) 입력 크기 | Chartist 최대 입력이 12,000 한도 안. RM 행은 실제 4,356~5,092토큰이고 오프라인 추정치는 실제의 1.25~1.36배(과소추정 없음). 설계 최대 계좌(15행, 최악 크기 행)는 실제 58,103토큰 < 128,000 |

**발견해서 고친 것**

- **RiskManager 입력 한도 (`d74d1ac`):** 운영 모델은 호출 전에 입력 토큰을 세고 한도를 넘으면 거부합니다. RiskManager 입력은 한도에 맞추지 않았고, 32,000토큰 한도에는 약 6행만 들어갔습니다. 보유 2종목 이상에 신규 5종목이면 매 사이클 같은 이유로 실패할 수 있었습니다(로컬 Ollama에는 이 관문이 없어 리허설에서 드러나지 않음). 기본 한도를 128,000토큰으로 올리고(공식 컨텍스트 창 1,050,000토큰, 비용은 실제 입력에 비례), 넘치면 점수가 낮은 신규 후보부터 빼고(`risk_manager_input_budget`) 보유 종목은 이벤트만 줄이도록 했습니다.
- **백테스트의 겹친 보유 (`417316a`):** 리밸런싱 간격보다 보유 기간이 길면(주간+20일, 연구용 월간+60일) 동시에 열린 여러 코호트를 모두 원금에 복리로 곱했습니다. 같은 데이터의 주간·20일 실행이 +71.6%, MDD −91.6%로 나왔고, 수정 후 +16.3%, −46.0%입니다. 겹치지 않는 설정(주간+5일, 월간+20일)은 결과가 같습니다. 실행 모델 버전을 바꿔 저장된 연구 결과는 재사용되지 않습니다. **장기(월간·60일) 연구 수치는 이 수정 뒤에 다시 돌려야 합니다.**
- **백테스트 리밸런싱일 (`7d827f3`):** 보유 기간만큼 잘라 낸 달력에서 주·월 마지막 날을 골라, 잘린 경계(예: 9월 7일)가 월말로 뽑히며 겹치는 코호트가 하나 더 생겼습니다(주간·5일 실행에서 총수익 21.4% → 12.1%). 실제 기간 말일 가운데 보유 기간만큼 데이터가 남은 날만 씁니다.
- **수집 루프 로그 (`7d2c0f9`):** 호스트에서 `nohup ... > log`로 띄우면 진행 메시지가 버퍼에 남아 몇 시간씩 보이지 않았습니다. 줄 단위 버퍼링으로 바꿨습니다(Docker는 원래 괜찮음).
- **분리된 계획의 사유 (`f5a0e12`):** 실제 사이클에서 `Input should be greater than 0`이 필드 이름 없이 반복돼 원인을 알 수 없었습니다. 이제 `entry_price: Input should be greater than 0`처럼 필드 위치를 붙입니다.
- **기타 (`ac8d31c`, `bc4cc0a`, `b8ebffc`):** 원격 스케줄러 토큰의 공백 제거, `.env.example`에 백엔드 캘린더·RM 입력 한도 변수 추가, 겹친 사이클 요청이 하나로 합쳐지는지 지키는 테스트, 사이클 요약에 거부된 계획과 검토하지 못한 후보 표시, 운영 스키마 왕복 테스트.

**바꾸지 않은 것**

- 백테스트 진입은 기준일 종가이고 신호도 같은 종가로 계산합니다(README에 문서화된 방법론). 같은 봉 체결 가정이라 다음 날 시가 진입보다 낙관적일 수 있어, 연구 재실행 때 시가 진입과 비교해 보기를 권합니다.

**확인하지 못한 것**

- 백엔드 jar 패키징: 받아 두지 않은 Maven 플러그인이 필요해 오프라인으로는 불가(컴파일·테스트는 통과, jar는 Docker 빌드에서 생성).
- 실제 KIS·OpenAI·KRX 연동.

## 부록: 커밋 목록

| 커밋 | 날짜 | 구분 | 제목 |
|---|---|---|---|
| `498e6e3` | 2026-09-21 | 이식 | Port agent-profile backtest options and CSV artifacts from ai-data-main |
| `219354d` | 2026-09-21 | 이식 | Bound Naver news search requests by date range |
| `63567b1` | 2026-09-21 | 이식 | Move agent-architecture research scripts under scripts/research |
| `54e581e` | 2026-09-21 | 이식 | docs: keep agent-architecture experiment outputs outside the repository |
| `2088844` | 2026-09-28 | 수정 | fix(calendar): block only unverified KRX sessions instead of every date after 2026-11-01 |
| `ea94f33` | 2026-09-28 | 수정 | fix(runtime): isolate broken themes and keep monitor and scheduler loops alive |
| `32d0764` | 2026-09-28 | 수정 | fix(index): index only text document sources |
| `892e0f6` | 2026-09-28 | 수정 | fix(collect): report provider no-data, fail on Naver block pages, keep corp codes usable |
| `4a1b723` | 2026-09-28 | 수정 | fix(collect): detect rate limits from structured status codes only |
| `152f7b8` | 2026-09-28 | 수정 | fix(ai-server): protect backtest results and turn off wildcard CORS |
| `5243eb3` | 2026-09-28 | 수정 | fix(backtest): stop caching multi-agent fallbacks and never downgrade cache-only misses |
| `ef8ae99` | 2026-09-28 | 수정 | fix(analysis): keep specialist inputs within the role token limit |
| `aca1ab2` | 2026-09-28 | 수정 | fix(analysis): make specialist requests answerable and correctly cited |
| `189f3c2` | 2026-09-28 | 수정 | feat(dev): make the local Ollama context window configurable |
| `6224bc2` | 2026-09-28 | 수정 | fix(runtime): prioritize scheduled cycles, skip holidays, and free previews from backend config |
| `3d0b77b` | 2026-09-28 | 수정 | fix(ai-server): serve stock news and disclosures from a bounded index off the event loop |
| `28141dc` | 2026-09-28 | 수정 | fix(backtest): remove same-day lookahead and survivorship, fill halts and gaps realistically |
| `3000f07` | 2026-09-28 | 수정 | fix(safety): refuse REAL KIS orders in the Python runtime |
| `be65e86` | 2026-09-28 | 수정 | feat(ops): reconcile the LLM budget ledger and expose an operator status view |
| `870f529` | 2026-09-28 | 수정 | fix(collect): keep discovery out of the live universe and respect KRX publication time |
| `7acd37f` | 2026-09-28 | 수정 | fix(index): prune old evidence generations after each publish |
| `01193fa` | 2026-09-28 | 수정 | fix(research): make the agent-ablation evidence reflect the data it aggregates |
| `a0fd2b6` | 2026-09-28 | 수정 | fix(collect): pause the loop only for rate limits that failed the run |
| `a295896` | 2026-09-28 | 수정 | fix(collect): keep no-data notes in incremental runs and gate 08:00 on real sessions |
| `1c116ba` | 2026-09-28 | 수정 | fix(index): keep replaced generations for the minimum age and check retention first |
| `0fff486` | 2026-09-28 | 수정 | fix(runtime): keep holdings at runtime priority and serve feeds on their own threads |
| `752daf5` | 2026-09-28 | 수정 | fix(analysis): bound evidence gaps and fit the Analyst with the notes it will carry |
| `4a97669` | 2026-09-28 | 수정 | fix(runtime): follow CSAT session hours and name the failed theme in previews |
| `c40d259` | 2026-09-28 | 수정 | feat(ops): list and release budget reservations that were never sent |
| `746698e` | 2026-09-28 | 수정 | docs(env): list the AI server CORS, result-size and generation retention settings |
| `7ad3bd7` | 2026-09-29 | 수정 | fix(backtest): keep halted sessions, fill gap-up targets, and stop two ranking leaks |
| `4b485bf` | 2026-09-29 | 수정 | fix(analysis): treat halted sessions as no-trade bars instead of voiding the history |
| `162b4db` | 2026-09-29 | 수정 | fix(research): never reuse pre-fix results or partial agent answers as current evidence |
| `8ef0e83` | 2026-09-29 | 수정 | fix(research): keep rule factors out of the agent ablation and derive every claim |
| `22db295` | 2026-10-09 | PAPER 준비 | fix(collect): log why a corp-code refresh failed, with the key redacted |
| `78ca2c5` | 2026-10-09 | PAPER 준비 | fix(collect): make the collection loop maintain the saved theme universe |
| `765db51` | 2026-10-09 | PAPER 준비 | feat(collect): let the collection loop refresh market indices once a day |
| `259a473` | 2026-10-09 | PAPER 준비 | fix(paper): stop the order lifecycle from cancelling, freezing or repeating protection |
| `afc0edf` | 2026-10-09 | PAPER 준비 | fix(signals): publish only plans the backend can accept, and say why others were skipped |
| `b90ef92` | 2026-10-09 | PAPER 준비 | fix(analysis): one bad plan, quote or holding no longer fails the whole account |
| `9237f07` | 2026-10-09 | PAPER 준비 | fix(monitor): protect first, follow the KRX session and report protection that cannot sell |
| `337c86c` | 2026-10-09 | PAPER 준비 | fix(backend): default the KRX closed dates to the Python calendar's |
| `f4bd145` | 2026-10-09 | PAPER 준비 | feat(paper): let an operator resolve UNKNOWN orders, verified against KIS |
| `7d2c0f9` | 2026-10-09 | 전체 점검 | fix(collect): line-buffer the collection loop so a redirected log shows progress |
| `ac8d31c` | 2026-10-09 | 전체 점검 | fix(runtime): strip the remote scheduler token and document the backend calendar settings |
| `d74d1ac` | 2026-10-09 | 전체 점검 | fix(analysis): fit the RiskManager call to its input limit, sized for the designed account |
| `bc4cc0a` | 2026-10-09 | 전체 점검 | feat(runtime): name refused plans and unreviewed stocks in the cycle summary |
| `417316a` | 2026-10-09 | 전체 점검 | fix(backtest): stop compounding overlapping holdings on the full capital |
| `b8ebffc` | 2026-10-09 | 전체 점검 | test(llm): round-trip the real decision and specialist schemas through the Responses API |
| `7d827f3` | 2026-10-09 | 전체 점검 | fix(backtest): rebalance on real week and month ends, never on the data cut-off |
| `5284348` | 2026-10-09 | 전체 점검 | docs(backtest): record the capital-sleeve and period-end rules and the close-entry assumption |
| `f5a0e12` | 2026-10-09 | 전체 점검 | fix(analysis): name the fields a set-aside RiskManager plan broke |
