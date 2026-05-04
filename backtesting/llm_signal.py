from __future__ import annotations

"""Point-in-time LLM scoring for theme leader backtests."""

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field

from backtesting.temporal_rag import TemporalRAG
from src.agents.llm_config import get_instruct_llm, get_llm_info
from src.config.settings import get_data_dir

logger = logging.getLogger(__name__)


PROMPT_VERSION = "temporal_theme_leader_llm_v1"


class LLMThemeLeaderEvaluation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    llm_score: int = 50
    confidence: int = 50
    theme_fit_score: int = 50
    catalyst_score: int = 50
    risk_score: int = 50
    summary: str = ""
    catalysts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class LLMScorerConfig:
    data_dir: str
    theme: str
    theme_key: str
    model_name: str
    provider: str
    context_docs: int = 5
    cache_path: str = ""
    prompt_version: str = PROMPT_VERSION


class TemporalLLMStockScorer:
    """Score candidate stocks with an LLM using only as-of-date context."""

    def __init__(
        self,
        *,
        data_dir: str | Path | None = None,
        theme: str = "AI",
        theme_key: str = "ai",
        context_docs: int = 5,
        cache_path: str | Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.theme = theme
        self.theme_key = theme_key
        self.context_docs = max(1, int(context_docs))
        self.rag = TemporalRAG(data_dir=str(self.data_dir), theme_key=theme_key)
        self.llm_info = get_llm_info()
        self.provider = str(self.llm_info.get("provider") or "")
        self.model_name = str(
            self.llm_info.get("instruct_model")
            or self.llm_info.get("thinking_model")
            or self.provider
            or "unknown"
        )
        self.cache_path = Path(cache_path) if cache_path else self._default_cache_path()
        self.cache: Dict[str, Dict[str, Any]] = {}
        self._load_cache()
        self.llm = get_instruct_llm()
        self.structured_llm = self._build_structured_llm(self.llm)

    def metadata(self) -> Dict[str, Any]:
        return asdict(
            LLMScorerConfig(
                data_dir=str(self.data_dir),
                theme=self.theme,
                theme_key=self.theme_key,
                model_name=self.model_name,
                provider=self.provider,
                context_docs=self.context_docs,
                cache_path=str(self.cache_path),
            )
        )

    def score(self, *, as_of_ymd: str, row: Dict[str, Any]) -> Dict[str, Any]:
        key = self._cache_key(as_of_ymd=as_of_ymd, row=row)
        cached = self.cache.get(key)
        if cached:
            return {**cached, "cache_hit": True}

        context = self._context_for_row(as_of_ymd=as_of_ymd, row=row)
        prompt = self._build_prompt(as_of_ymd=as_of_ymd, row=row, context=context)
        payload = self._invoke(prompt, row=row)
        result = self._normalize_payload(payload)
        result.update(
            {
                "cache_hit": False,
                "llm_model": self.model_name,
                "llm_provider": self.provider,
                "llm_prompt_version": PROMPT_VERSION,
                "llm_context_docs": self.context_docs,
            }
        )
        self.cache[key] = result
        self._append_cache(key, result)
        return result

    def _default_cache_path(self) -> Path:
        safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.model_name or "unknown")
        return self.data_dir / "backtest_results" / "llm_cache" / self.theme_key / f"{safe_model}.jsonl"

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        for row in _iter_jsonl(self.cache_path):
            key = str(row.get("cache_key") or "")
            result = row.get("result")
            if key and isinstance(result, dict):
                self.cache[key] = result

    def _append_cache(self, key: str, result: Dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"cache_key": key, "result": result}, ensure_ascii=False) + "\n")

    def _cache_key(self, *, as_of_ymd: str, row: Dict[str, Any]) -> str:
        return "|".join(
            [
                PROMPT_VERSION,
                self.provider,
                self.model_name,
                self.theme_key,
                as_of_ymd,
                str(row.get("stock_code") or ""),
                str(round(float(row.get("leader_score") or 0.0), 2)),
                str(round(float(row.get("return_20d") or 0.0), 4)),
                str(round(float(row.get("return_60d") or 0.0), 4)),
            ]
        )

    def _context_for_row(self, *, as_of_ymd: str, row: Dict[str, Any]) -> str:
        stock_name = str(row.get("stock_name") or "")
        stock_code = str(row.get("stock_code") or "")
        query = f"{stock_name} {stock_code} {self.theme} AI 수혜 성장 실적 공시 리스크"
        return self.rag.search_for_context(
            query=query,
            as_of_date=as_of_ymd,
            top_k=self.context_docs,
            source_types=["news", "general_news", "dart", "forum"],
            stock_code=stock_code,
        )

    def _build_prompt(self, *, as_of_ymd: str, row: Dict[str, Any], context: str) -> str:
        feature_payload = {
            "stock_name": row.get("stock_name"),
            "stock_code": row.get("stock_code"),
            "deterministic_leader_score": row.get("leader_score"),
            "return_5d": round(float(row.get("return_5d") or 0.0), 4),
            "return_20d": round(float(row.get("return_20d") or 0.0), 4),
            "return_60d": round(float(row.get("return_60d") or 0.0), 4),
            "trend_150d": round(float(row.get("trend_150d") or 0.0), 4),
            "volatility_20d": round(float(row.get("volatility_20d") or 0.0), 4),
            "volume_ratio_20d": round(float(row.get("volume_ratio_20d") or 0.0), 4),
            "doc_counts": row.get("doc_counts") or {},
        }
        return f"""
당신은 과거 시점 기준 AI 테마 주도주를 평가하는 LLM 심사역입니다.
아래 정보는 모두 as_of={as_of_ymd} 이전에 관측된 데이터입니다.

[중요 제약]
- 외부 지식과 미래 데이터 사용 금지
- 아래 feature와 문서 컨텍스트만 근거로 판단
- 목적은 향후 5거래일 내 AI 테마 주도주 가능성 점수화
- JSON만 출력

[후보 feature snapshot]
{json.dumps(feature_payload, ensure_ascii=False, indent=2)}

[시점 제한 문서 컨텍스트]
{context[:5000]}

다음 JSON 형식으로만 답하세요:
{{
  "llm_score": 0,
  "confidence": 0,
  "theme_fit_score": 0,
  "catalyst_score": 0,
  "risk_score": 0,
  "summary": "",
  "catalysts": ["", ""],
  "risks": ["", ""]
}}

점수 기준:
- llm_score: AI 테마 주도주 가능성, 0~100. 높을수록 좋음.
- confidence: 문서/feature 근거 충분성, 0~100.
- theme_fit_score: AI 테마 적합도, 0~100.
- catalyst_score: 단기 촉매 강도, 0~100.
- risk_score: 리스크 강도, 0~100. 높을수록 위험함.
"""

    def _invoke(self, prompt: str, *, row: Dict[str, Any]) -> Dict[str, Any]:
        if self.provider == "mock":
            return self._mock_payload(row)

        if self.structured_llm is not None:
            try:
                structured = self.structured_llm.invoke(prompt)
                return self._validate_structured_payload(structured)
            except Exception as exc:
                logger.warning("LLM structured score failed: %s", exc)

        response = self.llm.invoke(prompt)
        content = _response_to_text(getattr(response, "content", response))
        payload = _extract_first_json_object(content)
        if not payload:
            raise ValueError(f"LLM response did not include JSON: {content[:200]}")
        return self._validate_structured_payload(payload)

    @staticmethod
    def _build_structured_llm(llm: Any):
        if not hasattr(llm, "with_structured_output"):
            return None
        try:
            return llm.with_structured_output(LLMThemeLeaderEvaluation, method="json_schema")
        except Exception:
            try:
                return llm.with_structured_output(LLMThemeLeaderEvaluation, method="json_mode")
            except Exception:
                return None

    @staticmethod
    def _validate_structured_payload(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, BaseModel):
            return payload.model_dump()
        if not isinstance(payload, dict):
            raise TypeError(f"Expected dict payload, got {type(payload).__name__}")
        return LLMThemeLeaderEvaluation.model_validate(payload).model_dump()

    @staticmethod
    def _mock_payload(row: Dict[str, Any]) -> Dict[str, Any]:
        base = float(row.get("leader_score") or 50.0)
        doc_counts = row.get("doc_counts") or {}
        docs = sum(int(value or 0) for value in doc_counts.values())
        score = max(0, min(100, round(base * 0.75 + min(25, docs * 2))))
        return {
            "llm_score": score,
            "confidence": min(95, 45 + docs * 5),
            "theme_fit_score": score,
            "catalyst_score": min(100, score + 5),
            "risk_score": max(0, 100 - score),
            "summary": "mock LLM score from deterministic/document features",
            "catalysts": [],
            "risks": [],
        }

    @staticmethod
    def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        def bounded_int(key: str, default: int = 50) -> int:
            try:
                value = int(float(payload.get(key, default)))
            except (TypeError, ValueError):
                value = default
            return max(0, min(100, value))

        return {
            "llm_score": bounded_int("llm_score"),
            "llm_confidence": bounded_int("confidence"),
            "llm_theme_fit_score": bounded_int("theme_fit_score"),
            "llm_catalyst_score": bounded_int("catalyst_score"),
            "llm_risk_score": bounded_int("risk_score"),
            "llm_summary": str(payload.get("summary") or "")[:500],
            "llm_catalysts": _clean_string_list(payload.get("catalysts")),
            "llm_risks": _clean_string_list(payload.get("risks")),
        }


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:160] for item in value if str(item).strip()][:5]


def _response_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    chunks.append(str(text))
            else:
                chunks.append(str(item))
        return "\n".join(chunks)
    return str(content)


def _extract_first_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
