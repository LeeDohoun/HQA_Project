from dataclasses import FrozenInstanceError, fields
import subprocess
import sys

import pandas as pd
import pytest

from src.strategies import PointInTimeData, Strategy, StrategySpec, TargetPosition, _runner_module
from src.strategies.registry import STRATEGIES


def prices(*rows):
    records = []
    for day, code, changes in rows:
        row = {"trade_date": pd.Timestamp(day), "stock_code": code, "close": 2000,
               "calendar_status": "verified", "bar_at": f"{day}T15:30:00+09:00", **changes}
        records.append(row)
    return pd.DataFrame(records, columns=["trade_date", "stock_code", "close", "calendar_status", "bar_at",
                                         "collected_at", "available_at"]).set_index(["trade_date", "stock_code"])


def event(receipt="E", **changes):
    return {"event_id": receipt, "stock_code": "000001", "rcept_dt": "2024-01-05",
            "category": "contract", "revenue_ratio_pct": 10.0, **changes}


def test_spec_is_frozen_and_exposes_contract_fields():
    spec = STRATEGIES["d_momentum_lowvol"].spec
    assert {field.name for field in fields(spec)} == {
        "strategy_id", "sleeve", "thesis", "counterparty", "universe", "inputs", "decision_time",
        "horizon_sessions", "capacity_krw", "stage", "preregistration",
    }
    assert isinstance(spec, StrategySpec)
    assert spec.capacity_krw is None and spec.preregistration is None
    with pytest.raises(FrozenInstanceError):
        spec.stage = "paper"


@pytest.mark.parametrize("strategy_id", list(STRATEGIES))
def test_registered_candidates_implement_protocol(strategy_id):
    strategy = STRATEGIES[strategy_id]
    assert isinstance(strategy, Strategy)
    assert strategy.spec.strategy_id == strategy_id
    assert strategy.spec.stage == "candidate"


def test_price_buffer_future_rows_and_historical_backfill():
    source = prices(("2024-01-04", "000001", {}),
                    ("2024-01-05", "000001", {"collected_at": "2026-10-01T00:00:00+09:00",
                                               "available_at": "2026-10-01T00:00:00+09:00"}),
                    ("2024-01-08", "000001", {}))
    data = PointInTimeData(source, pd.DataFrame())
    before = data.prices_as_of("2024-01-05T15:59:59+09:00", None)
    at = data.prices_as_of("2024-01-05T16:00:00+09:00", None)
    assert list(before.index.get_level_values("trade_date")) == [pd.Timestamp("2024-01-04")]
    assert list(at.index.get_level_values("trade_date")) == list(pd.to_datetime(["2024-01-04", "2024-01-05"]))
    assert (at.available_at <= pd.Timestamp("2024-01-05T16:00:00+09:00")).all()
    assert source.loc[(pd.Timestamp("2024-01-05"), "000001"), "available_at"].startswith("2026")


@pytest.mark.parametrize("status,bar,before,at", [
    ("unverified_special_session", None, "16:29:59", "16:30:00"),
    ("unverified_special_session", "16:30:00", "16:59:59", "17:00:00"),
    ("verified", "16:30:00", "16:59:59", "17:00:00"),
])
def test_unverified_bound_and_special_bar_close(status, bar, before, at):
    source = prices(("2024-11-14", "000001", {"calendar_status": status,
                                             "bar_at": f"2024-11-14T{bar}+09:00" if bar else None}))
    data = PointInTimeData(source, pd.DataFrame())
    assert data.prices_as_of(f"2024-11-14T{before}+09:00", 1).empty
    assert len(data.prices_as_of(f"2024-11-14T{at}+09:00", 1)) == 1


def test_verified_price_without_bar_uses_existing_special_calendar():
    data = PointInTimeData(prices(("2024-11-14", "000001", {"bar_at": None})), pd.DataFrame())
    assert data.prices_as_of("2024-11-14T16:59:59+09:00", 1).empty
    assert len(data.prices_as_of("2024-11-14T17:00:00+09:00", 1)) == 1


