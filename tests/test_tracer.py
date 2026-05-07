# 파일: tests/test_tracer.py
"""
AgentTracer 단위 테스트

외부 의존성(LLM, DB) 없이 트레이서 자체 로직만 검증합니다.
"""

import json
import time
import threading
import tempfile
from pathlib import Path

import pytest

from src.tracing.agent_tracer import (
    AgentTrace,
    TraceEvent,
    AnalysisTrace,
    AgentSpan,
    AgentTracer,
)


class TestAgentTracerLifecycle:
    """기본 라이프사이클 테스트"""

    def test_trace_lifecycle(self, tmp_path):
        """start_trace → trace_agent → finish_trace 정상 흐름"""
        tracer = AgentTracer(traces_dir=str(tmp_path))

        # 세션 시작
        trace_id = tracer.start_trace(
            "삼성전자", "005930", "langgraph", query="삼성전자 분석해줘"
        )
        assert trace_id is not None
        assert tracer.is_active

        # 에이전트 실행
        with tracer.trace_agent("analyst", "삼성전자(005930)") as span:
            time.sleep(0.05)  # 최소 실행 시간
            span.set_output("A등급 (65/70)")
            span.set_reasoning("반도체 시장 지배력 확인됨")

        # 세션 종료
        saved_path = tracer.finish_trace(
            final_result_summary="매수, 230/270점",
            research_quality="B",
            retry_count=0,
        )

        # 검증
        assert saved_path is not None
        assert Path(saved_path).exists()

        data = tracer.to_dict()
        assert data["trace_id"] == trace_id
        assert data["stock_name"] == "삼성전자"
        assert data["stock_code"] == "005930"
        assert data["workflow_type"] == "langgraph"
        assert data["status"] == "completed"
        assert data["total_duration_seconds"] > 0
        assert len(data["agent_traces"]) == 1

        agent = data["agent_traces"][0]
        assert agent["agent_name"] == "analyst"
        assert agent["status"] == "success"
        assert agent["duration_seconds"] >= 0.05
        assert agent["output_summary"] == "A등급 (65/70)"
        assert agent["reasoning_summary"] == "반도체 시장 지배력 확인됨"

    def test_multiple_agents(self, tmp_path):
        """여러 에이전트 순차 실행"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace("SK하이닉스", "000660", "langgraph")

        for name in ["analyst", "quant", "chartist", "risk_manager"]:
            with tracer.trace_agent(name, f"SK하이닉스(000660)") as span:
                span.set_output(f"{name} 결과")

        tracer.finish_trace("매수")

        data = tracer.to_dict()
        assert len(data["agent_traces"]) == 4
        names = [at["agent_name"] for at in data["agent_traces"]]
        assert names == ["analyst", "quant", "chartist", "risk_manager"]


class TestContextManagerException:
    """context manager 예외 처리 테스트"""

    def test_exception_auto_recorded(self, tmp_path):
        """예외 발생 시 자동으로 에러 기록"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace("테스트", "000000", "langgraph")

        with pytest.raises(ValueError, match="테스트 에러"):
            with tracer.trace_agent("analyst", "테스트(000000)") as span:
                raise ValueError("테스트 에러")

        tracer.finish_trace(status="error")

        data = tracer.to_dict()
        agent = data["agent_traces"][0]
        assert agent["status"] == "error"
        assert "테스트 에러" in agent["error_message"]
        assert agent["error_type"] == "ValueError"
        assert agent["duration_seconds"] >= 0

    def test_set_error_manually(self, tmp_path):
        """수동 에러 설정"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace("테스트", "000000", "langgraph")

        with tracer.trace_agent("quant", "테스트") as span:
            span.set_error("LLM 타임아웃", error_type="llm_timeout")

        tracer.finish_trace()

        agent = tracer.to_dict()["agent_traces"][0]
        assert agent["status"] == "error"
        assert agent["error_type"] == "llm_timeout"

    def test_set_skipped(self, tmp_path):
        """스킵 처리"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace("테스트", "000000", "langgraph")

        with tracer.trace_agent("analyst", "테스트") as span:
            span.set_skipped("quality_gate_failed")

        tracer.finish_trace()

        agent = tracer.to_dict()["agent_traces"][0]
        assert agent["status"] == "skipped"
        assert agent["skip_reason"] == "quality_gate_failed"


