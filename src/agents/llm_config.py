# 파일: src/agents/llm_config.py

import os
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from src.config.settings import load_project_env

load_project_env()

logger = logging.getLogger(__name__)


# ==========================================
# Provider 설정
# ==========================================

DEFAULT_PROVIDER = "ollama"
SUPPORTED_PROVIDERS = {"ollama", "gemini", "mock"}
PROVIDER_ALIASES = {
    "google": "gemini",
    "google_ai": "gemini",
    "google-ai": "gemini",
    "google_genai": "gemini",
    "google-genai": "gemini",
    "test": "mock",
    "fake": "mock",
}

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_INSTRUCT_MODEL = "qwen2.5:14b"
DEFAULT_OLLAMA_THINKING_MODEL = "qwen2.5:14b"
DEFAULT_OLLAMA_VISION_MODEL = "llava:13b"

DEFAULT_GEMINI_INSTRUCT_MODEL = "gemini-2.5-flash-lite"
DEFAULT_GEMINI_THINKING_MODEL = "gemini-2.5-pro"
DEFAULT_GEMINI_VISION_MODEL = "gemini-2.5-flash"


def _env(name: str, default: str = "", *, allow_blank: bool = False) -> str:
    value = os.getenv(name)
    if value is None:
        return default

    value = value.strip()
    if not value and not allow_blank:
        return default
    return value


def _get_google_api_key() -> str:
    return _env("GOOGLE_API_KEY", "", allow_blank=True) or _env(
        "GEMINI_API_KEY",
        "",
        allow_blank=True,
    )


LLM_PROVIDER = _env("LLM_PROVIDER", DEFAULT_PROVIDER).lower().strip()

# Ollama 설정
OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
OLLAMA_INSTRUCT_MODEL = _env("OLLAMA_INSTRUCT_MODEL", DEFAULT_OLLAMA_INSTRUCT_MODEL)
OLLAMA_THINKING_MODEL = _env("OLLAMA_THINKING_MODEL", DEFAULT_OLLAMA_THINKING_MODEL)
OLLAMA_THINKING_VALIDATOR_MODEL = _env(
    "OLLAMA_THINKING_VALIDATOR_MODEL",
    "",
    allow_blank=True,
)
OLLAMA_VISION_MODEL = _env("OLLAMA_VISION_MODEL", DEFAULT_OLLAMA_VISION_MODEL)

# Gemini 설정
GOOGLE_API_KEY = _get_google_api_key()
GEMINI_INSTRUCT_MODEL = _env("GEMINI_INSTRUCT_MODEL", DEFAULT_GEMINI_INSTRUCT_MODEL)
GEMINI_THINKING_MODEL = _env("GEMINI_THINKING_MODEL", DEFAULT_GEMINI_THINKING_MODEL)
GEMINI_THINKING_VALIDATOR_MODEL = _env(
    "GEMINI_THINKING_VALIDATOR_MODEL",
    "",
    allow_blank=True,
)
GEMINI_VISION_MODEL = _env("GEMINI_VISION_MODEL", DEFAULT_GEMINI_VISION_MODEL)


