# 파일: src/agents/llm_config.py

import os
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, Optional
from src.config.settings import load_project_env
from src.tracing.agent_tracer import add_token_usage_from_response
from src.utils.llm_queue import arun_with_llm_slot, run_with_llm_slot

load_project_env()

logger = logging.getLogger(__name__)


# ==========================================
# Provider 설정
# ==========================================

DEFAULT_PROVIDER = "ollama"
SUPPORTED_PROVIDERS = {"ollama", "mock"}
PROVIDER_ALIASES = {
    "test": "mock",
    "fake": "mock",
}

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11435"
DEFAULT_OLLAMA_ANALYST_MODEL = "gemma4:12b"
DEFAULT_OLLAMA_SUMMARY_MODEL = "gemma4:e4b"
DEFAULT_OLLAMA_QUANT_MODEL = "gemma4:12b"
DEFAULT_OLLAMA_CHARTIST_MODEL = "qwen3.5:9b"
DEFAULT_OLLAMA_RISK_MANAGER_MODEL = "gemma4:12b"


def _env(name: str, default: str = "", *, allow_blank: bool = False) -> str:
    value = os.getenv(name)
    if value is None:
        return default

    value = value.strip()
    if not value and not allow_blank:
        return default
    return value


LLM_PROVIDER = _env("LLM_PROVIDER", DEFAULT_PROVIDER).lower().strip()

# Ollama 설정
OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
OLLAMA_ANALYST_MODEL = _env("OLLAMA_ANALYST_MODEL", DEFAULT_OLLAMA_ANALYST_MODEL)
OLLAMA_SUMMARY_MODEL = _env("OLLAMA_SUMMARY_MODEL", DEFAULT_OLLAMA_SUMMARY_MODEL)
OLLAMA_QUANT_MODEL = _env("OLLAMA_QUANT_MODEL", DEFAULT_OLLAMA_QUANT_MODEL)
OLLAMA_CHARTIST_MODEL = _env("OLLAMA_CHARTIST_MODEL", DEFAULT_OLLAMA_CHARTIST_MODEL)
OLLAMA_RISK_MANAGER_MODEL = _env("OLLAMA_RISK_MANAGER_MODEL", DEFAULT_OLLAMA_RISK_MANAGER_MODEL)

@dataclass(frozen=True)
class LLMConfig:
    raw_provider: str
    requested_provider: str
    provider: str
    fallback_reason: str
    ollama_base_url: str
    ollama_analyst_model: str
    ollama_summary_model: str
    ollama_quant_model: str
    ollama_chartist_model: str
    ollama_risk_manager_model: str

    @property
    def api_key_set(self) -> bool:
        return False


_WARNED_FALLBACKS: set[str] = set()


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key in _WARNED_FALLBACKS:
        return
    _WARNED_FALLBACKS.add(key)
    logger.warning(message, *args)


def get_llm_config() -> LLMConfig:
    """환경변수 기반 LLM 설정을 동적으로 반환합니다."""
    raw_provider = _env("LLM_PROVIDER", DEFAULT_PROVIDER).lower().strip()
    requested_provider = PROVIDER_ALIASES.get(raw_provider, raw_provider)
    provider = requested_provider
    fallback_reason = ""

    if requested_provider not in SUPPORTED_PROVIDERS:
        provider = DEFAULT_PROVIDER
        fallback_reason = f"unsupported_provider:{raw_provider}"
        _warn_once(
            fallback_reason,
            "지원하지 않는 LLM_PROVIDER=%s 입니다. Ollama로 폴백합니다.",
            raw_provider,
        )

    return LLMConfig(
        raw_provider=raw_provider,
        requested_provider=requested_provider,
        provider=provider,
        fallback_reason=fallback_reason,
        ollama_base_url=_env("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
        ollama_analyst_model=_env("OLLAMA_ANALYST_MODEL", DEFAULT_OLLAMA_ANALYST_MODEL),
        ollama_summary_model=_env("OLLAMA_SUMMARY_MODEL", DEFAULT_OLLAMA_SUMMARY_MODEL),
        ollama_quant_model=_env("OLLAMA_QUANT_MODEL", DEFAULT_OLLAMA_QUANT_MODEL),
        ollama_chartist_model=_env("OLLAMA_CHARTIST_MODEL", DEFAULT_OLLAMA_CHARTIST_MODEL),
        ollama_risk_manager_model=_env(
            "OLLAMA_RISK_MANAGER_MODEL",
            DEFAULT_OLLAMA_RISK_MANAGER_MODEL,
        ),
    )


def _get_provider() -> str:
    """현재 LLM Provider 반환 (검증 포함)"""
    return get_llm_config().provider


# ==========================================
# Ollama LLM 생성
# ==========================================

class MockChatModel:
    """네트워크나 외부 모델 없이 smoke test를 위한 최소 LLM."""

    def __init__(self, role: str):
        self.role = role

    def invoke(self, prompt) -> SimpleNamespace:
        if isinstance(prompt, list):
            return SimpleNamespace(content=f"[mock:{self.role}] 멀티모달 입력을 수신했습니다.")

        text = str(prompt)
        if "[검색 컨텍스트]" in text:
            context = text.split("[검색 컨텍스트]", 1)[1]
            context = context.split("[답변 형식]", 1)[0].strip()
            lines = [line.strip() for line in context.splitlines() if line.strip()]
            evidence = " ".join(lines[:2])[:240]
            return SimpleNamespace(
                content=(
                    f"[mock:{self.role}] 검색 문서를 기준으로 보면 {evidence} "
                    "추가 검증이 필요하면 실제 LLM/Ollama 런타임으로 다시 확인하세요."
                )
            )

        if "JSON만 출력하세요" in text:
            return SimpleNamespace(content='{"intent":"general","confidence":0.5}')

        return SimpleNamespace(content=f"[mock:{self.role}] 요청을 수신했습니다.")


class TracingLLMProxy:
    """LLM invoke 결과를 가로채 현재 활성 AgentSpan에 토큰 사용량을 적재."""

    def __init__(self, llm: Any):
        self._llm = llm

    def invoke(self, prompt) -> Any:
        response = run_with_llm_slot(lambda: self._llm.invoke(prompt))
        add_token_usage_from_response(response)
        return response

    async def ainvoke(self, prompt) -> Any:
        response = await arun_with_llm_slot(lambda: self._llm.ainvoke(prompt))
        add_token_usage_from_response(response)
        return response

    def with_structured_output(self, *args, **kwargs):
        structured = self._llm.with_structured_output(*args, **kwargs)
        return TracingLLMProxy(structured)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)


