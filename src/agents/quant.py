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

from typing import Dict, Optional
from dataclasses import dataclass

from src.agents.llm_config import get_gemini_llm
from src.tools.finance_tool import (
    QuantitativeAnalyzer,
    QuantitativeAnalysis,
    FinancialAnalysisTool,
)


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
    roe: Optional[float] = None
    debt_ratio: Optional[float] = None
    
    # 최종 의견
    opinion: str = ""
    grade: str = "C"  # A/B/C/D/F


class QuantAgent:
    """
    퀀트 에이전트
    - 재무 데이터 기반 정량 분석
    - Instruct 모델 (빠름)
    """
    
    def __init__(self):
        self.llm = get_gemini_llm()
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
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            
        Returns:
            QuantScore 데이터클래스
        """
        print(f"📊 [Quant] {stock_name}({stock_code}) 재무 분석 중...")
        
        try:
            # 1. 네이버 금융에서 데이터 수집 + 분석
            analysis: QuantitativeAnalysis = self.analyzer.analyze(stock_code)
            
            # 2. QuantScore로 변환
            return QuantScore(
                valuation_score=analysis.valuation_score,
                profitability_score=analysis.profitability_score,
                growth_score=analysis.growth_score,
                stability_score=analysis.stability_score,
                total_score=analysis.total_score,
                valuation_analysis=analysis.valuation_detail,
                profitability_analysis=analysis.profitability_detail,
                growth_analysis=analysis.growth_detail,
                stability_analysis=analysis.stability_detail,
                per=analysis.metrics.get("PER"),
                pbr=analysis.metrics.get("PBR"),
                roe=analysis.metrics.get("ROE"),
                debt_ratio=analysis.metrics.get("부채비율"),
                opinion=analysis.summary,
                grade=self._calculate_grade(analysis.total_score)
            )
            
        except Exception as e:
            print(f"❌ 재무 분석 오류: {e}")
            return self._default_score(stock_name, str(e))
    
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
            grade="C"
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
| ROE | {score.roe if score.roe else 'N/A'}% |
| 부채비율 | {score.debt_ratio if score.debt_ratio else 'N/A'}% |

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
    
    def quick_check(self, stock_code: str) -> Dict:
        """
        빠른 지표 확인 (점수 없이)
        
        Args:
            stock_code: 종목코드
            
        Returns:
            주요 지표 딕셔너리
        """
        try:
            analysis = self.analyzer.analyze(stock_code)
            return {
                "stock_code": stock_code,
                "total_score": analysis.total_score,
                "grade": self._calculate_grade(analysis.total_score),
                "metrics": analysis.metrics,
                "summary": analysis.summary
            }
        except Exception as e:
            return {
                "stock_code": stock_code,
                "error": str(e)
            }


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