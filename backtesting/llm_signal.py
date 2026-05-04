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
from src.agents.llm_config import get_instruct_llm, get_llm_info, get_thinking_llm
from src.config.settings import get_data_dir

logger = logging.getLogger(__name__)


PROMPT_VERSION = "temporal_theme_leader_llm_v1"
MULTI_AGENT_PROMPT_VERSION = "temporal_theme_leader_multi_agent_v2"


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


class AnalystBacktestEvaluation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme_fit_score: int = 50
    moat_score: int = 50
    growth_score: int = 50
    catalyst_score: int = 50
    risk_score: int = 50
    confidence: int = 50
    summary: str = ""
    catalysts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class QuantBacktestEvaluation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    valuation_score: int = 50
    profitability_score: int = 50
    growth_score: int = 50
    stability_score: int = 50
    risk_score: int = 50
    confidence: int = 50
    summary: str = ""
    risks: list[str] = Field(default_factory=list)


class RiskManagerBacktestEvaluation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    final_score: int = 50
    confidence: int = 50
    risk_score: int = 50
    action: str = "HOLD"
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
    mode: str = "single_llm"


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
                mode="single_llm",
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


class TemporalMultiAgentStockScorer:
    """Point-in-time Analyst/Quant/Chartist/RiskManager scorer for backtests."""

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
        self.thinking_model_name = str(self.llm_info.get("thinking_model") or self.model_name)
        self.cache_path = Path(cache_path) if cache_path else self._default_cache_path()
        self.cache: Dict[str, Dict[str, Any]] = {}
        self._load_cache()
        self.instruct_llm = get_instruct_llm()
        self.thinking_llm = get_thinking_llm()

    def metadata(self) -> Dict[str, Any]:
        payload = asdict(
            LLMScorerConfig(
                data_dir=str(self.data_dir),
                theme=self.theme,
                theme_key=self.theme_key,
                model_name=self.model_name,
                provider=self.provider,
                context_docs=self.context_docs,
                cache_path=str(self.cache_path),
                prompt_version=MULTI_AGENT_PROMPT_VERSION,
                mode="multi_agent",
            )
        )
        payload["thinking_model_name"] = self.thinking_model_name
        payload["agents"] = ["analyst", "quant", "chartist", "risk_manager"]
        return payload

    def score(self, *, as_of_ymd: str, row: Dict[str, Any]) -> Dict[str, Any]:
        key = self._cache_key(as_of_ymd=as_of_ymd, row=row)
        cached = self.cache.get(key)
        if cached:
            return {**cached, "cache_hit": True}

        context = self._context_for_row(as_of_ymd=as_of_ymd, row=row)
        feature_payload = _feature_payload(row)

        if self.provider == "mock":
            result = self._mock_multi_agent_payload(row)
        else:
            analyst = self._evaluate_analyst(as_of_ymd, row, feature_payload, context)
            quant = self._evaluate_quant(as_of_ymd, row, feature_payload, context)
            chartist = self._evaluate_chartist(row)
            risk = self._evaluate_risk_manager(as_of_ymd, row, feature_payload, analyst, quant, chartist)
            result = self._normalize_multi_agent_payload(analyst, quant, chartist, risk)

        result.update(
            {
                "cache_hit": False,
                "llm_model": self.model_name,
                "llm_provider": self.provider,
                "llm_prompt_version": MULTI_AGENT_PROMPT_VERSION,
                "llm_context_docs": self.context_docs,
                "llm_mode": "multi_agent",
            }
        )
        self.cache[key] = result
        self._append_cache(key, result)
        return result

    def _default_cache_path(self) -> Path:
        safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.model_name or "unknown")
        return (
            self.data_dir
            / "backtest_results"
            / "llm_cache"
            / self.theme_key
            / f"{safe_model}.multi_agent.jsonl"
        )

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
                MULTI_AGENT_PROMPT_VERSION,
                self.provider,
                self.model_name,
                self.thinking_model_name,
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
        query = f"{stock_name} {stock_code} {self.theme} AI 테마 수혜 성장 실적 공시 리스크"
        return self.rag.search_for_context(
            query=query,
            as_of_date=as_of_ymd,
            top_k=self.context_docs,
            source_types=["news", "general_news", "dart", "forum"],
            stock_code=stock_code,
        )

    def _evaluate_analyst(
        self,
        as_of_ymd: str,
        row: Dict[str, Any],
        feature_payload: Dict[str, Any],
        context: str,
    ) -> Dict[str, Any]:
        prompt = f"""
당신은 멀티 에이전트 백테스트의 AnalystAgent입니다.
as_of={as_of_ymd} 이전 데이터만 근거로 '{self.theme}' 테마에서
'{row.get("stock_name")}'({row.get("stock_code")})의 주도주 가능성을 평가하세요.

[금지]
- 외부 지식 사용 금지
- 미래 데이터 사용 금지
- 모든 점수는 0~100 정수. 50은 중립, 0은 매우 부정, 100은 매우 긍정
- risk_score는 높을수록 위험
- JSON 외 출력 금지

[feature snapshot]
{json.dumps(feature_payload, ensure_ascii=False, indent=2)}

[point-in-time documents]
{context[:5000]}

JSON:
{{
  "theme_fit_score": 0,
  "moat_score": 0,
  "growth_score": 0,
  "catalyst_score": 0,
  "risk_score": 0,
  "confidence": 0,
  "summary": "",
  "catalysts": ["", ""],
  "risks": ["", ""]
}}
"""
        try:
            return _invoke_schema(self.instruct_llm, prompt, AnalystBacktestEvaluation)
        except Exception as exc:
            logger.warning("AnalystAgent backtest score failed: %s", exc)
            return self._fallback_analyst(row)

    def _evaluate_quant(
        self,
        as_of_ymd: str,
        row: Dict[str, Any],
        feature_payload: Dict[str, Any],
        context: str,
    ) -> Dict[str, Any]:
        prompt = f"""
당신은 멀티 에이전트 백테스트의 QuantAgent입니다.
as_of={as_of_ymd} 이전 DART/뉴스/feature만 보고
'{row.get("stock_name")}'({row.get("stock_code")})의 기초체력과 재무 리스크를 보수적으로 평가하세요.

[점수 규칙]
- valuation/profitability/growth/stability_score는 0~100 정수
- 50은 중립입니다. 근거가 부족하면 40~50 사이를 사용하고, 직접적인 재무 악화 근거가 있을 때만 40 미만을 사용하세요
- risk_score는 높을수록 위험합니다. 근거가 부족하면 55~65 사이를 사용하세요
- JSON만 출력하세요

[feature snapshot]
{json.dumps(feature_payload, ensure_ascii=False, indent=2)}

[point-in-time documents]
{context[:5000]}

JSON:
{{
  "valuation_score": 0,
  "profitability_score": 0,
  "growth_score": 0,
  "stability_score": 0,
  "risk_score": 0,
  "confidence": 0,
  "summary": "",
  "risks": ["", ""]
}}
"""
        try:
            return _invoke_schema(self.instruct_llm, prompt, QuantBacktestEvaluation)
        except Exception as exc:
            logger.warning("QuantAgent backtest score failed: %s", exc)
            return self._fallback_quant(row)

    def _evaluate_chartist(self, row: Dict[str, Any]) -> Dict[str, Any]:
        ret20 = float(row.get("return_20d") or 0.0)
        ret60 = float(row.get("return_60d") or 0.0)
        trend = float(row.get("trend_150d") or 0.0)
        vol20 = float(row.get("volatility_20d") or 0.0)
        volume = float(row.get("volume_ratio_20d") or 0.0)
        trend_score = _bounded_int(50 + trend * 80)
        momentum_score = _bounded_int(50 + ret20 * 120 + ret60 * 45)
        volatility_score = _bounded_int(100 - vol20 * 55)
        volume_score = _bounded_int(35 + min(volume, 3.0) * 20)
        total = _bounded_int(
            0.30 * trend_score
            + 0.35 * momentum_score
            + 0.20 * volatility_score
            + 0.15 * volume_score
        )
        return {
            "trend_score": trend_score,
            "momentum_score": momentum_score,
            "volatility_score": volatility_score,
            "volume_score": volume_score,
            "total_score": total,
            "confidence": 80,
            "summary": (
                f"trend={trend_score}, momentum={momentum_score}, "
                f"volatility={volatility_score}, volume={volume_score}"
            ),
        }

    def _evaluate_risk_manager(
        self,
        as_of_ymd: str,
        row: Dict[str, Any],
        feature_payload: Dict[str, Any],
        analyst: Dict[str, Any],
        quant: Dict[str, Any],
        chartist: Dict[str, Any],
    ) -> Dict[str, Any]:
        agent_totals = _agent_totals(analyst, quant, chartist)
        prompt = f"""
당신은 멀티 에이전트 백테스트의 RiskManagerAgent입니다.
아래 Analyst/Quant/Chartist 결과를 종합해 as_of={as_of_ymd} 기준
향후 5거래일 AI 테마 주도주 가능성을 최종 점수화하세요.

[중요]
- 프로젝트 RiskManager의 quick_decision 가중치를 따르세요.
- 권장 최종점수 = Analyst 40% + Quant 35% + Chartist 25%
- 세 에이전트 신호가 충돌하면 Quant 리스크와 단기 과열 리스크를 우선 반영하세요.
- final_score는 아래 calibrated_agent_totals.recommended_final_score에서 ±10점 이상 벗어나지 마세요.
- 모든 점수는 0~100 정수이며 risk_score는 높을수록 위험합니다.

[후보]
{json.dumps(feature_payload, ensure_ascii=False, indent=2)}

[calibrated_agent_totals]
{json.dumps(agent_totals, ensure_ascii=False, indent=2)}

[AnalystAgent]
{json.dumps(analyst, ensure_ascii=False, indent=2)}

[QuantAgent]
{json.dumps(quant, ensure_ascii=False, indent=2)}

[ChartistAgent]
{json.dumps(chartist, ensure_ascii=False, indent=2)}

JSON:
{{
  "final_score": 0,
  "confidence": 0,
  "risk_score": 0,
  "action": "BUY/HOLD/REDUCE",
  "summary": "",
  "catalysts": ["", ""],
  "risks": ["", ""]
}}
"""
        try:
            return _invoke_schema(self.thinking_llm, prompt, RiskManagerBacktestEvaluation)
        except Exception as exc:
            logger.warning("RiskManagerAgent backtest score failed: %s", exc)
            return self._fallback_risk_manager(analyst, quant, chartist)

    def _normalize_multi_agent_payload(
        self,
        analyst: Dict[str, Any],
        quant: Dict[str, Any],
        chartist: Dict[str, Any],
        risk: Dict[str, Any],
    ) -> Dict[str, Any]:
        agent_totals = _agent_totals(analyst, quant, chartist)
        analyst_total = agent_totals["analyst_total"]
        quant_total = agent_totals["quant_total"]
        chartist_total = agent_totals["chartist_total"]
        raw_risk_final_score = _get_score(risk, "final_score")
        final_score = agent_totals["recommended_final_score"]
        confidence = _get_score(risk, "confidence")
        risk_score = _get_score(risk, "risk_score")
        return {
            "llm_score": final_score,
            "llm_confidence": confidence,
            "llm_theme_fit_score": _get_score(analyst, "theme_fit_score"),
            "llm_catalyst_score": _get_score(analyst, "catalyst_score"),
            "llm_risk_score": risk_score,
            "llm_summary": str(risk.get("summary") or "")[:500],
            "llm_catalysts": _clean_string_list(risk.get("catalysts") or analyst.get("catalysts")),
            "llm_risks": _clean_string_list(risk.get("risks") or analyst.get("risks") or quant.get("risks")),
            "llm_agent_scores": {
                "analyst": {
                    "total_score": analyst_total,
                    "theme_fit_score": _get_score(analyst, "theme_fit_score"),
                    "moat_score": _get_score(analyst, "moat_score"),
                    "growth_score": _get_score(analyst, "growth_score"),
                    "catalyst_score": _get_score(analyst, "catalyst_score"),
                    "risk_score": _get_score(analyst, "risk_score"),
                    "confidence": _get_score(analyst, "confidence"),
                    "summary": str(analyst.get("summary") or "")[:300],
                },
                "quant": {
                    "total_score": quant_total,
                    "valuation_score": _get_score(quant, "valuation_score"),
                    "profitability_score": _get_score(quant, "profitability_score"),
                    "growth_score": _get_score(quant, "growth_score"),
                    "stability_score": _get_score(quant, "stability_score"),
                    "risk_score": _get_score(quant, "risk_score"),
                    "confidence": _get_score(quant, "confidence"),
                    "summary": str(quant.get("summary") or "")[:300],
                },
                "chartist": chartist,
                "risk_manager": {
                    "final_score": final_score,
                    "raw_final_score": raw_risk_final_score,
                    "calibrated_final_score": final_score,
                    "confidence": confidence,
                    "risk_score": risk_score,
                    "action": str(risk.get("action") or "HOLD"),
                    "summary": str(risk.get("summary") or "")[:300],
                },
            },
        }

    @staticmethod
    def _fallback_analyst(row: Dict[str, Any]) -> Dict[str, Any]:
        docs = sum(int(v or 0) for v in (row.get("doc_counts") or {}).values())
        score = _bounded_int(float(row.get("leader_score") or 50) + min(15, docs))
        return {
            "theme_fit_score": score,
            "moat_score": score,
            "growth_score": score,
            "catalyst_score": score,
            "risk_score": 50,
            "confidence": 40,
            "summary": "Analyst fallback from feature/document counts",
            "catalysts": [],
            "risks": [],
        }

    @staticmethod
    def _fallback_quant(row: Dict[str, Any]) -> Dict[str, Any]:
        trend = float(row.get("trend_150d") or 0.0)
        score = _bounded_int(50 + trend * 35)
        return {
            "valuation_score": 50,
            "profitability_score": score,
            "growth_score": score,
            "stability_score": 50,
            "risk_score": 50,
            "confidence": 35,
            "summary": "Quant fallback from point-in-time price trend",
            "risks": [],
        }

    @staticmethod
    def _fallback_risk_manager(
        analyst: Dict[str, Any],
        quant: Dict[str, Any],
        chartist: Dict[str, Any],
    ) -> Dict[str, Any]:
        analyst_norm = _bounded_int(
            0.30 * _get_score(analyst, "theme_fit_score")
            + 0.25 * _get_score(analyst, "moat_score")
            + 0.25 * _get_score(analyst, "growth_score")
            + 0.20 * _get_score(analyst, "catalyst_score")
        )
        quant_total = _bounded_int(
            0.25 * _get_score(quant, "valuation_score")
            + 0.25 * _get_score(quant, "profitability_score")
            + 0.25 * _get_score(quant, "growth_score")
            + 0.25 * _get_score(quant, "stability_score")
        )
        final_score = _bounded_int(
            analyst_norm * 0.40
            + quant_total * 0.35
            + _get_score(chartist, "total_score") * 0.25
        )
        return {
            "final_score": final_score,
            "confidence": 40,
            "risk_score": _bounded_int((_get_score(analyst, "risk_score") + _get_score(quant, "risk_score")) / 2),
            "action": "BUY" if final_score >= 65 else "HOLD",
            "summary": "RiskManager fallback weighted Analyst/Quant/Chartist scores",
            "catalysts": [],
            "risks": [],
        }

    def _mock_multi_agent_payload(self, row: Dict[str, Any]) -> Dict[str, Any]:
        analyst = self._fallback_analyst(row)
        quant = self._fallback_quant(row)
        chartist = self._evaluate_chartist(row)
        risk = self._fallback_risk_manager(analyst, quant, chartist)
        result = self._normalize_multi_agent_payload(analyst, quant, chartist, risk)
        result["llm_summary"] = "mock multi-agent score from Analyst/Quant/Chartist/RiskManager fallbacks"
        return result


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:160] for item in value if str(item).strip()][:5]


