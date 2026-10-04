import numpy as np
import pandas as pd
import pytest

from src.strategies.disclosures import parse_disclosures
from src.strategies.filters import cb_overhang_exclusions


AS_OF = "2024-01-05T16:30:00+09:00"


def prices(listed_shares=100_000_000):
    return pd.DataFrame([
        {"trade_date": pd.Timestamp(day), "stock_code": code, "close": 2000,
         "listed_shares": listed_shares if day == "2024-01-05" else 1_000,
         "calendar_status": "verified", "bar_at": f"{day}T15:30:00+09:00"}
        for day in ("2024-01-05", "2024-01-08") for code in ("000001", "000002")
    ]).set_index(["trade_date", "stock_code"])


def cb(receipt="E", **changes):
    return {"event_id": receipt, "stock_code": "000001", "category": "convertible_bond",
            "event_subtype": "issuance", "bond_type": "cb",
            "rcept_dt": "2024-01-05", "first_seen_at": "2024-01-05T10:00:00+09:00",
            "conversion_start": "2024-01-01", "conversion_end": "2025-01-01",
            "conversion_shares_pct": 3.0, "conversion_shares": np.nan, "is_correction": False, **changes}


def test_sum_open_and_soon_open_windows_inclusive_threshold():
    events = pd.DataFrame([cb("open"), cb("soon", conversion_start="2024-02-04", conversion_shares_pct=2),
                           cb("too_far", conversion_start="2024-02-05", conversion_shares_pct=100),
                           cb("ended", conversion_end="2024-01-04", conversion_shares_pct=100)])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"]
    row = table.iloc[0]
    assert row.overhang_pct == 5 and row.event_count == 2 and row.unparsed_count == 0
    assert "5%" in row.reason and bool(row.excluded)


def test_pct_precedence_share_fallback_and_future_prices_are_blocked():
    events = pd.DataFrame([cb("ratio", conversion_shares_pct=3, conversion_shares=90_000_000),
                           cb("fallback", conversion_shares_pct=np.nan, conversion_shares=2_000_000)])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"] and table.iloc[0].overhang_pct == 5


def test_unparsed_windows_shares_and_denominators_are_counted_not_zero():
    events = pd.DataFrame([cb("missing_window", conversion_start=None),
                           cb("missing_size", conversion_shares_pct=np.nan),
                           cb("missing_denominator", stock_code="000003", conversion_shares_pct=np.nan,
                              conversion_shares=2_000_000),
                           cb("inverted", conversion_start="2025-02-01", conversion_end="2024-01-01")])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == []
    assert table.unparsed_count.tolist() == [3, 1]
    assert table.overhang_pct.isna().all() and table.event_count.eq(0).all()
    assert "skipped" in table.reason.iloc[0]


@pytest.mark.parametrize("listed", [0, np.nan])
def test_zero_or_missing_listed_shares_cannot_be_a_denominator(listed):
    events = pd.DataFrame([cb(conversion_shares_pct=np.nan, conversion_shares=2_000_000)])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices(listed))
    assert excluded == [] and table.unparsed_count.iloc[0] == 1


def test_forward_future_event_and_date_only_same_day_are_unusable():
    events = pd.DataFrame([cb("future", first_seen_at="2024-01-05T16:31:00+09:00", conversion_shares_pct=99),
                           cb("dated", first_seen_at=None, conversion_shares_pct=99),
                           cb("known", stock_code="000002", conversion_shares_pct=5)])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000002"] and table.stock_code.tolist() == ["000002"]
    excluded, table = cb_overhang_exclusions("2024-01-08T09:00:00+09:00", events, prices())
    assert excluded == ["000001", "000002"]


