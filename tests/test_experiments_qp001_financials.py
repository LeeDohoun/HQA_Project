"""Receipt-exact QP001 financial fixtures; no real prices or network."""
from datetime import datetime, timezone
import json

import pandas as pd
import pytest

from backtesting.experiments.qp001.financials import AssetArchive, ProfitabilityVersions, link_coverage
from research_data import dart_multi_raw as raw
from src.ingestion import dart_quarterly as quarterly
from src.ingestion.dart_financial_date_rules import FilingDates
from src.ingestion.storage import write_rows


def version(year, quarter, income, *, division="CFS", receipt=None, currency="KRW"):
    report = {v: k for k, v in quarterly.REPORTS.items()}[quarter]
    receipt = receipt or f"{year + (quarter == 4)}{[5, 8, 11, 3][quarter - 1]:02d}10000001"
    end = pd.Period(f"{year}Q{quarter}").end_time.date().isoformat()
    return {"corp_code": "00000001", "stock_codes": ["000010"], "bsns_year": year, "reprt_code": report,
        "fiscal_quarter": f"{year}Q{quarter}", "fs_div": division, "rcept_no": receipt,
        "available_date": pd.Timestamp(receipt[:8]).date().isoformat(), "currency": currency,
        "raw_accounts": {metric: {"account_nm": quarterly.ACCOUNTS[metric][0], "thstrm_amount": value,
            "thstrm_dt": f"{year}-01-01~{end}", "currency": currency, "fs_div": division, "rcept_no": receipt}
            for metric, value in (("revenue", 100), ("operating_income", income), ("net_income", 5))}}


def install(root, records, *, amounts=None, currencies=None, raw_income=None):
    amounts, currencies = amounts or {}, currencies or {}
    groups = {}
    for record in records:
        groups.setdefault((record["bsns_year"], record["reprt_code"]), []).append(record)
    directory = root / "fundamentals/dart_multi_raw"
    directory.mkdir(parents=True)
    universe = {"00000001": ["000010"]}
    (directory / "_universe.json").write_text(json.dumps({"universe": universe}))
    for (year, report), rows in groups.items():
        accounts = []
        for row in rows:
            identity = {key: row[key] for key in raw.VERSION_FIELDS}
            for metric, account in row["raw_accounts"].items():
                value = {**identity, **account, "sj_div": "IS"}
                if raw_income is not None and metric == "operating_income":
                    value["thstrm_amount"] = raw_income
                accounts.append(value)
            key = (row["fiscal_quarter"], row["fs_div"], row["rcept_no"])
            accounts.append({**identity, "sj_div": "BS", "account_nm": "자산총계",
                "currency": currencies.get(key, row["currency"]), "thstrm_amount": amounts.get(key, 200)})
        payload = {"status": "000", "list": accounts}
        job = raw.Job(year, report, ("00000001",))
        metadata, _ = raw.store_response(directory, job, json.dumps(payload).encode(), 200, datetime.now(timezone.utc))
        derived = directory / "derived" / (job.key + ".jsonl")
        derived.parent.mkdir(exist_ok=True)
        derived.write_bytes(b"".join(raw._encoded(row) for row in raw.derive_rows(payload, job, universe, metadata["sha256"])))
        write_rows(root / "fundamentals/dart_quarterly" / f"{year}_{report}.jsonl", rows)
    listings = [{"corp_code": row["corp_code"], "stock_code": "000010", "rcept_no": row["rcept_no"],
        "rcept_dt": row["rcept_no"][:8], "report_nm": f"{'사업' if row['reprt_code'] == '11011' else '반기' if row['reprt_code'] == '11012' else '분기'}보고서 ({row['bsns_year']}.{quarterly.REPORTS[row['reprt_code']] * 3:02d})"}
        for row in records]
    return FilingDates(listings)


def five(division="CFS"):
    return [version(2022, 4, 8, division=division)] + [version(2023, q, value, division=division)
        for q, value in enumerate((10, 30, 60, 100), 1)]


def loader(root, records, *, rule="S", cutoff="2026-01-01", dates=None, **kwargs):
    installed = install(root, records, **kwargs)
    return ProfitabilityVersions(root, dates or installed, rule, cutoff=cutoff, sample_end="2025-11-28", assets=AssetArchive(root))


def test_cumulative_q2_q3_and_annual_quarterisation_and_exact_assets(tmp_path):
    values = loader(tmp_path, five())
    frame = values.load("2025-01-01").set_index("fiscal_quarter")
    assert frame.loc[["2023Q1", "2023Q2", "2023Q3", "2023Q4"], "operating_income"].tolist() == [10, 20, 30, 40]
    result = values.roa("00000001", "2023Q4", "2025-01-01")
    assert result["roa"] == 0.5
    assert result["fs_div"] == "CFS"
    assert len(set(result["receipts"])) == 5


