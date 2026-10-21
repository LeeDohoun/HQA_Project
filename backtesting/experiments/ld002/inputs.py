"""Close-known candidates and fixed v1 rendering, from sealed views only."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd
from jsonschema import Draft202012Validator

from backtesting.experiments import hc001
from backtesting.experiments.lh001.inputs import _growth, json_value
from backtesting.experiments.lh003.inputs import financials, prices_from_view
from backtesting.experiments.lh003.snapshot import input_view
from src.ingestion import dart_listing
from src.ingestion.storage import read_rows

from . import PROMPT_DIR, Refused
from .storage import aware, digest, file_hash

HORIZONS = (1, 5, 20, 60, 250)
EXCLUDED = re.compile(r"스팩|SPAC|리츠|REIT|ETF|ETN", re.I)


def common_stock(code, row):
    name = row.get("stock_name")
    if not isinstance(name, str) or not name.strip():
        raise Refused("stock names are required for security exclusions")
    explicit = row.get("security_type")
    return (row["market"] in ("KOSPI", "KOSDAQ") and code.endswith("0")
            and not EXCLUDED.search(name) and (explicit is None or explicit == "common"))


def candidate_table(history, as_of, held, financial_frame, profiles, av_codes):
    day = pd.Timestamp(as_of)
    history = history.loc[history.index.get_level_values("trade_date") <= day].sort_index()
    if history.index.has_duplicates:
        raise Refused("duplicate stock/session price rows")
    sessions = pd.DatetimeIndex(history.index.get_level_values("trade_date").unique()).sort_values()
    if len(sessions) < 20 or sessions[-1] != day:
        raise Refused("at least 20 complete close-known sessions are required")
    current = history.xs(day, level="trade_date")
    trading = history.trading_value.where(history.calendar_status.eq("verified")).unstack("stock_code").reindex(sessions[-20:])
    adv = trading.mean().where(trading.notna().sum() == 20)
    common = [c for c, row in current.iterrows() if common_stock(c, row)]
    universe = sorted((c for c in common if current.loc[c, "calendar_status"] == "verified"
        and not bool(current.loc[c].get("halted", current.loc[c, "volume"] == 0))
        and pd.notna(adv.get(c)) and adv[c] >= 500_000_000), key=lambda c: (-adv[c], c))[:400]
    returns = history.ret_1d.unstack("stock_code").reindex(sessions)
    known = financial_frame.loc[pd.to_datetime(financial_frame.available_date) < day]
    signals = hc001.phase_signals(known) if len(known) else pd.DataFrame()
    if len(signals):
        signals = signals.sort_values("fiscal_quarter").groupby("stock_code").tail(1).set_index("stock_code")
    rows = []
    for code in sorted(set(universe) | set(held)):
        present = code in current.index
        row = current.loc[code] if present else {}
        chain = returns[code] if code in returns else pd.Series(dtype=float)
        raw = history.xs(code, level="stock_code").reindex(sessions) if code in returns else pd.DataFrame(index=sessions)
        signal = signals.loc[code] if code in signals.index else {}
        close = row.get("close")
        distance = None
        window = raw.tail(250)
        if len(window) == 250 and "close" in window and window.close.notna().all() and window.high.notna().all() and chain.tail(249).notna().all():
            adjusted = (1 + chain.tail(249)).cumprod()
            units = pd.Series([1.0, *adjusted.tolist()], index=window.index)
            high = (window.high / window.close * units).max()
            distance = units.iloc[-1] / high - 1 if high > 0 else None
        volatility = float(chain.tail(60).std(ddof=1)) if len(chain) >= 60 and chain.tail(60).notna().all() else None
        revenue, income = signal.get("revenue"), signal.get("operating_income")
        margin = income / revenue if revenue is not None and income is not None and pd.notna(revenue) and pd.notna(income) and revenue != 0 else None
        rows.append(json_value({"code": code, "name": row.get("stock_name"), "market": row.get("market"),
            "industry": profiles.get(code), "close": close, "market_cap": row.get("market_cap"),
            "adv20": adv.get(code), "in_universe": code in universe,
            "hold_or_sell_only": code not in universe, "halted": not present or bool(row.get("halted", row.get("volume", 0) == 0)),
            **{f"return_{n}": _growth(chain, n) for n in HORIZONS}, "distance_52w_high": distance,
            "volatility_60": volatility, "revenue_yoy": signal.get("revenue_yoy"),
            "operating_income_yoy": signal.get("operating_income_yoy"), "operating_margin": margin,
            "av_included": code in av_codes}))
    breadth = {}
    for market in ("KOSPI", "KOSDAQ"):
        members = [c for c in common if current.loc[c, "market"] == market and pd.notna(current.loc[c, "ret_1d"])]
        breadth[market] = float((current.loc[members, "ret_1d"] > 0).mean()) if members else None
    return sorted(universe), rows, breadth


def disclosures(rows, codes, as_of):
    selected = []
    for row in rows:
        if row["stock_code"] not in codes or pd.Timestamp(row["rcept_dt"]).date().isoformat() != as_of:
            continue
        if not row.get("received_at"):
            raise Refused("enabled disclosure list requires actual receipt timestamps")
        stamp = aware(row["received_at"])
        if stamp.date().isoformat() != as_of:
            raise Refused("disclosure receipt timestamp/date mismatch")
        selected.append({"code": row["stock_code"], "title": row["report_nm"],
                         "received_at": stamp.isoformat(), "receipt": row["rcept_no"]})
    counts, result = {}, []
    for row in sorted(selected, key=lambda r: (r["received_at"], r["receipt"], r["code"])):
        if counts.get(row["code"], 0) < 3 and len(result) < 300:
            result.append(row)
            counts[row["code"]] = counts.get(row["code"], 0) + 1
    return result


def latest_av(data_dir, *, as_of, at):
    # Only completed FW001 publications are signals; a JSON file alone is not.
    from src.forward import fw001
    choices = []
    for path in sorted((Path(data_dir) / fw001.RECORD_DIR / "decisions").glob("*.json")):
        decision = fw001.load_json(path)
        if decision["decision_date"] > as_of:
            continue
        proof = fw001.completion_proof(data_dir, "decisions", decision["month"])
        if fw001.load_json(path) != decision:
            raise Refused("FW001 decision changed during proof verification")
        if proof is not None and aware(proof["publication_not_after"]) <= aware(at):
            choices.append((decision["decision_date"], decision, proof))
    if not choices:
        raise Refused("latest FW001 av_all completion proof unavailable")
    _, decision, proof = max(choices, key=lambda row: row[0])
    return {"codes": decision["rules"]["av_all"]["targets"], "weights": decision["rules"]["av_all"]["weights"],
            "decision_sha256": fw001.digest(decision), "proof": proof, "month": decision["month"]}


def prepare(snapshot, day, previous, calendar, *, at, disclosure_enabled, av, nav_history, memos, last_trades):
    as_of = calendar.previous(day)
    if aware(snapshot.distribution.manifest["sync_verified_at"]) < aware(calendar.close(as_of)):
        raise Refused("input transfer must be verified after T-1 close")
    sessions = calendar.sessions((pd.Timestamp(as_of) - pd.Timedelta(days=550)).date().isoformat(), as_of)[-251:]
    if len(sessions) != 251:
        raise Refused("251 close-known sessions are required for v1 input")
    with input_view(None, snapshot, end=as_of.replace("-", "")) as view:
        before = {p.relative_to(view).as_posix(): file_hash(p) for p in sorted(view.rglob("*")) if p.is_file()}
        history = prices_from_view(view, pd.DatetimeIndex(sessions))
        history["security_type"], history["halted"] = None, False
        current_path = view / f"market/krx_daily/{as_of[:4]}/{as_of.replace('-', '')}.jsonl"
        for row in read_rows(current_path):
            history.loc[(pd.Timestamp(as_of), row["stock_code"]), "security_type"] = row.get("security_type")
            history.loc[(pd.Timestamp(as_of), row["stock_code"]), "halted"] = row.get("halted", row["volume"] == 0)
        if not {"KOSPI", "KOSDAQ"}.issubset(set(history.xs(pd.Timestamp(as_of), level="trade_date").market)):
            raise Refused("T-1 input requires both verified markets")
        liquidity = history.trading_value.where(history.calendar_status.eq("verified")).unstack("stock_code").reindex(pd.DatetimeIndex(sessions[-20:]))
        all_adv = json_value(liquidity.mean().where(liquidity.notna().sum() == 20).to_dict())
        financial_frame, date_report = financials(view, as_of)
        profiles = hc001._industries(view)
        universe, candidates, breadth = candidate_table(history, as_of, previous["positions"], financial_frame, profiles, av["codes"])
        listings = []
        if disclosure_enabled:
            status = json.loads((view / "lh003_coverage.json").read_text())
            if as_of.replace("-", "") not in status["covered_listing_days"] and as_of not in status["covered_listing_days"]:
                raise Refused("required disclosure day is incomplete")
            listings = disclosures(dart_listing.listing_rows(view, as_of.replace("-", "")),
                                  {r["code"] for r in candidates}, as_of)
        benchmarks = read_rows(view / "market_context/benchmarks.jsonl")
        market = []
        for name in ("KOSPI", "KOSDAQ"):
            levels = {pd.Timestamp(r["trade_date"]): r["close"] for r in benchmarks if r["series"] == name
                      and r["index_name"] == ("코스피" if name == "KOSPI" else "코스닥") and str(r["trade_date"]) <= as_of}
            closes = pd.Series(levels, dtype=float).reindex(pd.DatetimeIndex(sessions))
            market.append({"market": name, **{f"return_{n}": float(closes.iloc[-1] / closes.iloc[-n - 1] - 1)
                if closes.tail(n + 1).notna().all() and closes.iloc[-n - 1] > 0 else None for n in (1, 5, 20, 60)},
                "advancing_share": breadth[name]})
        after = {p.relative_to(view).as_posix(): file_hash(p) for p in sorted(view.rglob("*")) if p.is_file()}
        if before != after:
            raise Refused("sealed input view changed during generation")
    positions = [{"code": code, "quantity": p["quantity"], "weight": p["quantity"] * p["last_close"] / previous["nav"],
        "avg_cost": p["avg_cost"], "unrealized_return": p["last_close"] / p["avg_cost"] - 1 if p["avg_cost"] else None,
        "holding_days": len(calendar.sessions(p["opened_on"], as_of)), "stale_days": p["stale_days"]}
        for code, p in sorted(previous["positions"].items())]
    return {"decision_day": day, "as_of": as_of, "universe": universe, "candidates": candidates,
        "disclosures": listings if disclosure_enabled else {"omitted_for_entire_window": True}, "market": market,
        "ledger": {"cash": previous["cash"], "nav": previous["nav"], "positions": positions, "last_trades": last_trades,
            "nav_20": nav_history[-20:], "drawdown": previous["nav"] / previous["peak_nav"] - 1, "memos_5": memos[-5:]},
        "rules": "§5 v1; 오늘 종가 체결; 비용 v2 왕복 약 0.3%", "source_hashes": before,
        "financial_dates": date_report, "av": av,
        "adv20": all_adv}


def prompt_hashes(prompt_dir=PROMPT_DIR):
    return {p.name: file_hash(p) for p in sorted(Path(prompt_dir).glob("v1*")) if p.is_file()}


def payload(snapshot, prompt_dir=PROMPT_DIR):
    prompt_dir = Path(prompt_dir)
    spec = importlib.util.spec_from_file_location("ld002_frozen_renderer", prompt_dir / "v1_renderer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = deepcopy(json.loads((prompt_dir / "v1_schema.json").read_text()))
    codes = sorted(r["code"] for r in snapshot["candidates"])
    schema["properties"]["holdings"]["items"]["properties"]["code"] = {"type": "string", "enum": codes} if codes else False
    Draft202012Validator.check_schema(schema)
    rendered = module.render(snapshot)
    return {"prompt": (prompt_dir / "v1_prompt.txt").read_text() + "\n<UNTRUSTED_DATA>\n" + rendered + "\n</UNTRUSTED_DATA>",
            "reask": (prompt_dir / "v1_reask.txt").read_text(), "schema": schema,
            "artifact_hashes": prompt_hashes(prompt_dir), "renderer_input_sha256": digest(rendered)}


def validate(output, schema):
    Draft202012Validator(schema).validate(output)
    codes = [h["code"] for h in output["holdings"]]
    if len(codes) != len(set(codes)):
        raise Refused("duplicate holding code")
    if bool(output["invest_level"]) != bool(codes):
        raise Refused("invest_level and holdings disagree")
    if type(output["invest_level"]) is not int or any(type(h["conviction"]) is not int for h in output["holdings"]):
        raise Refused("integer fields must be JSON integers")
