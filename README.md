# HQA Project

한국 주식의 공시·뉴스·가격을 수집하고, 종목별 공통 멀티에이전트 분석과 계좌별 위험 판단을 결합하는 PAPER 자동매매 프로젝트입니다. 주문 검증·접수·체결 확인은 LLM과 독립된 Spring 백엔드가 담당합니다. 실계좌 자동매매는 허용하지 않습니다.

## 구조

| 위치 | 역할 |
| --- | --- |
| `frontend/` | Next.js 사용자 화면, 분석 조회, PAPER 계좌 설정 |
| `backend/` | Spring 인증·계좌·시세·주문 수명 관리와 DB migration |
| `ai_server/` | FastAPI 분석 API와 단일 AI worker의 Dockerfile |
| `src/ingestion/` | 원천 데이터 수집·검증·관측 이력 저장 |
| `src/evidence/`, `src/retrieval/`, `src/data_pipeline/` | 정제·인덱스와 기존 검색/백테스트 호환 계층 |
| `src/runner/` | 공통 분석, 계좌별 판단, 스케줄러, 독립 가격 감시 |
| `src/agents/`, `src/tools/`, `prompts/` | 에이전트·도구·프롬프트; 기존 대화/연구 경로도 포함 |
| `scripts/data/` | 수집·빌드·배치 실행 명령 |
| `backtesting/` | 과거 전략 검증과 오프라인 PAPER 평가 명령 |
| `research/` | 보존된 과거 실험 결과·데이터 스냅샷·이전 경로 목록 |
| `data/` | 현재 수집 데이터, 캐시, 분석·주문·예산 상태; 새 산출물은 Git 제외 |
| `config/`, `tests/`, `docs/` | 운영 설정, 회귀 테스트, 주제별 문서 |

과거 연구 결과는 현재 Luna의 수익률이나 운영 준비 완료를 입증하지 않습니다. 자세한 경계와 명령 변경 내역은 [저장소 구조](docs/repository-layout.md)를 참고하세요.

## 실행 흐름

```text
수집 컴퓨터: 공시·뉴스·가격 → 시점 검증·수치 계산 → 정제 데이터
AI 서버: 후보 선별 → Analyst / Quant / Chartist 공통 분석 → 계좌별 RiskManager
백엔드: PAPER 계좌·위험 한도 검증 → 버전이 있는 계획 저장
독립 monitor: 가격 조건 확인 → 백엔드 주문 요청 → 체결·취소 조회
```

동일 종목 분석은 사용자 간 재사용하지만 계좌별 판단은 공유하지 않습니다. 누락된 데이터나 실패한 분석을 중립 점수로 대체하지 않으며, 주문 접수를 체결로 간주하지 않습니다.

대시보드에서 선택한 워치리스트 종목은 현재 엔진의 Analyst·Quant·Chartist로 분석합니다. 수동 분석은 계좌별 RiskManager나 주문을 실행하지 않습니다. 분석 제출·조회·이력은 로그인이 필요하며, 완료 결과는 사용자별로 PostgreSQL에 저장합니다. 기존 DB에는 백엔드 시작 시 Flyway V10이 적용됩니다.

## 설치

Python 수집 환경은 Linux 또는 WSL2 기준입니다. 기존 `.env`를 덮어쓰지 말고 [.env.example](.env.example)을 기준으로 필요한 항목만 설정하세요. `.env-ai`가 있으면 Python은 `.env` 대신 해당 파일을 읽으며, 기존 환경변수가 우선합니다.

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

선택적인 종목토론방 수집은 `playwright install chromium`이 필요합니다. 현재 기본 수집 소스에는 종목토론방이 포함되지 않습니다.

- 수집만 실행: `DART_API_KEY`, `KRX_OPEN_API_KEY`와 서비스별 이용 승인.
- LLM 분석: `OPENAI_API_KEY`와 예산·호출 한도 설정.
- PAPER 연동: 내부 인증 토큰, 백엔드 암호화 키, 백엔드에 등록한 사용자별 PAPER 계좌.
- 백엔드: Java 17과 Maven. 프론트엔드: `frontend/package.json` 기준 Node/npm 환경.

