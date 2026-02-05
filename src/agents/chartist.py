# 파일: src/agents/chartist.py
"""
Chartist Agent - 기술적 분석 전문 에이전트

역할:
- 기술적 지표(RSI, MACD, 볼린저밴드 등) 기반 분석
- 추세 및 모멘텀 판단
- 매매 타이밍 제안

모델: Instruct (빠름)
점수 체계: 100점 만점 (추세 30 + 모멘텀 30 + 변동성 20 + 거래량 20)
"""

from typing import Dict, Optional
from dataclasses import dataclass

from crewai import Agent, Task, Crew, Process
from src.agents.llm_config import get_gemini_llm
from src.tools.charts_tools import (
    TechnicalAnalysisTool,
    RSIAnalysisTool,
    MACDAnalysisTool,
    BollingerBandTool,
    TrendAnalysisTool,
    analyze_stock
)


@dataclass
class ChartistScore:
    """차티스트 분석 점수"""
    # 점수 (총 100점)
    trend_score: int  # 추세 (0-30)
    momentum_score: int  # 모멘텀 (0-30)
    volatility_score: int  # 변동성 (0-20)
    volume_score: int  # 거래량 (0-20)
    total_score: int  # 총점 (0-100)
    
    # 신호
    signal: str  # 매수/중립/매도
    
    # 세부 분석
    trend_analysis: str
    momentum_analysis: str
    volatility_analysis: str
    volume_analysis: str
    
    # 핵심 지표
    current_price: float = 0
    rsi: float = 0
    macd_histogram: float = 0
    bb_position: str = ""
    volume_ratio: float = 0
    
    # 매매 전략
    short_term_opinion: str = ""  # 단기 의견
    mid_term_opinion: str = ""  # 중기 의견
    stop_loss: str = ""  # 손절가
    target_price: str = ""  # 목표가


