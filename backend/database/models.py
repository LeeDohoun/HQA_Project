# 파일: backend/database/models.py
"""
SQLAlchemy 모델 (프로덕션용)

다중 사용자 지원을 위한 영구 데이터 저장:
- 사용자
- 분석 요청/결과
- 대화 세션
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from backend.database.connection import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """사용자"""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=True)
    api_key = Column(String(255), unique=True, nullable=True)
    name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    analyses = relationship("AnalysisRecord", back_populates="user")
    sessions = relationship("ChatSession", back_populates="user")


class AnalysisRecord(Base):
    """분석 기록"""
    __tablename__ = "analysis_records"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    task_id = Column(String(36), unique=True, index=True)

    # 종목 정보
    stock_name = Column(String(100), nullable=False)
    stock_code = Column(String(10), nullable=False, index=True)

    # 분석 설정
    mode = Column(String(20), default="full")  # full, quick
    max_retries = Column(Integer, default=1)

    # 상태
    status = Column(String(20), default="pending", index=True)  # pending, running, completed, failed

    # 에이전트 결과 (JSON)
    analyst_result = Column(JSON, nullable=True)
    quant_result = Column(JSON, nullable=True)
    chartist_result = Column(JSON, nullable=True)
    final_decision = Column(JSON, nullable=True)

    # 품질
    research_quality = Column(String(5), nullable=True)
    quality_warnings = Column(JSON, default=list)

    # 최종 점수
    total_score = Column(Float, nullable=True)
    action = Column(String(20), nullable=True)
    confidence = Column(Float, nullable=True)

    # 에러
    errors = Column(JSON, default=dict)

    # 시간
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Relationships
    user = relationship("User", back_populates="analyses")

    __table_args__ = (
        Index("ix_analysis_stock_date", "stock_code", "created_at"),
    )


class ChatSession(Base):
    """대화 세션"""
    __tablename__ = "chat_sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    session_id = Column(String(36), unique=True, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="sessions")
    messages = relationship("ChatMessage", back_populates="session", order_by="ChatMessage.created_at")


class ChatMessage(Base):
    """대화 메시지"""
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(36), ForeignKey("chat_sessions.id"), nullable=False)
    role = Column(String(20), nullable=False)  # user, assistant
    content = Column(Text, nullable=False)
    intent = Column(String(50), nullable=True)
    stocks = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        Index("ix_chat_session_time", "session_id", "created_at"),
    )


class StockCache(Base):
    """종목 데이터 캐시"""
    __tablename__ = "stock_cache"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    stock_code = Column(String(10), nullable=False, index=True)
    stock_name = Column(String(100), nullable=False)
    data_type = Column(String(50), nullable=False)  # price, financials, news, report
    data = Column(JSON, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_stock_cache_type", "stock_code", "data_type"),
    )
