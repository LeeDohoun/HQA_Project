# 파일: backend/tasks/celery_app.py
"""
Celery 설정

Redis를 브로커 및 결과 백엔드로 사용합니다.

사용법:
    # Worker 시작
    celery -A backend.tasks.celery_app worker --loglevel=info --concurrency=2
    
    # Flower 모니터링 (선택)
    celery -A backend.tasks.celery_app flower --port=5555
"""

import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "hqa_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["backend.tasks.analysis_tasks"],
)

celery_app.conf.update(
    # 직렬화
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # 타임아웃
    task_soft_time_limit=300,   # 5분 소프트 리밋
    task_time_limit=600,        # 10분 하드 리밋

    # 재시도
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # 결과 만료
    result_expires=3600,  # 1시간

    # 타임존
    timezone="Asia/Seoul",
    enable_utc=True,

    # 큐 설정
    task_routes={
        "backend.tasks.analysis_tasks.run_analysis_task": {"queue": "analysis"},
        "backend.tasks.analysis_tasks.run_quick_analysis_task": {"queue": "quick"},
    },
    task_default_queue="default",
)
