"""Normalized raw-close input. Limit flags must be explicitly certified upstream."""
from datetime import date, timedelta
import math

from . import Refused
from .data import rows
from .storage import aware, file_hash


def close_input(distribution, day, *, at, calendar):
    if aware(at) <= aware(calendar.close(day)):
        raise Refused("daily close is not yet available")
    if aware(distribution.manifest["sync_verified_at"]) < aware(calendar.close(day)):
        raise Refused("execution distribution was verified before T close")
    relative = f"market/krx_daily/{day[:4]}/{day.replace('-', '')}.jsonl"
    path = distribution.path(relative)
    before = file_hash(path)
    quotes, markets = {}, set()
    for row in rows(path):
        if row["trade_date"] != day or row["calendar_status"] != "verified" or row.get("price_basis") != "unadjusted":
            raise Refused("execution prices require verified, unadjusted T-close rows")
        if row["stock_code"] in quotes:
            raise Refused("duplicate execution stock row")
        # The daily archive's base/price fields alone do not certify an exact
        # statutory price limit. Require the transferred official status sidecar.
        quotes[row["stock_code"]] = {"close": row["close"], "market": row["market"],
            "halted": row["volume"] == 0, "limit_up": None, "limit_down": None}
        markets.add(row["market"])
    if file_hash(path) != before:
        raise Refused("execution prices changed while reading")
    required = {market: market in markets for market in ("KOSPI", "KOSDAQ")}
    if not all(required.values()):
        raise Refused("ledger_not_final: both verified market close files are required")
    return quotes, required, {relative: before}


def apply_status(quotes, document, day):
    """Official daily limit/halt and corporate-action coverage is mandatory."""
    if document["day"] != day or document["coverage_verified"] is not True or not document["official_source"]:
        raise Refused("official daily status/action coverage is required")
    if set(quotes) - set(document["stocks"]):
        raise Refused("daily official limit/halt coverage is incomplete")
    result = {}
    for code, quote in quotes.items():
        status = document["stocks"][code]
        if any(type(status.get(key)) is not bool for key in ("limit_up", "limit_down", "halted")):
            raise Refused("official execution flags must be booleans")
        result[code] = {**quote, **{k: status[k] for k in ("limit_up", "limit_down", "halted")}}
    return result, document["corporate_actions"]


def prior_adv(distribution, day, calendar):
    """Independent prior-20 data for mandatory liquidation on unsealed-input days."""
    previous = calendar.previous(day)
    sessions = calendar.sessions((date.fromisoformat(previous) - timedelta(days=60)).isoformat(), previous)[-20:]
    if len(sessions) != 20:
        raise Refused("liquidation ADV requires twenty prior official sessions")
    sums, counts, hashes = {}, {}, {}
    for session in sessions:
        relative = f"market/krx_daily/{session[:4]}/{session.replace('-', '')}.jsonl"
        path = distribution.path(relative)
        checksum, seen, markets = file_hash(path), set(), set()
        for row in rows(path):
            code = row["stock_code"]
            if code in seen or row["trade_date"] != session or row["calendar_status"] != "verified":
                raise Refused("invalid prior-session liquidity row")
            seen.add(code)
            markets.add(row["market"])
            value = row["trading_value"]
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                    raise Refused("invalid prior-session trading value")
                sums[code], counts[code] = sums.get(code, 0) + value, counts.get(code, 0) + 1
        if not {"KOSPI", "KOSDAQ"}.issubset(markets) or file_hash(path) != checksum:
            raise Refused("prior-session liquidity lacks both stable market files")
        hashes[relative] = checksum
    return {c: value / 20 if counts[c] == 20 else None for c, value in sums.items()}, hashes


def index_closes(distribution, day):
    relative = "market_context/benchmarks.jsonl"
    path = distribution.path(relative)
    checksum = file_hash(path)
    levels = dict.fromkeys(("KOSPI", "KOSDAQ"))
    for row in rows(path):
        if row["trade_date"] == day and (row["series"], row["index_name"]) in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
            value = row["close"]
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise Refused("invalid price-index close")
            levels[row["series"]] = value
    if file_hash(path) != checksum:
        raise Refused("price-index archive changed while reading")
    return levels, {relative: checksum}
