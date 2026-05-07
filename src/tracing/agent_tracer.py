# 파일: src/tracing/agent_tracer.py
"""
에이전트 트레이싱 시스템 v2

핵심 설계:
- AgentTrace: 개별 에이전트 실행 기록 (agent_id UUID로 동시성 안전)
- TraceEvent: 이벤트 기반 타임라인 (LangSmith 수준 흐름 추적)
- AnalysisTrace: 전체 분석 세션 기록
- AgentSpan: context manager (기존 코드 최소 침습)
- AgentTracer: 트레이스 수집·저장 (thread-safe)

사용 예시:
    tracer = AgentTracer(debug=True)
    tracer.start_trace("삼성전자", "005930", "langgraph")

    with tracer.trace_agent("analyst", "삼성전자(005930)") as span:
        result = run_analyst()
        span.set_output("A등급 (65/70)")
        span.set_reasoning("반도체 지배력 확인", raw=full_reasoning_text)

    tracer.finish_trace("매수 판단, 총 230/270점")
"""

import json
import logging
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.settings import get_traces_dir

logger = logging.getLogger(__name__)

# 한국 표준시
KST = timezone(timedelta(hours=9))

# reasoning_summary 최대 길이
_MAX_SUMMARY_LEN = 500


def _now_iso() -> str:
    """현재 시각 ISO 형식 (KST)"""
    return datetime.now(KST).isoformat()


def _truncate(text: str, max_len: int = _MAX_SUMMARY_LEN) -> str:
    """텍스트 길이 제한"""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... (truncated, total {len(text)} chars)"


# ──────────────────────────────────────────────
# 데이터 구조
# ──────────────────────────────────────────────

@dataclass
class AgentTrace:
    """개별 에이전트 실행 기록"""

    # ID & 이름
    agent_id: str = ""                       # UUID (동시성/retry 구분)
    agent_name: str = ""                     # "analyst", "quant", "chartist" 등

    # 시간
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0

    # 상태
    status: str = "running"                  # "success" | "error" | "skipped"

    # 입출력
    input_summary: str = ""
    output_summary: str = ""

    # 판단 근거 (요약/원본 분리)
    reasoning_summary: str = ""              # 항상 저장 (짧은 요약)
    reasoning_raw: Optional[str] = None      # debug 모드에서만 저장

    # 에러/스킵 상세
    error_message: Optional[str] = None
    error_type: Optional[str] = None         # "llm_timeout", "parse_error" 등
    skip_reason: Optional[str] = None        # "quality_gate_failed" 등
    retry_from: Optional[str] = None         # 이전 agent_id (retry 흐름 추적)


@dataclass
class TraceEvent:
    """이벤트 기반 타임라인 항목"""
    timestamp: str = ""
    event_type: str = ""                     # "agent_started", "fallback_triggered" 등
    agent_name: Optional[str] = None
    detail: str = ""


@dataclass
class AnalysisTrace:
    """전체 분석 세션 기록"""

    # 세션
    trace_id: str = ""
    stock_name: str = ""
    stock_code: str = ""
    query: str = ""

    # 시간
    started_at: str = ""
    finished_at: str = ""
    total_duration_seconds: float = 0.0

    # 워크플로우
    workflow_type: str = ""                  # "langgraph" | "fallback_parallel"
    fallback_reason: Optional[str] = None    # fallback 전환 이유

    # 에이전트 기록
    agent_traces: List[AgentTrace] = field(default_factory=list)
    events: List[TraceEvent] = field(default_factory=list)

    # 결과
    final_result_summary: str = ""
    research_quality: str = ""
    retry_count: int = 0

    # 메타데이터
    metadata: Dict[str, Any] = field(default_factory=dict)

    # 상태
    status: str = "running"                  # "completed" | "error"


# ──────────────────────────────────────────────
# AgentSpan (context manager 반환 객체)
# ──────────────────────────────────────────────

