# HQA_Project
졸업작품: 퀀트멘탈 기반 멀티 에이전트 자동매매 시스템

HQA_Project/
│
├── .env                    # API Key 보관 (절대 깃허브 업로드 금지)
├── .gitignore              # 깃허브 업로드 제외 설정
├── requirements.txt        # 설치된 라이브러리 목록
├── README.md               # 프로젝트 설명서 (졸업작품 개요 작성용)
├── main.py                 # 프로그램 실행 진입점 (Entry Point)
│
├── 📁 data/                # 데이터 저장소
│   ├── 📁 raw/             # 크롤링한 원본 텍스트/PDF 리포트
│   └── 📁 processed/       # 가공된 데이터 (전처리된 CSV 등)
│
├── 📁 database/            # DB 저장소
│   ├── 📁 chroma_db/       # Vector DB (RAG용 임베딩 데이터)
│   └── trade_log.db        # SQLite (매매 기록 및 주가 데이터)
│
├── 📁 src/                 # 핵심 소스 코드 (Source Code)
│   ├── __init__.py         # (빈 파일) 파이썬 패키지 인식용
│   │
│   ├── 📁 agents/          # CrewAI 에이전트 정의
│   │   ├── __init__.py
│   │   ├── analyst.py      # [Analyst] 펀더멘털/헤게모니 분석 에이전트
│   │   ├── chartist.py     # [Chartist] 차트/기술적 분석 에이전트
│   │   └── risk_manager.py # [Risk Manager] 자금 관리 에이전트
│   │
│   ├── 📁 tools/           # 에이전트가 사용할 도구(Tool) 모음
│   │   ├── __init__.py
│   │   ├── search_tools.py # 네이버 뉴스/리포트 검색 도구
│   │   └── chart_tools.py  # TA-Lib 활용 기술적 지표 계산 도구
│   │
│   └── 📁 data_pipeline/   # 데이터 수집 및 처리 로직
│       ├── __init__.py
│       ├── crawler.py      # 네이버 증권 크롤러
│       └── price_loader.py # FinanceDataReader 주가 수집기
│
└── 📁 dashboard/           # 웹 UI (Phase 4 단계)
    └── app.py              # Streamlit 대시보드 실행 파일