class TestConcurrentAgents:
    """동시성 안전 테스트"""

    def test_concurrent_trace_agents(self, tmp_path):
        """멀티 스레드에서 동시에 trace_agent 호출"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace("동시성테스트", "999999", "fallback_parallel")

        errors = []

        def run_agent(agent_name: str):
            try:
                with tracer.trace_agent(agent_name, f"입력-{agent_name}") as span:
                    time.sleep(0.02)
                    span.set_output(f"출력-{agent_name}")
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=run_agent, args=(name,))
            for name in ["analyst", "quant", "chartist"]
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

        tracer.finish_trace("동시성 완료")
        data = tracer.to_dict()

        # 3개 에이전트 모두 기록됨
        assert len(data["agent_traces"]) == 3

        # 각 에이전트가 고유 agent_id를 가짐
        agent_ids = [at["agent_id"] for at in data["agent_traces"]]
        assert len(set(agent_ids)) == 3  # 모두 다른 ID


class TestReasoningTruncation:
    """reasoning 요약/원본 분리 테스트"""

    def test_debug_false_no_raw(self, tmp_path):
        """debug=False일 때 reasoning_raw 미저장"""
        tracer = AgentTracer(debug=False, traces_dir=str(tmp_path))
        tracer.start_trace("테스트", "000000", "langgraph")

        long_raw = "A" * 10000
        with tracer.trace_agent("analyst", "테스트") as span:
            span.set_reasoning("짧은 요약", raw=long_raw)
            span.set_output("결과")

        tracer.finish_trace()

        agent = tracer.to_dict()["agent_traces"][0]
        assert agent["reasoning_summary"] == "짧은 요약"
        assert agent["reasoning_raw"] is None  # raw 저장 안됨

    def test_debug_true_saves_raw(self, tmp_path):
        """debug=True일 때 reasoning_raw 저장"""
        tracer = AgentTracer(debug=True, traces_dir=str(tmp_path))
        tracer.start_trace("테스트", "000000", "langgraph")

        long_raw = "A" * 10000
        with tracer.trace_agent("analyst", "테스트") as span:
            span.set_reasoning("짧은 요약", raw=long_raw)
            span.set_output("결과")

        tracer.finish_trace()

        agent = tracer.to_dict()["agent_traces"][0]
        assert agent["reasoning_summary"] == "짧은 요약"
        assert agent["reasoning_raw"] == long_raw  # raw 저장됨

    def test_long_summary_truncated(self, tmp_path):
        """500자 초과 요약은 자동 truncate"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace("테스트", "000000", "langgraph")

        long_summary = "B" * 1000
        with tracer.trace_agent("analyst", "테스트") as span:
            span.set_reasoning(long_summary)
            span.set_output("결과")

        tracer.finish_trace()

        agent = tracer.to_dict()["agent_traces"][0]
        assert len(agent["reasoning_summary"]) < 1000
        assert "truncated" in agent["reasoning_summary"]


