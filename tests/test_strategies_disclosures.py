import numpy as np
import pandas as pd
import pytest

from src.strategies import _runner_module
from src.strategies.disclosures import parse_disclosures


# Anonymized table shapes from the read-only DART backup, not synthetic API rows.
CONTRACT = (
    "가상회사/단일판매ㆍ공급계약체결/(2025.05.22) 단일판매ㆍ공급계약 체결 "
    "1. 판매ㆍ공급계약 구분 상품공급 - 체결계약명 원료 공급계약 "
    "2. 계약내역 계약금액(원) 88,814,945,010 최근매출액(원) 2,067,791,451,854 "
    "매출액대비(%) 4.30 대규모법인여부 해당 "
    "3. 계약상대 가상고객 주식회사 - 회사와의 관계 - "
    "4. 판매ㆍ공급지역 미정 5. 계약기간 시작일 2025-05-21 종료일 2026-12-31 "
    "6. 주요 계약조건 계약금ㆍ선급금 유무 무"
)
CB = (
    "전환사채권 발행결정 1. 사채의 종류 회차 153 종류 무기명식 무보증사모 전환사채 "
    "2. 사채의 권면(전자등록)총액 (원) 110,000,000,000 "
    "3. 자금조달의 목적 시설자금 (원) 110,000,000,000 "
    "9. 전환에 관한 사항 전환비율 (%) 100 전환가액 (원/주) 11,524 "
    "전환에 따라발행할 주식 종류 가상회사 기명식 보통주 주식수 9,545,296 "
    "주식총수 대비비율(%) 4.87 전환청구기간 시작일 2025년 11월 30일 종료일 2027년 10월 29일"
)
RAISE = (
    "유상증자 결정 1. 신주의 종류와 수 보통주식(주) 16,830,200 종류주식(주) - "
    "2. 1주당 액면가액(원) 500 3. 증자전 발행주식총수(주) 보통주식(주) 74,165,069 "
    "4. 자금조달의 목적 시설자금(원) - 영업양수자금(원) - 운영자금(원) 119,999,326,000 "
    "채무상환자금(원) - 타법인 증권 취득자금(원) - 기타자금(원) - "
    "5. 증자방식 주주배정후실권주일반공모 6. 신주 발행가액 예정발행가 보통주식(원) 7,130"
)
BUYBACK = (
    "자기주식 취득 결정 1. 취득예정주식(주) 보통주식 191,938 기타주식 - "
    "2. 취득예정금액(원) 보통주식 20,000,000,000 기타주식 - "
    "3. 취득예상기간 시작일 2025년 07월 02일 종료일 2025년 10월 01일"
)


def document(title="단일판매ㆍ공급계약체결", body=CONTRACT, receipt="20250522800001", **changes):
    return {"title": title, "content": body, "published_at": "2025-05-22T00:00:00",
            "stock_code": "000001", "metadata": {"rcept_no": receipt, "report_nm": title}, **changes}


def parsed(**kwargs):
    return parse_disclosures([document(**kwargs)]).iloc[0]


def test_contract_amount_ratio_dates_and_provenance_from_real_shape():
    row = parsed()
    assert row.category == "contract" and row.event_id == "20250522800001"
    assert row.rcept_dt == pd.Timestamp("2025-05-22") and pd.isna(row.first_seen_at)
    assert row.contract_amount_krw == 88_814_945_010
    assert row.revenue_ratio_pct == 4.3
    assert row.contract_start == pd.Timestamp("2025-05-21")
    assert row.contract_end == pd.Timestamp("2026-12-31")
    assert bool(row.counterparty_disclosed) and not bool(row.is_correction)
    assert row.parse_status == "ok"


