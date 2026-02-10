# 파일: backend/api/schemas.py
"""
Pydantic 요청/응답 스키마

프론트엔드와 백엔드 간 계약(Contract)을 명확히 정의합니다.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# 공통
# ──────────────────────────────────────────────

class StockInfo(BaseModel):
    """종목 기본 정보"""
    name: str
    code: str


class HealthResponse(BaseModel):
    """헬스체크 응답"""
    status: str = "ok"
    version: str
    environment: str
    langgraph_available: bool
    timestamp: datetime = Field(default_factory=datetime.now)


# ──────────────────────────────────────────────
# 종목 관련
# ──────────────────────────────────────────────

class StockSearchRequest(BaseModel):
    """종목 검색 요청"""
    query: str = Field(..., min_length=1, max_length=100, description="종목명 또는 종목코드")


class StockSearchResult(BaseModel):
    """종목 검색 결과"""
    name: str
    code: str
    market: Optional[str] = None


class StockSearchResponse(BaseModel):
    """종목 검색 응답"""
    results: List[StockSearchResult]
    total: int


class RealtimePriceResponse(BaseModel):
    """실시간 시세 응답"""
    stock: StockInfo
    current_price: int
    change: int
    change_rate: float
    open_price: int
    high_price: int
    low_price: int
    volume: int
    market_cap: Optional[int] = None
    per: Optional[float] = None
    pbr: Optional[float] = None
    timestamp: datetime


# ──────────────────────────────────────────────
# 분석 관련
# ──────────────────────────────────────────────

class AnalysisMode(str, Enum):
    """분석 모드"""
    FULL = "full"        # 전체 분석 (Analyst + Quant + Chartist + RiskManager)
    QUICK = "quick"      # 빠른 분석 (Quant + Chartist)


class AnalysisRequest(BaseModel):
    """분석 요청"""
    stock_name: str = Field(..., description="종목명")
    stock_code: str = Field(..., pattern=r"^\d{6}$", description="종목코드 (6자리)")
    mode: AnalysisMode = AnalysisMode.FULL
    max_retries: int = Field(default=1, ge=0, le=3, description="리서치 재시도 횟수")


class AnalysisStatus(str, Enum):
    """분석 상태"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisTaskResponse(BaseModel):
    """분석 작업 접수 응답"""
    task_id: str
    status: AnalysisStatus = AnalysisStatus.PENDING
    message: str = "분석 요청이 접수되었습니다."
    estimated_time_seconds: int = 120


class AgentProgress(BaseModel):
    """에이전트 진행 상황 (SSE용)"""
    agent: str           # analyst, quant, chartist, risk_manager
    status: str          # started, running, completed, error
    message: str
    progress: float      # 0.0 ~ 1.0
    timestamp: datetime = Field(default_factory=datetime.now)


class ScoreDetail(BaseModel):
    """개별 에이전트 점수"""
    agent: str
    total_score: float
    max_score: float
    grade: Optional[str] = None
    opinion: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class AnalysisResultResponse(BaseModel):
    """분석 결과 응답"""
    task_id: str
    status: AnalysisStatus
    stock: StockInfo
    mode: AnalysisMode

    # 개별 에이전트 점수
    scores: List[ScoreDetail] = Field(default_factory=list)

    # 최종 판단 (full 모드)
    final_decision: Optional[Dict[str, Any]] = None

    # 품질 정보
    research_quality: Optional[str] = None
    quality_warnings: List[str] = Field(default_factory=list)

    # 메타
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    errors: Dict[str, str] = Field(default_factory=dict)


# ──────────────────────────────────────────────
# 대화형 질문
# ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    """대화 요청"""
    message: str = Field(..., min_length=1, max_length=1000)
    session_id: Optional[str] = None  # 세션 ID (메모리 연동)


class ChatResponse(BaseModel):
    """대화 응답"""
    session_id: str
    message: str
    intent: Optional[str] = None
    stocks: List[StockInfo] = Field(default_factory=list)
    analysis_triggered: bool = False
    task_id: Optional[str] = None  # 분석이 시작된 경우


# ──────────────────────────────────────────────
# 쿼리 제안 (Answerability Check)
# ──────────────────────────────────────────────

class QuerySuggestionRequest(BaseModel):
    """쿼리 제안 요청"""
    query: str = Field(..., min_length=1)


class QuerySuggestion(BaseModel):
    """추천 쿼리"""
    original_query: str
    is_answerable: bool
    corrected_query: Optional[str] = None
    suggestions: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


# ──────────────────────────────────────────────
# 분석 이력
# ──────────────────────────────────────────────

class AnalysisHistoryItem(BaseModel):
    """분석 이력 항목"""
    task_id: str
    stock: StockInfo
    mode: AnalysisMode
    status: AnalysisStatus
    total_score: Optional[float] = None
    action: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class AnalysisHistoryResponse(BaseModel):
    """분석 이력 응답"""
    items: List[AnalysisHistoryItem]
    total: int
    page: int
    page_size: int
