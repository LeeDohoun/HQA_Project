"""Read-only BB003 proofs; BB001 pre-entry eligibility without entry-day filters."""
from collections import Counter
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import re

import numpy as np

from . import Refused, aware
from .calendar import time_map
from .storage import PRECISION_NS, load, publication_ns, read, sha


class BB003:
    def __init__(self, root, store):
        self.root, self.store = Path(root).resolve(), store

    def keys(self, kind):
        if kind not in {"preparations", "decisions", "data_not_ready", "omissions", "seen", "post_entry"}:
            raise Refused("unsupported BB003 input kind")
        return sorted(p.parent.name for p in (self.root / "sealed" / kind).glob("*/complete.json"))

    def get(self, kind, key, *, as_of=None):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", key):
            raise Refused("invalid BB003 record key")
        marker = self.root / "sealed" / kind / key / "complete.json"
        if not marker.exists():
            return None
        proof = load(marker)
        ns = publication_ns(marker)
        upper = ns + PRECISION_NS
        if (set(p.name for p in marker.parent.iterdir()) != {"complete.json"}
                or proof["publication_clock"] != "private_directory_mtime_ns"
                or proof["publication_precision_ns"] != PRECISION_NS or proof["measurement"]
                or ns < marker.stat().st_mtime_ns
                or upper < int(aware(proof["prepared_at"]).timestamp() * 1e9)):
            raise Refused("invalid BB003 forward completion proof")
        if as_of is not None and upper > int(aware(as_of).timestamp() * 1e9):
            return None
        if proof["deadline"]:
            cutoff = int(aware(proof["deadline"]).timestamp() * 1e9)
            if upper > cutoff if proof["inclusive"] else upper >= cutoff:
                return None
        path = self.root / proof["record_path"]
        if not path.resolve().is_relative_to(self.root):
            raise Refused("BB003 record escapes its read-only root")
        raw = read(path)
        if sha(raw) != proof["record_sha256"]:
            raise Refused("BB003 record hash differs")
        record = json.loads(raw)
        if (record["record_kind"], record["record_key"], record["label"]) != (kind, key, "forward"):
            raise Refused("BB003 record identity/label differs")
        self.store.blob(raw)
        return {**record, "xo001_input_sha256": sha(raw), "xo001_publication_not_after_ns": upper}

    def candidates(self, day, cutoff):
        latest = {}
        for key in self.keys("preparations"):
            row = self.get("preparations", key, as_of=cutoff)
            if row and row["event"]["entry_date"] == day:
                latest[row["event"]["rcept_no"]] = row
        return [r for _, r in sorted(latest.items()) if r["status"] == "prepared"]

    def decisions(self, day, cutoff):
        rows = []
        for key in self.keys("decisions"):
            row = self.get("decisions", key, as_of=cutoff)
            if row and row["event"]["entry_date"] == day and row["status"] == "included":
                rows.append(row)
        return sorted(rows, key=lambda r: r["event"]["rcept_no"])


def boundaries(values, parts):
    return np.quantile(np.asarray(values, dtype=float), np.arange(1, parts) / parts,
                       method="inverted_cdf").tolist()


def lower_bin(value, limits):
    return int(np.searchsorted(limits, value, side="left"))


def available_bytes(path, cutoff):
    """Do not backfill a roster from files that first appeared after 08:25."""
    path = Path(path)
    before = path.stat()
    raw = read(path)
    after = path.stat()
    if (before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size
            or before.st_mtime_ns + PRECISION_NS > int(cutoff.timestamp() * 1e9)):
        raise Refused("population_input_not_available_at_roster_cutoff")
    return raw


