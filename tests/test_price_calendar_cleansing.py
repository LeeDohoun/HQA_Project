from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from src.runner.analysis_data import price_features
from src.runner.trading_calendar import CALENDAR_VERSION, SPECIAL_CLOSES, completed_daily_sessions, daily_session_close

NOW = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)


def prices(as_of=NOW, count=160):
    return [{"timestamp": day, "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000,
             "metadata": {"collected_at": close.isoformat(), "bar_at": close.isoformat(), "trade_date": day}}
            for day, close in completed_daily_sessions(as_of, count)]


def test_complete_exchange_sessions_use_observation_time_without_changing_bar_date():
    rows = prices()
    rows[-1]["metadata"]["collected_at"] = "2026-09-04T07:00:00+00:00"
    features, known = price_features(rows, NOW)
    assert features["history_days"] == 160 and features["calendar_version"] == CALENDAR_VERSION
    assert known[-1]["observed_at"] == "2026-09-04T07:00:00+00:00"
    assert known[-1]["available_at"] == known[-1]["bar_at"] == "2026-09-04T06:30:00+00:00"
    assert known[-1]["trade_date"] == "2026-09-04"


def test_weekly_rows_cannot_be_labeled_as_daily_factors():
    rows = prices(count=300)[::5]
    rows.append(prices()[-1])
    with pytest.raises(ValueError, match="incomplete_price_history"):
        price_features(rows, NOW)


def test_single_missing_session_is_not_silently_filled_or_called_a_suspension():
    rows = prices()
    del rows[-20]
    with pytest.raises(ValueError, match="missing_sessions=1"):
        price_features(rows, NOW)


def test_latest_completed_session_missing_is_stale_even_after_one_day():
    with pytest.raises(ValueError, match="stale_daily_prices"):
        price_features(prices()[:-1], NOW)


def test_holiday_closure_does_not_trigger_four_calendar_day_false_staleness():
    # Chuseok 2026 plus the weekend: Monday pre-open still uses Wednesday's bar.
    as_of = datetime(2026, 9, 28, 0, tzinfo=timezone.utc) - timedelta(seconds=1)
    rows = prices(as_of)
    assert rows[-1]["timestamp"] == "2026-09-23"
    assert price_features(rows, as_of)[0]["current_price"] == 100


def test_special_close_is_resolved_by_the_calendar_not_a_fixed_1530_clock():
    close = daily_session_close("2020-12-03")
    assert close.isoformat() == "2020-12-03T07:30:00+00:00"
    during_session = close - timedelta(minutes=30)
    rows = prices(during_session)
    rows.append({"timestamp": "2020-12-03", "close": "uncompleted row is excluded"})
    assert price_features(rows, during_session)[1][-1]["trade_date"] == "2020-12-02"


@pytest.mark.parametrize("day", ["2024-11-14", "2025-11-13"])
def test_verified_krx_notices_override_missing_recent_csat_special_closes(day):
    close = daily_session_close(day)
    assert close.isoformat() == day + "T07:30:00+00:00"
    assert completed_daily_sessions(close - timedelta(minutes=1), 1)[-1][0] != day
    assert completed_daily_sessions(close, 1)[-1] == (day, close)
    notice = SPECIAL_CLOSES[day]
    assert set(notice["source_urls"]) == {"KOSPI", "KOSDAQ"}
    assert all(url.startswith("https://kind.krx.co.kr/external/") for url in notice["source_urls"].values())
    assert datetime.fromisoformat(notice["published_at"]) < close


@pytest.mark.parametrize("day", ["2026-09-05", "2026-09-25"])
def test_weekend_and_exchange_holiday_bars_are_rejected(day):
    rows = prices(datetime(2026, 9, 30, 8, tzinfo=timezone.utc))
    rows.append({**deepcopy(rows[-1]), "timestamp": day, "metadata": {"collected_at": "2026-09-30T08:00:00+00:00"}})
    with pytest.raises(ValueError, match="nontrading_price_date"):
        price_features(rows, datetime(2026, 9, 30, 8, tzinfo=timezone.utc))


@pytest.mark.parametrize("metadata", [{}, {"collected_at": "2026-09-04T16:00:00"},
                                      {"collected_at": "2026-09-04T06:00:00+00:00"}])
def test_missing_naive_and_pre_close_observation_times_fail(metadata):
    rows = prices()
    rows[-1]["metadata"] = metadata
    with pytest.raises(ValueError, match="observation"):
        price_features(rows, NOW)


def test_later_price_correction_cannot_leak_into_earlier_as_of():
    rows = prices()
    revision = deepcopy(rows[-1])
    revision["close"] = 103
    revision["metadata"]["collected_at"] = "2026-09-04T08:01:00+00:00"
    rows.append(revision)
    assert price_features(rows, NOW)[0]["current_price"] == 100
    assert price_features(rows, NOW + timedelta(minutes=1))[0]["current_price"] == 103


@pytest.mark.parametrize("reverse", [False, True])
def test_a_b_a_reversion_selects_last_observation_not_first_matching_content(reverse):
    rows = prices()
    original = deepcopy(rows[-1])
    corrected = deepcopy(original)
    corrected["close"] = 103
    corrected["metadata"]["collected_at"] = "2026-09-04T07:00:00+00:00"
    reverted = deepcopy(original)
    reverted["metadata"]["collected_at"] = "2026-09-04T07:30:00+00:00"
    rows += [corrected, reverted]
    features, normalized = price_features(rows[::-1] if reverse else rows, NOW)
    assert features["current_price"] == 100
    assert normalized[-1]["observed_at"] == "2026-09-04T07:30:00+00:00"


def test_unchanged_observations_keep_first_availability_and_do_not_churn_input_hash():
    rows = prices()
    expected = price_features(rows, NOW)
    repeated = deepcopy(rows[-1])
    repeated["metadata"]["collected_at"] = "2026-09-04T07:00:00+00:00"
    assert price_features(rows + [repeated], NOW) == expected


def test_conflicting_same_observation_is_rejected_not_last_writer_wins():
    rows = prices()
    conflict = {**rows[-1], "close": 103}
    with pytest.raises(ValueError, match="conflicting OHLCV"):
        price_features(rows + [conflict], NOW)


def test_calendar_range_and_naive_as_of_fail_clearly():
    with pytest.raises(ValueError, match="supported_range"):
        daily_session_close("2051-01-03")
    with pytest.raises(ValueError, match="aware"):
        completed_daily_sessions(datetime(2026, 9, 4))


@pytest.mark.parametrize("day", ["2021-11-18", "2022-11-17", "2023-11-16", "2026-11-19"])
def test_unverified_special_session_periods_fail_instead_of_guessing_normal_close(day):
    with pytest.raises(ValueError, match="calendar_special_session_coverage_unverified"):
        daily_session_close(day)


def test_calendar_expiry_blocks_future_analysis_until_official_notice_is_added():
    with pytest.raises(ValueError, match="official_KRX_notice_required"):
        completed_daily_sessions(datetime(2026, 11, 1, tzinfo=timezone.utc))


def test_future_observation_is_excluded_before_its_bad_bar_is_validated():
    rows = prices()
    rows.append({"timestamp": "2026-08-29", "metadata": {"collected_at": "2026-09-07T07:00:00+00:00"}})
    assert price_features(rows, NOW) == price_features(prices(), NOW)