def test_unique_key_availability_matches_previous_row_logic(monkeypatch):
    source = prices(*[
        (day, f"{code:06d}", {"calendar_status": status, "bar_at": bar})
        for day in ("2024-01-05", "2024-11-14")
        for code, (status, clock) in enumerate([
            ("verified", None), ("verified", None),
            ("verified", "15:30:00+09:00"), ("verified", "15:30:00+09:00"),
            ("unverified_special_session", None), (None, None),
            ("unverified_special_session", "15:30:00+09:00"),
            ("unverified_special_session", "16:30:00+09:00"),
            ("verified", "07:30:00+00:00"),
        ], 1)
        for bar in [f"{day}T{clock}" if clock else None]
    ]).iloc[::-1]
    expected = []
    calendar = _runner_module("trading_calendar")
    for row in source.sort_index().reset_index().to_dict("records"):
        day = row["trade_date"]
        verified = row.get("calendar_status") == "verified"
        bound = day.tz_localize("Asia/Seoul") + pd.Timedelta(hours=16, minutes=30)
        if pd.notna(row.get("bar_at")):
            known = pd.Timestamp(row["bar_at"]).tz_convert("Asia/Seoul") + pd.Timedelta(minutes=30)
        elif verified:
            known = pd.Timestamp(calendar.daily_session_close(day.date().isoformat())).tz_convert("Asia/Seoul")
            known += pd.Timedelta(minutes=30)
        else:
            known = bound
        expected.append(known if verified else max(known, bound))
    import src.strategies.data as module
    original = module._price_availability
    calls = []

    def counted(*key):
        calls.append(key)
        return original(*key)

    monkeypatch.setattr(module, "_price_availability", counted)
    data = PointInTimeData(source, pd.DataFrame())
    actual = data.prices_as_of("2024-11-15T09:00:00+09:00", None).available_at
    pd.testing.assert_series_equal(actual, pd.Series(expected, index=source.sort_index().index,
                                                     name="available_at", dtype="datetime64[ns, Asia/Seoul]"))
    assert len(calls) == 14 < len(source)


def test_date_only_disclosure_uses_next_exchange_open_including_holidays():
    data = PointInTimeData(prices(), pd.DataFrame([event(rcept_dt="2024-02-08")]))
    assert data.events_as_of("2024-02-13T08:59:59+09:00", 30).empty
    known = data.events_as_of("2024-02-13T09:00:00+09:00", 30)
    assert known.available_at.iloc[0] == pd.Timestamp("2024-02-13T09:00:00+09:00")


def test_first_seen_is_used_and_future_correction_never_appears():
    source = pd.DataFrame([event(first_seen_at="2024-01-05T11:00:00+09:00"),
                           event("correction", first_seen_at="2024-01-05T16:35:00+09:00", revenue_ratio_pct=99)])
    data = PointInTimeData(prices(), source)
    assert data.events_as_of("2024-01-05T10:59:59+09:00", None).empty
    assert data.events_as_of("2024-01-05T16:34:59+09:00", None).event_id.tolist() == ["E"]
    assert data.events_as_of("2024-01-05T16:35:00+09:00", None).event_id.tolist() == ["E", "correction"]


def test_future_disclosure_cannot_be_backdated_by_first_seen():
    with pytest.raises(ValueError, match="precede"):
        PointInTimeData(prices(), pd.DataFrame([
            event(rcept_dt="2024-01-08", first_seen_at="2024-01-05T11:00:00+09:00"),
        ]))


def test_event_study_date_and_timed_input_forms_are_supported():
    data = PointInTimeData(prices(), pd.DataFrame([
        {"event_id": "dated", "stock_code": "000001", "available_at": "2024-01-05"},
        {"event_id": "timed", "stock_code": "000001", "available_at": "2024-01-08T08:00:00+09:00"},
    ]))
    assert data.events_as_of("2024-01-08T08:00:00+09:00", None).event_id.tolist() == ["timed"]
    assert data.events_as_of("2024-01-08T09:00:00+09:00", None).event_id.tolist() == ["timed", "dated"]


def test_shared_session_lookback_and_availability_day_lookback():
    data = PointInTimeData(prices(("2024-01-03", "000001", {}), ("2024-01-04", "000002", {}),
                                 ("2024-01-05", "000002", {})), pd.DataFrame([
        event("old", rcept_dt="2024-01-03", first_seen_at="2024-01-03T16:00:00+09:00"),
        event("caught_up", rcept_dt="2023-12-01", first_seen_at="2024-01-05T16:00:00+09:00"),
    ]))
    recent = data.prices_as_of("2024-01-05T16:30:00+09:00", 2)
    assert recent.index.get_level_values("stock_code").tolist() == ["000002", "000002"]
    assert data.events_as_of("2024-01-05T16:30:00+09:00", 1).event_id.tolist() == ["caught_up"]


