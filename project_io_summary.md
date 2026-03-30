# HQA (Hegemony Quantitative Analyst) 프로젝트 I/O 명세서

현재 프로젝트의 멀티 에이전트 워크플로우에 기반한 입력 및 출력 명세서입니다.

## 1. 시스템 입력 (Inputs)

### 1.1 사용자 및 파일 설정 입력
- **종목 정보**: 종목명 (예: "삼성전자") 또는 종목코드 (예: "005930")
- **자연어 쿼리 (Interactive Mode)**: 사용자의 분석/질문 요청 (예: "반도체 산업 동향 분석해줘")
- **실행 모드 (Mode)**:
  - `Interactive`: 자연어 대화형 분석
  - `Full`: 전체 심층 분석
  - `Quick`: 재무(Quant) + 차트(Chart)만 빠르게 분석
  - `Realtime`: 단순 현재 실시간 시세 조회
  - `Autonomous`: 감시 목록(Watchlist) 기반 자율 주행 모드
- **루프 및 스케줄 설정**: `config/watchlist.yaml` 파일 (감시 종목 리스트, 매매 조건, 주기, 시뮬레이션 여부 등 지정)

### 1.2 외부 연동 데이터 (환경변수 / Tool Inputs)
- **API Keys**: `GOOGLE_API_KEY` (에이전트 LLM), `KIS_APP_KEY` 및 `KIS_APP_SECRET` (실시간 주가/매매), `DART_API_KEY` (공시 조회)

---

## 2. 개별 에이전트 입출력 및 상호작용 (Agent Nodes I/O)

### 2.1 Supervisor Agent
- **입력 (Input)**: 사용자 자연어 쿼리
- **출력 (Output)**: 쿼리 의도 분석 및 그에 맞는 작업 실행 라우팅 (대화, 분석, 검색 등)

### 2.2 Analyst Agent (Researcher + Strategist)
- **입력**: 분석할 종목명 및 종목코드
- **동작**: 리서치 도구들을 활용해 데이터를 수집하고(Researcher), 자체 품질 검증(Quality Gate) 후 불합격(D등급) 시 재검색 루프 진행. 이후 분석 내용 전략화(Strategist).
- **출력 (`AnalystScore`)**: 
  - 경제적 해자(Moat) 점수, 성장성 점수
  - 총합 점수 (70점 만점) 및 헤게모니 등급 (A~D)
  - 경쟁 우위, 위험 요소 분석 텍스트 및 요약

### 2.3 Quant Agent
- **입력**: 분석할 종목명 및 종목코드
- **출력 (`QuantScore`)**:
  - 가치평가(Valuation), 수익성, 성장성, 안정성 점수
  - 총합 재무 점수 (100점 만점) 및 재무 등급
  - 종합 재무 소견(Opinion)

### 2.4 Chartist Agent
- **입력**: 분석할 종목명 및 종목코드
- **출력 (`ChartistScore`)**:
  - 트렌드, 모멘텀, 변동성, 거래량 점수
  - 총합 기술적 점수 (100점 만점) 및 매매 신호 (Signal)

### 2.5 Risk Manager Agent
- **입력**: Analyst, Quant, Chartist에서 올라온 세 가지 평가 점수 (`AgentScores`) 및 분석 내용
- **출력 (`FinalDecision`)**:
  - **최종 매매 액션 (Action)**: "적극 매수", "매수", "관망", "매도" 등
  - 총합 종합 점수 (270점 만점)
  - 의사결정 확신도 (Confidence %) 및 리스크 수준 (Risk Level)
  - 최종 투자 리포트 요약 텍스트

---

## 3. 최종 시스템 결과물 (System Outputs)

- **보고서 (Report/CLI)**: 콘솔 및 로그 형태로 출력되는 주식 분석 종합 리포트 및 에이전트 상태 진행 과정.
- **Trace 로그 (Agent Tracer)**: 에이전트들의 추론 내역, 에러 복구 과정, 품질 검증 및 재시도 횟수 등의 투명한 과정 로그.
- **자동 거래 결과 (Trade Executor)**:
  - `Autonomous` 모드 실행 시 매수/매도 액션 만족에 따른 **실전 주문 API 호출**
  - 모의 투자(`dry-run`) 시 **매매 시뮬레이션 로그** (누적 지출, 잔여 예산 계산 결과)
