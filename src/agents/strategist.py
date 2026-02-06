# 파일: src/agents/strategist.py
"""
Strategist Agent (전략가 에이전트)

역할: 헤게모니(경제적 해자) 분석 및 판단
- Researcher가 수집한 정보를 바탕으로 깊은 추론
- 산업 구조 분석
- 경쟁 우위 판단
- 정책 영향 평가
- 장기 성장성 판단

모델: Thinking (깊은 추론)
"""

from typing import Dict, Optional
from dataclasses import dataclass

from src.agents.llm_config import get_thinking_llm
from src.agents.researcher import ResearchResult, ResearcherAgent


@dataclass
class HegemonyScore:
    """헤게모니 분석 점수"""
    # 점수 (총 70점)
    moat_score: int  # 독점력/해자 (0-40점)
    growth_score: int  # 성장성 (0-30점)
    total_score: int  # 총점
    
    # 세부 분석
    moat_analysis: str  # 독점력 상세 분석
    growth_analysis: str  # 성장성 상세 분석
    
    # 핵심 판단
    competitive_advantage: str  # 경쟁 우위 요약
    risk_factors: str  # 주요 리스크
    policy_impact: str  # 정책 영향
    
    # 최종 의견
    hegemony_grade: str  # A/B/C/D/F
    final_opinion: str  # 한 줄 총평
    detailed_reasoning: str  # 상세 추론 과정