def test_direct_three_month_and_add_amount_follow_existing_derive(tmp_path):
    records = five()
    for q, amount, added in ((2, 20, 30), (3, 30, 60)):
        account = records[q]["raw_accounts"]["operating_income"]
        account.update(thstrm_amount=amount, thstrm_add_amount=added,
                       thstrm_dt=f"2023-{(q - 1) * 3 + 1:02d}-01~2023-{q * 3:02d}-30")
    assert loader(tmp_path, records).roa("00000001", "2023Q4", "2025-01-01")["roa"] == .5


def test_correction_dates_are_applied_before_derivation(tmp_path):
    original = version(2023, 1, 10)
    correction = version(2023, 1, 15, receipt="20230901000001")
    second = version(2023, 2, 30)
    dates = install(tmp_path, [original, correction, second])
    dates.receipts[correction["rcept_no"]]["report_nm"] = "[정정]분기보고서 (2023.03)"
    # Rebuild the original-link index using the final titles.
    dates = FilingDates(dates.receipts.values())
    s = ProfitabilityVersions(tmp_path, dates, "S", cutoff="2024-01-01", sample_end="2023-12-28", assets=AssetArchive(tmp_path))
    o = ProfitabilityVersions(tmp_path, dates, "O", cutoff="2024-01-01", sample_end="2023-12-28", assets=AssetArchive(tmp_path))
    sframe, oframe = [v.load("2023-08-31").set_index("fiscal_quarter") for v in (s, o)]
    assert sframe.loc["2023Q2", "operating_income"] == 20
    assert oframe.loc["2023Q2", "operating_income"] == 15
    assert s.load("2023-09-29").set_index("fiscal_quarter").loc["2023Q2", "operating_income"] == 20


@pytest.mark.parametrize("rule", ["S", "O"])
def test_actual_cutoff_removes_future_correction_before_backdating_and_dependencies(tmp_path, rule):
    first = version(2023, 1, 100, receipt="20240101000001")
    second = version(2023, 2, 250)
    dates = install(tmp_path, [first, second])
    original = {**dates.receipts[first["rcept_no"]], "rcept_no": "20230510000002", "rcept_dt": "20230510"}
    dates.receipts[first["rcept_no"]]["report_nm"] = "[정정]분기보고서 (2023.03)"
    dates = FilingDates([*dates.receipts.values(), original])
    values = ProfitabilityVersions(tmp_path, dates, rule, cutoff="2024-01-01", sample_end="2023-12-28", assets=AssetArchive(tmp_path))
    frame = values.load("2023-12-28")
    assert frame.fiscal_quarter.tolist() == ["2023Q2"]
    assert pd.isna(frame.operating_income.iloc[0])
    assert frame.missing_reasons.iloc[0]["operating_income"] == "missing_q1_cumulative"
    validation = ProfitabilityVersions(tmp_path, dates, rule, cutoff="2026-01-01", sample_end="2025-11-28", assets=AssetArchive(tmp_path))
    assert "2023Q1" in validation.load("2024-02-01").fiscal_quarter.tolist()


def test_missing_cfs_income_falls_back_to_entire_ofs_and_never_mixes(tmp_path):
    cfs, ofs = five(), five("OFS")
    cfs[2]["raw_accounts"]["operating_income"]["thstrm_amount"] = None
    values = loader(tmp_path, cfs + ofs)
    result = values.roa("00000001", "2023Q4", "2025-01-01")
    assert result["roa"] == .5 and result["fs_div"] == "OFS" and result["fallback"]
    # AV phase loader retains its usual CFS priority, including the missing OP.
    assert set(values.load("2025-01-01").fs_div) == {"CFS"}
    assert pd.isna(values.load("2025-01-01").set_index("fiscal_quarter").loc["2023Q2", "operating_income"])


def test_incomplete_ofs_cannot_borrow_good_cfs_quarter(tmp_path):
    records = five() + five("OFS")
    records[2]["raw_accounts"]["operating_income"]["thstrm_amount"] = None
    records[8]["raw_accounts"]["operating_income"]["thstrm_amount"] = None
    assert loader(tmp_path, records).roa("00000001", "2023Q4", "2025-01-01")["roa"] is None


def test_no_fallback_to_older_q_or_older_receipt_assets(tmp_path):
    values = loader(tmp_path, five())
    assert values.roa("00000001", "2023Q4", "2025-01-01")["roa"] == .5
    assert values.roa("00000001", "2024Q1", "2025-01-01")["roa"] is None


def test_latest_receipt_missing_assets_does_not_borrow_older_receipt(tmp_path):
    records = five()
    corrected = version(2023, 4, 101, receipt="20240901000001")
    records.append(corrected)
    key = ("2023Q4", "CFS", corrected["rcept_no"])
    values = loader(tmp_path, records, amounts={key: None})
    assert values.roa("00000001", "2023Q4", "2024-08-30")["roa"] == .5
    result = values.roa("00000001", "2023Q4", "2024-10-01")
    assert result["roa"] is None
    assert result["failures"]["CFS"] == "invalid_total_assets"


