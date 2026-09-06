# 파일: src/agents/chartist.py
"""
Chartist Agent - KRX 차트 데이터 기반 기술적 분석 LLM 에이전트
"""

import json
import re
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from src.agents.llm_config import get_chartist_llm
from src.tools.charts_tools import TechnicalAnalyzer
from src.utils.prompt_loader import load_prompt


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
    live_current_price: float = 0
    price_snapshot_source: str = ""
    price_snapshot_at: str = ""
    live_vs_daily_close_pct: float = 0
    entry_timing: str = ""
    overheat_risk: str = ""
    technical_invalid_price: str = ""
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

    def __init__(self, data_dir: Optional[str] = None, theme_key: Optional[str] = None):
        self.llm = get_chartist_llm()
        self.analyzer = TechnicalAnalyzer(data_dir=data_dir, theme_key=theme_key)

    def analyze_technicals(self, stock_name: str, stock_code: str) -> str:
        """종목의 기술적 분석 수행 후 보고서 문자열 반환"""
        return self.generate_report(self.full_analysis(stock_name, stock_code), stock_name)
    
    def full_analysis(
        self,
        stock_name: str,
        stock_code: str,
        price_snapshot: Optional[Dict[str, Any]] = None,
    ) -> ChartistScore:
        """
        KRX 차트 데이터와 Python 계산 지표를 LLM에 전달해 기술적 분석을 수행합니다.
        """
        print(f"📊 [Chartist] {stock_name}({stock_code}) 기술적 분석 중...")

        try:
            indicators = self.analyzer.analyze(stock_code, stock_name=stock_name)
            recent_candles = self._load_recent_candles(stock_code, days=30)
            snapshot_context = self._build_price_snapshot_context(
                daily_close=indicators.current_price,
                price_snapshot=price_snapshot,
            )
            prompt = self._build_analysis_prompt(
                stock_name=stock_name,
                stock_code=stock_code,
                indicators=indicators,
                recent_candles=recent_candles,
                price_snapshot_context=snapshot_context,
            )
            payload = self._invoke_llm_json(prompt)
            score = self._score_from_payload(payload, indicators, snapshot_context)

            return score

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
            target_price="N/A",
        )

    def _build_analysis_prompt(
        self,
        *,
        stock_name: str,
        stock_code: str,
        indicators: Any,
        recent_candles: List[Dict[str, Any]],
        price_snapshot_context: Dict[str, Any],
    ) -> str:
        return load_prompt(
            "chartist",
            "chartist",
            stock_name=stock_name,
            stock_code=stock_code,
            technical_indicators=json.dumps(
                {"technical_indicators": indicators.to_dict()},
                ensure_ascii=False,
                indent=2,
            ),
            recent_candles=json.dumps(
                {"recent_candles": recent_candles},
                ensure_ascii=False,
                indent=2,
            ),
            price_snapshot_context=json.dumps(
                {"price_snapshot": price_snapshot_context or None},
                ensure_ascii=False,
                indent=2,
            ),
        )

    def _load_recent_candles(self, stock_code: str, days: int = 30) -> List[Dict[str, Any]]:
        df = self.analyzer.price_loader.get_stock_data(stock_code, days=max(days, 60))
        rows: List[Dict[str, Any]] = []
        for index, row in df.tail(days).iterrows():
            rows.append(
                {
                    "date": index.strftime("%Y-%m-%d") if hasattr(index, "strftime") else str(index),
                    "open": self._to_float(row.get("Open")),
                    "high": self._to_float(row.get("High")),
                    "low": self._to_float(row.get("Low")),
                    "close": self._to_float(row.get("Close")),
                    "volume": self._to_float(row.get("Volume")),
                }
            )
        return rows

    def _invoke_llm_json(self, prompt: str) -> Dict[str, Any]:
        response = self.llm.invoke(prompt)
        response_text = str(getattr(response, "content", response)).strip()
        match = re.search(r"\{[\s\S]*\}", response_text)
        if not match:
            raise ValueError("JSON 형식 응답 없음")
        return json.loads(match.group())

    def _score_from_payload(
        self,
        payload: Dict[str, Any],
        indicators: Any,
        price_snapshot_context: Dict[str, Any],
    ) -> ChartistScore:
        trend_score = self._clamp_int(payload.get("trend_score", 15), 0, 30)
        momentum_score = self._clamp_int(payload.get("momentum_score", 15), 0, 30)
        volatility_score = self._clamp_int(payload.get("volatility_score", 10), 0, 20)
        volume_score = self._clamp_int(payload.get("volume_score", 10), 0, 20)
        total_score = trend_score + momentum_score + volatility_score + volume_score
        signal = str(payload.get("signal") or self._signal_from_total(total_score))

        return ChartistScore(
            trend_score=trend_score,
            momentum_score=momentum_score,
            volatility_score=volatility_score,
            volume_score=volume_score,
            total_score=total_score,
            signal=signal,
            trend_analysis=str(payload.get("trend_reason", "")),
            momentum_analysis=str(payload.get("momentum_reason", "")),
            volatility_analysis=str(payload.get("volatility_reason", "")),
            volume_analysis=str(payload.get("volume_reason", "")),
            current_price=float(getattr(indicators, "current_price", 0) or 0),
            live_current_price=price_snapshot_context.get("current_price", 0) or 0,
            price_snapshot_source=price_snapshot_context.get("source", ""),
            price_snapshot_at=price_snapshot_context.get("snapshot_at", ""),
            live_vs_daily_close_pct=price_snapshot_context.get("live_vs_daily_close_pct", 0) or 0,
            entry_timing=str(payload.get("entry_timing", "")),
            overheat_risk=str(payload.get("overheat_risk", "")),
            technical_invalid_price=str(payload.get("technical_invalid_price", "")),
            rsi=float(getattr(indicators, "rsi_14", 0) or 0),
            macd_histogram=float(getattr(indicators, "macd_histogram", 0) or 0),
            bb_position=str(getattr(indicators, "bb_position", "")),
            volume_ratio=float(getattr(indicators, "volume_ratio", 0) or 0),
            short_term_opinion=str(payload.get("trade_zone") or signal),
            mid_term_opinion=str(payload.get("final_opinion") or signal),
            stop_loss=str(payload.get("stop_loss") or self._default_stop_loss(indicators)),
            target_price=str(payload.get("target_price") or self._default_target_price(indicators)),
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

    def _build_price_snapshot_context(
        self,
        *,
        daily_close: float,
        price_snapshot: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not price_snapshot or not price_snapshot.get("success", True):
            return {}

        try:
            live_price = float(price_snapshot.get("current_price") or price_snapshot.get("currentPrice") or 0)
            daily = float(daily_close or 0)
        except (TypeError, ValueError):
            return {}
        if live_price <= 0 or daily <= 0:
            return {}

        drift = round(((live_price - daily) / daily) * 100, 2)
        abs_drift = abs(drift)
        if drift >= 10:
            overheat_risk = "high"
            entry_timing = "wait_for_pullback"
        elif drift >= 5:
            overheat_risk = "medium"
            entry_timing = "cautious_entry"
        elif drift <= -10:
            overheat_risk = "low"
            entry_timing = "falling_knife_watch"
        elif abs_drift <= 3:
            overheat_risk = "low"
            entry_timing = "acceptable"
        else:
            overheat_risk = "medium"
            entry_timing = "confirm_support"

        invalid_price = int(round(min(daily, live_price) * 0.95))
        return {
            "current_price": int(round(live_price)),
            "source": str(price_snapshot.get("source") or "kis"),
            "snapshot_at": str(price_snapshot.get("snapshot_at") or price_snapshot.get("snapshotAt") or ""),
            "live_vs_daily_close_pct": drift,
            "entry_timing": entry_timing,
            "overheat_risk": overheat_risk,
            "technical_invalid_price": f"{invalid_price:,}원",
        }

    @staticmethod
    def _clamp_int(value: Any, low: int, high: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = low
        return min(high, max(low, number))

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _signal_from_total(total_score: int) -> str:
        if total_score >= 75:
            return "적극 매수"
        if total_score >= 60:
            return "매수"
        if total_score >= 45:
            return "중립"
        if total_score >= 30:
            return "매도"
        return "적극 매도"

    @staticmethod
    def _default_stop_loss(indicators: Any) -> str:
        atr = float(getattr(indicators, "atr_14", 0) or 0)
        return f"-{atr * 2:.0f}원 (2ATR)" if atr else "N/A"

    @staticmethod
    def _default_target_price(indicators: Any) -> str:
        atr = float(getattr(indicators, "atr_14", 0) or 0)
        return f"+{atr * 3:.0f}원 (3ATR)" if atr else "N/A"


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
