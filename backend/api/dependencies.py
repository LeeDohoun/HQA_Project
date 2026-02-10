# 파일: backend/api/dependencies.py
"""
FastAPI 의존성 주입 (Dependency Injection)
"""

from __future__ import annotations

from typing import AsyncGenerator, Optional

from fastapi import Depends, Header, HTTPException, status

from backend.config import Settings, get_settings


async def get_current_settings() -> Settings:
    """설정 의존성"""
    return get_settings()


async def verify_api_key(
    x_api_key: Optional[str] = Header(None),
    settings: Settings = Depends(get_current_settings),
) -> bool:
    """
    API 키 검증 (프로덕션용)
    
    로컬/개발 환경에서는 비활성화됩니다.
    """
    if settings.ENV in ("local", "dev"):
        return True

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API 키가 필요합니다. X-API-Key 헤더를 설정하세요.",
        )

    # 간단한 API 키 검증 (실제로는 DB 조회)
    if x_api_key != settings.SECRET_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="유효하지 않은 API 키입니다.",
        )

    return True
