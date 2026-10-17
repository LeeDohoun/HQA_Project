"""Project KRX notices, verified coverage and metadata-only price preflight."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import exchange_calendars as calendars
import pandas as pd
import pytest

from backtesting import holdout
from backtesting.experiments import av001, b001, ev001, hc001, hc003
from backtesting.experiments.lh001 import __main__ as lh001
from src.forward import hegemony_shadow as shadow
from src.runner import trading_calendar as calendar
from scripts.research import lh002_screening_diagnostic as diagnostic
from preregistration_fixtures import committed_preregistrations


def test_all_2015_2025_sessions_identical_to_dependency():
    original = calendars.get_calendar("XKRX", start="2015-01-02", end="2025-12-31").sessions
    pd.testing.assert_index_equal(calendar.session_calendar("2015-01-02", "2025-12-31"), original)
    for year in range(2015, 2026):
        actual = calendar._calendar(year).sessions
        actual = actual[(actual >= original[0]) & (actual <= original[-1])]
        expected = original[(original >= actual[0]) & (original <= actual[-1])]
        pd.testing.assert_index_equal(actual, expected)


@pytest.mark.parametrize("day,previous", [("2026-06-03", "2026-06-02"), ("2026-07-17", "2026-07-16")])
def test_notice_applies_to_session_apis_and_completed_bars(day, previous):
    notice = calendar.KRX_ADDITIONAL_CLOSED_DAYS[day]
    assert notice["reason"] and notice["source"].endswith("/2026/05/20/0023")
    assert not calendar._calendar(2026).is_session(day)
    with pytest.raises(ValueError, match="nontrading_price_date"):
        calendar.daily_session_close(day)
    as_of = datetime.fromisoformat(day + "T08:00:00+00:00")
    assert calendar.completed_daily_sessions(as_of, 1)[-1][0] == previous
    for builder in (hc001._session_calendar, b001._session_calendar, hc003.session_calendar,
                    lh001.session_calendar, lambda: shadow._calendar(pd.Timestamp(day))):
        assert pd.Timestamp(day) not in builder()
    assert calendar._calendar(2026).session_offset(previous, 1) > pd.Timestamp(day)


@pytest.mark.parametrize("stamp,previous", [("2026-11-18T08:40:00+09:00", "2026-11-17"),
    ("2026-11-19T08:40:00+09:00", "2026-11-18"),
    ("2026-11-20T18:30:00+09:00", "2026-11-20")])
def test_completed_sessions_verify_only_sessions_being_returned(stamp, previous):
    assert calendar.completed_daily_sessions(datetime.fromisoformat(stamp), 1)[-1][0] == previous


@pytest.mark.parametrize("stamp,count", [("2026-11-19T18:30:00+09:00", 1),
    ("2026-11-20T08:40:00+09:00", 1), ("2026-11-20T18:30:00+09:00", 2)])
def test_completed_sessions_never_return_an_unverified_special_session(stamp, count):
    with pytest.raises(ValueError, match="calendar_special_session_coverage_unverified:2026-11-19"):
        calendar.completed_daily_sessions(datetime.fromisoformat(stamp), count)


@pytest.mark.parametrize("operation", [
    lambda: calendar.session_calendar("2026-12-01", "2027-01-01"),
    lambda: calendar._calendar(2027),
    lambda: calendar.daily_session_close("2027-01-04"),
    lambda: calendar.completed_daily_sessions(datetime(2027, 1, 1, tzinfo=timezone.utc)),
    lambda: calendar._calendar(2026).sessions_in_range("2026-12-01", "2027-01-04"),
    lambda: calendar._calendar(2026).date_to_session("2026-12-31", direction="next"),
    lambda: calendar._calendar(2026).next_session("2026-12-30"),
    lambda: calendar._calendar(2026).sessions_window("2026-12-30", 2),
    lambda: calendar._calendar(2026).session_offset("2026-12-29", 2),
    lambda: shadow._calendar(pd.Timestamp("2027-01-04")),
])
def test_unverified_session_requests_fail_clearly(operation):
    with pytest.raises(ValueError, match="calendar_unverified"):
        operation()


def inventory(root, days):
    for day in days:
        path = root / "market/krx_daily" / day[:4] / (day.replace("-", "") + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("price contents must not be read\n")


def test_preflight_uses_metadata_only_even_for_protected_dates(tmp_path, monkeypatch):
    days = calendar.session_calendar("2026-06-01", "2026-06-05").strftime("%Y-%m-%d").tolist()
    inventory(tmp_path, days)
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("price contents opened"))
    monkeypatch.setattr(holdout, "_claim", lambda *a, **k: pytest.fail("ledger claimed"))
    result = calendar.preflight_price_sessions(tmp_path, "2026-06-01", "2026-06-05")
    assert result["sessions"] == result["price_sessions"] == 4
    assert result["missing"] == result["unexpected"] == []


def test_preflight_names_missing_and_unexpected_dates_and_empty_files(tmp_path):
    inventory(tmp_path, ["2026-06-01", "2026-06-03", "2026-06-04"])
    empty = tmp_path / "market/krx_daily/2026/20260605.jsonl"
    empty.touch()
    with pytest.raises(ValueError) as error:
        calendar.preflight_price_sessions(tmp_path, "2026-06-01", "2026-06-05")
    assert "missing=['2026-06-02', '2026-06-05']" in str(error.value)
    assert "unexpected=['2026-06-03']" in str(error.value)


@pytest.mark.parametrize("module", [av001, ev001])
def test_holdout_mismatch_stops_before_hashing_claim_and_publication(tmp_path, monkeypatch, module):
    monkeypatch.setattr(module, "_registration", lambda *a: SimpleNamespace())
    monkeypatch.setattr(hc003, "ledger_experiments", lambda *a: set())
    monkeypatch.setattr(hc003, "registry_rows", lambda *a: [])
    monkeypatch.setattr(module, "saved_design", lambda *a, **k: ({}, {"passing_variants": [module.VARIANTS[0]]}))
    monkeypatch.setattr(module, "verify_non_holdout", lambda *a, **k: None)
    monkeypatch.setattr(av001, "coverage_plan", lambda *a, **k: ({}, []))
    monkeypatch.setattr(ev001.financial_dates.FilingDates, "from_data", lambda *a: None)
    monkeypatch.setattr(ev001.financial_dates, "FinancialVersions", lambda *a, **k: SimpleNamespace(sensitivity_available=True))
    monkeypatch.setattr(module, "holdout_input_hashes", lambda *a, **k: pytest.fail("protected hashes requested"))
    monkeypatch.setattr(module, "publish", lambda *a, **k: pytest.fail("result published"))
    monkeypatch.setattr(holdout, "_claim", lambda *a, **k: pytest.fail("ledger claimed"))
    with pytest.raises(ValueError, match="calendar_price_session_mismatch"):
        module.run_holdout(data_dir=tmp_path, repo_root=tmp_path)
    assert not (tmp_path / holdout.LEDGER_PATH).exists()


@pytest.mark.parametrize("module", [av001, ev001])
def test_design_preflight_precedes_prices_and_computation(tmp_path, monkeypatch, module):
    # A real committed preregistration: the abort path verifies it against git history.
    repo = tmp_path / "repo"
    # Runners and ordering verify dependent experiments' preregistrations too.
    committed_preregistrations(repo, sorted({path.parent.name for path in Path(
        "research/experiments").glob("*/preregistration.md") if path.parent.name not in {"A001_cb_overhang", "B002_contract_ratio", "TEMPLATE"}}))
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(module, "source_hashes", lambda *a, **k: pytest.fail("prices hashed before preflight"))
    monkeypatch.setattr(module, "prepare", lambda *a, **k: pytest.fail("prepared before preflight"))
    monkeypatch.setattr(holdout, "_claim", lambda *a, **k: pytest.fail("ledger claimed"))
    with pytest.raises(ValueError, match="calendar_price_session_mismatch"):
        module.run_experiment(data_dir=data_dir, repo_root=repo)
    # The preflight fails before evaluation starts, so no trial row is written.
    assert not (repo / "research/experiments/registry.csv").exists() or "design_aborted" not in (
        repo / "research/experiments/registry.csv").read_text(encoding="utf-8")


@pytest.mark.parametrize("module", [av001, ev001])
@pytest.mark.parametrize("start,end", [("2026-01-01", "2026-06-30"), ("2025-12-01", "2026-05-31"),
                                      ("2025-01-02", "2025-06-30"), ("2025-01-01", "2025-05-31")])
def test_rehearsal_rejects_protected_or_partial_windows_before_reading(tmp_path, monkeypatch, module, start, end):
    monkeypatch.setattr(module, "_registration", lambda *a: pytest.fail("read before window validation"))
    with pytest.raises(ValueError, match="unprotected months"):
        module.rehearse_holdout(start, end, data_dir=tmp_path, repo_root=tmp_path)


@pytest.mark.parametrize("module", [av001, ev001])
@pytest.mark.parametrize("dates", [[], ["2024-01-01", "2024-06-30"]])
def test_rehearsal_cli_routes_only_to_read_only_path(module, dates, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(module, "rehearse_holdout", lambda *a, **k: calls.append((a, k)) or {"stage": "holdout_rehearsal"})
    monkeypatch.setattr(module, "run_holdout", lambda **k: pytest.fail("real holdout called"))
    monkeypatch.setattr(module, "run_experiment", lambda **k: pytest.fail("design trial called"))
    assert module.main(["--rehearse-holdout", *dates]) == 0
    assert calls[0][0] == tuple(dates)
    assert "holdout_rehearsal" in capsys.readouterr().out


def test_lh002_diagnostic_uses_project_friday_sessions():
    decisions = diagnostic.calendar_decisions("2026-07-01")
    assert "2026-07-17" not in decisions
    assert "2026-07-16" in decisions
