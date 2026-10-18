"""Cost-independent cohort quotes, HF001 exits and sealed cost accounting."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from backtesting import cost_model
from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments.hf001 import price_limit

from . import EVALUATION_AT, Refused
from .calendar import exchange
from .inputs import price_path, prices_from_view
from .runner import verify_pinned
from .distribution import latest as latest_distribution
from .storage import aware, digest, file_hash, now, read, seal

PRICE_END = "2027-12-03"
MULTIPLIERS = (1.0, 1.5, 2.0)


def _adv(frame, pos):
    values = frame.trading_value.iloc[max(0, pos - 20):pos].to_numpy(float)
    if len(values) != 20 or not np.isfinite(values).all() or (values < 0).any():
        raise Refused("execution requires the preceding 20 verified session trading values")
    return float(values.mean())


def quote_cohort(prices, decision, sessions):
    """Every input-universe stock is valued, without sampling or cost evaluation."""
    sessions = pd.DatetimeIndex(sessions)
    if sessions[-1] > pd.Timestamp(PRICE_END):
        raise Refused("LH003 prices/presence cannot extend past the registered 2027-12-03 price end")
    entry, target = (sessions.get_loc(pd.Timestamp(decision[field])) for field in ("entry_date", "exit_date"))
    if target != entry + 19:
        raise Refused("exit must be entry-inclusive twentieth XKRX session")
    thresholds = np.array([price_limit(day) - 0.005 for day in sessions])
    rows = {}
    for code in sorted(set(decision["universe_codes"]) | set(decision["a_codes"])):
        if code in prices.index.get_level_values("stock_code"):
            frame = prices.xs(code, level="stock_code").reindex(sessions)
        else:
            frame = pd.DataFrame(index=sessions, columns=prices.columns)
        opening, closing, volume = (frame[key].to_numpy(float) for key in ("open", "close", "volume"))
        base = frame.base_price.to_numpy(float)
        base = np.where(np.isnan(base), np.r_[np.nan, closing[:-1]], base)
        has_base = np.isfinite(base) & (base > 0)
        trades = np.isfinite(closing) & (closing > 0) & np.isfinite(volume) & (volume > 0)
        down = has_base & (closing <= base * (1 - thresholds))
        permissible = np.isfinite(closing) & (closing > 0) & ~down
        reason = None
        if not np.isfinite(opening[entry]):
            reason = "missing_entry_price"
        elif opening[entry] <= 0 or not np.isfinite(volume[entry]) or volume[entry] <= 0:
            reason = "untradable_entry"
        elif not has_base[entry]:
            raise Refused("entry price-limit reference missing: " + code)
        elif opening[entry] >= base[entry] * (1 + thresholds[entry]):
            reason = "limit_up_entry"
        row = {"stock_code": code, "filled": reason is None, "reason": reason,
               "entry_date": decision["entry_date"], "scheduled_exit_date": decision["exit_date"]}
        if reason is None:
            resumed = bool(trades[target + 1:].any())
            delisted = bool(not trades[target] and not resumed)
            choices = np.flatnonzero(permissible[:target + 1] & (trades[:target + 1] if delisted else True))
            if not len(choices) or choices[-1] < entry:
                raise Refused("entered position has no permissible HF001 exit: " + code)
            actual = int(choices[-1])
            chain = frame.ret_1d.iloc[entry + 1:actual + 1].to_numpy(float)
            adjusted = np.isfinite(closing[entry]) and closing[entry] > 0 and np.isfinite(chain).all() and (chain >= -1).all()
            gross = closing[entry] / opening[entry] * np.prod(1 + chain) - 1 if adjusted else closing[actual] / opening[entry] - 1
            entry_market, exit_market = frame.market.iloc[entry], frame.market.iloc[actual]
            if entry_market not in {"KOSPI", "KOSDAQ"} or exit_market not in {"KOSPI", "KOSDAQ"}:
                raise Refused("execution market missing: " + code)
            row.update(gross=float(gross), gross_delisting_loss=-1.0 if delisted else float(gross),
                       entry_price=float(opening[entry]), exit_price=float(closing[actual]),
                       entry_market=entry_market, exit_market=exit_market,
                       entry_adv_20=_adv(frame, entry), exit_adv_20=_adv(frame, actual),
                       exit_date=sessions[actual].date().isoformat(), delisted=delisted,
                       halted_exit=bool(not trades[target] and resumed),
                       stale_exit=bool(not trades[target] or down[target]), limit_down_exit=bool(down[target]),
                       unadjusted_fallback=not bool(adjusted))
        rows[code] = row
    return {"index": decision["index"], "decision_date": decision["decision_date"],
            "decision_sha256": digest(decision), "price_end": sessions[-1].date().isoformat(), "quotes": rows}


def record_executions(*, data_dir, root, decisions, sync_manifest, clock=now, audit=False, repo_root=PROJECT_ROOT,
                      distribution_dir=None):
    """Save quotes without ever consulting the tax table, including in 2027."""
    root = Path(root)
    applied = verify_pinned(root, repo_root, at=clock())
    publication, receipt = latest_distribution(root / "distributions" if distribution_dir is None else distribution_dir,
                                               sync_manifest=sync_manifest, at=clock())
    data_dir = publication.root
    manifest = read(sync_manifest)
    if aware(manifest["as_of"]) != aware(EVALUATION_AT):
        raise Refused("execution source manifest must freeze data as stored at 2027-12-17 00:00 KST")
    if aware(clock()) < aware(EVALUATION_AT):
        raise Refused("final execution capture requires the registered data-as-of cutoff")
    if not decisions or any(d is None or not d["observation"] or d.get("synthetic") for d in decisions):
        raise Refused("execution capture requires timely real decision records")
    sessions = exchange().sessions
    first = min(pd.Timestamp(d["entry_date"]) for d in decisions)
    pos = sessions.get_loc(first)
    sessions = sessions[pos - 20:sessions.get_loc(pd.Timestamp(PRICE_END)) + 1]
    hashes = {}
    for day in sessions:
        path = price_path(data_dir, day)
        relative = path.relative_to(data_dir).as_posix()
        checksum = file_hash(path)
        if checksum is None or (not audit and manifest["files"].get(relative) != checksum):
            raise Refused("frozen evaluation price missing or hash differs: " + relative)
        hashes[relative] = checksum
    prices = prices_from_view(data_dir, sessions)
    if any(file_hash(Path(data_dir) / name) != checksum for name, checksum in hashes.items()):
        raise Refused("execution price inputs changed during capture")
    results = []
    for decision in decisions:
        raw = {**quote_cohort(prices, decision, sessions), "source_hashes": hashes,
               "code_pins": applied, "distribution": receipt,
               "data_asof": EVALUATION_AT, "source_manifest_sha256": file_hash(sync_manifest), "audit_only": audit}
        path = root / ("audit_executions" if audit else "executions/raw") / f"{decision['index']:02d}.json"
        if audit:
            path = path.parent / (path.stem + "-" + digest(raw)[:16] + ".json")
        seal(path, raw)
        results.append(raw)
    return results


def priced_quotes(raw, multiplier, *, cost_fn=None):
    cost_fn = cost_model.one_way_cost if cost_fn is None else cost_fn
    result = {}
    for code, quote in raw["quotes"].items():
        if not quote["filled"]:
            result[code] = quote
            continue
        try:
            buy = cost_fn(quote["entry_price"], quote["entry_market"], quote["entry_date"].replace("-", ""),
                          quote["entry_adv_20"], "buy", multiplier=multiplier, model_version="v2")
            sell = cost_fn(quote["exit_price"], quote["exit_market"], quote["exit_date"].replace("-", ""),
                           quote["exit_adv_20"], "sell", multiplier=multiplier, model_version="v2")
        except ValueError as exc:
            if "unknown tax year" in str(exc):
                raise Refused("LH003 cost evaluation refused: " + str(exc)
                              + "; record a legally sourced statutory rate as a SELL_TAX amendment for the loaded backtesting/cost_model.py before evaluation. "
                                "Decisions and raw executions remain usable without 2027 costs.") from exc
            raise
        if not np.isfinite([buy, sell]).all() or buy < 0 or not 0 <= sell < 1:
            raise Refused("invalid one-way costs")
        result[code] = {**quote, "buy_rate": float(buy), "sell_rate": float(sell)}
    return result


def portfolio(quotes, codes, *, loss=False):
    """Equal entry budgets including buy costs, then reweight executable names."""
    if len(set(codes)) != len(codes) or any(code not in quotes for code in codes):
        raise Refused("portfolio differs from sealed quote universe")
    filled = [quotes[code] for code in codes if quotes[code]["filled"]]
    if not filled:
        return {"net": 0.0, "gross": 0.0, "cash": 1.0, "count": 0, "positions": [],
                "excluded": [{"stock_code": c, "reason": quotes[c]["reason"]} for c in codes]}
    weight, positions = 1 / len(filled), []
    for quote in filled:
        gross = quote["gross_delisting_loss"] if loss else quote["gross"]
        buy, sell = quote["buy_rate"], quote["sell_rate"]
        net = (1 + gross) * (1 - sell) / (1 + buy) - 1
        quantity = weight / (quote["entry_price"] * (1 + buy))
        sell_value = weight * (1 + gross) / (1 + buy)
        positions.append({"stock_code": quote["stock_code"], "budget": weight, "quantity": quantity,
                          "exit_quantity": sell_value / quote["exit_price"], "buy_cost": quantity * quote["entry_price"] * buy,
                          "sell_cost": sell_value * sell, "buy_rate": buy, "sell_rate": sell,
                          "gross": gross, "net": net, "closing_cash": weight * (1 + net),
                          "entry_date": quote["entry_date"], "exit_date": quote["exit_date"]})
    return {"net": float(np.mean([p["net"] for p in positions])), "gross": float(np.mean([
        q["gross_delisting_loss"] if loss else q["gross"] for q in filled])), "cash": float(sum(p["closing_cash"] for p in positions)),
        "count": len(filled), "positions": positions,
        "excluded": [{"stock_code": c, "reason": quotes[c]["reason"]} for c in codes if not quotes[c]["filled"]]}