class ChartistAgent:
    """기술적 분석 에이전트"""
    
    def __init__(self):
        self.llm = get_gemini_llm()
        
        # 기술적 분석 도구들
        self.tools = [
            TechnicalAnalysisTool(),
            RSIAnalysisTool(),
            MACDAnalysisTool(),
            BollingerBandTool(),
            TrendAnalysisTool()
        ]
    
    def analyze_technicals(self, stock_name: str, stock_code: str) -> str:
        """
        종목의 기술적 분석 수행
        
        Args:
            stock_name: 종목명
            stock_code: 종목 코드
            
        Returns:
            기술적 분석 리포트 (문자열)
        """
        # 에이전트 설정
        chartist = Agent(
            role='Senior Technical Analyst',
            goal=f'{stock_name}({stock_code})의 차트를 분석하여 단기/중기 매매 타이밍과 추세를 판단',
            backstory="""
                당신은 15년 경력의 기술적 분석 전문가입니다.
                RSI, MACD, 볼린저밴드, 이동평균선 등 다양한 지표를 종합하여
                정확한 매매 타이밍과 추세를 판단하는 능력이 탁월합니다.
                감정에 휘둘리지 않고 오직 차트와 지표만으로 객관적인 분석을 제공합니다.
            """,
            tools=self.tools,
            llm=self.llm,
            function_calling_llm=self.llm,
            verbose=True,
            allow_delegation=False,
            max_rpm=5
        )
        
        # 태스크 설정
        analysis_task = Task(
            description=f"""
                '{stock_name}'(종목코드: {stock_code})의 기술적 분석을 수행하세요.
                
                [분석 단계]
                1. Technical Analysis 도구로 종합 지표를 확인하세요.
                2. 각 지표의 의미를 해석하고 종합적인 판단을 내리세요.
                
                [평가 항목]
                A. 추세 분석 (0~25점)
                   - 150일 이동평균선 위/아래 여부
                   - 이평선 배열 (정배열/역배열/혼조)
                   - 골든크로스/데드크로스 발생 여부
                
                B. 모멘텀 분석 (0~25점)
                   - RSI: 과매수(70+)/과매도(30-)/중립
                   - MACD: 히스토그램 방향, 시그널 크로스
                   - 스토캐스틱: 과매수/과매도 구간
                
                C. 변동성 분석 (0~25점)
                   - 볼린저밴드 위치 (상단돌파/하단돌파/밴드내)
                   - ATR 기반 변동성 수준
                   - 밴드폭 (수축/확장)
                
                D. 거래량 분석 (0~25점)
                   - 20일 평균 대비 거래량
                   - 거래량 동반 여부
                
                3. 최종 매매 의견을 제시하세요 (반드시 한글로 작성).
            """,
            expected_output=f"""
                # {stock_name} 기술적 분석 보고서
                
                ## 1. 지표 요약
                (주요 기술적 지표 수치 정리)
                
                ## 2. 세부 분석
                ### A. 추세 분석 (XX/25점)
                - 이평선 분석 내용...
                
                ### B. 모멘텀 분석 (XX/25점)
                - RSI, MACD 분석 내용...
                
                ### C. 변동성 분석 (XX/25점)
                - 볼린저밴드 분석 내용...
                
                ### D. 거래량 분석 (XX/25점)
                - 거래량 분석 내용...
                
                ## 3. 종합 점수: XX / 100점
                
                ## 4. 매매 의견
                - **단기(1-2주):** 매수/관망/매도
                - **중기(1-3개월):** 매수/관망/매도
                - **손절가:** XXX원 (ATR 기반)
                - **목표가:** XXX원
                
                ## 5. 핵심 요약 (한 줄)
            """,
            agent=chartist
        )
        
        # 크루 실행
        crew = Crew(
            agents=[chartist],
            tasks=[analysis_task],
            process=Process.sequential,
            verbose=True
        )
        
        result = crew.kickoff()
        return result
    
    def quick_check(self, stock_code: str) -> dict:
        """
        빠른 기술적 상태 확인 (에이전트 없이 직접 계산)
        
        Args:
            stock_code: 종목 코드
            
        Returns:
            기술적 상태 딕셔너리
        """
        try:
            result = analyze_stock(stock_code)
            
            # 세부 점수 계산
            trend_score = 0
            momentum_score = 0
            volatility_score = 0
            volume_score = 0
            
            trend_signals = []
            momentum_signals = []
            volatility_signals = []
            volume_signals = []
            
            # 추세 점수 (30점)
            if result.above_ma150:
                trend_score += 18
                trend_signals.append("✅ 150일선 위 (상승추세)")
            else:
                trend_signals.append("❌ 150일선 아래 (하락추세)")
            
            if result.ma5 > result.ma20 > result.ma60:
                trend_score += 12
                trend_signals.append("✅ 이평선 정배열")
            elif result.ma5 < result.ma20 < result.ma60:
                trend_signals.append("❌ 이평선 역배열")
            else:
                trend_score += 6
                trend_signals.append("➖ 이평선 혼조")
            
            # 모멘텀 점수 (30점)
            if result.rsi_signal == "과매도":
                momentum_score += 18
                momentum_signals.append("✅ RSI 과매도 (반등 기대)")
            elif result.rsi_signal == "과매수":
                momentum_score += 3
                momentum_signals.append("⚠️ RSI 과매수 (조정 주의)")
            else:
                momentum_score += 12
                momentum_signals.append("➖ RSI 중립")
            
            if result.macd_histogram > 0:
                momentum_score += 12
                momentum_signals.append("✅ MACD 상승 모멘텀")
            else:
                momentum_signals.append("❌ MACD 하락 모멘텀")
            
            # 변동성 점수 (20점)
            if result.bb_position == "하단돌파":
                volatility_score += 12
                volatility_signals.append("✅ 볼린저 하단 (반등 기대)")
            elif result.bb_position == "상단돌파":
                volatility_score += 4
                volatility_signals.append("⚠️ 볼린저 상단 (과열)")
            else:
                volatility_score += 8
                volatility_signals.append("➖ 볼린저 밴드 내")
            
            if result.bb_width < 10:
                volatility_score += 4
                volatility_signals.append("🔥 밴드 수축 (변동성 확대 예상)")
            else:
                volatility_score += 8
            
            # 거래량 점수 (20점)
            if result.volume_ratio > 2:
                volume_score += 14
                volume_signals.append("🔥 거래량 급증")
            elif result.volume_ratio > 1:
                volume_score += 10
                volume_signals.append("✅ 거래량 양호")
            else:
                volume_score += 4
                volume_signals.append("➖ 거래량 부족")
            
            volume_score += 6  # 기본 점수
            
            # 총점 계산
            total_score = trend_score + momentum_score + volatility_score + volume_score
            
            # 매매 의견
            if total_score >= 75:
                signal = "적극 매수"
            elif total_score >= 60:
                signal = "매수"
            elif total_score >= 45:
                signal = "중립"
            elif total_score >= 30:
                signal = "매도"
            else:
                signal = "적극 매도"
            
            return {
                "stock_code": stock_code,
                "date": result.date,
                "price": result.current_price,
                "trend_score": trend_score,
                "momentum_score": momentum_score,
                "volatility_score": volatility_score,
                "volume_score": volume_score,
                "total_score": total_score,
                "signal": signal,
                "trend_signals": trend_signals,
                "momentum_signals": momentum_signals,
                "volatility_signals": volatility_signals,
                "volume_signals": volume_signals,
                "indicators": result.to_dict()
            }
            
        except Exception as e:
            return {
                "error": str(e),
                "stock_code": stock_code
            }
    
    def full_analysis(self, stock_name: str, stock_code: str) -> ChartistScore:
        """
        전체 기술적 분석 수행 (Risk Manager 호환)
        
        Args:
            stock_name: 종목명
            stock_code: 종목코드
            
        Returns:
            ChartistScore 데이터클래스
        """
        print(f"📊 [Chartist] {stock_name}({stock_code}) 기술적 분석 중...")
        
        try:
            # 빠른 체크로 데이터 수집
            check_result = self.quick_check(stock_code)
            
            if "error" in check_result:
                return self._default_score(stock_code, check_result["error"])
            
            # ChartistScore 생성
            return ChartistScore(
                trend_score=check_result["trend_score"],
                momentum_score=check_result["momentum_score"],
                volatility_score=check_result["volatility_score"],
                volume_score=check_result["volume_score"],
                total_score=check_result["total_score"],
                signal=check_result["signal"],
                trend_analysis=", ".join(check_result["trend_signals"]),
                momentum_analysis=", ".join(check_result["momentum_signals"]),
                volatility_analysis=", ".join(check_result["volatility_signals"]),
                volume_analysis=", ".join(check_result["volume_signals"]),
                current_price=check_result["price"],
                rsi=check_result["indicators"].get("rsi", 0),
                macd_histogram=check_result["indicators"].get("macd_histogram", 0),
                bb_position=check_result["indicators"].get("bb_position", ""),
                volume_ratio=check_result["indicators"].get("volume_ratio", 0),
                short_term_opinion=check_result["signal"],
                mid_term_opinion=check_result["signal"],
                stop_loss=f"-{check_result['indicators'].get('atr', 0) * 2:.0f}원 (2ATR)",
                target_price=f"+{check_result['indicators'].get('atr', 0) * 3:.0f}원 (3ATR)"
            )
            
        except Exception as e:
            print(f"❌ 기술적 분석 오류: {e}")
            return self._default_score(stock_code, str(e))
    
    def _default_score(self, stock_code: str, error: str) -> ChartistScore:
        """오류 시 기본 점수 반환"""
        return ChartistScore(
            trend_score=15,
            momentum_score=15,
            volatility_score=10,
            volume_score=10,
            total_score=50,
            signal="중립",
            trend_analysis=f"데이터 오류: {error}",
            momentum_analysis="분석 불가",
            volatility_analysis="분석 불가",
            volume_analysis="분석 불가",
            short_term_opinion="관망",
            mid_term_opinion="관망",
            stop_loss="N/A",
            target_price="N/A"
        )
    
    def generate_report(self, score: ChartistScore, stock_name: str) -> str:
        """
        분석 결과를 보고서 형식으로 출력
        """
        # 신호 이모지
        signal_emoji = {
            "적극 매수": "🚀",
            "매수": "📈",
            "중립": "⏸️",
            "매도": "📉",
            "적극 매도": "⛔"
        }
        
        return f"""
# {stock_name} 기술적 분석 보고서

## {signal_emoji.get(score.signal, "📊")} 매매 신호: {score.signal}

| 항목 | 점수 | 비중 |
|------|------|------|
| 추세 | **{score.trend_score}** / 30 | 30% |
| 모멘텀 | **{score.momentum_score}** / 30 | 30% |
| 변동성 | **{score.volatility_score}** / 20 | 20% |
| 거래량 | **{score.volume_score}** / 20 | 20% |
| **총점** | **{score.total_score}** / 100 | 100% |

---

## 1. 추세 분석 ({score.trend_score}/30점)
{score.trend_analysis}

## 2. 모멘텀 분석 ({score.momentum_score}/30점)
{score.momentum_analysis}

## 3. 변동성 분석 ({score.volatility_score}/20점)
{score.volatility_analysis}

## 4. 거래량 분석 ({score.volume_score}/20점)
{score.volume_analysis}

---

## 📈 매매 전략
- **단기(1-2주):** {score.short_term_opinion}
- **중기(1-3개월):** {score.mid_term_opinion}
- **손절가:** {score.stop_loss}
- **목표가:** {score.target_price}

## 📊 핵심 지표
- 현재가: {score.current_price:,.0f}원
- RSI: {score.rsi:.1f}
- MACD Histogram: {score.macd_histogram:.2f}
- 볼린저밴드 위치: {score.bb_position}
- 거래량 비율: {score.volume_ratio:.2f}x
"""


# 테스트
if __name__ == "__main__":
    print("=" * 60)
    print("📊 Chartist Agent 테스트")
    print("=" * 60)
    
    chartist = ChartistAgent()
    
    # 전체 분석 테스트
    print("\n[1] 전체 기술적 분석 (SK하이닉스)")
    score = chartist.full_analysis("SK하이닉스", "000660")
    
    print(f"\n📊 점수 요약:")
    print(f"   추세: {score.trend_score}/30")
    print(f"   모멘텀: {score.momentum_score}/30")
    print(f"   변동성: {score.volatility_score}/20")
    print(f"   거래량: {score.volume_score}/20")
    print(f"   총점: {score.total_score}/100")
    print(f"   신호: {score.signal}")
    
    # 보고서 출력
    print("\n" + "=" * 60)
    report = chartist.generate_report(score, "SK하이닉스")
    print(report)
