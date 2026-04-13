from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


_results: OrderedDict[str, dict[str, Any]] = OrderedDict()
_MAX_CACHE = 256


class SuggestRequest(BaseModel):
    query: str


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("HQA AI Server started")
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "hqa-ai-server",
        "cached_results": len(_results),
        "timestamp": _now_iso(),
    }


@app.post("/analyze")
async def analyze(request: Request) -> dict[str, str]:
    payload: dict[str, Any] = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        body = (await request.body()).decode("utf-8", errors="ignore").strip()
        logger.warning("Analyze request JSON parsing failed. raw_body=%s", body)

    task_id = str(payload.get("task_id", "")).strip()
    stock_name = str(payload.get("stock_name", "")).strip()
    stock_code = str(payload.get("stock_code", "")).strip()
    mode = str(payload.get("mode", "full")).strip().lower() or "full"
    max_retries_raw = payload.get("max_retries", 1)

    try:
        max_retries = int(max_retries_raw)
    except (TypeError, ValueError):
        max_retries = 1

    if not task_id:
        raise HTTPException(status_code=422, detail="task_id is required")
    if not stock_name:
        raise HTTPException(status_code=422, detail="stock_name is required")
    if len(stock_code) != 6 or not stock_code.isdigit():
        raise HTTPException(status_code=422, detail="stock_code must be a 6-digit string")
    if mode not in {"full", "quick"}:
        mode = "full"

    _results[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "mode": mode,
        "stock": {"name": stock_name, "code": stock_code},
        "scores": {},
        "final_decision": {},
        "research_quality": "collecting",
        "quality_warnings": [],
        "errors": {},
    }
    _trim_cache()
    asyncio.create_task(
        _run_analysis_background(
            task_id,
            stock_name,
            stock_code,
            mode,
            max_retries,
        )
    )
    return {"task_id": task_id, "status": "pending"}


