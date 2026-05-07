# 파일: src/utils/prompt_loader.py
"""
에이전트 프롬프트 로더

prompts/{agent}/{name}.md 파일에서 프롬프트 템플릿을 로드하고
변수를 치환하여 최종 프롬프트를 반환합니다.

사용법:
    from src.utils.prompt_loader import load_prompt

    prompt = load_prompt("risk_manager", "decision",
        stock_name="삼성전자",
        stock_code="005930",
        analyst_moat_score=32,
        ...
    )
"""

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 프로젝트 루트 기준 prompts 디렉토리
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_PROMPTS_DIR = _PROJECT_ROOT / "prompts"


def load_prompt(agent: str, name: str, **kwargs: Any) -> str:
    """
    프롬프트 파일 로드 + 변수 치환

    Args:
        agent: 에이전트명 (예: "risk_manager", "strategist")
        name: 프롬프트명 (예: "decision", "hegemony")
        **kwargs: 템플릿 변수 (f-string의 {변수}에 대응)

    Returns:
        변수가 치환된 프롬프트 문자열

    Raises:
        FileNotFoundError: 프롬프트 파일이 없을 때

    Example:
        >>> prompt = load_prompt("risk_manager", "decision",
        ...     stock_name="삼성전자", stock_code="005930")
    """
    path = _PROMPTS_DIR / agent / f"{name}.md"

    if not path.exists():
        raise FileNotFoundError(
            f"프롬프트 파일을 찾을 수 없습니다: {path}\n"
            f"→ prompts/{agent}/{name}.md 파일을 생성하세요."
        )

    template = path.read_text(encoding="utf-8")

    if kwargs:
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.warning(
                f"[PromptLoader] 변수 치환 실패: {e} — "
                f"prompts/{agent}/{name}.md에 존재하지 않는 변수"
            )
            # 부분 치환 (실패한 변수는 그대로 유지)
            return _safe_format(template, **kwargs)

    return template


def load_prompt_optional(
    agent: str, name: str, fallback: Optional[str] = None, **kwargs: Any
) -> Optional[str]:
    """
    프롬프트 파일이 있으면 로드, 없으면 fallback 반환

    기존 하드코딩 프롬프트와 호환성을 위해 사용합니다.
    prompts/ 파일이 있으면 우선 사용하고, 없으면 코드 내 기본값을 사용합니다.

    Args:
        agent: 에이전트명
        name: 프롬프트명
        fallback: 파일이 없을 때 반환할 기본 프롬프트
        **kwargs: 템플릿 변수

    Returns:
        프롬프트 문자열 또는 None
    """
    try:
        return load_prompt(agent, name, **kwargs)
    except FileNotFoundError:
        if fallback and kwargs:
            try:
                return fallback.format(**kwargs)
            except (KeyError, IndexError):
                return fallback
        return fallback


def list_prompts(agent: Optional[str] = None) -> dict:
    """
    사용 가능한 프롬프트 목록 조회

    Args:
        agent: 특정 에이전트만 조회 (None이면 전체)

    Returns:
        {agent: [prompt_names]} 딕셔너리
    """
    result = {}

    if not _PROMPTS_DIR.exists():
        return result

    if agent:
        agent_dir = _PROMPTS_DIR / agent
        if agent_dir.exists():
            result[agent] = [f.stem for f in agent_dir.glob("*.md")]
    else:
        for agent_dir in sorted(_PROMPTS_DIR.iterdir()):
            if agent_dir.is_dir() and not agent_dir.name.startswith("."):
                result[agent_dir.name] = [f.stem for f in agent_dir.glob("*.md")]

    return result


def _safe_format(template: str, **kwargs: Any) -> str:
    """실패하지 않는 부분 치환"""
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", str(value))
    return result