def test_corrected_contract_reads_restated_body_and_preserves_original_event():
    table = ("정정신고(보고) 정정사항 정정항목 정정전 정정후 "
             "계약금액(원) 100,000,000 88,814,945,010 매출액대비(%) 1.0 4.30 "
             "5. 계약기간 종료일 2025-08-23 - ")
    correction = document(title="[기재정정]단일판매ㆍ공급계약체결", body=table + CONTRACT,
                          receipt="20250523800001", published_at="2025-05-23T00:00:00")
    events = parse_disclosures([document(), correction])
    assert events.event_id.tolist() == ["20250522800001", "20250523800001"]
    row = events.iloc[1]
    assert row.contract_amount_krw == 88_814_945_010 and row.revenue_ratio_pct == 4.3
    assert bool(row.is_correction) and row.rcept_dt == pd.Timestamp("2025-05-23")


def test_truncated_correction_table_is_not_mistaken_for_current_values():
    row = parsed(title="[기재정정]단일판매ㆍ공급계약체결",
                 body="정정사항 정정전 정정후 계약금액(원) 100,000 200,000 매출액대비(%) 1 2")
    assert row.parse_status == "unparsed"
    assert pd.isna(row.contract_amount_krw) and pd.isna(row.revenue_ratio_pct)


@pytest.mark.parametrize("value,expected", [
    ("계약금액(원) 1,234,567 원", 1_234_567),
    ("계약금액 : 12.5억원", 1_250_000_000),
    ("계약금액(백만원) 12,345", 12_345_000_000),
    ("계약금액 1억 2,500만원", 125_000_000),
])
def test_korean_commas_units_and_label_units(value, expected):
    row = parsed(body=value)
    assert row.contract_amount_krw == expected
    assert row.parse_status == "partial" and pd.isna(row.revenue_ratio_pct)


@pytest.mark.parametrize("body", ["사업 내용 100억원 50% 2025-01-01", "계약금액(원) -", "계약금액(원) 1,2"])
def test_unlabelled_missing_and_malformed_numbers_are_not_guessed(body):
    row = parsed(body=body)
    assert pd.isna(row.contract_amount_krw)
    assert row.parse_status == "unparsed"


def test_undisclosed_counterparty_and_unknown_end_date_are_explicit():
    row = parsed(body=CONTRACT.replace("가상고객 주식회사", "경영상 비밀유지로 공시유보")
                 .replace("종료일 2026-12-31", "종료일 -"))
    assert not bool(row.counterparty_disclosed)
    assert row.contract_start == pd.Timestamp("2025-05-21") and pd.isna(row.contract_end)
    assert row.parse_status == "partial"


@pytest.mark.parametrize("word", ["해지", "취소"])
def test_contract_termination_flag(word):
    row = parsed(title="단일판매ㆍ공급계약" + word)
    assert bool(row.is_termination) and row.category == "contract"


def test_cb_all_core_features_and_explicit_issue_round():
    row = parsed(title="주요사항보고서(전환사채권발행결정)", body=CB)
    assert row.category == "convertible_bond" and row.parse_status == "ok"
    assert row.issue_amount_krw == 110_000_000_000 and row.conversion_price_krw == 11_524
    assert row.conversion_shares == 9_545_296 and row.conversion_shares_pct == 4.87
    assert row.conversion_start == pd.Timestamp("2025-11-30")
    assert row.conversion_end == pd.Timestamp("2027-10-29") and row.bond_round == 153
    assert pd.isna(row.refixing_floor_pct)


def test_cb_correction_uses_new_body_instead_of_old_price_or_shares():
    table = "정정사항 항목 정정 전 정정 후 전환가액(원/주) 1,000 11,524 주식수 100,000 9,545,296 "
    row = parsed(title="[기재정정]주요사항보고서(전환사채권발행결정)", body=table + CB)
    assert row.conversion_price_krw == 11_524 and row.conversion_shares == 9_545_296
    assert bool(row.is_correction) and row.parse_status == "ok"


@pytest.mark.parametrize("suffix,expected", [
    (" 최저 조정한도는 발행 시 전환가액(조정 전에 이미 조정한 경우 이를 감안한 가격)의 70%에 해당하는 가액", 70),
    (" 리픽싱 하한(%) 80", 80),
    (" 최저 조정가액(원) - 발행당시 전환가액의70% 미만으로 조정가능한 잔여발행한도(원) -", np.nan),
])
def test_cb_floor_percentage_is_not_confused_with_residual_issuance_limit(suffix, expected):
    row = parsed(title="주요사항보고서(전환사채권발행결정)", body=CB + suffix)
    assert pd.isna(row.refixing_floor_pct) if pd.isna(expected) else row.refixing_floor_pct == expected


