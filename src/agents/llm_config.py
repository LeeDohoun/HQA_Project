# 파일: src/agents/llm_config.py
"""
LLM 설정 모듈 (Provider 패턴)

LLM_PROVIDER 환경변수로 Ollama / Gemini 전환 가능.

모델 구분:
- Instruct (빠름): 정보 수집, 요약, 패턴 인식
- Thinking (깊은 추론): 헤게모니 판단, 최종 결정
- Thinking Validator (교차 검증): 최종 결정 재검토
- Vision (이미지): 차트/그래프 분석

에이전트별 모델:
- Supervisor: Instruct (쿼리 분석/라우팅)
- Researcher: Instruct + Vision (정보 수집)
- Strategist: Thinking (헤게모니 판단)
- Quant: Instruct (재무 분석)
- Chartist: Instruct (기술 분석)
- Risk Manager: Thinking (최종 판단)

설정 (.env):
  LLM_PROVIDER=ollama          # "ollama" | "gemini" (기본: ollama)

  # --- Ollama 모드 ---
  OLLAMA_BASE_URL=http://localhost:11434
  OLLAMA_INSTRUCT_MODEL=llama3.1:8b
  OLLAMA_THINKING_MODEL=deepseek-r1:14b
  OLLAMA_THINKING_VALIDATOR_MODEL=qwen3.5:14b
  OLLAMA_VISION_MODEL=llava:13b

  # --- Gemini 모드 ---
  GOOGLE_API_KEY=AIza...
  GEMINI_INSTRUCT_MODEL=gemini-2.5-flash-lite
  GEMINI_THINKING_MODEL=gemini-2.5-flash-preview-04-17
  GEMINI_THINKING_VALIDATOR_MODEL=gemini-2.5-flash
  GEMINI_VISION_MODEL=gemini-2.5-flash-preview-04-17
"""

import os
import logging
from typing import List, Dict, Optional
from langchain_core.messages import HumanMessage
from langchain_core.language_models import BaseChatModel
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ==========================================
# Provider 설정
# ==========================================

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower().strip()

# Ollama 설정
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_INSTRUCT_MODEL = os.getenv("OLLAMA_INSTRUCT_MODEL", "llama3.1:8b")
OLLAMA_THINKING_MODEL = os.getenv("OLLAMA_THINKING_MODEL", "deepseek-r1:14b")
OLLAMA_THINKING_VALIDATOR_MODEL = os.getenv("OLLAMA_THINKING_VALIDATOR_MODEL", "").strip()
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava:13b")

# Gemini 설정
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GEMINI_INSTRUCT_MODEL = os.getenv("GEMINI_INSTRUCT_MODEL", "gemini-2.5-flash-lite")
GEMINI_THINKING_MODEL = os.getenv("GEMINI_THINKING_MODEL", "gemini-2.5-flash-preview-04-17")
GEMINI_THINKING_VALIDATOR_MODEL = os.getenv("GEMINI_THINKING_VALIDATOR_MODEL", "").strip()
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash-preview-04-17")


def _get_provider() -> str:
    """현재 LLM Provider 반환 (검증 포함)"""
    if LLM_PROVIDER == "gemini":
        if not GOOGLE_API_KEY:
            logger.warning(
                "⚠️ LLM_PROVIDER=gemini이지만 GOOGLE_API_KEY가 미설정. "
                "Ollama로 폴백합니다."
            )
            return "ollama"
        return "gemini"
    return "ollama"


# ==========================================
# Ollama LLM 생성
# ==========================================

def _create_ollama_llm(model: str, temperature: float = 0.3) -> BaseChatModel:
    """Ollama ChatModel 생성"""
    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=model,
        base_url=OLLAMA_BASE_URL,
        temperature=temperature,
    )


# ==========================================
# Gemini LLM 생성
# ==========================================

def _create_gemini_llm(model: str, temperature: float = 0.3, **kwargs) -> BaseChatModel:
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
        google_api_key=GOOGLE_API_KEY,
        temperature=temperature,
        **kwargs,
    )


# ==========================================
# 통합 팩토리 함수 (에이전트가 호출하는 인터페이스)
# ==========================================

