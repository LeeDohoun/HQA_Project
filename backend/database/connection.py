# 파일: backend/database/connection.py
"""
데이터베이스 연결 관리 (SQLAlchemy Async)

로컬 개발: SQLite (aiosqlite)
프로덕션: PostgreSQL (asyncpg)

DATABASE_URL 형식:
  - SQLite:     sqlite+aiosqlite:///./database/hqa.db
  - PostgreSQL: postgresql+asyncpg://user:pass@host:5432/hqa
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy 모델 베이스"""
    pass


# ── 엔진 & 세션 팩토리 (싱글턴) ──
_engine = None
_session_factory = None


def _get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        connect_args = {}
        if settings.DATABASE_URL.startswith("sqlite"):
            connect_args = {"check_same_thread": False}

        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DEBUG,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 의존성 주입용 DB 세션"""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """테이블 생성 (앱 시작 시)"""
    engine = _get_engine()
    async with engine.begin() as conn:
        # 모델 임포트 (테이블 등록)
        from backend.database import models  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    logger.info("데이터베이스 테이블 초기화 완료")


async def check_db_connection() -> bool:
    """DB 연결 확인"""
    try:
        engine = _get_engine()
        async with engine.connect() as conn:
            await conn.execute(
                __import__("sqlalchemy").text("SELECT 1")
            )
        return True
    except Exception as e:
        logger.error(f"DB 연결 실패: {e}")
        return False