class StrategistAgent:
    """
    전략가 에이전트
    - Thinking 모델로 깊은 추론
    - 헤게모니(경제적 해자) 판단 전문
    """
    
    def __init__(self):
        self.llm = get_thinking_llm()
    
    def analyze_hegemony(
        self,
        research_result: ResearchResult
    ) -> HegemonyScore:
        """
        헤게모니 분석 수행
        
        Args:
            research_result: Researcher가 수집한 정보
            
        Returns:
            HegemonyScore 데이터클래스
        """
        stock_name = research_result.stock_name
        research_summary = research_result.to_strategist_prompt()
        
        print(f"🧠 {stock_name} 헤게모니 분석 중 (Thinking 모델)...")
        
        # Thinking 모델에 전달할 프롬프트
        analysis_prompt = f"""
당신은 20년 경력의 베테랑 투자 전략가입니다.
다음 리서치 자료를 바탕으로 '{stock_name}'의 헤게모니(경제적 해자)를 분석하세요.

{research_summary}

---

다음 관점에서 깊이 있게 분석하세요:

## 1. 독점력/경제적 해자 분석 (0-40점)
- 시장 점유율과 지배력
- 진입 장벽 (기술, 자본, 규모)
- 가격 결정력 (Pricing Power)
- 브랜드/네트워크 효과
- 전환 비용 (Switching Cost)

## 2. 성장성 분석 (0-30점)
- 미래 산업 연관성 (AI, 로봇, 친환경 등)
- 매출 성장 구조 (일회성 vs 구조적)
- TAM(Total Addressable Market) 확장 가능성
- R&D 투자와 기술 리더십

## 3. 정책/규제 영향
- 정부 지원 정책의 수혜 여부
- 규제 리스크
- 글로벌 통상 환경 영향

## 4. 경쟁 구도 분석
- 주요 경쟁사 대비 포지션
- 경쟁 심화/완화 전망
- 대체재 위협

## 5. 리스크 요인
- 산업 사이클 리스크
- 기술 변화 리스크
- 지정학적 리스크

---

[⚠️ 중요: 데이터 신뢰도에 따른 행동 강령]
Researcher가 제공한 '정보 품질 등급'을 반드시 확인하고, 아래 규칙을 따르십시오.

1. 등급이 'D (매우 부족)'인 경우:
   - 투자의견을 두 단계 낮추십시오 (예: 적극 매수 → 중립).
   - hegemony_grade를 C 이하로 부여하십시오.
   - final_opinion에 "데이터 심각 부족으로 분석 신뢰도가 매우 낮음"을 반드시 명시하십시오.
   - 점수는 보수적으로 부여하십시오 (각 항목 최대 60% 수준).

2. 등급이 'C (부족)'인 경우:
   - 투자의견을 한 단계 낮추십시오 (예: 적극 매수 → 매수, 매수 → 중립).
   - final_opinion에 "데이터 부족으로 인한 불확실성이 존재함"을 명시하십시오.
   - 확인되지 않은 정보에 대해서는 추측하지 말고 "확인 불가"로 표시하십시오.

3. 등급이 'B (양호)'인 경우:
   - 정상적으로 분석하되, quality_warnings에 언급된 빈 영역은 분석에서 제외하십시오.
   - "일부 데이터 미확보"를 간략히 언급하십시오.

4. 등급이 'A (충분)'인 경우:
   - 확보된 데이터를 근거로 확신에 찬 어조를 사용하십시오.
   - 구체적 수치를 적극 인용하십시오.

※ 데이터 출처가 'web'인 항목은 원문 리포트 대비 정확도가 낮을 수 있으므로,
  해당 정보에만 의존한 판단은 반드시 단서를 달아주십시오.

---

분석 결과를 다음 JSON 형식으로 출력하세요:

{{
    "moat_score": <0-40 정수>,
    "moat_analysis": "<독점력 분석 3-5문장>",
    "growth_score": <0-30 정수>,
    "growth_analysis": "<성장성 분석 3-5문장>",
    "competitive_advantage": "<경쟁 우위 핵심 1-2문장>",
    "risk_factors": "<주요 리스크 2-3가지>",
    "policy_impact": "<정책 영향 1-2문장>",
    "hegemony_grade": "<A/B/C/D/F 중 하나>",
    "final_opinion": "<한 줄 총평>",
    "detailed_reasoning": "<상세 추론 과정 5-10문장>"
}}

등급 기준:
- A (60-70점): 압도적 해자, 적극 매수
- B (50-59점): 견고한 해자, 매수
- C (40-49점): 보통 해자, 중립
- D (30-39점): 약한 해자, 관망
- F (0-29점): 해자 없음, 매도

JSON만 출력하세요.
"""
        
        try:
            response = self.llm.invoke(analysis_prompt)
            response_text = response.content.strip()
            
            # JSON 파싱
            import json
            import re
            
            # JSON 블록 추출
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise ValueError("JSON 형식 응답 없음")
            
            # 점수 범위 검증
            moat_score = min(40, max(0, int(result.get("moat_score", 20))))
            growth_score = min(30, max(0, int(result.get("growth_score", 15))))
            
            return HegemonyScore(
                moat_score=moat_score,
                growth_score=growth_score,
                total_score=moat_score + growth_score,
                moat_analysis=result.get("moat_analysis", ""),
                growth_analysis=result.get("growth_analysis", ""),
                competitive_advantage=result.get("competitive_advantage", ""),
                risk_factors=result.get("risk_factors", ""),
                policy_impact=result.get("policy_impact", ""),
                hegemony_grade=result.get("hegemony_grade", "C"),
                final_opinion=result.get("final_opinion", ""),
                detailed_reasoning=result.get("detailed_reasoning", "")
            )
            
        except Exception as e:
            print(f"❌ 분석 오류: {e}")
            return HegemonyScore(
                moat_score=20,
                growth_score=15,
                total_score=35,
                moat_analysis="분석 오류로 기본값 적용",
                growth_analysis="분석 오류로 기본값 적용",
                competitive_advantage="판단 불가",
                risk_factors="분석 오류",
                policy_impact="판단 불가",
                hegemony_grade="C",
                final_opinion="데이터 부족으로 중립 의견",
                detailed_reasoning=f"오류 발생: {str(e)}"
            )
    
    def analyze_from_scratch(
        self,
        stock_name: str,
        stock_code: str
    ) -> HegemonyScore:
        """
        Researcher + Strategist 통합 분석
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            
        Returns:
            HegemonyScore
        """
        # 1. Researcher로 정보 수집
        researcher = ResearcherAgent()
        research_result = researcher.research(stock_name, stock_code)
        
        # 2. 헤게모니 분석
        hegemony_score = self.analyze_hegemony(research_result)
        
        return hegemony_score
    
    def generate_report(self, score: HegemonyScore, stock_name: str) -> str:
        """
        분석 결과를 보고서 형식으로 출력
        
        Args:
            score: HegemonyScore
            stock_name: 종목명
            
        Returns:
            마크다운 형식 보고서
        """
        return f"""
# {stock_name} 헤게모니 분석 보고서

## 📊 점수 요약
| 항목 | 점수 | 비중 |
|------|------|------|
| 독점력 (Moat) | **{score.moat_score}** / 40 | 57% |
| 성장성 (Growth) | **{score.growth_score}** / 30 | 43% |
| **총점** | **{score.total_score}** / 70 | 100% |

## 🏆 헤게모니 등급: {score.hegemony_grade}

## 💡 핵심 판단
> {score.final_opinion}

---

## 1. 독점력/경제적 해자 분석
{score.moat_analysis}

**경쟁 우위:** {score.competitive_advantage}

## 2. 성장성 분석
{score.growth_analysis}

## 3. 정책 영향
{score.policy_impact}

## 4. 리스크 요인
{score.risk_factors}

---

## 📝 상세 추론 과정
{score.detailed_reasoning}
"""


# 사용 예시
if __name__ == "__main__":
    strategist = StrategistAgent()
    
    print("=" * 60)
    print("삼성전자 헤게모니 분석 (Researcher + Strategist)")
    print("=" * 60)
    
    # 통합 분석
    score = strategist.analyze_from_scratch("삼성전자", "005930")
    
    # 보고서 출력
    report = strategist.generate_report(score, "삼성전자")
    print(report)
