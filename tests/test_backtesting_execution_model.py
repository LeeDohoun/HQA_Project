from __future__ import annotations

import json

import pytest

pd = pytest.importorskip("pandas")

from backtesting.leader_backtest import (  # noqa: E402
    ExitConfig, _count_recent_docs, _features_for_stock, _market_dates, _score_universe, _simulate_exit,
)


def frame(code, closes, *, start="2025-01-01", volumes=None, opens=None, lows=None, highs=None, dates=None):
    index = pd.DatetimeIndex(dates) if dates is not None else pd.bdate_range(start, periods=len(closes))
    volumes = volumes or [1000.0] * len(closes)
    df = pd.DataFrame({
        "Open": opens or closes, "High": highs or [value * 1.01 for value in closes],
        "Low": lows or [value * 0.99 for value in closes], "Close": closes, "Volume": volumes,
    }, index=index)
    df["YMD"] = df.index.strftime("%Y%m%d")
    df["stock_code"], df["stock_name"] = code, code
    return df


def features(df, as_of, calendar, hold=5, exit_config=None):
    return _features_for_stock(market_dates=calendar, code=df["stock_code"].iloc[0], name="x", df=df,
                               as_of_ymd=as_of, doc_index={}, hold_days=hold, min_history_days=5,
                               exit_config=exit_config or ExitConfig())


def test_same_day_documents_are_not_known_at_the_close(tmp_path):
    from backtesting import TemporalEvidence

    rows = [{"text": title, "metadata": {"source_type": "dart", "stock_code": "005930", "title": title,
                                         "published_at": published}}
            for title, published in (("before", "2025-03-09"), ("same day", "2025-03-10T18:30:00"))]
    path = tmp_path / "canonical_index/ai/corpus.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    kept = TemporalEvidence(data_dir=str(tmp_path), theme_key="ai").filter_records("20250310")
    assert [row["metadata"]["title"] for row in kept] == ["before"]
    assert _count_recent_docs({"dart": ["20250309", "20250310"]}, "20250310") == {"dart": 1}


def test_stock_whose_data_stops_stays_in_the_pool_and_exits_at_its_last_bar():
    market = frame("B", [100.0 + i for i in range(20)])
    delisted = frame("A", [50.0 + i for i in range(13)])  # data ends 2 sessions after entry
    calendar = _market_dates({"A": delisted, "B": market})
    as_of = delisted["YMD"].iloc[10]
    row = features(delisted, as_of, calendar)
    assert row["eligible"] is True
    assert row["exit_reason"] == "stock_data_ended"
    assert row["exit_price"] == delisted["Close"].iloc[-1]
    assert row["planned_exit_date"] == market.index[15].strftime("%Y-%m-%d")
    scored = _score_universe(as_of_ymd=as_of, target_by_code={"A": type("T", (), {"stock_name": "A"})(),
                                                              "B": type("T", (), {"stock_name": "B"})()},
                             prices={"A": delisted, "B": market}, doc_index={}, hold_days=5, min_history_days=5)
    assert {row["stock_code"] for row in scored if row["eligible"]} == {"A", "B"}


def test_end_of_all_data_is_still_unevaluable():
    market = frame("B", [100.0 + i for i in range(12)])
    calendar = _market_dates({"B": market})
    assert features(market, market["YMD"].iloc[9], calendar)["reason"] == "insufficient_future"


def test_missing_or_halted_bar_on_the_rebalance_date_is_not_an_entry():
    market = frame("B", [100.0 + i for i in range(20)])
    gap_dates = list(market.index[:10]) + list(market.index[11:])
    gapped = frame("A", [50.0 + i for i in range(19)], dates=gap_dates)
    calendar = _market_dates({"A": gapped, "B": market})
    assert features(gapped, market["YMD"].iloc[10], calendar)["reason"] == "no_bar_on_rebalance_date"
    halted = frame("A", [50.0] * 20, volumes=[1000.0] * 10 + [0.0] + [1000.0] * 9)
    assert features(halted, halted["YMD"].iloc[10], _market_dates({"A": halted}))["reason"] == "not_traded_on_rebalance_date"


def test_halt_bars_do_not_fill_stops_and_a_halted_exit_waits_for_trading():
    closes = [100.0] * 20
    lows = [99.0] * 20
    volumes = [1000.0] * 20
    lows[12], volumes[12] = 50.0, 0.0      # placeholder bar during the hold
    volumes[15] = 0.0                     # halted at the planned exit
    df = frame("A", closes, lows=lows, volumes=volumes)
    result = _simulate_exit(df=df, entry_pos=10, planned_exit_pos=15, entry_price=100.0,
                            exit_config=ExitConfig(stop_loss_pct=10.0))
    assert result["exit_reason"] == "exit_delayed_by_halt" and result["exit_pos"] == 16


def test_gaps_through_stop_and_target_fill_at_the_open():
    closes = [100.0] * 20
    opens, lows, highs = closes[:], [99.0] * 20, [101.0] * 20
    opens[12], lows[12], highs[12] = 80.0, 78.0, 82.0
    down = _simulate_exit(df=frame("A", closes, opens=opens, lows=lows, highs=highs), entry_pos=10,
                          planned_exit_pos=15, entry_price=100.0, exit_config=ExitConfig(stop_loss_pct=10.0))
    assert down["exit_reason"] == "stop_loss" and down["exit_price"] == 80.0
    opens[12], lows[12], highs[12] = 125.0, 124.0, 126.0
    up = _simulate_exit(df=frame("A", closes, opens=opens, lows=lows, highs=highs), entry_pos=10,
                        planned_exit_pos=15, entry_price=100.0, exit_config=ExitConfig(take_profit_pct=20.0))
    assert up["exit_reason"] == "take_profit" and up["exit_price"] == 125.0


