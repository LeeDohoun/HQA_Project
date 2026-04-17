# 파일: src/tracing/metrics.py
"""
성능 지표 수집기 (MetricsCollector)

분석 파이프라인의 실행 시간, LLM 호출 수, 캐시 적중률 등
핵심 성능 지표를 수집합니다.

특징:
- thread-safe (threading.Lock)
- timer는 context manager + UUID 토큰 방식 (병렬 실행 시 label 충돌 방지)
- 동일 label 반복 실행 시 count/total/avg 누적 통계

사용 예시:
    metrics = MetricsCollector()

    # context manager 방식
    with metrics.timer("agent.analyst"):
        result = run_analyst()

    # 토큰 방식 (병렬 실행 시)
    token = metrics.start_timer("agent.quant")
    result = run_quant()
    metrics.stop_timer(token)

    # 카운터
    metrics.increment("llm_call_count")
    metrics.increment("cache_hit_count")

    # 단일 값 기록
    metrics.record("final_action", "BUY")
    metrics.record("final_confidence", 75)

    # 전체 지표 조회
    print(metrics.to_dict())
"""

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class _TimerEntry:
    """진행 중인 타이머 항목"""
    label: str
    start_time: float


class MetricsCollector:
    """
    성능 지표 수집기

    thread-safe: 모든 상태 변경은 Lock으로 보호됩니다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # 카운터 (llm_call_count, cache_hit_count 등)
        self._counters: Dict[str, int] = {}
        # 단일 값 (final_action, final_confidence 등)
        self._values: Dict[str, Any] = {}
        # 타이머 누적 통계: label → {"count": N, "total": float}
        self._timer_stats: Dict[str, Dict[str, Any]] = {}
        # 진행 중인 타이머: token → _TimerEntry
        self._active_timers: Dict[str, _TimerEntry] = {}

    def record(self, key: str, value: Any) -> None:
        """
        단일 지표 기록

        Args:
            key: 지표 이름 (예: "final_action", "candidate_count_before_filter")
            value: 지표 값
        """
        with self._lock:
            self._values[key] = value

    def increment(self, key: str, delta: int = 1) -> None:
        """
        카운터 증가

        Args:
            key: 카운터 이름 (예: "llm_call_count", "cache_hit_count")
            delta: 증가량 (기본 1)
        """
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + delta

    def start_timer(self, label: str) -> str:
        """
        타이머 시작 (토큰 방식)

        병렬 실행 시 같은 label을 여러 스레드에서 동시에 사용해도
        UUID 토큰으로 구분되어 충돌하지 않습니다.

        Args:
            label: 타이머 label (예: "agent.analyst", "candidate.005930")

        Returns:
            토큰 문자열 (stop_timer에 전달)
        """
        token = str(uuid.uuid4())
        entry = _TimerEntry(label=label, start_time=time.perf_counter())
        with self._lock:
            self._active_timers[token] = entry
        return token

    def stop_timer(self, token: str) -> Optional[float]:
        """
        타이머 종료 (토큰 방식)

        경과 시간을 label별 누적 통계에 추가합니다.
        잘못된 토큰이 들어오면 경고만 남기고 None을 반환합니다.

        Args:
            token: start_timer()에서 받은 토큰

        Returns:
            경과 시간(초) 또는 None (잘못된 토큰)
        """
        elapsed_time = time.perf_counter()
        with self._lock:
            entry = self._active_timers.pop(token, None)
            if entry is None:
                logger.warning(
                    "[MetricsCollector] 알 수 없는 timer 토큰: %s (이미 종료되었거나 잘못된 토큰)",
                    token,
                )
                return None

            elapsed = round(elapsed_time - entry.start_time, 4)
            # label별 누적 통계 갱신
            stats = self._timer_stats.setdefault(
                entry.label, {"count": 0, "total": 0.0}
            )
            stats["count"] += 1
            stats["total"] = round(stats["total"] + elapsed, 4)
            return elapsed

    @contextmanager
    def timer(self, label: str):
        """
        타이머 context manager

        사용 예:
            with metrics.timer("agent.analyst"):
                result = run_analyst()

        내부적으로 start_timer/stop_timer를 호출하며,
        예외가 발생해도 타이머는 정상 종료됩니다.
        """
        token = self.start_timer(label)
        try:
            yield token
        finally:
            self.stop_timer(token)

    def to_dict(self) -> Dict[str, Any]:
        """
        전체 지표를 딕셔너리로 반환

        timer 통계에 avg 필드를 추가하여 반환합니다.
        진행 중인 타이머는 포함하지 않습니다.

        Returns:
            {
                "total_elapsed_seconds": ...,
                "agent_elapsed_seconds": {"analyst": {"count": 5, "total": 42.1, "avg": 8.4}},
                "candidate_elapsed_seconds": {"086520": {"count": 1, "total": 12.3, "avg": 12.3}},
                "llm_call_count": ...,
                "cache_hit_count": ...,
                ...
            }
        """
        with self._lock:
            result: Dict[str, Any] = {}

            # 단일 값 복사
            result.update(self._values)

            # 카운터 복사
            result.update(self._counters)

            # 타이머 통계를 agent / candidate / 기타로 분류
            agent_timers: Dict[str, Dict[str, Any]] = {}
            candidate_timers: Dict[str, Dict[str, Any]] = {}
            other_timers: Dict[str, Dict[str, Any]] = {}

            for label, stats in self._timer_stats.items():
                # avg 계산
                entry = {
                    "count": stats["count"],
                    "total": round(stats["total"], 3),
                    "avg": round(stats["total"] / max(stats["count"], 1), 3),
                }
                if label.startswith("agent."):
                    # "agent.analyst" → "analyst"
                    short_label = label[len("agent."):]
                    agent_timers[short_label] = entry
                elif label.startswith("candidate."):
                    # "candidate.005930" → "005930"
                    short_label = label[len("candidate."):]
                    candidate_timers[short_label] = entry
                else:
                    other_timers[label] = entry

            if agent_timers:
                result["agent_elapsed_seconds"] = agent_timers
            if candidate_timers:
                result["candidate_elapsed_seconds"] = candidate_timers
            # 기타 타이머 (예: total, risk_manager 등)
            for label, entry in other_timers.items():
                # 단일 실행 타이머는 total 값만 직접 노출
                if entry["count"] == 1:
                    result[f"{label}_elapsed_seconds"] = entry["total"]
                else:
                    result[f"{label}_elapsed_seconds"] = entry

            return result
