# 파일: backend/services/analysis_service.py
"""
분석 서비스 레이어

비즈니스 로직을 API 라우터에서 분리합니다.
- 인메모리 실행 (Celery 없이도 동작)
- Celery Task Queue 연동 (Redis 필요)
- SSE 스트리밍 지원
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

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

logger = logging.getLogger(__name__)


class AnalysisService:
    """분석 서비스 (싱글턴)"""

    def __init__(self):
        # 인메모리 결과 저장소 (프로덕션에서는 DB 사용)
        self._results: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._progress: Dict[str, List[Dict]] = {}  # task_id -> progress events
        self._sessions: Dict[str, Any] = {}  # session_id -> ConversationMemory
        self._max_cache = 500
        self._celery_available = self._check_celery()

    def _check_celery(self) -> bool:
        """Celery 사용 가능 여부 확인"""
        try:
            from backend.tasks.celery_app import celery_app
            celery_app.control.ping(timeout=1)
            return True
        except Exception:
            logger.info("Celery 미사용 → 인메모리 백그라운드 실행 모드")
            return False

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
        """분석 작업 제출"""
        # 결과 슬롯 생성
        self._results[task_id] = {
            "task_id": task_id,
            "status": AnalysisStatus.PENDING,
            "stock": {"name": stock_name, "code": stock_code},
            "mode": mode,
            "created_at": datetime.now(),
        }
        self._progress[task_id] = []

        # LRU 관리
        while len(self._results) > self._max_cache:
            self._results.popitem(last=False)

        if self._celery_available:
            # Celery로 백그라운드 실행
            from backend.tasks.analysis_tasks import run_analysis_task
            run_analysis_task.delay(task_id, stock_name, stock_code, mode.value, max_retries)
        else:
            # asyncio 백그라운드 태스크로 실행
            asyncio.create_task(
                self._run_analysis_in_background(
                    task_id, stock_name, stock_code, mode, max_retries
                )
            )

    async def _run_analysis_in_background(
        self,
        task_id: str,
        stock_name: str,
        stock_code: str,
        mode: AnalysisMode,
        max_retries: int,
    ) -> None:
        """인메모리 백그라운드 분석 실행"""
        self._update_status(task_id, AnalysisStatus.RUNNING)

        try:
            # 동기 분석 함수를 별도 스레드에서 실행 (이벤트 루프 블로킹 방지)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._execute_analysis_sync,
                task_id,
                stock_name,
                stock_code,
                mode,
                max_retries,
            )

            # 결과 저장
            self._results[task_id].update(result)
            self._update_status(task_id, AnalysisStatus.COMPLETED)
            self._add_progress(task_id, {
                "event": "completed",
                "data": {"task_id": task_id, "status": "completed"},
            })

        except Exception as e:
            logger.exception(f"분석 실패: {task_id}")
            self._update_status(task_id, AnalysisStatus.FAILED)
            self._results[task_id]["error"] = str(e)
            self._add_progress(task_id, {
                "event": "error",
                "data": {"task_id": task_id, "error": str(e)},
            })

    def _execute_analysis_sync(
        self,
        task_id: str,
        stock_name: str,
        stock_code: str,
        mode: AnalysisMode,
        max_retries: int,
    ) -> Dict[str, Any]:
        """동기 분석 실행 (별도 스레드)"""

        if mode == AnalysisMode.QUICK:
            return self._run_quick_analysis(task_id, stock_name, stock_code)
        else:
            return self._run_full_analysis(task_id, stock_name, stock_code, max_retries)

    def _run_quick_analysis(self, task_id: str, stock_name: str, stock_code: str) -> Dict:
        """빠른 분석 (Quant + Chartist)"""
        from src.agents import QuantAgent, ChartistAgent
        from src.utils.parallel import run_agents_parallel, is_error

        self._add_progress(task_id, _progress_event("quant", "started", "재무 분석 시작", 0.1))
        self._add_progress(task_id, _progress_event("chartist", "started", "기술적 분석 시작", 0.1))

        quant = QuantAgent()
        chartist = ChartistAgent()

        parallel_results = run_agents_parallel({
            "quant": (quant.full_analysis, (stock_name, stock_code)),
            "chartist": (chartist.full_analysis, (stock_name, stock_code)),
        })

        quant_score = parallel_results["quant"]
        chartist_score = parallel_results["chartist"]

        if is_error(quant_score):
            quant_score = quant._default_score(stock_name, str(quant_score))
        if is_error(chartist_score):
            chartist_score = chartist._default_score(stock_code, str(chartist_score))

        self._add_progress(task_id, _progress_event("quant", "completed", f"재무 분석 완료: {quant_score.grade}", 1.0))
        self._add_progress(task_id, _progress_event("chartist", "completed", f"기술적 분석 완료: {chartist_score.signal}", 1.0))

        scores = [
            ScoreDetail(
                agent="quant",
                total_score=quant_score.total_score,
                max_score=100,
                grade=quant_score.grade,
                opinion=quant_score.opinion,
                details={
                    "valuation": quant_score.valuation_score,
                    "profitability": quant_score.profitability_score,
                    "growth": quant_score.growth_score,
                    "stability": quant_score.stability_score,
                },
            ),
            ScoreDetail(
                agent="chartist",
                total_score=chartist_score.total_score,
                max_score=100,
                grade=chartist_score.signal,
                details={
                    "trend": chartist_score.trend_score,
                    "momentum": chartist_score.momentum_score,
                    "volatility": chartist_score.volatility_score,
                    "volume": chartist_score.volume_score,
                },
            ),
        ]

        return {
            "scores": [s.model_dump() for s in scores],
            "completed_at": datetime.now(),
        }

    def _run_full_analysis(self, task_id: str, stock_name: str, stock_code: str, max_retries: int) -> Dict:
        """전체 분석 (LangGraph 워크플로우)"""
        from src.agents.graph import run_stock_analysis

        self._add_progress(task_id, _progress_event("analyst", "started", "리서치 및 헤게모니 분석 시작", 0.05))
        self._add_progress(task_id, _progress_event("quant", "started", "재무 분석 시작", 0.05))
        self._add_progress(task_id, _progress_event("chartist", "started", "기술적 분석 시작", 0.05))

        result = run_stock_analysis(
            stock_name=stock_name,
            stock_code=stock_code,
            query="",
            max_retries=max_retries,
        )

        raw_scores = result.get("scores", {})
        scores = []

        analyst_score = raw_scores.get("analyst")
        if analyst_score:
            scores.append(ScoreDetail(
                agent="analyst",
                total_score=analyst_score.total_score,
                max_score=70,
                grade=getattr(analyst_score, "hegemony_grade", None),
                opinion=getattr(analyst_score, "final_opinion", None),
                details={
                    "moat_score": analyst_score.moat_score,
                    "growth_score": analyst_score.growth_score,
                    "moat_reason": getattr(analyst_score, "moat_reason", ""),
                    "growth_reason": getattr(analyst_score, "growth_reason", ""),
                },
            ))
            self._add_progress(task_id, _progress_event(
                "analyst", "completed",
                f"헤게모니 분석 완료: {getattr(analyst_score, 'hegemony_grade', '?')}등급", 1.0
            ))

        quant_score = raw_scores.get("quant")
        if quant_score:
            scores.append(ScoreDetail(
                agent="quant",
                total_score=quant_score.total_score,
                max_score=100,
                grade=quant_score.grade,
                opinion=quant_score.opinion,
                details={
                    "valuation": quant_score.valuation_score,
                    "profitability": quant_score.profitability_score,
                    "growth": quant_score.growth_score,
                    "stability": quant_score.stability_score,
                },
            ))
            self._add_progress(task_id, _progress_event("quant", "completed", f"재무 분석 완료: {quant_score.grade}", 1.0))

        chartist_score = raw_scores.get("chartist")
        if chartist_score:
            scores.append(ScoreDetail(
                agent="chartist",
                total_score=chartist_score.total_score,
                max_score=100,
                grade=chartist_score.signal,
                details={
                    "trend": chartist_score.trend_score,
                    "momentum": chartist_score.momentum_score,
                    "volatility": chartist_score.volatility_score,
                    "volume": chartist_score.volume_score,
                },
            ))
            self._add_progress(task_id, _progress_event("chartist", "completed", f"기술적 분석 완료: {chartist_score.signal}", 1.0))

        # 최종 판단
        final_decision = result.get("final_decision")
        final_dict = None
        if final_decision:
            self._add_progress(task_id, _progress_event("risk_manager", "completed", "최종 판단 완료", 1.0))
            final_dict = {
                "action": final_decision.action.value if hasattr(final_decision.action, "value") else str(final_decision.action),
                "confidence": final_decision.confidence,
                "risk_level": final_decision.risk_level.value if hasattr(final_decision.risk_level, "value") else str(final_decision.risk_level),
                "total_score": final_decision.total_score,
                "summary": final_decision.summary,
                "key_catalysts": getattr(final_decision, "key_catalysts", []),
                "risk_factors": getattr(final_decision, "risk_factors", []),
                "detailed_reasoning": getattr(final_decision, "detailed_reasoning", ""),
            }

        return {
            "scores": [s.model_dump() for s in scores],
            "final_decision": final_dict,
            "research_quality": result.get("research_quality"),
            "quality_warnings": result.get("quality_warnings", []),
            "completed_at": datetime.now(),
        }

    # ──────────────────────────────────────────────
    # 결과 조회
    # ──────────────────────────────────────────────

    async def get_result(self, task_id: str) -> Optional[AnalysisResultResponse]:
        """분석 결과 조회"""
        data = self._results.get(task_id)
        if data is None:
            return None

        stock = data.get("stock", {})
        scores_raw = data.get("scores", [])

        # ScoreDetail 리스트 복원
        scores = []
        for s in scores_raw:
            if isinstance(s, dict):
                scores.append(ScoreDetail(**s))
            elif isinstance(s, ScoreDetail):
                scores.append(s)

        created_at = data.get("created_at", datetime.now())
        completed_at = data.get("completed_at")
        duration = None
        if completed_at and created_at:
            duration = (completed_at - created_at).total_seconds()

        return AnalysisResultResponse(
            task_id=task_id,
            status=data.get("status", AnalysisStatus.PENDING),
            stock=StockInfo(name=stock.get("name", ""), code=stock.get("code", "")),
            mode=data.get("mode", AnalysisMode.FULL),
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
    # SSE 스트리밍
    # ──────────────────────────────────────────────

    async def stream_progress(self, task_id: str) -> AsyncGenerator[Dict, None]:
        """SSE 이벤트 스트리밍"""
        if task_id not in self._results:
            yield {"event": "error", "data": {"error": "Task not found"}}
            return

        sent_count = 0
        max_wait = 600  # 최대 10분 대기
        waited = 0

        while waited < max_wait:
            events = self._progress.get(task_id, [])

            # 새 이벤트 전송
            while sent_count < len(events):
                yield events[sent_count]
                event_type = events[sent_count].get("event", "progress")
                sent_count += 1

                if event_type in ("completed", "error"):
                    return

            # 분석 완료 체크
            status = self._results.get(task_id, {}).get("status")
            if status in (AnalysisStatus.COMPLETED, AnalysisStatus.FAILED):
                if sent_count >= len(events):
                    return

            await asyncio.sleep(1)
            waited += 1

        yield {"event": "error", "data": {"error": "Timeout"}}

    # ──────────────────────────────────────────────
    # 대화형 질문
    # ──────────────────────────────────────────────

    async def chat(self, message: str, session_id: str) -> Dict[str, Any]:
        """대화형 질문 처리"""
        from src.utils.memory import ConversationMemory

        # 세션 메모리
        if session_id not in self._sessions:
            self._sessions[session_id] = ConversationMemory(max_turns=10)
        memory = self._sessions[session_id]

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._chat_sync, message, memory)
        return result

    def _chat_sync(self, message: str, memory) -> Dict[str, Any]:
        """동기 대화 실행"""
        from src.agents import SupervisorAgent

        supervisor = SupervisorAgent(memory=memory)
        result = supervisor.execute(message)

        if isinstance(result, dict):
            response_text = (
                result.get("summary")
                or result.get("answer")
                or result.get("analysis")
                or result.get("message")
                or str(result)
            )
            return {
                "message": response_text,
                "intent": result.get("intent"),
                "stocks": result.get("stocks", []),
            }
        else:
            return {"message": str(result)}

    # ──────────────────────────────────────────────
    # 쿼리 제안
    # ──────────────────────────────────────────────

    async def suggest_query(self, query: str) -> QuerySuggestion:
        """쿼리 제안 (Answerability Check)"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._suggest_sync, query)

    def _suggest_sync(self, query: str) -> QuerySuggestion:
        """동기 쿼리 제안"""
        from src.agents.llm_config import get_gemini_llm

        llm = get_gemini_llm()

        prompt = f"""당신은 주식 분석 AI 시스템의 쿼리 검증 모듈입니다.

사용자의 질문이 다음 기능 범위 내에 있는지 판단하세요:
1. 한국 주식 종목 분석 (재무, 기술적, 헤게모니)
2. 실시간 시세 조회
3. 산업/테마 분석
4. 종목 비교

사용자 질문: "{query}"

다음 JSON 형식으로 응답하세요:
{{
    "is_answerable": true/false,
    "corrected_query": "교정된 질문 (필요시)",
    "suggestions": ["대안 질문1", "대안 질문2", "대안 질문3"],
    "reason": "판단 근거"
}}
"""
        try:
            response = llm.invoke(prompt)
            import json
            # JSON 추출
            text = response.content
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                return QuerySuggestion(
                    original_query=query,
                    is_answerable=data.get("is_answerable", True),
                    corrected_query=data.get("corrected_query"),
                    suggestions=data.get("suggestions", []),
                    reason=data.get("reason"),
                )
        except Exception as e:
            logger.warning(f"쿼리 제안 실패: {e}")

        return QuerySuggestion(
            original_query=query,
            is_answerable=True,
            suggestions=[],
        )

    # ──────────────────────────────────────────────
    # 분석 이력
    # ──────────────────────────────────────────────

    async def get_history(self, page: int = 1, page_size: int = 20) -> AnalysisHistoryResponse:
        """분석 이력 조회"""
        all_items = list(reversed(self._results.values()))
        total = len(all_items)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = all_items[start:end]

        items = []
        for data in page_items:
            stock = data.get("stock", {})
            final = data.get("final_decision")
            items.append(AnalysisHistoryItem(
                task_id=data.get("task_id", ""),
                stock=StockInfo(name=stock.get("name", ""), code=stock.get("code", "")),
                mode=data.get("mode", AnalysisMode.FULL),
                status=data.get("status", AnalysisStatus.PENDING),
                total_score=final.get("total_score") if final else None,
                action=final.get("action") if final else None,
                created_at=data.get("created_at", datetime.now()),
                completed_at=data.get("completed_at"),
            ))

        return AnalysisHistoryResponse(
            items=items, total=total, page=page, page_size=page_size
        )

    # ──────────────────────────────────────────────
    # 내부 헬퍼
    # ──────────────────────────────────────────────

    def _update_status(self, task_id: str, status: AnalysisStatus):
        if task_id in self._results:
            self._results[task_id]["status"] = status

    def _add_progress(self, task_id: str, event: Dict):
        if task_id not in self._progress:
            self._progress[task_id] = []
        self._progress[task_id].append(event)


def _progress_event(agent: str, status: str, message: str, progress: float) -> Dict:
    """SSE 프로그레스 이벤트 생성"""
    return {
        "event": "progress",
        "data": {
            "agent": agent,
            "status": status,
            "message": message,
            "progress": progress,
            "timestamp": datetime.now().isoformat(),
        },
    }
