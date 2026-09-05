"""Disclosed corporate-action dates, not an exchange or adjusted-price calendar."""
from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.ingestion.dart import DartDisclosureCollector
from src.runner.event_evidence import _document, _timestamp

CORPORATE_ACTION_VERSION = "corporate-actions-v1"
_KST = ZoneInfo("Asia/Seoul")
# https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS005&apiId=2020024
_FRIC_DATES = {
    "nstk_asstd": "record_date",
    "nstk_dlprd": "new_share_delivery_date",
    "nstk_lstprd": "expected_listing_date",
    "bddd": "board_decision_date",
    "nstk_dividrk": "dividend_accrual_date",
}
_ACTION_TITLES = (
    ("\ubb34\uc0c1\uc99d\uc790", "bonus_issue"),
    ("\uc720\uc0c1\uc99d\uc790", "paid_in_capital_increase"),
    ("\uc8fc\uc2dd\ubd84\ud560", "stock_split"),
    ("\uc8fc\uc2dd\ubcd1\ud569", "reverse_split"),
    ("\ubc30\ub2f9", "dividend"),
)


def corporate_action_type(title: str) -> str | None:
    compact = re.sub(r"\s", "", title)
    return next((kind for token, kind in _ACTION_TITLES if token in compact), None)


def _date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    parts = (re.fullmatch(r"([0-9]{4})([0-9]{2})([0-9]{2})", text)
             or re.fullmatch(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", text)
             or re.fullmatch(r"([0-9]{4})\.([0-9]{2})\.([0-9]{2})", text)
             or re.fullmatch(r"([0-9]{4})\ub144\s*([0-9]{1,2})\uc6d4\s*([0-9]{1,2})\uc77c", text))
    if parts is None:
        return None
    try:
        return date(*(int(part) for part in parts.groups())).isoformat()
    except ValueError:
        return None


def build_corporate_action_context(documents: list[dict], as_of: datetime) -> dict:
    """Consume all latest-known per-stock documents before attention/event caps."""
    if not isinstance(documents, list):
        raise ValueError("corporate-action documents must be a list")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("corporate-action as_of must be an aware datetime")
    today = as_of.astimezone(_KST).date().isoformat()
    disclosures = {}
    gaps = {"complete_corporate_action_calendar_unavailable", "ex_dates_unavailable",
            "corporate_action_adjustment_unverified"}
    for raw in documents:
        row = _document(raw)
        if row["source_type"] != "dart" or _timestamp(row["available_at"]) > as_of:
            continue
        title = re.sub(r"\s", "", row["title"])
        action = corporate_action_type(title)
        if action is None:
            continue
        meta = row["metadata"]
        receipt = meta.get("rcept_no")
        if not isinstance(receipt, str) or not re.fullmatch(r"[0-9]{14}", receipt):
            raise ValueError("corporate-action DART receipt must contain 14 digits")
        current = disclosures.get(receipt)
        if current and row["available_at"] == current["row"]["available_at"] and row != current["row"]:
            raise ValueError("conflicting corporate-action revisions at the same availability")
        if current and row["available_at"] <= current["row"]["available_at"]:
            continue
        disclosures[receipt] = {"row": row, "action_type": action, "dates": [],
            "is_correction": meta.get("is_correction", False) or bool(re.search(
                r"\[(?:\uae30\uc7ac|\ucca8\ubd80|\ucca8\ubd80\ucd94\uac00)?\uc815\uc815\]", title)),
            "is_withdrawal": meta.get("is_withdrawal", False) or "\ucca0\ud68c" in title,
            "has_correction": meta.get("has_correction", False), "status": "disclosed"}

    by_id = {item["row"]["source_id"]: item for item in sorted(disclosures.values(),
                                                              key=lambda item: item["row"]["source_id"])}
    if len(by_id) != len(disclosures):
        raise ValueError("corporate-action source IDs must identify unique receipts")
    linked, replaced = {}, set()
    for source_id, item in by_id.items():
        row = item["row"]
        targets = row["metadata"].get("supersedes_source_ids", [])
        valid = [target for target in targets if target in by_id and target != source_id
                 and by_id[target]["action_type"] == item["action_type"]
                 and row["published_at"] >= by_id[target]["row"]["published_at"]]
        linked[source_id] = bool(valid) and len(valid) == len(targets)
        if linked[source_id]:
            replaced.update(valid)
            for target in valid:
                by_id[target]["status"] = "withdrawn" if item["is_withdrawal"] else "superseded"

    risks = []

    def risk(code: str, item: dict) -> None:
        row = item["row"]
        risks.append({"code": code, "action_type": item["action_type"],
            "source_ids": [row["source_id"]], "available_at": row["available_at"]})

    unresolved = set()
    for source_id, item in by_id.items():
        row, meta = item["row"], item["row"]["metadata"]
        if item["is_withdrawal"]:
            item["status"] = "withdrawn"
            risk("withdrawn_disclosure", item)
        # An old unlinked correction remains unresolved even if newer reports exist.
        codes = []
        if item["is_correction"] and not linked[source_id]:
            codes.append("unlinked_correction")
        if item["is_withdrawal"] and not linked[source_id]:
            codes.append("unlinked_withdrawal")
        if item["has_correction"] and source_id not in replaced:
            codes.append("subsequent_correction_unresolved")
        for code in codes:
            unresolved.add(item["action_type"])
            risk(code, item)
        fields = meta.get("structured_row")
        verified = bool(DartDisclosureCollector.structured_fields_content(row["title"], meta))
        if fields is not None and not verified:
            gaps.add("invalid_structured_evidence:" + source_id)
            risk("invalid_structured_evidence", item)
            unresolved.add(item["action_type"])
        if verified and meta.get("structured_endpoint") == "fricDecsn":
            for field, kind in _FRIC_DATES.items():
                value = fields.get(field)
                parsed = _date(value)
                if parsed is not None:
                    item["dates"].append({"provider_field": field, "date_kind": kind, "date": parsed})
                elif value not in (None, "", "-"):
                    gaps.add(f"invalid_corporate_action_date:{source_id}:{field}")
        if not item["dates"]:
            gaps.add("corporate_action_dates_unavailable:" + source_id)

    events = []
    for source_id, item in sorted(by_id.items()):
        row = item["row"]
        if item["status"] == "disclosed" and item["action_type"] in unresolved:
            item["status"] = "unresolved"
        if item["status"] in {"disclosed", "unresolved"}:
            if not item["dates"]:
                risk("calendar_dates_unavailable", item)
            if item["action_type"] != "paid_in_capital_increase":
                risk("price_basis_review", item)
        source = {key: row[key] for key in ("source_id", "url", "published_at",
                  "published_at_precision", "available_at")}
        source["rcept_no"] = row["metadata"]["rcept_no"]
        for entry in item["dates"]:
            events.append({**entry, "action_type": item["action_type"], "status": item["status"],
                "source_ids": [source_id], "sources": [source],
                "is_upcoming": item["status"] == "disclosed" and entry["date"] >= today})
    events.sort(key=lambda event: (event["date"], event["date_kind"], event["source_ids"]))
    for item_risk in risks:
        item = by_id[item_risk["source_ids"][0]]
        row = item["row"]
        item_risk["status"] = item["status"]
        item_risk["known_dates"] = sorted({entry["date"] for entry in item["dates"]})
        item_risk["sources"] = [{**{key: row[key] for key in ("source_id", "url", "published_at",
            "published_at_precision", "available_at")}, "rcept_no": row["metadata"]["rcept_no"]}]
    return {"version": CORPORATE_ACTION_VERSION, "coverage": "disclosed_events_only",
            "price_adjustment_status": "unverified", "events": events,
            "upcoming_events": [event for event in events if event["is_upcoming"]],
            "risks": risks, "data_gaps": sorted(gaps)}


def assess_price_basis(price_history: list[dict], context: dict, as_of: datetime) -> dict:
    """Flag known mechanical-action exposure; absence is not adjustment certification."""
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("price-basis as_of must be an aware datetime")
    if (not isinstance(price_history, list) or not price_history
            or any(not isinstance(row, dict) for row in price_history)):
        raise ValueError("price-basis review requires observed price history")
    bars = [(row, _timestamp(row.get("available_at"))) for row in price_history]
    bars = [(row, at) for row, at in bars if at <= as_of]
    if not bars:
        raise ValueError("price-basis review requires prices available as of analysis")
    start, end = (at.astimezone(_KST).date().isoformat() for at in (
        min(at for _, at in bars), max(at for _, at in bars)))
    mechanical = {"bonus_issue", "stock_split", "reverse_split"}
    unresolved_codes = {"unlinked_correction", "unlinked_withdrawal", "subsequent_correction_unresolved",
                        "invalid_structured_evidence"}
    unresolved_types = {risk["action_type"] for risk in context["risks"]
                        if risk["code"] in unresolved_codes}
    affected = set()
    for risk in context["risks"]:
        if risk["action_type"] not in mechanical:
            continue
        if risk["status"] not in {"disclosed", "unresolved"} and risk["code"] not in unresolved_codes:
            continue
        for source in risk["sources"]:
            published = _timestamp(source["published_at"])
            if (_timestamp(source["available_at"]) <= as_of and published <= as_of
                    and start <= published.astimezone(_KST).date().isoformat() <= end):
                affected.add(source["source_id"])
    for event in context["events"]:
        if event["action_type"] not in mechanical:
            continue
        if event["status"] not in {"disclosed", "unresolved"} and event["action_type"] not in unresolved_types:
            continue
        if (event["date_kind"] in {"record_date", "expected_listing_date", "new_share_delivery_date"}
                and start <= event["date"] <= end):
            affected.update(source["source_id"] for source in event["sources"]
                            if _timestamp(source["available_at"]) <= as_of
                            and _timestamp(source["published_at"]) <= as_of)
    raw = all(row.get("price_basis") == "unadjusted" and any(
        isinstance(row.get(key), str) and row[key].strip() for key in ("source", "source_url")) for row, _ in bars)
    gaps = set(context["data_gaps"])
    if not raw:
        gaps.add("price_basis_provenance_unverified")
    return {"status": "review_required" if affected else "unverified", "price_basis": "raw" if raw else "unknown",
            "entry_block_reasons": ["unverified_corporate_action_price_basis"] if affected else [],
            "source_ids": sorted(affected), "observed_window": {"start": start, "end": end},
            "warnings": sorted({risk["code"] for risk in context["risks"]}), "data_gaps": sorted(gaps)}