class AgentSpan:
    """
    개별 에이전트 실행 스팬

    context manager에서 반환되며,
    실행 중 set_output / set_reasoning 등을 호출합니다.
    __exit__에서 자동으로 타이밍 + 예외 처리.
    """

    def __init__(self, trace: AgentTrace, debug: bool = False):
        self._trace = trace
        self._debug = debug
        self._start_time: Optional[datetime] = None

    @property
    def agent_id(self) -> str:
        return self._trace.agent_id

    def set_output(self, summary: str) -> None:
        """결과 요약 설정"""
        self._trace.output_summary = _truncate(summary)

    def set_reasoning(
        self,
        summary: str,
        raw: Optional[str] = None,
    ) -> None:
        """
        판단 근거 설정

        Args:
            summary: 짧은 요약 (항상 저장)
            raw: 원본 텍스트 (debug=True일 때만 저장)
        """
        self._trace.reasoning_summary = _truncate(summary)
        if self._debug and raw:
            self._trace.reasoning_raw = raw

    def set_error(
        self,
        message: str,
        error_type: str = "unknown",
    ) -> None:
        """에러 정보 설정"""
        self._trace.status = "error"
        self._trace.error_message = _truncate(message, 1000)
        self._trace.error_type = error_type

    def set_skipped(self, reason: str) -> None:
        """스킵 정보 설정"""
        self._trace.status = "skipped"
        self._trace.skip_reason = reason

    def set_retry_from(self, previous_agent_id: str) -> None:
        """retry 흐름 연결"""
        self._trace.retry_from = previous_agent_id

    def __enter__(self) -> "AgentSpan":
        self._start_time = datetime.now(KST)
        self._trace.started_at = self._start_time.isoformat()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        end_time = datetime.now(KST)
        self._trace.finished_at = end_time.isoformat()

        if self._start_time:
            self._trace.duration_seconds = round(
                (end_time - self._start_time).total_seconds(), 3
            )

        # 예외 발생 시 자동 에러 기록
        if exc_type is not None:
            self._trace.status = "error"
            self._trace.error_message = _truncate(str(exc_val), 1000)
            self._trace.error_type = exc_type.__name__
            # 예외를 삼키지 않음 (호출자에서 처리)
            return False

        # 명시적으로 상태를 바꾸지 않았으면 success
        if self._trace.status == "running":
            self._trace.status = "success"

        return False


# ──────────────────────────────────────────────
# AgentTracer (메인 클래스)
# ──────────────────────────────────────────────

