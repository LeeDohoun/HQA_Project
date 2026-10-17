"""Registered date rules on local fixtures; legacy loader stays byte identical."""
import subprocess
import types

import numpy as np
import pandas as pd
import pytest

from backtesting.experiments import common, hc002
from src.ingestion import dart_quarterly as quarterly
from src.ingestion.dart_financial_date_rules import FilingDates, FinancialVersions
from src.ingestion.dart_listing import listing_rows
from src.ingestion.storage import write_rows
from test_experiments_hc003 import prices_for


def filing(prefix, listed=None, title="분기보고서 (2025.03)", corp="00000001", number=1):
    return {"corp_code": corp, "stock_code": "000010", "rcept_no": prefix + f"{number:06d}",
            "rcept_dt": listed or prefix, "report_nm": title}


def version(receipt, *, year=2025, report="11013", division="CFS", revenue=100, income=10, corp="00000001"):
    return {"corp_code": corp, "stock_codes": ["000010"], "bsns_year": year, "reprt_code": report,
            "fiscal_quarter": f"{year}Q{quarterly.REPORTS[report]}", "rcept_no": receipt,
            "available_date": pd.Timestamp(receipt[:8]).date().isoformat(), "fs_div": division,
            "currency": "KRW", "raw_accounts": {key: {"thstrm_amount": value, "currency": "KRW", "thstrm_dt": "2025.01.01~2025.03.31"}
                for key, value in zip(quarterly.ACCOUNTS, (revenue, income, 5))}}


def save(root, *versions):
    groups = {}
    for row in versions:
        groups.setdefault(f"{row['bsns_year']}_{row['reprt_code']}.jsonl", []).append(row)
    for name, rows in groups.items():
        write_rows(root / "fundamentals/dart_quarterly" / name, rows)


def test_all_actual_date_cases_and_global_L_by_reason_and_year(tmp_path):
    listed = filing("20250515", "20250516")
    late = filing("20241001", "20241018", "기타", number=2)
    conflict = filing("20250517")
    dates = FilingDates([listed, late, conflict, {**conflict, "report_nm": "다른 행"}])
    rows = [version(listed["rcept_no"]), version("20150501000001", year=2015),
            version("20160601000001", year=2016), version(conflict["rcept_no"]),
            version(listed["rcept_no"], corp="00000002")]
    save(tmp_path, *rows)
    result = FinancialVersions(tmp_path, dates, "S", "design_validation")
    assert dates.L == 17
    info = [dates.annotate(row) for row in rows]
    assert [row["actual_public_date"] for row in info] == ["2025-05-16", "2015-05-18", None, None, None]
    assert result.counts["conflicting_link"] == 2
    assert result.counts["unlinked_since_listing_start"] == 1
    assert result.by_year["2015"]["unlinked_pre_listing_plus_L"] == 1
    assert result.load("2025-05-16").available_date.tolist() == ["2015-05-18"]
    assert set(result.load("2025-05-17").available_date) == {"2015-05-18", "2025-05-16"}


def test_O_same_corp_type_period_and_actual_cutoffs(tmp_path):
    original = filing("20250515")
    correction = filing("20250901", "20250902", "[기재정정]분기보고서 (2025.03)")
    protected = filing("20251231", "20260101", "[정정]분기보고서 (2025.03)", number=2)
    july = filing("20260630", "20260701", "[정정]분기보고서 (2025.03)", number=3)
    wrong = [filing("20250501", title="사업보고서 (2025.03)"),
             filing("20250502", corp="00000002"), filing("20250503", title="분기보고서 (2024.03)")]
    dates = FilingDates([original, correction, protected, july, *wrong])
    save(tmp_path, version(correction["rcept_no"], revenue=200),
         version(protected["rcept_no"], revenue=999), version(july["rcept_no"], revenue=888))
    s = FinancialVersions(tmp_path, dates, "S", "design_validation")
    o = FinancialVersions(tmp_path, dates, "O", "design_validation")
    assert s.load("2025-08-29").empty
    assert o.load("2025-05-15").empty
    assert o.load("2025-05-16").revenue.tolist() == [200.0]
    assert s.load("2025-09-02").empty
    assert s.load("2025-09-03").revenue.tolist() == [200.0]
    assert o.load("2025-12-30").revenue.tolist() == [200.0]
    assert s.counts["stage_cutoff"] == o.counts["stage_cutoff"] == 2
    h = FinancialVersions(tmp_path, dates, "O", "holdout")
    assert h.load("2026-05-29").revenue.tolist() == [999.0]
    assert h.counts["stage_cutoff"] == 1
    assert dates.annotate(version(correction["rcept_no"]))["original_rcept_no"] == original["rcept_no"]
    with pytest.raises(ValueError, match="outside"):
        o.load("2026-01-01")


