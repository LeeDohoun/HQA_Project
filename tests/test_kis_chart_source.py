"""kis_chart: KIS daily bars as the explicit, provenance-checked stand-in for KRX daily prices."""
import json
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

from scripts.data import collect as theme_pipeline
from scripts.data import common as collection_common
from scripts.data import loop as collect_themes_loop
from src.ingestion.kis_chart import KST, KisChartCollector
from src.ingestion.krx_chart import KrxChartCollector
from src.ingestion.services import IngestionService
from src.ingestion.storage import read_rows
from src.ingestion.theme_targets import ThemeTargetStore
from src.ingestion.types import CollectRequest, StockTarget
from src.runner.analysis_data import LocalAnalysisData, price_features
from src.runner.trading_calendar import daily_session_close, is_trading_day

NOW = datetime(2026, 9, 7, 1, tzinfo=timezone.utc)  # Monday 10:00 KST; the last session is Friday 09-04.
OBSERVED = NOW - timedelta(hours=1)
KEY, SECRET = "fixture-app-key", "fixture-app-secret"


def sessions(start="2024-09-01", end="2026-09-04"):
    day, last, days = date.fromisoformat(start), date.fromisoformat(end), []
    while day <= last:
        if is_trading_day(day.isoformat()):
            days.append(day.strftime("%Y%m%d"))
        day += timedelta(days=1)
    return days


def bar(day, index):
    close = 50000 + 10 * index
    return {"stck_bsop_date": day, "stck_oprc": str(close), "stck_hgpr": str(close + 100),
            "stck_lwpr": str(close - 100), "stck_clpr": str(close), "acml_vol": "123456",
            "acml_tr_pbmn": "6172800000", "flng_cls_code": "00", "prtt_rate": "0.00", "mod_yn": "N",
            "prdy_vrss_sign": "3", "prdy_vrss": "0", "revl_issu_reas": ""}


BLANK = {key: "" for key in bar("20260101", 0)}


class FakeKis:
    """KIS paper endpoints over fixed sessions: newest first, at most 100 rows per call."""

    def __init__(self, days=None, token_codes=(), chart_codes=(), mutate=None):
        self.days = sessions() if days is None else days
        self.token_codes, self.chart_codes = list(token_codes), list(chart_codes)
        self.mutate = mutate
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None, allow_redirects=None):
        self.calls.append(("POST", url, json))
        code = self.token_codes.pop(0) if self.token_codes else None
        if code:
            return SimpleNamespace(status_code=403, json=lambda: {"error_code": code, "error_description": "refused"})
        return SimpleNamespace(status_code=200, json=lambda: {"access_token": "fixture-token", "expires_in": 86400})

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=None):
        self.calls.append(("GET", url, dict(params), dict(headers)))
        code = self.chart_codes.pop(0) if self.chart_codes else None
        if code:
            return SimpleNamespace(status_code=500, json=lambda: {"rt_cd": "1", "msg_cd": code, "msg1": "refused"})
        window = [day for day in self.days if params["FID_INPUT_DATE_1"] <= day <= params["FID_INPUT_DATE_2"]]
        rows = [bar(day, self.days.index(day)) for day in reversed(window)][:100]
        payload = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok",
                   "output1": {"stck_shrn_iscd": params["FID_INPUT_ISCD"], "hts_kor_isnm": "삼성전자"},
                   "output2": rows or [dict(BLANK)]}
        if self.mutate:
            self.mutate(payload)
        return SimpleNamespace(status_code=200, json=lambda: payload)

    def gets(self):
        return [call for call in self.calls if call[0] == "GET"]

    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]


