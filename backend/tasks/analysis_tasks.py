# 파일: backend/tasks/analysis_tasks.py
"""
Celery 분석 태스크 (AI 서버 위임)

분석 실행은 AI 서버(port 8001)에 위임합니다.
이 태스크는 Celery가 활성화된 환경에서 하위 호환성을 위해 유지됩니다.
"""

import logging
import os

import httpx

from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _get_ai_server_url() -> str:
    return os.getenv("AI_SERVER_URL", "http://localhost:8001")


@celery_app.task(
    bind=True,
    name="backend.tasks.analysis_tasks.run_analysis_task",
    max_retries=1,
    default_retry_delay=30,
)
def run_analysis_task(
    self, task_id: str, stock_name: str, stock_code: str, mode: str, max_retries: int = 1
):
    """
    분석 태스크 - AI 서버에 위임

    AI 서버가 분석을 실행하고 Redis pub/sub으로 진행 상황을 전달합니다.
    """
    ai_url = _get_ai_server_url()
    try:
        response = httpx.post(
            f"{ai_url}/analyze",
            json={
                "task_id": task_id,
                "stock_name": stock_name,
                "stock_code": stock_code,
                "mode": mode,
                "max_retries": max_retries,
            },
            timeout=30,
        )
        response.raise_for_status()
        logger.info(f"AI 서버에 분석 요청 완료: {task_id}")
        return {"task_id": task_id, "status": "submitted"}

    except httpx.ConnectError:
        logger.error(f"AI 서버 연결 실패 ({ai_url})")
        raise self.retry(exc=RuntimeError(f"AI 서버에 연결할 수 없습니다: {ai_url}"))
    except Exception as e:
        logger.exception(f"분석 태스크 실패: {task_id}")
        raise
