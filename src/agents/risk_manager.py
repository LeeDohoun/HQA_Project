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
from typing import Any, Dict, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from src.agents.llm_config import get_llm_info, get_thinking_llm, get_thinking_validator_llm
from src.agents.context import AgentContextPacket
from src.utils.prompt_loader import load_prompt_optional

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
    # Analyst (Strategist) - 헤게모니 분석
    analyst_moat_score: int = 0  # 독점력 (0-40)
    analyst_growth_score: int = 0  # 성장성 (0-30)
    analyst_total: int = 0  # 총점 (0-70)
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

    # 구조화된 중간 컨텍스트
    analyst_context: Dict[str, Any] = field(default_factory=dict)
    quant_context: Dict[str, Any] = field(default_factory=dict)
    chartist_context: Dict[str, Any] = field(default_factory=dict)


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

    # 교차 검증 메타데이터
    validation_status: str = "disabled"  # disabled | passed | warning | unavailable
    validation_summary: str = ""
    validator_model: str = ""
    primary_model: str = ""
    validator_action: str = ""
    validator_confidence: int = 0
    
    # 메타데이터
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class FinalDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_score: float | int = 50
    action: str = "HOLD"
    confidence: float | int = 50
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
        self.llm = get_thinking_llm()
        self.validator_llm = get_thinking_validator_llm()
        llm_info = get_llm_info()
        self.primary_model_name = llm_info.get("thinking_model", "")
        self.validator_model_name = llm_info.get("thinking_validator_model", "")
    
    def make_decision(
        self,
        stock_name: str,
        stock_code: str,
        scores: AgentScores
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
        prompt = self._build_decision_prompt(stock_name, stock_code, scores)
        
        try:
            primary_decision = self._invoke_decision_llm(
                self.llm,
                stock_name,
                stock_code,
                prompt,
            )

            if not self.validator_llm:
                primary_decision.validation_status = "disabled"
                primary_decision.validation_summary = "보조 최종판단 모델이 설정되지 않아 단일 모델로 결정했습니다."
                primary_decision.primary_model = self.primary_model_name
                return primary_decision

            try:
                validator_decision = self._invoke_decision_llm(
                    self.validator_llm,
                    stock_name,
                    stock_code,
                    prompt,
                )
            except Exception as validator_error:
                primary_decision.validation_status = "unavailable"
                primary_decision.validation_summary = (
                    f"보조 모델 검증 실패: {str(validator_error)[:160]}"
                )
                primary_decision.primary_model = self.primary_model_name
                primary_decision.validator_model = self.validator_model_name
                return primary_decision

            return self._reconcile_decisions(primary_decision, validator_decision)

        except Exception as e:
            print(f"❌ 판단 오류: {e}")
            return self._default_decision(stock_name, stock_code, scores)

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
        scores: AgentScores
    ) -> str:
        """결정 프롬프트 구성"""
        analyst_context = self._format_context_packet(scores.analyst_context, "analyst")
        quant_context = self._format_context_packet(scores.quant_context, "quant")
        chartist_context = self._format_context_packet(scores.chartist_context, "chartist")

        return load_prompt_optional(
            "risk_manager",
            "decision",
            fallback=self._decision_fallback_prompt(),
            stock_name=stock_name,
            stock_code=stock_code,
            analyst_total=scores.analyst_total,
            analyst_grade=scores.analyst_grade,
            quant_total=scores.quant_total,
            chartist_total=scores.chartist_total,
            analyst_context=analyst_context,
            quant_context=quant_context,
            chartist_context=chartist_context,
        )
    
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

    def _reconcile_decisions(
        self,
        primary: FinalDecision,
        validator: FinalDecision,
    ) -> FinalDecision:
        """주 모델과 검증 모델의 최종 판단을 병합"""
        primary_rank = self._action_rank(primary.action)
        validator_rank = self._action_rank(validator.action)
        disagreement = abs(primary_rank - validator_rank)

        merged = FinalDecision(
            stock_name=primary.stock_name,
            stock_code=primary.stock_code,
            total_score=round((primary.total_score + validator.total_score) / 2),
            action=primary.action,
            confidence=round((primary.confidence + validator.confidence) / 2),
            risk_level=self._max_risk_level(primary.risk_level, validator.risk_level),
            risk_factors=self._merge_unique(primary.risk_factors, validator.risk_factors, limit=5),
            position_size=self._more_conservative_position(primary.position_size, validator.position_size),
            entry_strategy=primary.entry_strategy,
            exit_strategy=primary.exit_strategy,
            stop_loss=primary.stop_loss or validator.stop_loss,
            signal_alignment=primary.signal_alignment,
            key_catalysts=self._merge_unique(primary.key_catalysts, validator.key_catalysts, limit=5),
            contrarian_view=self._merge_text(primary.contrarian_view, validator.contrarian_view),
            summary=primary.summary,
            detailed_reasoning=primary.detailed_reasoning,
            validation_status="passed",
            validation_summary="주 모델과 보조 모델의 최종 판단이 대체로 일치했습니다.",
            validator_model=self.validator_model_name,
            primary_model=self.primary_model_name,
            validator_action=validator.action.value,
            validator_confidence=validator.confidence,
        )

        if disagreement == 0:
            return merged

        if disagreement == 1:
            merged.validation_status = "warning"
            merged.confidence = max(20, merged.confidence - 10)
            merged.summary = (
                f"{primary.summary} [검증 보류: 보조 모델은 {validator.action.value} 의견]"
            ).strip()
            merged.validation_summary = (
                f"주 모델은 {primary.action.value}, 보조 모델은 {validator.action.value}로 "
                "인접 단계에서 엇갈렸습니다. 주 판단을 유지하되 확신도를 낮췄습니다."
            )
            return merged

        conservative = validator if validator_rank < primary_rank else primary
        merged.action = conservative.action
        merged.total_score = min(primary.total_score, validator.total_score)
        merged.confidence = max(15, min(primary.confidence, validator.confidence) - 15)
        merged.position_size = self._more_conservative_position(
            primary.position_size,
            validator.position_size,
        )
        merged.entry_strategy = conservative.entry_strategy or merged.entry_strategy
        merged.exit_strategy = conservative.exit_strategy or merged.exit_strategy
        merged.stop_loss = conservative.stop_loss or merged.stop_loss
        merged.summary = (
            f"{primary.summary} [교차 검증 충돌: 보수적 결론 {conservative.action.value} 채택]"
        ).strip()
        merged.contrarian_view = self._merge_text(
            primary.contrarian_view,
            validator.contrarian_view,
        )
        merged.validation_status = "warning"
        merged.validation_summary = (
            f"주 모델은 {primary.action.value}, 보조 모델은 {validator.action.value}로 "
            "의견 차이가 커서 더 보수적인 결론을 채택했습니다."
        )
        return merged

    def _action_rank(self, action: InvestmentAction) -> int:
        ranks = {
            InvestmentAction.STRONG_SELL: 0,
            InvestmentAction.SELL: 1,
            InvestmentAction.REDUCE: 2,
            InvestmentAction.HOLD: 3,
            InvestmentAction.BUY: 4,
            InvestmentAction.STRONG_BUY: 5,
        }
        return ranks[action]

    def _risk_rank(self, risk_level: RiskLevel) -> int:
        ranks = {
            RiskLevel.VERY_LOW: 0,
            RiskLevel.LOW: 1,
            RiskLevel.MEDIUM: 2,
            RiskLevel.HIGH: 3,
            RiskLevel.VERY_HIGH: 4,
        }
        return ranks[risk_level]

    def _max_risk_level(self, left: RiskLevel, right: RiskLevel) -> RiskLevel:
        return left if self._risk_rank(left) >= self._risk_rank(right) else right

    def _more_conservative_position(self, left: str, right: str) -> str:
        def _parse(value: str) -> int:
            try:
                return int(value.replace("%", "").strip())
            except Exception:
                return 25

        return f"{min(_parse(left), _parse(right))}%"

    def _merge_unique(self, left: List[str], right: List[str], limit: int = 5) -> List[str]:
        merged: List[str] = []
        for item in left + right:
            normalized = item.strip()
            if normalized and normalized not in merged:
                merged.append(normalized)
            if len(merged) >= limit:
                break
        return merged

    def _merge_text(self, left: str, right: str) -> str:
        texts = [text.strip() for text in [left, right] if text and text.strip()]
        if not texts:
            return ""
        if len(texts) == 1 or texts[0] == texts[1]:
            return texts[0]
        return f"{texts[0]}\n\n[검증 보조 의견] {texts[1]}"

    def _format_context_packet(self, packet: Dict[str, Any], fallback_name: str) -> str:
        if not packet:
            return f"- {fallback_name}: 없음"

        try:
            ctx = AgentContextPacket(**packet)
            return ctx.to_prompt_block()
        except Exception:
            return f"- {fallback_name}: {packet}"

    def _decision_fallback_prompt(self) -> str:
        """기본 결정 프롬프트"""
        return """
당신은 20년 경력의 헤지펀드 포트폴리오 매니저입니다.
아래의 구조화된 컨텍스트와 점수를 함께 검토하여, 종목 '{stock_name}'({stock_code})에 대한 최종 투자 결정을 내려주세요.

---

## 1. 요약 점수
- Analyst 총점: {analyst_total} / 70점
- Analyst 등급: {analyst_grade}
- Quant 총점: {quant_total} / 100점
- Chartist 총점: {chartist_total} / 100점

## 2. 구조화된 중간 컨텍스트

### Analyst Packet
{analyst_context}

### Quant Packet
{quant_context}

### Chartist Packet
{chartist_context}

---

다음 JSON 형식으로만 응답하세요.
{
  "total_score": 0,
  "action": "STRONG_BUY",
  "confidence": 0,
  "risk_level": "MEDIUM",
  "risk_factors": ["", "", ""],
  "position_size": "25%",
  "entry_strategy": "",
  "exit_strategy": "",
  "stop_loss": "",
  "signal_alignment": "",
  "key_catalysts": ["", ""],
  "contrarian_view": "",
  "summary": "",
  "detailed_reasoning": ""
}

JSON만 출력하세요.
"""
    
    def _default_decision(
        self,
        stock_name: str,
        stock_code: str,
        scores: AgentScores
    ) -> FinalDecision:
        """오류 시 기본 결정 반환"""
        # 단순 평균으로 기본 점수 계산
        analyst_normalized = (scores.analyst_total / 70) * 100
        quant_normalized = scores.quant_total
        chartist_normalized = scores.chartist_total
        
        avg_score = int((analyst_normalized + quant_normalized + chartist_normalized) / 3)
        
        return FinalDecision(
            stock_name=stock_name,
            stock_code=stock_code,
            total_score=avg_score,
            action=InvestmentAction.HOLD,
            confidence=30,
            risk_level=RiskLevel.MEDIUM,
            risk_factors=["분석 오류로 보수적 판단"],
            position_size="25%",
            entry_strategy="분할 매수 권장",
            exit_strategy="목표가 도달 시 분할 매도",
            stop_loss="-10% 손절",
            signal_alignment="분석 오류로 판단 불가",
            key_catalysts=["추가 분석 필요"],
            contrarian_view="데이터 부족으로 보수적 접근 권장",
            summary="분석 오류 - 관망 권고",
            detailed_reasoning="분석 과정에서 오류가 발생하여 보수적으로 관망 의견을 제시합니다.",
            validation_status="unavailable",
            validation_summary="주 모델 판단 자체가 실패하여 교차 검증을 수행하지 못했습니다.",
            validator_model=self.validator_model_name,
            primary_model=self.primary_model_name,
        )
    
    def generate_report(self, decision: FinalDecision) -> str:
        """
        최종 결정을 보고서 형식으로 출력
        
        Args:
            decision: FinalDecision
            
        Returns:
            마크다운 형식 보고서
        """
        # Action 이모지 매핑
        action_emoji = {
            InvestmentAction.STRONG_BUY: "🚀",
            InvestmentAction.BUY: "📈",
            InvestmentAction.HOLD: "⏸️",
            InvestmentAction.REDUCE: "📉",
            InvestmentAction.SELL: "🔻",
            InvestmentAction.STRONG_SELL: "⛔",
        }
        
        # Risk 색상
        risk_emoji = {
            RiskLevel.VERY_LOW: "🟢",
            RiskLevel.LOW: "🟢",
            RiskLevel.MEDIUM: "🟡",
            RiskLevel.HIGH: "🟠",
            RiskLevel.VERY_HIGH: "🔴",
        }
        
        risk_factors_str = "\n".join([f"   - {r}" for r in decision.risk_factors]) if decision.risk_factors else "   - 없음"
        catalysts_str = "\n".join([f"   - {c}" for c in decision.key_catalysts]) if decision.key_catalysts else "   - 없음"
        
        return f"""
# {decision.stock_name} ({decision.stock_code}) 최종 투자 판단

## {action_emoji.get(decision.action, "📊")} 투자 의견: {decision.action.value}

| 항목 | 값 |
|------|-----|
| **종합 점수** | {decision.total_score} / 100 |
| **확신도** | {decision.confidence}% |
| **리스크 수준** | {risk_emoji.get(decision.risk_level, "🟡")} {decision.risk_level.value} |
| **권장 비중** | {decision.position_size} |
| **교차 검증** | {decision.validation_status} |

---

## 💡 핵심 요약
> {decision.summary}

---

## 📊 신호 분석
{decision.signal_alignment}

## 🎯 핵심 촉매
{catalysts_str}

## ⚠️ 리스크 요인
{risk_factors_str}

## 🔄 반대 의견
{decision.contrarian_view}

## 🧪 교차 검증
- 주 모델: {decision.primary_model or "미설정"}
- 보조 모델: {decision.validator_model or "미설정"}
- 보조 모델 의견: {decision.validator_action or "없음"} / 확신도 {decision.validator_confidence}%
- 검증 요약: {decision.validation_summary or "없음"}

---

## 📈 매매 전략

### 진입 전략
{decision.entry_strategy}

### 청산 전략
{decision.exit_strategy}

### 손절 기준
{decision.stop_loss}

---

## 📝 상세 추론 과정
{decision.detailed_reasoning}

---
*분석 시점: {decision.timestamp}*
"""
    
    def quick_decision(
        self,
        analyst_total: int,
        quant_total: int,
        chartist_total: int
    ) -> str:
        """
        빠른 판단 (점수만으로)
        
        Args:
            analyst_total: Analyst 총점 (0-70)
            quant_total: Quant 총점 (0-100)
            chartist_total: Chartist 총점 (0-100)
            
        Returns:
            간단한 투자 의견
        """
        # 정규화
        analyst_norm = (analyst_total / 70) * 100
        
        # 가중 평균 (Analyst 40%, Quant 35%, Chartist 25%)
        weighted_score = (
            analyst_norm * 0.40 +
            quant_total * 0.35 +
            chartist_total * 0.25
        )
        
        if weighted_score >= 80:
            return f"📈 적극 매수 (점수: {weighted_score:.0f})"
        elif weighted_score >= 65:
            return f"📈 매수 (점수: {weighted_score:.0f})"
        elif weighted_score >= 45:
            return f"⏸️ 관망 (점수: {weighted_score:.0f})"
        elif weighted_score >= 30:
            return f"📉 비중 축소 (점수: {weighted_score:.0f})"
        else:
            return f"🔻 매도 (점수: {weighted_score:.0f})"


# 사용 예시
if __name__ == "__main__":
    manager = RiskManagerAgent()
    
    # 테스트 점수
    scores = AgentScores(
        # Analyst
        analyst_moat_score=32,
        analyst_growth_score=24,
        analyst_total=56,
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
    
    # 보고서 출력
    report = manager.generate_report(decision)
    print(report)
