"""
HQA AI Server.

Runs stock analysis workflows and query suggestion endpoints for the backend.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_results: OrderedDict[str, dict[str, Any]] = OrderedDict()
_MAX_CACHE = 500


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("HQA AI Server starting on port 8001")
    try:
        from src.agents.graph import is_langgraph_available

        logger.info("LangGraph available: %s", is_langgraph_available())
    except Exception:
        logger.info("LangGraph availability check failed")
    yield
    logger.info("HQA AI Server stopped")


app = FastAPI(
    title="HQA AI Server",
    description="AI analysis service for HQA",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    task_id: str
    stock_name: str
    stock_code: str
    mode: str = "full"
    max_retries: int = 1


class SuggestRequest(BaseModel):
    query: str


def _get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _publish_progress(task_id: str, agent: str, status: str, message: str, progress: float) -> None:
    try:
        import redis

        redis_client = redis.from_url(_get_redis_url())
        payload = json.dumps(
            {
                "task_id": task_id,
                "agent": agent,
                "status": status,
                "message": message,
                "progress": progress,
                "timestamp": datetime.now().isoformat(),
            },
            ensure_ascii=False,
        )
        redis_client.publish(f"hqa:progress:{task_id}", payload)
    except Exception:
        pass


def _store_result(task_id: str, result: dict[str, Any]) -> None:
    try:
        import redis

        redis_client = redis.from_url(_get_redis_url())
        redis_client.setex(
            f"hqa:result:{task_id}",
            3600,
            json.dumps(result, ensure_ascii=False, default=str),
        )
    except Exception:
        pass

    _results[task_id] = result
    while len(_results) > _MAX_CACHE:
        _results.popitem(last=False)


async def _run_analysis_background(
    task_id: str, stock_name: str, stock_code: str, mode: str, max_retries: int
) -> None:
    loop = asyncio.get_event_loop()
    try:
        _publish_progress(task_id, "system", "started", f"{stock_name} analysis started", 0.0)
        if mode == "quick":
            result = await loop.run_in_executor(None, _execute_quick, task_id, stock_name, stock_code)
        else:
            result = await loop.run_in_executor(
                None, _execute_full, task_id, stock_name, stock_code, max_retries
            )

        _store_result(task_id, {**result, "status": "completed"})
        _publish_progress(task_id, "system", "completed", "analysis completed", 1.0)
    except Exception as exc:
        logger.exception("Analysis failed: %s", task_id)
        _store_result(task_id, {"task_id": task_id, "status": "failed", "error": str(exc)})
        _publish_progress(task_id, "system", "error", f"error: {str(exc)[:200]}", 0.0)


def _execute_quick(task_id: str, stock_name: str, stock_code: str) -> dict[str, Any]:
    from src.agents import ChartistAgent, QuantAgent
    from src.utils.parallel import is_error, run_agents_parallel

    _publish_progress(task_id, "quant", "started", "financial analysis running", 0.2)
    _publish_progress(task_id, "chartist", "started", "technical analysis running", 0.2)

    quant = QuantAgent()
    chartist = ChartistAgent()
    parallel_results = run_agents_parallel(
        {
            "quant": (quant.full_analysis, (stock_name, stock_code)),
            "chartist": (chartist.full_analysis, (stock_name, stock_code)),
        }
    )

    quant_score = parallel_results["quant"]
    chartist_score = parallel_results["chartist"]

    if is_error(quant_score):
        quant_score = quant._default_score(stock_name, str(quant_score))
    if is_error(chartist_score):
        chartist_score = chartist._default_score(stock_code, str(chartist_score))

    _publish_progress(task_id, "quant", "completed", f"financial: {quant_score.grade}", 1.0)
    _publish_progress(task_id, "chartist", "completed", f"technical: {chartist_score.signal}", 1.0)

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


def _execute_full(task_id: str, stock_name: str, stock_code: str, max_retries: int) -> dict[str, Any]:
    from src.agents.graph import run_stock_analysis

    _publish_progress(task_id, "analyst", "started", "analyst running", 0.1)
    _publish_progress(task_id, "quant", "started", "quant running", 0.1)
    _publish_progress(task_id, "chartist", "started", "chartist running", 0.1)

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
        _publish_progress(
            task_id,
            "analyst",
            "completed",
            f"analyst: {getattr(analyst_score, 'hegemony_grade', '?')}",
            1.0,
        )
    if quant_score:
        _publish_progress(task_id, "quant", "completed", f"quant: {quant_score.grade}", 1.0)
    if chartist_score:
        _publish_progress(task_id, "chartist", "completed", f"chartist: {chartist_score.signal}", 1.0)
    if final_decision:
        _publish_progress(task_id, "risk_manager", "completed", f"decision: {final_decision.action.value}", 1.0)

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


def _score_to_dict(score: Any) -> dict[str, Any]:
    if score is None:
        return {}
    if hasattr(score, "__dict__"):
        return {key: value for key, value in score.__dict__.items() if not key.startswith("_")}
    return {}


def _decision_to_dict(decision: Any) -> dict[str, Any]:
    if decision is None:
        return {}
    return {
        "action": decision.action.value if hasattr(decision.action, "value") else str(decision.action),
        "confidence": getattr(decision, "confidence", 0),
        "risk_level": decision.risk_level.value
        if hasattr(decision.risk_level, "value")
        else str(decision.risk_level),
        "total_score": getattr(decision, "total_score", 0),
        "summary": getattr(decision, "summary", ""),
        "key_catalysts": getattr(decision, "key_catalysts", []),
        "risk_factors": getattr(decision, "risk_factors", []),
        "detailed_reasoning": getattr(decision, "detailed_reasoning", ""),
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "HQA AI Server", "port": 8001}


@app.post("/analyze", status_code=202)
async def analyze(request: AnalyzeRequest) -> dict[str, str]:
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
async def get_analyze_result(task_id: str) -> dict[str, Any]:
    try:
        import redis

        redis_client = redis.from_url(_get_redis_url())
        data = redis_client.get(f"hqa:result:{task_id}")
        if data:
            return json.loads(data)
    except Exception:
        pass

    result = _results.get(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return result


@app.post("/suggest")
async def suggest(request: SuggestRequest) -> dict[str, Any]:
    loop = asyncio.get_event_loop()

    def _run() -> dict[str, Any]:
        from src.agents.llm_config import get_instruct_llm

        llm = get_instruct_llm()
        prompt = f"""You are validating whether a query is suitable for a Korean stock analysis assistant.

The system can answer:
1. Individual Korean stock analysis
2. Realtime price lookup
3. Sector/theme analysis
4. Stock comparison

User query: "{request.query}"

Return JSON with:
{{
  "is_answerable": true,
  "corrected_query": null,
  "suggestions": [],
  "reason": null
}}
"""
        try:
            response = llm.invoke(prompt)
            text = response.content
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception as exc:
            logger.warning("Suggestion generation failed: %s", exc)
        return {
            "is_answerable": True,
            "corrected_query": None,
            "suggestions": [],
            "reason": None,
        }

    result = await loop.run_in_executor(None, _run)
    return {"original_query": request.query, **result}