def test_cb_dot_dates_and_share_unit_suffix():
    body = CB.replace("9,545,296", "9,545,296 주").replace("2025년 11월 30일", "2025.11.30")
    body = body.replace("2027년 10월 29일", "2027.10.29")
    row = parsed(title="주요사항보고서(전환사채권발행결정)", body=body)
    assert row.conversion_start == pd.Timestamp("2025-11-30") and row.parse_status == "ok"


def test_exchangeable_issuance_keeps_category_and_exposes_existing_share_supply():
    body = CB.replace("전환", "교환").replace("교환에 따라발행할 주식 종류", "교환대상 종류")
    row = parsed(title="주요사항보고서(교환사채권발행결정)", body=body)
    assert row.category == "convertible_bond" and row.parse_status == "ok"
    assert row.event_subtype == "exchangeable_issuance" and row.bond_type == "eb"
    assert row.conversion_shares == 9_545_296 and row.conversion_start == pd.Timestamp("2025-11-30")


def test_capital_raise_sums_labelled_funding_purposes():
    row = parsed(title="유상증자결정(종속회사의주요경영사항)", body=RAISE)
    assert row.new_shares == 16_830_200 and row.raise_amount_krw == 119_999_326_000
    assert row.method == "주주배정" and row.parse_status == "ok"
    body = RAISE.replace("시설자금(원) -", "시설자금(원) 10억원").replace("종류주식(주) -", "종류주식(주) 20,000주")
    row = parsed(title="유상증자결정", body=body)
    assert row.raise_amount_krw == 120_999_326_000 and row.new_shares == 16_850_200


@pytest.mark.parametrize("method", ["제3자배정", "주주배정", "일반공모"])
def test_capital_raise_methods_and_explicit_total(method):
    body = RAISE.replace("주주배정후실권주일반공모", method) + " 모집총액(원) 2,000,000,000"
    row = parsed(title="유상증자결정", body=body)
    assert row.method == method and row.raise_amount_krw == 2_000_000_000


def test_corrected_raise_uses_new_purpose_and_bonus_issue_has_no_invented_amount():
    row = parsed(title="[기재정정]유상증자결정", body="정정사항 운영자금(원) 100 200 " + RAISE)
    assert row.raise_amount_krw == 119_999_326_000 and row.parse_status == "ok"
    bonus = parsed(title="무상증자결정", body="1. 신주의 종류와 수 보통주식(주) 22,467,264 기타주식(주) - 2. 1주당 액면가액(원) 500")
    assert bonus.new_shares == 22_467_264 and pd.isna(bonus.raise_amount_krw)
    assert bonus.parse_status == "partial"


def test_buyback_plans_sum_explicit_stock_classes():
    row = parsed(title="주요사항보고서(자기주식취득결정)", body=BUYBACK)
    assert row.planned_shares == 191_938 and row.planned_amount_krw == 20_000_000_000
    assert row.parse_status == "ok"
    body = BUYBACK.replace("기타주식 - 2.", "기타주식 1,000 2.").replace("기타주식 - 3.", "기타주식 1,000,000 3.")
    row = parsed(title="주요사항보고서(자기주식취득결정)", body=body)
    assert row.planned_shares == 192_938 and row.planned_amount_krw == 20_001_000_000


def test_buyback_trust_plan_partial_but_termination_amount_is_not_a_plan():
    body = "1. 계약금액(원) 2,000,000,000 2. 계약기간 시작일 2023년 11월 02일 종료일 2024년 05월 02일"
    row = parsed(title="주요사항보고서(자기주식취득신탁계약체결결정)", body=body)
    assert row.planned_amount_krw == 2_000_000_000 and pd.isna(row.planned_shares)
    assert row.parse_status == "partial"
    row = parsed(title="주요사항보고서(자기주식취득신탁계약해지결정)", body=body)
    assert row.parse_status == "unparsed" and pd.isna(row.planned_amount_krw)


