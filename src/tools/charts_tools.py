# 파일: src/tools/charts_tools.py
"""
기술적 분석 도구 모음
- RSI, MACD, 볼린저밴드, 이동평균선 등 계산
- CrewAI Agent에서 사용 가능한 Tool 형태로 제공
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

# CrewAI Tool
try:
    from crewai.tools import BaseTool
    HAS_CREWAI = True
except ImportError:
    HAS_CREWAI = False
    BaseTool = object

# 주가 데이터 로더
from src.data_pipeline.price_loader import PriceLoader


# ============================================================
# 기술적 지표 계산 클래스
# ============================================================

@dataclass
class TechnicalIndicators:
    """기술적 지표 결과 데이터 클래스"""
    stock_code: str
    stock_name: str
    date: str
    
    # 가격 정보
    current_price: float
    price_change: float  # 전일 대비 %
    
    # 이동평균선
    ma5: float
    ma20: float
    ma60: float
    ma120: float
    ma150: float
    
    # 추세 판단
    above_ma150: bool  # 150일선 위?
    golden_cross: bool  # 골든크로스 발생?
    death_cross: bool   # 데드크로스 발생?
    
    # RSI
    rsi_14: float
    rsi_signal: str  # "과매수", "과매도", "중립"
    
    # MACD
    macd: float
    macd_signal: float
    macd_histogram: float
    macd_trend: str  # "상승", "하락"
    
    # 볼린저 밴드
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_position: str  # "상단돌파", "하단돌파", "밴드내"
    bb_width: float  # 밴드폭 (변동성)
    
    # 스토캐스틱
    stoch_k: float
    stoch_d: float
    stoch_signal: str  # "과매수", "과매도", "중립"
    
    # ATR (변동성)
    atr_14: float
    atr_percent: float  # ATR / 현재가 %
    
    # 거래량
    volume: int
    volume_ma20: float
    volume_ratio: float  # 현재 거래량 / 20일 평균
    
    def to_dict(self) -> Dict:
        """딕셔너리 변환"""
        return {
            "종목코드": self.stock_code,
            "종목명": self.stock_name,
            "기준일": self.date,
            "현재가": f"{self.current_price:,.0f}원",
            "전일대비": f"{self.price_change:+.2f}%",
            "이동평균선": {
                "MA5": f"{self.ma5:,.0f}",
                "MA20": f"{self.ma20:,.0f}",
                "MA60": f"{self.ma60:,.0f}",
                "MA120": f"{self.ma120:,.0f}",
                "MA150": f"{self.ma150:,.0f}",
                "150일선_위": "✅" if self.above_ma150 else "❌"
            },
            "RSI_14": f"{self.rsi_14:.1f} ({self.rsi_signal})",
            "MACD": {
                "MACD": f"{self.macd:.2f}",
                "Signal": f"{self.macd_signal:.2f}",
                "Histogram": f"{self.macd_histogram:.2f}",
                "추세": self.macd_trend
            },
            "볼린저밴드": {
                "상단": f"{self.bb_upper:,.0f}",
                "중간": f"{self.bb_middle:,.0f}",
                "하단": f"{self.bb_lower:,.0f}",
                "위치": self.bb_position,
                "밴드폭": f"{self.bb_width:.1f}%"
            },
            "스토캐스틱": {
                "K": f"{self.stoch_k:.1f}",
                "D": f"{self.stoch_d:.1f}",
                "신호": self.stoch_signal
            },
            "ATR_14": f"{self.atr_14:,.0f} ({self.atr_percent:.1f}%)",
            "거래량": {
                "현재": f"{self.volume:,}",
                "20일평균": f"{self.volume_ma20:,.0f}",
                "비율": f"{self.volume_ratio:.1f}배"
            }
        }
    
    def summary(self) -> str:
        """요약 텍스트 생성"""
        lines = [
            f"═══════════════════════════════════════════════════",
            f"📊 {self.stock_name}({self.stock_code}) 기술적 분석",
            f"═══════════════════════════════════════════════════",
            f"📅 기준일: {self.date}",
            f"💰 현재가: {self.current_price:,.0f}원 ({self.price_change:+.2f}%)",
            f"",
            f"【 추세 분석 】",
            f"  • 150일선: {'✅ 상승추세 (가격 > MA150)' if self.above_ma150 else '❌ 하락추세 (가격 < MA150)'}",
            f"  • 이평선 배열: MA5({self.ma5:,.0f}) / MA20({self.ma20:,.0f}) / MA60({self.ma60:,.0f})",
            f"",
            f"【 모멘텀 지표 】",
            f"  • RSI(14): {self.rsi_14:.1f} → {self.rsi_signal}",
            f"  • MACD: {self.macd:.2f} (Signal: {self.macd_signal:.2f}) → {self.macd_trend}",
            f"  • 스토캐스틱: K={self.stoch_k:.1f}, D={self.stoch_d:.1f} → {self.stoch_signal}",
            f"",
            f"【 변동성 분석 】",
            f"  • 볼린저밴드: {self.bb_position} (밴드폭: {self.bb_width:.1f}%)",
            f"  • ATR(14): {self.atr_14:,.0f}원 (변동성: {self.atr_percent:.1f}%)",
            f"",
            f"【 거래량 분석 】",
            f"  • 현재 거래량: {self.volume:,}주",
            f"  • 20일 평균 대비: {self.volume_ratio:.1f}배",
            f"═══════════════════════════════════════════════════",
        ]
        return "\n".join(lines)


class TechnicalAnalyzer:
    """기술적 지표 계산기"""
    
    def __init__(self):
        self.price_loader = PriceLoader()
    
    def analyze(self, stock_code: str, stock_name: str = "Unknown", days: int = 300) -> TechnicalIndicators:
        """
        종목의 기술적 지표를 계산합니다.
        
        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            days: 데이터 수집 기간
            
        Returns:
            TechnicalIndicators 객체
        """
        # 주가 데이터 로드
        df = self.price_loader.get_stock_data(stock_code, days=days)
        
        if len(df) < 150:
            raise ValueError(f"데이터 부족: {len(df)}일 (최소 150일 필요)")
        
        # 기술적 지표 계산
        df = self._calculate_all_indicators(df)
        
        # 최신 데이터 추출
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 가격 변동률
        price_change = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
        
        # 추세 판단
        above_ma150 = latest['Close'] > latest['MA150']
        golden_cross = (prev['MA5'] <= prev['MA20']) and (latest['MA5'] > latest['MA20'])
        death_cross = (prev['MA5'] >= prev['MA20']) and (latest['MA5'] < latest['MA20'])
        
        # RSI 신호
        rsi_signal = self._get_rsi_signal(latest['RSI'])
        
        # MACD 추세
        macd_trend = "상승" if latest['MACD_Histogram'] > 0 else "하락"
        
        # 볼린저밴드 위치
        bb_position = self._get_bb_position(latest['Close'], latest['BB_Upper'], latest['BB_Lower'])
        bb_width = ((latest['BB_Upper'] - latest['BB_Lower']) / latest['BB_Middle']) * 100
        
        # 스토캐스틱 신호
        stoch_signal = self._get_stoch_signal(latest['Stoch_K'], latest['Stoch_D'])
        
        # ATR 퍼센트
        atr_percent = (latest['ATR'] / latest['Close']) * 100
        
        # 거래량 비율
        volume_ratio = latest['Volume'] / latest['Volume_MA20'] if latest['Volume_MA20'] > 0 else 0
        
        return TechnicalIndicators(
            stock_code=stock_code,
            stock_name=stock_name,
            date=df.index[-1].strftime("%Y-%m-%d"),
            current_price=latest['Close'],
            price_change=price_change,
            ma5=latest['MA5'],
            ma20=latest['MA20'],
            ma60=latest['MA60'],
            ma120=latest['MA120'],
            ma150=latest['MA150'],
            above_ma150=above_ma150,
            golden_cross=golden_cross,
            death_cross=death_cross,
            rsi_14=latest['RSI'],
            rsi_signal=rsi_signal,
            macd=latest['MACD'],
            macd_signal=latest['MACD_Signal'],
            macd_histogram=latest['MACD_Histogram'],
            macd_trend=macd_trend,
            bb_upper=latest['BB_Upper'],
            bb_middle=latest['BB_Middle'],
            bb_lower=latest['BB_Lower'],
            bb_position=bb_position,
            bb_width=bb_width,
            stoch_k=latest['Stoch_K'],
            stoch_d=latest['Stoch_D'],
            stoch_signal=stoch_signal,
            atr_14=latest['ATR'],
            atr_percent=atr_percent,
            volume=int(latest['Volume']),
            volume_ma20=latest['Volume_MA20'],
            volume_ratio=volume_ratio
        )
    
    def _calculate_all_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """모든 기술적 지표 계산"""
        df = df.copy()
        
        # 이동평균선
        df['MA5'] = df['Close'].rolling(window=5).mean()
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['MA60'] = df['Close'].rolling(window=60).mean()
        df['MA120'] = df['Close'].rolling(window=120).mean()
        df['MA150'] = df['Close'].rolling(window=150).mean()
        
        # RSI
        df['RSI'] = self._calculate_rsi(df['Close'], period=14)
        
        # MACD
        df['MACD'], df['MACD_Signal'], df['MACD_Histogram'] = self._calculate_macd(df['Close'])
        
        # 볼린저 밴드
        df['BB_Middle'] = df['Close'].rolling(window=20).mean()
        bb_std = df['Close'].rolling(window=20).std()
        df['BB_Upper'] = df['BB_Middle'] + (bb_std * 2)
        df['BB_Lower'] = df['BB_Middle'] - (bb_std * 2)
        
        # 스토캐스틱
        df['Stoch_K'], df['Stoch_D'] = self._calculate_stochastic(df)
        
        # ATR
        df['ATR'] = self._calculate_atr(df, period=14)
        
        # 거래량 이동평균
        df['Volume_MA20'] = df['Volume'].rolling(window=20).mean()
        
        return df
    
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """RSI 계산"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def _calculate_macd(self, prices: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """MACD 계산"""
        ema12 = prices.ewm(span=12, adjust=False).mean()
        ema26 = prices.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        histogram = macd - signal
        return macd, signal, histogram
    
    def _calculate_stochastic(self, df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
        """스토캐스틱 계산"""
        low_min = df['Low'].rolling(window=k_period).min()
        high_max = df['High'].rolling(window=k_period).max()
        
        stoch_k = ((df['Close'] - low_min) / (high_max - low_min)) * 100
        stoch_d = stoch_k.rolling(window=d_period).mean()
        
        return stoch_k, stoch_d
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ATR (Average True Range) 계산"""
        high = df['High']
        low = df['Low']
        close = df['Close'].shift(1)
        
        tr1 = high - low
        tr2 = abs(high - close)
        tr3 = abs(low - close)
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        return atr
    
    def _get_rsi_signal(self, rsi: float) -> str:
        """RSI 신호 판단"""
        if rsi >= 70:
            return "과매수"
        elif rsi <= 30:
            return "과매도"
        else:
            return "중립"
    
    def _get_bb_position(self, price: float, upper: float, lower: float) -> str:
        """볼린저밴드 위치 판단"""
        if price >= upper:
            return "상단돌파"
        elif price <= lower:
            return "하단돌파"
        else:
            return "밴드내"
    
    def _get_stoch_signal(self, k: float, d: float) -> str:
        """스토캐스틱 신호 판단"""
        if k >= 80 and d >= 80:
            return "과매수"
        elif k <= 20 and d <= 20:
            return "과매도"
        else:
            return "중립"


