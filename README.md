# HQA Project (Hybrid Quantitative Analyst)

**AI 기반 멀티 에이전트 금융 분석 및 자동 매매 시스템**

## 📖 프로젝트 개요
이 프로젝트는 CrewAI를 활용한 다중 에이전트 시스템(Multi-Agent System)을 통해 주식 시장의 펀더멘털 분석, 기술적 분석, 리스크 관리를 수행하고, RAG(검색 증강 생성) 기술을 활용하여 투자 리포트를 자동으로 생성/분석하는 졸업작품 프로젝트입니다.

## 🛠 기술 스택 (Tech Stack)
- **Language**: Python 3.9+
- **AI Framework**: CrewAI, LangChain
- **Database**: ChromaDB (Vector), SQLite (RDB)
- **Data Source**: FinanceDataReader, Naver Finance Crawler
- **Dashboard**: Streamlit (예정)

## 📂 디렉토리 구조 (Directory Structure)

```text
HQA_Project/
│
├── .env                    # API Key 보관 (절대 깃허브 업로드 금지)
├── .gitignore              # 깃허브 업로드 제외 설정
├── requirements.txt        # 의존성 패키지 목록
├── README.md               # 프로젝트 설명서
├── main.py                 # 메인 실행 파일
│
├── 📁 data/                # 데이터 저장소
│   ├── 📁 raw/             # 원본 데이터 (PDF 리포트 등)
│   └── 📁 processed/       # 전처리된 데이터
│
├── 📁 database/            # 데이터베이스
│   ├── 📁 chroma_db/       # RAG용 Vector DB
│   └── trade_log.db        # 매매 로그 및 시세 데이터
│
├── 📁 src/                 # 소스 코드
│   ├── 📁 agents/          # AI 에이전트 (Analyst, Chartist, Risk Manager)
│   ├── 📁 tools/           # 에이전트 도구 (Search, TA-Lib)
│   └── 📁 data_pipeline/   # 크롤러 및 데이터 로더
│
└── 📁 dashboard/           # 웹 대시보드 (Streamlit)
    └── app.py