def test_forward_enriched_record_preserves_first_seen_time():
    row = parsed(first_seen_at="2025-05-22T10:02:03+09:00")
    assert row.first_seen_at == pd.Timestamp("2025-05-22T10:02:03+09:00")
    raw = document()
    raw["metadata"]["first_seen_at"] = "2025-05-22T01:02:03+00:00"
    raw["metadata"]["rcept_dt"] = "20250521"
    row = parse_disclosures([raw]).iloc[0]
    assert row.first_seen_at == pd.Timestamp("2025-05-22T10:02:03+09:00")
    assert row.rcept_dt == pd.Timestamp("2025-05-21")


def test_missing_first_seen_values_stay_missing_or_use_explicit_metadata():
    row = parsed(first_seen_at=np.nan)
    assert pd.isna(row.first_seen_at)
    raw = document(first_seen_at=pd.NaT)
    raw["metadata"]["first_seen_at"] = "2025-05-22T10:00:00+09:00"
    assert parse_disclosures([raw]).first_seen_at.iloc[0] == pd.Timestamp("2025-05-22T10:00:00+09:00")


def test_categories_are_reused_from_existing_classifier(monkeypatch):
    seen = []

    def classify(row):
        seen.append(row)
        return "dividend"

    monkeypatch.setattr(_runner_module("event_evidence"), "_category", classify)
    row = parsed()
    assert seen == [{"source_type": "dart", "title": "단일판매ㆍ공급계약체결"}]
    assert row.category == "dividend" and row.parse_status == "unparsed"


def test_empty_input_has_schema_and_missing_identity_fails():
    assert {"event_id", "parse_status", "conversion_shares_pct"}.issubset(parse_disclosures([]).columns)
    raw = document()
    raw["metadata"] = {}
    with pytest.raises(ValueError, match="rcept_no"):
        parse_disclosures([raw])


# Field names observed in the backup; receipts below are aligned deliberately.
@pytest.mark.parametrize("endpoint,title,price,count,ratio,start,end", [
    ("cvbdIsDecsn", "주요사항보고서(전환사채권발행결정)", "cv_prc", "cvisstk_cnt",
     "cvisstk_tisstk_vs", "cvrqpd_bgd", "cvrqpd_edd"),
    ("exbdIsDecsn", "주요사항보고서(교환사채권발행결정)", "ex_prc", "extg_stkcnt",
     "extg_tisstk_vs", "exrqpd_bgd", "exrqpd_edd"),
    ("bdwtIsDecsn", "주요사항보고서(신주인수권부사채권발행결정)", "ex_prc", "nstk_isstk_cnt",
     "nstk_isstk_tisstk_vs", "expd_bgd", "expd_edd"),
])
@pytest.mark.parametrize("separator", ["\n", " "])
def test_receipt_matched_structured_bond_codes_take_precedence(endpoint, title, price, count, ratio,
                                                              start, end, separator):
    body = separator.join([
        f"[structured_endpoint] {endpoint}", "rcept_no: 20250522800001", "bd_tm: 7",
        "bd_fta: 2,000,000,000", f"{price}: 2,390", f"{count}: 836,820", f"{ratio}: 2.38",
        f"{start}: 2024년 05월 31일", f"{end}: 2026-04-30", "act_mktprcfl_cvprc_lwtrsprc: 1,675",
    ])
    row = parsed(title=title, body=CB + "\n" + body)
    assert row.parse_status == "ok" and not bool(row.structured_source_mismatch)
    assert row.issue_amount_krw == 2_000_000_000 and row.conversion_price_krw == 2_390
    assert row.conversion_shares == 836_820 and row.conversion_shares_pct == 2.38 and row.bond_round == 7
    assert row.conversion_start == pd.Timestamp("2024-05-31") and row.conversion_end == pd.Timestamp("2026-04-30")
    assert row.refixing_floor_krw == 1_675 and pd.isna(row.refixing_floor_pct)