# ============================================================
# CrewAI Tool 구현
# ============================================================

if HAS_CREWAI:
    
    class TechnicalAnalysisTool(BaseTool):
        """종합 기술적 분석 도구 (CrewAI)"""
        name: str = "Technical Analysis"
        description: str = (
            "Performs comprehensive technical analysis on a stock. "
            "Calculates RSI, MACD, Bollinger Bands, Stochastic, moving averages, and ATR. "
            "Input should be the stock code (e.g., '005930' for Samsung Electronics). "
            "Returns detailed technical indicators and signals."
        )
        
        _analyzer: TechnicalAnalyzer = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._analyzer = TechnicalAnalyzer()
        
        def _run(self, stock_code: str) -> str:
            """기술적 분석 실행"""
            try:
                # 종목코드 정리
                stock_code = stock_code.strip().replace(" ", "")
                
                # 분석 실행
                result = self._analyzer.analyze(stock_code, stock_name=stock_code)
                return result.summary()
                
            except Exception as e:
                return f"기술적 분석 오류: {str(e)}"
    
    
    class RSIAnalysisTool(BaseTool):
        """RSI 분석 도구 (CrewAI)"""
        name: str = "RSI Analysis"
        description: str = (
            "Analyzes the RSI (Relative Strength Index) of a stock. "
            "RSI above 70 indicates overbought, below 30 indicates oversold. "
            "Input should be the stock code (e.g., '005930')."
        )
        
        _analyzer: TechnicalAnalyzer = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._analyzer = TechnicalAnalyzer()
        
        def _run(self, stock_code: str) -> str:
            """RSI 분석"""
            try:
                stock_code = stock_code.strip()
                result = self._analyzer.analyze(stock_code)
                
                return f"""
📊 RSI 분석 결과 ({stock_code})
━━━━━━━━━━━━━━━━━━━━━━━━
• RSI(14): {result.rsi_14:.1f}
• 신호: {result.rsi_signal}
• 해석: {'매도 신호 - 과매수 구간' if result.rsi_signal == '과매수' else '매수 신호 - 과매도 구간' if result.rsi_signal == '과매도' else '중립 구간 - 추세 지속'}
"""
            except Exception as e:
                return f"RSI 분석 오류: {str(e)}"
    
    
    class MACDAnalysisTool(BaseTool):
        """MACD 분석 도구 (CrewAI)"""
        name: str = "MACD Analysis"
        description: str = (
            "Analyzes the MACD (Moving Average Convergence Divergence) of a stock. "
            "Positive histogram indicates bullish momentum, negative indicates bearish. "
            "Input should be the stock code (e.g., '005930')."
        )
        
        _analyzer: TechnicalAnalyzer = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._analyzer = TechnicalAnalyzer()
        
        def _run(self, stock_code: str) -> str:
            """MACD 분석"""
            try:
                stock_code = stock_code.strip()
                result = self._analyzer.analyze(stock_code)
                
                cross_status = ""
                if result.macd > result.macd_signal:
                    cross_status = "MACD가 Signal 위에 있음 (상승 모멘텀)"
                else:
                    cross_status = "MACD가 Signal 아래에 있음 (하락 모멘텀)"
                
                return f"""
📊 MACD 분석 결과 ({stock_code})
━━━━━━━━━━━━━━━━━━━━━━━━
• MACD: {result.macd:.2f}
• Signal: {result.macd_signal:.2f}
• Histogram: {result.macd_histogram:.2f}
• 추세: {result.macd_trend}
• 상태: {cross_status}
"""
            except Exception as e:
                return f"MACD 분석 오류: {str(e)}"
    
    
    class BollingerBandTool(BaseTool):
        """볼린저밴드 분석 도구 (CrewAI)"""
        name: str = "Bollinger Band Analysis"
        description: str = (
            "Analyzes the Bollinger Bands of a stock. "
            "Shows if price is near upper band (overbought) or lower band (oversold). "
            "Input should be the stock code (e.g., '005930')."
        )
        
        _analyzer: TechnicalAnalyzer = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._analyzer = TechnicalAnalyzer()
        
        def _run(self, stock_code: str) -> str:
            """볼린저밴드 분석"""
            try:
                stock_code = stock_code.strip()
                result = self._analyzer.analyze(stock_code)
                
                interpretation = ""
                if result.bb_position == "상단돌파":
                    interpretation = "상단밴드 돌파 - 과매수 또는 강한 상승 추세"
                elif result.bb_position == "하단돌파":
                    interpretation = "하단밴드 돌파 - 과매도 또는 강한 하락 추세"
                else:
                    interpretation = "밴드 내 - 정상 변동 범위"
                
                return f"""
📊 볼린저밴드 분석 결과 ({stock_code})
━━━━━━━━━━━━━━━━━━━━━━━━
• 현재가: {result.current_price:,.0f}원
• 상단밴드: {result.bb_upper:,.0f}원
• 중간밴드: {result.bb_middle:,.0f}원 (20일 이평)
• 하단밴드: {result.bb_lower:,.0f}원
• 밴드폭: {result.bb_width:.1f}% (변동성)
• 위치: {result.bb_position}
• 해석: {interpretation}
"""
            except Exception as e:
                return f"볼린저밴드 분석 오류: {str(e)}"
    
    
    class TrendAnalysisTool(BaseTool):
        """추세 분석 도구 (CrewAI)"""
        name: str = "Trend Analysis"
        description: str = (
            "Analyzes the trend of a stock using moving averages. "
            "Checks if price is above 150-day MA and detects golden/death crosses. "
            "Input should be the stock code (e.g., '005930')."
        )
        
        _analyzer: TechnicalAnalyzer = None
        
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._analyzer = TechnicalAnalyzer()
        
        def _run(self, stock_code: str) -> str:
            """추세 분석"""
            try:
                stock_code = stock_code.strip()
                result = self._analyzer.analyze(stock_code)
                
                # 이평선 배열 판단
                ma_alignment = ""
                if result.ma5 > result.ma20 > result.ma60:
                    ma_alignment = "정배열 (상승 추세)"
                elif result.ma5 < result.ma20 < result.ma60:
                    ma_alignment = "역배열 (하락 추세)"
                else:
                    ma_alignment = "혼조 (횡보/전환 구간)"
                
                cross_event = ""
                if result.golden_cross:
                    cross_event = "🌟 골든크로스 발생! (단기 상승 신호)"
                elif result.death_cross:
                    cross_event = "💀 데드크로스 발생! (단기 하락 신호)"
                else:
                    cross_event = "특별한 크로스 없음"
                
                return f"""
📊 추세 분석 결과 ({stock_code})
━━━━━━━━━━━━━━━━━━━━━━━━
• 현재가: {result.current_price:,.0f}원
• 150일선: {result.ma150:,.0f}원 ({'✅ 상승추세' if result.above_ma150 else '❌ 하락추세'})
• 이평선 배열: {ma_alignment}
  - MA5: {result.ma5:,.0f}원
  - MA20: {result.ma20:,.0f}원
  - MA60: {result.ma60:,.0f}원
  - MA120: {result.ma120:,.0f}원
• 크로스: {cross_event}
"""
            except Exception as e:
                return f"추세 분석 오류: {str(e)}"


# ============================================================
# 직접 사용 가능한 함수
# ============================================================

def analyze_stock(stock_code: str, stock_name: str = "Unknown") -> TechnicalIndicators:
    """종목 기술적 분석"""
    analyzer = TechnicalAnalyzer()
    return analyzer.analyze(stock_code, stock_name)


def get_rsi(stock_code: str) -> float:
    """RSI 값만 조회"""
    result = analyze_stock(stock_code)
    return result.rsi_14


def get_macd(stock_code: str) -> Dict:
    """MACD 값 조회"""
    result = analyze_stock(stock_code)
    return {
        "macd": result.macd,
        "signal": result.macd_signal,
        "histogram": result.macd_histogram,
        "trend": result.macd_trend
    }


def is_bullish(stock_code: str) -> bool:
    """상승 추세 여부"""
    result = analyze_stock(stock_code)
    return result.above_ma150


# ============================================================
# 테스트
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("📊 기술적 분석 도구 테스트")
    print("=" * 60)
    
    analyzer = TechnicalAnalyzer()
    
    # SK하이닉스 테스트
    result = analyzer.analyze("000660", "SK하이닉스")
    print(result.summary())