def _with_tracing(llm: Any) -> Any:
    if isinstance(llm, TracingLLMProxy):
        return llm
    return TracingLLMProxy(llm)


def _create_ollama_llm(
    model: str,
    temperature: float = 0.3,
    reasoning: bool | str | None = False,
    base_url: Optional[str] = None,
) -> Any:
    """Ollama ChatModel 생성"""
    from langchain_ollama import ChatOllama

    kwargs: Dict[str, Any] = {
        "model": model,
        "base_url": base_url or get_llm_config().ollama_base_url,
        "temperature": temperature,
    }
    if reasoning is not None:
        kwargs["reasoning"] = reasoning

    try:
        return ChatOllama(**kwargs)
    except TypeError:
        if "reasoning" not in kwargs:
            raise
        kwargs.pop("reasoning")
        logger.debug("현재 langchain-ollama 버전이 reasoning 옵션을 지원하지 않아 생략합니다.")
        return ChatOllama(**kwargs)


def _create_role_llm(role: str, model: str, temperature: float) -> Any:
    config = get_llm_config()
    if config.provider == "mock":
        return _with_tracing(MockChatModel(role))
    llm = _create_ollama_llm(
        model,
        temperature=temperature,
        reasoning=False,
        base_url=config.ollama_base_url,
    )
    logger.debug("🤖 %s LLM: Ollama (%s)", role, model)
    return _with_tracing(llm)


# ==========================================
# 통합 팩토리 함수 (에이전트가 호출하는 인터페이스)
# ==========================================

def get_instruct_llm() -> Any:
    """
    구형 호출부 호환용 빠른 LLM.

    별도 공통 Instruct 설정은 더 이상 사용하지 않고, 빠른 판단 역할에
    가까운 Chartist 모델 설정을 사용합니다.
    """
    config = get_llm_config()
    return _create_role_llm("instruct", config.ollama_chartist_model, temperature=0.3)


def get_thinking_llm() -> Any:
    """
    구형 호출부 호환용 깊은 추론 LLM.

    별도 공통 Thinking 설정은 더 이상 사용하지 않고, 최종 판단 역할에
    가까운 Risk Manager 모델 설정을 사용합니다.
    """
    config = get_llm_config()
    return _create_role_llm("thinking", config.ollama_risk_manager_model, temperature=0.5)


def get_analyst_llm() -> Any:
    """AnalystAgent 전용 LLM."""
    config = get_llm_config()
    return _create_role_llm("analyst", config.ollama_analyst_model, temperature=0.5)


def get_summary_llm() -> Any:
    """긴 검색 근거를 짧게 압축하는 요약 전용 LLM."""
    config = get_llm_config()
    return _create_role_llm("summary", config.ollama_summary_model, temperature=0.2)


def get_quant_llm() -> Any:
    """QuantAgent 전용 LLM."""
    config = get_llm_config()
    return _create_role_llm("quant", config.ollama_quant_model, temperature=0.3)


def get_chartist_llm() -> Any:
    """ChartistAgent 전용 LLM."""
    config = get_llm_config()
    return _create_role_llm("chartist", config.ollama_chartist_model, temperature=0.3)


def get_risk_manager_llm() -> Any:
    """RiskManagerAgent 전용 LLM."""
    config = get_llm_config()
    return _create_role_llm("risk_manager", config.ollama_risk_manager_model, temperature=0.5)


# ==========================================
# Provider 정보 (디버깅/헬스체크용)
# ==========================================

def get_llm_info() -> Dict[str, Any]:
    """현재 LLM 설정 정보 반환 (디버깅용)"""
    config = get_llm_config()
    provider = config.provider
    info: Dict[str, Any] = {"provider": provider}
    if config.requested_provider != provider:
        info["requested_provider"] = config.requested_provider
    if config.raw_provider != config.requested_provider:
        info["raw_provider"] = config.raw_provider
    if config.fallback_reason:
        info["fallback_reason"] = config.fallback_reason

    info.update({
        "base_url": config.ollama_base_url,
        "agent_models": {
            "analyst": "mock" if provider == "mock" else config.ollama_analyst_model,
            "summary": "mock" if provider == "mock" else config.ollama_summary_model,
            "quant": "mock" if provider == "mock" else config.ollama_quant_model,
            "chartist": "mock" if provider == "mock" else config.ollama_chartist_model,
            "risk_manager": "mock" if provider == "mock" else config.ollama_risk_manager_model,
        },
    })
    return info