def test_structured_correction_without_restated_table_and_missing_values():
    row = parsed(title="[기재정정]주요사항보고서(전환사채권발행결정)", body=(
        "[structured_endpoint] cvbdIsDecsn rcept_no: 20250522800001 bd_tm: 7 "
        "bd_fta: 2,000,000,000 cv_prc: - cvisstk_cnt: 1,2 cvisstk_tisstk_vs: - "
        "cvrqpd_bgd: 2024년 05월 31일 cvrqpd_edd: 미정"
    ))
    assert row.parse_status == "partial" and row.issue_amount_krw == 2_000_000_000
    assert pd.isna(row.conversion_price_krw) and pd.isna(row.conversion_shares)
    assert pd.isna(row.conversion_shares_pct) and pd.isna(row.conversion_end)


@pytest.mark.parametrize("source_receipt", ["20230404000050", ""])
def test_structured_source_with_wrong_or_missing_receipt_is_rejected(source_receipt):
    row = parsed(title="주요사항보고서(전환사채권발행결정)", body=(
        f"[structured_endpoint] cvbdIsDecsn rcept_no: {source_receipt} bd_tm: 7 bd_fta: 2,000,000,000 "
        "cv_prc: 2,390 cvisstk_cnt: 836,820 cvisstk_tisstk_vs: 2.38 "
        "cvrqpd_bgd: 2024-05-31 cvrqpd_edd: 2026-04-30"
    ))
    assert bool(row.structured_source_mismatch) and row.parse_status == "unparsed"
    assert pd.isna(row.issue_amount_krw) and pd.isna(row.bond_round)


def test_full_structured_metadata_is_used_with_exact_receipt_and_category():
    raw = document(title="주요사항보고서(전환사채권발행결정)", body="본문 미수집")
    raw["metadata"].update(structured_endpoint="cvbdIsDecsn", structured_rcept_no="20250522800001",
                           structured_row={"rcept_no": "20250522800001", "bd_tm": "7", "bd_fta": "2,000,000,000"})
    row = parse_disclosures([raw]).iloc[0]
    assert row.bond_round == 7 and row.issue_amount_krw == 2_000_000_000 and row.parse_status == "partial"
    raw["metadata"]["structured_rcept_no"] = "20230404000050"
    row = parse_disclosures([raw]).iloc[0]
    assert bool(row.structured_source_mismatch) and row.parse_status == "unparsed"


@pytest.mark.parametrize("title,subtype,category", [
    ("전환사채(해외전환사채포함)발행후만기전사채취득 (제3회차)", "early_repurchase", "convertible_bond"),
    ("주요사항보고서(자기전환사채만기전취득결정)", "early_repurchase", "convertible_bond"),
    ("자기교환사채만기전취득결정 (제36회차)", "early_repurchase", "convertible_bond"),
    ("전환청구권ㆍ신주인수권ㆍ교환청구권행사 (전환사채의 전환청구권 행사)",
     "conversion_exercise", "convertible_bond"),
    ("주요사항보고서(전환사채권발행결정)", "issuance", "convertible_bond"),
    ("주요사항보고서(신주인수권부사채권발행결정)", "issuance", "convertible_bond"),
    ("주요사항보고서(교환사채권발행결정)", "exchangeable_issuance", "convertible_bond"),
    ("주요사항보고서(제3자의전환사채매수선택권행사)", "other_bond", "convertible_bond"),
    ("주요사항보고서(자기전환사채매도결정)", "other_bond", "convertible_bond"),
    ("기타경영사항(자율공시) (제9회차 전환사채 만기전 취득 후 재매각)", "other_bond", "convertible_bond"),
    ("기타주요경영사항 (제2회 전환사채 발행결정 철회)", "other_bond", "convertible_bond"),
    ("전환가액의조정", "refixing", "other"),
])
def test_bond_subtypes_preserve_existing_categories(title, subtype, category):
    row = parsed(title=title, body="")
    assert row.category == category and row.event_subtype == subtype