class TestEventsTimeline:
    """이벤트 타임라인 테스트"""

    def test_events_recorded(self, tmp_path):
        """이벤트가 순서대로 기록"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace("테스트", "000000", "langgraph")

        tracer.add_event("quality_gate_passed", "B등급 통과", "quality_gate")
        tracer.add_event("custom_event", "커스텀 이벤트")

        with tracer.trace_agent("analyst", "테스트") as span:
            span.set_output("결과")

        tracer.finish_trace("완료")

        data = tracer.to_dict()
        events = data["events"]

        # 최소 이벤트: trace_started, quality_gate_passed, custom_event,
        # agent_started, agent_finished, trace_completed
        assert len(events) >= 6

        # 이벤트 타입 확인
        event_types = [e["event_type"] for e in events]
        assert "trace_started" in event_types
        assert "quality_gate_passed" in event_types
        assert "custom_event" in event_types
        assert "agent_started" in event_types
        assert "agent_finished" in event_types
        assert "trace_completed" in event_types

    def test_fallback_event(self, tmp_path):
        """fallback 이벤트 기록"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace("테스트", "000000", "fallback_parallel")
        tracer.set_fallback_reason("langgraph 미설치")

        tracer.finish_trace("완료")

        data = tracer.to_dict()
        assert data["fallback_reason"] == "langgraph 미설치"

        event_types = [e["event_type"] for e in data["events"]]
        assert "fallback_triggered" in event_types


class TestJsonFileStructure:
    """JSON 파일 저장 구조 테스트"""

    def test_directory_structure(self, tmp_path):
        """날짜/종목코드 디렉토리 구조 확인"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        trace_id = tracer.start_trace("삼성전자", "005930", "langgraph")

        with tracer.trace_agent("analyst", "삼성전자") as span:
            span.set_output("결과")

        saved_path = tracer.finish_trace("완료")

        # 경로 구조: {tmp_path}/{date}/005930/{trace_id}.json
        path = Path(saved_path)
        assert path.exists()
        assert path.suffix == ".json"
        assert path.stem == trace_id
        assert path.parent.name == "005930"  # 종목코드 디렉토리

    def test_json_valid_and_complete(self, tmp_path):
        """저장된 JSON 파일이 유효하고 필수 필드 포함"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace("삼성전자", "005930", "langgraph", query="분석해줘")

        with tracer.trace_agent("analyst", "삼성전자(005930)") as span:
            span.set_output("A등급 65/70")
            span.set_reasoning("요약문")

        saved_path = tracer.finish_trace(
            final_result_summary="매수",
            research_quality="B",
            retry_count=0,
        )

        # JSON 파싱
        with open(saved_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 필수 최상위 필드
        assert "trace_id" in data
        assert "stock_name" in data
        assert "stock_code" in data
        assert "started_at" in data
        assert "finished_at" in data
        assert "total_duration_seconds" in data
        assert "workflow_type" in data
        assert "agent_traces" in data
        assert "events" in data
        assert "status" in data

        # agent_traces 필수 필드
        agent = data["agent_traces"][0]
        assert "agent_id" in agent
        assert "agent_name" in agent
        assert "duration_seconds" in agent
        assert "status" in agent
        assert "reasoning_summary" in agent

    def test_metadata_stored(self, tmp_path):
        """메타데이터 저장 확인"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace(
            "삼성전자", "005930", "langgraph",
            metadata={"model": "gemini-2.5-flash-lite"},
        )
        tracer.set_metadata("total_tokens", 5000)
        tracer.finish_trace("완료")

        data = tracer.to_dict()
        assert data["metadata"]["model"] == "gemini-2.5-flash-lite"
        assert data["metadata"]["total_tokens"] == 5000


class TestRetryFlowTracking:
    """retry 흐름 추적 테스트"""

    def test_retry_from_links(self, tmp_path):
        """retry_from으로 이전 agent_id 연결"""
        tracer = AgentTracer(traces_dir=str(tmp_path))
        tracer.start_trace("테스트", "000000", "langgraph")

        # 첫 번째 analyst
        with tracer.trace_agent("analyst", "테스트") as span1:
            first_id = span1.agent_id
            span1.set_output("D등급")

        # retry
        with tracer.trace_agent("analyst_retry", "테스트 재시도") as span2:
            span2.set_retry_from(first_id)
            span2.set_output("B등급")

        tracer.finish_trace("완료")

        data = tracer.to_dict()
        retry_agent = data["agent_traces"][1]
        assert retry_agent["retry_from"] == first_id
        assert retry_agent["agent_name"] == "analyst_retry"