수집용 컴퓨터에는 OpenAI·KIS 키가 필요하지 않습니다. 시크릿·계좌 DB·예산 원장은 Git이나 연구 결과 폴더에 저장하지 마세요.

## 데이터 수집

프로젝트 루트에서 실행합니다. 아래 수집/빌드 명령은 모델을 호출하거나 주문하지 않습니다.

```bash
python -m scripts.data.collect --theme "2차전지"
python -m scripts.data.build --theme-key "2차전지" --stats
python -m scripts.data.batch --themes "AI:ai,반도체:semiconductor" --dry-run
python -m scripts.data.market_context --help
```

수집은 `news,dart,financials,chart`가 기본이며, 저장된 종목 목록을 재사용합니다. 목록을 갱신하려면 `--refresh-targets`를 지정합니다. 날짜를 생략하면 한국 기준 전일까지 400일로 시작해 이후 증분 수집하며, 명시한 날짜 구간은 별도 재수집입니다.

지수 수집은 별도 명령과 별도 KRX 서비스 승인이 필요합니다. 수집 주기나 다른 컴퓨터에서 분석 서버로의 자동 전송은 자동 활성화되지 않습니다. [데이터 정제](docs/data-cleansing.md), [지수 수집](docs/market-context-data.md)을 참고하세요.

## 서비스 실행

```bash
./scripts/dev.sh
```

기본 주소는 프론트엔드 `http://localhost:3000`, 백엔드 `http://localhost:8000`, AI 서버 `http://localhost:8001`입니다. 중지는 `./scripts/kill-dev.sh`입니다.

AI 서버만 실행하려면:

```bash
venv/bin/python -m uvicorn ai_server.app:app --host 127.0.0.1 --port 8001 --workers 1
```

`docker compose up --build`는 로컬 서비스 구성이며, 자동 분석 스케줄러와 주문 조건 monitor는 기본적으로 꺼져 있습니다. `paper` 프로필은 계좌·주문·체결 통합 검증 후에만 활성화하세요. 구체적인 설정과 복구 절차는 [Luna PAPER 운영](docs/luna-paper-runtime.md)에 있습니다.

`python main.py`는 도움말만 표시합니다. `--theme-trade`와 `--multi-theme-trade`는 분석 서버에 요청하므로 API 비용이 발생할 수 있습니다. Python CLI의 직접 주문 기능은 없습니다.

## 백테스팅과 평가

하나의 진입점에서 과거 백테스트와 PAPER 평가를 구분합니다.

```bash
python -m backtesting --help
python -m backtesting run --help
python -m backtesting paper-runtime --help
python -m backtesting paper-performance --help
```

PAPER 평가는 제공한 기록을 오프라인으로 읽습니다. 과거 백테스트도 가격은 로컬 파일에서 읽지만, LLM 옵션이나 `--submit-url`을 사용하면 외부 요청이 발생합니다. 실행 전에 [백테스팅 안내](backtesting/README.md)를 확인하세요. 과거 데이터의 시점 필터링과 현재 관측 이력 검증은 동일하지 않습니다.

기존 결과 파일은 [연구 보관 목록](research/README.md)에 보존했습니다. 실제 웹 화면이 사용하는 `frontend/public/backtesting/`는 배포용 사본이며, 원본 실험 경로와 알려진 누락을 별도로 기록합니다.

## 검증

```bash
OPENAI_API_KEY=offline-disabled OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false \
  venv/bin/python -m pytest -q
mvn -f backend/pom.xml test
npm --prefix frontend run build
```

Python 테스트는 기본적으로 외부 네트워크 접속을 차단합니다. 실제 KIS 통합 테스트는 별도 명시적 활성화가 필요하며, 일반 검증과 구분해야 합니다. 오프라인 테스트 통과는 API 비용·속도 목표나 투자 성과 검증을 대신하지 않습니다.

## 문서

- [문서 목록](docs/README.md)
- [구조와 명령 이전 안내](docs/repository-layout.md)
- [데이터 정제](docs/data-cleansing.md)
- [공시·뉴스와 주가 반응](docs/event-data-pipeline.md)
- [시장·업종 비교](docs/market-context-data.md)
- [Luna PAPER 운영](docs/luna-paper-runtime.md)