def test_original_choice_is_earliest_actual_public_date_not_listed_date():
    original1 = filing("20250519", "20250514")
    original2 = filing("20250516", "20250517", number=2)
    correction = filing("20250525", title="[정정]분기보고서 (2025.03)")
    dates = FilingDates([original1, original2, correction])
    result = dates.annotate(version(correction["rcept_no"]))
    assert result["assumed_public_date"] == "2025-05-17"
    assert result["original_rcept_no"] == original2["rcept_no"]


def test_unmatched_original_counts_failure_and_sensitivity_unavailable(tmp_path):
    corrected = filing("20250515", title="[정정]반기보고서 (2024.06)")
    save(tmp_path, version(corrected["rcept_no"]))
    dates = FilingDates([corrected])
    report = FinancialVersions(tmp_path, dates, "O", "design_validation").report()
    assert report["sample_correction_links"] == {"success": 0, "failure": 1, "success_rate": 0.0}
    assert report["sensitivity_available"] is False
    assert dates.annotate(version(corrected["rcept_no"]))["assumed_public_date"] == "2025-05-15"
    with pytest.raises(ValueError, match="L is undefined"):
        FilingDates([])


def test_CFS_first_OFS_fallback_and_no_missing_CFS_account_substitution(tmp_path):
    ofs, cfs = filing("20250501"), filing("20250515", number=2)
    dates = FilingDates([ofs, cfs])
    save(tmp_path, version(ofs["rcept_no"], division="OFS", revenue=150), version(cfs["rcept_no"], revenue=None))
    for rule in ("S", "O"):
        loader = FinancialVersions(tmp_path, dates, rule, "design_validation")
        assert loader.load("2025-05-15").revenue.tolist() == [150.0]
        frame = loader.load("2025-05-16")
        assert frame.fs_div.tolist() == ["CFS"] and np.isnan(frame.revenue.iloc[0])


def test_stage_cutoff_applies_to_cumulative_derivation_dependencies(tmp_path):
    q1 = filing("20260101", title="[정정]분기보고서 (2025.03)")
    q2 = filing("20250814", title="반기보고서 (2025.06)")
    dates = FilingDates([filing("20250515"), q1, q2])
    first = version(q1["rcept_no"], revenue=100)
    second = version(q2["rcept_no"], report="11012", revenue=250)
    for account in second["raw_accounts"].values():
        account["thstrm_dt"] = "2025.01.01~2025.06.30"
    save(tmp_path, first, second)
    result = FinancialVersions(tmp_path, dates, "O", "design_validation").load("2025-09-01")
    assert result.fiscal_quarter.tolist() == ["2025Q2"]
    assert np.isnan(result.revenue.iloc[0])
    assert result.missing_reasons.iloc[0]["revenue"] == "missing_q1_cumulative"


def test_conflicting_merged_rows_are_unusable_without_changing_default_reader(tmp_path):
    row = filing("20250515")
    main = tmp_path / "disclosures/dart_full/list/2025/20250515.jsonl"
    supp = tmp_path / "disclosures/dart_full/list_supplement/2025/20250515.jsonl"
    write_rows(main, [row])
    write_rows(supp, [{**row, "report_nm": "[정정]분기보고서 (2025.03)"}])
    with pytest.raises(ValueError, match="conflicting"):
        listing_rows(tmp_path, "20250515")
    dates = FilingDates.from_data(tmp_path)
    assert dates.annotate(version(row["rcept_no"]))["date_reason"] == "conflicting_link"
    assert dates.event_listings().empty


