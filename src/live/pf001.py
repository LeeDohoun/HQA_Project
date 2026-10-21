"""OP001 pf001 (continuous_intraday) operations: activation, input seal, NAV for D=25%, liquidation.

Contract: research/experiments/OP001_combined_paper_portfolio/pf001_contract.md (research branch).
Everything that writes is opt-in (execute=True) and every record is immutable
(shadow.write_immutable): activation.json, inputs/YYYYMMDD.json, nav/YYYYMMDD.json,
termination.json under <data_dir>/live/<strategy_id>/. Sleeve attribution lives in these
records only; the status payload shows totals.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path

from . import decisions, rebalance, shadow

DRAWDOWN_LIMIT = 0.25
INPUT_DEADLINE = time(8, 59)
NAV_BLOCKING_REASONS = ("NAV_CROSSCHECK_MISMATCH", "NAV_CLOSE_MISSING")


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strategy_dir(data_dir, strategy_id) -> Path:
    return Path(data_dir) / shadow.LIVE_DIR / strategy_id


def code_hashes(repo_root) -> dict:
    """sha256 of every src/live module and the strategy book, plus the git commit if available."""
    root = Path(repo_root)
    files = sorted((root / "src/live").glob("*.py")) + [root / "config/strategy_book.yaml"]
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                                timeout=10, check=True).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"commit": commit, "files": hashes, "algorithm": sha256_text(_canonical(hashes))}


def config_hash(entry) -> str:
    from dataclasses import asdict

    return sha256_text(_canonical(asdict(entry)))


def capital_parts(entry, t0_nav) -> tuple[float, float]:
    """(traded sleeve capital, cash reserved for sleeves without a source) from the T0 NAV."""
    traded = entry.traded_sleeve
    reserved = sum(s.weight for s in entry.sleeves if s is not traded) * float(t0_nav)
    return traded.weight * float(t0_nav), reserved


# ---------------------------------------------------------------- December extension points


def load_op001_targets(data_dir, day):
    """OP001 combined-target record `forward/op001/targets/YYYYMMDD.json` + op001.completion_proof.

    Extension point for December (first BB003 entry after 2026-12-01). Not implemented yet.
    """
    raise NotImplementedError("op001_combined_target_reader_not_implemented")


def plan_bb_slots(*args, **kwargs):
    """BB slots: min(slot cash, 1% of last confirmed NAV); exits in the CLOSE window with carry-over."""
    raise NotImplementedError("bb_slots_not_implemented")


def aggregate_sleeve_orders(*args, **kwargs):
    """Cross-sleeve same-direction aggregation (fills split by rebalance.allocate_fills)."""
    raise NotImplementedError("cross_sleeve_aggregation_not_implemented")


# ---------------------------------------------------------------- activation


def activation_path(data_dir, strategy_id) -> Path:
    return strategy_dir(data_dir, strategy_id) / "activation.json"


def load_activation(data_dir, strategy_id) -> dict | None:
    path = activation_path(data_dir, strategy_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def activate(ctx, entry, stage, *, execute=False, client=None, now=None) -> dict:
    """Seal activation.json: code/config hashes, account-identifier hash, T0 NAV, timestamp.

    Refuses unless the strategy is bound (backend accounts), stage paper, the account holds
    cash only with no reserved cash and no UNKNOWN orders, and the backend reports
    activationNavKrw. Without execute nothing is read from the backend or written.
    """
    now = now or shadow.now_kst()
    hashes = code_hashes(ctx.repo_root)
    base = {"strategy_id": entry.strategy_id, "t0": entry.t0, "code": hashes, "config_hash": config_hash(entry)}
    refusals = []
    if stage.effective != "paper":
        refusals.append(f"stage_not_paper:{stage.effective}")
    if not entry.account_user_id:
        refusals.append("account_user_id_missing")
    if refusals or not execute:
        return {"status": "refused" if refusals else "dry_run", "reasons": refusals, "record": base,
                "checks": ["account_bound", "cash_only", "no_reserved_cash", "no_unknown_orders",
                           "activation_nav_present"]}
    bound = [a for a in client.accounts() if a.get("userId") == entry.account_user_id]
    if not bound or bound[0].get("strategyId") != entry.strategy_id:
        refusals.append("account_not_bound_to_strategy")
    elif not isinstance(bound[0].get("accountBinding"), str) or not bound[0]["accountBinding"]:
        refusals.append("account_binding_missing")  # the backend's account fingerprint (field name to confirm)
    account = client.fetch_account(entry.account_user_id)
    risk = client.risk(entry.account_user_id)
    if account.holdings:
        refusals.append("account_not_cash_only")
    if account.reserved > 0:
        refusals.append("account_has_reserved_cash")
    if any(o.get("userId") == entry.account_user_id for o in client.unknown_orders()):
        refusals.append("account_has_unknown_orders")
    t0_nav = risk.get("activationNavKrw")
    if isinstance(t0_nav, bool) or not isinstance(t0_nav, (int, float)) or t0_nav <= 0:
        refusals.append("activation_nav_missing")
    if refusals:
        return {"status": "refused", "reasons": refusals, "record": base}
    binding = bound[0]["accountBinding"]
    record = {**base, "account_id_hash": sha256_text(f"{entry.account_user_id}|{binding}"),
              "t0_nav_krw": float(t0_nav), "activated_at": now.isoformat()}
    record, written = shadow.write_immutable(activation_path(ctx.data_dir, entry.strategy_id), record,
                                             ignore=("activated_at",))
    return {"status": "activated" if written else "already_activated", "record": record}


# ---------------------------------------------------------------- NAV (binding for D = 25%)


def nav_dir(data_dir, strategy_id) -> Path:
    return strategy_dir(data_dir, strategy_id) / "nav"


def nav_records(data_dir, strategy_id) -> list[dict]:
    directory = nav_dir(data_dir, strategy_id)
    if not directory.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))
            if not p.name.startswith(".")]


def close_nav(ctx, entry, account, session, *, execute=False, risk=None) -> dict:
    """NAV at the previous session's official KRX unadjusted close from the pre-session snapshot.

    NAV = settledCashD2Krw (KIS D+2 settled cash; snapshot, else GET risk) + holdings x close.
    orderableCash (D+0 deposit, ignores unsettled T+2 trades) is never used for the NAV; it is
    recorded for diagnostics only. Missing settled cash, or a name with neither a verified close
    nor a last confirmed price, makes the record valuation_missing (buys are then blocked).
    """
    previous = shadow.previous_session(session)
    quotes = shadow.load_quotes(ctx.data_dir, previous)
    earlier = nav_records(ctx.data_dir, entry.strategy_id)
    last_prices = {}
    for record in earlier:
        if record["sessionDate"] < previous:
            for code, item in (record.get("prices") or {}).items():
                if item.get("source") == "krx_close":
                    last_prices[code] = {"price": item["price"], "date": record["sessionDate"]}
    prices, missing = {}, []
    for code in sorted(account.holdings):
        q = (quotes or {}).get(code)
        if q is not None and q.verified and q.close is not None and q.close > 0:
            prices[code] = {"price": q.close, "source": "krx_close"}
        elif code in last_prices:
            prices[code] = {"price": last_prices[code]["price"], "source": "last_confirmed",
                            "confirmedDate": last_prices[code]["date"]}
        else:
            missing.append(code)
    holdings_value = sum(account.holdings[c] * prices[c]["price"] for c in prices)
    settled = account.settled_cash_d2
    if settled is None and isinstance((risk or {}).get("settledCashD2Krw"), (int, float)) \
            and not isinstance((risk or {}).get("settledCashD2Krw"), bool):
        settled = float(risk["settledCashD2Krw"])
    if settled is None:
        missing.append("settled_cash_d2_missing")
    cash = float(settled) if settled is not None else 0.0
    record = {"strategy_id": entry.strategy_id, "sessionDate": previous, "cashKrw": cash,
              "cashField": "settledCashD2Krw", "orderableCashKrw": float(account.cash),
              "definition": "settledCashD2Krw + sum(quantity x official unadjusted close); fees and taxes of "
                            "unsettled trades are not subtracted separately (KIS settled cash reflects them)",
              "fields": {"settledCashD2Krw": settled,
                         "settledCashSource": "snapshot" if account.settled_cash_d2 is not None
                         else "risk" if settled is not None else None,
                         "orderableCash": float(account.cash), "cashD1Krw": account.cash_d1,
                         "netAssetKrw": account.net_asset, "totalEvaluationKrw": account.total_evaluation,
                         "snapshotCapturedAt": account.captured_at.isoformat()},
              "holdingsValueKrw": holdings_value, "navKrw": cash + holdings_value,
              "holdings": dict(sorted(account.holdings.items())), "prices": prices,
              "valuationMissing": missing, "priceFile": quotes is not None, "source": "krx_official"}
    record["inputHash"] = sha256_text(_canonical({k: record[k] for k in ("cashKrw", "holdings", "prices",
                                                                       "sessionDate")}))
    if quotes is None:
        record["valuationMissing"] = sorted(set(record["valuationMissing"]) | set(account.holdings)
                                            | {"price_file_missing"})
    path = nav_dir(ctx.data_dir, entry.strategy_id) / f"{previous.replace('-', '')}.json"
    if path.is_file():
        # The first sealed close NAV for a session binds (a later pre-session run reuses it).
        return json.loads(path.read_text(encoding="utf-8"))
    if execute and not record["valuationMissing"]:
        record, _ = shadow.write_immutable(path, record, ignore=())
    return record


def drawdown_state(ctx, entry, nav_record, t0_nav) -> dict:
    navs = [r["navKrw"] for r in nav_records(ctx.data_dir, entry.strategy_id)
            if r["sessionDate"] != nav_record["sessionDate"]] + [nav_record["navKrw"]]
    peak = max([float(t0_nav), *navs])
    return {"navKrw": nav_record["navKrw"], "runningMaxKrw": peak, "limitKrw": (1 - DRAWDOWN_LIMIT) * peak,
            "breach": nav_record["navKrw"] <= (1 - DRAWDOWN_LIMIT) * peak,
            "drawdownPct": round((peak - nav_record["navKrw"]) / peak * 100, 4) if peak else 0.0}


def termination_path(data_dir, strategy_id) -> Path:
    return strategy_dir(data_dir, strategy_id) / "termination.json"


def load_termination(data_dir, strategy_id) -> dict | None:
    path = termination_path(data_dir, strategy_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def record_breach(ctx, entry, nav_record, state, *, now) -> dict:
    record = {"strategy_id": entry.strategy_id, "state": "terminated_drawdown", "reason": "drawdown_25pct",
              "sessionDate": nav_record["sessionDate"], "navKrw": nav_record["navKrw"],
              "runningMaxKrw": state["runningMaxKrw"], "limitKrw": state["limitKrw"],
              "navInputHash": nav_record["inputHash"], "recorded_at": now.isoformat()}
    record, _ = shadow.write_immutable(termination_path(ctx.data_dir, entry.strategy_id), record)
    return record


def nav_close_body(nav_record) -> dict:
    return {"sessionDate": nav_record["sessionDate"], "navKrw": round(nav_record["navKrw"]),
            "cashKrw": round(nav_record["cashKrw"]), "holdingsValueKrw": round(nav_record["holdingsValueKrw"]),
            "source": "krx_official", "inputHash": nav_record["inputHash"]}


# ---------------------------------------------------------------- input reference (sealed plan)


def inputs_path(data_dir, strategy_id, session) -> Path:
    return strategy_dir(data_dir, strategy_id) / "inputs" / f"{session.replace('-', '')}.json"


def plan_from_dict(data: dict) -> rebalance.RebalancePlan:
    return rebalance.RebalancePlan(
        data["strategy_id"], data["execution"], data["decision_hash"], data["decision_date"], data["session_date"],
        data["phase"], tuple(rebalance.Order(**o) for o in data["orders"]), tuple(data["skipped"]),
        tuple(data["targets"]), data["cash_before"], data["cash_after_estimate"], data["value_estimate"],
        data.get("meta") or {})


def liquidation_inputs_path(data_dir, strategy_id, session) -> Path:
    return strategy_dir(data_dir, strategy_id) / "inputs" / f"{session.replace('-', '')}_liquidation.json"


def input_deadline(session, tz) -> datetime:
    from datetime import date as _date

    return datetime.combine(_date.fromisoformat(session), INPUT_DEADLINE, tz)


def before_input_deadline(now, session) -> bool:
    """Strictly before 08:59:00 KST on the session date."""
    return now < input_deadline(session, shadow.KST)


def last_input_attempt(session, now) -> datetime:
    return input_deadline(session, shadow.KST) - timedelta(seconds=1)  # 08:58:59


def sealed_in_time(record) -> bool:
    try:
        return datetime.fromisoformat(record["sealedAt"]) < input_deadline(record["session"], shadow.KST)
    except (KeyError, TypeError, ValueError):
        return False


def input_record(ctx, entry, session, *, decision, account, nav_record, plans, terminated, buys_blocked, now) -> dict:
    """The sealed input reference: decision hash, snapshot, NAV, capped integer quantities, hashes."""
    hashes = code_hashes(ctx.repo_root)
    t0 = load_activation(ctx.data_dir, entry.strategy_id) or {}
    traded = entry.traded_sleeve
    sleeves = {s.name: {"weight": s.weight, "source": s.source,
                        "capital_krw": s.weight * float(t0.get("t0_nav_krw") or 0.0)} for s in entry.sleeves}
    return {"strategy_id": entry.strategy_id, "session": session,
            "decision": None if decision is None else {"month": decision.month, "recordHash": decision.record_hash,
                                                      "sealedAt": decision.sealed_at},
            "account": {"capturedAt": account.captured_at.isoformat(), "equity": account.equity,
                        "cash": account.cash, "reserved": account.reserved,
                        "holdings": dict(sorted(account.holdings.items()))},
            "nav": {k: nav_record.get(k) for k in ("sessionDate", "navKrw", "inputHash", "valuationMissing")},
            "sleeves": sleeves, "tradedSleeve": traded.name,
            "terminated": terminated, "buysBlocked": buys_blocked,
            "plans": {p.phase: p.to_dict() for p in plans},
            "algorithm": hashes["algorithm"], "commit": hashes["commit"], "configHash": config_hash(entry),
            "sealedAt": now.isoformat(), "kind": "liquidation" if terminated else "rebalance",
            "sealedBeforeDeadline": before_input_deadline(now, session)}


def decision_today(ctx, entry, session):
    """(decision or None, not-final row or None) for a session that is a FW001 trade date."""
    previous = shadow.previous_session(session)
    if previous[:7] == session[:7]:
        return None, None
    month = previous[:7]
    proof, reason, error = decisions.completion(ctx.data_dir, "decisions", month)
    if proof is None:
        return None, {"strategyId": entry.strategy_id, "month": month, "tradeDate": session, "state": reason,
                      "error": error}
    decision = decisions.decision_for_month(ctx.data_dir, entry.decision_rule, month, execution=entry.execution)
    if decision is None or not decision.forward or decision.trade_date != session:
        return None, {"strategyId": entry.strategy_id, "month": month, "tradeDate": session,
                      "state": "decision_not_tradable", "error": None}
    return decision, None