class Clock:
    """Monotonic time that moves only when the collector sleeps."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def collector(fake, clock=None):
    clock = clock or Clock()
    return KisChartCollector(KEY, SECRET, session=fake, sleep=clock.sleep, clock=lambda: clock.now)


@pytest.fixture
def at_now(monkeypatch):
    monkeypatch.setattr("src.ingestion.kis_chart._now", lambda: OBSERVED)


def test_backfill_pages_back_over_original_prices_and_paces_every_call(at_now):
    fake, clock = FakeKis(), Clock()
    records = collector(fake, clock).collect_daily("삼성전자", "005930", "20250305", "20260906")
    expected = [day for day in fake.days if "20250305" <= day <= "20260906"]
    assert [record.metadata["raw_date"] for record in records] == expected and len(expected) > 300
    gets = fake.gets()
    assert len(gets) == -(-len(expected) // 100) and len(fake.posts()) == 1
    window_end = "20260906"
    for _, url, params, headers in gets:
        # Each call ends the day before the oldest bar of the previous (full) page.
        assert params["FID_INPUT_DATE_2"] == window_end
        oldest = [day for day in expected if day <= window_end][-100:][0]
        window_end = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        assert url == KisChartCollector.DAILY_URL
        assert params["FID_ORG_ADJ_PRC"] == "1" and params["FID_PERIOD_DIV_CODE"] == "D"
        assert params["FID_INPUT_DATE_1"] == "20250305" and params["FID_INPUT_ISCD"] == "005930"
        assert headers["tr_id"] == "FHKST03010100" and headers["authorization"] == "Bearer fixture-token"
    assert clock.sleeps == [pytest.approx(1.1)] * (len(fake.calls) - 1)
    record = records[-1]
    meta = record.metadata
    assert (record.source_type, record.timestamp, record.close) == (
        "chart", "2026-09-04T00:00:00", str(50000 + 10 * fake.days.index("20260904")))
    assert meta["source"] == "kis" and meta["price_basis"] == "unadjusted" and meta["tr_id"] == "FHKST03010100"
    assert meta["source_url"] == KisChartCollector.DAILY_URL and meta["trade_date"] == "2026-09-04"
    assert meta["bar_at"] == daily_session_close("2026-09-04").astimezone(KST).isoformat()
    assert meta["collected_at"] == meta["available_at"] == OBSERVED.isoformat()
    assert len(meta["version"]) == 64 and meta["source_id"] == "kis-chart:" + meta["version"]
    assert "market" not in meta
    features, known = price_features([asdict(row) for row in records], NOW)
    assert features["history_days"] == 300 and known[-1]["trade_date"] == "2026-09-04"
    assert {(row["source"], row["price_basis"], row["source_url"]) for row in known} == {
        ("kis", "unadjusted", KisChartCollector.DAILY_URL)}


def test_token_is_issued_once_per_run_and_one_egw00133_is_waited_out(at_now):
    fake, clock = FakeKis(token_codes=["EGW00133"]), Clock()
    kis = collector(fake, clock)
    kis.collect_daily("삼성전자", "005930", "20260801", "20260906")
    kis.collect_daily("SK하이닉스", "000660", "20260801", "20260906")
    assert len(fake.posts()) == 2 and len(fake.gets()) == 2
    assert 61 in clock.sleeps


@pytest.mark.parametrize("codes", [["EGW00133", "EGW00133"], ["EGW00103"]])
def test_token_refusals_report_the_code_without_credentials(at_now, codes):
    fake, clock = FakeKis(token_codes=codes), Clock()
    with pytest.raises(ValueError, match=f"error_code={codes[-1]}") as error:
        collector(fake, clock).collect_daily("삼성전자", "005930", "20260801", "20260906")
    assert KEY not in str(error.value) and SECRET not in str(error.value)
    assert fake.gets() == [] and (61 in clock.sleeps) is (len(codes) == 2)


def test_per_second_refusals_are_retried_then_reported_without_daily_quota_wording(at_now):
    fake, clock = FakeKis(chart_codes=["EGW00201", "EGW00201"]), Clock()
    assert collector(fake, clock).collect_daily("삼성전자", "005930", "20260801", "20260906")
    fake = FakeKis(chart_codes=["EGW00201"] * 3)
    with pytest.raises(ValueError, match="HTTP 500, msg_cd=EGW00201") as error:
        collector(fake).collect_daily("삼성전자", "005930", "20260801", "20260906")
    # The loop pauses until tomorrow on a daily quota; a per-second KIS limit must not look like one.
    assert not collect_themes_loop._contains_rate_limit(str(error.value))
    fake = FakeKis(chart_codes=["OPSQ2000"])
    with pytest.raises(ValueError, match="msg_cd=OPSQ2000"):
        collector(fake).collect_daily("삼성전자", "005930", "20260801", "20260906")
    assert len(fake.gets()) == 1


def test_transport_errors_keep_only_the_exception_type(at_now):
    class Failing(FakeKis):
        def get(self, url, **kwargs):
            raise requests.ConnectionError(f"{url}?appkey={KEY}&appsecret={SECRET} Bearer fixture-token")

    with pytest.raises(requests.RequestException) as error:
        collector(Failing()).collect_daily("삼성전자", "005930", "20260801", "20260906")
    assert str(error.value) == "KIS daily chart request failed (ConnectionError)"


def test_credentials_dates_and_codes_are_checked_before_any_call(at_now, monkeypatch):
    for name in ("KIS_PAPER_APP_KEY", "KIS_VTS_APP_KEY", "KIS_PAPER_APP_SECRET", "KIS_VTS_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    fake = FakeKis()
    with pytest.raises(ValueError, match="KIS_PAPER_APP_KEY and KIS_PAPER_APP_SECRET are required"):
        KisChartCollector(session=fake).collect_daily("삼성전자", "005930", "20260801", "20260906")
    with pytest.raises(ValueError, match="exclude current"):
        collector(fake).collect_daily("삼성전자", "005930", "20260801", "20260907")
    with pytest.raises(ValueError, match="six-digit"):
        collector(fake).collect_daily("삼성전자", "5930", "20260801", "20260906")
    with pytest.raises(ValueError, match="YYYYMMDD"):
        collector(fake).collect_daily("삼성전자", "005930", "2026-08-01", "20260906")
    assert fake.calls == []


@pytest.mark.parametrize("mutate, message", [
    (lambda payload: payload["output2"][0].update(stck_bsop_date="20260907"), "outside the requested dates"),
    (lambda payload: payload["output2"][0].update(stck_clpr="50,000"), "nonnegative finite"),
    (lambda payload: payload["output2"][0].update(acml_vol="-1"), "nonnegative finite"),
    (lambda payload: payload["output2"][0].pop("stck_hgpr"), "nonnegative finite"),
    (lambda payload: payload["output1"].update(stck_shrn_iscd="000660"), "different stock"),
    (lambda payload: payload.update(output2={}), "output2 array"),
])
def test_provider_rows_are_validated(at_now, mutate, message):
    with pytest.raises(ValueError, match=message):
        collector(FakeKis(mutate=mutate)).collect_daily("삼성전자", "005930", "20260801", "20260906")


def test_an_empty_window_answers_with_no_bars(at_now):
    fake = FakeKis()
    assert collector(fake).collect_daily("삼성전자", "005930", "20260905", "20260906") == []
    assert fake.gets()[0][2]["FID_INPUT_DATE_1"] == "20260905"


def kis_rows():
    fake = FakeKis()
    records = KisChartCollector(KEY, SECRET, session=fake, sleep=lambda _: None).collect_daily(
        "삼성전자", "005930", "20250305", "20260906")
    return [asdict(record) for record in records]


@pytest.mark.parametrize("change, message", [
    ({"source_url": KisChartCollector.DOMAIN + "/uapi/domestic-stock/v1/quotations/inquire-daily-price"}, "KIS"),
    ({"source_url": "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/"
                    "inquire-daily-itemchartprice"}, "KIS"),
    ({"price_basis": "adjusted"}, "KIS"),
    ({"price_basis": None}, "KIS"),
    ({"market": "KOSPI"}, "KRX"),
])
def test_price_features_accept_only_the_exact_kis_endpoint_and_original_basis(at_now, change, message):
    rows = kis_rows()
    rows[-1] = {**rows[-1], "metadata": {**rows[-1]["metadata"], **change}}
    with pytest.raises(ValueError, match=f"unverified {message} price"):
        price_features(rows, NOW)


def test_kis_bars_never_splice_with_unlabeled_bars_but_join_krx_bars(at_now):
    rows = kis_rows()
    def unlabeled(row):  # the shape of the FinanceDataReader bars used for local tests
        return {**row, "timestamp": row["timestamp"] + "+09:00", "metadata": {
            "source": "fdr", "trade_date": row["metadata"]["trade_date"], "collected_at": "2026-09-05T00:00:00+00:00"}}

    with pytest.raises(ValueError, match="mixed_price_basis"):
        price_features(rows[:-200] + [unlabeled(row) for row in rows[-200:-190]] + rows[-190:], NOW)
    # Older unlabeled bars outside the 300-session window do not matter.
    features, known = price_features([unlabeled(rows[0])] + rows[-300:], NOW)
    assert {row["source"] for row in known} == {"kis"}
    krx = [{**row, "metadata": {**row["metadata"], "source": "krx", "market": "KOSPI",
                                "source_url": KrxChartCollector.KOSPI_DAILY_URL}} for row in rows[-10:]]
    features, known = price_features(rows[:-10] + krx, NOW)
    assert [row["source"] for row in known[-11:]] == ["kis"] + ["krx"] * 10


def request(tmp_path, theme="first", sources=("kis_chart",), **changes):
    return replace(CollectRequest(target=StockTarget("삼성전자", "005930", "00126380"), max_news=20,
        forum_pages=1, chart_pages=1, from_date="20250802", to_date="20260906", dart_api_key="",
        theme_key=theme, enabled_sources=list(sources), raw_output_dir=str(tmp_path / "raw"),
        incremental=True), **changes)


class StateClock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current


@pytest.fixture
def state_clock(monkeypatch):
    monkeypatch.setattr("src.ingestion.collection_state.datetime", StateClock)
    StateClock.current = NOW
    return StateClock


def test_first_run_backfills_one_basis_window_then_revisits_recent_days(tmp_path, at_now, state_clock, monkeypatch):
    fake = FakeKis()
    service = IngestionService(kis_chart_collector=collector(fake))
    first = service.collect(request(tmp_path))
    calls = len(fake.gets())
    # 400 requested calendar days hold only ~270 sessions; the backfill reaches 550 days.
    assert fake.gets()[0][2]["FID_INPUT_DATE_1"] == "20250305"
    shared = read_rows(tmp_path / "raw/chart/_shared_005930.jsonl")
    projected = read_rows(tmp_path / "raw/chart/first.jsonl")
    assert len(shared) == len(projected) == first.report.raw_saved_counts["kis_chart"] > 300
    assert first.report.source_status == {"kis_chart": "success"} and first.report.cache_hits == {"kis_chart": False}
    assert {row["metadata"]["source"] for row in projected} == {"kis"}
    assert not (tmp_path / "raw/kis_chart").exists()
    second = service.collect(request(tmp_path, "second"))
    assert second.report.cache_hits == {"kis_chart": True} and len(fake.gets()) == calls
    assert len(read_rows(tmp_path / "raw/chart/second.jsonl")) == len(shared)
    state_clock.current = NOW + timedelta(days=1)
    monkeypatch.setattr("src.ingestion.kis_chart._now", lambda: OBSERVED + timedelta(days=1))
    third = service.collect(request(tmp_path, to_date="20260907", from_date="20250803"))
    assert fake.gets()[-1][2]["FID_INPUT_DATE_1"] == "20260830"
    assert third.report.source_status == {"kis_chart": "success"} and third.report.raw_saved_counts["kis_chart"] == 0
    assert len(read_rows(tmp_path / "raw/chart/_shared_005930.jsonl")) == len(shared)


@pytest.mark.parametrize("last_day, reused", [("2026-09-04", True), ("2026-09-03", False)])
def test_same_day_runs_reuse_a_result_only_when_it_holds_the_last_session(tmp_path, at_now, state_clock,
                                                                          last_day, reused):
    fake = FakeKis(days=sessions(end=last_day))
    service = IngestionService(kis_chart_collector=collector(fake))
    service.collect(request(tmp_path))
    calls = len(fake.gets())
    state_clock.current = NOW + timedelta(hours=3)  # 13:00 KST, past the 15-minute reuse
    result = service.collect(request(tmp_path))
    assert result.report.cache_hits == {"kis_chart": reused}
    assert (len(fake.gets()) == calls) is reused
    state_clock.current = NOW + timedelta(hours=15)  # 01:00 KST the next day, same requested window
    assert service.collect(request(tmp_path)).report.cache_hits == {"kis_chart": False}


def test_chart_and_kis_chart_are_alternatives_and_chart_never_falls_back_to_kis(tmp_path):
    class Kis:
        def collect_daily(self, **kwargs):
            raise AssertionError("chart must not fall back to KIS")

    class Krx:
        def collect_daily(self, **kwargs):
            raise RuntimeError("krx_unauthorized")

    service = IngestionService(krx_chart_collector=Krx(), kis_chart_collector=Kis())
    with pytest.raises(ValueError, match="alternative price sources"):
        service.collect(request(tmp_path, sources=("chart", "kis_chart")))
    with pytest.raises(ValueError, match="alternative price sources"):
        collection_common.enabled_sources("news,chart,kis_chart")
    assert collection_common.enabled_sources("news,kis_chart") == ["news", "kis_chart"]
    result = service.collect(request(tmp_path, sources=("chart",), incremental=False))
    assert result.report.source_status == {"chart": "error"} and result.report.failures["chart"] == "krx_unauthorized"


def test_collect_with_kis_chart_publishes_a_generation_the_analysis_accepts(tmp_path, monkeypatch, at_now):
    class DateClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    ThemeTargetStore(str(tmp_path)).save_targets("fixture", [StockTarget("삼성전자", "005930")])
    monkeypatch.setattr(collection_common, "datetime", DateClock)
    fake = FakeKis()
    monkeypatch.setattr("src.ingestion.services.KisChartCollector",
                        lambda: KisChartCollector(KEY, SECRET, session=fake, sleep=lambda _: None))
    monkeypatch.setattr("sys.argv", ["collect", "--theme", "fixture", "--data-dir", str(tmp_path),
                                     "--enabled-sources", "kis_chart"])
    assert theme_pipeline.main() == 0
    report = json.loads((tmp_path / "reports/fixture_ingestion_report.json").read_text(encoding="utf-8"))
    assert (report["status"], report["build_status"], report["to_date"]) == ("done", "done", "20260906")
    assert report["per_stock_reports"][0]["source_status"] == {"kis_chart": "success"}
    loader = LocalAnalysisData(data_dir=str(tmp_path))
    generation = loader._current_generation("fixture")
    rows = read_rows(loader._generation_dir("fixture", generation) / "chart.jsonl")
    assert report["market_stats"]["chart"] == len(rows) > 300
    features, known = price_features(rows, NOW)
    assert features["history_days"] == 300 and {row["source"] for row in known} == {"kis"}
    assert KEY not in (tmp_path / "reports/fixture_ingestion_report.json").read_text(encoding="utf-8")
