import numpy as np
import pandas as pd
import pytest

from src.ingestion.storage import write_rows
from src.research.industry_index import ALL_MARKET, build_indices, industry_features, load_indices


def prices(days=3, codes=("000010", "000020"), rates=(0.1, -0.1), caps=(100, 300), industries=None):
    dates = pd.bdate_range("2024-01-02", periods=days)
    index = pd.MultiIndex.from_product([dates, codes], names=["trade_date", "stock_code"])
    return pd.DataFrame({"stock_name": "Example", "industry": np.tile(industries or ["반도체"] * len(codes), days),
                         "close": 100.0, "market_cap": np.tile(np.array(caps, dtype=float), days),
                         "ret_1d": np.tile(np.array(rates, dtype=float), days),
                         "trading_value": 200.0}, index=index)


def test_cap_and_equal_arithmetic_use_previous_session_caps():
    frame = prices()
    dates = frame.index.get_level_values("trade_date").unique()
    frame.loc[(dates[1], "000010"), "market_cap"] = 900
    frame.loc[(dates[1], "000020"), "market_cap"] = 100
    frame.loc[(dates[2], "000010"), "ret_1d"] = -0.2
    frame.loc[(dates[2], "000020"), "ret_1d"] = 0.4
    original = frame.copy()
    industry = build_indices(frame).xs("반도체", level="industry")
    assert industry.cap_return.iloc[1:].tolist() == pytest.approx([-0.05, -0.14])
    assert industry.equal_return.iloc[1:].tolist() == pytest.approx([0, 0.1])
    assert industry.cap_index.tolist() == pytest.approx([100, 95, 81.7])
    assert industry.equal_index.tolist() == pytest.approx([100, 100, 110])
    assert industry.member_count.tolist() == [0, 2, 2]
    pd.testing.assert_frame_equal(frame, original)


def test_changing_same_day_caps_cannot_change_that_days_index():
    frame = prices()
    date = frame.index.get_level_values("trade_date").unique()[1]
    baseline = build_indices(frame)
    frame.loc[(date, "000010"), "market_cap"] = 1e12
    changed = build_indices(frame)
    pd.testing.assert_frame_equal(baseline.loc[:date], changed.loc[:date])
    assert baseline.cap_return.iloc[-1] != changed.cap_return.iloc[-1]


def test_split_day_chains_exchange_returns_instead_of_raw_closes():
    frame = prices(rates=(0, 0), caps=(100, 100))
    dates = frame.index.get_level_values("trade_date").unique()
    frame.loc[(dates[1], "000010"), "close"] = 50
    frame.loc[(dates[2], "000010"), ["close", "ret_1d"]] = [55, 0.1]
    result = build_indices(frame).xs("반도체", level="industry")
    assert result.cap_index.tolist() == result.equal_index.tolist() == pytest.approx([100, 100, 105])


@pytest.mark.parametrize("spac_name", ["Example SPAC", "한국제1호스팩", "Example 기업 인수 목적"])
def test_common_shares_spac_and_alphanumeric_codes(spac_name):
    codes = ("0015G0", "000010", "000011", "00088K")
    frame = prices(codes=codes, rates=(0.1, 100, 100, 100), caps=(100, 100, 100, 100))
    frame.loc[(slice(None), "000010"), "stock_name"] = spac_name
    result = build_indices(frame).xs(ALL_MARKET, level="industry")
    assert result.member_count.iloc[1:].tolist() == [1, 1]
    assert result.cap_return.iloc[1:].tolist() == pytest.approx([0.1, 0.1])


def test_new_listing_and_spac_rename_use_prior_session_membership():
    frame = prices(days=4, caps=(100, 100), rates=(0.1, 0))
    dates = frame.index.get_level_values("trade_date").unique()
    frame = frame.drop((dates[0], "000020"))
    frame.loc[(dates[1], "000010"), "stock_name"] = "Example SPAC"
    result = build_indices(frame).xs("반도체", level="industry")
    assert result.member_count.tolist() == [0, 1, 1, 2]
    assert result.cap_return.iloc[1:].tolist() == pytest.approx([0.1, 0, 0.05])


