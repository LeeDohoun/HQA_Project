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

# 웹 검색 폴백 (선택적)
try:
    from src.tools.web_search_tool import search_web
    _WEB_SEARCH_AVAILABLE = True
except ImportError:
    _WEB_SEARCH_AVAILABLE = False


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
        Plan A: 네이버 금융 크롤링 → Plan B: 웹 검색 + LLM 추출
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            
        Returns:
            QuantScore 데이터클래스
        """
        print(f"📊 [Quant] {stock_name}({stock_code}) 재무 분석 중...")
        
        # ── Plan A: 네이버 금융 크롤링 ──
        try:
            analysis: QuantitativeAnalysis = self.analyzer.analyze(stock_code)
            
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
            print(f"   ⚠️ 네이버 금융 크롤링 실패: {e} → 웹 검색 폴백")
        
        # ── Plan B: 웹 검색 + LLM 추출 ──
        return self._web_search_fallback(stock_name, stock_code)
    
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
    
    def _web_search_fallback(self, stock_name: str, stock_code: str) -> QuantScore:
        """
        웹 검색 폴백: 검색 결과에서 LLM으로 재무 지표를 추출하여 분석.
        네이버 금융 크롤링이 실패했을 때 Plan B로 사용.
        """
        if not _WEB_SEARCH_AVAILABLE:
            print("   ⚠️ 웹 검색 도구 미설치 → 기본값 반환")
            return self._default_score(stock_name, "네이버 금융 + 웹 검색 모두 불가")
        
        print(f"   🔄 [Quant Plan B] {stock_name} 웹 검색으로 재무 지표 수집 중...")
        
        try:
            # 1. 여러 쿼리로 재무 지표 수집
            queries = [
                f"{stock_name} PER PBR ROE 2025",
                f"{stock_name} 부채비율 영업이익률 매출 성장률",
            ]
            
            all_snippets = []
            for q in queries:
                results = search_web(q, max_results=3)
                if results:
                    for r in results:
                        snippet = r.get("snippet") or r.get("content", "")
                        title = r.get("title", "")
                        if snippet:
                            all_snippets.append(f"[{title}] {snippet}")
            
            if not all_snippets:
                print("   ⚠️ 웹 검색 결과 없음 → 기본값 반환")
                return self._default_score(stock_name, "웹 검색 결과 없음")
            
            combined_text = "\n".join(all_snippets[:8])  # 상위 8개
            
            # 2. LLM으로 지표 추출 + 채점
            extract_prompt = f"""
다음은 '{stock_name}'({stock_code})에 대한 웹 검색 결과입니다.
여기에서 재무 지표를 추출하고 점수를 매기세요.

[검색 결과]
{combined_text[:3000]}

아래 JSON 형식으로만 응답하세요:
{{
    "per": <숫자 또는 null>,
    "pbr": <숫자 또는 null>,
    "roe": <숫자(%) 또는 null>,
    "debt_ratio": <숫자(%) 또는 null>,
    "valuation_score": <0-25>,
    "valuation_analysis": "<밸류에이션 분석 1-2문장>",
    "profitability_score": <0-25>,
    "profitability_analysis": "<수익성 분석 1-2문장>",
    "growth_score": <0-25>,
    "growth_analysis": "<성장성 분석 1-2문장>",
    "stability_score": <0-25>,
    "stability_analysis": "<안정성 분석 1-2문장>",
    "opinion": "<종합 의견 1문장>"
}}

채점 기준:
- 밸류에이션: PER 10배 이하 25점, 15배 이하 20점, 20배 이하 15점, 30배 이상 5점
- 수익성: ROE 15%+ 25점, 10%+ 20점, 5%+ 15점, 이하 10점
- 성장성: 매출 성장률 20%+ 25점, 10%+ 20점, 5%+ 15점, 이하 10점
- 안정성: 부채비율 50% 이하 25점, 100% 이하 20점, 200% 이상 10점

지표를 찾을 수 없으면 null로 두고, 해당 점수는 12점(중간값)으로 부여하세요.
JSON만 출력하세요.
"""
            response = self.llm.invoke(extract_prompt)
            
            import json
            import re
            
            json_match = re.search(r'\{[\s\S]*\}', response.content)
            if not json_match:
                raise ValueError("LLM이 JSON을 반환하지 않음")
            
            data = json.loads(json_match.group())
            
            # 점수 범위 보정
            v = min(25, max(0, int(data.get("valuation_score", 12))))
            p = min(25, max(0, int(data.get("profitability_score", 12))))
            g = min(25, max(0, int(data.get("growth_score", 12))))
            s = min(25, max(0, int(data.get("stability_score", 12))))
            total = v + p + g + s
            
            disclaimer = "\n\n[데이터 출처: 웹 검색 — 네이버 금융 원본 대비 정확도 제한적]"
            
            score = QuantScore(
                valuation_score=v,
                profitability_score=p,
                growth_score=g,
                stability_score=s,
                total_score=total,
                valuation_analysis=data.get("valuation_analysis", "웹 검색 기반") + disclaimer,
                profitability_analysis=data.get("profitability_analysis", "웹 검색 기반") + disclaimer,
                growth_analysis=data.get("growth_analysis", "웹 검색 기반") + disclaimer,
                stability_analysis=data.get("stability_analysis", "웹 검색 기반") + disclaimer,
                per=data.get("per"),
                pbr=data.get("pbr"),
                roe=data.get("roe"),
                debt_ratio=data.get("debt_ratio"),
                opinion=data.get("opinion", "웹 검색 기반 분석") + disclaimer,
                grade=self._calculate_grade(total),
            )
            
            print(f"   ✅ 웹 검색 폴백 성공: {total}/100점 (등급 {score.grade})")
            return score
            
        except Exception as e:
            print(f"   ❌ 웹 검색 폴백도 실패: {e}")
            return self._default_score(stock_name, f"네이버 금융 + 웹 검색 모두 실패: {e}")
    
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