@dataclass(frozen=True)
class LLMConfig:
    raw_provider: str
    requested_provider: str
    provider: str
    fallback_reason: str
    ollama_base_url: str
    ollama_instruct_model: str
    ollama_thinking_model: str
    ollama_thinking_validator_model: str
    ollama_vision_model: str
    google_api_key: str
    gemini_instruct_model: str
    gemini_thinking_model: str
    gemini_thinking_validator_model: str
    gemini_vision_model: str

    @property
    def api_key_set(self) -> bool:
        return bool(self.google_api_key)


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
    google_api_key = _get_google_api_key()
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
    elif requested_provider == "gemini" and not google_api_key:
        provider = DEFAULT_PROVIDER
        fallback_reason = "missing_google_api_key"
        _warn_once(
            fallback_reason,
            "LLM_PROVIDER=gemini이지만 GOOGLE_API_KEY/GEMINI_API_KEY가 미설정되어 Ollama로 폴백합니다.",
        )

    return LLMConfig(
        raw_provider=raw_provider,
        requested_provider=requested_provider,
        provider=provider,
        fallback_reason=fallback_reason,
        ollama_base_url=_env("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
        ollama_instruct_model=_env("OLLAMA_INSTRUCT_MODEL", DEFAULT_OLLAMA_INSTRUCT_MODEL),
        ollama_thinking_model=_env("OLLAMA_THINKING_MODEL", DEFAULT_OLLAMA_THINKING_MODEL),
        ollama_thinking_validator_model=_env(
            "OLLAMA_THINKING_VALIDATOR_MODEL",
            "",
            allow_blank=True,
        ),
        ollama_vision_model=_env("OLLAMA_VISION_MODEL", DEFAULT_OLLAMA_VISION_MODEL),
        google_api_key=google_api_key,
        gemini_instruct_model=_env("GEMINI_INSTRUCT_MODEL", DEFAULT_GEMINI_INSTRUCT_MODEL),
        gemini_thinking_model=_env("GEMINI_THINKING_MODEL", DEFAULT_GEMINI_THINKING_MODEL),
        gemini_thinking_validator_model=_env(
            "GEMINI_THINKING_VALIDATOR_MODEL",
            "",
            allow_blank=True,
        ),
        gemini_vision_model=_env("GEMINI_VISION_MODEL", DEFAULT_GEMINI_VISION_MODEL),
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


# ==========================================
# Gemini LLM 생성
# ==========================================

def _create_gemini_llm(
    model: str,
    temperature: float = 0.3,
    google_api_key: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Google Gemini ChatModel 생성"""
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError:
        raise ImportError(
            "LLM_PROVIDER=gemini 사용 시 langchain-google-genai 패키지가 필요합니다.\n"
            "  pip install langchain-google-genai"
        )

    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=google_api_key or get_llm_config().google_api_key,
        temperature=temperature,
        **kwargs,
    )


# ==========================================
# 통합 팩토리 함수 (에이전트가 호출하는 인터페이스)
# ==========================================

def get_instruct_llm() -> Any:
    """
    Instruct (빠른 분석) LLM

    - Supervisor, Researcher, Quant, Chartist용
    - 빠르고 가벼운 추론

    Returns:
        LangChain BaseChatModel
    """
    config = get_llm_config()
    provider = config.provider

    if provider == "mock":
        llm = MockChatModel("instruct")
        logger.debug("🤖 Instruct LLM: Mock")
    elif provider == "gemini":
        llm = _create_gemini_llm(
            config.gemini_instruct_model,
            temperature=0.3,
            google_api_key=config.google_api_key,
        )
        logger.debug("🤖 Instruct LLM: Gemini (%s)", config.gemini_instruct_model)
    else:
        llm = _create_ollama_llm(
            config.ollama_instruct_model,
            temperature=0.3,
            reasoning=False,
            base_url=config.ollama_base_url,
        )
        logger.debug("🤖 Instruct LLM: Ollama (%s)", config.ollama_instruct_model)

    return llm


def get_thinking_llm() -> Any:
    """
    Thinking (깊은 추론) LLM

    - Strategist, Risk Manager용
    - 복잡한 맥락 추론, 트레이드오프 판단

    Returns:
        LangChain BaseChatModel
    """
    config = get_llm_config()
    provider = config.provider

    if provider == "mock":
        llm = MockChatModel("thinking")
        logger.debug("🧠 Thinking LLM: Mock")
    elif provider == "gemini":
        llm = _create_gemini_llm(
            config.gemini_thinking_model,
            temperature=1,  # Gemini Thinking은 temperature=1 권장
            google_api_key=config.google_api_key,
        )
        logger.debug("🧠 Thinking LLM: Gemini (%s)", config.gemini_thinking_model)
    else:
        llm = _create_ollama_llm(
            config.ollama_thinking_model,
            temperature=0.5,
            reasoning=False,
            base_url=config.ollama_base_url,
        )
        logger.debug("🧠 Thinking LLM: Ollama (%s)", config.ollama_thinking_model)

    return llm


def get_thinking_validator_llm() -> Optional[Any]:
    """
    Thinking Validator (최종 판단 교차 검증) LLM

    - Risk Manager 최종 판단 재검토용
    - 미설정 시 None 반환
    """
    config = get_llm_config()
    provider = config.provider

    if provider == "mock":
        return MockChatModel("thinking_validator")

    if provider == "gemini":
        if not config.gemini_thinking_validator_model:
            return None
        llm = _create_gemini_llm(
            config.gemini_thinking_validator_model,
            temperature=0.7,
            google_api_key=config.google_api_key,
        )
        logger.debug(
            "🧪 Thinking Validator LLM: Gemini (%s)",
            config.gemini_thinking_validator_model,
        )
        return llm

    if not config.ollama_thinking_validator_model:
        return None

    llm = _create_ollama_llm(
        config.ollama_thinking_validator_model,
        temperature=0.5,
        reasoning=False,
        base_url=config.ollama_base_url,
    )
    logger.debug(
        "🧪 Thinking Validator LLM: Ollama (%s)",
        config.ollama_thinking_validator_model,
    )
    return llm


def get_vision_llm() -> Any:
    """
    Vision (이미지 분석) LLM

    - Researcher의 차트/그래프 분석에 사용
    - 멀티모달 지원

    Returns:
        LangChain BaseChatModel
    """
    config = get_llm_config()
    provider = config.provider

    if provider == "mock":
        llm = MockChatModel("vision")
        logger.debug("👁️ Vision LLM: Mock")
    elif provider == "gemini":
        llm = _create_gemini_llm(
            config.gemini_vision_model,
            temperature=0.3,
            google_api_key=config.google_api_key,
        )
        logger.debug("👁️ Vision LLM: Gemini (%s)", config.gemini_vision_model)
    else:
        llm = _create_ollama_llm(
            config.ollama_vision_model,
            temperature=0.3,
            reasoning=False,
            base_url=config.ollama_base_url,
        )
        logger.debug("👁️ Vision LLM: Ollama (%s)", config.ollama_vision_model)

    return llm


# ==========================================
# 하위 호환 별칭 (기존 코드 깨지지 않도록)
# ==========================================
# 기존 코드에서 get_gemini_llm(), get_gemini_vision_llm()을 직접 호출하는 경우 대비
get_gemini_llm = get_instruct_llm
get_gemini_vision_llm = get_vision_llm


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

    if provider == "mock":
        info.update({
            "instruct_model": "mock",
            "thinking_model": "mock",
            "thinking_validator_model": "mock",
            "vision_model": "mock",
        })
        return info

    if provider == "gemini":
        info.update({
            "instruct_model": config.gemini_instruct_model,
            "thinking_model": config.gemini_thinking_model,
            "thinking_validator_model": config.gemini_thinking_validator_model,
            "vision_model": config.gemini_vision_model,
            "api_key_set": config.api_key_set,
        })
        return info

    info.update({
        "base_url": config.ollama_base_url,
        "instruct_model": config.ollama_instruct_model,
        "thinking_model": config.ollama_thinking_model,
        "thinking_validator_model": config.ollama_thinking_validator_model,
        "vision_model": config.ollama_vision_model,
    })
    return info


# ==========================================
# Vision Analyzer (멀티모달 헬퍼)
# ==========================================

class VisionAnalyzer:
    """
    이미지 분석기 (Provider 자동 전환)

    - 증권 리포트의 차트/그래프 분석
    - Base64 이미지 입력 지원
    - Ollama(llava) / Gemini 자동 선택
    """

    def __init__(self):
        self.llm = get_vision_llm()

    def analyze_image(
        self,
        image_base64: str,
        prompt: str = "이 이미지를 분석해주세요.",
        mime_type: str = "image/png"
    ) -> str:
        """
        단일 이미지 분석

        Args:
            image_base64: Base64 인코딩된 이미지
            prompt: 분석 프롬프트
            mime_type: 이미지 MIME 타입

        Returns:
            분석 결과 텍스트
        """
        from langchain_core.messages import HumanMessage

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
                }
            ]
        )

        response = self.llm.invoke([message])
        return response.content

    def analyze_multiple_images(
        self,
        images: List[Dict],
        prompt: str = "다음 이미지들을 분석해주세요."
    ) -> str:
        """
        여러 이미지 동시 분석

        Args:
            images: [{"base64": str, "mime_type": str}, ...]
            prompt: 분석 프롬프트

        Returns:
            통합 분석 결과
        """
        from langchain_core.messages import HumanMessage

        content = [{"type": "text", "text": prompt}]

        for img in images:
            mime_type = img.get("mime_type", "image/png")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{img['base64']}"}
            })

        message = HumanMessage(content=content)
        response = self.llm.invoke([message])
        return response.content

    def analyze_report_images(
        self,
        image_data_list: List[Dict],
        stock_name: str
    ) -> str:
        """
        증권 리포트 이미지 분석 (RAG 결과용)

        Args:
            image_data_list: RAG에서 반환된 이미지 데이터 리스트
                [{"image_base64": str, "source": str, "page_num": int, "text_fallback": str}, ...]
            stock_name: 분석 대상 종목명

        Returns:
            차트/그래프 분석 결과
        """
        if not image_data_list:
            return "분석할 이미지가 없습니다."

        valid_images = [
            img for img in image_data_list
            if img.get("image_base64")
        ]

        if not valid_images:
            fallback_texts = [
                f"[{img.get('source', 'unknown')} - 페이지 {img.get('page_num', '?')}]\n{img.get('text_fallback', '')}"
                for img in image_data_list
            ]
            return "이미지 데이터 없음. 텍스트 정보:\n" + "\n\n".join(fallback_texts)

        prompt = f"""
당신은 증권 애널리스트입니다. 다음은 '{stock_name}'에 대한 증권사 리포트의 차트/그래프 이미지입니다.

각 이미지를 분석하고 다음 정보를 추출해주세요:

1. **차트 유형**: (매출 추이, 주가 차트, 시장점유율, 실적 전망 등)
2. **핵심 데이터 포인트**: (숫자, 비율, 성장률 등)
3. **트렌드 분석**: (상승/하락/보합, 변곡점)
4. **투자 시사점**: (긍정적/부정적 신호)

분석 결과를 체계적으로 정리해주세요. 한글로 작성하세요.
"""

        content = [{"type": "text", "text": prompt}]

        for i, img in enumerate(valid_images[:5]):
            source = img.get("source", "unknown")
            page_num = img.get("page_num", "?")

            content.append({
                "type": "text",
                "text": f"\n[이미지 {i+1}] 출처: {source}, 페이지: {page_num}"
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img['image_base64']}"}
            })

        message = HumanMessage(content=content)

        try:
            response = self.llm.invoke([message])
            return response.content
        except Exception as e:
            return f"이미지 분석 중 오류 발생: {str(e)}"


# 하위 호환 별칭
GeminiVisionAnalyzer = VisionAnalyzer