def test_industry_change_uses_prior_session_label():
    frame = prices(codes=("000010",), caps=(100,), rates=(0.1,))
    dates = frame.index.get_level_values("trade_date").unique()
    frame.loc[(dates[1], "000010"), "industry"] = "금융"
    result = build_indices(frame)
    assert result.loc[(dates[1], "반도체"), "cap_return"] == pytest.approx(0.1)
    assert result.loc[(dates[1], "금융"), "member_count"] == 0
    assert result.loc[(dates[2], "금융"), "member_count"] == 1


def test_liquidity_needs_full_20_sessions_and_cannot_use_same_day_spike():
    frame = prices(days=23, caps=(100, 100), rates=(0.1, 0.02))
    dates = frame.index.get_level_values("trade_date").unique()
    frame.loc[(slice(None), "000010"), "trading_value"] = 90
    frame.loc[(dates[20], "000010"), "trading_value"] = 1e8
    result = build_indices(frame, min_trading_value_20d=100).xs("반도체", level="industry")
    assert result.member_count.iloc[:20].eq(0).all()
    assert result.member_count.iloc[20] == 1 and result.cap_return.iloc[20] == pytest.approx(0.02)
    assert result.member_count.iloc[21] == 2 and result.cap_return.iloc[21] == pytest.approx(0.06)
    assert result.cap_index.iloc[19] == 100


def test_missing_liquidity_observation_is_not_filled():
    frame = prices(days=23, caps=(100, 100), rates=(0.1, 0.02))
    date = frame.index.get_level_values("trade_date").unique()[10]
    frame.loc[(date, "000010"), "trading_value"] = np.nan
    result = build_indices(frame, min_trading_value_20d=100).xs("반도체", level="industry")
    assert result.member_count.iloc[20:].tolist() == [1, 1, 1]


@pytest.mark.parametrize("reappears", [False, True])
def test_delisted_member_contributes_zero_then_leaves_next_days_weights(reappears):
    frame = prices(days=5)
    dates = frame.index.get_level_values("trade_date").unique()
    missing_dates = dates[2:3] if reappears else dates[2:]
    frame = frame.drop([(date, "000010") for date in missing_dates])
    result = build_indices(frame)
    industry = result.xs("반도체", level="industry")
    assert industry.member_count.tolist() == [0, 2, 2, 1, 1]
    assert industry.cap_return.iloc[1:].tolist() == pytest.approx([-0.05, -0.075, -0.1, -0.1])
    assert industry.equal_return.iloc[1:].tolist() == pytest.approx([0, -0.05, -0.1, -0.1])
    assert result.attrs["members_dropped_no_row"] == 1
    assert result.attrs["raw_fallback"] == 0


def test_zero_volume_halt_keeps_stored_return_and_membership():
    frame = prices(days=4)
    dates = frame.index.get_level_values("trade_date").unique()
    frame["volume"] = 100
    frame.loc[(dates[1], "000010"), ["volume", "ret_1d", "close"]] = [0, 0, 50]
    result = build_indices(frame)
    industry = result.xs("반도체", level="industry")
    assert industry.member_count.tolist() == [0, 2, 2, 2]
    assert industry.cap_return.iloc[1:].tolist() == pytest.approx([-0.075, -0.05, -0.05])
    assert result.attrs["members_dropped_no_row"] == result.attrs["raw_fallback"] == 0


def test_missing_return_uses_raw_close_fallback_and_keeps_member():
    frame = prices(days=4, caps=(100, 100))
    dates = frame.index.get_level_values("trade_date").unique()
    frame.loc[(dates[1], "000010"), ["ret_1d", "close"]] = [np.nan, 120]
    original = frame.copy()
    result = build_indices(frame)
    industry = result.xs("반도체", level="industry")
    assert industry.member_count.tolist() == [0, 2, 2, 2]
    assert industry.cap_return.iloc[1:].tolist() == pytest.approx([0.05, 0, 0])
    assert industry.equal_return.iloc[1:].tolist() == pytest.approx([0.05, 0, 0])
    assert result.attrs["raw_fallback"] == 1
    assert result.attrs["members_dropped_no_row"] == 0
    pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize("unusable_close", ["absent", "current", "previous", "zero", "infinite"])
