"""
HQA AI Server - AI 에이전트 & RAG 전용 서버

포트: 8001
역할: CPU/GPU 집약적인 LLM 추론, LangGraph 워크플로우, RAG 파이프라인 실행
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 프로젝트 루트를 sys.path에 추가 (src/ 패키지 접근용)
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# .env-ai 우선 로드, 없으면 .env fallback
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env-ai") or load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 인메모리 결과 캐시 (Redis 폴백용) ──
_results: OrderedDict[str, Dict[str, Any]] = OrderedDict()
_MAX_CACHE = 500


# ──────────────────────────────────────────────
# 라이프사이클
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🤖 HQA AI Server 시작 (port 8001)")
    try:
        from src.agents.graph import is_langgraph_available
        if is_langgraph_available():
            logger.info("   LangGraph: 활성 ✅")
        else:
            logger.info("   LangGraph: 비활성 (폴백 모드)")
    except Exception:
        logger.info("   LangGraph: 로드 실패")
    yield
    logger.info("🛑 HQA AI Server 종료")


# ──────────────────────────────────────────────
# 앱 생성
# ──────────────────────────────────────────────

app = FastAPI(
    title="HQA AI Server",
    description="AI 에이전트 & RAG 분석 전용 서버",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# 스키마
# ──────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    task_id: str
    stock_name: str
    stock_code: str
    mode: str = "full"          # "full" | "quick"
    max_retries: int = 1


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class SuggestRequest(BaseModel):
    query: str


# ──────────────────────────────────────────────
# Redis 헬퍼
# ──────────────────────────────────────────────

def _get_redis_url() -> str:
    import os
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _publish_progress(task_id: str, agent: str, status: str, message: str, progress: float):
    """Redis pub/sub으로 진행 상황 전달"""
    try:
        import redis
        r = redis.from_url(_get_redis_url())
        event = json.dumps({
            "task_id": task_id,
            "agent": agent,
            "status": status,
            "message": message,
            "progress": progress,
            "timestamp": datetime.now().isoformat(),
        }, ensure_ascii=False)
        r.publish(f"hqa:progress:{task_id}", event)
    except Exception:
        pass  # Redis 미사용 시 무시


def _store_result(task_id: str, result: dict):
    """결과를 Redis + 인메모리에 저장"""
    try:
        import redis
        r = redis.from_url(_get_redis_url())
        r.setex(
            f"hqa:result:{task_id}",
            3600,  # 1시간 TTL
            json.dumps(result, ensure_ascii=False, default=str),
        )
    except Exception:
        pass

    # 인메모리 폴백
    _results[task_id] = result
    while len(_results) > _MAX_CACHE:
        _results.popitem(last=False)


# ──────────────────────────────────────────────
# 분석 실행 (백그라운드)
# ──────────────────────────────────────────────

async def _run_analysis_background(
    task_id: str, stock_name: str, stock_code: str, mode: str, max_retries: int
):
    """asyncio 백그라운드에서 AI 분석 실행"""
    loop = asyncio.get_event_loop()
    try:
        _publish_progress(task_id, "system", "started", f"{stock_name} 분석 시작", 0.0)

        if mode == "quick":
            result = await loop.run_in_executor(
                None, _execute_quick, task_id, stock_name, stock_code
            )
        else:
            result = await loop.run_in_executor(
                None, _execute_full, task_id, stock_name, stock_code, max_retries
            )

        _store_result(task_id, {**result, "status": "completed"})
        _publish_progress(task_id, "system", "completed", "분석 완료", 1.0)

    except Exception as e:
        logger.exception(f"분석 실패: {task_id}")
        _store_result(task_id, {"task_id": task_id, "status": "failed", "error": str(e)})
        _publish_progress(task_id, "system", "error", f"오류: {str(e)[:200]}", 0.0)


def _execute_quick(task_id: str, stock_name: str, stock_code: str) -> dict:
    """빠른 분석 (Quant + Chartist 병렬)"""
    from src.agents import QuantAgent, ChartistAgent
    from src.utils.parallel import run_agents_parallel, is_error

    _publish_progress(task_id, "quant", "started", "재무 분석 중...", 0.2)
    _publish_progress(task_id, "chartist", "started", "기술적 분석 중...", 0.2)

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

    _publish_progress(task_id, "quant", "completed", f"재무: {quant_score.grade}", 1.0)
    _publish_progress(task_id, "chartist", "completed", f"기술: {chartist_score.signal}", 1.0)

    return {
        "task_id": task_id,
        "mode": "quick",
        "stock": {"name": stock_name, "code": stock_code},
        "scores": {
            "quant": _score_to_dict(quant_score),
            "chartist": _score_to_dict(chartist_score),
        },
        "completed_at": datetime.now().isoformat(),
    }


def _execute_full(task_id: str, stock_name: str, stock_code: str, max_retries: int) -> dict:
    """전체 분석 (LangGraph 워크플로우)"""
    from src.agents.graph import run_stock_analysis

    _publish_progress(task_id, "analyst", "started", "헤게모니 분석 중...", 0.1)
    _publish_progress(task_id, "quant", "started", "재무 분석 중...", 0.1)
    _publish_progress(task_id, "chartist", "started", "기술적 분석 중...", 0.1)

    result = run_stock_analysis(
        stock_name=stock_name,
        stock_code=stock_code,
        max_retries=max_retries,
    )

    scores = result.get("scores", {})
    analyst_score = scores.get("analyst")
    quant_score = scores.get("quant")
    chartist_score = scores.get("chartist")
    final_decision = result.get("final_decision")

    if analyst_score:
        _publish_progress(task_id, "analyst", "completed",
                          f"헤게모니: {getattr(analyst_score, 'hegemony_grade', '?')}", 1.0)
    if quant_score:
        _publish_progress(task_id, "quant", "completed", f"재무: {quant_score.grade}", 1.0)
    if chartist_score:
        _publish_progress(task_id, "chartist", "completed", f"기술: {chartist_score.signal}", 1.0)
    if final_decision:
        _publish_progress(task_id, "risk_manager", "completed",
                          f"판단: {final_decision.action.value}", 1.0)

    return {
        "task_id": task_id,
        "mode": "full",
        "stock": {"name": stock_name, "code": stock_code},
        "scores": {
            "analyst": _score_to_dict(analyst_score) if analyst_score else None,
            "quant": _score_to_dict(quant_score) if quant_score else None,
            "chartist": _score_to_dict(chartist_score) if chartist_score else None,
        },
        "final_decision": _decision_to_dict(final_decision) if final_decision else None,
        "research_quality": result.get("research_quality"),
        "quality_warnings": result.get("quality_warnings", []),
        "completed_at": datetime.now().isoformat(),
    }


# ──────────────────────────────────────────────
# 변환 헬퍼
# ──────────────────────────────────────────────

def _score_to_dict(score) -> dict:
    if score is None:
        return {}
    if hasattr(score, "__dict__"):
        return {k: v for k, v in score.__dict__.items() if not k.startswith("_")}
    return {}


def _decision_to_dict(decision) -> dict:
    if decision is None:
        return {}
    return {
        "action": decision.action.value if hasattr(decision.action, "value") else str(decision.action),
        "confidence": getattr(decision, "confidence", 0),
        "risk_level": decision.risk_level.value if hasattr(decision.risk_level, "value") else str(decision.risk_level),
        "total_score": getattr(decision, "total_score", 0),
        "summary": getattr(decision, "summary", ""),
        "key_catalysts": getattr(decision, "key_catalysts", []),
        "risk_factors": getattr(decision, "risk_factors", []),
        "detailed_reasoning": getattr(decision, "detailed_reasoning", ""),
    }


# ──────────────────────────────────────────────
# 엔드포인트
# ──────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "HQA AI Server", "port": 8001}


@app.post("/analyze", status_code=202)
async def analyze(request: AnalyzeRequest):
    """
    분석 요청 (비동기)

    즉시 task_id를 반환하고 백그라운드에서 분석을 실행합니다.
    진행 상황은 Redis pub/sub `hqa:progress:{task_id}` 채널로 전달됩니다.
    결과는 Redis `hqa:result:{task_id}` 키에 저장됩니다.
    """
    asyncio.create_task(
        _run_analysis_background(
            request.task_id,
            request.stock_name,
            request.stock_code,
            request.mode,
            request.max_retries,
        )
    )
    return {"task_id": request.task_id, "status": "pending"}


@app.get("/analyze/{task_id}")
async def get_analyze_result(task_id: str):
    """분석 결과 조회 (Redis → 인메모리 순서로 조회)"""
    # Redis 우선 조회
    try:
        import redis
        r = redis.from_url(_get_redis_url())
        data = r.get(f"hqa:result:{task_id}")
        if data:
            return json.loads(data)
    except Exception:
        pass

    # 인메모리 폴백
    result = _results.get(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"작업을 찾을 수 없습니다: {task_id}")
    return result


@app.post("/chat")
async def chat(request: ChatRequest):
    """대화형 질문 (SupervisorAgent)"""
    loop = asyncio.get_event_loop()

    def _run():
        from src.agents import SupervisorAgent
        supervisor = SupervisorAgent()
        result = supervisor.execute(request.message)
        if isinstance(result, dict):
            return {
                "message": (
                    result.get("summary") or result.get("answer") or
                    result.get("analysis") or result.get("message") or str(result)
                ),
                "intent": result.get("intent"),
                "stocks": result.get("stocks", []),
            }
        return {"message": str(result)}

    return await loop.run_in_executor(None, _run)


@app.post("/suggest")
async def suggest(request: SuggestRequest):
    """쿼리 제안 (Answerability Check)"""
    loop = asyncio.get_event_loop()

    def _run():
        from src.agents.llm_config import get_instruct_llm
        llm = get_instruct_llm()
        prompt = f"""당신은 주식 분석 AI 시스템의 쿼리 검증 모듈입니다.

사용자의 질문이 다음 기능 범위 내에 있는지 판단하세요:
1. 한국 주식 종목 분석 (재무, 기술적, 헤게모니)
2. 실시간 시세 조회
3. 산업/테마 분석
4. 종목 비교

사용자 질문: "{request.query}"

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
            text = response.content
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception as e:
            logger.warning(f"쿼리 제안 실패: {e}")
        return {"is_answerable": True, "corrected_query": None, "suggestions": [], "reason": None}

    result = await loop.run_in_executor(None, _run)
    return {"original_query": request.query, **result}
