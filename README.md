# HQA Project

한국 주식의 공시·뉴스·가격을 모아 AI 에이전트가 종목을 분석하고, 계좌마다 매매 계획을 세워 **모의투자(PAPER)**로 실행하는 프로젝트입니다. 주문 검증·접수·체결 확인은 LLM과 분리된 Spring 백엔드가 맡습니다. 실계좌 자동매매는 허용하지 않습니다.

## 주요 기능

- **데이터 수집:** 테마별 종목의 뉴스, DART 공시, 재무, 일봉(KRX 또는 KIS)을 모으고 시점을 검증해 정제합니다. 테마 종목은 네이버 모바일 테마 API로 찾습니다.
- **AI 분석:** 종목마다 Analyst·Quant·Chartist가 분석하고, 같은 종목의 분석은 사용자끼리 재사용합니다. 계좌마다 RiskManager가 보유 종목, 현금, 위험 한도를 보고 매매 계획을 냅니다. 계획에는 진입 범위, 손절, 목표가, 부분 익절, 예정 청산, 비중이 들어갑니다.
- **주문 실행(백엔드):** 계획을 버전과 함께 저장하고, 조건이 맞으면 KIS 모의투자에 지정가로 주문한 뒤 체결과 취소를 대조합니다.
  - 주문 가격은 KRX 호가 단위에 맞춥니다.
  - 장중 매도는 현재가보다 최대 2% 낮게 내고, 1분 안에 체결되지 않으면 그때 가격으로 다시 냅니다.
  - 장 마감 동시호가 매도는 최대 5% 낮게 냅니다(하한가 아래로는 내지 않음). 마감 뒤에도 남은 주문은 만료로 기록합니다.
  - 응답을 받지 못한 취소는 주문 목록을 확인한 뒤 다시 보냅니다.
  - 손절한 종목은 10거래일 동안 다시 사지 않습니다. 손절가에 팔려도 손실이 자산의 0.5%를 넘지 않도록 매수 수량을 제한합니다.
  - 휴장일과 특별 거래시간(예: 수능일)을 반영합니다.
- **가격 감시(monitor):** 매매 계획의 조건을 20초마다 확인해 백엔드에 주문을 요청하는 별도 프로세스입니다.
- **웹 화면:** 대시보드, 종목 분석과 근거, 매매 계획과 주문 내역, PAPER 계좌 설정, 커뮤니티를 제공합니다.

선택 기능은 환경변수로 켭니다.

| 환경변수 | 효과 |
| --- | --- |
| `HQA_ANALYST_RAG=stock` | Analyst에게 같은 종목의 과거 문서를 근거로 함께 줍니다. 기본값 `off` |
| `HQA_RISK_REVIEW_TRIGGER=change` | 시세 말고 다른 입력이 바뀔 때만 계좌를 다시 검토해 LLM 호출을 줄입니다. 기본값 `cycle` |
| `HQA_SPECIALIST_CACHE_PATH` | 전문가 에이전트의 답을 디스크에 저장해 같은 요청에 다시 씁니다 |

## 동작 흐름

```text
수집:    공시·뉴스·가격 → 시점 검증·수치 계산 → 정제 데이터
AI 서버: 후보 선별 → Analyst / Quant / Chartist 공통 분석 → 계좌별 RiskManager → 매매 계획
백엔드:  PAPER 계좌·위험 한도 검증 → 버전이 있는 계획 저장
monitor: 가격 조건 확인 → 백엔드에 주문 요청 → 체결·취소 대조
```

- 누락된 데이터나 실패한 분석을 중립 점수로 대신하지 않습니다.
- 주문 접수를 체결로 보지 않습니다.
- 종목 분석은 공유하지만 계좌별 판단은 공유하지 않습니다.
- 주문은 백엔드만 냅니다. Python에는 직접 주문 기능이 없습니다.

## 기술 스택

| 영역 | 사용 기술 |
| --- | --- |
| 프런트엔드 | Next.js 15, React 19, TypeScript |
| 백엔드 | Java 17, Spring Boot 3.5, PostgreSQL, Flyway, Redis(선택) |
| AI·데이터 | Python, FastAPI, Claude(Anthropic) 또는 OpenAI 모델 |
| 증권 API | 한국투자증권 KIS Open API(모의투자) |
| 실행 | 로컬 스크립트 또는 Docker Compose |

## 폴더 구조

| 위치 | 역할 |
| --- | --- |
| `frontend/` | Next.js 웹 화면 |
| `backend/` | Spring Boot: 인증, 계좌·시세, 매매 계획과 주문 수명 관리, DB migration |
| `ai_server/` | FastAPI 분석 API |
| `src/ingestion/` | 원천 데이터 수집·검증·관측 이력 |
| `src/runner/` | 공통 분석, 계좌별 판단, 스케줄러, 가격 감시 |
| `src/agents/`, `src/tools/`, `prompts/` | 에이전트, 도구, 프롬프트 |
| `src/evidence/`, `src/retrieval/`, `src/data_pipeline/` | 정제, 인덱스, 검색 |
| `scripts/` | 수집·빌드·운영 명령 |
| `backtesting/` | 과거 전략 검증과 오프라인 PAPER 평가 |
| `research/` | 과거 실험 결과 보관 |
| `config/`, `tests/`, `docs/` | 설정, 테스트, 문서 |
| `data/` | 수집 데이터와 상태. 새 산출물은 Git에 올리지 않습니다 |