def test_missing_cfs_endpoint_uses_whole_ofs_not_mixed_assets(tmp_path):
    records = five() + five("OFS")
    key = ("2023Q4", "CFS", records[4]["rcept_no"])
    values = loader(tmp_path, records, amounts={key: None})
    result = values.roa("00000001", "2023Q4", "2025-01-01")
    assert result["fs_div"] == "OFS" and result["roa"] == .5


@pytest.mark.parametrize("rule", ["S", "O"])
def test_validation_actual_2026_cutoff_blocks_backdated_values(tmp_path, rule):
    row = version(2025, 1, 99, receipt="20260101000001")
    dates = install(tmp_path, [row])
    corrected = {**dates.receipts[row["rcept_no"]], "report_nm": "[정정]분기보고서 (2025.03)"}
    original = {**corrected, "rcept_no": "20250510000002", "rcept_dt": "20250510", "report_nm": "분기보고서 (2025.03)"}
    dates = FilingDates([original, corrected])
    values = ProfitabilityVersions(tmp_path, dates, rule, cutoff="2026-01-01", sample_end="2025-11-28", assets=AssetArchive(tmp_path))
    assert values.load("2025-11-28").empty


@pytest.mark.parametrize("failure", ["currency", "zero", "missing"])
def test_denominator_same_receipt_currency_and_positive_average(tmp_path, failure):
    records = five()
    key = (records[-1]["fiscal_quarter"], "CFS", records[-1]["rcept_no"])
    kwargs = {"currencies": {key: "USD"}} if failure == "currency" else {
        "amounts": {(r["fiscal_quarter"], "CFS", r["rcept_no"]): 0 if failure == "zero" else None for r in records}}
    values = loader(tmp_path, records, **kwargs)
    assert values.roa("00000001", "2023Q4", "2025-01-01")["roa"] is None


def test_op_conflict_stops_even_precheck(tmp_path):
    records = [version(2023, 1, 10)]
    install(tmp_path, records, raw_income=11)
    with pytest.raises(ValueError, match="OP integrity conflict"):
        link_coverage(tmp_path, AssetArchive(tmp_path))


def test_derived_archive_is_verified_without_repair(tmp_path):
    install(tmp_path, [version(2023, 1, 10)])
    path = next((tmp_path / "fundamentals/dart_multi_raw/derived").glob("*.jsonl"))
    path.write_text("{}\n")
    with pytest.raises(ValueError, match="differs from raw"):
        link_coverage(tmp_path, AssetArchive(tmp_path))
    assert path.read_text() == "{}\n"


def test_link_report_by_year_and_listing_includes_corrections(tmp_path):
    records = [version(2023, 1, 10), version(2023, 1, 11, receipt="20230901000001")]
    install(tmp_path, records)
    write_rows(tmp_path / "reference/dart_company/companies.jsonl", [{"corp_code": "00000001", "corp_cls": "E"}])
    report = link_coverage(tmp_path, AssetArchive(tmp_path))
    assert report["overall"]["matched"] == 2
    assert report["by_year_and_listing_status"]["2023"]["delisted"]["matched"] == 2
    assert report["op_scan"]["op_compared"] == 2


def test_out_of_request_raw_accounts_are_skipped_counted_and_never_fatal(tmp_path):
    import hashlib
    record = version(2023, 1, 10)
    install(tmp_path, [record])
    directory = tmp_path / "fundamentals/dart_multi_raw"
    request = next((directory / "requests").glob("*.json"))
    metadata = json.loads(request.read_text())
    payload = json.loads((directory / "blobs" / (metadata["sha256"] + ".json")).read_bytes())
    base = payload["list"][0]
    # Bad receipts in rows outside the request must not reach receipt parsing.
    payload["list"].extend([{**base, "corp_code": "99999999", "rcept_no": "BAD"},
        {**base, "bsns_year": 2022, "rcept_no": "BAD"},
        {**base, "reprt_code": "11011", "rcept_no": "BAD"}])
    body = json.dumps(payload).encode()
    metadata["sha256"] = hashlib.sha256(body).hexdigest()
    (directory / "blobs" / (metadata["sha256"] + ".json")).write_bytes(body)
    request.write_text(json.dumps(metadata))
    job = raw.Job(2023, "11013", ("00000001",))
    expected = raw.derive_rows(payload, job, {"00000001": ["000010"]}, metadata["sha256"])
    (directory / "derived" / (job.key + ".jsonl")).write_bytes(b"".join(raw._encoded(row) for row in expected))
    report = link_coverage(tmp_path, AssetArchive(tmp_path))
    assert report["overall"]["matched"] == 1 and report["op_conflicts"] == 0
    assert report["raw_skipped_rows"] == {"corp_not_in_batch": 1, "bsns_year": 1, "reprt_code": 1}
