# 파일: backend/api/routes/analysis.py
"""
분석 엔드포인트

- POST /analysis         → 분석 요청 (비동기 Task Queue)
- GET  /analysis/{id}    → 결과 조회
- GET  /analysis/{id}/stream → SSE 스트리밍
- GET  /analysis/history → 분석 이력
- POST /analysis/chat    → 대화형 질문
- POST /analysis/suggest → 쿼리 제안
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.api.dependencies import verify_api_key
from backend.api.schemas import (
    AnalysisHistoryItem,
    AnalysisHistoryResponse,
    AnalysisMode,
    AnalysisRequest,
    AnalysisResultResponse,
    AnalysisStatus,
    AnalysisTaskResponse,
    ChatRequest,
    ChatResponse,
    QuerySuggestion,
    QuerySuggestionRequest,
    ScoreDetail,
    StockInfo,
)
from backend.services.analysis_service import AnalysisService

router = APIRouter(prefix="/analysis", tags=["Analysis"], dependencies=[Depends(verify_api_key)])


# ── 싱글턴 서비스 ──
_analysis_service: Optional[AnalysisService] = None


def get_analysis_service() -> AnalysisService:
    global _analysis_service
    if _analysis_service is None:
        _analysis_service = AnalysisService()
    return _analysis_service


# ──────────────────────────────────────────────
# 분석 요청 (비동기)
# ──────────────────────────────────────────────

@router.post("", response_model=AnalysisTaskResponse, status_code=202)
async def create_analysis(
    request: AnalysisRequest,
    service: AnalysisService = Depends(get_analysis_service),
):
    """
    종목 분석 요청 (비동기)
    
    분석은 백그라운드에서 수행되며, task_id로 결과를 조회할 수 있습니다.
    SSE 스트리밍으로 실시간 진행 상황을 확인할 수 있습니다.
    """
    task_id = str(uuid.uuid4())

    try:
        # Celery가 사용 가능하면 Task Queue, 아니면 인메모리 실행
        await service.submit_analysis(
            task_id=task_id,
            stock_name=request.stock_name,
            stock_code=request.stock_code,
            mode=request.mode,
            max_retries=request.max_retries,
        )

        estimated_time = 180 if request.mode == AnalysisMode.FULL else 60

        return AnalysisTaskResponse(
            task_id=task_id,
            status=AnalysisStatus.PENDING,
            message=f"{request.stock_name}({request.stock_code}) 분석이 접수되었습니다.",
            estimated_time_seconds=estimated_time,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"분석 요청 실패: {str(e)}")


# ──────────────────────────────────────────────
# 결과 조회
# ──────────────────────────────────────────────

@router.get("/{task_id}", response_model=AnalysisResultResponse)
async def get_analysis_result(
    task_id: str,
    service: AnalysisService = Depends(get_analysis_service),
):
    """
    분석 결과 조회
    
    task_id로 분석 결과를 조회합니다.
    분석이 아직 진행 중이면 현재 상태를 반환합니다.
    """
    result = await service.get_result(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"분석 작업을 찾을 수 없습니다: {task_id}")
    return result


# ──────────────────────────────────────────────
# SSE 스트리밍
# ──────────────────────────────────────────────

@router.get("/{task_id}/stream")
async def stream_analysis_progress(
    task_id: str,
    service: AnalysisService = Depends(get_analysis_service),
):
    """
    분석 진행 상황 SSE 스트리밍
    
    Server-Sent Events로 에이전트별 진행 상황을 실시간 전달합니다.
    
    이벤트 형식:
    ```
    event: progress
    data: {"agent": "analyst", "status": "running", "message": "리서치 중...", "progress": 0.3}
    
    event: completed
    data: {"task_id": "...", "status": "completed"}
    ```
    """
    async def event_generator():
        async for event in service.stream_progress(task_id):
            event_type = event.get("event", "progress")
            data = json.dumps(event.get("data", event), ensure_ascii=False)
            yield f"event: {event_type}\ndata: {data}\n\n"

            if event_type in ("completed", "error"):
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Nginx 버퍼링 비활성화
        },
    )


# ──────────────────────────────────────────────
# 대화형 질문
# ──────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    service: AnalysisService = Depends(get_analysis_service),
):
    """
    대화형 질문 (Supervisor 에이전트)
    
    자연어로 질문하면 Supervisor가 의도를 파악하고 적절한 에이전트를 호출합니다.
    세션 ID로 대화 맥락이 유지됩니다.
    """
    session_id = request.session_id or str(uuid.uuid4())

    try:
        result = await service.chat(
            message=request.message, session_id=session_id
        )
        return ChatResponse(
            session_id=session_id,
            message=result.get("message", ""),
            intent=result.get("intent"),
            stocks=[StockInfo(**s) for s in result.get("stocks", [])],
            analysis_triggered=result.get("analysis_triggered", False),
            task_id=result.get("task_id"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"대화 처리 실패: {str(e)}")


# ──────────────────────────────────────────────
# 쿼리 제안 (Answerability Check)
# ──────────────────────────────────────────────

@router.post("/suggest", response_model=QuerySuggestion)
async def suggest_query(
    request: QuerySuggestionRequest,
    service: AnalysisService = Depends(get_analysis_service),
):
    """
    쿼리 제안 (Answerability Check)
    
    질문이 시스템의 답변 범위를 벗어날 경우:
    - 교정된 질문을 제안
    - 가능한 질문 리스트를 반환
    """
    try:
        suggestion = await service.suggest_query(request.query)
        return suggestion
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"쿼리 제안 실패: {str(e)}")


# ──────────────────────────────────────────────
# 분석 이력
# ──────────────────────────────────────────────

@router.get("/history/list", response_model=AnalysisHistoryResponse)
async def get_analysis_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: AnalysisService = Depends(get_analysis_service),
):
    """분석 이력 조회"""
    history = await service.get_history(page=page, page_size=page_size)
    return history
