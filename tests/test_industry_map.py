import pandas as pd
import pytest

from src.ingestion.storage import write_rows
from src.research.industry_map import (KSIC_PREFIXES, UNCLASSIFIED, attach_industry,
                                       industry_from_ksic, industry_of, load_industry_map)


@pytest.mark.parametrize("code,expected", [
    ("26110", "반도체"), ("26211", "디스플레이·전자부품"), ("263", "디스플레이·전자부품"),
    ("26410", "통신장비"), ("28202", "2차전지·전기장비"), ("30", "자동차·부품"),
    ("31111", "조선"), ("31201", "기계·장비"), ("27112", "의료기기"), ("272", "기계·장비"),
    ("24", "철강·금속"), ("20", "화학"), ("19", "정유·에너지"), ("21", "제약·바이오"),
    ("41", "건설"), ("49", "운송"), ("47", "유통·소매"), ("10", "음식료·담배"),
    ("20423", "섬유·의복·화장품"), ("20422", "화학"), ("581", "미디어·엔터"),
    ("58211", "게임·소프트웨어"), ("6391", "미디어·엔터"), ("6399", "게임·소프트웨어"),
    ("61", "통신서비스"), ("64", "금융(은행·증권·보험)"), ("64992", "지주회사"),
    ("35", "유틸리티"), ("68", "기타"), (" 26110 ", "반도체"),
])
def test_longest_prefix_and_investment_groups(code, expected):
    assert industry_from_ksic(code) == expected


@pytest.mark.parametrize("code", [None, "", "  ", "04000", "26", "C26110", "261100", 26110, "26.1", "２６１１０"])
def test_unknown_and_missing_codes_remain_unclassified(code):
    assert industry_from_ksic(code) == UNCLASSIFIED


def test_table_has_24_groups_and_only_two_to_five_digit_prefixes():
    assert len(set(KSIC_PREFIXES.values())) == 24
    assert all(prefix.isascii() and prefix.isdigit() and 2 <= len(prefix) <= 5 for prefix in KSIC_PREFIXES)


def test_stock_lookup_preserves_delisted_reference_and_unknowns(tmp_path):
    path = tmp_path / "companies.jsonl"
    write_rows(path, [{"stock_code": "005930", "reference_stock_code": "005930", "induty_code": "26110"},
                      {"stock_code": "", "reference_stock_code": "0015G0", "induty_code": "31111"},
                      {"stock_code": "000660", "induty_code": ""}])
    assert industry_of("005930", companies_path=path) == "반도체"
    assert industry_of("0015G0", companies_path=path) == "조선"
    assert industry_of("000660", companies_path=path) == industry_of("999990", companies_path=path) == UNCLASSIFIED


@pytest.mark.parametrize("indexed", [True, False])
def test_attach_counts_unmapped_stocks_and_rows_without_mutation(tmp_path, indexed):
    path = tmp_path / "companies.jsonl"
    write_rows(path, [{"stock_code": "000010", "induty_code": "26110"},
                      {"stock_code": "000020", "induty_code": ""},
                      {"stock_code": "000030", "induty_code": "04000"}])
    prices = pd.DataFrame({"trade_date": pd.to_datetime(["2024-01-02"] * 4 + ["2024-01-03"]),
                           "stock_code": ["000010", "000020", "000030", "000040", "000020"], "close": 100})
    if indexed:
        prices = prices.set_index(["trade_date", "stock_code"])
    original = prices.copy()
    result = attach_industry(prices, companies_path=path)
    assert result["industry"].tolist() == ["반도체", UNCLASSIFIED, UNCLASSIFIED, UNCLASSIFIED, UNCLASSIFIED]
    assert result.attrs["unmapped_stock_count"] == 3 and result.attrs["unmapped_row_count"] == 4
    assert result.attrs["unmapped_stock_codes"] == ["000020", "000030", "000040"]
    pd.testing.assert_frame_equal(prices, original)


def test_missing_archive_and_conflicting_identity_fail(tmp_path):
    path = tmp_path / "companies.jsonl"
    with pytest.raises(FileNotFoundError, match="archive is required"):
        industry_of("005930", companies_path=path)
    write_rows(path, [{"stock_code": "005930", "induty_code": "26110"},
                      {"stock_code": "005930", "induty_code": "21"}])
    with pytest.raises(ValueError, match="duplicate"):
        load_industry_map(path)
