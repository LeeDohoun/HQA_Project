"""Data-only field contract. Missing/unsupported PAPER fields stay unknown."""
from datetime import datetime
import math
import re

from . import KST, Refused

BOOK_FIELDS = ("date", "exchange", "session", "provider_time", "expected_price", "expected_volume",
               "expected_code", "status", "vi", "upper_limit", "lower_limit", "total_ask", "total_bid")
TRADE_FIELDS = ("date", "exchange", "provider_time", "price", "quantity", "status")


def field(data, path):
    if not isinstance(path, str) or not re.fullmatch(r"[a-zA-Z0-9_.]+", path):
        raise Refused("field profile must contain JSON field paths only")
    value = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def number(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


class Profile:
    def __init__(self, data):
        self.data = data
        if (set(data) - {"first_trade_values"} != {"book", "trade", "preopen_values", "krx_values", "halt_values", "delay_values",
                          "auction_values", "auction_volume_definition", "official_spec_sha256"}
                or not re.fullmatch(r"[0-9a-f]{64}", data["official_spec_sha256"])
                or not set(BOOK_FIELDS) <= set(data["book"])
                or not set(TRADE_FIELDS) <= set(data["trade"])):
            raise Refused("incomplete explicit PAPER field contract")
        for mapping in (data["book"], data["trade"]):
            for path in mapping.values():
                field({}, path)
        for side in ("ask", "bid"):
            if not all(f"{side}_{item}_{i}" in data["book"] for item in ("price", "quantity") for i in range(1, 11)):
                raise Refused("field profile requires all ten levels on both sides")

    def normalize(self, envelope, day, *, book):
        result = {"state": "unknown", "reason": envelope.get("error"), "exchange": None,
                  "date": None, "provider_at": None, "auction_volume": None, "auction_confirmed": False,
                  "first_trade_confirmed": False}
        data = envelope.get("data")
        if envelope.get("error") or not data:
            return result
        mapping = self.data["book" if book else "trade"]
        values = {key: field(data, path) for key, path in mapping.items()}
        result.update(values)
        provider_day = str(values["date"]).replace("-", "")
        if provider_day != day.replace("-", "") or values["exchange"] not in self.data["krx_values"]:
            result["reason"] = "unconfirmed_KRX_date_or_exchange"
            return result
        hour = str(values["provider_time"])
        if not re.fullmatch(r"\d{6}", hour):
            result["reason"] = "missing_provider_event_time"
            return result
        try:
            at = datetime.fromisoformat(day + "T" + hour[:2] + ":" + hour[2:4] + ":" + hour[4:]).replace(tzinfo=KST)
        except ValueError:
            result["reason"] = "invalid_provider_event_time"
            return result
        result.update(date=day, exchange="KRX", provider_at=at.isoformat())
        if values["status"] in self.data["halt_values"]:
            result.update(state="confirmed_absent", reason="official_halt")
            return result
        result["opening_delayed"] = values["status"] in self.data["delay_values"]
        if book:
            if values["session"] not in self.data["preopen_values"]:
                result["reason"] = "unconfirmed_preopen_session"
                return result
            for key in ("expected_price", "expected_volume", "upper_limit", "lower_limit", "total_ask", "total_bid"):
                result[key] = number(values[key])
            asks, bids = [], []
            for side, target in (("ask", asks), ("bid", bids)):
                for i in range(1, 11):
                    target.append([number(values[f"{side}_price_{i}"]), number(values[f"{side}_quantity_{i}"])])
            result.update(asks=asks, bids=bids)
            complete = (all(v is not None for pair in asks + bids for v in pair)
                        and all(pair[1] is not None and pair[1].is_integer() for pair in asks + bids)
                        and all(result[k] is not None for k in ("expected_price", "expected_volume", "total_ask", "total_bid",
                                                               "upper_limit", "lower_limit"))
                        and values["expected_code"] is not None and values["vi"] is not None)
            if not complete:
                result["reason"] = "missing_required_book_fields"
                return result
        else:
            result["price"], result["quantity"] = number(values["price"]), number(values["quantity"])
            if not result["price"] or not result["quantity"] or not result["quantity"].is_integer():
                result["reason"] = "no_confirmed_trade_print"
                return result
            if ("auction_flag" in values and values["auction_flag"] in self.data["auction_values"]
                    and self.data["auction_volume_definition"]):
                result["auction_confirmed"] = True
                result["auction_volume"] = number(values.get("auction_volume"))
            result["first_trade_confirmed"] = (isinstance(values["status"], str) and bool(values["status"].strip()) and (
                result["auction_confirmed"] or ("first_trade_flag" in values
                    and values["first_trade_flag"] in self.data.get("first_trade_values", []))))
        result.update(state="observed", reason=None)
        return result