def test_revisions_of_explicit_round_use_latest_known_receipt_only():
    events = pd.DataFrame([cb("first", bond_round=1, conversion_shares_pct=3),
                           cb("second_issue", bond_round=2, conversion_shares_pct=1),
                           cb("corrected", bond_round=1, is_correction=True, conversion_shares_pct=4,
                              first_seen_at="2024-01-05T11:00:00+09:00"),
                           cb("future_correction", bond_round=1, is_correction=True, conversion_shares_pct=99,
                              first_seen_at="2024-01-08T11:00:00+09:00")])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"] and table.overhang_pct.iloc[0] == 5
    assert table.event_count.iloc[0] == 2
    reverse_excluded, reverse_table = cb_overhang_exclusions(AS_OF, events.iloc[::-1], prices())
    assert excluded == reverse_excluded
    pd.testing.assert_frame_equal(table, reverse_table)


def test_correction_with_no_explicit_round_is_skipped_and_reported():
    events = pd.DataFrame([cb("first"), cb("unlinked", is_correction=True,
                                        first_seen_at="2024-01-05T11:00:00+09:00")])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == [] and table.overhang_pct.iloc[0] == 3
    assert table.unparsed_count.iloc[0] == 1


def test_latest_unparsed_revision_does_not_resurrect_old_pct():
    events = pd.DataFrame([cb("old", bond_round=1, conversion_shares_pct=10),
                           cb("new", bond_round=1, is_correction=True, conversion_shares_pct=np.nan,
                              first_seen_at="2024-01-05T11:00:00+09:00")])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == [] and table.unparsed_count.iloc[0] == 1
    assert pd.isna(table.overhang_pct.iloc[0])


def test_window_end_today_counts_and_lookahead_is_configurable():
    events = pd.DataFrame([cb("ends_today", conversion_end="2024-01-05", conversion_shares_pct=5),
                           cb("soon", stock_code="000002", conversion_start="2024-01-10", conversion_shares_pct=5)])
    excluded, _ = cb_overhang_exclusions(AS_OF, events, prices(), lookahead_days=0)
    assert excluded == ["000001"]
    excluded, _ = cb_overhang_exclusions(AS_OF, events, prices(), threshold_pct=6)
    assert excluded == []


def test_anonymized_parsed_cb_flows_through_availability_and_exclusion():
    body = ("1. 사채의 종류 회차 7 종류 무기명식 사모 전환사채 "
            "2. 사채의 권면(전자등록)총액(원) 1,000,000,000 "
            "9. 전환에 관한 사항 전환가액(원/주) 1,000 "
            "전환에 따라발행할 주식 종류 가상회사 보통주 주식수 1,000,000 "
            "주식총수 대비비율(%) 6.5 전환청구기간 시작일 2024년 01월 01일 종료일 2025년 01월 01일")
    events = parse_disclosures([{"title": "주요사항보고서(전환사채권발행결정)", "content": body,
                                "published_at": "2024-01-04T00:00:00", "stock_code": "000001",
                                "metadata": {"rcept_no": "20240104000001"}}])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"] and table.overhang_pct.iloc[0] == 6.5


def test_exchange_bond_round_cannot_replace_same_numbered_cb_issue():
    body = ("1. 사채의 종류 회차 7 종류 무기명식 사모 전환사채 "
            "2. 사채의 권면(전자등록)총액(원) 1,000,000,000 "
            "9. 전환에 관한 사항 전환가액(원/주) 1,000 "
            "전환에 따라발행할 주식 종류 가상회사 보통주 주식수 1,000,000 "
            "주식총수 대비비율(%) 6.5 전환청구기간 시작일 2024년 01월 01일 종료일 2025년 01월 01일")
    documents = [
        {"title": "주요사항보고서(전환사채권발행결정)", "content": body, "published_at": "2024-01-03",
         "stock_code": "000001", "metadata": {"rcept_no": "20240103000001"}},
        {"title": "주요사항보고서(교환사채권발행결정)",
         "content": body.replace("전환", "교환").replace("교환에 따라발행할 주식 종류", "교환대상 종류"),
         "published_at": "2024-01-04", "stock_code": "000001", "metadata": {"rcept_no": "20240104000001"}},
    ]
    events = parse_disclosures(documents)
    assert events.bond_round.tolist() == [7, 7]
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"] and table.overhang_pct.iloc[0] == 13
    assert table.event_count.iloc[0] == 2 and table.unparsed_count.iloc[0] == 0