def test_missing_return_without_usable_closes_uses_zero_then_drops(unusable_close):
    frame = prices(days=4)
    dates = frame.index.get_level_values("trade_date").unique()
    frame.loc[(dates[1], "000010"), "ret_1d"] = np.nan
    if unusable_close == "absent":
        frame = frame.drop(columns="close")
    elif unusable_close == "previous":
        frame.loc[(dates[0], "000010"), "close"] = np.nan
    else:
        frame.loc[(dates[1], "000010"), "close"] = {
            "current": np.nan, "zero": 0, "infinite": np.inf}[unusable_close]
    result = build_indices(frame)
    industry = result.xs("반도체", level="industry")
    assert industry.member_count.tolist() == [0, 2, 1, 1]
    assert industry.cap_return.iloc[1:].tolist() == pytest.approx([-0.075, -0.1, -0.1])
    assert result.attrs["members_dropped_no_row"] == 1
    assert result.attrs["raw_fallback"] == 0


def test_attrs_count_member_events_by_prior_industry_and_preserve_source_attrs():
    frame = prices(days=4, codes=("000010", "000020", "000030"), caps=(100, 100, 100),
                   rates=(0.1, 0.1, 0.1), industries=["반도체", "금융", "금융"])
    dates = frame.index.get_level_values("trade_date").unique()
    frame = frame.drop([(date, "000010") for date in dates[1:]])
    frame.loc[(dates[1], "000020"), ["ret_1d", "close"]] = [np.nan, np.nan]
    frame.loc[(dates[1], "000020"), "industry"] = "반도체"
    frame.loc[(dates[1], "000030"), ["ret_1d", "close"]] = [np.nan, 120]
    frame.loc[(dates[1], "000030"), "industry"] = "반도체"
    frame.attrs["source"] = "synthetic"
    expected = {"source": "synthetic", "members_dropped_no_row": 2,
                "members_dropped_no_row_by_industry": {"금융": 1, "반도체": 1},
                "raw_fallback": 1, "raw_fallback_by_industry": {"금융": 1, "반도체": 0}}
    assert build_indices(frame).attrs == expected
    features = industry_features(frame, (dates[-1] + pd.Timedelta(days=1)).date().isoformat())
    assert features.attrs == expected
    assert features.loc["반도체", "member_count"] == 0
    assert features.loc["금융", "member_count"] == 1


def test_first_session_missing_return_is_valid_weighting_baseline():
    frame = prices()
    date = frame.index.get_level_values("trade_date").unique()[0]
    frame.loc[date, "ret_1d"] = np.nan
    result = build_indices(frame)
    assert result.xs("반도체", level="industry").cap_index.iloc[0] == 100
    assert result.attrs["members_dropped_no_row"] == result.attrs["raw_fallback"] == 0


def feature_prices(days=131):
    return prices(days=days, codes=tuple(f"{number:05d}0" for number in range(1, 8)),
                  rates=(0.003, 0.002, 0.001, -0.001, -0.002, -0.004, 0),
                  caps=(600, 500, 400, 300, 200, 100, 700), industries=["반도체"] * 6 + ["금융"])


def test_relative_strength_breadth_top_five_and_member_count_formulas():
    frame = feature_prices()
    cutoff = frame.index.get_level_values("trade_date").max()
    result = industry_features(frame, (cutoff + pd.Timedelta(days=1)).date().isoformat())
    row = result.loc["반도체"]
    # Manual daily cap returns: numerator 2.1, industry cap 2100, all-market cap 2800.
    industry_return, market_return = 2.1 / 2100, 2.1 / 2800
    for horizon in (20, 60, 120):
        assert row[f"rs_{horizon}"] == pytest.approx(((1 + industry_return) / (1 + market_return)) ** horizon - 1)
    assert row["breadth_20"] == 0.5 and row["member_count"] == 6
    assert row["top5_return_60"] == pytest.approx(1.001 ** 60 - 1)
    assert row["as_of"] == cutoff
    assert result.loc["금융", "breadth_20"] == 0


