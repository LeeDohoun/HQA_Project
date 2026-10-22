from __future__ import annotations

import pytest


def test_python_runtime_refuses_real_kis_orders_before_any_request(monkeypatch):
    from src.tools import realtime_tool

    monkeypatch.setattr(realtime_tool, "call_api", lambda *a, **k: pytest.fail("no KIS request may be sent"))
    with pytest.raises(PermissionError, match="REAL"):
        realtime_tool.place_domestic_stock_order("005930", "BUY", 1, paper=False)
    tool = realtime_tool.KISRealtimeTool(paper=False)
    with pytest.raises(PermissionError, match="REAL"):
        tool.place_order("005930", "SELL", 1)
