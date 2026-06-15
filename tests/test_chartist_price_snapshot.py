from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd

from src.agents.chartist import ChartistAgent


def _agent() -> ChartistAgent:
    return ChartistAgent.__new__(ChartistAgent)


class _FakeIndicators:
    date = "2026-06-03"
    current_price = 10100
    rsi_14 = 58.0
    macd_histogram = 12.5
    bb_position = "밴드내"
    volume_ratio = 1.3
    atr_14 = 250

    def to_dict(self):
        return {
            "current_price": self.current_price,
            "rsi": self.rsi_14,
            "macd_histogram": self.macd_histogram,
            "bb_position": self.bb_position,
            "atr": self.atr_14,
            "volume_ratio": self.volume_ratio,
            "이동평균선": {"MA5": "10,050", "MA20": "9,900", "MA60": "9,500"},
        }


class _FakePriceLoader:
    def get_stock_data(self, stock_code: str, days: int = 300):
        return pd.DataFrame(
            [
                {"Open": 9800, "High": 10000, "Low": 9700, "Close": 9900, "Volume": 1000},
                {"Open": 9900, "High": 10200, "Low": 9850, "Close": 10100, "Volume": 1800},
                {"Open": 10100, "High": 10300, "Low": 10000, "Close": 10100, "Volume": 1500},
            ],
            index=pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03"]),
        )


class _FakeAnalyzer:
    def __init__(self):
        self.price_loader = _FakePriceLoader()

    def analyze(self, stock_code: str, stock_name: str = "Unknown", days: int = 300):
        return _FakeIndicators()


class _FakeLLM:
    def __init__(self):
        self.last_prompt = ""

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return SimpleNamespace(
            content=json.dumps(
                {
                    "trend_score": 23,
                    "trend_reason": "최근 캔들이 MA 위에서 지지",
                    "momentum_score": 21,
                    "momentum_reason": "MACD 양수",
                    "volatility_score": 14,
                    "volatility_reason": "ATR 리스크 보통",
                    "volume_score": 13,
                    "volume_reason": "상승일 거래량 증가",
                    "total_score": 71,
                    "signal": "매수",
                    "trade_zone": "눌림 대기",
                    "entry_timing": "confirm_support",
                    "technical_invalid_price": "9,595원",
                    "stop_loss": "9,595원",
                    "target_price": "10,850원",
                    "overheat_risk": "medium",
                    "final_opinion": "지지 확인 후 분할 접근",
                },
                ensure_ascii=False,
            )
        )


def test_full_analysis_uses_llm_with_python_indicators_and_recent_raw_candles():
    agent = _agent()
    agent.llm = _FakeLLM()
    agent.analyzer = _FakeAnalyzer()
    agent.quick_check = lambda _stock_code: (_ for _ in ()).throw(
        AssertionError("quick_check should not be used")
    )

    score = agent.full_analysis("삼성전자", "005930")

    assert score.trend_score == 23
    assert score.momentum_score == 21
    assert score.total_score == 71
    assert score.signal == "매수"
    assert score.trend_analysis == "최근 캔들이 MA 위에서 지지"
    assert score.current_price == 10100
    assert score.live_current_price == 0
    assert not hasattr(score, "analysis_packet")
    assert '"technical_indicators"' in agent.llm.last_prompt
    assert '"recent_candles"' in agent.llm.last_prompt
    assert '"date": "2026-06-03"' in agent.llm.last_prompt
    assert '"close": 10100.0' in agent.llm.last_prompt


def test_full_analysis_adds_price_snapshot_context_to_llm_prompt_and_packet():
    agent = _agent()
    agent.llm = _FakeLLM()
    agent.analyzer = _FakeAnalyzer()
    agent.quick_check = lambda _stock_code: (_ for _ in ()).throw(
        AssertionError("quick_check should not be used")
    )

    score = agent.full_analysis(
        "삼성전자",
        "005930",
        price_snapshot={
            "stock_code": "005930",
            "current_price": 11200,
            "snapshot_at": "2026-06-02T10:15:00+09:00",
            "source": "kis",
            "success": True,
        },
    )

    assert score.current_price == 10100
    assert score.live_current_price == 11200
    assert score.live_vs_daily_close_pct == 10.89
    assert score.price_snapshot_source == "kis"
    assert score.price_snapshot_at == "2026-06-02T10:15:00+09:00"
    assert score.overheat_risk == "medium"
    assert score.entry_timing == "confirm_support"
    assert score.technical_invalid_price == "9,595원"
    assert '"price_snapshot"' in agent.llm.last_prompt
