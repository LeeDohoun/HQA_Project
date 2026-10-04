"""Label-bound DART extraction, including receipt-matched structured API text.

Category rules are reused unchanged from event_evidence._category (whose CB
category also includes BW/EB). Missing or truncated fields stay NaN/NaT. `ok`
means all subtype-specific core fields were found; `partial` means at least one;
`unparsed` means none. Correction excerpts of exactly 2500 characters are marked
`truncated`. Optional refixing floors and provenance flags do not count toward
completeness. Unrelated categories are retained for B1 evaluation.

Corrected filings are parsed only from a numbered, restated main body, never
the old/new correction table. Each receipt remains a distinct historical event.
Explicit bond_round and bond_type distinguish CB/BW/EB revisions. Shared
conversion_* fields describe potential shares, including existing EB shares.
Repurchases use the acquired face amount, never the interest-inclusive payment
or the original issue amount. Structured rows from another receipt are rejected.
Share totals sum explicitly labelled stock classes; capital-raise amounts use
an explicit total, or sum labelled funding purposes. No price multiplication or
unlabelled prose number is used. Buyback disposals/cancellations are not plans.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd

from . import _runner_module
from .data import _timestamp

_CORE = {
    "contract": ("contract_amount_krw", "revenue_ratio_pct", "contract_start", "contract_end",
                 "counterparty_disclosed"),
    "convertible_bond": ("issue_amount_krw", "conversion_price_krw", "conversion_shares",
                         "conversion_shares_pct", "conversion_start", "conversion_end"),
    "capital_raise": ("new_shares", "raise_amount_krw", "method"),
    "buyback": ("planned_shares", "planned_amount_krw"),
}
_BOND_CORE = {
    "early_repurchase": ("repurchased_amount_krw", "bond_round"),
    "conversion_exercise": ("exercised_shares", "exercised_shares_pct"),
    "refixing": ("conversion_price_krw",),
}
_STRUCTURED_BONDS = {
    "cvbdIsDecsn": ("cv_prc", "cvisstk_cnt", "cvisstk_tisstk_vs", "cvrqpd_bgd", "cvrqpd_edd"),
    "exbdIsDecsn": ("ex_prc", "extg_stkcnt", "extg_tisstk_vs", "exrqpd_bgd", "exrqpd_edd"),
    "bdwtIsDecsn": ("ex_prc", "nstk_isstk_cnt", "nstk_isstk_tisstk_vs", "expd_bgd", "expd_edd"),
}
_START = {
    "contract": r"1\.\s*판매[ㆍ·]?\s*공급계약.{0,500}?2\.\s*계약내역",
    "convertible_bond": r"1\.\s*사채의\s*종류.{0,500}?2\.\s*사채의\s*권면",
    "capital_raise": r"1\.\s*신주의\s*종류.{0,500}?2\.\s*1주당\s*액면가액",
    "buyback": r"1\.\s*(?:취득예정주식.{0,300}?2\.\s*취득예정금액|계약금액.{0,300}?2\.\s*계약기간)",
}
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_SCALE = r"(?:천만|백만|조|억|만|천)"
_VALUE = rf"{_NUMBER}(?:\s*{_SCALE}(?:\s*{_NUMBER}\s*{_SCALE})*)?"
_MULTIPLIERS = {"": 1, "천": 1e3, "만": 1e4, "백만": 1e6, "천만": 1e7, "억": 1e8, "조": 1e12}
_DATE = r"\d{4}\s*(?:[-./]|년)\s*\d{1,2}\s*(?:[-./]|월)\s*\d{1,2}\s*일?"
_COLUMNS = ["event_id", "stock_code", "category", "event_subtype", "bond_type", "report_nm", "rcept_dt",
            "first_seen_at", "is_correction", "is_termination", "parse_status", "structured_source_mismatch",
            "bond_round", "repurchased_amount_krw", "exercised_shares", "exercised_shares_pct",
            "refixing_floor_pct", "refixing_floor_krw",
            *[field for fields in _CORE.values() for field in fields]]


def _number(text: str, label: str):
    units = rf"(?P<unit>(?:{_SCALE})?(?:원(?:/주)?|주)|%)"
    match = re.search(rf"{label}\s*(?:\(\s*{units}\s*\))?\s*[:：]?\s*"
                      rf"(?P<value>{_VALUE})(?![\d,])\s*(?P<suffix>원|주|%)?", text)
    if match is None:
        return np.nan
    parts = re.findall(rf"({_NUMBER})\s*({_SCALE})?", match["value"])
    value = sum(float(number.replace(",", "")) * _MULTIPLIERS[scale] for number, scale in parts)
    unit = re.sub(r"원(?:/주)?|주|%", "", match["unit"] or "")
    return value * _MULTIPLIERS[unit]


def _section(text: str, label: str) -> str:
    match = re.search(label, text)
    if match is None:
        return ""
    return re.split(r"\s+\d+\.\s+[가-힣]", text[match.end():], maxsplit=1)[0]


def _stock_total(text: str, label: str):
    direct = _number(text, label)
    if pd.notna(direct):
        return direct
    section = _section(text, label)
    values = [_number(section, kind + r"\s*주식") for kind in ("보통", "기타", "종류", "우선")]
    found = [value for value in values if pd.notna(value)]
    return sum(found) if found else np.nan


def _parsed_date(value):
    if not isinstance(value, str) or not re.fullmatch(_DATE, value.strip()):
        return pd.NaT
    year, month, day = re.findall(r"\d+", value)
    return pd.to_datetime(f"{year}-{int(month):02d}-{int(day):02d}", errors="coerce")


def _period(text: str, label: str) -> tuple:
    match = re.search(rf"{label}\s*(?:시작일\s*(?P<start>{_DATE}|-|미정))?"
                      rf"\s*(?:종료일\s*(?P<end>{_DATE}|-|미정))?", text)
    values = []
    for name in ("start", "end"):
        value = match[name] if match else None
        values.append(_parsed_date(value))
    return tuple(values)


def _bond_identity(title: str, category: str) -> tuple:
    text = re.sub(r"\s", "", title)
    kind = next((kind for kind, word in (("bw", "신주인수권부사채"), ("eb", "교환사채"),
                                        ("cb", "전환사채")) if word in text), np.nan)
    if re.search(r"(?:전환|교환|행사)가액(?:의)?조정", text):
        return "refixing", kind
    if re.search(r"(?:전환|교환)청구권.*행사|신주인수권행사", text):
        return "conversion_exercise", kind
    if category != "convertible_bond":
        return np.nan, np.nan
    if re.search(r"발행후만기전사채취득|자기(?:전환|교환)사채만기전취득결정", text):
        return "early_repurchase", kind
    if "발행결정" in text and not re.search(r"철회|취소", text):
        return "exchangeable_issuance" if kind == "eb" else "issuance", kind
    return "other_bond", kind


def _bond_round(body: str, title: str):
    value = _number(body, r"사채의\s*종류\s*회차")
    if pd.notna(value):
        return value
    match = re.search(r"(?:제\s*)?(\d+)\s*회(?:차)?", title) or re.search(
        r"(?:전환사채|교환사채|신주인수권부사채)\s*(?:\(해외[^)]*\))?\s*(?:취득\s*)?(\d+)\s*회차", body,
    )
    return float(match[1]) if match else np.nan


def _structured_features(fields: dict, endpoint: str, category: str) -> dict:
    if category != "convertible_bond" or endpoint not in _STRUCTURED_BONDS:
        return {}
    codes = ("bd_fta", *_STRUCTURED_BONDS[endpoint])
    features = {}
    for name, code in zip(_CORE[category], codes):
        value = str(fields.get(code, "")).strip()
        features[name] = _parsed_date(value) if name in {"conversion_start", "conversion_end"} else (
            _number("value: " + value, "value") if re.fullmatch(_VALUE, value) else np.nan
        )
    for name, code in (("bond_round", "bd_tm"), ("refixing_floor_krw", "act_mktprcfl_cvprc_lwtrsprc")):
        value = str(fields.get(code, "")).strip()
        features[name] = _number("value: " + value, "value") if re.fullmatch(_VALUE, value) else np.nan
    return features


def _features(body: str, category: str, title: str, subtype, bond_type) -> dict:
    fields = {field: np.nan for field in _CORE.get(category, ())}
    if category == "contract":
        fields.update(contract_amount_krw=_number(body, r"계약\s*금액"),
                      revenue_ratio_pct=_number(body, r"매출액\s*대비(?:\s*비율)?"))
        fields["contract_start"], fields["contract_end"] = _period(body, r"계약\s*기간")
        match = re.search(r"계약\s*상대(?:방)?\s*[:：]?\s*(.*?)"
                          r"(?=\s*-\s*회사와의\s*관계|\s+4\.\s*|$)", body)
        if match:
            value = match[1].strip()
            fields["counterparty_disclosed"] = bool(value and value != "-" and not re.search(
                r"유보|비공개|미공개|미정|비밀", value,
            ))
    elif category == "convertible_bond" or pd.notna(subtype):
        prefix = {"eb": "교환", "bw": "행사"}.get(bond_type, "전환")
        fields.update(issue_amount_krw=_number(body, r"사채의\s*권면(?:\s*\(전자등록\))?\s*총액"),
                      conversion_price_krw=_number(body, prefix + r"\s*가액"),
                      bond_round=_bond_round(body, title))
        label = {"eb": r"교환\s*대상\s*종류", "bw": r"신주인수권\s*행사에\s*따라\s*발행할\s*주식"}.get(
            bond_type, r"전환에\s*따라\s*발행할\s*주식",
        )
        conversion = _section(body, label)
        fields["conversion_shares"] = _number(conversion, r"주식\s*수")
        fields["conversion_shares_pct"] = _number(conversion, r"주식총수\s*대비(?:\s*비율)?")
        period = r"권리\s*행사기간" if bond_type == "bw" else prefix + r"\s*청구기간"
        fields["conversion_start"], fields["conversion_end"] = _period(body, period)
        if subtype == "early_repurchase":
            label = (r"(?:취득한\s*사채의\s*권면(?:\s*\(전자등록\))?\s*총액|"
                     r"취득\s*대상\s*사채의\s*권면(?:\s*\(전자등록\))?\s*금액)")
            if re.search(label + r"\s*\(통화단위\)", body):
                match = re.search(label + rf"\s*\(통화단위\)\s*({_VALUE})\s*KRW\b", body)
                fields["repurchased_amount_krw"] = _number("value: " + match[1], "value") if match else np.nan
            else:
                fields["repurchased_amount_krw"] = _number(body, label)
        elif subtype == "conversion_exercise":
            fields["exercised_shares"] = _number(body, r"행사주식수\s*누계(?:\s*\(주\))?"
                                                      r"(?:\s*\(기\s*신고된\s*주식수량\s*제외\))?")
            fields["exercised_shares_pct"] = _number(body, r"발행주식총수\s*대비")
        floor = _number(body, r"(?:리픽싱\s*하한|최저\s*조정\s*비율|조정\s*하한)")
        if pd.isna(floor):
            match = re.search(rf"최저\s*조정(?:가액|한도)[^%]{{0,220}}?({_NUMBER})\s*%"
                              r"\s*(?:에\s*해당|이상|까지|로)", body)
            floor = float(match[1].replace(",", "")) if match else np.nan
        fields["refixing_floor_pct"] = floor
    elif category == "capital_raise":
        fields["new_shares"] = _stock_total(body, r"신주의\s*종류와\s*수")
        amount = _number(body, r"(?:발행\s*\(모집\)\s*총액|모집\s*총액|신주\s*발행\s*총액)")
        if pd.isna(amount):
            purposes = _section(body, r"자금조달의\s*목적")
            values = [_number(purposes, label) for label in (
                r"시설자금", r"영업양수자금", r"운영자금", r"채무상환자금",
                r"타법인\s*증권\s*취득자금", r"기타자금",
            )]
            found = [value for value in values if pd.notna(value)]
            amount = sum(found) if found else np.nan
        fields["raise_amount_krw"] = amount
        method = re.search(r"증자방식\s*[:：]?\s*(제\s*3\s*자\s*배정|주주\s*배정|일반\s*공모)", body)
        if method:
            fields["method"] = re.sub(r"\s", "", method[1])
    elif category == "buyback":
        fields["planned_shares"] = _stock_total(body, r"취득\s*예정\s*주식(?:\s*\(주\))?")
        fields["planned_amount_krw"] = _stock_total(body, r"취득\s*예정\s*금액(?:\s*\(원\))?")
        if "신탁계약체결" in re.sub(r"\s", "", title):
            fields["planned_amount_krw"] = _number(body, r"계약\s*금액")
    return fields


def parse_disclosures(documents) -> pd.DataFrame:
    """Parse raw documents (also accepts enriched forward records) without I/O."""
    rows = []
    classify = _runner_module("event_evidence")._category
    for document in documents:
        meta = document.get("metadata", {})
        report = document.get("report_nm") or meta.get("report_nm") or document["title"]
        title = document.get("title") or report
        receipt = document.get("rcept_no") or meta.get("rcept_no")
        if not receipt or not document.get("stock_code"):
            raise ValueError("DART documents require rcept_no and stock_code")
        category = classify({"source_type": "dart", "title": title})
        subtype, bond_type = _bond_identity(report + " " + title, category)
        correction = "정정" in title or "정정" in report
        content = document.get("content", "")
        body = " ".join(unicodedata.normalize("NFC", content).split())
        marker = re.search(r"\[structured_endpoint\]\s*(\w+)", body)
        endpoint, structured = None, {}
        if marker:
            endpoint = marker[1]
            parts = re.split(r"(?:^|\s)([a-z][a-z0-9_]*):\s*", body[marker.end():])
            structured = {code: value.strip() for code, value in zip(parts[1::2], parts[2::2])}
            body = body[:marker.start()]
        if isinstance(meta.get("structured_row"), dict):
            endpoint, structured = meta.get("structured_endpoint"), meta["structured_row"]
        mismatch = bool(structured) and (str(structured.get("rcept_no")) != str(receipt) or (
            meta.get("structured_rcept_no") is not None and str(meta["structured_rcept_no"]) != str(receipt)
        ))
        pattern = _START.get(category)
        if subtype == "early_repurchase":
            pattern = (r"(?:전환사채|교환사채|신주인수권부사채)\s*\(해외[^)]*\)\s*(?:취득\s*)?\d+\s*회차\s*1\."
                       r"|1\.\s*사채의\s*종류.{0,500}?2\.\s*사채발행일자")
        if pattern:
            starts = list(re.finditer(pattern, body))
            if starts:
                body = body[starts[-1 if correction else 0].start():]
            elif correction:
                body = ""  # An old/new table alone is not the restated filing.
        features = _features(body, category, report + " " + title, subtype, bond_type)
        if structured and not mismatch:
            features.update(_structured_features(structured, endpoint, category))
        core = _BOND_CORE.get(subtype, _CORE.get(category, ()))
        parsed = sum(pd.notna(features[field]) for field in core)
        status = "ok" if core and parsed == len(core) else "partial" if parsed else "unparsed"
        if correction and len(content) == 2500:
            status = "truncated"
        received = document.get("rcept_dt") or meta.get("rcept_dt") or document.get("published_at")
        if received is None:
            raise ValueError("DART documents require rcept_dt or published_at")
        received = pd.Timestamp(received)
        if received.tzinfo is not None:
            received = received.tz_convert("Asia/Seoul")
        first_seen = document.get("first_seen_at")
        if pd.isna(first_seen):
            first_seen = meta.get("first_seen_at")
        rows.append({"event_id": str(receipt), "stock_code": document["stock_code"], "category": category,
                     "event_subtype": subtype, "bond_type": bond_type, "structured_source_mismatch": mismatch,
                     "report_nm": report, "rcept_dt": pd.Timestamp(received.date()),
                     "first_seen_at": _timestamp(first_seen) if pd.notna(first_seen) else pd.NaT,
                     "is_correction": correction, "is_termination": bool(re.search(r"해지|취소", title)),
                     "parse_status": status, **features})
    return pd.DataFrame(rows, columns=_COLUMNS)