def eligibility(history):
    """Identical tests/reduction order to BB001._pre_entry; no today's prices."""
    if len(history) != 20:
        raise Refused("exactly twenty verified pre-entry sessions required")
    features, counts = {}, Counter()
    for code, current in history[-1].items():
        if (not code.endswith("0") or current.get("market") not in ("KOSPI", "KOSDAQ")
                or not isinstance(current.get("stock_name"), str) or not current["stock_name"].strip()
                or "스팩" in current["stock_name"]):
            counts["not_common_share_market_or_spac"] += 1
            continue
        rows = [daily.get(code) for daily in history]
        if any(not row or row.get("calendar_status") != "verified" for row in rows):
            counts["incomplete_verified_liquidity_window"] += 1
            continue
        adv = [r["trading_value"] for r in rows]
        returns = np.array([r["ret_1d"] for r in rows])
        close, cap = current["close"], current["market_cap"]
        if not np.isfinite(close) or close <= 0 or close < 1000:
            counts["price_below_1000"] += 1
        elif not np.isfinite(adv).all() or min(adv) < 0 or np.mean(adv) < 1e8:
            counts["adv_below_100m_or_missing"] += 1
        elif not np.isfinite(returns).all() or (returns < -1).any():
            counts["incomplete_adjusted_returns"] += 1
        elif not np.isfinite(cap) or cap <= 0:
            counts["missing_market_cap"] += 1
        else:
            features[code] = {"market": current["market"], "market_cap": cap, "pre_close": close,
                "adv": float(np.mean(adv)), "prior20": float(np.prod(1 + returns) - 1),
                "prior5": float(np.prod(1 + returns[-5:]) - 1)}
    counts["eligible"] = len(features)
    return features, dict(counts)


def pre_entry(source, calendar, day, store):
    history, hashes = [], {}
    cutoff = time_map(calendar.day(day))["roster"]
    for session in calendar.previous(day, 20):
        path = Path(source) / f"market/krx_daily/{session[:4]}/{session.replace('-', '')}.jsonl"
        raw = available_bytes(path, cutoff)
        rows = {}
        for line in raw.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            code = row.get("stock_code", "")
            if (not re.fullmatch(r"[0-9A-Z]{6}", code) or code in rows
                    or row.get("trade_date") != session or row.get("calendar_status") != "verified"):
                raise Refused("invalid pre-entry KRX daily identity/calendar")
            value = dict(row)
            for key in ("close", "trading_value", "market_cap"):
                value[key] = float(row[key]) if row.get(key) not in (None, "") else math.nan
            value["ret_1d"] = float(row["change_rate_pct"]) / 100 if row.get("change_rate_pct") not in (None, "") else math.nan
            rows[code] = value
        if not {"KOSPI", "KOSDAQ"} <= {r.get("market") for r in rows.values()}:
            raise Refused("missing_or_partial_whole_market_archive:" + session)
        history.append(rows)
        hashes[session] = store.blob(raw)
    features, counts = eligibility(history)
    return features, counts, hashes


def recent_titles(source, calendar, day, store):
    """BB001 age 0..63 inclusive; require completed daily listing coverage."""
    sessions = calendar.previous(day, 64)
    cutoff = time_map(calendar.day(day))["roster"]
    directory = Path(source) / "disclosures/dart_full"
    state_raw = available_bytes(directory / "_state.json", cutoff)
    completed = set(json.loads(state_raw)["completed_listing_days"])
    hashes, codes = {"checkpoint": store.blob(state_raw)}, set()
    first = datetime.fromisoformat(sessions[0]).date()
    last = datetime.fromisoformat(day).date() - timedelta(days=1)
    cursor = first
    while cursor <= last:
        key = cursor.strftime("%Y%m%d")
        path = directory / f"list/{cursor.year}/{key}.jsonl"
        if key not in completed or not path.is_file():
            raise Refused("recent_title_coverage_unknown:" + key)
        for path in (path, directory / f"list_supplement/{cursor.year}/{key}.jsonl"):
            if not path.is_file():
                continue
            raw = available_bytes(path, cutoff)
            hashes[str(path.relative_to(directory))] = store.blob(raw)
            for line in raw.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("rcept_dt") != key or not isinstance(row.get("report_nm"), str):
                    raise Refused("invalid recent-title listing identity")
                # Exact BB001/dart_buyback.title_matches, without importing settings.
                title = re.sub(r"\s+", "", row["report_nm"])
                if ("자기주식취득결정" in title and not any(v in title for v in ("신탁", "정정", "철회", "취소"))
                        and row.get("stock_code")):
                    codes.add(row["stock_code"])
        cursor += timedelta(days=1)
    return codes, hashes


