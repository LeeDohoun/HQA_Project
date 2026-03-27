# 파일: backend/api/routes/health.py
"""
헬스체크 엔드포인트
"""

from datetime import datetime

from fastapi import APIRouter, Depends

from backend.api.schemas import HealthResponse
from backend.config import Settings, get_settings

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(settings: Settings = Depends(get_settings)):
    """시스템 상태 확인"""
    try:
        from src.agents.graph import is_langgraph_available
        lg_available = is_langgraph_available()
    except Exception:
        lg_available = False

    return HealthResponse(
        status="ok",
        version=settings.APP_VERSION,
        environment=settings.ENV.value,
        langgraph_available=lg_available,
        timestamp=datetime.now(),
    )


@router.get("/health/detailed")
async def detailed_health(settings: Settings = Depends(get_settings)):
    """상세 시스템 상태 (내부용)"""
    checks = {
        "api": "ok",
        "database": "unknown",
        "redis": "unknown",
        "vector_store": "unknown",
    }

    # Database 체크
    try:
        from backend.database.connection import check_db_connection
        checks["database"] = "ok" if await check_db_connection() else "error"
    except Exception as e:
        checks["database"] = f"error: {str(e)[:100]}"

    # Redis 체크
    try:
        from backend.tasks.celery_app import celery_app
        celery_app.control.ping(timeout=2)
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable (task queue disabled)"

    return {
        "status": "ok" if all(v == "ok" for v in checks.values()) else "degraded",
        "checks": checks,
        "version": settings.APP_VERSION,
        "environment": settings.ENV.value,
    }
