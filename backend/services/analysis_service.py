# 파일: backend/services/analysis_service.py
"""
분석 서비스 레이어

AI 서버(port 8001)에 HTTP 요청으로 분석을 위임합니다.
- POST /analyze  → 분석 실행 (비동기)
- GET  /analyze/{task_id} → 결과 조회
- POST /chat     → 대화형 질문
- POST /suggest  → 쿼리 제안

진행 상황은 Redis pub/sub `hqa:progress:{task_id}` 채널로 수신합니다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from backend.api.schemas import (
    AnalysisHistoryItem,
    AnalysisHistoryResponse,
    AnalysisMode,
    AnalysisResultResponse,
    AnalysisStatus,
    QuerySuggestion,
    ScoreDetail,
    StockInfo,
)
from backend.config import get_settings

logger = logging.getLogger(__name__)


class AnalysisService:
    """분석 서비스 - AI 서버 HTTP 클라이언트"""

    def __init__(self):
        settings = get_settings()
        self._ai_server_url = settings.AI_SERVER_URL
        self._redis_url = settings.REDIS_URL

        # 태스크 메타데이터 (stock 정보, 상태, 타임스탬프)
        self._tasks: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._max_cache = 500

    # ──────────────────────────────────────────────
    # 분석 제출
    # ──────────────────────────────────────────────

    async def submit_analysis(
        self,
        task_id: str,
        stock_name: str,
        stock_code: str,
        mode: AnalysisMode,
        max_retries: int = 1,
    ) -> None:
        """AI 서버에 분석 요청 제출"""
        # 태스크 메타데이터 저장
        self._tasks[task_id] = {
            "task_id": task_id,
            "status": AnalysisStatus.PENDING,
            "stock": {"name": stock_name, "code": stock_code},
            "mode": mode,
            "created_at": datetime.now(),
        }
        while len(self._tasks) > self._max_cache:
            self._tasks.popitem(last=False)

        # AI 서버에 비동기 POST
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                await client.post(
                    f"{self._ai_server_url}/analyze",
                    json={
                        "task_id": task_id,
                        "stock_name": stock_name,
                        "stock_code": stock_code,
                        "mode": mode.value,
                        "max_retries": max_retries,
                    },
                )
                self._tasks[task_id]["status"] = AnalysisStatus.RUNNING
            except httpx.ConnectError:
                raise RuntimeError(
                    f"AI 서버에 연결할 수 없습니다 ({self._ai_server_url}). "
                    "AI 서버가 실행 중인지 확인하세요."
                )

    # ──────────────────────────────────────────────
    # 결과 조회
    # ──────────────────────────────────────────────

    async def get_result(self, task_id: str) -> Optional[AnalysisResultResponse]:
        """AI 서버에서 분석 결과 조회"""
        if task_id not in self._tasks:
            return None

        task_meta = self._tasks[task_id]

        # AI 서버에서 결과 조회
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._ai_server_url}/analyze/{task_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    return self._build_result_response(task_id, task_meta, data)
                elif resp.status_code == 404:
                    # 아직 결과 없음 (분석 진행 중)
                    return self._build_result_response(task_id, task_meta, {})
        except Exception as e:
            logger.warning(f"AI 서버 결과 조회 실패: {e}")

        return self._build_result_response(task_id, task_meta, {})

    def _build_result_response(
        self, task_id: str, task_meta: dict, data: dict
    ) -> AnalysisResultResponse:
        """AI 서버 응답 → AnalysisResultResponse 변환"""
        stock = task_meta.get("stock", {})
        ai_status = data.get("status")

        # AI 서버 status → AnalysisStatus 매핑
        if ai_status == "completed":
            status = AnalysisStatus.COMPLETED
            self._tasks[task_id]["status"] = AnalysisStatus.COMPLETED
        elif ai_status == "failed":
            status = AnalysisStatus.FAILED
            self._tasks[task_id]["status"] = AnalysisStatus.FAILED
        else:
            status = task_meta.get("status", AnalysisStatus.PENDING)

        # 점수 변환
        scores: List[ScoreDetail] = []
        raw_scores = data.get("scores", {})

        quant_data = raw_scores.get("quant") if raw_scores else None
        chartist_data = raw_scores.get("chartist") if raw_scores else None
        analyst_data = raw_scores.get("analyst") if raw_scores else None

        if analyst_data:
            scores.append(ScoreDetail(
                agent="analyst",
                total_score=analyst_data.get("total_score", 0),
                max_score=70,
                grade=analyst_data.get("hegemony_grade"),
                opinion=analyst_data.get("final_opinion"),
                details={
                    "moat_score": analyst_data.get("moat_score"),
                    "growth_score": analyst_data.get("growth_score"),
                    "moat_reason": analyst_data.get("moat_reason", ""),
                    "growth_reason": analyst_data.get("growth_reason", ""),
                },
            ))
        if quant_data:
            scores.append(ScoreDetail(
                agent="quant",
                total_score=quant_data.get("total_score", 0),
                max_score=100,
                grade=quant_data.get("grade"),
                opinion=quant_data.get("opinion"),
                details={
                    "valuation": quant_data.get("valuation_score"),
                    "profitability": quant_data.get("profitability_score"),
                    "growth": quant_data.get("growth_score"),
                    "stability": quant_data.get("stability_score"),
                },
            ))
        if chartist_data:
            scores.append(ScoreDetail(
                agent="chartist",
                total_score=chartist_data.get("total_score", 0),
                max_score=100,
                grade=chartist_data.get("signal"),
                details={
                    "trend": chartist_data.get("trend_score"),
                    "momentum": chartist_data.get("momentum_score"),
                    "volatility": chartist_data.get("volatility_score"),
                    "volume": chartist_data.get("volume_score"),
                },
            ))

        created_at = task_meta.get("created_at", datetime.now())
        completed_at_str = data.get("completed_at")
        completed_at = None
        if completed_at_str:
            try:
                completed_at = datetime.fromisoformat(completed_at_str)
            except Exception:
                pass

        duration = None
        if completed_at and created_at:
            duration = (completed_at - created_at).total_seconds()

        return AnalysisResultResponse(
            task_id=task_id,
            status=status,
            stock=StockInfo(name=stock.get("name", ""), code=stock.get("code", "")),
            mode=task_meta.get("mode", AnalysisMode.FULL),
            scores=scores,
            final_decision=data.get("final_decision"),
            research_quality=data.get("research_quality"),
            quality_warnings=data.get("quality_warnings", []),
            created_at=created_at,
            completed_at=completed_at,
            duration_seconds=duration,
            errors=data.get("errors", {}),
        )

    # ──────────────────────────────────────────────
    # SSE 스트리밍 (Redis pub/sub)
    # ──────────────────────────────────────────────

    async def stream_progress(self, task_id: str) -> AsyncGenerator[Dict, None]:
        """Redis pub/sub으로 AI 서버 진행 상황 스트리밍"""
        if task_id not in self._tasks:
            yield {"event": "error", "data": {"error": "Task not found"}}
            return

        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(self._redis_url)
            pubsub = r.pubsub()
            await pubsub.subscribe(f"hqa:progress:{task_id}")

            max_wait_seconds = 600
            waited = 0

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                data = json.loads(message["data"])
                status = data.get("status")
                event_type = "progress"

                if status == "completed":
                    event_type = "completed"
                elif status == "error":
                    event_type = "error"

                yield {"event": event_type, "data": data}

                if event_type in ("completed", "error"):
                    break

                waited += 1
                if waited > max_wait_seconds:
                    yield {"event": "error", "data": {"error": "Timeout"}}
                    break

            await pubsub.unsubscribe(f"hqa:progress:{task_id}")
            await r.aclose()

        except Exception as e:
            logger.warning(f"Redis pub/sub 실패, 폴링으로 전환: {e}")
            async for event in self._stream_by_polling(task_id):
                yield event

    async def _stream_by_polling(self, task_id: str) -> AsyncGenerator[Dict, None]:
        """Redis 미사용 시 AI 서버 폴링으로 스트리밍 대체"""
        max_wait = 600
        waited = 0

        while waited < max_wait:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{self._ai_server_url}/analyze/{task_id}")
                    if resp.status_code == 200:
                        data = resp.json()
                        status = data.get("status")
                        if status == "completed":
                            yield {"event": "completed", "data": {"task_id": task_id, "status": "completed"}}
                            return
                        elif status == "failed":
                            yield {"event": "error", "data": {"task_id": task_id, "error": data.get("error", "")}}
                            return
            except Exception:
                pass

            await asyncio.sleep(2)
            waited += 2

        yield {"event": "error", "data": {"error": "Timeout"}}

    # ──────────────────────────────────────────────
    # 대화형 질문
    # ──────────────────────────────────────────────

    async def chat(self, message: str, session_id: str) -> Dict[str, Any]:
        """AI 서버 /chat 엔드포인트 호출"""
        async with httpx.AsyncClient(timeout=120) as client:
            try:
                resp = await client.post(
                    f"{self._ai_server_url}/chat",
                    json={"message": message, "session_id": session_id},
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.ConnectError:
                raise RuntimeError(f"AI 서버에 연결할 수 없습니다 ({self._ai_server_url})")

    # ──────────────────────────────────────────────
    # 쿼리 제안
    # ──────────────────────────────────────────────

    async def suggest_query(self, query: str) -> QuerySuggestion:
        """AI 서버 /suggest 엔드포인트 호출"""
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    f"{self._ai_server_url}/suggest",
                    json={"query": query},
                )
                resp.raise_for_status()
                data = resp.json()
                return QuerySuggestion(
                    original_query=query,
                    is_answerable=data.get("is_answerable", True),
                    corrected_query=data.get("corrected_query"),
                    suggestions=data.get("suggestions", []),
                    reason=data.get("reason"),
                )
            except Exception as e:
                logger.warning(f"쿼리 제안 실패: {e}")
                return QuerySuggestion(original_query=query, is_answerable=True, suggestions=[])

    # ──────────────────────────────────────────────
    # 분석 이력
    # ──────────────────────────────────────────────

    async def get_history(self, page: int = 1, page_size: int = 20) -> AnalysisHistoryResponse:
        """태스크 메타데이터 기반 이력 조회"""
        all_items = list(reversed(self._tasks.values()))
        total = len(all_items)
        start = (page - 1) * page_size
        page_items = all_items[start:start + page_size]

        items = []
        for data in page_items:
            stock = data.get("stock", {})
            items.append(AnalysisHistoryItem(
                task_id=data.get("task_id", ""),
                stock=StockInfo(name=stock.get("name", ""), code=stock.get("code", "")),
                mode=data.get("mode", AnalysisMode.FULL),
                status=data.get("status", AnalysisStatus.PENDING),
                total_score=None,
                action=None,
                created_at=data.get("created_at", datetime.now()),
                completed_at=None,
            ))

        return AnalysisHistoryResponse(
            items=items, total=total, page=page, page_size=page_size
        )