def test_constructor_and_returned_frames_cannot_mutate_stored_data():
    source = prices(("2024-01-05", "000001", {}), ("2024-01-08", "000001", {}))
    events = pd.DataFrame([event(first_seen_at="2024-01-05T11:00:00+09:00")])
    data = PointInTimeData(source, events)
    source.loc[:, "close"] = 9
    events.loc[:, "revenue_ratio_pct"] = 999
    returned = data.prices_as_of("2024-01-05T16:30:00+09:00", None)
    returned.loc[:, "close"] = 1
    returned_events = data.events_as_of("2024-01-05T16:30:00+09:00", None)
    returned_events.loc[:, "revenue_ratio_pct"] = 0
    assert data.prices_as_of("2024-01-05T16:30:00+09:00", None).close.tolist() == [2000]
    assert data.events_as_of("2024-01-05T16:30:00+09:00", None).revenue_ratio_pct.tolist() == [10.0]
    assert not hasattr(data, "prices") and not hasattr(data, "events")


@pytest.mark.parametrize("method,argument", [("prices_as_of", 0), ("events_as_of", -1)])
def test_invalid_lookbacks_fail(method, argument):
    data = PointInTimeData(prices(), pd.DataFrame())
    with pytest.raises(ValueError, match="lookback"):
        getattr(data, method)("2024-01-05T16:30:00+09:00", argument)


def test_naive_decisions_invalid_bars_and_duplicate_keys_fail():
    data = PointInTimeData(prices(), pd.DataFrame())
    with pytest.raises(ValueError, match="timezone-aware"):
        data.prices_as_of("2024-01-05T16:30:00", 1)
    with pytest.raises(ValueError, match="trade_date"):
        PointInTimeData(prices(("2024-01-08", "000001", {"bar_at": "2024-01-05T15:30:00+09:00"})), pd.DataFrame())
    with pytest.raises(ValueError, match="unique"):
        PointInTimeData(prices(("2024-01-05", "000001", {}), ("2024-01-05", "000001", {})), pd.DataFrame())


def test_registry_scores_and_targets_are_deterministic_and_latest_not_maximum():
    events = pd.DataFrame([
        event("older", first_seen_at="2024-01-05T10:00:00+09:00", revenue_ratio_pct=100),
        event("latest", first_seen_at="2024-01-05T11:00:00+09:00", revenue_ratio_pct=10),
        event("tie", first_seen_at="2024-01-05T11:00:00+09:00", stock_code="000002"),
        event("future", first_seen_at="2024-01-08T10:00:00+09:00", revenue_ratio_pct=999),
        event("routine", first_seen_at="2024-01-05T11:00:00+09:00", category="other", stock_code="000003"),
    ])
    a, b = PointInTimeData(prices(), events), PointInTimeData(prices(), events.iloc[::-1])
    cutoff = "2024-01-05T16:30:00+09:00"
    strategy = STRATEGIES["b2_contract_ratio"]
    targets = strategy.generate(cutoff, a, 2)
    assert targets == strategy.generate(cutoff, b, 2)
    assert all(isinstance(target, TargetPosition) and target.weight is None and target.reason for target in targets)
    assert [target.stock_code for target in targets] == ["000001", "000002"]
    assert [target.score for target in targets] == [10, 10]
    b1 = STRATEGIES["b1_disclosure_category"]
    assert b1.score(cutoff, a).score.tolist() == [1.0, 1.0, 0.0]
    assert [target.stock_code for target in b1.generate(cutoff, a, 10)] == ["000001", "000002"]
    assert strategy.generate(cutoff, a, 0) == []
    with pytest.raises(ValueError, match="top_n"):
        strategy.generate(cutoff, a, -1)


def test_newer_unparsed_contract_does_not_resurrect_old_feature():
    data = PointInTimeData(prices(), pd.DataFrame([
        event("first", first_seen_at="2024-01-05T10:00:00+09:00"),
        event("corrected", first_seen_at="2024-01-05T11:00:00+09:00", revenue_ratio_pct=float("nan")),
    ]))
    assert STRATEGIES["b2_contract_ratio"].score("2024-01-05T16:30:00+09:00", data).empty


def test_strategy_import_and_leaf_reuse_never_import_live_runners():
    code = """
import sys
from src.strategies import _runner_module
from src.strategies.registry import STRATEGIES
assert callable(_runner_module('event_evidence')._category)
assert callable(_runner_module('trading_calendar').daily_session_close)
assert 'src.runner' not in sys.modules
assert 'src.agents.llm_config' not in sys.modules
assert 'src.utils.kis_auth' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
