from __future__ import annotations

import copy
import json
import socket
import sys

import pytest
from pydantic import ValidationError

from src.tracing.paper_performance import compare_performance

DATES = ["2026-09-01T06:30:00Z", "2026-09-02T06:30:00Z", "2026-09-03T06:30:00Z"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("No network is allowed"))


def observed_run(equity=(100.0, 80.0, 110.0)):
    return {"universe": ["005930", "000660"], "currency": "KRW",
            "period": {"start": DATES[0], "end": DATES[-1]},
            "cost_assumptions": {"fee_bps": 10.0, "slippage_bps": 20.0},
            "equity_basis": "net_of_fees_and_slippage", "cash_flows": None,
            "equity": [{"timestamp": at, "net_equity": value, "positions": positions}
                       for at, value, positions in zip(DATES, equity, [
                           [{"stock_code": "005930", "sector": "Technology", "market_value": 50.0}],
                           [{"stock_code": "005930", "sector": "Technology", "market_value": 40.0},
                            {"stock_code": "000660", "sector": "Industrials", "market_value": 20.0}], []])],
            "fills": [{"fill_id": "buy-1", "timestamp": DATES[0], "stock_code": "005930",
                       "side": "BUY", "notional": 50.0, "fees": 1.0},
                      {"fill_id": "sell-1", "timestamp": DATES[-1], "stock_code": "005930",
                       "side": "SELL", "notional": 60.0, "fees": 1.0}]}


def observations():
    return {"strategy": observed_run(), "numerical_baseline": observed_run((100.0, 100.0, 105.0)),
            "buy_and_hold": observed_run((100.0, 90.0, 108.0))}


def test_net_returns_drawdown_exposure_and_one_way_turnover_from_observations():
    report = compare_performance(observations())
    metrics = report["runs"]["strategy"]
    assert metrics["net_return_pct"] == pytest.approx(10.0)
    assert metrics["max_drawdown_pct"] == pytest.approx(-20.0)
    assert metrics["traded_notional"] == 110.0
    assert metrics["one_way_turnover"] == pytest.approx(110.0 / ((100 + 80 + 110) / 3))
    assert metrics["mean_market_exposure_pct"] == pytest.approx((50 + 75 + 0) / 3)
    assert metrics["mean_sector_exposure_pct"] == pytest.approx({"Technology": 100 / 3, "Industrials": 25 / 3})
    assert metrics["fees_paid"] == 2.0
    assert report["excess_net_return_percentage_points"] == pytest.approx({"numerical_baseline": 5, "buy_and_hold": 2})


def test_recorded_fees_are_not_subtracted_from_net_equity_twice():
    payload = observations()
    before = compare_performance(payload)["runs"]["strategy"]
    payload["strategy"]["fills"][0]["fees"] = 10.0
    after = compare_performance(payload)["runs"]["strategy"]
    assert after["fees_paid"] == 11.0
    assert after["net_return_pct"] == before["net_return_pct"]
    assert after["max_drawdown_pct"] == before["max_drawdown_pct"]


@pytest.mark.parametrize("change", [
    lambda run: run["universe"].append("035420"),
    lambda run: run.update(currency="USD"),
    lambda run: run["cost_assumptions"].update(fee_bps=15.0),
    lambda run: run["cost_assumptions"].update(slippage_bps=25.0),
    lambda run: run["equity"][1].update(timestamp="2026-09-02T06:31:00Z"),
    lambda run: run["period"].update(end="2026-09-04T06:30:00Z"),
])
def test_noncomparable_runs_are_rejected(change):
    payload = observations()
    change(payload["numerical_baseline"])
    with pytest.raises(ValidationError):
        compare_performance(payload)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "100"])
def test_equity_requires_finite_positive_json_numbers(value):
    payload = observations()
    payload["strategy"]["equity"][1]["net_equity"] = value
    with pytest.raises(ValidationError):
        compare_performance(payload)


@pytest.mark.parametrize("change", [
    lambda run: run["equity"][1].update(timestamp="2026-09-02T06:30:00"),
    lambda run: run["equity"][1].update(timestamp=1788328800),
    lambda run: run["equity"][1].update(timestamp=DATES[0]),
    lambda run: run["equity"].reverse(),
    lambda run: run.update(cash_flows=[]),
    lambda run: run.update(cash_flows=[{"amount": 100.0, "timestamp": DATES[1]}]),
    lambda run: run.update(equity_basis="gross"),
    lambda run: run["fills"].append(copy.deepcopy(run["fills"][0])),
    lambda run: run["fills"][0].update(notional=-10.0),
    lambda run: run["fills"][0].update(fees=float("nan")),
    lambda run: run["fills"][0].update(timestamp="2026-08-31T06:30:00Z"),
    lambda run: run["equity"][0]["positions"].append(copy.deepcopy(run["equity"][0]["positions"][0])),
    lambda run: run["equity"][0]["positions"][0].update(stock_code="999999"),
    lambda run: run["equity"][0]["positions"][0].update(market_value=-1.0),
    lambda run: run["equity"][0]["positions"][0].update(sector=""),
    lambda run: run["equity"][0]["positions"][0].update(sector="Different taxonomy"),
])
def test_invalid_observations_and_cash_flows_fail_explicitly(change):
    payload = observations()
    change(payload["strategy"])
    with pytest.raises(ValidationError):
        compare_performance(payload)


def test_equivalent_timezone_and_universe_order_are_comparable():
    payload = observations()
    payload["numerical_baseline"]["universe"].reverse()
    payload["numerical_baseline"]["equity"][1]["timestamp"] = "2026-09-02T15:30:00+09:00"
    assert compare_performance(payload)["runs"]["strategy"]["fill_count"] == 2


def test_cli_reads_observations_and_prints_json_without_network(tmp_path, monkeypatch, capsys):
    from backtesting.__main__ import main
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(observations()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["backtesting", "paper-performance", "--input", str(path)])
    main()
    assert json.loads(capsys.readouterr().out)["runs"]["strategy"]["net_return_pct"] == pytest.approx(10.0)


def test_no_fills_or_positions_reports_observed_zero_not_a_fabricated_benchmark():
    payload = observations()
    for run in payload.values():
        run["fills"] = []
        for point in run["equity"]:
            point["positions"] = []
    metrics = compare_performance(payload)["runs"]["strategy"]
    assert metrics["one_way_turnover"] == metrics["fees_paid"] == metrics["mean_market_exposure_pct"] == 0
    assert metrics["mean_sector_exposure_pct"] == {}
    del payload["buy_and_hold"]
    with pytest.raises(ValidationError):
        compare_performance(payload)
