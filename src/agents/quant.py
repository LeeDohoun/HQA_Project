# 파일: src/agents/quant.py
"""
Quant Agent (퀀트 에이전트)

역할: 재무 데이터 기반 정량 분석
- 밸류에이션 분석 (PER, PBR, EPS)
- 수익성 분석 (ROE, ROA, 마진)
- 성장성 분석 (매출/이익 성장률)
- 안정성 분석 (부채비율, 유동비율)

모델: Instruct (빠름)
점수 체계: 100점 만점 (밸류 25 + 수익성 25 + 성장성 25 + 안정성 25)
"""

import json
import re
from typing import Any, Dict, Optional
from dataclasses import dataclass, field

from src.agents.llm_config import get_quant_llm
from src.tools.finance_tool import (
    QuantitativeAnalyzer,
    QuantitativeAnalysis,
)
from src.utils.prompt_loader import load_prompt


@dataclass
class QuantScore:
    """퀀트 분석 점수"""
    # 점수 (각 25점, 총 100점)
    valuation_score: int  # 밸류에이션 (0-25)
    profitability_score: int  # 수익성 (0-25)
    growth_score: int  # 성장성 (0-25)
    stability_score: int  # 안정성 (0-25)
    total_score: int  # 총점 (0-100)
    
    # 세부 분석
    valuation_analysis: str
    profitability_analysis: str
    growth_analysis: str
    stability_analysis: str
    
    # 핵심 지표
    per: Optional[float] = None
    pbr: Optional[float] = None
    eps: Optional[float] = None
    bps: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    debt_ratio: Optional[float] = None
    current_ratio: Optional[float] = None
    revenue: Optional[float] = None
    operating_profit: Optional[float] = None
    net_income: Optional[float] = None
    
    # 최종 의견
    opinion: str = ""
    grade: str = "C"  # A/B/C/D/F
    quality_flags: Dict[str, Any] = field(default_factory=dict)