def get_instruct_llm() -> BaseChatModel:
    """
    Instruct (빠른 분석) LLM

    - Supervisor, Researcher, Quant, Chartist용
    - 빠르고 가벼운 추론

    Returns:
        LangChain BaseChatModel
    """
    provider = _get_provider()

    if provider == "gemini":
        llm = _create_gemini_llm(GEMINI_INSTRUCT_MODEL, temperature=0.3)
        logger.debug(f"🤖 Instruct LLM: Gemini ({GEMINI_INSTRUCT_MODEL})")
    else:
        llm = _create_ollama_llm(OLLAMA_INSTRUCT_MODEL, temperature=0.3)
        logger.debug(f"🤖 Instruct LLM: Ollama ({OLLAMA_INSTRUCT_MODEL})")

    return llm


def get_thinking_llm() -> BaseChatModel:
    """
    Thinking (깊은 추론) LLM

    - Strategist, Risk Manager용
    - 복잡한 맥락 추론, 트레이드오프 판단

    Returns:
        LangChain BaseChatModel
    """
    provider = _get_provider()

    if provider == "gemini":
        llm = _create_gemini_llm(
            GEMINI_THINKING_MODEL,
            temperature=1,  # Gemini Thinking은 temperature=1 권장
        )
        logger.debug(f"🧠 Thinking LLM: Gemini ({GEMINI_THINKING_MODEL})")
    else:
        llm = _create_ollama_llm(OLLAMA_THINKING_MODEL, temperature=0.5)
        logger.debug(f"🧠 Thinking LLM: Ollama ({OLLAMA_THINKING_MODEL})")

    return llm


def get_thinking_validator_llm() -> Optional[BaseChatModel]:
    """
    Thinking Validator (최종 판단 교차 검증) LLM

    - Risk Manager 최종 판단 재검토용
    - 미설정 시 None 반환
    """
    provider = _get_provider()

    if provider == "gemini":
        if not GEMINI_THINKING_VALIDATOR_MODEL:
            return None
        llm = _create_gemini_llm(
            GEMINI_THINKING_VALIDATOR_MODEL,
            temperature=0.7,
        )
        logger.debug(
            f"🧪 Thinking Validator LLM: Gemini ({GEMINI_THINKING_VALIDATOR_MODEL})"
        )
        return llm

    if not OLLAMA_THINKING_VALIDATOR_MODEL:
        return None

    llm = _create_ollama_llm(OLLAMA_THINKING_VALIDATOR_MODEL, temperature=0.5)
    logger.debug(
        f"🧪 Thinking Validator LLM: Ollama ({OLLAMA_THINKING_VALIDATOR_MODEL})"
    )
    return llm


def get_vision_llm() -> BaseChatModel:
    """
    Vision (이미지 분석) LLM

    - Researcher의 차트/그래프 분석에 사용
    - 멀티모달 지원

    Returns:
        LangChain BaseChatModel
    """
    provider = _get_provider()

    if provider == "gemini":
        llm = _create_gemini_llm(GEMINI_VISION_MODEL, temperature=0.3)
        logger.debug(f"👁️ Vision LLM: Gemini ({GEMINI_VISION_MODEL})")
    else:
        llm = _create_ollama_llm(OLLAMA_VISION_MODEL, temperature=0.3)
        logger.debug(f"👁️ Vision LLM: Ollama ({OLLAMA_VISION_MODEL})")

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

def get_llm_info() -> Dict[str, str]:
    """현재 LLM 설정 정보 반환 (디버깅용)"""
    provider = _get_provider()

    if provider == "gemini":
        return {
            "provider": "gemini",
            "instruct_model": GEMINI_INSTRUCT_MODEL,
            "thinking_model": GEMINI_THINKING_MODEL,
            "thinking_validator_model": GEMINI_THINKING_VALIDATOR_MODEL,
            "vision_model": GEMINI_VISION_MODEL,
            "api_key_set": bool(GOOGLE_API_KEY),
        }
    else:
        return {
            "provider": "ollama",
            "base_url": OLLAMA_BASE_URL,
            "instruct_model": OLLAMA_INSTRUCT_MODEL,
            "thinking_model": OLLAMA_THINKING_MODEL,
            "thinking_validator_model": OLLAMA_THINKING_VALIDATOR_MODEL,
            "vision_model": OLLAMA_VISION_MODEL,
        }


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
