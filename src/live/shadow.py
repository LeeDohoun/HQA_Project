"""Shadow ledger: virtual fills at actual KRX daily open/close with the preregistered cost model.

Each session writes one immutable file, data/live/<strategy_id>/shadow/YYYYMMDD.json.
The state of a session is rebuilt from the latest prior entry; nothing else is cached.
Unfillable orders (missing row, volume 0, limit-up open for buys, limit-down for sells)
are recorded and leave the position/cash unchanged. Incidents are recorded, never hidden.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from backtesting import cost_model
from backtesting.experiments import hf001
from src.ingestion.storage import read_rows
from src.runner.trading_calendar import session_calendar

from . import decisions as decision_store
from . import rebalance

KST = ZoneInfo("Asia/Seoul")
PROTECTED_HOLDOUT = (date(2026, 1, 1), date(2026, 6, 30))
LIVE_DIR = Path("live")
MODES = ("observe", "shadow")
ADV_SESSIONS = 20
LIMIT_TOLERANCE = 0.005  # same as HF001/HC003: price_limit(day) - 0.005
# A ledger entry is written only after the KRX daily file for the session can be final.
LEDGER_WRITE_AFTER = time(16, 30)
# Reasons a shadow order did not fill that are not about the session's price row.
NON_PRICE_REASONS = ("cash_limited", "insufficient_cash", "cost_model_error")
_LEDGER_FILE = re.compile(r"^\d{8}\.json$")


class LedgerError(ValueError):
    """The ledger is garbled, out of order or would be rewritten with different content."""


class LedgerConflict(LedgerError):
    """An immutable file already exists with different content."""


class ShadowRefused(LedgerError):
    """No ledger entry may be built or written for this session (kind is machine-readable)."""

    def __init__(self, kind, detail):
        super().__init__(f"{kind}:{detail}")
        self.kind, self.detail = kind, detail


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def _day(value) -> date:
    return value if type(value) is date else date.fromisoformat(str(value)[:10])


# ---------------------------------------------------------------- calendar


@lru_cache(maxsize=8)
def _year_sessions(year: int) -> tuple[str, ...]:
    # One project calendar per year (building one is slow); raises for unverified years.
    return tuple(d.date().isoformat() for d in session_calendar(f"{year}-01-01", f"{year}-12-31"))


def sessions_between(start, end) -> list[str]:
    """Inclusive XKRX sessions via the project calendar (raises beyond the verified year)."""
    start, end = _day(start), _day(end)
    if start > end:
        raise ValueError("calendar start must not exceed end")
    days = [d for year in range(start.year, end.year + 1) for d in _year_sessions(year)]
    return [d for d in days if start.isoformat() <= d <= end.isoformat()]


def is_session(day) -> bool:
    return _day(day).isoformat() in sessions_between(day, day)


def previous_session(day) -> str:
    day = _day(day)
    earlier = [s for s in sessions_between(day - timedelta(days=20), day) if s < day.isoformat()]
    if not earlier:
        raise ValueError(f"no previous session for {day}")
    return earlier[-1]


def last_sessions(end, count) -> list[str]:
    end = _day(end)
    sessions = sessions_between(end - timedelta(days=2 * count + 20), end)
    if len(sessions) < count:
        raise ValueError("not enough sessions")
    return sessions[-count:]


# ---------------------------------------------------------------- prices


def guard_price_date(day) -> date:
    """Never open protected holdout KRX daily files (2026-01-01..2026-06-30)."""
    day = _day(day)
    if PROTECTED_HOLDOUT[0] <= day <= PROTECTED_HOLDOUT[1]:
        raise PermissionError(f"protected_holdout_date:{day.isoformat()}")
    return day


def krx_path(data_dir, day) -> Path:
    day = guard_price_date(day)
    return Path(data_dir) / "market" / "krx_daily" / f"{day:%Y}" / f"{day:%Y%m%d}.jsonl"


def _float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class Quote:
    stock_code: str
    market: str | None
    open: float | None
    close: float | None
    volume: float | None
    base_price: float | None
    trading_value: float | None
    verified: bool


def load_quotes(data_dir, day) -> dict[str, Quote] | None:
    """Latest stored version per stock for one KRX daily file; None when the file is missing."""
    path = krx_path(data_dir, day)
    if not path.is_file():
        return None
    latest = {}
    for row in read_rows(path):
        code = row.get("stock_code")
        if isinstance(code, str) and code:
            latest[code] = row  # save_episodes appends, so the last row is the latest version
    iso = _day(day).isoformat()
    return {code: Quote(code, row.get("market"), _float(row.get("open")), _float(row.get("close")),
                        _float(row.get("volume")), _float(row.get("base_price")), _float(row.get("trading_value")),
                        row.get("calendar_status") == "verified" and row.get("trade_date", iso) == iso)
            for code, row in latest.items()}


def adv20_from_files(data_dir, decision_date, codes) -> dict[str, float]:
    """Mean verified trading_value over the 20 sessions up to and including the decision date.

    A stock needs all 20 values (HC003: rolling(20, min_periods=20)); otherwise it has no ADV.
    """
    codes = set(codes)
    if not codes:
        return {}
    values = {code: [] for code in codes}
    for day in last_sessions(decision_date, ADV_SESSIONS):
        quotes = load_quotes(data_dir, day)
        if quotes is None:
            return {}
        for code in codes:
            q = quotes.get(code)
            if q is not None and q.verified and q.trading_value is not None and q.trading_value >= 0:
                values[code].append(q.trading_value)
    return {code: sum(v) / len(v) for code, v in values.items() if len(v) == ADV_SESSIONS}


def limit_threshold(day) -> float:
    return hf001.price_limit(_day(day).isoformat()) - LIMIT_TOLERANCE


def executable_price(quote: Quote | None, side: str, phase: str, day) -> tuple[float | None, str | None]:
    """Fill price for one side/phase, or the reason it cannot fill (HF001/HC003 limit rule vs base_price)."""
    if quote is None:
        return None, "missing_price_row"
    if not quote.verified:
        return None, "unverified_price_row"
    price = quote.open if phase == "OPEN" else quote.close
    if price is None or price <= 0 or quote.volume is None or quote.volume <= 0:
        return None, "halted"
    if quote.base_price is None or quote.base_price <= 0:
        return None, "missing_base_price"
    threshold = limit_threshold(day)
    if side == "BUY" and price >= quote.base_price * (1 + threshold):
        return None, "limit_up_" + phase.lower()
    if side == "SELL" and price <= quote.base_price * (1 - threshold):
        return None, "limit_down_" + phase.lower()
    return price, None


def plan_inputs(data_dir, decision, extra_codes=(), fallback_marks=None):
    """Reference closes (decision day, verified rows), markets and ADV20 for planning.

    ADV20 comes from the decision record's universe (the research value) and falls back
    to the KRX daily files of the 20 sessions up to the decision date.
    """
    quotes = load_quotes(data_dir, decision.decision_date)
    if quotes is None:
        raise rebalance.PlanError(f"price_file_missing:{decision.decision_date}")
    codes = set(decision.targets) | set(extra_codes)
    ref, markets = {}, {}
    for code in codes:
        q = quotes.get(code)
        if q is not None and q.verified and q.close is not None and q.close > 0:
            ref[code] = q.close
        elif fallback_marks and fallback_marks.get(code):
            ref[code] = fallback_marks[code]
        if q is not None and q.market in ("KOSPI", "KOSDAQ"):
            markets[code] = q.market
    adv = {code: decision.adv20[code] for code in codes if code in decision.adv20}
    missing = codes - set(adv)
    if missing:
        adv.update(adv20_from_files(data_dir, decision.decision_date, missing))
    return ref, markets, adv


# ---------------------------------------------------------------- immutable files


def encoded(record) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def _strip(record, ignore):
    return {k: v for k, v in record.items() if k not in ignore}


def write_immutable(path, record, *, ignore=("recorded_at",)) -> tuple[dict, bool]:
    """Fsync a temporary file and hard-link it into place (no clobber).

    An existing file with the same content (ignoring timestamps) is returned unchanged;
    different content raises LedgerConflict. Returns (record, written).
    """
    path = Path(path)
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        if _strip(previous, ignore) != _strip(json.loads(encoded(record)), ignore):
            raise LedgerConflict(f"immutable record differs: {path}")
        return previous, False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded(record))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return write_immutable(path, record, ignore=ignore)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return record, True
    finally:
        os.unlink(temporary)


# ---------------------------------------------------------------- ledger


def ledger_dir(data_dir, strategy_id) -> Path:
    return Path(data_dir) / LIVE_DIR / strategy_id / "shadow"


def ledger_path(data_dir, strategy_id, day) -> Path:
    return ledger_dir(data_dir, strategy_id) / f"{_day(day):%Y%m%d}.json"


def read_ledger(data_dir, strategy_id) -> list[dict]:
    directory = ledger_dir(data_dir, strategy_id)
    if not directory.is_dir():
        return []
    entries = []
    for path in sorted(p for p in directory.iterdir() if p.is_file() and _LEDGER_FILE.match(p.name)):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LedgerError(f"unreadable ledger entry {path.name}: {error}") from None
        if not isinstance(entry, dict) or entry.get("strategy_id") != strategy_id:
            raise LedgerError(f"ledger entry {path.name} belongs to another strategy")
        if not isinstance(entry.get("date"), str) or entry["date"].replace("-", "") != path.stem:
            raise LedgerError(f"ledger entry {path.name} date does not match its file name")
        if entry.get("mode") not in MODES:
            raise LedgerError(f"ledger entry {path.name} has an unknown mode")
        entries.append(entry)
    return entries


# ---------------------------------------------------------------- fills


def _initial_state(entry):
    if entry.execution == "basket_20":
        return {"baskets": []}
    return {"last_decision_hash": None, "markets": {}}


def _holdings(entry, state, holdings):
    if entry.execution == "basket_20":
        total = {}
        for basket in state["baskets"]:
            for code, qty in basket["holdings"].items():
                total[code] = total.get(code, 0) + qty
        return total
    return holdings


def _fill(order, phase, quotes, day, cash, model_version, incidents):
    price, reason = executable_price(None if quotes is None else quotes.get(order.stock_code),
                                     order.side, phase, day)
    if quotes is None:
        reason = "price_file_missing"
    row = {"seq": order.seq, "stock_code": order.stock_code, "side": order.side, "phase": phase,
           "quantity_planned": order.quantity, "quantity": 0, "ref_price": order.ref_price,
           "fill_price": None, "notional": 0.0, "cost_rate": None, "cost_krw": 0.0, "filled": False,
           "reason": reason, "plan_reason": order.reason, "adv20_krw": order.adv20_krw,
           "adv_warning": order.adv_warning}
    if reason is not None:
        return row, 0.0
    market = order.market or quotes[order.stock_code].market
    try:
        rate = cost_model.one_way_cost(price, market, day.replace("-", ""), float(order.adv20_krw),
                                       order.side.lower(), model_version=model_version)
    except (ValueError, TypeError) as error:
        incidents.append(f"cost_model_error:{order.stock_code}:{error}")
        row["reason"] = "cost_model_error"
        return row, 0.0
    qty = order.quantity
    if order.side == "BUY" and qty * price * (1 + rate) > cash + 1e-9:
        qty = math.floor(cash / (price * (1 + rate)))
        row["reason"] = "cash_limited" if qty > 0 else "insufficient_cash"
        if qty <= 0:
            return row, 0.0
    notional = qty * price
    cost = notional * rate
    row.update(quantity=qty, fill_price=price, notional=notional, cost_rate=rate, cost_krw=cost, filled=True)
    return row, (notional - cost if order.side == "SELL" else -(notional + cost))


def _fill_prices_match(rows, quotes) -> bool:
    for row in rows:
        if row["filled"]:
            q = quotes.get(row["stock_code"]) if quotes else None
            expected = None if q is None else (q.open if row["phase"] == "OPEN" else q.close)
            if expected is None or row["fill_price"] != expected:
                return False
    return True


def _targets_match(entry, plan, decision, held_before) -> bool:
    targets = set(decision.targets)
    buys = {o.stock_code for o in plan.orders if o.side == "BUY"}
    sells = {o.stock_code for o in plan.orders if o.side == "SELL"}
    skipped = {s["stock_code"] for s in plan.skipped}
    if entry.execution == "basket_20":
        accounted = buys | {s["stock_code"] for s in plan.skipped if s["side"] == "BUY"}
        return set(plan.targets) == targets and accounted == targets
    covered = buys | sells | skipped
    return (set(plan.targets) == targets and targets <= covered and buys <= targets
            and sells <= set(held_before) and covered <= targets | set(held_before))


def execution_path(data_dir, month) -> Path:
    from src.forward import fw001

    return fw001.path_for(data_dir, "executions", month, 1.0)


def _same_price(a, b) -> bool:
    a, b = _float(a), _float(b)
    return a is not None and b is not None and abs(a - b) <= 1e-9 * max(1.0, abs(b))


def independent_check(entry, decision, order_rows, data_dir):
    """Compare the entry session's fills with FW001's own final execution record (read-only).

    Returns (result, reason, error). result is True/False, or "unavailable" when the FW001
    1.0x execution is absent, unsealed, late, or lacks a needed field. Corrupt or
    inconsistent completion evidence is False with reason execution_seal_invalid and the
    error message (never treated as missing). Only orders the shadow tried to fill are
    compared: price-fillability (filled, or blocked only by cash/cost) and the fill price.
    """
    proof, reason, error = decision_store.completion(data_dir, "executions", decision.month, 1.0)
    if reason == "execution_seal_invalid":
        return False, reason, error
    if proof is None:
        return "unavailable", reason, None
    try:
        record = json.loads(execution_path(data_dir, decision.month).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, "execution_seal_invalid", str(exc)
    if record.get("decision_hash") != decision.record_hash:
        return False, "execution_decision_hash_mismatch", None
    if record.get("trade_date") not in (None, decision.trade_date):
        return False, "execution_trade_date_mismatch", None
    result, reason = _compare_execution(entry, record, order_rows)
    return result, reason, None


def _compare_execution(entry, record, order_rows):
    tried = [r for r in order_rows if not r.get("skipped") and r.get("phase") == "OPEN"]
    if entry.execution == "basket_20":
        slots = (record.get("f_both") or {}).get("slots")
        if not isinstance(slots, dict):
            return "unavailable", "execution_field_missing"
        for row in tried:
            slot = slots.get(row["stock_code"])
            if not isinstance(slot, dict) or not isinstance(slot.get("filled"), bool):
                return "unavailable", "execution_field_missing"
            fillable = row["filled"] or row["reason"] in NON_PRICE_REASONS
            if fillable != slot["filled"]:
                return False, "execution_fill_mismatch"
            if row["filled"] and not _same_price(row["fill_price"], slot.get("raw_open")):
                return False, "execution_fill_mismatch"
        return True, None
    av_all = record.get("av_all") or {}
    quotes = record.get("quotes")
    if av_all.get("status") != "executed" or not isinstance(quotes, dict):
        return "unavailable", "execution_field_missing"
    for row in tried:
        quote = quotes.get(row["stock_code"])
        if not isinstance(quote, dict) or not all(isinstance(quote.get(k), bool)
                                                  for k in ("tradable", "limit_up", "limit_down")):
            return "unavailable", "execution_field_missing"
        expected = quote["tradable"] and not quote["limit_down"] and not (row["side"] == "BUY" and quote["limit_up"])
        fillable = row["filled"] or row["reason"] in NON_PRICE_REASONS
        if fillable != expected:
            return False, "execution_fill_mismatch"
        if row["filled"] and not _same_price(row["fill_price"], quote.get("raw_open")):
            return False, "execution_fill_mismatch"
    return True, None


def shadow_fill(entry, session_date, data_dir, *, mode, execute=False, recorded_at=None, now=None) -> dict:
    """Build (and with execute=True, write) the ledger entry for one session.

    Raises ShadowRefused when the session's KRX daily file is missing or unreadable, and,
    with execute=True, before 16:30 KST of the session date.
    """
    if mode not in MODES:
        raise ValueError(f"unknown shadow mode: {mode}")
    session = _day(session_date).isoformat()
    if not is_session(session):
        raise ValueError(f"not_a_krx_session:{session}")
    if execute:
        current = now or now_kst()
        if current.tzinfo is None:
            raise ValueError("shadow write time must be timezone-aware")
        if current < datetime.combine(_day(session), LEDGER_WRITE_AFTER, KST):
            raise ShadowRefused("session_not_closed", f"{session}:write_after_{LEDGER_WRITE_AFTER:%H:%M}_KST")
    ledger = read_ledger(data_dir, entry.strategy_id)
    if any(e["date"] > session for e in ledger):
        raise LedgerError(f"ledger has entries after {session}; sessions are recorded in order")
    prior = next((e for e in reversed(ledger) if e["date"] < session), None)
    incidents, warnings, order_rows, match = [], [], [], {"targets_match": True, "fill_prices_match": True}
    if prior is None:
        cash, holdings, marks = float(entry.shadow_nav_krw), {}, {}
        state = _initial_state(entry)
    else:
        cash = float(prior["cash_after"])
        holdings = {code: int(qty) for code, qty in prior["holdings_after"].items()}
        marks = dict(prior.get("marks_close") or {})
        state = copy.deepcopy(prior["state_after"])
        gap = sessions_between(prior["date"], session)[1:-1]
        if gap:
            incidents.append(f"ledger_gap:{len(gap)}:{gap[0]}..{gap[-1]}")
    try:
        quotes = load_quotes(data_dir, session)
    except ValueError as error:
        raise ShadowRefused("price_file_unreadable", f"{session}:{error}") from None
    if quotes is None:
        raise ShadowRefused("price_file_missing", session)
    held_before = dict(holdings)

    acting, decision_hashes = None, []
    previous = previous_session(session)
    if previous[:7] != session[:7]:
        month = previous[:7]
        try:
            found = decision_store.decision_for_month(data_dir, entry.decision_rule, month,
                                                      execution=entry.execution)
        except decision_store.DecisionError as error:
            found = None
            incidents.append(f"decision_unreadable:{month}:{error}")
        else:
            if found is None:
                incidents.append(f"decision_missing:{month}")
            elif found.label == "forward" and not found.sealed or found.seal_reason:
                incidents.append(f"{found.seal_reason}:{month}"
                                 + (f":{found.seal_error}" if found.seal_error else ""))
            elif not found.forward:
                incidents.append(f"decision_not_forward:{month}:{found.label}")
            elif found.trade_date != session:
                incidents.append(f"decision_trade_date_mismatch:{month}:{found.trade_date}")
            else:
                acting = found

    version = entry.cost_model_version
    # OPEN: entries and continuous rebalances.
    if acting is not None and entry.execution == "basket_20":
        if any(b["decision_hash"] == acting.record_hash for b in state["baskets"]):
            warnings.append(f"basket_already_entered:{acting.record_hash}")
        else:
            if state["baskets"]:
                warnings.append("basket_overlap:" + ",".join(b["decision_hash"][:12] for b in state["baskets"]))
            decision_hashes.append(acting.record_hash)
            try:
                ref, markets, adv = plan_inputs(data_dir, acting)
                plan = rebalance.plan_basket_entry(entry.strategy_id, acting, cash=cash, ref_prices=ref,
                                                   markets=markets, adv20=adv, model_version=version)
            except (rebalance.PlanError, ValueError) as error:
                incidents.append(f"plan_failed:OPEN:{error}")
                match["targets_match"] = False
            else:
                match["targets_match"] &= _targets_match(entry, plan, acting, holdings)
                basket = {"decision_hash": acting.record_hash, "decision_date": acting.decision_date,
                          "trade_date": acting.trade_date, "exit_date": acting.exit_date, "holdings": {},
                          "markets": {}, "adv20": {}}
                for order in plan.orders:
                    row, delta = _fill(order, "OPEN", quotes, session, cash, version, incidents)
                    order_rows.append(row)
                    if row["filled"]:
                        cash += delta
                        basket["holdings"][order.stock_code] = row["quantity"]
                        basket["markets"][order.stock_code] = order.market
                        basket["adv20"][order.stock_code] = order.adv20_krw
                order_rows.extend({"stock_code": s["stock_code"], "side": s["side"], "phase": "OPEN",
                                   "filled": False, "reason": s["reason"], "skipped": True} for s in plan.skipped)
                state["baskets"].append(basket)
    elif acting is not None:
        if state.get("last_decision_hash") == acting.record_hash:
            warnings.append(f"rebalance_already_applied:{acting.record_hash}")
        else:
            decision_hashes.append(acting.record_hash)
            try:
                ref, markets, adv = plan_inputs(data_dir, acting, extra_codes=holdings, fallback_marks=marks)
                for code in holdings:
                    markets.setdefault(code, state.get("markets", {}).get(code))
                    if markets[code] is None:
                        del markets[code]
                plan = rebalance.plan_continuous(entry.strategy_id, acting, holdings=holdings, cash=cash,
                                                 ref_prices=ref, markets=markets, adv20=adv,
                                                 model_version=version, band=entry.band)
            except (rebalance.PlanError, ValueError) as error:
                incidents.append(f"plan_failed:OPEN:{error}")
                match["targets_match"] = False
            else:
                match["targets_match"] &= _targets_match(entry, plan, acting, holdings)
                for order in plan.orders:  # sells precede buys by seq
                    row, delta = _fill(order, "OPEN", quotes, session, cash, version, incidents)
                    order_rows.append(row)
                    if row["filled"]:
                        cash += delta
                        sign = 1 if order.side == "BUY" else -1
                        holdings[order.stock_code] = holdings.get(order.stock_code, 0) + sign * row["quantity"]
                        if holdings[order.stock_code] <= 0:
                            del holdings[order.stock_code]
                        state.setdefault("markets", {})[order.stock_code] = order.market
                order_rows.extend({"stock_code": s["stock_code"], "side": s["side"], "phase": "OPEN",
                                   "filled": False, "reason": s["reason"], "skipped": True} for s in plan.skipped)
                state["last_decision_hash"] = acting.record_hash

    # CLOSE: basket exits on (or, as retries, after) the 20th session.
    if entry.execution == "basket_20":
        remaining = []
        for basket in state["baskets"]:
            if basket["exit_date"] > session or basket["decision_hash"] in decision_hashes:
                remaining.append(basket)
                continue
            decision_hashes.append(basket["decision_hash"])
            if basket["exit_date"] < session:
                warnings.append(f"exit_retry:{basket['decision_hash'][:12]}:{basket['exit_date']}")
            try:
                ref = {code: marks.get(code) for code in basket["holdings"]}
                plan = rebalance.plan_basket_exit(
                    entry.strategy_id, decision_hash=basket["decision_hash"], decision_date=basket["decision_date"],
                    session_date=session, holdings=basket["holdings"], cash=cash, ref_prices=ref,
                    markets=basket["markets"], adv20=basket["adv20"], model_version=version)
            except (rebalance.PlanError, ValueError) as error:
                incidents.append(f"plan_failed:CLOSE:{error}")
                remaining.append(basket)
                continue
            for order in plan.orders:
                row, delta = _fill(order, "CLOSE", quotes, session, cash, version, incidents)
                order_rows.append(row)
                if row["filled"]:
                    cash += delta
                    basket["holdings"][order.stock_code] -= row["quantity"]
                    if basket["holdings"][order.stock_code] <= 0:
                        del basket["holdings"][order.stock_code]
            if basket["holdings"]:
                remaining.append(basket)
        state["baskets"] = remaining
        holdings = _holdings(entry, state, holdings)

    match["fill_prices_match"] = _fill_prices_match(order_rows, quotes)
    if acting is None or acting.record_hash not in decision_hashes:
        match["independent_check"], match["independent_check_reason"] = "not_applicable", None
    else:
        result, reason, error = independent_check(entry, acting, order_rows, data_dir)
        match["independent_check"], match["independent_check_reason"] = result, reason
        if error is not None:
            incidents.append(f"{reason}:{acting.month}:{error}")
    # Every code due today (targets acted on, orders, holdings before/after) must have a row.
    due = set(held_before) | {r["stock_code"] for r in order_rows} | set(_holdings(entry, state, holdings))
    if acting is not None and acting.record_hash in decision_hashes:
        due |= set(acting.targets)
    incidents.extend(f"price_row_missing:{code}" for code in sorted(due) if code not in quotes)
    # Mark at this session's close; a stock without a verified close keeps its last mark.
    marks_close = {}
    for code in sorted(holdings):
        q = quotes.get(code) if quotes else None
        if q is not None and q.verified and q.close is not None and q.close > 0:
            marks_close[code] = q.close
        elif marks.get(code):
            marks_close[code] = marks[code]
            warnings.append(f"stale_mark:{code}")
        else:
            filled = [r["fill_price"] for r in order_rows if r.get("stock_code") == code and r.get("filled")]
            if filled:
                marks_close[code] = filled[-1]
                warnings.append(f"stale_mark:{code}")
            else:
                incidents.append(f"unvalued_holding:{code}")
                marks_close[code] = 0.0
    nav = cash + sum(qty * marks_close[code] for code, qty in holdings.items())
    record = {"strategy_id": entry.strategy_id, "date": session, "mode": mode,
              "execution": entry.execution, "cost_model_version": version,
              "decision_hash": decision_hashes[0] if decision_hashes else None,
              "decision_hashes": decision_hashes, "initial_nav_krw": entry.shadow_nav_krw,
              "orders": order_rows, "holdings_after": {c: holdings[c] for c in sorted(holdings)},
              "cash_after": cash, "marks_close": marks_close, "nav_close_krw": nav,
              "state_after": state, "match": match, "incidents": incidents, "warnings": warnings,
              "previous_date": None if prior is None else prior["date"],
              "recorded_at": (recorded_at or now_kst()).isoformat()}
    record = json.loads(encoded(record))
    if execute:
        record, written = write_immutable(ledger_path(data_dir, entry.strategy_id, session), record)
        return {**record, "_written": written}
    existing = ledger_path(data_dir, entry.strategy_id, session)
    if existing.exists():
        previous = json.loads(existing.read_text(encoding="utf-8"))
        if _strip(previous, ("recorded_at",)) != _strip(record, ("recorded_at",)):
            raise LedgerConflict(f"immutable record differs: {existing}")
    return record


# ---------------------------------------------------------------- operational incidents outside the ledger


def incidents_dir(data_dir, strategy_id) -> Path:
    return Path(data_dir) / LIVE_DIR / strategy_id / "incidents"


def record_incident(data_dir, strategy_id, day, kind, detail, *, recorded_at=None) -> Path:
    """Immutable incident file for failures that cannot enter the ledger (e.g. a conflict)."""
    body = {"strategy_id": strategy_id, "date": _day(day).isoformat(), "kind": kind, "detail": str(detail)}
    suffix = hashlib.sha256(encoded(body).encode()).hexdigest()[:12]
    path = incidents_dir(data_dir, strategy_id) / f"{_day(day):%Y%m%d}_{kind}_{suffix}.json"
    write_immutable(path, {**body, "recorded_at": (recorded_at or now_kst()).isoformat()})
    return path


def read_incidents(data_dir, strategy_id) -> list[dict]:
    directory = incidents_dir(data_dir, strategy_id)
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(p for p in directory.iterdir() if p.is_file() and p.suffix == ".json"
                       and not p.name.startswith(".")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            raise LedgerError(f"unreadable incident file {path.name}: {error}") from None
    return rows
