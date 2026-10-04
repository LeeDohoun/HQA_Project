"""Quarter derivation and filing availability use only offline DART fixtures."""
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pandas as pd
import pytest

from scripts.data import dart_quarterly as cli
from src.ingestion import dart_quarterly as module
from src.ingestion.dart_quarterly import KST, backfill, load_quarterly, load_universe, yoy_signals
from src.ingestion.storage import read_rows


KEY = "fixture-private-dart-key"
CORP = "00126380"
STOCK = "005930"
START = datetime(2026, 10, 5, 10, tzinfo=KST)
UNIVERSE = [{"corp_code": CORP, "stock_code": STOCK}]
DAYS = {"11013": "20240515", "11012": "20240814", "11014": "20241114", "11011": "20250331"}


@pytest.fixture(autouse=True)
def no_env_or_real_session(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Quarterly tests must not load env or create real sessions")

    monkeypatch.setattr("src.config.settings.load_project_env", denied)
    monkeypatch.setattr(cli, "load_project_env", denied)
    monkeypatch.setattr("requests.Session", denied)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        if isinstance(self.payload, ValueError):
            raise self.payload
        return self.payload

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.payloads:
            pytest.fail("Unexpected DART request")
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception) and not isinstance(payload, ValueError):
            raise payload
        return FakeResponse(payload)


def filing(report, amounts=(100, 10, 5), *, cumulative=None, day=None, number=1,
           division="CFS", corp=CORP, currency="KRW", period=None, year=2024):
    names = ("매출액", "영업이익", "당기순이익")
    day = DAYS[report] if day is None else day
    return [{"corp_code": corp, "bsns_year": str(year), "reprt_code": report,
             "fs_div": division, "rcept_no": f"{day}{number:06d}", "currency": currency,
             "account_nm": name, "thstrm_amount": None if amount is None else f"{amount:,}",
             "thstrm_add_amount": None if cumulative is None or cumulative[i] is None else f"{cumulative[i]:,}",
             "thstrm_dt": period} for i, (name, amount) in enumerate(zip(names, amounts))]


def payload(*rows):
    return {"status": "000", "list": list(rows)}


def root(tmp_path):
    return tmp_path / "fundamentals/dart_quarterly"


def archive(tmp_path, report="11013"):
    return root(tmp_path) / f"2024_{report}.jsonl"


def run(tmp_path, *responses, universe=UNIVERSE, now=START, **kwargs):
    session = FakeSession(*responses)
    summary = backfill(2024, 2024, execute=True, api_key=KEY, session=session,
        data_dir=tmp_path, universe_source=universe, clock=lambda: now, **kwargs)
    return summary, session


def test_universe_union_keeps_delisted_codes_and_all_stock_aliases(tmp_path):
    path = tmp_path / "corp_codes.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("corp_code", "corp_name", "stock_code", "modify_date"))
        writer.writeheader()
        writer.writerows([
            {"corp_code": CORP, "stock_code": STOCK, "corp_name": "과거상장", "modify_date": "20160101"},
            {"corp_code": "00000002", "stock_code": "00088K", "corp_name": "상장폐지", "modify_date": "20150101"},
            {"corp_code": "00000003", "stock_code": "", "corp_name": "비상장", "modify_date": "20150101"},
        ])
    listing = tmp_path / "list/2026"
    listing.mkdir(parents=True)
    rows = [*UNIVERSE, {"corp_code": CORP, "stock_code": "005935"},
            {"corp_code": "00000004", "stock_code": "0015G0"}]
    (listing / "20261001.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert load_universe(corp_codes_path=path, listing_dir=listing.parent) == {
        "00000002": ["00088K"], "00000004": ["0015G0"], CORP: [STOCK, "005935"]}
    source = Mock(return_value=rows)
    assert load_universe(corp_codes_path="missing.csv", listing_dir="missing", universe_source=source)[CORP] == [STOCK, "005935"]
    source.assert_called_once_with()


@pytest.mark.parametrize("row", [
    {"corp_code": "1", "stock_code": STOCK}, {"corp_code": CORP, "stock_code": "bad"},
    {"corp_code": CORP, "stock_code": 5930}, None,
])
def test_invalid_universe_is_explicit(row):
    with pytest.raises(ValueError, match="invalid universe"):
        load_universe(universe_source=[row])


def test_dry_run_plans_without_env_session_clock_requests_or_writes(tmp_path, capsys):
    companies = [{"corp_code": f"{i:08d}", "stock_code": f"{i:06d}"} for i in range(201)]
    session = FakeSession()
    clock = Mock(side_effect=AssertionError("dry run must not read the clock"))
    summary = backfill(2015, 2026, data_dir=tmp_path / "untouched", universe_source=companies,
        session=session, clock=clock)
    assert summary["status"] == "dry_run"
    assert (summary["companies"], summary["batches"], summary["reports"], summary["requests"]) == (201, 3, 48, 144)
    assert summary["pending_requests"] == 144 and summary["max_requests"] == 3000
    assert json.loads(capsys.readouterr().out) == summary
    assert session.calls == [] and not list(tmp_path.iterdir())
    clock.assert_not_called()