def test_relative_strength_difference_and_explicit_ratio():
    frame = feature_prices()
    decision = (frame.index.get_level_values("trade_date").max() + pd.Timedelta(days=1)).date().isoformat()
    default = industry_features(frame, decision)
    ratio = industry_features(frame, decision, relative_strength="ratio")
    difference = industry_features(frame, decision, relative_strength="difference")
    pd.testing.assert_frame_equal(default, ratio)
    for horizon in (20, 60, 120):
        industry_growth, market_growth = 1.001 ** horizon, 1.00075 ** horizon
        assert difference.loc["반도체", f"rs_{horizon}"] == pytest.approx(industry_growth - market_growth)
        assert difference.loc["반도체", f"rs_{horizon}"] != pytest.approx(ratio.loc["반도체", f"rs_{horizon}"])
    other_columns = ["as_of", "member_count", "breadth_20", "top5_return_60"]
    pd.testing.assert_frame_equal(default[other_columns], difference[other_columns])


def test_difference_is_defined_when_market_cumulative_growth_is_zero():
    frame = prices(days=21, rates=(-1, -1), industries=["반도체", "금융"])
    decision = (frame.index.get_level_values("trade_date").max() + pd.Timedelta(days=1)).date().isoformat()
    assert industry_features(frame, decision)["rs_20"].isna().all()
    assert industry_features(frame, decision, relative_strength="difference")["rs_20"].eq(0).all()


def test_invalid_relative_strength_fails_clearly():
    with pytest.raises(ValueError, match="relative_strength"):
        industry_features(prices(), "2024-02-01", relative_strength="unknown")


def test_raw_fallback_is_used_by_breadth_and_top_five_features():
    frame = prices(days=65, rates=(0.01, -0.01), caps=(100, 100))
    dates = frame.index.get_level_values("trade_date").unique()
    decision = (dates[-1] + pd.Timedelta(days=1)).date().isoformat()
    frame["close"] = (100 * np.array([1.01, 0.99]) ** np.arange(65)[:, None]).ravel()
    expected = industry_features(frame, decision)
    frame.loc[(dates[-1], "000010"), "ret_1d"] = np.nan
    actual = industry_features(frame, decision)
    pd.testing.assert_frame_equal(expected, actual)
    assert actual.attrs["raw_fallback"] == 1
    assert actual.attrs["members_dropped_no_row"] == 0


def test_features_ignore_decision_day_and_all_future_data():
    frame = feature_prices(days=135)
    dates = frame.index.get_level_values("trade_date").unique()
    decision = dates[130]
    expected = industry_features(frame, decision.date().isoformat())
    frame.loc[decision:, "market_cap"] = 1e20
    frame.loc[decision:, "ret_1d"] = 5
    frame.loc[decision:, "industry"] = "새 업종"
    actual = industry_features(frame, decision.date().isoformat())
    pd.testing.assert_frame_equal(expected, actual)
    assert actual.as_of.eq(dates[129]).all()


def test_feature_breadth_uses_chained_prices_on_split_day():
    frame = prices(days=65, rates=(0.01, -0.01), caps=(100, 100))
    dates = frame.index.get_level_values("trade_date").unique()
    frame["close"] = np.tile([100, 100], 65) * np.repeat(np.arange(1, 66), 2)
    frame.loc[(dates[-1], "000010"), "close"] = 1
    row = industry_features(frame, (dates[-1] + pd.Timedelta(days=1)).date().isoformat()).loc["반도체"]
    assert row["breadth_20"] == 0.5
    assert row["top5_return_60"] == pytest.approx(((1.01 ** 60 - 1) + (0.99 ** 60 - 1)) / 2)


def test_top_cap_selection_uses_cutoff_caps_and_deterministic_code_ties():
    frame = feature_prices()
    cutoff = frame.index.get_level_values("trade_date").max()
    frame.loc[(cutoff, slice(None)), "market_cap"] = 100
    frame.loc[(cutoff, "000060"), "market_cap"] = 1e8
    # Top five are negative-return stock 6, then ascending codes 1, 2, 3, 4.
    result = industry_features(frame, (cutoff + pd.Timedelta(days=1)).date().isoformat())
    assert result.loc["반도체", "top5_return_60"] == pytest.approx(1.001 ** 60 - 1)
    frame.loc[(cutoff, "000010"), "market_cap"] = 1
    # Stock 5 replaces stock 1; the median is now the -0.1% daily-return stock 4.
    result = industry_features(frame, (cutoff + pd.Timedelta(days=1)).date().isoformat())
    assert result.loc["반도체", "top5_return_60"] == pytest.approx(0.999 ** 60 - 1)