class QuantAgent:
    """
    퀀트 에이전트
    - 재무 데이터 기반 정량 분석
    - Instruct 모델 (빠름)
    """
    
    def __init__(self):
        self.llm = get_quant_llm()
        self.analyzer = QuantitativeAnalyzer()
    
    def analyze_fundamentals(self, stock_name: str, stock_code: str) -> str:
        """
        재무 분석 수행 (CrewAI 방식, 하위 호환성)
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            
        Returns:
            분석 보고서 문자열
        """
        score = self.full_analysis(stock_name, stock_code)
        return self.generate_report(score, stock_name)
    
    def full_analysis(self, stock_name: str, stock_code: str) -> QuantScore:
        """
        전체 재무 분석 수행
        DART/KRX 기반 지표 계산 → LLM 해석
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            
        Returns:
            QuantScore 데이터클래스
        """
        print(f"📊 [Quant] {stock_name}({stock_code}) 재무 분석 중...")
        
        try:
            analysis: QuantitativeAnalysis = self.analyzer.analyze(stock_code)
            metrics = self._metrics_from_analysis(analysis)
            score = self._interpret_with_llm(stock_name, stock_code, analysis, metrics)
            print(f"   ✅ DART/KRX 기반 LLM 해석 완료: {score.total_score}/100점 (등급 {score.grade})")
            return score
            
        except Exception as e:
            print(f"   ❌ DART/KRX 기반 퀀트 분석 실패: {e}")
            return self._default_score(stock_name, f"DART/KRX 기반 퀀트 분석 실패: {e}")
    
    def _calculate_grade(self, total_score: int) -> str:
        """점수에 따른 등급 계산"""
        if total_score >= 80:
            return "A"
        elif total_score >= 65:
            return "B"
        elif total_score >= 50:
            return "C"
        elif total_score >= 35:
            return "D"
        else:
            return "F"
    
    def _interpret_with_llm(
        self,
        stock_name: str,
        stock_code: str,
        analysis: QuantitativeAnalysis,
        metrics: Dict[str, Optional[float]],
    ) -> QuantScore:
        missing = analysis.missing_core_metrics()
        quality_warnings = []
        if missing:
            quality_warnings.append("핵심 재무 지표 누락: " + ", ".join(missing))
        if not analysis.has_sufficient_financial_data():
            quality_warnings.append("정량 점수 신뢰도 제한: 밸류에이션 또는 수익성/안정성 지표 부족")

        prompt = load_prompt(
            "quant",
            "quant",
            stock_name=stock_name,
            stock_code=stock_code,
            quant_metrics=json.dumps(metrics, ensure_ascii=False, indent=2),
            financial_source=analysis.financial_source,
            python_scores=json.dumps(
                {
                    "valuation_score": analysis.valuation_score,
                    "profitability_score": analysis.profitability_score,
                    "growth_score": analysis.growth_score,
                    "stability_score": analysis.stability_score,
                    "total_score": analysis.total_score,
                },
                ensure_ascii=False,
                indent=2,
            ),
            quality_warnings="\n".join(f"- {item}" for item in quality_warnings) or "- 특이 경고 없음",
        )
        response = self.llm.invoke(prompt)
        data = self._extract_json(response)
        return self._score_from_payload(stock_name, stock_code, analysis, metrics, data, quality_warnings)

    def _extract_json(self, response: Any) -> Dict[str, Any]:
        content = getattr(response, "content", response)
        if not isinstance(content, str):
            content = str(content)
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            raise ValueError("LLM이 JSON을 반환하지 않음")
        payload = json.loads(json_match.group())
        if not isinstance(payload, dict):
            raise ValueError("LLM JSON 응답이 객체가 아님")
        return payload

    def _score_from_payload(
        self,
        stock_name: str,
        stock_code: str,
        analysis: QuantitativeAnalysis,
        metrics: Dict[str, Optional[float]],
        data: Dict[str, Any],
        quality_warnings: list[str],
    ) -> QuantScore:
        v = self._bounded_score(data.get("valuation_score"), analysis.valuation_score)
        p = self._bounded_score(data.get("profitability_score"), analysis.profitability_score)
        g = self._bounded_score(data.get("growth_score"), analysis.growth_score)
        s = self._bounded_score(data.get("stability_score"), analysis.stability_score)
        total = v + p + g + s

        score = QuantScore(
            valuation_score=v,
            profitability_score=p,
            growth_score=g,
            stability_score=s,
            total_score=total,
            valuation_analysis=str(data.get("valuation_analysis") or self._valuation_detail(analysis)),
            profitability_analysis=str(data.get("profitability_analysis") or self._profitability_detail(analysis)),
            growth_analysis=str(data.get("growth_analysis") or self._growth_detail(analysis)),
            stability_analysis=str(data.get("stability_analysis") or self._stability_detail(analysis)),
            per=metrics.get("per"),
            pbr=metrics.get("pbr"),
            eps=metrics.get("eps"),
            bps=metrics.get("bps"),
            roe=metrics.get("roe"),
            roa=metrics.get("roa"),
            operating_margin=metrics.get("operating_margin"),
            net_margin=metrics.get("net_margin"),
            debt_ratio=metrics.get("debt_ratio"),
            current_ratio=metrics.get("current_ratio"),
            revenue=metrics.get("revenue"),
            operating_profit=metrics.get("operating_profit"),
            net_income=metrics.get("net_income"),
            opinion=str(data.get("opinion") or self._analysis_opinion(analysis)),
            grade=self._calculate_grade(total),
            quality_flags={
                "data_quality": "sufficient" if analysis.has_sufficient_financial_data() else "limited",
                "source": analysis.financial_source,
                "missing_core_metrics": analysis.missing_core_metrics(),
                "quality_warnings": quality_warnings,
            },
        )
        return score

    @staticmethod
    def _bounded_score(value: Any, fallback: int) -> int:
        try:
            score = int(value)
        except (TypeError, ValueError):
            score = int(fallback)
        return min(25, max(0, score))
    
    def _default_score(self, stock_name: str, error: str) -> QuantScore:
        """오류 시 기본 점수 반환"""
        return QuantScore(
            valuation_score=12,
            profitability_score=12,
            growth_score=12,
            stability_score=12,
            total_score=48,
            valuation_analysis=f"데이터 수집 오류: {error}",
            profitability_analysis="분석 불가",
            growth_analysis="분석 불가",
            stability_analysis="분석 불가",
            opinion="데이터 부족으로 중립 의견",
            grade="C",
            quality_flags={
                "data_quality": "insufficient",
                "source": "default_score",
                "fallback_used": True,
                "reason": error,
            },
        )
    
    def generate_report(self, score: QuantScore, stock_name: str) -> str:
        """
        분석 결과를 보고서 형식으로 출력
        
        Args:
            score: QuantScore
            stock_name: 종목명
            
        Returns:
            마크다운 형식 보고서
        """
        # 등급 이모지
        grade_emoji = {
            "A": "🟢", "B": "🔵", "C": "🟡", "D": "🟠", "F": "🔴"
        }
        
        return f"""
# {stock_name} 퀀트 분석 보고서

## 📊 점수 요약
| 항목 | 점수 | 비중 |
|------|------|------|
| 밸류에이션 | **{score.valuation_score}** / 25 | 25% |
| 수익성 | **{score.profitability_score}** / 25 | 25% |
| 성장성 | **{score.growth_score}** / 25 | 25% |
| 안정성 | **{score.stability_score}** / 25 | 25% |
| **총점** | **{score.total_score}** / 100 | 100% |

## {grade_emoji.get(score.grade, "⚪")} 등급: {score.grade}

---

## 1. 주요 재무 지표
| 지표 | 값 |
|------|-----|
| PER | {score.per if score.per else 'N/A'} |
| PBR | {score.pbr if score.pbr else 'N/A'} |
| EPS | {score.eps if score.eps else 'N/A'} |
| BPS | {score.bps if score.bps else 'N/A'} |
| ROE | {score.roe if score.roe else 'N/A'}% |
| ROA | {score.roa if score.roa else 'N/A'}% |
| 영업이익률 | {score.operating_margin if score.operating_margin else 'N/A'}% |
| 순이익률 | {score.net_margin if score.net_margin else 'N/A'}% |
| 부채비율 | {score.debt_ratio if score.debt_ratio else 'N/A'}% |
| 유동비율 | {score.current_ratio if score.current_ratio else 'N/A'}% |

## 2. 밸류에이션 분석 ({score.valuation_score}/25점)
{score.valuation_analysis}

## 3. 수익성 분석 ({score.profitability_score}/25점)
{score.profitability_analysis}

## 4. 성장성 분석 ({score.growth_score}/25점)
{score.growth_analysis}

## 5. 안정성 분석 ({score.stability_score}/25점)
{score.stability_analysis}

---

## 💡 퀀트 총평
> {score.opinion}
"""

    def _metrics_from_analysis(self, analysis: QuantitativeAnalysis) -> Dict[str, Optional[float]]:
        return {
            "per": analysis.per,
            "pbr": analysis.pbr,
            "eps": analysis.eps,
            "bps": analysis.bps,
            "roe": analysis.roe,
            "roa": analysis.roa,
            "operating_margin": analysis.operating_margin,
            "net_margin": analysis.net_margin,
            "debt_ratio": analysis.debt_ratio,
            "current_ratio": analysis.current_ratio,
            "current_assets": analysis.current_assets,
            "current_liabilities": analysis.current_liabilities,
            "revenue": analysis.revenue,
            "operating_profit": analysis.operating_profit,
            "net_income": analysis.net_income,
            "dividend_yield": analysis.dividend_yield,
        }

    def _analysis_opinion(self, analysis: QuantitativeAnalysis) -> str:
        return (
            f"{analysis.stock_name}의 정량 점수는 {analysis.total_score}/100점으로 "
            f"투자 의견은 '{analysis.get_opinion()}'입니다."
        )

    def _valuation_detail(self, analysis: QuantitativeAnalysis) -> str:
        return (
            f"PER {self._fmt_metric(analysis.per)}배 {analysis._per_comment()} / "
            f"PBR {self._fmt_metric(analysis.pbr)}배 {analysis._pbr_comment()}를 기준으로 "
            f"밸류에이션 점수는 {analysis.valuation_score}/25점입니다."
        )

    def _profitability_detail(self, analysis: QuantitativeAnalysis) -> str:
        return (
            f"ROE {self._fmt_metric(analysis.roe)}% {analysis._roe_comment()}, "
            f"ROA {self._fmt_metric(analysis.roa)}%, "
            f"영업이익률 {self._fmt_metric(analysis.operating_margin)}%, "
            f"순이익률 {self._fmt_metric(analysis.net_margin)}%를 반영해 "
            f"수익성 점수는 {analysis.profitability_score}/25점입니다."
        )

    def _growth_detail(self, analysis: QuantitativeAnalysis) -> str:
        return (
            f"성장성은 ROE 기반 재투자 수익률과 배당수익률 "
            f"{self._fmt_metric(analysis.dividend_yield)}%를 기준으로 추정했으며 "
            f"점수는 {analysis.growth_score}/25점입니다."
        )

    def _stability_detail(self, analysis: QuantitativeAnalysis) -> str:
        return (
            f"부채비율 {self._fmt_metric(analysis.debt_ratio)}% {analysis._debt_comment()}와 "
            f"유동비율 {self._fmt_metric(analysis.current_ratio)}%, PBR/배당 정보를 반영한 재무 안정성 점수는 "
            f"{analysis.stability_score}/25점입니다."
        )

    def _fmt_metric(self, value: Optional[float]) -> str:
        if value is None:
            return "N/A"
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        return f"{value:.2f}"


# 사용 예시
if __name__ == "__main__":
    agent = QuantAgent()
    
    print("=" * 60)
    print("삼성전자 퀀트 분석")
    print("=" * 60)
    
    # 전체 분석
    score = agent.full_analysis("삼성전자", "005930")
    
    # 보고서 출력
    report = agent.generate_report(score, "삼성전자")
    print(report)
    
    print(f"\n📊 점수 요약:")
    print(f"   밸류에이션: {score.valuation_score}/25")
    print(f"   수익성: {score.profitability_score}/25")
    print(f"   성장성: {score.growth_score}/25")
    print(f"   안정성: {score.stability_score}/25")
    print(f"   총점: {score.total_score}/100 (등급: {score.grade})")
