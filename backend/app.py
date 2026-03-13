# 파일: backend/app.py
"""
HQA FastAPI 메인 애플리케이션

실행:
    # 개발 모드 (자동 리로드)
    uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

    # 프로덕션 모드
    uvicorn backend.app:app --host 0.0.0.0 --port 8000 --workers 4

    # 또는 gunicorn (Linux)
    gunicorn backend.app:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import get_settings
from backend.middleware.error_handler import ErrorHandlerMiddleware
from backend.middleware.rate_limit import RateLimitMiddleware

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 라이프사이클
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 시작/종료 시 실행"""
    settings = get_settings()
    logger.info(f"🚀 HQA API 시작 (환경: {settings.ENV.value})")
    logger.info(f"   OCR Provider: {settings.OCR_PROVIDER}")
    logger.info(f"   Reranker Provider: {settings.RERANKER_PROVIDER}")
    logger.info(f"   Vector DB: {settings.VECTOR_DB_PROVIDER}")
    logger.info(f"   LangSmith: {'활성' if settings.LANGCHAIN_TRACING_V2 else '비활성'}")

    # LangGraph 상태 확인
    try:
        from src.agents.graph import is_langgraph_available
        if is_langgraph_available():
            logger.info("   LangGraph: 활성 ✅")
        else:
            logger.info("   LangGraph: 비활성 (폴백 모드)")
    except Exception:
        logger.info("   LangGraph: 로드 실패")

    # 데이터베이스 초기화
    try:
        from backend.database.connection import init_db
        await init_db()
    except Exception as e:
        logger.warning(f"데이터베이스 초기화 실패 (차트 등 일부 기능은 정상 작동): {e}")

    yield  # 서버 실행 중

    # 차트 WebSocket 매니저 종료
    try:
        from backend.api.routes.charts import get_chart_manager
        chart_manager = get_chart_manager()
        await chart_manager.shutdown()
    except Exception as e:
        logger.warning(f"차트 WebSocket 종료 오류: {e}")

    logger.info("🛑 HQA API 종료")


# ──────────────────────────────────────────────
# 앱 생성
# ──────────────────────────────────────────────

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "HQA (Hegemony Quantitative Analyst) - AI 기반 멀티 에이전트 주식 분석 API\n\n"
        "## 주요 기능\n"
        "- **종목 분석**: Analyst + Quant + Chartist + RiskManager 협업 분석\n"
        "- **실시간 시세**: 한국투자증권 API 연동\n"
        "- **SSE 스트리밍**: 분석 진행 상황 실시간 전달\n"
        "- **대화형 질문**: Supervisor 에이전트를 통한 자연어 처리\n"
        "- **쿼리 제안**: Answerability Check\n"
    ),
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ──────────────────────────────────────────────
# 미들웨어 (순서 중요: 아래에서 위로 실행)
# ──────────────────────────────────────────────

# 1. 전역 에러 핸들링 (가장 바깥)
app.add_middleware(ErrorHandlerMiddleware)

# 2. Rate Limiting
app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.RATE_LIMIT_PER_MINUTE)

# 3. CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining"],
)


# ──────────────────────────────────────────────
# 라우터 등록
# ──────────────────────────────────────────────

from backend.api.routes.health import router as health_router
from backend.api.routes.stocks import router as stocks_router
from backend.api.routes.analysis import router as analysis_router
from backend.api.routes.charts import router as charts_router

app.include_router(health_router)
app.include_router(stocks_router, prefix="/api/v1")
app.include_router(analysis_router, prefix="/api/v1")
app.include_router(charts_router, prefix="/api/v1")


# ──────────────────────────────────────────────
# 루트 엔드포인트
# ──────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "HQA API",
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }
