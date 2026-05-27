"""
HQA AI Server - AI 에이전트 & RAG 전용 서버

포트: 8001
역할: CPU/GPU 집약적인 LLM 추론, LangGraph 워크플로우, RAG 파이프라인 실행
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

# 프로젝트 루트를 sys.path에 추가 (src/ 패키지 접근용)
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import get_env_status, get_settings, load_project_env

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
_runtime_loop_lock = threading.Lock()
_runtime_loop_state: Dict[str, Any] = {
    "status": "stopped",
    "task_id": None,
    "operation": "multi_theme_trade_loop",
    "started_at": None,
    "stopped_at": None,
    "error": None,
}
_runtime_loop_stop_event: Optional[threading.Event] = None


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


class ThemeAnalyzeRequest(BaseModel):
    task_id: str
    theme: str
    theme_key: str = ""
    candidate_limit: int = 5
    top_n: int = 3


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


class TradeDecisionPayload(BaseModel):
    total_score: int
    action: str
    action_code: str = ""
    confidence: int = 0
    risk_level: str = "MEDIUM"
    risk_level_code: str = ""
    summary: str = ""
    key_catalysts: List[str] = Field(default_factory=list)
    risk_factors: List[str] = Field(default_factory=list)
    detailed_reasoning: str = ""
    position_size: str = "0%"
    entry_strategy: str = ""
    exit_strategy: str = ""
    stop_loss: str = ""
    signal_alignment: str = ""
    contrarian_view: str = ""
    validation_status: str = "disabled"
    validation_summary: str = ""
    validator_model: str = ""
    primary_model: str = ""
    validator_action: str = ""
    validator_confidence: int = 0


class TradeDecisionRequest(BaseModel):
    stock_name: str
    stock_code: str
    final_decision: TradeDecisionPayload
    current_price: Optional[int] = None
    quantity: int = 0
    dry_run_override: Optional[bool] = None
    trading_enabled_override: Optional[bool] = None


class ThemeTradeRequest(BaseModel):
    theme: str
    theme_key: str = ""
    candidate_limit: int = 5
    top_n: int = 3
    execute_top_n: int = 1
    execute: bool = False
    preview: bool = True
    min_leader_score: Optional[int] = None
    strategy_profile: str = "default"
    config_path: str = "config/watchlist.yaml"
    data_dir: Optional[str] = None
    paper: bool = False
    dry_run: bool = True
    save_report: bool = True
    dry_run_override: Optional[bool] = None
    trading_enabled_override: Optional[bool] = None
    account_type_override: Optional[str] = None


class ThemeTradeReportRequest(BaseModel):
    report_path: str
    execute_top_n: Optional[int] = 1
    execute: bool = False
    preview: bool = True
    config_path: str = "config/watchlist.yaml"
    data_dir: Optional[str] = None
    paper: bool = False
    dry_run: bool = True
    save_report: bool = True
    dry_run_override: Optional[bool] = None
    trading_enabled_override: Optional[bool] = None
    account_type_override: Optional[str] = None


class MultiThemeTradeRequest(BaseModel):
    candidate_limit: int = 5
    per_theme_top_n: int = 3
    top_n: int = 3
    execute: bool = False
    preview: bool = True
    min_leader_score: Optional[int] = None
    min_confidence: Optional[int] = None
    max_risk_level: Optional[str] = None
    buy_only: bool = True
    strategy_profile: str = "default"
    include_theme_keys: Optional[List[str]] = None
    exclude_theme_keys: Optional[List[str]] = None
    config_path: str = "config/watchlist.yaml"
    data_dir: Optional[str] = None
    paper: bool = False
    dry_run: bool = True
    save_report: bool = True
    dry_run_override: Optional[bool] = None
    trading_enabled_override: Optional[bool] = None
    account_type_override: Optional[str] = None


class MultiThemeLoopStartRequest(MultiThemeTradeRequest):
    trade_interval_minutes: int = 60
    market_hours_only: bool = True
    collect_interval_minutes: Optional[int] = None
    collection_command: Optional[str] = None
    long_plan_time: str = "08:00"
    long_plan_window_minutes: int = 40
    long_check_interval_minutes: int = 5
    poll_seconds: int = 30


class AutonomousRunRequest(BaseModel):
    config_path: str = "config/watchlist.yaml"
    dry_run: bool = True
    loop: bool = False


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
    task_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
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
    task["started_at"] = datetime.now().isoformat()
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, fn)
        task["status"] = "completed"
        task["completed_at"] = datetime.now().isoformat()
        task["result"] = result
    except Exception as exc:
        logger.exception("runtime task failed: %s", task_id)
        task["status"] = "failed"
        task["failed_at"] = datetime.now().isoformat()
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


def _resolve_runner_overrides(
    *,
    execute: bool,
    paper: bool,
    dry_run: bool,
    dry_run_override: Optional[bool],
    trading_enabled_override: Optional[bool],
    account_type_override: Optional[str],
) -> Dict[str, Any]:
    account_type = account_type_override or "paper"
    resolved_dry_run = True
    resolved_trading_enabled = True

    if execute:
        if dry_run_override is False and not paper:
            raise HTTPException(status_code=400, detail="dry_run_override=false requires paper=true")
        if account_type == "real" and dry_run_override is False:
            raise HTTPException(status_code=400, detail="real trading is not enabled through runtime API")
        if paper:
            resolved_dry_run = False
            account_type = "paper"
        elif dry_run or dry_run_override is True:
            resolved_dry_run = True
        else:
            raise HTTPException(status_code=400, detail="execute requires paper=true or dry_run=true")

    if dry_run_override is not None:
        resolved_dry_run = bool(dry_run_override)
    if trading_enabled_override is not None:
        resolved_trading_enabled = bool(trading_enabled_override)

    return {
        "dry_run_override": resolved_dry_run,
        "trading_enabled_override": resolved_trading_enabled,
        "account_type_override": account_type,
    }


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


async def _run_theme_analysis_background(
    task_id: str,
    theme: str,
    theme_key: str,
    candidate_limit: int,
    top_n: int,
):
    """asyncio 백그라운드에서 테마 주도주 선별 실행"""
    loop = asyncio.get_event_loop()
    try:
        _publish_progress(task_id, "system", "started", f"{theme} 테마 분석 시작", 0.0)

        result = await loop.run_in_executor(
            None,
            _execute_theme,
            task_id,
            theme,
            theme_key,
            candidate_limit,
            top_n,
        )

        _store_result(task_id, {**result, "status": "completed"})
        _publish_progress(task_id, "system", "completed", "테마 분석 완료", 1.0)
    except Exception as e:
        logger.exception(f"테마 분석 실패: {task_id}")
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


def _execute_theme(
    task_id: str,
    theme: str,
    theme_key: str,
    candidate_limit: int,
    top_n: int,
) -> dict:
    """테마 전체 스캔 후 주도주 선별"""
    from src.agents import ThemeLeaderOrchestrator

    _publish_progress(task_id, "theme_orchestrator", "started", "후보군 추출 및 평가 중...", 0.1)

    orchestrator = ThemeLeaderOrchestrator()
    result = orchestrator.run(
        theme=theme,
        theme_key=theme_key,
        candidate_limit=candidate_limit,
        top_n=top_n,
    )

    if result.get("status") != "success":
        return {
            "task_id": task_id,
            "mode": "theme",
            "theme": theme,
            "theme_key": theme_key,
            "status": "failed",
            "error": result.get("message", "테마 분석 실패"),
            "completed_at": datetime.now().isoformat(),
        }

    _publish_progress(task_id, "theme_orchestrator", "completed", "주도주 선별 완료", 1.0)

    leaders = []
    for row in result.get("leaders", []):
        candidate = row.get("candidate", {})
        decision = row.get("final_decision", {})
        leaders.append(
            {
                "stock_name": candidate.get("stock_name"),
                "stock_code": candidate.get("stock_code"),
                "leader_score": row.get("leader_score"),
                "seed_score": candidate.get("seed_score"),
                "action": decision.get("action"),
                "confidence": decision.get("confidence"),
                "summary": decision.get("summary"),
                "risk_level": decision.get("risk_level"),
                "key_catalysts": decision.get("key_catalysts", []),
                "risk_factors": decision.get("risk_factors", []),
            }
        )

    return {
        "task_id": task_id,
        "mode": "theme",
        "theme": theme,
        "theme_key": result.get("theme_key", theme_key),
        "candidate_limit": candidate_limit,
        "top_n": top_n,
        "candidate_count": result.get("candidate_count", 0),
        "evaluated_count": result.get("evaluated_count", 0),
        "leaders": leaders,
        "summary": result.get("summary", ""),
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
        "action_code": decision.action.name if hasattr(decision.action, "name") else str(decision.action),
        "confidence": getattr(decision, "confidence", 0),
        "risk_level": decision.risk_level.value if hasattr(decision.risk_level, "value") else str(decision.risk_level),
        "risk_level_code": decision.risk_level.name if hasattr(decision.risk_level, "name") else str(decision.risk_level),
        "total_score": getattr(decision, "total_score", 0),
        "summary": getattr(decision, "summary", ""),
        "key_catalysts": getattr(decision, "key_catalysts", []),
        "risk_factors": getattr(decision, "risk_factors", []),
        "detailed_reasoning": getattr(decision, "detailed_reasoning", ""),
    }


# ──────────────────────────────────────────────
# 엔드포인트
# ──────────────────────────────────────────────


def _load_watchlist_config() -> Dict[str, Any]:
    settings = get_settings()
    config_path = settings.project_root / "config" / "watchlist.yaml"
    if not config_path.exists():
        return {"config_path": str(config_path), "watchlist": [], "trading": {}}

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"PyYAML 미설치: {exc}") from exc

    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    config["config_path"] = str(config_path)
    return config


def _build_trade_executor(
    *,
    dry_run_override: Optional[bool] = None,
    trading_enabled_override: Optional[bool] = None,
):
    from src.runner.trade_executor import TradeExecutor

    config = _load_watchlist_config()
    trading = dict(config.get("trading") or {})
    if dry_run_override is not None:
        trading["dry_run"] = dry_run_override
    if trading_enabled_override is not None:
        trading["enabled"] = trading_enabled_override
    return TradeExecutor(trading), config


def _coerce_enum(raw: str, enum_cls, field_name: str):
    value = str(raw or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail=f"{field_name} 값이 비어 있습니다.")

    direct = enum_cls.__members__.get(value)
    if direct is not None:
        return direct

    upper = enum_cls.__members__.get(value.upper())
    if upper is not None:
        return upper

    for member in enum_cls:
        if member.value == value:
            return member

    allowed = [member.name for member in enum_cls]
    raise HTTPException(
        status_code=422,
        detail=f"{field_name} 값이 올바르지 않습니다: {value}. 허용값: {allowed}",
    )


def _build_final_decision(stock_name: str, stock_code: str, payload: TradeDecisionPayload):
    from src.agents.risk_manager import FinalDecision, InvestmentAction, RiskLevel

    action_raw = payload.action_code or payload.action
    risk_level_raw = payload.risk_level_code or payload.risk_level

    return FinalDecision(
        stock_name=stock_name,
        stock_code=stock_code,
        total_score=max(0, min(100, int(payload.total_score))),
        action=_coerce_enum(action_raw, InvestmentAction, "action"),
        confidence=max(0, min(100, int(payload.confidence))),
        risk_level=_coerce_enum(risk_level_raw, RiskLevel, "risk_level"),
        risk_factors=list(payload.risk_factors),
        position_size=payload.position_size,
        entry_strategy=payload.entry_strategy,
        exit_strategy=payload.exit_strategy,
        stop_loss=payload.stop_loss,
        signal_alignment=payload.signal_alignment,
        key_catalysts=list(payload.key_catalysts),
        contrarian_view=payload.contrarian_view,
        summary=payload.summary,
        detailed_reasoning=payload.detailed_reasoning,
        validation_status=payload.validation_status,
        validation_summary=payload.validation_summary,
        validator_model=payload.validator_model,
        primary_model=payload.primary_model,
        validator_action=payload.validator_action,
        validator_confidence=max(0, min(100, int(payload.validator_confidence))),
    )


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


def _run_theme_trade(request: ThemeTradeRequest) -> Dict[str, Any]:
    from src.runner import ThemeLeaderTradingRunner

    overrides = _resolve_runner_overrides(
        execute=request.execute,
        paper=request.paper,
        dry_run=request.dry_run,
        dry_run_override=request.dry_run_override,
        trading_enabled_override=request.trading_enabled_override,
        account_type_override=request.account_type_override,
    )
    runner = ThemeLeaderTradingRunner(
        config_path=request.config_path,
        data_dir=request.data_dir,
        **overrides,
    )
    return runner.run_once(
        theme=request.theme,
        theme_key=request.theme_key,
        candidate_limit=max(1, int(request.candidate_limit)),
        top_n=max(1, int(request.top_n)),
        execute_top_n=max(0, int(request.execute_top_n)),
        execute=bool(request.execute),
        min_leader_score=request.min_leader_score,
        strategy_profile=request.strategy_profile,
        save_report=bool(request.save_report),
    )


def _run_theme_trade_report(request: ThemeTradeReportRequest) -> Dict[str, Any]:
    from src.runner import ThemeLeaderTradingRunner

    overrides = _resolve_runner_overrides(
        execute=request.execute,
        paper=request.paper,
        dry_run=request.dry_run,
        dry_run_override=request.dry_run_override,
        trading_enabled_override=request.trading_enabled_override,
        account_type_override=request.account_type_override,
    )
    runner = ThemeLeaderTradingRunner(
        config_path=request.config_path,
        data_dir=request.data_dir,
        **overrides,
    )
    return runner.run_from_report(
        report_path=request.report_path,
        execute_top_n=request.execute_top_n,
        execute=bool(request.execute),
        save_report=bool(request.save_report),
    )


def _run_multi_theme_trade(request: MultiThemeTradeRequest) -> Dict[str, Any]:
    from src.runner import MultiThemeLeaderTradingRunner

    overrides = _resolve_runner_overrides(
        execute=request.execute,
        paper=request.paper,
        dry_run=request.dry_run,
        dry_run_override=request.dry_run_override,
        trading_enabled_override=request.trading_enabled_override,
        account_type_override=request.account_type_override,
    )
    runner = MultiThemeLeaderTradingRunner(
        config_path=request.config_path,
        data_dir=request.data_dir,
        **overrides,
    )
    return runner.run_all(
        candidate_limit=max(1, int(request.candidate_limit)),
        per_theme_top_n=max(1, int(request.per_theme_top_n)),
        top_n=max(1, int(request.top_n)),
        execute=bool(request.execute),
        min_leader_score=request.min_leader_score,
        min_confidence=request.min_confidence,
        max_risk_level=request.max_risk_level,
        buy_only=bool(request.buy_only),
        strategy_profile=request.strategy_profile,
        include_theme_keys=request.include_theme_keys,
        exclude_theme_keys=request.exclude_theme_keys,
        save_report=bool(request.save_report),
    )


def _run_autonomous_once(request: AutonomousRunRequest) -> Dict[str, Any]:
    if request.loop:
        raise ValueError("autonomous loop mode is not controllable through this endpoint; use one-shot mode")

    from src.runner.autonomous_runner import AutonomousRunner

    runner = AutonomousRunner(
        config_path=request.config_path,
        dry_run_override=True if request.dry_run else None,
    )
    results = runner.run_once()
    return {
        "status": "success",
        "mode": "once",
        "config_path": request.config_path,
        "dry_run": bool(request.dry_run),
        "result_count": len(results),
        "results": results,
        "completed_at": datetime.now().isoformat(),
    }


def _run_multi_theme_loop(task_id: str, request: MultiThemeLoopStartRequest, stop_event: threading.Event) -> None:
    global _runtime_loop_state
    from src.runner import MultiThemeLeaderTradingRunner
    from src.runner.multi_theme_scheduler import MultiThemeScheduler

    try:
        overrides = _resolve_runner_overrides(
            execute=request.execute,
            paper=request.paper,
            dry_run=request.dry_run,
            dry_run_override=request.dry_run_override,
            trading_enabled_override=request.trading_enabled_override,
            account_type_override=request.account_type_override,
        )
        trade_runner = MultiThemeLeaderTradingRunner(
            config_path=request.config_path,
            data_dir=request.data_dir,
            **overrides,
        )
        scheduler = MultiThemeScheduler(
            trade_runner=trade_runner,
            short_interval_minutes=request.trade_interval_minutes,
            short_market_hours_only=request.market_hours_only,
            long_plan_time=request.long_plan_time,
            long_plan_window_minutes=request.long_plan_window_minutes,
            long_trigger_check_minutes=request.long_check_interval_minutes,
            long_market_hours_only=request.market_hours_only,
            collect_interval_minutes=request.collect_interval_minutes,
            collect_command=request.collection_command,
            poll_seconds=request.poll_seconds,
            stop_event=stop_event,
        )
        scheduler.run_loop(
            candidate_limit=max(1, int(request.candidate_limit)),
            per_theme_top_n=max(1, int(request.per_theme_top_n)),
            short_top_n=max(1, int(request.top_n)),
            long_top_n=max(1, int(request.top_n)),
            execute=bool(request.execute),
            min_leader_score=request.min_leader_score,
            min_confidence=request.min_confidence,
            max_risk_level=request.max_risk_level,
            short_strategy_profile="short",
            long_strategy_profile="long",
            include_theme_keys=request.include_theme_keys,
            exclude_theme_keys=request.exclude_theme_keys,
        )
        with _runtime_loop_lock:
            _runtime_loop_state.update({
                "status": "stopped",
                "stopped_at": datetime.now().isoformat(),
                "error": None,
            })
    except Exception as exc:
        logger.exception("multi-theme loop failed")
        with _runtime_loop_lock:
            _runtime_loop_state.update({
                "status": "failed",
                "stopped_at": datetime.now().isoformat(),
                "error": str(exc),
            })

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


@app.get("/trading/status")
async def trading_status():
    executor, config = _build_trade_executor()
    runtime = executor.get_runtime_config()
    return {
        "status": "ok",
        "config_path": config.get("config_path"),
        "watchlist_count": len(config.get("watchlist", [])),
        "runtime": runtime,
        "orders_dir": str(get_settings().orders_dir),
    }


@app.post("/trading/decision/preview")
async def preview_trade_decision(request: TradeDecisionRequest):
    executor, config = _build_trade_executor(
        dry_run_override=request.dry_run_override,
        trading_enabled_override=request.trading_enabled_override,
    )
    decision = _build_final_decision(
        stock_name=request.stock_name,
        stock_code=request.stock_code,
        payload=request.final_decision,
    )
    preview = executor.preview_decision(
        stock_name=request.stock_name,
        stock_code=request.stock_code,
        decision=decision,
        quantity=request.quantity,
        current_price=request.current_price,
    )
    return {
        "stock": {"name": request.stock_name, "code": request.stock_code},
        "decision": _decision_to_dict(decision),
        "preview": preview,
        "config_path": config.get("config_path"),
    }


@app.post("/trading/decision/execute")
async def execute_trade_decision(request: TradeDecisionRequest):
    executor, config = _build_trade_executor(
        dry_run_override=request.dry_run_override,
        trading_enabled_override=request.trading_enabled_override,
    )
    decision = _build_final_decision(
        stock_name=request.stock_name,
        stock_code=request.stock_code,
        payload=request.final_decision,
    )
    result = executor.execute_decision(
        stock_name=request.stock_name,
        stock_code=request.stock_code,
        decision=decision,
        quantity=request.quantity,
        current_price=request.current_price,
    )
    return {
        "stock": {"name": request.stock_name, "code": request.stock_code},
        "decision": _decision_to_dict(decision),
        "trade": result,
        "runtime": executor.get_runtime_config(),
        "config_path": config.get("config_path"),
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


@app.post("/runtime/theme-trade", status_code=202)
async def runtime_theme_trade(request: ThemeTradeRequest):
    return _submit_runtime_task("theme_trade", lambda: _run_theme_trade(request))


@app.post("/runtime/theme-trade-report", status_code=202)
async def runtime_theme_trade_report(request: ThemeTradeReportRequest):
    return _submit_runtime_task("theme_trade_report", lambda: _run_theme_trade_report(request))


@app.post("/runtime/multi-theme-trade", status_code=202)
async def runtime_multi_theme_trade(request: MultiThemeTradeRequest):
    return _submit_runtime_task("multi_theme_trade", lambda: _run_multi_theme_trade(request))


@app.post("/runtime/autonomous", status_code=202)
async def runtime_autonomous(request: AutonomousRunRequest):
    return _submit_runtime_task("autonomous", lambda: _run_autonomous_once(request))


@app.get("/runtime/tasks/{task_id}")
async def runtime_task(task_id: str):
    task = _runtime_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"runtime task not found: {task_id}")
    return task


@app.post("/runtime/multi-theme-trade/loop/start", status_code=202)
async def runtime_multi_theme_loop_start(request: MultiThemeLoopStartRequest):
    global _runtime_loop_stop_event
    with _runtime_loop_lock:
        if _runtime_loop_state.get("status") in {"running", "stopping"}:
            raise HTTPException(
                status_code=409,
                detail=f"multi-theme loop is already running: {_runtime_loop_state.get('task_id')}",
            )
        task_id = str(uuid.uuid4())
        stop_event = threading.Event()
        _runtime_loop_stop_event = stop_event
        _runtime_loop_state.update({
            "status": "running",
            "task_id": task_id,
            "operation": "multi_theme_trade_loop",
            "started_at": datetime.now().isoformat(),
            "stopped_at": None,
            "error": None,
        })
        thread = threading.Thread(
            target=_run_multi_theme_loop,
            args=(task_id, request, stop_event),
            name=f"hqa-runtime-loop-{task_id[:8]}",
            daemon=True,
        )
        _runtime_loop_state["thread_name"] = thread.name
        thread.start()
        return dict(_runtime_loop_state)


@app.post("/runtime/multi-theme-trade/loop/stop")
async def runtime_multi_theme_loop_stop():
    global _runtime_loop_stop_event
    with _runtime_loop_lock:
        if _runtime_loop_state.get("status") != "running":
            return {**_runtime_loop_state, "message": "multi-theme loop is not running"}
        if _runtime_loop_stop_event is not None:
            _runtime_loop_stop_event.set()
        _runtime_loop_state["status"] = "stopping"
        _runtime_loop_state["stopped_at"] = datetime.now().isoformat()
        return dict(_runtime_loop_state)


@app.get("/runtime/multi-theme-trade/loop/status")
async def runtime_multi_theme_loop_status():
    with _runtime_loop_lock:
        return dict(_runtime_loop_state)


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
    """Fetch a stored backtest result. Shares storage with /analyze/{task_id}."""
    return await get_analyze_result(task_id)


@app.post("/theme/analyze", status_code=202)
async def analyze_theme(request: ThemeAnalyzeRequest):
    """
    테마 주도주 선별 요청 (비동기)

    즉시 task_id를 반환하고 백그라운드에서 후보 추출 및 멀티 에이전트 평가를 실행합니다.
    결과는 `GET /theme/analyze/{task_id}` 또는 `GET /analyze/{task_id}`로 조회할 수 있습니다.
    """
    asyncio.create_task(
        _run_theme_analysis_background(
            request.task_id,
            request.theme,
            request.theme_key,
            request.candidate_limit,
            request.top_n,
        )
    )
    return {"task_id": request.task_id, "status": "pending", "mode": "theme"}


@app.get("/theme/analyze/{task_id}")
async def get_theme_analyze_result(task_id: str):
    """테마 주도주 선별 결과 조회"""
    return await get_analyze_result(task_id)


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
