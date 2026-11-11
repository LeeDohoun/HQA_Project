"""KOSPI/KOSDAQ closes and stock market mappings from KIS, in the formats the benchmark loader checks."""
import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts.data import kis_market_context, loop as collect_loop
from src.ingestion.kis_benchmarks import (KisBenchmarkCollector, kis_market_mapping, market_from_representative)
from src.ingestion.krx_benchmarks import _record, kis_benchmark_record, validate_benchmark_record
from src.runner.market_context_data import _mapping, load_benchmark_context
from src.runner.trading_calendar import is_trading_day

OBSERVED = datetime(2026, 11, 11, 14, 0, tzinfo=timezone.utc)  # 23:00 KST


def test_kis_index_records_validate_and_tampering_is_rejected():
    row = kis_benchmark_record("KOSPI", date(2026, 11, 11), 5375.36, OBSERVED)
    assert validate_benchmark_record(row) == row
    assert row["source_id"].startswith("kis-benchmark:") and row["index_name"] == "코스피"
    assert "FID_INPUT_ISCD=0001" in row["source_url"] and row["price_basis"] == "price_index"
    for change in ({"close": 5375.37}, {"index_name": "코스닥"}, {"series": "KOSDAQ"}, {"source_url": "https://x"}):
        with pytest.raises(ValueError):
            validate_benchmark_record({**row, **change})
    with pytest.raises(ValueError, match="precedes the completed market close"):
        kis_benchmark_record("KOSPI", date(2026, 11, 11), 5375.36, datetime(2026, 11, 11, 5, tzinfo=timezone.utc))
    krx = _record("KOSPI", "코스피", date(2026, 11, 11), 5375.36, OBSERVED)
    assert validate_benchmark_record(krx) == krx and krx["source_id"].startswith("krx-benchmark:")


def test_representative_market_names_map_only_to_kospi_or_kosdaq():
    names = ("KOSPI200", "KOSPI", "KSQ150", "KOSDAQ", "KRX300", "KONEX", "", None)
    assert [market_from_representative(name) for name in names] == \
        ["KOSPI", "KOSPI", "KOSDAQ", "KOSDAQ", None, None, None, None]


def test_market_mappings_pass_the_analysis_loaders_own_check():
    row = kis_market_mapping("000660", "KOSPI", OBSERVED, date(2025, 10, 7))
    assert _mapping(row)["series"] == "KOSPI"
    assert row["effective_to"] is None and row["source_id"].startswith("kis-market:")
    with pytest.raises(ValueError):
        kis_market_mapping("000660", "NASDAQ", OBSERVED, date(2025, 10, 7))


def sessions(start, end):
    day, days = start, []
    while day <= end:
        if is_trading_day(day.isoformat()):
            days.append(day.strftime("%Y%m%d"))
        day += timedelta(days=1)
    return days


class FakeIndexKis:
    """The KIS index chart (at most 50 rows per call, newest first) and quote endpoints."""

    def __init__(self, days):
        self.days, self.calls = days, []

    def post(self, url, json=None, headers=None, timeout=None, allow_redirects=None):
        return SimpleNamespace(status_code=200, json=lambda: {"access_token": "fixture-token"})

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=None):
        self.calls.append((url, dict(params)))
        if url.endswith("inquire-price"):
            payload = {"rt_cd": "0", "output": {"rprs_mrkt_kor_name": "KSQ150"}}
        else:
            window = [day for day in self.days if params["FID_INPUT_DATE_1"] <= day <= params["FID_INPUT_DATE_2"]]
            rows = [{"stck_bsop_date": day, "bstp_nmix_prpr": f"{5000 + index}.12"}
                    for index, day in enumerate(reversed(window))][:50]
            payload = {"rt_cd": "0", "output2": rows or [{"stck_bsop_date": "", "bstp_nmix_prpr": ""}]}
        return SimpleNamespace(status_code=200, json=lambda: payload)


def fake_collector(fake):
    clock = SimpleNamespace(now=0.0)

    def sleep(seconds):
        clock.now += seconds
    return KisBenchmarkCollector("fixture-key", "fixture-secret", session=fake, sleep=sleep, clock=lambda: clock.now)


def test_index_history_pages_back_fifty_sessions_at_a_time(monkeypatch):
    monkeypatch.setattr("src.ingestion.kis_benchmarks._now", lambda: OBSERVED)
    start, end = date(2025, 10, 7), date(2026, 11, 11)
    fake = FakeIndexKis(sessions(start, end))
    records = fake_collector(fake).collect_index_daily("KOSDAQ", start, end)
    assert [row["trade_date"].replace("-", "") for row in records] == fake.days
    assert len(fake.calls) == len(fake.days) // 50 + 1
    assert all(validate_benchmark_record(row) == row for row in records)
    market, _ = fake_collector(fake).collect_market("218410")
    assert market == "KOSDAQ"


def write_targets(data_dir, codes):
    folder = data_dir / "raw" / "theme_targets"
    folder.mkdir(parents=True)
    (folder / "t.jsonl").write_text("".join(json.dumps({"stock_code": code, "stock_name": code}) + "\n"
                                            for code in codes), encoding="utf-8")


class StubCollector:
    def __init__(self):
        self.index_calls, self.market_calls = [], []

    def collect_index_daily(self, series, start, end):
        self.index_calls.append((series, start, end))
        days = sessions(start, end)
        return [kis_benchmark_record(series, datetime.strptime(day, "%Y%m%d").date(), 5000.0 + index, OBSERVED)
                for index, day in enumerate(days)]

    def collect_market(self, code):
        self.market_calls.append(code)
        return ("KOSPI" if code != "999999" else None), OBSERVED


def test_refresh_stores_what_the_loader_reads_and_then_calls_nothing(tmp_path):
    write_targets(tmp_path, ["005930", "999999"])
    stub = StubCollector()
    summary = kis_market_context.refresh(tmp_path, OBSERVED, collector=stub)
    assert summary["latest_session"] == "2026-11-11" and summary["new_mappings"] == 1
    assert summary["unmapped"] == ["999999"] and summary["saved_records"] > 500
    assert [call[0] for call in stub.index_calls] == ["KOSPI", "KOSDAQ"]
    again = StubCollector()
    assert kis_market_context.refresh(tmp_path, OBSERVED, collector=again)["saved_records"] == 0
    assert again.index_calls == [] and again.market_calls == ["999999"]

    bar = OBSERVED - timedelta(hours=7, minutes=30)
    candidate = {"stock_code": "005930", "price_history": [
        {"trade_date": "2026-11-11", "available_at": bar.isoformat(), "bar_at": bar.isoformat(),
         "observed_at": OBSERVED.isoformat(), "close": 205500.0}]}
    context = load_benchmark_context(tmp_path, candidate, OBSERVED + timedelta(minutes=1))
    assert context["market"]["status"] == "ready" and context["market"]["series"] == "KOSPI"
    assert context["market"]["bars"][-1]["trade_date"] == "2026-11-11"


def test_the_loop_reports_a_hung_refresh_instead_of_dying(monkeypatch):
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="kis_market_context", timeout=600)
    monkeypatch.setattr(collect_loop.subprocess, "run", hang)
    code, output = collect_loop._run_kis_market_context()
    assert code == 124 and "timed out" in output