def _feature_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stock_name": row.get("stock_name"),
        "stock_code": row.get("stock_code"),
        "deterministic_leader_score": row.get("leader_score"),
        "return_5d": round(float(row.get("return_5d") or 0.0), 4),
        "return_20d": round(float(row.get("return_20d") or 0.0), 4),
        "return_60d": round(float(row.get("return_60d") or 0.0), 4),
        "trend_150d": round(float(row.get("trend_150d") or 0.0), 4),
        "volatility_20d": round(float(row.get("volatility_20d") or 0.0), 4),
        "volume_ratio_20d": round(float(row.get("volume_ratio_20d") or 0.0), 4),
        "avg_trading_value_20d": round(float(row.get("avg_trading_value_20d") or 0.0), 2),
        "doc_counts": row.get("doc_counts") or {},
    }


def _agent_totals(
    analyst: Dict[str, Any],
    quant: Dict[str, Any],
    chartist: Dict[str, Any],
) -> Dict[str, int]:
    analyst_total = _bounded_int(
        0.30 * _get_score(analyst, "theme_fit_score")
        + 0.25 * _get_score(analyst, "moat_score")
        + 0.25 * _get_score(analyst, "growth_score")
        + 0.20 * _get_score(analyst, "catalyst_score")
    )
    quant_total = _bounded_int(
        0.25 * _get_score(quant, "valuation_score")
        + 0.25 * _get_score(quant, "profitability_score")
        + 0.25 * _get_score(quant, "growth_score")
        + 0.25 * _get_score(quant, "stability_score")
    )
    chartist_total = _get_score(chartist, "total_score")
    recommended_final_score = _bounded_int(
        analyst_total * 0.40
        + quant_total * 0.35
        + chartist_total * 0.25
    )
    return {
        "analyst_total": analyst_total,
        "quant_total": quant_total,
        "chartist_total": chartist_total,
        "recommended_final_score": recommended_final_score,
    }


def _invoke_schema(llm: Any, prompt: str, schema: type[BaseModel]) -> Dict[str, Any]:
    if hasattr(llm, "with_structured_output"):
        try:
            structured_llm = llm.with_structured_output(schema, method="json_schema")
        except Exception:
            structured_llm = llm.with_structured_output(schema, method="json_mode")
        structured = structured_llm.invoke(prompt)
        return _validate_payload(structured, schema)

    response = llm.invoke(prompt)
    content = _response_to_text(getattr(response, "content", response))
    payload = _extract_first_json_object(content)
    if not payload:
        raise ValueError(f"LLM response did not include JSON: {content[:200]}")
    return _validate_payload(payload, schema)


def _validate_payload(payload: Any, schema: type[BaseModel]) -> Dict[str, Any]:
    if isinstance(payload, BaseModel):
        return payload.model_dump()
    if not isinstance(payload, dict):
        raise TypeError(f"Expected dict payload, got {type(payload).__name__}")
    return schema.model_validate(payload).model_dump()


def _get_score(payload: Dict[str, Any], key: str, default: int = 50) -> int:
    return _bounded_int(payload.get(key, default))


def _bounded_int(value: Any, default: int = 50) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(100, parsed))


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
