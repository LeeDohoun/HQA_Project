# 파일: src/agents/risk_manager.py
"""
Risk Manager Agent (리스크 매니저 에이전트)

역할: 최종 투자 판단 및 리스크 관리
- Analyst, Quant, Chartist 3개 에이전트 결과 종합
- 상충되는 신호 조율
- 포지션 사이징 권고
- 최종 투자 의견 도출

모델: Thinking (깊은 추론)
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from src.agents.llm_config import get_risk_manager_llm
from src.utils.portfolio_context import prompt_block_for_portfolio_context
from src.utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class InvestmentAction(Enum):
    """투자 행동"""
    STRONG_BUY = "적극 매수"
    BUY = "매수"
    HOLD = "보유/관망"
    REDUCE = "비중 축소"
    SELL = "매도"
    STRONG_SELL = "적극 매도"


class RiskLevel(Enum):
    """리스크 수준"""
    VERY_LOW = "매우 낮음"
    LOW = "낮음"
    MEDIUM = "보통"
    HIGH = "높음"
    VERY_HIGH = "매우 높음"


@dataclass
class AgentScores:
    """에이전트별 점수 입력"""
    # 원본 에이전트 결과
    analyst_result: Any = None
    quant_result: Any = None
    chartist_result: Any = None

    # Analyst (Strategist) - 헤게모니 분석
    analyst_moat_score: int = 0  # 독점력 (0-50)
    analyst_growth_score: int = 0  # 성장성 (0-50)
    analyst_total: int = 0  # 총점 (0-100)
    analyst_grade: str = "C"  # A/B/C/D/F
    analyst_opinion: str = ""
    
    # Quant - 재무 분석
    quant_valuation_score: int = 0  # 밸류에이션 (0-25)
    quant_profitability_score: int = 0  # 수익성 (0-25)
    quant_growth_score: int = 0  # 성장성 (0-25)
    quant_stability_score: int = 0  # 안정성 (0-25)
    quant_total: int = 0  # 총점 (0-100)
    quant_opinion: str = ""
    
    # Chartist - 기술적 분석
    chartist_trend_score: int = 0  # 추세 (0-30)
    chartist_momentum_score: int = 0  # 모멘텀 (0-30)
    chartist_volatility_score: int = 0  # 변동성 (0-20)
    chartist_volume_score: int = 0  # 거래량 (0-20)
    chartist_total: int = 0  # 총점 (0-100)
    chartist_signal: str = ""  # 매수/중립/매도

    def __post_init__(self) -> None:
        """원본 결과 객체가 있으면 기존 점수 필드를 자동 채운다."""
        if self.analyst_result is not None:
            self.analyst_moat_score = self._get_int(self.analyst_result, "moat_score")
            self.analyst_growth_score = self._get_int(self.analyst_result, "growth_score")
            self.analyst_total = self._get_int(self.analyst_result, "total_score")
            self.analyst_grade = str(self._get(self.analyst_result, "hegemony_grade", self.analyst_grade) or "")
            self.analyst_opinion = str(self._get(self.analyst_result, "final_opinion", self.analyst_opinion) or "")

        if self.quant_result is not None:
            self.quant_valuation_score = self._get_int(self.quant_result, "valuation_score")
            self.quant_profitability_score = self._get_int(self.quant_result, "profitability_score")
            self.quant_growth_score = self._get_int(self.quant_result, "growth_score")
            self.quant_stability_score = self._get_int(self.quant_result, "stability_score")
            self.quant_total = self._get_int(self.quant_result, "total_score")
            self.quant_opinion = str(self._get(self.quant_result, "opinion", self.quant_opinion) or "")

        if self.chartist_result is not None:
            self.chartist_trend_score = self._get_int(self.chartist_result, "trend_score")
            self.chartist_momentum_score = self._get_int(self.chartist_result, "momentum_score")
            self.chartist_volatility_score = self._get_int(self.chartist_result, "volatility_score")
            self.chartist_volume_score = self._get_int(self.chartist_result, "volume_score")
            self.chartist_total = self._get_int(self.chartist_result, "total_score")
            self.chartist_signal = str(self._get(self.chartist_result, "signal", self.chartist_signal) or "")

    @staticmethod
    def _get(obj: Any, name: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    @classmethod
    def _get_int(cls, obj: Any, name: str) -> int:
        try:
            return int(cls._get(obj, name, 0) or 0)
        except (TypeError, ValueError):
            return 0


@dataclass
class FinalDecision:
    """최종 투자 결정"""
    stock_name: str
    stock_code: str
    
    # 종합 점수 (100점 만점)
    total_score: int
    
    # 투자 의견
    action: InvestmentAction
    confidence: int  # 확신도 (0-100%)
    
    # 리스크 평가
    risk_level: RiskLevel
    risk_factors: List[str]
    
    # 포지션 가이드
    position_size: str  # "0%", "25%", "50%", "75%", "100%"
    entry_strategy: str  # 진입 전략
    exit_strategy: str  # 청산 전략
    stop_loss: str  # 손절 기준
    
    # 상세 분석
    signal_alignment: str  # 신호 일치도 분석
    key_catalysts: List[str]  # 핵심 촉매
    contrarian_view: str  # 반대 의견/리스크
    
    # 최종 의견
    summary: str  # 한 줄 요약
    detailed_reasoning: str  # 상세 추론 과정

    # 메타데이터
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class FinalDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_score: int = 50
    action: str = "HOLD"
    confidence: int = 50
    risk_level: str = "MEDIUM"
    risk_factors: List[str] = Field(default_factory=list)
    position_size: str = "25%"
    entry_strategy: str = ""
    exit_strategy: str = ""
    stop_loss: str = ""
    signal_alignment: str = ""
    key_catalysts: List[str] = Field(default_factory=list)
    contrarian_view: str = ""
    summary: str = ""
    detailed_reasoning: str = ""


class RiskManagerAgent:
    """
    리스크 매니저 에이전트
    - Thinking 모델로 최종 판단
    - 3개 에이전트 결과 종합
    """
    
    def __init__(self):
        self.llm = get_risk_manager_llm()
    
    def make_decision(
        self,
        stock_name: str,
        stock_code: str,
        scores: AgentScores,
        portfolio_context: Optional[Dict[str, Any]] = None,
        investor_profile: Optional[Dict[str, Any]] = None,
    ) -> FinalDecision:
        """
        최종 투자 결정 수행
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            scores: 3개 에이전트 점수
            
        Returns:
            FinalDecision 데이터클래스
        """
        print(f"🎯 [Risk Manager] {stock_name} 최종 판단 중 (Thinking 모델)...")
        
        # 프롬프트 구성
        prompt = self._build_decision_prompt(
            stock_name,
            stock_code,
            scores,
            portfolio_context=portfolio_context,
            investor_profile=investor_profile,
        )
        
        return self._invoke_decision_llm(
            self.llm,
            stock_name,
            stock_code,
            prompt,
        )

    def _invoke_decision_llm(
        self,
        llm: Any,
        stock_name: str,
        stock_code: str,
        prompt: str,
    ) -> FinalDecision:
        """단일 LLM 호출 결과를 FinalDecision으로 변환"""
        try:
            structured_llm = self._build_structured_llm(llm)
            if structured_llm is not None:
                structured = structured_llm.invoke(prompt)
                result = self._validate_payload(structured)
                return self._parse_decision(stock_name, stock_code, result)
        except Exception as exc:
            logger.warning("RiskManager structured output failed: %s", exc)

        response = llm.invoke(prompt)
        response_text = self._response_to_text(getattr(response, "content", response)).strip()
        payload = self._extract_first_json_object(response_text)
        if not payload:
            raise ValueError("JSON 형식 응답 없음")

        result = self._validate_payload(payload)
        return self._parse_decision(stock_name, stock_code, result)

    @staticmethod
    def _build_structured_llm(llm: Any):
        if not hasattr(llm, "with_structured_output"):
            return None
        try:
            return llm.with_structured_output(FinalDecisionPayload, method="json_schema")
        except Exception:
            return llm.with_structured_output(FinalDecisionPayload, method="json_mode")

    @staticmethod
    def _validate_payload(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, BaseModel):
            return payload.model_dump()
        if not isinstance(payload, dict):
            raise TypeError(f"Expected dict payload, got {type(payload).__name__}")
        return FinalDecisionPayload.model_validate(payload).model_dump()

    @staticmethod
    def _response_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        chunks.append(str(text))
                else:
                    chunks.append(str(item))
            return "\n".join(chunk for chunk in chunks if chunk)
        return str(content)

    @staticmethod
    def _extract_first_json_object(text: str) -> Dict[str, Any]:
        if not text:
            return {}

        cleaned = text.strip()
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
    
    def _build_decision_prompt(
        self,
        stock_name: str,
        stock_code: str,
        scores: AgentScores,
        portfolio_context: Optional[Dict[str, Any]] = None,
        investor_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """결정 프롬프트 구성"""
        analyst_result = self._format_analyst_result(scores)
        quant_result = self._format_quant_result(scores)
        chartist_result = self._format_chartist_result(scores)
        portfolio_context_block = prompt_block_for_portfolio_context(portfolio_context)
        investor_profile_block = self._format_investor_profile(investor_profile)

        return load_prompt(
            "risk_manager",
            "risk_manager",
            stock_name=stock_name,
            stock_code=stock_code,
            analyst_total=scores.analyst_total,
            analyst_grade=scores.analyst_grade,
            quant_total=scores.quant_total,
            chartist_total=scores.chartist_total,
            analyst_result=analyst_result,
            quant_result=quant_result,
            chartist_result=chartist_result,
            portfolio_context=portfolio_context_block,
            investor_profile=investor_profile_block,
        )

    @staticmethod
    def _format_investor_profile(investor_profile: Optional[Dict[str, Any]]) -> str:
        if not investor_profile:
            return "## 4. 투자자 프로필\n- investor profile: not provided"

        ordered_keys = [
            "total_assets",
            "monthly_investment",
            "investment_period_months",
            "target_return_rate",
            "investment_goal",
            "investment_experience",
            "investment_type",
            "volatility_tolerance",
            "loss_action",
            "leverage_allowed",
            "occupation_type",
            "loss_tolerance",
        ]
        lines = ["## 4. 투자자 프로필"]
        for key in ordered_keys:
            if key in investor_profile:
                lines.append(f"- {key}: {investor_profile.get(key)}")
        lines.extend(
            [
                "",
                "RiskManager만 사용자 적합성을 반영하세요.",
                "Analyst, Quant, Chartist의 시장 공통 평가는 바꾸지 말고, 최종 action/confidence/risk_level/position_size/stop_loss에만 투자자 성향을 반영하세요.",
                "안정형·낮은 변동성·낮은 손실허용도는 매수 기준을 강화하고 포지션을 줄이세요.",
                "공격형·높은 변동성·높은 손실허용도도 stop_loss와 risk_factors는 반드시 유지하세요.",
                "leverage_allowed가 false이면 레버리지 전제의 진입 전략을 제안하지 마세요.",
            ]
        )
        return "\n".join(lines)
    
    def _parse_decision(
        self,
        stock_name: str,
        stock_code: str,
        result: Dict
    ) -> FinalDecision:
        """JSON 결과를 FinalDecision으로 변환"""
        # Action 매핑
        action_map = {
            "STRONG_BUY": InvestmentAction.STRONG_BUY,
            "BUY": InvestmentAction.BUY,
            "HOLD": InvestmentAction.HOLD,
            "REDUCE": InvestmentAction.REDUCE,
            "SELL": InvestmentAction.SELL,
            "STRONG_SELL": InvestmentAction.STRONG_SELL,
        }
        
        # Risk Level 매핑
        risk_map = {
            "VERY_LOW": RiskLevel.VERY_LOW,
            "LOW": RiskLevel.LOW,
            "MEDIUM": RiskLevel.MEDIUM,
            "HIGH": RiskLevel.HIGH,
            "VERY_HIGH": RiskLevel.VERY_HIGH,
        }
        
        return FinalDecision(
            stock_name=stock_name,
            stock_code=stock_code,
            total_score=min(100, max(0, int(result.get("total_score", 50)))),
            action=action_map.get(result.get("action", "HOLD"), InvestmentAction.HOLD),
            confidence=min(100, max(0, int(result.get("confidence", 50)))),
            risk_level=risk_map.get(result.get("risk_level", "MEDIUM"), RiskLevel.MEDIUM),
            risk_factors=result.get("risk_factors", [])[:5],
            position_size=result.get("position_size", "25%"),
            entry_strategy=result.get("entry_strategy", ""),
            exit_strategy=result.get("exit_strategy", ""),
            stop_loss=result.get("stop_loss", ""),
            signal_alignment=result.get("signal_alignment", ""),
            key_catalysts=result.get("key_catalysts", [])[:5],
            contrarian_view=result.get("contrarian_view", ""),
            summary=result.get("summary", ""),
            detailed_reasoning=result.get("detailed_reasoning", ""),
        )

    def _format_analyst_result(self, scores: AgentScores) -> str:
        result = self._result_dict(scores.analyst_result)
        if not result:
            result = {
                "moat_score": scores.analyst_moat_score,
                "growth_score": scores.analyst_growth_score,
                "total_score": scores.analyst_total,
                "hegemony_grade": scores.analyst_grade,
                "final_opinion": scores.analyst_opinion,
            }

        return "\n".join(
            [
                "## Analyst Result",
                f"- 헤게모니 총점: {result.get('total_score', scores.analyst_total)} / 100",
                f"- 등급: {result.get('hegemony_grade', scores.analyst_grade)}",
                f"- 독점력 점수: {result.get('moat_score', scores.analyst_moat_score)} / 50",
                f"- 성장성 점수: {result.get('growth_score', scores.analyst_growth_score)} / 50",
                f"- 최종 의견: {self._fmt(result.get('final_opinion', scores.analyst_opinion))}",
                f"- 독점력 판단: {self._fmt(result.get('moat_reason'))}",
                f"- 성장성 판단: {self._fmt(result.get('growth_reason'))}",
                f"- 경쟁 우위: {self._fmt(result.get('competitive_advantage'))}",
                f"- 우려 요인: {self._fmt(result.get('risk_factors'))}",
                f"- 근거 요약: {self._fmt(result.get('evidence_summary'))}",
                f"- 상세 판단: {self._fmt(result.get('detailed_reasoning'))}",
            ]
        )

    def _format_quant_result(self, scores: AgentScores) -> str:
        result = self._result_dict(scores.quant_result)
        if not result:
            result = {
                "valuation_score": scores.quant_valuation_score,
                "profitability_score": scores.quant_profitability_score,
                "growth_score": scores.quant_growth_score,
                "stability_score": scores.quant_stability_score,
                "total_score": scores.quant_total,
                "opinion": scores.quant_opinion,
            }

        return "\n".join(
            [
                "## Quant Result",
                f"- 총점: {result.get('total_score', scores.quant_total)} / 100",
                f"- 등급: {self._fmt(result.get('grade'))}",
                f"- 최종 의견: {self._fmt(result.get('opinion', scores.quant_opinion))}",
                f"- 밸류에이션 점수: {result.get('valuation_score', scores.quant_valuation_score)} / 25",
                f"- 수익성 점수: {result.get('profitability_score', scores.quant_profitability_score)} / 25",
                f"- 성장성 점수: {result.get('growth_score', scores.quant_growth_score)} / 25",
                f"- 안정성 점수: {result.get('stability_score', scores.quant_stability_score)} / 25",
                f"- PER: {self._fmt(result.get('per'))}",
                f"- PBR: {self._fmt(result.get('pbr'))}",
                f"- EPS: {self._fmt(result.get('eps'))}",
                f"- BPS: {self._fmt(result.get('bps'))}",
                f"- ROE: {self._fmt(result.get('roe'))}",
                f"- ROA: {self._fmt(result.get('roa'))}",
                f"- 영업이익률: {self._fmt(result.get('operating_margin'))}",
                f"- 순이익률: {self._fmt(result.get('net_margin'))}",
                f"- 부채비율: {self._fmt(result.get('debt_ratio'))}",
                f"- 유동비율: {self._fmt(result.get('current_ratio'))}",
                f"- 밸류에이션 판단: {self._fmt(result.get('valuation_analysis'))}",
                f"- 수익성 판단: {self._fmt(result.get('profitability_analysis'))}",
                f"- 성장성 판단: {self._fmt(result.get('growth_analysis'))}",
                f"- 안정성 판단: {self._fmt(result.get('stability_analysis'))}",
                f"- 데이터 품질: {self._fmt(result.get('quality_flags'))}",
            ]
        )

    def _format_chartist_result(self, scores: AgentScores) -> str:
        result = self._result_dict(scores.chartist_result)
        if not result:
            result = {
                "trend_score": scores.chartist_trend_score,
                "momentum_score": scores.chartist_momentum_score,
                "volatility_score": scores.chartist_volatility_score,
                "volume_score": scores.chartist_volume_score,
                "total_score": scores.chartist_total,
                "signal": scores.chartist_signal,
            }

        return "\n".join(
            [
                "## Chartist Result",
                f"- 총점: {result.get('total_score', scores.chartist_total)} / 100",
                f"- 신호: {result.get('signal', scores.chartist_signal)}",
                f"- 추세 점수: {result.get('trend_score', scores.chartist_trend_score)} / 30",
                f"- 모멘텀 점수: {result.get('momentum_score', scores.chartist_momentum_score)} / 30",
                f"- 변동성 점수: {result.get('volatility_score', scores.chartist_volatility_score)} / 20",
                f"- 거래량 점수: {result.get('volume_score', scores.chartist_volume_score)} / 20",
                f"- 추세 판단: {self._fmt(result.get('trend_analysis'))}",
                f"- 모멘텀 판단: {self._fmt(result.get('momentum_analysis'))}",
                f"- 변동성 판단: {self._fmt(result.get('volatility_analysis'))}",
                f"- 거래량 판단: {self._fmt(result.get('volume_analysis'))}",
                f"- RSI: {self._fmt(result.get('rsi'))}",
                f"- MACD histogram: {self._fmt(result.get('macd_histogram'))}",
                f"- 거래량 비율: {self._fmt(result.get('volume_ratio'))}",
                f"- 진입 타이밍: {self._fmt(result.get('entry_timing'))}",
                f"- 과열 위험: {self._fmt(result.get('overheat_risk'))}",
                f"- 손절가: {self._fmt(result.get('stop_loss'))}",
                f"- 목표가: {self._fmt(result.get('target_price'))}",
                f"- 단기 의견: {self._fmt(result.get('short_term_opinion'))}",
                f"- 중기 의견: {self._fmt(result.get('mid_term_opinion'))}",
                f"- 현재가 스냅샷: {self._format_price_snapshot(result)}",
            ]
        )

    @staticmethod
    def _result_dict(result: Any) -> Dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, dict):
            return dict(result)
        if is_dataclass(result):
            return asdict(result)
        if hasattr(result, "__dict__"):
            return dict(vars(result))
        return {}

    @staticmethod
    def _fmt(value: Any) -> str:
        if value in (None, "", [], {}):
            return "N/A"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _format_price_snapshot(self, result: Dict[str, Any]) -> str:
        snapshot_fields = {
            "live_current_price": result.get("live_current_price"),
            "price_snapshot_source": result.get("price_snapshot_source"),
            "price_snapshot_at": result.get("price_snapshot_at"),
            "live_vs_daily_close_pct": result.get("live_vs_daily_close_pct"),
        }
        if any(value not in (None, "", 0) for value in snapshot_fields.values()):
            return self._fmt(snapshot_fields)
        return "N/A"

# 사용 예시
if __name__ == "__main__":
    manager = RiskManagerAgent()
    
    # 테스트 점수
    scores = AgentScores(
        # Analyst
        analyst_moat_score=40,
        analyst_growth_score=34,
        analyst_total=74,
        analyst_grade="B",
        analyst_opinion="반도체 업황 회복 기대, HBM 경쟁력 우위",
        
        # Quant
        quant_valuation_score=15,
        quant_profitability_score=20,
        quant_growth_score=18,
        quant_stability_score=22,
        quant_total=75,
        quant_opinion="밸류에이션 다소 부담, 수익성 양호",
        
        # Chartist
        chartist_trend_score=22,
        chartist_momentum_score=25,
        chartist_volatility_score=15,
        chartist_volume_score=16,
        chartist_total=78,
        chartist_signal="매수"
    )
    
    print("=" * 60)
    print("삼성전자 최종 투자 판단")
    print("=" * 60)
    
    # 최종 결정
    decision = manager.make_decision("삼성전자", "005930", scores)
    print(f"action={decision.action.value}")
    print(f"total_score={decision.total_score}")
    print(f"confidence={decision.confidence}")
    print(f"risk_level={decision.risk_level.value}")
    print(f"summary={decision.summary}")