class AgentTracer:
    """
    에이전트 트레이싱 수집·저장

    Thread-safe: threading.Lock으로 동시 실행 안전.

    사용법:
        tracer = AgentTracer(debug=True)
        tracer.start_trace("삼성전자", "005930", "langgraph")

        with tracer.trace_agent("analyst", "삼성전자(005930)") as span:
            ...
            span.set_output("A등급")
            span.set_reasoning("요약", raw="전체 텍스트")

        tracer.add_event("quality_gate_passed", "품질 B등급 통과")
        tracer.finish_trace("매수, 230/270점")
    """

    def __init__(
        self,
        debug: bool = False,
        traces_dir: Optional[str] = None,
    ):
        """
        Args:
            debug: True면 reasoning_raw 저장 (개발용)
            traces_dir: 트레이스 JSON 저장 루트 디렉토리
        """
        self._debug = debug
        self._traces_dir = Path(traces_dir) if traces_dir else get_traces_dir()
        self._lock = threading.Lock()
        self._trace: Optional[AnalysisTrace] = None
        self._start_time: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        """트레이스 세션이 활성 상태인지"""
        return self._trace is not None

    @property
    def trace_id(self) -> Optional[str]:
        """현재 trace_id"""
        return self._trace.trace_id if self._trace else None

    def start_trace(
        self,
        stock_name: str,
        stock_code: str,
        workflow_type: str,
        query: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        분석 세션 시작

        Args:
            stock_name: 종목명
            stock_code: 종목코드
            workflow_type: "langgraph" | "fallback_parallel"
            query: 원본 사용자 쿼리
            metadata: 추가 메타 (model_info, 설정 등)

        Returns:
            trace_id (UUID 문자열)
        """
        trace_id = str(uuid.uuid4())
        self._start_time = datetime.now(KST)

        self._trace = AnalysisTrace(
            trace_id=trace_id,
            stock_name=stock_name,
            stock_code=stock_code,
            query=query,
            workflow_type=workflow_type,
            started_at=self._start_time.isoformat(),
            metadata=metadata or {},
        )

        self.add_event(
            "trace_started",
            f"{stock_name}({stock_code}) {workflow_type} 분석 시작",
        )

        logger.info(f"[Tracer] 트레이스 시작: {trace_id} ({stock_name})")
        return trace_id

    @contextmanager
    def trace_agent(
        self,
        agent_name: str,
        input_summary: str = "",
    ):
        """
        에이전트 실행 추적 (context manager)

        사용법:
            with tracer.trace_agent("analyst", "삼성전자(005930)") as span:
                result = run_analyst()
                span.set_output("A등급 65/70")
                span.set_reasoning("요약", raw="전체 텍스트")

        자동 처리:
            - 시작/종료 타이밍
            - 예외 시 에러 기록
            - AgentTrace를 AnalysisTrace에 추가
            - 이벤트 타임라인 기록
        """
        agent_id = str(uuid.uuid4())

        trace = AgentTrace(
            agent_id=agent_id,
            agent_name=agent_name,
            input_summary=_truncate(input_summary),
        )

        span = AgentSpan(trace, debug=self._debug)

        # 이벤트: 에이전트 시작
        self.add_event("agent_started", f"{agent_name} 시작", agent_name)

        try:
            with span:
                yield span
        finally:
            # 이벤트: 에이전트 종료
            status_detail = f"{agent_name} {trace.status}"
            if trace.duration_seconds:
                status_detail += f" ({trace.duration_seconds:.1f}s)"
            self.add_event("agent_finished", status_detail, agent_name)

            # thread-safe하게 AgentTrace 추가
            with self._lock:
                if self._trace:
                    self._trace.agent_traces.append(trace)

    def add_event(
        self,
        event_type: str,
        detail: str,
        agent_name: Optional[str] = None,
    ) -> None:
        """
        이벤트 타임라인에 항목 추가

        Args:
            event_type: 이벤트 종류
                "trace_started", "agent_started", "agent_finished",
                "quality_gate_passed", "quality_gate_failed",
                "fallback_triggered", "retry_started",
                "trace_completed", "trace_error"
            detail: 상세 설명
            agent_name: 관련 에이전트 (선택)
        """
        event = TraceEvent(
            timestamp=_now_iso(),
            event_type=event_type,
            agent_name=agent_name,
            detail=_truncate(detail, 300),
        )

        with self._lock:
            if self._trace:
                self._trace.events.append(event)

    def set_fallback_reason(self, reason: str) -> None:
        """fallback 전환 이유 설정"""
        with self._lock:
            if self._trace:
                self._trace.fallback_reason = reason
        self.add_event("fallback_triggered", reason)

    def set_metadata(self, key: str, value: Any) -> None:
        """메타데이터 추가/업데이트"""
        with self._lock:
            if self._trace:
                self._trace.metadata[key] = value

    def finish_trace(
        self,
        final_result_summary: str = "",
        research_quality: str = "",
        retry_count: int = 0,
        status: str = "completed",
    ) -> Optional[str]:
        """
        분석 세션 종료 및 JSON 파일 저장

        Args:
            final_result_summary: 최종 판단 요약
            research_quality: 리서치 품질 등급
            retry_count: 재시도 횟수
            status: "completed" | "error"

        Returns:
            저장된 JSON 파일 경로 (None = 저장 실패)
        """
        if not self._trace:
            logger.warning("[Tracer] 활성 트레이스 없음 — finish_trace 무시")
            return None

        end_time = datetime.now(KST)
        self._trace.finished_at = end_time.isoformat()
        self._trace.final_result_summary = _truncate(final_result_summary, 1000)
        self._trace.research_quality = research_quality
        self._trace.retry_count = retry_count
        self._trace.status = status

        if self._start_time:
            self._trace.total_duration_seconds = round(
                (end_time - self._start_time).total_seconds(), 3
            )

        self.add_event(
            "trace_completed" if status == "completed" else "trace_error",
            f"총 {self._trace.total_duration_seconds:.1f}s, "
            f"에이전트 {len(self._trace.agent_traces)}개 실행",
        )

        # JSON 저장
        saved_path = self._save_json()

        logger.info(
            f"[Tracer] 트레이스 완료: {self._trace.trace_id} "
            f"({self._trace.total_duration_seconds:.1f}s)"
        )

        return saved_path

    def to_dict(self) -> Dict[str, Any]:
        """
        API 응답용 직렬화

        Returns:
            AnalysisTrace를 dict으로 변환
        """
        if not self._trace:
            return {}

        return {
            "trace_id": self._trace.trace_id,
            "stock_name": self._trace.stock_name,
            "stock_code": self._trace.stock_code,
            "query": self._trace.query,
            "started_at": self._trace.started_at,
            "finished_at": self._trace.finished_at,
            "total_duration_seconds": self._trace.total_duration_seconds,
            "workflow_type": self._trace.workflow_type,
            "fallback_reason": self._trace.fallback_reason,
            "status": self._trace.status,
            "research_quality": self._trace.research_quality,
            "retry_count": self._trace.retry_count,
            "final_result_summary": self._trace.final_result_summary,
            "metadata": self._trace.metadata,
            "agent_traces": [asdict(at) for at in self._trace.agent_traces],
            "events": [asdict(ev) for ev in self._trace.events],
        }

    def _save_json(self) -> Optional[str]:
        """
        트레이스를 JSON 파일로 저장

        저장 경로: {traces_dir}/{date}/{stock_code}/{trace_id}.json
        """
        if not self._trace:
            return None

        try:
            # 디렉토리 구조: {date}/{stock_code}/
            date_str = datetime.now(KST).strftime("%Y-%m-%d")
            stock_code = self._trace.stock_code or "unknown"
            save_dir = self._traces_dir / date_str / stock_code
            save_dir.mkdir(parents=True, exist_ok=True)

            file_path = save_dir / f"{self._trace.trace_id}.json"

            data = self.to_dict()

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"[Tracer] JSON 저장: {file_path}")
            return str(file_path)

        except Exception as e:
            logger.exception(f"[Tracer] JSON 저장 실패: {e}")
            return None