## 시작하기

### 1. 설치

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

백엔드는 Java 17과 Maven, 프런트엔드는 Node.js와 npm이 필요합니다.

### 2. 환경변수

[.env.example](.env.example)을 참고해 `.env`에 필요한 항목만 넣습니다. 이미 있는 `.env`는 덮어쓰지 마세요. `.env-ai`가 있으면 Python은 `.env` 대신 그 파일을 읽고, 이미 설정된 환경변수가 우선합니다.

- **수집:** `DART_API_KEY`, `KRX_OPEN_API_KEY`, 각 서비스의 이용 승인. KIS 일봉(`kis_chart`)으로 받을 때는 KIS 모의투자 키도 필요합니다.
- **AI 분석:** `LLM_PROVIDER`에 맞는 키
  - `anthropic`: `ANTHROPIC_API_KEY`
  - `claude_plan`(Claude 구독 한도 사용): `claude setup-token`으로 만든 `CLAUDE_CODE_OAUTH_TOKEN`
  - `openai`: `OPENAI_API_KEY`
  - 연결 점검: `python -m scripts.claude_check` (구독은 `--plan`)
- **모의투자:** 내부 인증 토큰, 백엔드 암호화 키, 백엔드에 등록한 사용자별 KIS 모의투자 계좌

기본 수집에는 LLM 키와 KIS 키가 필요 없습니다. 키, 계좌 DB, 비용 원장은 Git에 올리지 마세요.

### 3. 서비스 실행

```bash
./scripts/dev.sh
```

프런트엔드는 http://localhost:3000, 백엔드는 http://localhost:8000, AI 서버는 http://localhost:8001에서 열립니다. 중지는 `./scripts/kill-dev.sh`입니다.

Docker로 실행하려면 `docker compose up --build`를 씁니다. 자동 분석 스케줄러와 주문 감시(monitor)는 기본으로 꺼져 있고 `paper` 프로필에서만 켜집니다. 계좌·주문·체결 연동을 확인한 뒤에만 켜세요. 운영 설정과 복구 절차는 [PAPER 운영](docs/luna-paper-runtime.md)에 있습니다.

### 4. 데이터 수집

프로젝트 루트에서 실행합니다. 아래 명령은 모델을 호출하거나 주문하지 않습니다.

```bash
python -m scripts.data.collect --theme "2차전지"
python -m scripts.data.build --theme-key "2차전지" --stats
python -m scripts.data.batch --themes "AI:ai,반도체:semiconductor" --dry-run
python -m scripts.data.market_context --help
```

- 기본 수집 대상은 `news,dart,financials,chart`이고, 저장된 종목 목록을 다시 씁니다. 목록을 새로 받으려면 `--refresh-targets`를 붙입니다.
- 날짜를 생략하면 처음에는 전일까지 400일을 받고, 이후에는 늘어난 부분만 받습니다.
- 지수 수집은 별도 명령과 KRX 서비스 승인이 필요합니다. 자세한 내용은 [데이터 정제](docs/data-cleansing.md)와 [지수 수집](docs/market-context-data.md)에 있습니다.

## 백테스팅

```bash
python -m backtesting --help
python -m backtesting run --help
python -m backtesting paper-performance --help
```

과거 백테스트와 모의투자 기록 평가를 한 진입점에서 실행합니다. LLM 옵션이나 `--submit-url`을 쓰면 외부 요청과 비용이 생깁니다. 실행 전에 [백테스팅 안내](backtesting/README.md)를 확인하세요. 과거 실험 결과는 [연구 보관 목록](research/README.md)에 있습니다.

## 테스트

```bash
OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false \
  venv/bin/python -m pytest -q
mvn -f backend/pom.xml test
npm --prefix frontend run build
```

- Python 테스트는 외부 네트워크 접속을 막고 돌립니다. 실제 KIS 연동 테스트는 따로 켜야 합니다.
- 주문 흐름 시뮬레이션은 `HQA_SIM_PYTHON=$PWD/venv/bin/python mvn -f backend/pom.xml test -Dtest=PaperLifecycleSimulationTest`로 돌립니다. 실제 모니터가 급락, 갭, 장 마감 같은 가격 시나리오에서 백엔드 주문 로직과 맞물려 움직이는지 확인합니다.
- 테스트 통과가 투자 성과를 뜻하지는 않습니다.

## 문서

- [문서 목록](docs/README.md)
- [저장소 구조와 명령 안내](docs/repository-layout.md)
- [데이터 정제](docs/data-cleansing.md)
- [공시·뉴스와 주가 반응](docs/event-data-pipeline.md)
- [시장·업종 비교](docs/market-context-data.md)
- [PAPER 운영](docs/luna-paper-runtime.md)
- [PAPER 사전 점검표](docs/paper-preflight-checklist.md)
