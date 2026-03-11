# 파일: backend/tasks/analysis_tasks.py
"""
Celery 분석 태스크

Redis가 설치된 환경에서 백그라운드로 분석을 수행합니다.
Redis 없이도 AnalysisService의 인메모리 모드로 동작 가능합니다.
"""

import json
import logging
from datetime import datetime

from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="backend.tasks.analysis_tasks.run_analysis_task",
    max_retries=1,
    default_retry_delay=30,
)
def run_analysis_task(self, task_id: str, stock_name: str, stock_code: str, mode: str, max_retries: int = 1):
    """
    전체/빠른 분석 Celery 태스크
    
    진행 상황은 Redis pub/sub으로 AnalysisService에 전달됩니다.
    """
    try:
        _publish_progress(task_id, "system", "started", f"{stock_name} 분석 시작", 0.0)

        if mode == "quick":
            result = _execute_quick(task_id, stock_name, stock_code)
        else:
            result = _execute_full(task_id, stock_name, stock_code, max_retries)

        # 결과를 Redis에 저장
        _store_result(task_id, result)
        _publish_progress(task_id, "system", "completed", "분석 완료", 1.0)

        return {"task_id": task_id, "status": "completed"}

    except Exception as e:
        logger.exception(f"분석 태스크 실패: {task_id}")
        _publish_progress(task_id, "system", "error", f"오류: {str(e)[:200]}", 0.0)
        raise


def _execute_quick(task_id: str, stock_name: str, stock_code: str) -> dict:
    """빠른 분석 실행"""
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
        "mode": "quick",
        "stock": {"name": stock_name, "code": stock_code},
        "quant": _score_to_dict(quant_score),
        "chartist": _score_to_dict(chartist_score),
        "completed_at": datetime.now().isoformat(),
    }


def _execute_full(task_id: str, stock_name: str, stock_code: str, max_retries: int) -> dict:
    """전체 분석 실행"""
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
    if analyst_score:
        _publish_progress(task_id, "analyst", "completed", f"헤게모니: {getattr(analyst_score, 'hegemony_grade', '?')}", 1.0)

    quant_score = scores.get("quant")
    if quant_score:
        _publish_progress(task_id, "quant", "completed", f"재무: {quant_score.grade}", 1.0)

    chartist_score = scores.get("chartist")
    if chartist_score:
        _publish_progress(task_id, "chartist", "completed", f"기술: {chartist_score.signal}", 1.0)

    final_decision = result.get("final_decision")
    if final_decision:
        _publish_progress(task_id, "risk_manager", "completed", f"판단: {final_decision.action.value}", 1.0)

    return {
        "mode": "full",
        "stock": {"name": stock_name, "code": stock_code},
        "analyst": _score_to_dict(analyst_score) if analyst_score else None,
        "quant": _score_to_dict(quant_score) if quant_score else None,
        "chartist": _score_to_dict(chartist_score) if chartist_score else None,
        "final_decision": _decision_to_dict(final_decision) if final_decision else None,
        "research_quality": result.get("research_quality"),
        "quality_warnings": result.get("quality_warnings", []),
        "completed_at": datetime.now().isoformat(),
    }


# ── 헬퍼 ──

def _score_to_dict(score) -> dict:
    """에이전트 스코어를 dict로 변환"""
    if hasattr(score, "__dict__"):
        return {k: v for k, v in score.__dict__.items() if not k.startswith("_")}
    return {}


def _decision_to_dict(decision) -> dict:
    """최종 판단을 dict로 변환"""
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


def _publish_progress(task_id: str, agent: str, status: str, message: str, progress: float):
    """Redis pub/sub으로 진행 상황 전달"""
    try:
        import redis

        r = redis.from_url("redis://localhost:6379/0")
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
    """결과를 Redis에 저장"""
    try:
        import redis

        r = redis.from_url("redis://localhost:6379/0")
        r.setex(
            f"hqa:result:{task_id}",
            3600,  # 1시간 TTL
            json.dumps(result, ensure_ascii=False, default=str),
        )
    except Exception:
        pass