@pytest.mark.parametrize("days", [10, 25])
def test_feature_warmup_is_explicit_nan(days):
    frame = prices(days=days)
    cutoff = frame.index.get_level_values("trade_date").max()
    row = industry_features(frame, (cutoff + pd.Timedelta(days=1)).date().isoformat()).loc["반도체"]
    assert row["member_count"] == 2
    assert pd.isna(row["rs_60"]) and pd.isna(row["rs_120"]) and pd.isna(row["top5_return_60"])
    assert pd.isna(row["breadth_20"]) == (days == 10)
    assert pd.isna(row["rs_20"]) == (days == 10)


def test_new_member_with_short_history_does_not_become_zero_breadth_or_partial_median():
    frame = prices(days=70, rates=(0.01, 0.02))
    dates = frame.index.get_level_values("trade_date").unique()
    frame = frame.drop([(day, "000020") for day in dates[:-5]])
    row = industry_features(frame, (dates[-1] + pd.Timedelta(days=1)).date().isoformat()).loc["반도체"]
    assert row["member_count"] == 2 and pd.isna(row["breadth_20"]) and pd.isna(row["top5_return_60"])


def test_all_market_includes_unclassified_and_local_store_loader(tmp_path):
    frame = prices(industries=["반도체", "미분류"]).drop(columns="industry")
    write_rows(tmp_path / "reference/dart_company/companies.jsonl", [{"stock_code": "000010", "induty_code": "26110"}])
    dates = frame.index.get_level_values("trade_date").unique()
    for day in dates:
        rows = [{**row.to_dict(), "stock_code": code, "trade_date": day.date().isoformat(),
                 "open": 100, "high": 100, "low": 100, "volume": 100, "market": "KOSPI",
                 "change_rate_pct": row["ret_1d"] * 100} for code, row in frame.loc[day].iterrows()]
        write_rows(tmp_path / f"market/krx_daily/{day:%Y}/{day:%Y%m%d}.jsonl", rows)
    result = load_indices(dates[0].date().isoformat(), dates[-1].date().isoformat(), tmp_path)
    assert result.loc[(dates[1], ALL_MARKET), "cap_return"] == pytest.approx(-0.05)
    assert result.loc[(dates[1], "반도체"), "member_count"] == 1
    assert result.loc[(dates[1], "미분류"), "member_count"] == 1
    assert result.attrs["unmapped_stock_count"] == 1 and result.attrs["unmapped_row_count"] == 3


def test_store_loader_blocks_holdout_before_reading_prices(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research.industry_index.load_prices", lambda *args: pytest.fail("Holdout store was read"))
    with pytest.raises(ValueError, match="holdout access requires experiment_id"):
        load_indices("2026-01-01", "2026-06-30", tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field,value", [("market_cap", np.nan), ("market_cap", 0), ("market_cap", np.inf),
                                         ("ret_1d", -1.01), ("ret_1d", np.inf), ("trading_value", -1)])
def test_invalid_inputs_fail_clearly(field, value):
    frame = prices()
    frame.iloc[0, frame.columns.get_loc(field)] = value
    with pytest.raises(ValueError):
        build_indices(frame, min_trading_value_20d=1)


@pytest.mark.parametrize("minimum", [-1, float("inf"), True])
def test_invalid_liquidity_thresholds_fail(minimum):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        build_indices(prices(), min_trading_value_20d=minimum)


def test_duplicate_keys_missing_columns_and_empty_history_fail():
    frame = prices()
    with pytest.raises(ValueError, match="unique"):
        build_indices(pd.concat([frame, frame.iloc[:1]]))
    with pytest.raises(ValueError, match="missing industry price columns"):
        build_indices(frame.drop(columns="market_cap"))
    with pytest.raises(ValueError, match="observed sessions"):
        industry_features(frame, "2024-01-01")
