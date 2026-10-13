import pandas as pd
import pytest

from src.ingestion.dart_filing_dates import annotate_versions, periodic_period, provenance_index
from src.ingestion import dart_quarterly as quarterly
from src.ingestion.storage import write_rows


CORP = "00000001"


def filing(day, title, number=1, corp=CORP):
    return {"corp_code": corp, "rcept_no": day + f"{number:06d}", "rcept_dt": day, "report_nm": title}


@pytest.mark.parametrize("title,expected", [
    ("[기재정정]분기보고서 (2020.03)", ("분기보고서", "2020.03")),
    ("[첨부정정] 반기보고서 ( 2020. 06 )", ("반기보고서", "2020.06")),
    ("사업보고서 (2020.12)", ("사업보고서", "2020.12")),
    ("사업보고서제출기한연장신고서", None), ("분기보고서", None)])
def test_period_parser(title, expected):
    assert periodic_period(title) == expected


def test_correction_link_uses_same_corp_report_type_period_and_earliest_original():
    rows = [filing("20220511", "[기재정정]분기보고서 (2020.03)"),
            filing("20200515", "분기보고서 (2020.03)"), filing("20200514", "분기보고서 (2020.03)"),
            filing("20200430", "사업보고서 (2020.03)"), filing("20200202", "분기보고서 (2019.12)"),
            filing("20200501", "분기보고서 (2020.03)", corp="00000002")]
    # Submission/listing disagreement must use the later date on both versions.
    rows[0]["rcept_dt"] = "20220512"
    rows[2]["rcept_dt"] = "20200513"
    version = {"corp_code": CORP, "rcept_no": rows[0]["rcept_no"], "fs_div": "CFS"}
    annotated = annotate_versions([version], index=provenance_index(None, listings=rows))[0]
    assert annotated["is_correction"] is True and annotated["list_rcept_dt"] == "20220512"
    assert annotated["original_rcept_no"] == rows[2]["rcept_no"]
    assert annotated["original_rcept_dt"] == "20200513"
    assert annotated["known_date_stored"] == "2022-05-12"
    assert annotated["known_date_original"] == "2020-05-14"
    assert version == {"corp_code": CORP, "rcept_no": rows[0]["rcept_no"], "fs_div": "CFS"}


def test_original_unlinked_and_correction_without_original_have_explicit_dates():
    rows = [filing("20200515", "분기보고서 (2020.03)"), filing("20200814", "[정정]반기보고서 (2020.06)")]
    versions = [{"corp_code": CORP, "rcept_no": r["rcept_no"]} for r in rows] + [{"corp_code": CORP, "rcept_no": "20201114000001"}]
    annotated = annotate_versions(versions, index=provenance_index(None, listings=rows))
    assert [v["is_correction"] for v in annotated] == [False, True, None]
    assert all(v["known_date_original"] == v["known_date_stored"] for v in annotated)
    assert annotated[1]["original_rcept_no"] is None and annotated[2]["list_rcept_dt"] is None


def test_found_receipt_keeps_list_date_when_report_period_is_unparsable():
    row = filing("20200515", "[정정] 기타 보고서")
    row["rcept_dt"] = "20200518"
    version = {"corp_code": CORP, "rcept_no": row["rcept_no"]}
    result = annotate_versions([version], index=provenance_index(None, listings=[row]))[0]
    assert result["list_rcept_dt"] == "20200518" and result["is_correction"] is True
    assert result["original_rcept_no"] is None and result["report_period"] is None
    assert result["known_date_stored"] == result["known_date_original"] == "2020-05-18"


def test_opt_in_original_rule_uses_supplement_and_default_keeps_saved_prefix(tmp_path, monkeypatch):
    original = filing("20200515", "분기보고서 (2020.03)")
    corrected = filing("20220511", "[기재정정]분기보고서 (2020.03)")
    corrected["rcept_dt"] = "20220512"
    root = tmp_path / "disclosures/dart_full/list_supplement"
    for row in (original, corrected):
        day = row["rcept_dt"]
        write_rows(root / day[:4] / f"{day}.jsonl", [row])
    record = {"corp_code": CORP, "stock_codes": ["000010"], "bsns_year": 2020,
              "reprt_code": "11013", "fiscal_quarter": "2020Q1", "rcept_no": corrected["rcept_no"],
              "available_date": "2022-05-11", "fs_div": "CFS", "currency": "KRW",
              "raw_accounts": {m: {"thstrm_amount": "100", "currency": "KRW"} for m in quarterly.ACCOUNTS}}
    write_rows(tmp_path / "fundamentals/dart_quarterly/2020_11013.jsonl", [record])
    default = quarterly.load_quarterly("2020-05-16", data_dir=tmp_path)
    assert default.empty
    alternative = quarterly.load_quarterly("2020-05-16", data_dir=tmp_path, date_rule="original")
    assert alternative.revenue.tolist() == [100.0] and alternative.available_date.tolist() == ["2020-05-15"]
    assert quarterly.load_quarterly("2020-05-15", data_dir=tmp_path, date_rule="original").empty
    stored = quarterly.load_quarterly("2022-05-12", data_dir=tmp_path)
    assert stored.available_date.tolist() == ["2022-05-11"]
    monkeypatch.setattr(quarterly, "provenance_index", lambda *a, **k: pytest.fail("default read listing provenance"))
    pd.testing.assert_frame_equal(stored, quarterly.load_quarterly("2022-05-12", data_dir=tmp_path, date_rule="stored"))
    with pytest.raises(ValueError, match="date_rule"):
        quarterly.load_quarterly("2020-01-01", data_dir=tmp_path, date_rule="latest")
