# 파일: tests/test_metrics.py
"""
MetricsCollector 단위 테스트

검증 항목:
- record / increment 기본 동작
- timer context manager / 토큰 방식
- 멀티스레드 timer count/total/avg 정확성
- 잘못된 토큰 stop_timer 안전 처리
- to_dict() agent/candidate/기타 분류
"""

import threading
import time

import pytest

from src.tracing.metrics import MetricsCollector


class TestRecord:
    """단일 값 기록 테스트"""

    def test_record_string(self):
        m = MetricsCollector()
        m.record("final_action", "BUY")
        assert m.to_dict()["final_action"] == "BUY"

    def test_record_int(self):
        m = MetricsCollector()
        m.record("final_confidence", 75)
        assert m.to_dict()["final_confidence"] == 75

    def test_record_overwrite(self):
        """같은 키에 덮어쓰기"""
        m = MetricsCollector()
        m.record("status", "running")
        m.record("status", "done")
        assert m.to_dict()["status"] == "done"


class TestIncrement:
    """카운터 테스트"""

    def test_increment_default(self):
        m = MetricsCollector()
        m.increment("llm_call_count")
        m.increment("llm_call_count")
        assert m.to_dict()["llm_call_count"] == 2

    def test_increment_custom_delta(self):
        m = MetricsCollector()
        m.increment("cache_hit_count", delta=5)
        assert m.to_dict()["cache_hit_count"] == 5

    def test_increment_multiple_keys(self):
        m = MetricsCollector()
        m.increment("cache_hit_count", 3)
        m.increment("cache_miss_count", 2)
        result = m.to_dict()
        assert result["cache_hit_count"] == 3
        assert result["cache_miss_count"] == 2


class TestTimerContextManager:
    """timer context manager 테스트"""

    def test_timer_basic(self):
        """기본 timer 동작: count, total, avg가 기록되는지"""
        m = MetricsCollector()
        with m.timer("agent.analyst"):
            time.sleep(0.05)
        result = m.to_dict()
        stats = result["agent_elapsed_seconds"]["analyst"]
        assert stats["count"] == 1
        assert stats["total"] >= 0.04  # 최소 40ms
        assert stats["avg"] == stats["total"]

    def test_timer_multiple_calls(self):
        """같은 label에 여러 번 호출 시 누적"""
        m = MetricsCollector()
        for _ in range(3):
            with m.timer("agent.quant"):
                time.sleep(0.01)
        result = m.to_dict()
        stats = result["agent_elapsed_seconds"]["quant"]
        assert stats["count"] == 3
        assert stats["total"] >= 0.02
        assert abs(stats["avg"] - stats["total"] / 3) < 0.001

    def test_timer_exception_still_records(self):
        """예외가 발생해도 타이머는 정상 기록"""
        m = MetricsCollector()
        with pytest.raises(ValueError):
            with m.timer("agent.chartist"):
                time.sleep(0.01)
                raise ValueError("test error")
        result = m.to_dict()
        stats = result["agent_elapsed_seconds"]["chartist"]
        assert stats["count"] == 1
        assert stats["total"] >= 0.005


class TestTimerToken:
    """토큰 방식 timer 테스트"""

    def test_start_stop(self):
        m = MetricsCollector()
        token = m.start_timer("agent.analyst")
        time.sleep(0.02)
        elapsed = m.stop_timer(token)
        assert elapsed is not None
        assert elapsed >= 0.01

    def test_invalid_token(self):
        """잘못된 토큰 → None 반환, 전체 죽지 않음"""
        m = MetricsCollector()
        result = m.stop_timer("invalid-token-12345")
        assert result is None

    def test_double_stop(self):
        """같은 토큰 2번 stop → 2번째는 None"""
        m = MetricsCollector()
        token = m.start_timer("agent.quant")
        m.stop_timer(token)
        result = m.stop_timer(token)
        assert result is None


class TestTimerClassification:
    """to_dict()에서 timer label이 올바르게 분류되는지"""

    def test_agent_prefix(self):
        m = MetricsCollector()
        with m.timer("agent.analyst"):
            pass
        result = m.to_dict()
        assert "analyst" in result.get("agent_elapsed_seconds", {})

    def test_candidate_prefix(self):
        m = MetricsCollector()
        with m.timer("candidate.005930"):
            pass
        result = m.to_dict()
        assert "005930" in result.get("candidate_elapsed_seconds", {})
        # count/total/avg 구조 확인
        stats = result["candidate_elapsed_seconds"]["005930"]
        assert "count" in stats
        assert "total" in stats
        assert "avg" in stats

    def test_other_single_timer(self):
        """단일 실행 타이머는 _elapsed_seconds로 직접 노출"""
        m = MetricsCollector()
        with m.timer("total"):
            time.sleep(0.01)
        result = m.to_dict()
        assert "total_elapsed_seconds" in result
        assert isinstance(result["total_elapsed_seconds"], float)

    def test_other_multi_timer(self):
        """여러 번 실행된 기타 타이머는 dict로 노출"""
        m = MetricsCollector()
        with m.timer("risk_manager"):
            pass
        with m.timer("risk_manager"):
            pass
        result = m.to_dict()
        stats = result["risk_manager_elapsed_seconds"]
        assert isinstance(stats, dict)
        assert stats["count"] == 2


class TestThreadSafety:
    """멀티스레드에서 같은 label을 동시에 기록해도 정확한지"""

    def test_concurrent_timers(self):
        """5개 스레드가 동시에 agent.quant 타이머를 기록"""
        m = MetricsCollector()
        errors = []

        def worker():
            try:
                with m.timer("agent.quant"):
                    time.sleep(0.02)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"스레드 에러: {errors}"
        result = m.to_dict()
        stats = result["agent_elapsed_seconds"]["quant"]
        assert stats["count"] == 5
        assert stats["total"] >= 0.05  # 각 20ms × 5 (병렬이므로 total >= 100ms)
        assert abs(stats["avg"] - stats["total"] / 5) < 0.01

    def test_concurrent_increments(self):
        """10개 스레드가 동시에 카운터 증가"""
        m = MetricsCollector()
        errors = []

        def worker():
            try:
                for _ in range(100):
                    m.increment("counter")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert m.to_dict()["counter"] == 1000


class TestToDict:
    """to_dict() 출력 형식"""

    def test_empty(self):
        m = MetricsCollector()
        assert m.to_dict() == {}

    def test_full_output(self):
        """모든 유형의 지표가 포함된 전체 출력"""
        m = MetricsCollector()
        m.record("final_action", "BUY")
        m.record("final_confidence", 80)
        m.increment("llm_call_count", 3)
        m.increment("cache_hit_count", 5)
        with m.timer("agent.analyst"):
            pass
        with m.timer("candidate.005930"):
            pass
        with m.timer("total"):
            pass

        result = m.to_dict()
        # 단일 값
        assert result["final_action"] == "BUY"
        assert result["final_confidence"] == 80
        # 카운터
        assert result["llm_call_count"] == 3
        assert result["cache_hit_count"] == 5
        # 에이전트 타이머
        assert "analyst" in result["agent_elapsed_seconds"]
        # 후보 타이머
        assert "005930" in result["candidate_elapsed_seconds"]
        # 기타 타이머
        assert "total_elapsed_seconds" in result