def test_multi_company_batches_are_at_most_100_and_013_is_completed(tmp_path):
    companies = [{"corp_code": f"{i:08d}", "stock_code": f"{i:06d}"} for i in range(201)]
    summary, session = run(tmp_path, *[{"status": "013"}] * 12, universe=companies)
    assert summary["status"] == "ok" and summary["requests_made"] == 12
    assert summary["records_saved"] == summary["pending_requests"] == 0
    for index, (url, kwargs) in enumerate(session.calls):
        params = kwargs["params"]
        assert url == module.URL
        assert len(params["corp_code"].split(",")) == (100, 100, 1)[index % 3]
        assert params["bsns_year"] == "2024"
        assert params["reprt_code"] == list(module.REPORTS)[index // 3]
        assert params["crtfc_key"] == KEY
        assert "fs_div" not in params
    state = json.loads((root(tmp_path) / "_state.json").read_text())
    assert len(state["completed_batches"]) == 12
    assert all(archive(tmp_path, report).read_bytes() == b"" for report in module.REPORTS)
    before = {path.name: path.read_bytes() for path in root(tmp_path).iterdir()}
    resumed, session = run(tmp_path, universe=companies)
    assert resumed["status"] == "ok" and session.calls == []
    assert before == {path.name: path.read_bytes() for path in root(tmp_path).iterdir()}


def test_cfs_preference_ofs_fallback_and_missing_cfs_accounts_are_not_mixed(tmp_path):
    universe = [*UNIVERSE, {"corp_code": CORP, "stock_code": "005935"},
                {"corp_code": "00000002", "stock_code": "00088K"}]
    rows = [*filing("11013", (100, None, 5)), *filing("11013", (999, 99, 9), division="OFS"),
            *filing("11013", (50, 5, 2), division="OFS", corp="00000002")]
    summary, _ = run(tmp_path, payload(*rows), *[{"status": "013"}] * 3, universe=universe)
    assert summary["records_saved"] == 3
    assert {row["fs_div"] for row in read_rows(archive(tmp_path))} == {"CFS", "OFS"}
    assert load_quarterly("2024-05-15", data_dir=tmp_path).empty
    frame = load_quarterly("2024-05-16", data_dir=tmp_path).set_index("stock_code")
    assert len(frame) == 3
    assert frame.loc[STOCK, "fs_div"] == "CFS" and frame.loc[STOCK, "revenue"] == 100
    assert pd.isna(frame.loc[STOCK, "operating_income"])
    assert frame.loc[STOCK, "missing_reasons"]["operating_income"] == "missing_or_invalid_q1_amount"
    assert frame.loc["005935", "revenue"] == 100
    assert frame.loc["00088K", "fs_div"] == "OFS" and frame.loc["00088K", "revenue"] == 50
    raw = read_rows(archive(tmp_path))[0]["raw_accounts"]["revenue"]
    assert {"thstrm_amount", "thstrm_add_amount", "rcept_no", "currency", "fs_div"} <= raw.keys()


@pytest.mark.parametrize("name", ["매출액", "수익(매출액)", "영업수익", " 수익 (매출액)\u3000"])
def test_account_aliases_and_normalisation(tmp_path, name):
    rows = filing("11013", (1234, -10, -5))
    rows[0]["account_nm"] = name
    rows[1]["account_nm"] = "영업 이익(손실)"
    rows[2]["account_nm"] = "당기 순이익(손실)"
    run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    frame = load_quarterly("2024-05-16", data_dir=tmp_path)
    assert frame.loc[0, ["revenue", "operating_income", "net_income"]].tolist() == [1234, -10, -5]


@pytest.mark.parametrize("metric,index,names", [
    ("revenue", 0, ("매출액", "수익(매출액)", "영업수익")),
    ("revenue", 0, ("수익(매출액)", "영업수익")),
    ("operating_income", 1, ("영업이익", "영업이익(손실)")),
    ("net_income", 2, ("당기순이익", "당기순이익(손실)", "연결당기순이익")),
    ("net_income", 2, ("당기순이익(손실)", "연결당기순이익")),
])
def test_account_alias_priority_is_independent_of_response_order(tmp_path, metric, index, names):
    rows = filing("11013")
    account = rows.pop(index)
    rows.extend({**account, "account_nm": name, "thstrm_amount": str(100 + offset)}
                for offset, name in reversed(list(enumerate(names))))
    summary, _ = run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    assert summary["status"] == "ok" and summary["conflicting_metrics"] == 0
    frame = load_quarterly("2024-05-16", data_dir=tmp_path)
    assert frame.iloc[0][metric] == 100 and frame.iloc[0]["missing_reasons"] == {}
    assert read_rows(archive(tmp_path))[0]["raw_accounts"][metric]["account_nm"] == names[0]


@pytest.mark.parametrize("index", [0, 1, 2])
def test_identical_account_duplicates_are_silently_deduplicated(tmp_path, index):
    rows = filing("11013")
    rows.extend([dict(rows[index]), dict(rows[index])])
    summary, _ = run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    assert summary["status"] == "ok" and summary["conflicting_metrics"] == 0
    stored = read_rows(archive(tmp_path))[0]
    assert len(stored["raw_accounts"]) == 3 and stored["missing_reasons"] == {}
    assert [stored[metric] for metric in module.ACCOUNTS] == [100, 10, 5]


@pytest.mark.parametrize("metric,index,alias", [
    ("revenue", 0, "매출액"), ("operating_income", 1, "영업이익"),
    ("net_income", 2, "당기순이익"),
])
@pytest.mark.parametrize("field", ["thstrm_amount", "thstrm_add_amount"])
def test_conflicting_selected_alias_is_missing_without_using_lower_alias(tmp_path, metric, index, alias, field):
    rows = filing("11013")
    rows.extend([{**rows[index], field: "200"}, {**rows[index], field: "200"},
                 {**rows[index], "account_nm": module.ACCOUNTS[metric][1]}])
    summary, session = run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    assert summary["status"] == "ok" and summary["conflicting_metrics"] == 1
    assert summary["requests_made"] == len(session.calls) == 4 and summary["pending_requests"] == 0
    reason = {"reason": "conflicting_accounts", "alias": alias}
    stored = read_rows(archive(tmp_path))[0]
    assert stored[metric] is None and stored["cumulative"][metric] is None
    assert stored["missing_reasons"] == {metric: reason}
    frame = load_quarterly("2024-05-16", data_dir=tmp_path)
    assert pd.isna(frame.iloc[0][metric]) and frame.iloc[0]["missing_reasons"] == {metric: reason}
    for other in module.ACCOUNTS:
        if other != metric:
            assert pd.notna(frame.iloc[0][other])


def test_account_conflicts_do_not_stop_other_companies_or_later_batches(tmp_path):
    companies = [{"corp_code": f"{i:08d}", "stock_code": f"{i:06d}"} for i in range(1, 102)]
    rows = filing("11013", corp="00000001")
    rows.append({**rows[0], "thstrm_amount": "200"})
    rows.extend(filing("11013", corp="00000002"))
    summary, session = run(tmp_path, payload(*rows), payload(*filing("11013", corp="00000101")),
        payload(*filing("11012", (150, 20, 9), cumulative=(250, 30, 14), corp="00000002")),
        *[{"status": "013"}] * 5, universe=companies)
    assert summary["status"] == "ok" and summary["conflicting_metrics"] == 1
    assert summary["requests_made"] == len(session.calls) == 8
    assert summary["records_saved"] == 4 and summary["pending_requests"] == 0
    frame = load_quarterly("2024-08-15", data_dir=tmp_path).set_index(["corp_code", "fiscal_quarter"])
    assert pd.isna(frame.loc[("00000001", "2024Q1"), "revenue"])
    assert frame.loc[("00000002", "2024Q1"), "revenue"] == 100
    assert frame.loc[("00000101", "2024Q1"), "revenue"] == 100
    assert frame.loc[("00000002", "2024Q2"), "revenue"] == 150
    resumed, session = run(tmp_path, universe=companies)
    assert resumed["requests_made"] == resumed["conflicting_metrics"] == 0 and session.calls == []


@pytest.mark.parametrize("field,value", [("currency", "USD"), ("thstrm_dt", "2024.01.01 ~ 2024.03.31")])
def test_duplicate_account_metadata_conflicts_still_fail_validation(tmp_path, field, value):
    rows = filing("11013")
    rows.append({**rows[0], field: value})
    summary, _ = run(tmp_path, payload(*rows))
    assert summary["status"] == "error" and summary["pending_requests"] == 4
    assert not archive(tmp_path).exists()


def test_direct_q2_q3_and_annual_minus_nine_months_q4(tmp_path):
    reports = [filing("11013"), filing("11012", (150, 20, 9), cumulative=(250, 30, 14)),
               filing("11014", (200, 30, 15), cumulative=(450, 60, 29)),
               filing("11011", (700, 90, 40))]
    summary, _ = run(tmp_path, *[payload(*rows) for rows in reports])
    assert summary["status"] == "ok" and summary["records_saved"] == 4
    frame = load_quarterly("2025-04-01", data_dir=tmp_path).set_index("fiscal_quarter")
    assert frame[["revenue", "operating_income", "net_income"]].values.tolist() == [
        [100, 10, 5], [150, 20, 9], [200, 30, 15], [250, 30, 11]]
    stored = read_rows(archive(tmp_path, "11011"))[0]
    assert stored["derivation_inputs"]["revenue"] == DAYS["11014"] + "000001"
    assert stored["missing_reasons"] == {}


@pytest.mark.parametrize("dated_amount", [False, True])
def test_cumulative_only_q2_q3_subtract_previous_ytd(tmp_path, dated_amount):
    if dated_amount:
        q2 = filing("11012", (250, 30, 14), period="2024.01.01 ~ 2024.06.30")
        q3 = filing("11014", (450, 60, 29), period="2024.01.01 ~ 2024.09.30")
    else:
        q2 = filing("11012", (None, None, None), cumulative=(250, 30, 14))
        q3 = filing("11014", (None, None, None), cumulative=(450, 60, 29))
    run(tmp_path, payload(*filing("11013")), payload(*q2), payload(*q3), payload(*filing("11011", (700, 90, 40))))
    frame = load_quarterly("2025-04-01", data_dir=tmp_path).set_index("fiscal_quarter")
    assert frame.loc["2024Q2", "revenue"] == 150
    assert frame.loc["2024Q3", "revenue"] == 200
    assert frame.loc["2024Q4", "revenue"] == 250


def test_dated_three_month_amounts_reconstruct_missing_ytd(tmp_path):
    q2 = filing("11012", (150, 20, 9), period="2024-04-01 ~ 2024-06-30")
    q3 = filing("11014", (200, 30, 15), period="2024/07/01 ~ 2024/09/30")
    run(tmp_path, payload(*filing("11013")), payload(*q2), payload(*q3), payload(*filing("11011", (700, 90, 40))))
    assert read_rows(archive(tmp_path, "11014"))[0]["cumulative"] == {
        "revenue": 450, "operating_income": 60, "net_income": 29}
    assert load_quarterly("2025-04-01", data_dir=tmp_path).iloc[-1]["revenue"] == 250


@pytest.mark.parametrize("report,reason,prior_report,currency", [
    ("11012", "missing_q1_cumulative", None, "KRW"),
    ("11014", "missing_q2_cumulative", None, "KRW"),
    ("11011", "missing_q3_cumulative", None, "KRW"),
    ("11012", "currency_mismatch", "11013", "USD"),
    ("11012", "missing_currency_for_derivation", "11013", None),
])
def test_missing_derivation_inputs_or_incompatible_currency_remain_nan(tmp_path, report, reason, prior_report, currency):
    responses = []
    for code in module.REPORTS:
        if code == report:
            rows = filing(code, (300, 30, 15), currency=currency) if code == "11011" else filing(code, (None, None, None), cumulative=(300, 30, 15), currency=currency)
            responses.append(payload(*rows))
        elif code == prior_report:
            responses.append(payload(*filing(code)))
        else:
            responses.append({"status": "013"})
    run(tmp_path, *responses)
    stored = read_rows(archive(tmp_path, report))[0]
    assert stored["revenue"] is None and stored["missing_reasons"]["revenue"] == reason
    frame = load_quarterly("2025-04-01", data_dir=tmp_path).set_index("fiscal_quarter")
    assert pd.isna(frame.loc[f"2024Q{module.REPORTS[report]}", "revenue"])


@pytest.mark.parametrize("report,amount,cumulative,reason", [
    ("11012", 300, None, "ambiguous_thstrm_period"),
    ("11014", None, None, "missing_or_invalid_cumulative"),
    ("11011", None, None, "missing_or_invalid_annual_cumulative"),
])
def test_unknown_period_or_missing_current_input_is_not_guessed(tmp_path, report, amount, cumulative, reason):
    responses = [payload(*filing(code, (amount, amount, amount), cumulative=cumulative))
                 if code == report else {"status": "013"} for code in module.REPORTS]
    run(tmp_path, *responses)
    stored = read_rows(archive(tmp_path, report))[0]
    assert stored["revenue"] is None and stored["missing_reasons"]["revenue"] == reason


def test_corrections_keep_all_versions_and_exclude_receipt_day(tmp_path):
    rows = [*filing("11013"), *filing("11013", (120, 12, 6), day="20240620", number=2)]
    run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    assert len(read_rows(archive(tmp_path))) == 2
    assert load_quarterly("2024-05-15", data_dir=tmp_path).empty
    for session, expected in [("2024-05-16", 100), ("2024-06-20", 100), ("2024-06-21", 120)]:
        assert load_quarterly(session, data_dir=tmp_path).iloc[0]["revenue"] == expected
    assert load_quarterly("2024-06-20T16:00:00Z", data_dir=tmp_path).iloc[0]["revenue"] == 120


def test_later_correction_of_q1_does_not_leak_into_earlier_q2_derivation(tmp_path):
    q1 = [*filing("11013"), *filing("11013", (120, 12, 6), day="20240901", number=2)]
    q2 = filing("11012", (None, None, None), cumulative=(250, 30, 14))
    run(tmp_path, payload(*q1), payload(*q2), *[{"status": "013"}] * 2)
    frame = load_quarterly("2024-09-02", data_dir=tmp_path).set_index("fiscal_quarter")
    assert frame.loc["2024Q1", "revenue"] == 120
    assert frame.loc["2024Q2", "revenue"] == 150
    assert read_rows(archive(tmp_path, "11012"))[0]["derivation_inputs"]["revenue"] == "20240515000001"


def test_same_day_correction_uses_last_receipt_and_ofs_only_until_cfs_known(tmp_path):
    rows = [*filing("11013", (80, 8, 4), division="OFS"),
            *filing("11013", day="20240517", number=1),
            *filing("11013", (120, 12, 6), day="20240517", number=2)]
    run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    before = load_quarterly("2024-05-17", data_dir=tmp_path).iloc[0]
    assert before["fs_div"] == "OFS" and before["revenue"] == 80
    after = load_quarterly("2024-05-20", data_dir=tmp_path).iloc[0]
    assert after["fs_div"] == "CFS" and after["revenue"] == 120


def test_changed_universe_batch_does_not_skip_new_companies_or_delete_saved_versions(tmp_path):
    run(tmp_path, payload(*filing("11013")), *[{"status": "013"}] * 3)
    expanded = [*UNIVERSE, {"corp_code": "00000002", "stock_code": "000002"}]
    summary, session = run(tmp_path, payload(*filing("11013", (120, 12, 6), day="20240620", number=2)),
        *[{"status": "013"}] * 3, universe=expanded)
    assert summary["requests_made"] == len(session.calls) == 4
    assert len(read_rows(archive(tmp_path))) == 2
    assert load_quarterly("2024-06-01", data_dir=tmp_path).iloc[0]["revenue"] == 100
    assert load_quarterly("2024-06-21", data_dir=tmp_path).iloc[0]["revenue"] == 120


def test_daily_quota_stops_same_day_rerun_and_resumes_after_kst_midnight(tmp_path):
    now = datetime(2026, 10, 5, 14, 59, tzinfo=timezone.utc)
    first, session = run(tmp_path, payload(*filing("11013")), {"status": "013"}, max_requests=2, now=now)
    assert first["status"] == "quota_reached" and first["reason"] == "daily_request_budget"
    assert first["requests_made"] == len(session.calls) == 2 and first["pending_requests"] == 2
    again, session = run(tmp_path, max_requests=2, now=now)
    assert again["status"] == "quota_reached" and again["requests_made"] == 0 and session.calls == []
    resumed, session = run(tmp_path, *[{"status": "013"}] * 2, max_requests=2, now=now + timedelta(minutes=1))
    assert resumed["status"] == "ok" and resumed["requests_made"] == len(session.calls) == 2
    assert resumed["pending_requests"] == 0
    quota = json.loads((root(tmp_path) / "_quota.json").read_text())["days"]
    assert quota == {"2026-10-05": {"requests": 2, "provider_limited": False},
                     "2026-10-06": {"requests": 2, "provider_limited": False}}


def test_provider_020_stops_without_completion_and_resumes_next_day(tmp_path):
    first, session = run(tmp_path, {"status": "020", "message": KEY})
    assert first["status"] == "quota_reached" and first["reason"] == "provider_status_020"
    assert first["requests_made"] == len(session.calls) == 1 and first["pending_requests"] == 4
    assert not (root(tmp_path) / "_state.json").exists()
    again, session = run(tmp_path)
    assert again["status"] == "quota_reached" and again["requests_made"] == 0 and session.calls == []
    resumed, session = run(tmp_path, *[{"status": "013"}] * 4, now=START + timedelta(days=1))
    assert resumed["status"] == "ok" and resumed["requests_made"] == len(session.calls) == 4
    assert resumed["pending_requests"] == 0


@pytest.mark.parametrize("response", [
    {"status": "010", "message": KEY}, {"status": "800", "message": KEY}, {}, [],
    {"status": "000", "list": []}, {"status": "000", "list": {}},
    {"status": "000", "list": [None]}, {"status": "013", "list": [{"message": KEY}]},
    ValueError(KEY), RuntimeError(f"transport?crtfc_key={KEY}"),
])
def test_invalid_payload_and_transport_errors_are_redacted_and_remain_pending(tmp_path, response, capsys):
    summary, session = run(tmp_path, response)
    assert summary["status"] == "error" and summary["pending_requests"] == 4
    assert summary["requests_made"] == len(session.calls) == 1
    assert KEY not in json.dumps(summary) + capsys.readouterr().out
    assert not archive(tmp_path).exists() and not (root(tmp_path) / "_state.json").exists()
    assert json.loads((root(tmp_path) / "_quota.json").read_text())["days"]["2026-10-05"]["requests"] == 1


@pytest.mark.parametrize("field,value,reason", [
    ("corp_code", "99999999", "corp_not_in_batch"), ("corp_code", [], "corp_not_in_batch"),
    ("reprt_code", "11012", "reprt_code"), ("bsns_year", "2023", "bsns_year"),
    ("bsns_year", 2023, "bsns_year"), ("fs_div", "BAD", "fs_div"), ("fs_div", [], "fs_div"),
    ("account_nm", None, "account_nm"), ("account_nm", "  ", "account_nm"),
    ("currency", [], "currency_type"), ("thstrm_dt", 123, "thstrm_dt_type"),
])
def test_a_malformed_row_is_skipped_and_valid_accounts_are_saved(tmp_path, field, value, reason):
    rows = filing("11013")
    rows[-1][field] = value
    summary, _ = run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    assert summary["status"] == "ok" and summary["pending_requests"] == 0
    assert summary["records_saved"] == 1 and summary["skipped_rows"] == {reason: 1}
    assert summary["batches_with_all_rows_skipped"] == []
    sample = summary["skipped_row_samples"][reason][0]
    assert sample == {"field": field, "type": type(value).__name__, "value": value,
        "corp_code": rows[-1]["corp_code"], "reprt_code": rows[-1]["reprt_code"],
        "bsns_year": rows[-1]["bsns_year"]}
    stored = read_rows(archive(tmp_path))[0]
    assert stored["revenue"] == 100 and stored["operating_income"] == 10
    assert stored["net_income"] is None and stored["missing_reasons"] == {"net_income": "missing_account"}
    log = read_rows(root(tmp_path) / "_skipped_rows.jsonl")
    assert len(log) == 1
    assert log[0]["skipped_rows"] == summary["skipped_rows"]
    assert log[0]["skipped_row_samples"] == summary["skipped_row_samples"]


def test_malformed_receipt_remains_fatal(tmp_path):
    rows = filing("11013")
    rows[-1]["rcept_no"] = "20240230000001"
    summary, _ = run(tmp_path, payload(*rows))
    assert summary["status"] == "error" and summary["pending_requests"] == 4
    assert not archive(tmp_path).exists()


def test_all_skipped_batch_is_completed_and_listed_on_resume(tmp_path):
    rows = [{**row, "fs_div": "BAD"} for row in filing("11013")]
    summary, _ = run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    state = json.loads((root(tmp_path) / "_state.json").read_text())
    skipped = summary["batches_with_all_rows_skipped"]
    assert summary["status"] == "ok" and summary["pending_requests"] == 0
    assert summary["records_saved"] == 0 and summary["skipped_rows"] == {"fs_div": 3}
    assert len(skipped) == 1 and skipped[0].startswith("2024_11013_")
    assert state["batches_with_all_rows_skipped"] == skipped
    assert skipped[0] in state["completed_batches"]
    assert archive(tmp_path).exists() and read_rows(archive(tmp_path)) == []
    resumed, session = run(tmp_path)
    assert session.calls == [] and resumed["batches_with_all_rows_skipped"] == skipped
    assert resumed["skipped_rows"] == resumed["skipped_row_samples"] == {}
    assert len(read_rows(root(tmp_path) / "_skipped_rows.jsonl")) == 1


@pytest.mark.parametrize("retry_all", [False, True])
def test_retry_batches_requests_completed_batches_and_clears_skipped_marker(tmp_path, retry_all):
    rows = [{**row, "fs_div": "BAD"} for row in filing("11013")]
    first, _ = run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    retry = [] if retry_all else first["batches_with_all_rows_skipped"]
    responses = [payload(*filing("11013"))] + ([{"status": "013"}] * 3 if retry_all else [])
    summary, session = run(tmp_path, *responses, retry_batches=retry)
    assert summary["status"] == "ok" and summary["pending_requests"] == 0
    assert summary["requests_made"] == len(session.calls) == (4 if retry_all else 1)
    assert summary["records_saved"] == 1 and summary["batches_with_all_rows_skipped"] == []
    assert read_rows(archive(tmp_path))[0]["revenue"] == 100
    assert json.loads((root(tmp_path) / "_state.json").read_text())["batches_with_all_rows_skipped"] == []
    resumed, session = run(tmp_path)
    assert resumed["requests_made"] == 0 and session.calls == []


def test_skipped_samples_are_bounded_per_reason_per_run_redacted_and_appended(tmp_path, capsys):
    base = filing("11013")[0]
    rows = [{**base, "account_nm": {"number": i, "text": KEY, "api_key": KEY,
        "url": f"{module.URL}?crtfc_key={KEY}"}} for i in range(25)]
    rows.extend({**base, "currency": i} for i in range(25))
    rows.append({**base, "fs_div": "BAD", "currency": 42})
    first, _ = run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    assert first["skipped_rows"] == {"account_nm": 25, "currency_type": 25, "fs_div": 1}
    assert {reason: len(samples) for reason, samples in first["skipped_row_samples"].items()} == {
        "account_nm": 20, "currency_type": 20, "fs_div": 1}
    sample = first["skipped_row_samples"]["account_nm"][0]
    assert sample["type"] == "dict" and sample["value"] == {
        "number": 0, "text": "[REDACTED]", "url": "[REDACTED_URL]"}
    second, _ = run(tmp_path, payload(*rows), retry_batches=first["batches_with_all_rows_skipped"])
    log = read_rows(root(tmp_path) / "_skipped_rows.jsonl")
    assert len(log) == 2 and log[0]["skipped_rows"] == log[1]["skipped_rows"] == first["skipped_rows"]
    assert log[0]["skipped_row_samples"] == first["skipped_row_samples"]
    assert log[1]["skipped_row_samples"] == second["skipped_row_samples"]
    saved = "".join(path.read_text() for path in root(tmp_path).iterdir())
    output = saved + json.dumps(first) + json.dumps(second) + capsys.readouterr().out
    assert KEY not in output and module.URL not in output and "crtfc_key" not in output


def test_failed_retry_stays_pending_and_preserves_existing_completion(tmp_path):
    run(tmp_path, *[{"status": "013"}] * 4)
    summary, _ = run(tmp_path, {"status": "000", "list": []}, retry_batches=[])
    assert summary["status"] == "error" and summary["pending_requests"] == 4
    assert len(json.loads((root(tmp_path) / "_state.json").read_text())["completed_batches"]) == 4


def test_retry_all_only_refreshes_requested_years_and_dry_run_does_not_write(tmp_path, capsys):
    initial = FakeSession(*[{"status": "013"}] * 8)
    backfill(2024, 2025, execute=True, api_key=KEY, session=initial, data_dir=tmp_path,
        universe_source=UNIVERSE, clock=lambda: START)
    before = {path.name: path.read_text() for path in root(tmp_path).iterdir()}
    planned = backfill(2025, 2025, data_dir=tmp_path, universe_source=UNIVERSE, retry_batches=["all"])
    assert planned["pending_requests"] == 4
    assert {path.name: path.read_text() for path in root(tmp_path).iterdir()} == before
    assert json.loads(capsys.readouterr().out) == planned
    session = FakeSession(*[{"status": "013"}] * 4)
    summary = backfill(2025, 2025, execute=True, api_key=KEY, session=session, data_dir=tmp_path,
        universe_source=UNIVERSE, clock=lambda: START, retry_batches=["all"])
    assert summary["status"] == "ok" and summary["pending_requests"] == 0
    assert summary["requests_made"] == len(session.calls) == 4
    assert {kwargs["params"]["bsns_year"] for _, kwargs in session.calls} == {"2025"}
    state = json.loads((root(tmp_path) / "_state.json").read_text())
    assert len(state["completed_batches"]) == 8


def test_skipped_diagnostics_are_written_even_if_a_later_batch_is_fatal(tmp_path):
    rows = [{**row, "fs_div": "BAD"} for row in filing("11013")]
    summary, _ = run(tmp_path, payload(*rows), {"status": "000", "list": []})
    assert summary["status"] == "error" and summary["pending_requests"] == 3
    assert read_rows(root(tmp_path) / "_skipped_rows.jsonl")[0]["skipped_rows"] == {"fs_div": 3}


def test_key_is_redacted_from_raw_fields_archives_and_checkpoints(tmp_path, capsys):
    rows = filing("11013", currency=f"KRW {KEY}")
    response = {**payload(*rows), "message": KEY, "crtfc_key": KEY, "api_key": "other-secret"}
    summary, _ = run(tmp_path, response, *[{"status": "013"}] * 3)
    assert summary["status"] == "ok"
    saved = "".join(path.read_text() for path in root(tmp_path).iterdir())
    assert KEY not in saved + json.dumps(summary) + capsys.readouterr().out
    assert "other-secret" not in saved
    assert read_rows(archive(tmp_path))[0]["raw_accounts"]["revenue"]["currency"] == "KRW [REDACTED]"


@pytest.mark.parametrize("previous,current,expected", [
    (10, -1, -200), (-10, -20, -200), (-10, -5, 50), (-10, 0, 100),
    (-10, 20, 300), (-10, -10, 0), (10, 80, 500), (0, -1, -200),
    (0, 10, None), (None, 10, None), (10, None, None),
])
def test_loss_conventions_cap_and_zero_or_missing_denominator(previous, current, expected):
    frame = pd.DataFrame([{"corp_code": CORP, "fiscal_quarter": quarter, "revenue": value, "operating_income": value}
        for quarter, value in [("2023Q1", previous), ("2024Q1", current)]])
    result = yoy_signals(frame)
    for name in ("revenue_yoy", "operating_income_yoy"):
        if expected is None:
            assert pd.isna(result.iloc[-1][name])
        else:
            assert result.iloc[-1][name] == expected
    assert "phase" not in frame


@pytest.mark.parametrize("revenues,last_income,phase,leverage", [
    ([80, 100, 110, 120, 130, 150], 12, 1, False),
    ([80, 100, 110, 120, 130, 150], 15, 1, False),
    ([80, 100, 110, 120, 130, 150], 20, 2, True),
    ([80, 100, 200, 120, 130, 150], 12, 3, False),
    ([80, 100, 110, 90, 85, 80], 5, 4, False),
    ([80, 100, 110, 90, 85, 80], 9, None, False),
    ([80, 100, 110, 120, 130, 100], 20, None, False),
])
def test_exact_phase_rules(revenues, last_income, phase, leverage):
    quarters = ("2023Q1", "2023Q2", "2023Q3", "2023Q4", "2024Q1", "2024Q2")
    incomes = [10, 10, 11, 12, 13, last_income]
    frame = pd.DataFrame([{"corp_code": CORP, "fiscal_quarter": quarter, "revenue": revenue, "operating_income": income}
                          for quarter, revenue, income in zip(quarters, revenues, incomes)])
    result = yoy_signals(frame.iloc[::-1])
    target = result.iloc[0]
    assert target["operating_leverage"] == leverage
    assert pd.isna(target["phase"]) if phase is None else target["phase"] == phase
    assert str(result["phase"].dtype) == "Int64"


def test_phase_one_requires_six_consecutive_quarters_not_six_alias_rows():
    rows = [{"corp_code": CORP, "stock_code": stock, "fiscal_quarter": quarter,
             "revenue": revenue, "operating_income": 10}
            for stock in (STOCK, "005935") for quarter, revenue in
            [("2023Q1", 100), ("2023Q4", 110), ("2024Q1", 120)]]
    result = yoy_signals(pd.DataFrame(rows))
    target = result[result["fiscal_quarter"] == "2024Q1"]
    assert target["revenue_yoy"].tolist() == [20, 20]
    assert target["phase"].tolist() == [3, 3]
    assert yoy_signals(pd.DataFrame(columns=("corp_code", "fiscal_quarter", "revenue", "operating_income"))).empty


def test_missing_same_quarter_cannot_use_an_adjacent_quarter_or_another_corp():
    frame = pd.DataFrame([
        {"corp_code": CORP, "fiscal_quarter": "2023Q2", "revenue": 50, "operating_income": 5},
        {"corp_code": "00000002", "fiscal_quarter": "2023Q1", "revenue": 50, "operating_income": 5},
        {"corp_code": CORP, "fiscal_quarter": "2024Q1", "revenue": 100, "operating_income": 20},
    ])
    result = yoy_signals(frame)
    assert pd.isna(result.iloc[-1]["revenue_yoy"]) and pd.isna(result.iloc[-1]["phase"])


def test_conflicting_quarter_versions_cannot_be_used_as_signal_input():
    rows = [{"corp_code": CORP, "fiscal_quarter": "2024Q1", "revenue": revenue, "operating_income": 10}
            for revenue in (100, 120)]
    with pytest.raises(ValueError, match="one financial version"):
        yoy_signals(pd.DataFrame(rows))


def test_cli_dry_run_ignores_environment_and_passes_required_options(monkeypatch, capsys):
    monkeypatch.setenv("HQA_DATA_DIR", "/must-not-use-env-in-dry-run")
    monkeypatch.setenv("DART_API_KEY", KEY)
    monkeypatch.setattr(sys, "argv", ["dart_quarterly", "--from-year", "2015", "--to-year", "2026", "--max-requests", "25"])
    collect = Mock(return_value={"status": "dry_run"})
    monkeypatch.setattr(cli, "backfill", collect)
    cli.main()
    collect.assert_called_once_with(2015, 2026, execute=False, max_requests=25, api_key=None, data_dir=module.DEFAULT_DATA_DIR)
    assert capsys.readouterr().out == ""


def test_cli_execute_loads_env_and_reports_failure_without_key(monkeypatch, tmp_path, capsys):
    load_env = Mock()
    monkeypatch.setattr(cli, "load_project_env", load_env)
    monkeypatch.setenv("DART_API_KEY", KEY)
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["dart_quarterly", "--from-year", "2015", "--to-year", "2016", "--execute"])
    collect = Mock(side_effect=ValueError(KEY))
    monkeypatch.setattr(cli, "backfill", collect)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    load_env.assert_called_once_with()
    collect.assert_called_once_with(2015, 2016, execute=True, max_requests=3000, api_key=KEY, data_dir=tmp_path)
    assert KEY not in capsys.readouterr().out