@app.get("/analyze/{task_id}")
async def get_analyze_result(task_id: str) -> dict[str, Any]:
    result = _results.get(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return result


@app.post("/suggest")
async def suggest(request: SuggestRequest) -> dict[str, Any]:
    query = request.query.strip()
    if not query:
        return {
            "original_query": request.query,
            "is_answerable": False,
            "corrected_query": None,
            "suggestions": ["005930", "Samsung Electronics", "SK hynix"],
            "reason": "Empty query",
        }

    is_code = query.isdigit() and len(query) == 6
    suggestions = []
    corrected_query = None
    if not is_code and len(query) < 2:
        suggestions = ["005930", "000660", "035420"]
    elif "," in query:
        corrected_query = query.replace(",", " ").strip()

    return {
        "original_query": request.query,
        "is_answerable": True,
        "corrected_query": corrected_query,
        "suggestions": suggestions,
        "reason": None,
    }


@app.post("/recommend")
async def recommend(payload: dict[str, Any]) -> list[dict[str, Any]]:
    _ = payload.get("event_type", "RECOMMEND")
    return [
        {
            "stockCode": "005930",
            "stockName": "Samsung Electronics",
            "quantity": 1,
            "limitPrice": 70000,
        }
    ]


async def _run_analysis_background(
    task_id: str,
    stock_name: str,
    stock_code: str,
    mode: str,
    max_retries: int,
) -> None:
    try:
        _store_partial(task_id, {"status": "running", "research_quality": "running"})
        await asyncio.sleep(0.5)

        if mode.lower() == "quick":
            result = _execute_quick(task_id, stock_name, stock_code)
        else:
            result = _execute_full(task_id, stock_name, stock_code, max_retries)

        result["status"] = "completed"
        result["completed_at"] = _now_iso()
        _store_result(task_id, result)
    except Exception as exc:
        logger.exception("Analysis failed for %s", task_id)
        _store_result(
            task_id,
            {
                "task_id": task_id,
                "status": "failed",
                "mode": mode.lower(),
                "stock": {"name": stock_name, "code": stock_code},
                "scores": {},
                "final_decision": {},
                "research_quality": "failed",
                "quality_warnings": ["AI analysis failed"],
                "completed_at": _now_iso(),
                "errors": {"system": str(exc)},
            },
        )


def _execute_quick(task_id: str, stock_name: str, stock_code: str) -> dict[str, Any]:
    quant = _build_quant_score(stock_name, stock_code)
    chartist = _build_chartist_score(stock_code)

    return {
        "task_id": task_id,
        "mode": "quick",
        "stock": {"name": stock_name, "code": stock_code},
        "scores": {
            "quant": quant,
            "chartist": chartist,
        },
        "final_decision": _build_decision(quant["total_score"], chartist["total_score"], None),
        "research_quality": "mock-quick",
        "quality_warnings": ["Quick mode uses simplified local heuristics."],
        "errors": {},
    }


def _execute_full(task_id: str, stock_name: str, stock_code: str, max_retries: int) -> dict[str, Any]:
    analyst = _build_analyst_score(stock_name, stock_code, max_retries)
    quant = _build_quant_score(stock_name, stock_code)
    chartist = _build_chartist_score(stock_code)

    return {
        "task_id": task_id,
        "mode": "full",
        "stock": {"name": stock_name, "code": stock_code},
        "scores": {
            "analyst": analyst,
            "quant": quant,
            "chartist": chartist,
        },
        "final_decision": _build_decision(
            quant["total_score"],
            chartist["total_score"],
            analyst["total_score"],
        ),
        "research_quality": "mock-full",
        "quality_warnings": [
            "This restored AI server uses deterministic mock scoring.",
        ],
        "errors": {},
    }


def _build_analyst_score(stock_name: str, stock_code: str, max_retries: int) -> dict[str, Any]:
    base = _score_seed(stock_name, stock_code, "analyst")
    total_score = round(35 + (base % 36), 1)
    grade = "A" if total_score >= 62 else "B" if total_score >= 50 else "C"
    return {
        "stock_name": stock_name,
        "stock_code": stock_code,
        "total_score": total_score,
        "hegemony_grade": grade,
        "final_opinion": f"Macro and company-specific signals are stable after {max_retries + 1} pass(es).",
        "confidence": round(0.55 + ((base % 20) / 100), 2),
    }


def _build_quant_score(stock_name: str, stock_code: str) -> dict[str, Any]:
    base = _score_seed(stock_name, stock_code, "quant")
    total_score = round(45 + (base % 46), 1)
    if total_score >= 80:
        grade = "A"
        opinion = "Financial quality and valuation both look strong."
    elif total_score >= 65:
        grade = "B"
        opinion = "Financial signals are acceptable with moderate upside."
    else:
        grade = "C"
        opinion = "Financial profile is mixed and needs caution."
    return {
        "stock_name": stock_name,
        "stock_code": stock_code,
        "total_score": total_score,
        "grade": grade,
        "opinion": opinion,
    }


def _build_chartist_score(stock_code: str) -> dict[str, Any]:
    base = _score_seed(stock_code, "chartist")
    total_score = round(40 + (base % 51), 1)
    if total_score >= 78:
        signal = "BUY"
    elif total_score >= 58:
        signal = "HOLD"
    else:
        signal = "SELL"
    return {
        "stock_code": stock_code,
        "total_score": total_score,
        "signal": signal,
    }


def _build_decision(
    quant_score: float,
    chartist_score: float,
    analyst_score: float | None,
) -> dict[str, Any]:
    components = [quant_score, chartist_score]
    if analyst_score is not None:
        components.append(analyst_score)

    average = sum(components) / len(components)
    if average >= 75:
        action = "BUY"
    elif average >= 58:
        action = "HOLD"
    else:
        action = "SELL"

    return {
        "action": action,
        "confidence": round(min(0.95, max(0.45, average / 100)), 2),
        "summary": f"Composite score {average:.1f} suggests {action.lower()} bias.",
    }


def _score_seed(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _store_partial(task_id: str, patch: dict[str, Any]) -> None:
    current = dict(_results.get(task_id, {}))
    current.update(patch)
    _results[task_id] = current
    _trim_cache()


def _store_result(task_id: str, result: dict[str, Any]) -> None:
    _results[task_id] = result
    _trim_cache()


def _trim_cache() -> None:
    while len(_results) > _MAX_CACHE:
        _results.popitem(last=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8001, reload=False)