def draw(day, events, features, recent):
    """Market-specific non-event population. Sorted candidates; never relax/redraw."""
    known_events = {event["stock_code"] for event in events}
    pool = {c: f for c, f in sorted(features.items()) if c not in recent and c not in known_events}
    result, baseline, groups = [], [], {}
    for market in ("KOSPI", "KOSDAQ"):
        market_pool = {c: f for c, f in pool.items() if f["market"] == market}
        groups[market] = {"candidates": list(market_pool),
                          "size": boundaries([f["market_cap"] for f in market_pool.values()], 3) if market_pool else [],
                          "returns": boundaries([f["prior20"] for f in market_pool.values()], 5) if market_pool else []}
    for event in sorted(events, key=lambda e: e["rcept_no"]):
        code = event["stock_code"]
        feature = features.get(code)
        candidates = []
        if feature:
            group = groups[feature["market"]]
            sb, rb = lower_bin(feature["market_cap"], group["size"]), lower_bin(feature["prior20"], group["returns"])
            candidates = [c for c in group["candidates"] if c != code
                          and lower_bin(pool[c]["market_cap"], group["size"]) == sb
                          and lower_bin(pool[c]["prior20"], group["returns"]) == rb]
        chosen = np.random.default_rng(int(event["rcept_no"])).choice(candidates, size=min(2, len(candidates)), replace=False).tolist()
        result.append({"rcept_no": event["rcept_no"], "stock_code": code, "candidates": candidates,
                       "selected": chosen, "shortfall": 2 - len(chosen),
                       "reason": "event_pre_entry_ineligible" if feature is None else None})
    rng = np.random.default_rng(int(day.replace("-", "")))
    for market in ("KOSPI", "KOSDAQ"):
        group = groups[market]
        for size in range(3):
            candidates = [c for c in group["candidates"] if lower_bin(pool[c]["market_cap"], group["size"]) == size]
            selected = rng.choice(candidates, size=min(1, len(candidates)), replace=False).tolist()
            baseline.append({"market": market, "size_bin": size, "candidates": candidates,
                             "selected": selected, "shortfall": 1 - len(selected)})
    return {"matched": result, "baseline": baseline, "boundaries": groups,
            "numpy_version": np.__version__, "generator": "numpy.default_rng",
            "ties": "lower_bin", "quantile_method": "inverted_cdf"}


def make_roster(store, bb003, source, calendar, plan, *, population=None):
    day, times = plan["date"], time_map(plan)
    old = store.proof("roster", day)
    if old:
        return old["record"]
    preps = bb003.candidates(day, times["roster"])
    events = [p["event"] for p in preps]
    features, counts, hashes, recent, error = {}, {}, {}, set(), None
    try:
        if population is None:
            population = prepare_population(source, calendar, day, store)
        features, counts, hashes, recent = population
    except (OSError, Refused) as exc:
        error = str(exc) if isinstance(exc, Refused) else "pre_entry_source_missing"
        # Unknown populations never become a random sample from a partial file.
        features = {}
    selections = draw(day, events, features, recent)
    value = {"date": day, "as_of": times["roster"].isoformat(), "events": events, **selections,
             "eligibility_counts": counts, "input_hashes": hashes,
             "preparation_hashes": [p["xo001_input_sha256"] for p in preps], "population_error": error,
             "late": aware(store.clock()) > times["roster"]}
    # A late recovery is retained with an explicit label, never passed off as the 08:25 roster.
    value["label"] = "reconstructed" if value["late"] else "forward"
    return store.seal("roster", day, value,
        deadline=None if value["late"] else times["roster"])["record"]


def prepare_population(source, calendar, day, store):
    features, counts, hashes = pre_entry(source, calendar, day, store)
    recent, title_hashes = recent_titles(source, calendar, day, store)
    hashes.update({"titles/" + k: v for k, v in title_hashes.items()})
    return features, counts, hashes, recent