@pytest.mark.parametrize("selection", ["listed", "all", "bare"])
def test_module_cli_retry_batches_re_requests_completed_jobs(monkeypatch, tmp_path, capsys, selection):
    rows = [{**row, "account_nm": None} for row in filing("11013")]
    first, _ = run(tmp_path, payload(*rows), *[{"status": "013"}] * 3)
    options = first["batches_with_all_rows_skipped"] if selection == "listed" else (["all"] if selection == "all" else [])
    session = FakeSession(payload(*filing("11013")), *([{"status": "013"}] * 3 if selection != "listed" else []))
    load_env = Mock()
    monkeypatch.setattr("src.config.settings.load_project_env", load_env)
    monkeypatch.setenv("DART_API_KEY", KEY)
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["dart_quarterly", "--from-year", "2024", "--to-year", "2024",
        "--execute", "--retry-batches", *options])

    def offline_backfill(*args, **kwargs):
        return backfill(*args, **kwargs, session=session, universe_source=UNIVERSE, clock=lambda: START)

    monkeypatch.setattr(module, "backfill", offline_backfill)
    module.main()
    load_env.assert_called_once_with()
    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["status"] == "ok" and summary["pending_requests"] == 0
    assert summary["batches_with_all_rows_skipped"] == [] and summary["records_saved"] == 1
    assert summary["requests_made"] == len(session.calls) == (1 if selection == "listed" else 4)
    assert KEY not in output


def test_module_cli_dry_run_passes_retry_option_without_loading_env(monkeypatch, capsys):
    monkeypatch.setenv("HQA_DATA_DIR", "/must-not-use-env-in-dry-run")
    monkeypatch.setenv("DART_API_KEY", KEY)
    monkeypatch.setattr(sys, "argv", ["dart_quarterly", "--from-year", "2025", "--to-year", "2026", "--retry-batches"])
    collect = Mock(return_value={"status": "dry_run"})
    monkeypatch.setattr(module, "backfill", collect)
    module.main()
    collect.assert_called_once_with(2025, 2026, execute=False, max_requests=3000, api_key=None,
        data_dir=module.DEFAULT_DATA_DIR, retry_batches=[])
    assert capsys.readouterr().out == ""
