# AI·데이터 변경 내역 (feat/dohoon-changes, 2026-09)

`feat/dohoon-changes` 브랜치에서 AI 분석, 데이터 수집, 백테스트를 무엇을, 왜, 어떻게 바꿨는지 정리한 문서입니다. 코드를 열지 않고도 바뀐 동작과 운영 시 주의할 점을 알 수 있게 썼습니다. 각 변경의 자세한 근거는 괄호 안 커밋의 메시지(`git show <커밋>`)에 있습니다.

- **기간:** 2026-09-21 ~ 2026-09-29
- **구성:** `main`에서 분기 → `ai-data-main` 작업 이식 4개 커밋(`498e6e3`~`54e581e`) → 점검·수정 30개 커밋(`2088844`~`8ef0e83`)
- **규모 (수정 30개 커밋):** 64개 파일, +3,142 / −298줄, 그중 테스트 파일 23개. Spring 백엔드와 프론트엔드 코드는 바꾸지 않았습니다.
- **테스트:** 오프라인 1,247개 통과, 3개 건너뜀. 30개 커밋 각각에서 따로 돌려도 통과합니다.
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
  - 런타임 `PROMPT_VERSION`은 `hqa-fixed-dag-v7-role-contract`입니다.
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

- **캐시 재계산:** 런타임 `PROMPT_VERSION`(`hqa-fixed-dag-v7-role-contract`)과 백테스트 프롬프트 버전(v3·v4)이 바뀌어 기존 LLM 캐시는 다시 계산됩니다. `CALENDAR_VERSION`도 바뀌었습니다.
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
3. **실제로 돌려 보지 못한 부분:** 로컬에 KRX·OpenAI·KIS 키가 없어 KRX 가격 수집, 시장 맥락, 백엔드 연동, 계좌·RiskManager 사이클은 오프라인 테스트로만 검증했습니다.
4. **영숫자 종목코드:** 수집, 분석 계약, 백엔드 관심종목 검증(`^\d{6}$`) 어디에서도 아직 지원하지 않습니다.
5. **백엔드(Java) 작업:** PAPER 평가에 필요한 기준선 실행, 수수료, 섹터 데이터, 일별 자산 스냅샷이 없습니다. Docker Compose는 AI 컨테이너에 `.env` 전체를 넘깁니다.
6. **범위 제한:** 재무는 연간 보고서만 씁니다. 수집 기간이 전날에 끝나도록 설계돼 있어, 당일 뉴스는 다음 날 수집 뒤에 반영됩니다.

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
