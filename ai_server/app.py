"""
HQA AI Server - AI 분석 런타임 서버

포트: 8001
역할: CPU/GPU 집약적인 LLM 추론, 런타임 시그널 생성, 데이터 조회 보조 API
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from contextvars import copy_context
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

# 프로젝트 루트를 sys.path에 추가 (src/ 패키지 접근용)
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import get_env_status, get_settings, load_project_env
from src.utils.llm_queue import LLMTaskPriority, llm_task_priority

load_project_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 인메모리 결과 캐시 (Redis 폴백용) ──
_results: OrderedDict[str, Dict[str, Any]] = OrderedDict()
_MAX_CACHE = 500
_runtime_tasks: OrderedDict[str, Dict[str, Any]] = OrderedDict()


def _require_internal_runtime_token(x_hqa_internal_token: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("HQA_INTERNAL_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Internal runtime authentication is not configured")
    if x_hqa_internal_token is None:
        raise HTTPException(status_code=401, detail="Internal runtime token required")
    if not secrets.compare_digest(x_hqa_internal_token, expected):
        raise HTTPException(status_code=403, detail="Invalid internal runtime token")


@lru_cache(maxsize=1)
def _analysis_scheduler():
    from src.runner.analysis_scheduler import AnalysisScheduler, BackendAutoTradeTargetClient
    from src.runner.shared_analysis import get_runtime_analysis_service
    return AnalysisScheduler(backend_client=BackendAutoTradeTargetClient(),
                             analysis_service=get_runtime_analysis_service())


def _runtime_port() -> int:
    raw_port = os.getenv("PORT", "8001").strip()
    try:
        return int(raw_port)
    except ValueError:
        return 8001


# ──────────────────────────────────────────────
# 라이프사이클
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🤖 HQA AI Server 시작 (port %s)", _runtime_port())
    yield
    logger.info("🛑 HQA AI Server 종료")


# ──────────────────────────────────────────────
# 앱 생성
# ──────────────────────────────────────────────

app = FastAPI(
    title="HQA AI Server",
    description="AI 분석 런타임 및 시그널 생성 서버",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        raw_body = (await request.body()).decode("utf-8", errors="replace")
    except Exception:
        raw_body = "<unavailable>"
    logger.warning(
        "[422] %s %s — errors=%s body=%s",
        request.method,
        request.url.path,
        exc.errors(),
        raw_body,
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


# ──────────────────────────────────────────────
# 스키마
# ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class SuggestRequest(BaseModel):
    query: str


class StockPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock_code: str = Field(pattern=r"^[0-9]{6}$")


class BacktestResultRequest(BaseModel):
    """Precomputed backtest result submitted by a CLI/worker/backend caller."""

    model_config = ConfigDict(extra="allow")

    task_id: str
    theme: str = "AI"
    theme_key: str = "ai"
    status: str = "completed"
    period: Dict[str, Any] = Field(default_factory=dict)
    strategy: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    leaders: List[Dict[str, Any]] = Field(default_factory=list)
    predictions: List[Dict[str, Any]] = Field(default_factory=list)
    positions: List[Dict[str, Any]] = Field(default_factory=list)
    trades: List[Dict[str, Any]] = Field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = Field(default_factory=list)
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MultiThemeTradeRequest(BaseModel):
    user_id: Optional[str] = None
    investor_profile: Optional[Dict[str, Any]] = None
    candidate_limit: int = 5
    per_theme_top_n: int = 3
    top_n: int = 3
    min_leader_score: Optional[int] = None
    min_confidence: Optional[int] = None
    max_risk_level: Optional[str] = None
    buy_only: bool = True
    strategy_profile: str = "default"
    include_theme_keys: Optional[List[str]] = None
    exclude_theme_keys: Optional[List[str]] = None
    config_path: str = "config/watchlist.yaml"
    data_dir: Optional[str] = None
    save_report: bool = True


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


def _normalize_backtest_result(request: BacktestResultRequest) -> Dict[str, Any]:
    payload = request.model_dump(mode="json")
    payload["task_id"] = request.task_id.strip()
    payload["mode"] = "backtest"
    payload["result_type"] = "backtest"
    payload.setdefault("status", "completed")
    payload["received_at"] = datetime.now().isoformat()
    return payload


def _trim_runtime_tasks() -> None:
    while len(_runtime_tasks) > _MAX_CACHE:
        _runtime_tasks.popitem(last=False)


def _new_runtime_task(operation: str) -> str:
    if len(_runtime_tasks) >= _MAX_CACHE:
        finished = next((key for key, task in _runtime_tasks.items()
                         if task["status"] in {"completed", "failed"}), None)
        if finished is None:
            raise HTTPException(status_code=503, detail="Runtime task capacity exceeded")
        del _runtime_tasks[finished]
    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    _runtime_tasks[task_id] = {
        "task_id": task_id,
        "operation": operation,
        "status": "queued",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "failed_at": None,
        "error": None,
        "result": None,
    }
    _trim_runtime_tasks()
    return task_id


async def _run_runtime_task(task_id: str, fn) -> None:
    task = _runtime_tasks[task_id]
    task["status"] = "running"
    task["started_at"] = datetime.now(timezone.utc).isoformat()
    loop = asyncio.get_event_loop()
    try:
        with llm_task_priority(LLMTaskPriority.RUNTIME):
            context = copy_context()
            result = await loop.run_in_executor(None, context.run, fn)
        task["status"] = "completed"
        task["completed_at"] = datetime.now(timezone.utc).isoformat()
        task["result"] = result
    except Exception as exc:
        logger.exception("runtime task failed: %s", task_id)
        task["status"] = "failed"
        task["failed_at"] = datetime.now(timezone.utc).isoformat()
        task["error"] = str(exc)


def _submit_runtime_task(operation: str, fn) -> Dict[str, Any]:
    task_id = _new_runtime_task(operation)
    asyncio.create_task(_run_runtime_task(task_id, fn))
    return {
        "task_id": task_id,
        "operation": operation,
        "status": "queued",
        "result_url": f"/runtime/tasks/{task_id}",
    }


# ──────────────────────────────────────────────
# 엔드포인트
# ──────────────────────────────────────────────


def _load_order_logs(date: Optional[str], limit: int) -> List[Dict[str, Any]]:
    orders_dir = get_settings().orders_dir
    if not orders_dir.exists():
        return []

    if date:
        date_dirs = [orders_dir / date]
    else:
        date_dirs = sorted(
            [path for path in orders_dir.iterdir() if path.is_dir()],
            key=lambda path: path.name,
            reverse=True,
        )

    rows: List[Dict[str, Any]] = []
    for date_dir in date_dirs:
        log_file = date_dir / "orders.jsonl"
        if not log_file.exists():
            continue
        with log_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)

    rows.sort(key=lambda row: str(row.get("timestamp", "")), reverse=True)
    return rows[:limit]


def _run_multi_theme_trade(request: MultiThemeTradeRequest) -> Dict[str, Any]:
    from src.runner import MultiThemeLeaderTradingRunner
    from src.runner.shared_analysis import get_runtime_analysis_service
    from src.runner.trade_signal_submitter import submit_trade_signals

    runner = MultiThemeLeaderTradingRunner(
        config_path=request.config_path,
        data_dir=request.data_dir,
        analysis_service=get_runtime_analysis_service(request.config_path, request.data_dir),
    )
    result = runner.run_all(
        candidate_limit=max(1, int(request.candidate_limit)),
        per_theme_top_n=max(1, int(request.per_theme_top_n)),
        top_n=max(1, int(request.top_n)),
        execute=False,
        min_leader_score=request.min_leader_score,
        min_confidence=request.min_confidence,
        max_risk_level=request.max_risk_level,
        buy_only=bool(request.buy_only),
        strategy_profile=request.strategy_profile,
        include_theme_keys=request.include_theme_keys,
        exclude_theme_keys=request.exclude_theme_keys,
        save_report=bool(request.save_report),
        investor_profile=request.investor_profile,
        user_id=request.user_id,
    )
    if request.user_id:
        if result.get("status") == "completed":
            result["signal_submission"] = submit_trade_signals(
                user_id=request.user_id,
                result=result,
            )
        else:
            result["signal_submission"] = {
                "submitted": 0,
                "failed": 1,
                "error": result.get("error") or "analysis_not_completed",
            }
    return result

# ──────────────────────────────────────────────
# 종목 자료 (뉴스 · 공시)
# 수집기가 data/raw/{source}/{theme}.jsonl 로 저장한 원본을 stock_code로 필터링.
# 한 종목이 여러 테마에 속할 수 있으므로 모든 jsonl을 스캔.
# ──────────────────────────────────────────────

from src.config.settings import get_data_dir as _hqa_get_data_dir

# jsonl 캐시: (source, file_path) → (mtime, list[record])
# 디스크 I/O를 줄이고 핫 종목 조회를 빠르게 함. mtime 바뀌면 invalidate.
_jsonl_cache: Dict[str, Any] = {}


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _jsonl_cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    _jsonl_cache[str(path)] = (mtime, rows)
    return rows


def _collect_records_for_stock(source_dir: str, stock_code: str) -> List[Dict[str, Any]]:
    """
    data/raw/{source_dir}/*.jsonl 전부 스캔 → stock_code 일치 record만 모음.
    파일은 테마별로 묶여있고 한 종목이 여러 테마에 속할 수 있어서 합집합 필요.
    """
    base = _hqa_get_data_dir() / "raw" / source_dir
    if not base.exists():
        return []
    matched: List[Dict[str, Any]] = []
    for jsonl_path in base.glob("*.jsonl"):
        for record in _load_jsonl(jsonl_path):
            # stock_code는 record 본체 또는 metadata에 들어있을 수 있음
            code = record.get("stock_code")
            if not code:
                meta = record.get("metadata") or {}
                code = meta.get("stock_code") if isinstance(meta, dict) else None
            if code == stock_code:
                matched.append(record)
    return matched


def _record_sort_key(record: Dict[str, Any]) -> str:
    """발행일 우선, 없으면 수집일. 문자열 비교(ISO/yyyy-mm-dd 호환)."""
    published = record.get("published_at") or ""
    if published:
        return str(published)
    meta = record.get("metadata") or {}
    if isinstance(meta, dict):
        return str(meta.get("collected_at") or "")
    return ""


@app.get("/stocks/{stock_code}/news")
async def stock_news(stock_code: str, limit: int = Query(20, ge=1, le=100)):
    try:
        records = _collect_records_for_stock("news", stock_code)
    except Exception as exc:
        logger.warning("stock_news failed for %s: %s", stock_code, exc)
        return {"items": [], "error": str(exc)}
    records.sort(key=_record_sort_key, reverse=True)
    items = []
    seen_urls = set()
    for r in records[: limit * 2]:  # dedupe 후에도 limit 채우도록 여유
        url = r.get("url") or ""
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        items.append({
            "stockCode": r.get("stock_code") or (meta.get("stock_code") if meta else None),
            "stockName": r.get("stock_name") or (meta.get("stock_name") if meta else None),
            "title": r.get("title"),
            "summary": (meta or {}).get("summary"),
            "source": (meta or {}).get("press"),
            "url": url,
            "publishedAt": r.get("published_at"),
            "createdAt": (meta or {}).get("collected_at"),
        })
        if len(items) >= limit:
            break
    return {"items": items}


@app.get("/stocks/{stock_code}/disclosures")
async def stock_disclosures(stock_code: str, limit: int = Query(20, ge=1, le=100)):
    try:
        records = _collect_records_for_stock("dart", stock_code)
    except Exception as exc:
        logger.warning("stock_disclosures failed for %s: %s", stock_code, exc)
        return {"items": [], "error": str(exc)}
    records.sort(key=_record_sort_key, reverse=True)
    items = []
    seen_keys = set()
    for r in records[: limit * 2]:
        meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        rcept = (meta or {}).get("rcept_no") or r.get("url") or ""
        if rcept and rcept in seen_keys:
            continue
        seen_keys.add(rcept)
        items.append({
            "stockCode": r.get("stock_code") or (meta.get("stock_code") if meta else None),
            "stockName": r.get("stock_name") or (meta.get("stock_name") if meta else None),
            "reportName": (meta or {}).get("report_nm") or r.get("title"),
            "receiptNo": (meta or {}).get("rcept_no"),
            "receiptDate": r.get("published_at"),
            "submitter": (meta or {}).get("flr_nm") or (meta or {}).get("corp_name"),
            "url": r.get("url"),
            "createdAt": (meta or {}).get("collected_at"),
        })
        if len(items) >= limit:
            break
    return {"items": items}


@app.get("/health")
async def health():
    settings = get_settings()
    env_status = get_env_status()
    return {
        "status": "ok",
        "service": "HQA AI Server",
        "port": _runtime_port(),
        "data_dir": str(settings.data_dir),
        "data_dir_exists": settings.data_dir.exists(),
        "env_loaded": env_status.loaded,
        "env_file": str(env_status.path) if env_status.path else None,
        "env_message": env_status.message,
    }


@app.get("/trading/orders")
async def list_trading_orders(
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD 형식"),
    limit: int = Query(default=50, ge=1, le=500),
):
    rows = _load_order_logs(date=date, limit=limit)
    return {
        "status": "ok",
        "date": date,
        "count": len(rows),
        "orders": rows,
    }


@app.post("/runtime/multi-theme-trade", status_code=202, dependencies=[Depends(_require_internal_runtime_token)])
async def runtime_multi_theme_trade(request: MultiThemeTradeRequest):
    return _submit_runtime_task("multi_theme_trade", lambda: _run_multi_theme_trade(request))


@app.post("/runtime/stock-preview", status_code=202, dependencies=[Depends(_require_internal_runtime_token)])
async def runtime_stock_preview(request: StockPreviewRequest):
    def run():
        from src.runner.shared_analysis import get_runtime_analysis_service
        return get_runtime_analysis_service().preview_stock(request.stock_code)
    return _submit_runtime_task("stock_preview", run)


@app.post("/internal/runtime/analysis-cycle", status_code=202, dependencies=[Depends(_require_internal_runtime_token)])
async def internal_analysis_cycle():
    return _submit_runtime_task("analysis_cycle", lambda: _analysis_scheduler().run_once())


@app.get("/runtime/tasks/{task_id}", dependencies=[Depends(_require_internal_runtime_token)])
async def runtime_task(task_id: str):
    task = _runtime_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"runtime task not found: {task_id}")
    return task


async def _get_stored_result(task_id: str):
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


@app.post("/backtest/results", status_code=201)
async def submit_backtest_result(request: BacktestResultRequest):
    """Store a completed backtest result submitted by a runner or backend job."""
    task_id = request.task_id.strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id는 비어 있을 수 없습니다.")

    result = _normalize_backtest_result(request)
    _store_result(task_id, result)
    _publish_progress(task_id, "backtest", "completed", "백테스트 결과 저장 완료", 1.0)
    return {
        "task_id": task_id,
        "status": "stored",
        "mode": "backtest",
        "result_url": f"/backtest/results/{task_id}",
    }


@app.get("/backtest/results/{task_id}")
async def get_backtest_result(task_id: str):
    """Fetch a stored backtest result."""
    return await _get_stored_result(task_id)


@app.post("/chat", dependencies=[Depends(_require_internal_runtime_token)])
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


@app.post("/suggest", dependencies=[Depends(_require_internal_runtime_token)])
async def suggest(request: SuggestRequest):
    """쿼리 제안 (Answerability Check)"""
    loop = asyncio.get_event_loop()

    def _run():
        from src.agents.llm_config import get_chartist_llm
        llm = get_chartist_llm()
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