def test_price_basis_breaks_exclude_the_period():
    split_before = frame("A", [200.0] * 8 + [40.0] * 12)           # 5:1 split before entry
    calendar = _market_dates({"A": split_before})
    assert features(split_before, split_before["YMD"].iloc[12], calendar)["reason"] == "price_basis_break_in_features"
    split_during = frame("A", [100.0] * 12 + [50.0] * 8)            # bonus issue during the hold
    calendar = _market_dates({"A": split_during})
    assert features(split_during, split_during["YMD"].iloc[10], calendar)["reason"] == "price_basis_break_in_holding"
    limit_up = frame("A", [100.0] * 11 + [129.0] * 9)               # a +29% limit move is a real return
    calendar = _market_dates({"A": limit_up})
    assert features(limit_up, limit_up["YMD"].iloc[10], calendar)["eligible"] is True


def _write_chart(tmp_path, bars):
    path = tmp_path / "market_data/theme/chart.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"stock_code": code, "stock_name": code, "timestamp": day, "open": o,
                                        "high": h, "low": l, "close": c, "volume": v}) + "\n"
                            for code, day, o, h, l, c, v in bars), encoding="utf-8")


def _halt_case(tmp_path, resume_close, *, omit_halt_rows=False):
    from backtesting.leader_backtest import load_price_history

    days = [day.strftime("%Y-%m-%d") for day in pd.bdate_range("2025-08-01", periods=20)]
    bars = [("B", day, 100, 101, 99, 100, 1000) for day in days]
    for index, day in enumerate(days):
        if 11 <= index <= 14:  # halted across the planned exit (entry index 10 + 3 sessions)
            if not omit_halt_rows:
                bars.append(("A", day, "0", "0", "0", 50, "0"))  # how Naver/KRX report a halted session
        else:
            close = 50 if index < 15 else resume_close
            bars.append(("A", day, close, close, close, close, 1000))
    _write_chart(tmp_path, bars)
    prices = load_price_history(tmp_path, "theme")
    return features(prices["A"], prices["A"]["YMD"].iloc[10], _market_dates(prices), hold=3), days


def test_a_halt_stored_as_zero_prices_delays_the_exit_instead_of_ending_the_stock(tmp_path):
    row, days = _halt_case(tmp_path, 55)
    assert row["eligible"] is True and row["exit_reason"] == "exit_delayed_by_halt"
    assert row["exit_date"] == days[15] and row["exit_price"] == 55


def test_a_consolidation_during_a_halt_excludes_the_period(tmp_path):
    row, _ = _halt_case(tmp_path, 325)  # 5:1 consolidation while halted
    assert row["eligible"] is False and row["reason"] == "price_basis_break_in_holding"


def test_missing_rows_across_the_planned_exit_are_a_halt_when_trading_resumes(tmp_path):
    row, days = _halt_case(tmp_path, 55, omit_halt_rows=True)
    assert row["exit_reason"] == "exit_delayed_by_halt" and row["exit_date"] == days[15]


def test_an_open_beyond_the_target_fills_the_take_profit_before_the_stop():
    df = frame("A", [100.0, 100.0, 118.0, 118.0], opens=[100.0, 100.0, 121.0, 118.0],
               highs=[100.0, 115.0, 123.0, 118.0], lows=[100.0, 99.0, 111.0, 118.0])
    result = _simulate_exit(df=df, entry_pos=0, planned_exit_pos=3, entry_price=100.0,
                            exit_config=ExitConfig(take_profit_pct=20.0, trailing_stop_pct=2.5))
    assert (result["exit_reason"], result["exit_price"]) == ("take_profit", 121.0)


def test_an_llm_score_of_zero_is_a_score_not_a_missing_value():
    from backtesting.leader_backtest import _rerank_with_llm

    class Scorer:
        def score(self, *, as_of_ymd, row):
            return {"llm_score": {"A": 0, "B": 40}[row["stock_code"]], "llm_confidence": 80}

    ranked = [{"stock_code": "A", "leader_score": 90}, {"stock_code": "B", "leader_score": 60}]
    result = _rerank_with_llm(ranked=ranked, as_of_ymd="20250310", llm_scorer=Scorer(), top_n=2,
                              llm_rerank_top_k=2, llm_weight=1.0, warnings=[])
    assert [(row["stock_code"], row["leader_score"]) for row in result] == [("B", 40), ("A", 0)]


def test_a_membership_inferred_from_documents_starts_after_its_first_document_day():
    from src.ingestion.theme_membership import ThemeMembership, active_membership_codes

    rows = [ThemeMembership("ai", "A", "000001", "2025-03-10", source="local_corpus_inferred"),
            ThemeMembership("ai", "B", "000002", "2025-03-10", source="combined_local_corpus_inferred"),
            ThemeMembership("ai", "C", "000003", "2025-03-10", source="official")]
    assert active_membership_codes(rows, "2025-03-10") == {"000003"}  # same-day documents are not known
    assert active_membership_codes(rows, "2025-03-11") == {"000001", "000002", "000003"}
