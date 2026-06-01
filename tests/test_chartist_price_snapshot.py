from __future__ import annotations

from src.agents.chartist import ChartistAgent


def _agent() -> ChartistAgent:
    return ChartistAgent.__new__(ChartistAgent)


def _quick_check(price: int = 10000) -> dict:
    return {
        "stock_code": "005930",
        "date": "2026-06-02",
        "price": price,
        "trend_score": 20,
        "momentum_score": 18,
        "volatility_score": 14,
        "volume_score": 12,
        "total_score": 64,
        "signal": "매수",
        "trend_signals": ["150일선 위"],
        "momentum_signals": ["MACD 상승"],
        "volatility_signals": ["밴드 내"],
        "volume_signals": ["거래량 양호"],
        "indicators": {
            "rsi": 55.0,
            "macd_histogram": 12.5,
            "bb_position": "밴드내",
            "volume_ratio": 1.3,
            "atr": 250,
        },
    }


def test_full_analysis_without_price_snapshot_keeps_daily_chart_price(monkeypatch):
    agent = _agent()
    monkeypatch.setattr(agent, "quick_check", lambda _stock_code: _quick_check(price=10000))

    score = agent.full_analysis("삼성전자", "005930")

    assert score.current_price == 10000
    assert score.live_current_price == 0
    assert "price_snapshot" not in score.analysis_packet


def test_full_analysis_adds_price_snapshot_context_without_recalculating_indicators(monkeypatch):
    agent = _agent()
    monkeypatch.setattr(agent, "quick_check", lambda _stock_code: _quick_check(price=10000))

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

    assert score.current_price == 10000
    assert score.live_current_price == 11200
    assert score.live_vs_daily_close_pct == 12.0
    assert score.price_snapshot_source == "kis"
    assert score.price_snapshot_at == "2026-06-02T10:15:00+09:00"
    assert score.overheat_risk == "high"
    assert score.entry_timing == "wait_for_pullback"
    assert score.technical_invalid_price == "9,500원"

    packet = score.analysis_packet
    assert packet["price_snapshot"]["current_price"] == 11200
    assert packet["price_snapshot"]["live_vs_daily_close_pct"] == 12.0
    assert any("현재가 괴리" in risk for risk in packet["risks"])