def test_other_categories_and_empty_input_have_no_exclusions():
    excluded, table = cb_overhang_exclusions(AS_OF, pd.DataFrame([cb(category="contract")]), prices())
    assert excluded == [] and table.empty
    excluded, table = cb_overhang_exclusions(AS_OF, pd.DataFrame(), prices())
    assert excluded == [] and {"unparsed_count", "reason"}.issubset(table.columns)


@pytest.mark.parametrize("kwargs", [{"threshold_pct": 0}, {"threshold_pct": np.nan}, {"lookahead_days": -1}])
def test_invalid_filter_parameters_fail(kwargs):
    with pytest.raises(ValueError):
        cb_overhang_exclusions(AS_OF, pd.DataFrame(), prices(), **kwargs)


@pytest.mark.parametrize("subtype", ["conversion_exercise", "refixing", "other_bond"])
def test_non_issuance_never_adds_supply_or_replaces_an_issuance(subtype):
    events = pd.DataFrame([cb("issue", bond_round=7, conversion_shares_pct=6),
                           cb("notice", bond_round=7, event_subtype=subtype, conversion_shares_pct=99,
                              first_seen_at="2024-01-05T11:00:00+09:00")])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"] and table.overhang_pct.iloc[0] == 6
    assert table.event_count.iloc[0] == 1 and table.ignored_event_count.iloc[0] == 1


def repurchase(receipt="repurchase", **changes):
    return cb(receipt, **{"event_subtype": "early_repurchase", "bond_round": 7,
                          "repurchased_amount_krw": 500_000_000,
                          "first_seen_at": "2024-01-05T11:00:00+09:00", **changes})


def test_matched_repurchase_reduces_face_amount_fraction_and_reports_counts():
    events = pd.DataFrame([cb("issue", bond_round=7, issue_amount_krw=1_000_000_000, conversion_shares_pct=8),
                           repurchase()])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == [] and table.overhang_pct.iloc[0] == 4 and table.event_count.iloc[0] == 1
    assert table.matched_repurchase_count.iloc[0] == 1 and table.unmatched_repurchase_count.iloc[0] == 0
    assert "repurchases matched 1, unmatched 0" in table.reason.iloc[0]


@pytest.mark.parametrize("changes", [
    {"bond_round": None}, {"bond_round": 8}, {"bond_type": "eb"}, {"stock_code": "000002"},
    {"repurchased_amount_krw": None}, {"repurchased_amount_krw": 0}, {"repurchased_amount_krw": -1},
    {"repurchased_amount_krw": np.inf}, {"repurchased_amount_krw": 1_000_000_001}, {"is_correction": True},
])
def test_unmatched_repurchase_never_reduces_supply(changes):
    events = pd.DataFrame([cb("issue", bond_round=7, issue_amount_krw=1_000_000_000, conversion_shares_pct=8),
                           repurchase(**changes)])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"] and table.overhang_pct.iloc[0] == 8
    assert table.unmatched_repurchase_count.sum() == 1 and table.matched_repurchase_count.sum() == 0


def test_percentage_alone_cannot_support_a_face_amount_reduction():
    events = pd.DataFrame([cb("issue", bond_round=7, conversion_shares_pct=8), repurchase()])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"] and table.overhang_pct.iloc[0] == 8
    assert table.unmatched_repurchase_count.iloc[0] == 1


def test_multiple_repurchase_transactions_of_one_round_are_not_deduplicated():
    events = pd.DataFrame([cb("issue", bond_round=7, issue_amount_krw=1_000_000_000, conversion_shares_pct=8),
                           repurchase("first", repurchased_amount_krw=250_000_000),
                           repurchase("second", repurchased_amount_krw=250_000_000,
                                      first_seen_at="2024-01-05T12:00:00+09:00")])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == [] and table.overhang_pct.iloc[0] == 4
    assert table.matched_repurchase_count.iloc[0] == 2 and table.unmatched_repurchase_count.iloc[0] == 0
    _, reverse = cb_overhang_exclusions(AS_OF, events.iloc[::-1], prices())
    pd.testing.assert_frame_equal(table, reverse)