@pytest.mark.parametrize("self_acquisition", [False, True])
def test_early_repurchase_reads_acquired_face_amount_instead_of_interest_payment(self_acquisition):
    if self_acquisition:
        title = "주요사항보고서(자기전환사채만기전취득결정)"
        body = ("1. 사채의 종류 회차 4 종류 무기명식 전환사채 2. 사채발행일자 2022년 05월 20일 "
                "5. 사채의 권면(전자등록) 총액(원) 40,000,000,000 "
                "6. 취득 대상 사채의 권면(전자등록) 금액(원) 30,000,000,000 "
                "8. 취득금액 금액(원) 30,377,250,000")
    else:
        title = "전환사채(해외전환사채포함)발행후만기전사채취득"
        body = ("전환사채(해외전환사채) 4 회차 1. 만기전 취득 사채에 관한 사항 "
                "주당 전환가액(원) 17,652 2. 사채 취득금액 (통화단위) 30,377,250,000 KRW : South-Korean Won "
                "- 취득한 사채의 권면(전자등록)총액 (통화단위) 30,000,000,000 KRW : South-Korean Won "
                "3. 취득후 사채의 권면(전자등록)총액 (통화단위) 0 KRW : South-Korean Won")
    row = parsed(title=title, body=body)
    assert row.event_subtype == "early_repurchase" and row.parse_status == "ok"
    assert row.repurchased_amount_krw == 30_000_000_000 and row.bond_round == 4


def test_early_repurchase_structured_issue_amount_is_not_an_acquisition_amount():
    row = parsed(title="전환사채(해외전환사채포함)발행후만기전사채취득", body=(
        "[structured_endpoint] cvbdIsDecsn rcept_no: 20250522800001 bd_tm: 7 bd_fta: 2,000,000,000"
    ))
    assert row.parse_status == "partial" and row.bond_round == 7
    assert pd.isna(row.repurchased_amount_krw)


def test_bw_repurchase_round_in_acquisition_header():
    row = parsed(title="신주인수권부사채(해외신주인수권부사채포함)발행후만기전사채취득", body=(
        "신주인수권부사채(해외신주인수권부사채) 취득 3 회차 1. 만기전 취득 사채에 관한 사항 "
        "주당 신주인수권행사가액(원) 6,557 2. 사채 취득금액 (통화단위) 593,202,990 KRW : South-Korean Won "
        "- 취득한 사채의 권면(전자등록)총액 (통화단위) 570,000,000 KRW : South-Korean Won"
    ))
    assert row.parse_status == "ok" and row.bond_type == "bw" and row.bond_round == 3
    assert row.repurchased_amount_krw == 570_000_000


def test_conversion_exercise_measures_exercised_shares_separately():
    row = parsed(title="전환청구권ㆍ신주인수권ㆍ교환청구권행사 (전환사채의 전환청구권 행사)", body=(
        "1. 구분 전환사채권의 전환청구권 행사 "
        "2. 행사주식수 누계(주) (기 신고된 주식수량 제외) 12,491,809 "
        "- 발행주식총수(주) 27,656,302 - 발행주식총수 대비(%) 45.17"
    ))
    assert row.parse_status == "ok" and row.exercised_shares == 12_491_809 and row.exercised_shares_pct == 45.17
    assert pd.isna(row.conversion_shares)


@pytest.mark.parametrize("length,status", [(2499, "unparsed"), (2500, "truncated"), (2501, "unparsed")])
def test_correction_truncation_uses_original_character_count(length, status):
    body = "정정사항 정정전 정정후 전환가액(원/주) 100 200"
    row = parsed(title="[기재정정]주요사항보고서(전환사채권발행결정)", body=body.ljust(length))
    assert row.parse_status == status and pd.isna(row.conversion_price_krw)
    plain = parsed(title="주요사항보고서(전환사채권발행결정)", body="공시 본문 미수집".ljust(length))
    assert plain.parse_status == "unparsed"