def test_any_different_merged_row_for_a_receipt_is_a_conflicting_link():
    row = filing("20250515")
    dates = FilingDates([row, {**row, "rm": "different content"}])
    assert dates.annotate(version(row["rcept_no"]))["actual_public_date"] is None


@pytest.mark.parametrize("field,value", [("rm", "changed"), ("corp_cls", "E"), ("stock_code", "000020")])
def test_merged_archive_conflicts_include_current_attributes_without_changing_legacy_reads(tmp_path, field, value):
    row = filing("20250515")
    root = tmp_path / "disclosures/dart_full"
    write_rows(root / "list/2025/20250515.jsonl", [row])
    write_rows(root / "list_supplement/2025/20250515.jsonl", [{**row, field: value}])
    assert listing_rows(tmp_path, "20250515") == [row]
    dates = FilingDates.from_data(tmp_path)
    assert dates.annotate(version(row["rcept_no"]))["date_reason"] == "conflicting_link"
    assert dates.event_listings().empty


def test_correction_audit_uses_actual_last_decision_not_next_calendar_month(tmp_path):
    original = filing("20251128")
    corrected = filing("20251130", title="[정정]분기보고서 (2025.03)")
    save(tmp_path, version(corrected["rcept_no"]))
    dates = FilingDates([original, corrected])
    excluded = FinancialVersions(tmp_path, dates, "O", "design_validation", sample_end="2025-11-28")
    included = FinancialVersions(tmp_path, dates, "O", "design_validation", sample_end="2025-12-01")
    assert excluded.report()["sample_correction_links"]["success"] == 0
    assert included.report()["sample_correction_links"]["success"] == 1


def test_default_loader_serialization_byte_regression_against_committed_loader(tmp_path):
    source = subprocess.run(["git", "show", "89e53b6:src/ingestion/dart_quarterly.py"],
                            capture_output=True, text=True, check=True).stdout
    legacy = types.ModuleType("src.ingestion.legacy_quarterly")
    legacy.__package__, legacy.__file__ = "src.ingestion", quarterly.__file__
    exec(compile(source, quarterly.__file__, "exec"), legacy.__dict__)
    save(tmp_path, version("20250501000001", division="OFS", revenue=110),
         version("20250515000002", revenue=200), version("20240515000003", year=2024, income=5))
    for day in ("2025-05-01", "2025-05-15", "2025-05-16"):
        before = legacy.load_quarterly(day, data_dir=tmp_path)
        after = quarterly.load_quarterly(day, data_dir=tmp_path)
        pd.testing.assert_frame_equal(before, after)
        assert before.to_json().encode() == after.to_json().encode()
    dates = FilingDates([filing("20250515", number=2)])
    frame = quarterly.load_quarterly("2025-05-16", data_dir=tmp_path, date_rule="S",
                                     filing_dates=dates, stage="design_validation")
    assert frame.revenue.tolist() == [200.0]
    with pytest.raises(ValueError, match="S/O require"):
        quarterly.load_quarterly("2025-05-16", data_dir=tmp_path, date_rule="S")


def test_date_rules_flow_through_exact_HC002_eligibility_phase_priority_and_missing_signal(tmp_path):
    prior = filing("20240515", title="분기보고서 (2024.03)")
    corrected = filing("20250901", title="[정정]분기보고서 (2025.03)")
    original = filing("20250515")
    dates = FilingDates([prior, corrected, original])
    save(tmp_path, version(prior["rcept_no"], year=2024, revenue=100, income=10),
         version(corrected["rcept_no"], revenue=120, income=11))
    days = pd.bdate_range("2025-07-01", "2025-08-29")
    prices = prices_for(days, ("000010",))
    results = {}
    for rule in ("S", "O"):
        loader = FinancialVersions(tmp_path, dates, rule, "design_validation")
        with common.guarded_reads("AV001_avoidance_portfolio"):
            results[rule], counts = hc002.universe_on_decision(prices, days[-1], data_dir=tmp_path,
                                                               financial_loader=loader.load)
    assert results["S"].empty
    assert results["O"].phase.tolist() == [3]
    assert results["O"].score.tolist() == [-10.0]