def test_cb_bw_eb_rounds_remain_separate_and_untyped_ambiguous_repurchase_is_unmatched():
    events = pd.DataFrame([
        cb("cb", bond_round=7, issue_amount_krw=1_000_000_000, conversion_shares_pct=2),
        cb("bw", bond_round=7, bond_type="bw", issue_amount_krw=1_000_000_000, conversion_shares_pct=2),
        cb("eb", bond_round=7, bond_type="eb", event_subtype="exchangeable_issuance",
           issue_amount_krw=1_000_000_000, conversion_shares_pct=2),
        repurchase("eb_repurchase", bond_type="eb"),
        repurchase("ambiguous", bond_type=None),
    ])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"] and table.overhang_pct.iloc[0] == 5 and table.event_count.iloc[0] == 3
    assert table.matched_repurchase_count.iloc[0] == 1 and table.unmatched_repurchase_count.iloc[0] == 1


def test_repurchase_applies_to_latest_issue_revision_even_if_revision_arrives_later():
    events = pd.DataFrame([
        cb("issue", bond_round=7, issue_amount_krw=1_000_000_000, conversion_shares_pct=8),
        repurchase(),
        cb("corrected", bond_round=7, issue_amount_krw=1_000_000_000, conversion_shares_pct=10,
           is_correction=True, first_seen_at="2024-01-05T12:00:00+09:00"),
    ])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"] and table.overhang_pct.iloc[0] == 5 and table.event_count.iloc[0] == 1
    assert table.matched_repurchase_count.iloc[0] == 1


def test_full_repurchase_is_zero_and_future_repurchase_is_blocked():
    events = pd.DataFrame([cb("issue", bond_round=7, issue_amount_krw=1_000_000_000, conversion_shares_pct=8),
                           repurchase(repurchased_amount_krw=1_000_000_000,
                                      first_seen_at="2024-01-05T16:31:00+09:00")])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == ["000001"] and table.overhang_pct.iloc[0] == 8
    assert table.matched_repurchase_count.iloc[0] == 0
    excluded, table = cb_overhang_exclusions("2024-01-05T16:31:00+09:00", events, prices())
    assert excluded == [] and table.overhang_pct.iloc[0] == 0 and table.event_count.iloc[0] == 1


def test_bond_event_without_subtype_fails_clearly():
    events = pd.DataFrame([cb()]).drop(columns="event_subtype")
    with pytest.raises(ValueError, match="event_subtype"):
        cb_overhang_exclusions(AS_OF, events, prices())


def test_parsed_early_acquisition_reduces_supply_through_point_in_time_views():
    issue = ("1. 사채의 종류 회차 7 종류 무기명식 사모 전환사채 "
             "2. 사채의 권면(전자등록)총액(원) 1,000,000,000 "
             "전환가액(원/주) 1,000 전환에 따라발행할 주식 종류 가상회사 보통주 "
             "주식수 1,000,000 주식총수 대비비율(%) 8 "
             "전환청구기간 시작일 2024년 01월 01일 종료일 2025년 01월 01일")
    acquired = ("전환사채(해외전환사채) 7 회차 1. 만기전 취득 사채에 관한 사항 "
                "2. 사채 취득금액 (통화단위) 755,000,000 KRW : South-Korean Won "
                "- 취득한 사채의 권면(전자등록)총액 (통화단위) 750,000,000 KRW : South-Korean Won")
    events = parse_disclosures([
        {"title": "주요사항보고서(전환사채권발행결정)", "content": issue,
         "published_at": "2024-01-03", "stock_code": "000001", "metadata": {"rcept_no": "20240103000001"}},
        {"title": "전환사채(해외전환사채포함)발행후만기전사채취득", "content": acquired,
         "published_at": "2024-01-04", "stock_code": "000001", "metadata": {"rcept_no": "20240104000001"}},
    ])
    excluded, table = cb_overhang_exclusions(AS_OF, events, prices())
    assert excluded == [] and table.overhang_pct.iloc[0] == 2
    assert table.matched_repurchase_count.iloc[0] == 1 and table.event_count.iloc[0] == 1
