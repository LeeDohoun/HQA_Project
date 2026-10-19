"""Explicit-date local readers. Shared source archives are always read-only."""
from functools import lru_cache
import json
import math
from pathlib import Path
import re

import pandas as pd
import numpy as np

from backtesting.experiments import bb001
from src.ingestion import dart_buyback as dart
from src.utils.stock_codes import is_stock_code
from . import NotReady, aware
from .storage import sha


def guard(day):
    day = pd.Timestamp(day)
    if pd.Timestamp("2026-01-01") <= day <= pd.Timestamp("2026-06-30"):
        raise ValueError("BB003 never reads the protected 2026 H1 period")
    return day


def read_bytes(path):
    path = Path(path)
    if any(part.startswith(".env") for part in path.parts):
        raise ValueError("BB003 refuses environment files")
    raw = path.read_bytes()
    return raw


class Listings:
    """Only requested daily files, including empty calendar days, prove coverage.

    The daily collector's completed-day checkpoint is required. Supplement rows
    for these exact days are unioned, never globbed across protected periods.
    """
    def __init__(self, source, store):
        self.source, self.store = Path(source), store

    def load(self, first, last):
        directory = self.source / "disclosures/dart_full"
        state = directory / "_state.json"
        state_raw = read_bytes(state) if state.is_file() else None
        checkpoint = json.loads(state_raw) if state_raw is not None else {}
        complete = checkpoint.get("completed_listing_days", [])
        if not isinstance(complete, list) or any(not re.fullmatch(r"[0-9]{8}", d) for d in complete):
            raise ValueError("invalid DART completed-day checkpoint")
        rows, hashes, covered = {}, {}, set()
        if state_raw is not None:
            hashes["listing_checkpoint"] = self.store.blob(state_raw)
        for day in pd.date_range(first, last):
            guard(day)
            key = day.strftime("%Y%m%d")
            main = directory / f"list/{day:%Y}/{key}.jsonl"
            if key in complete and main.is_file():
                covered.add(key)
            for path in (main, directory / f"list_supplement/{day:%Y}/{key}.jsonl"):
                if not path.is_file():
                    continue
                raw = read_bytes(path)
                hashes[str(path.relative_to(self.source))] = self.store.blob(raw)
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    receipt = value.get("rcept_no", "")
                    if (not re.fullmatch(r"[0-9]{14}", receipt) or value.get("rcept_dt") != key
                            or not re.fullmatch(r"[0-9]{8}", value.get("corp_code", ""))
                            or not isinstance(value.get("report_nm"), str)):
                        raise ValueError("invalid DART original listing identity")
                    if not dart.title_matches(value["report_nm"]):
                        continue
                    row = {name: value.get(name) for name in ("rcept_no", "rcept_dt", "corp_code", "stock_code", "corp_name", "corp_cls", "report_nm")}
                    if receipt in rows and rows[receipt] != row:
                        raise ValueError("conflicting DART original listing identity")
                    rows[receipt] = row
        return sorted(rows.values(), key=lambda row: (row["rcept_dt"], row["rcept_no"])), covered, hashes


def parse_daily(raw, day):
    rows = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        code = row.get("stock_code", "")
        if (not is_stock_code(code) or code in rows
                or row.get("trade_date") != day.date().isoformat()
                or row.get("calendar_status") != "verified"):
            raise ValueError("invalid/duplicate/unverified BB003 daily row")
        value = dict(row)
        for key in ("open", "close", "volume", "trading_value", "market_cap", "base_price"):
            value[key] = float(row[key]) if row.get(key) not in (None, "") else math.nan
        value["ret_1d"] = float(row["change_rate_pct"]) / 100 if row.get("change_rate_pct") not in (None, "") else math.nan
        rows[code] = value
    if not {"KOSPI", "KOSDAQ"} <= {r.get("market") for r in rows.values()}:
        raise NotReady("missing_or_partial_whole_market_archive:" + str(day.date()))
    return rows


class Prices:
    def __init__(self, source, policy, store, *, as_of, frozen=False, freeze_at=None, vanish_end=None):
        self.source, self.policy, self.store = Path(source), policy, store
        self.sessions, self.as_of = policy.sessions, pd.Timestamp(as_of)
        self.frozen, self.freeze_at, self.vanish_end = frozen, freeze_at, vanish_end
        self.source_hashes, self._last_trades = {}, None

    def read(self, day):
        day = guard(self.policy.require(day))
        if day not in self.sessions or day > self.as_of:
            raise ValueError("BB003 price read exceeds its point-in-time session window")
        return self._frozen_load(day) if self.frozen else self._load(day)

    @lru_cache(maxsize=24)
    def _frozen_load(self, day):
        # The PC can retain a full d21 holding window; the collector remains
        # limited to eight daily frames. Never reparse 21 files per control.
        return Prices._load.__wrapped__(self, day)

    @lru_cache(maxsize=8)
    def _load(self, day):
        key = day.strftime("%Y%m%d")
        proof = self.store.proof("prices", key)
        if self.frozen:
            if (not proof or not proof["valid"] or self.freeze_at is None
                    or aware(proof["publication_not_after"]) > aware(self.freeze_at)):
                raise NotReady("daily_not_available_by_F:" + key)
            checksum = proof["record"]["input_hashes"]["daily"]
            raw = self.store.bytes(checksum)
        else:
            path = self.source / f"market/krx_daily/{day:%Y}/{key}.jsonl"
            if not path.is_file():
                raise NotReady("missing_whole_market_archive:" + key)
            raw = read_bytes(path)
            checksum = sha(raw)
        previous = self.source_hashes.get(key)
        if previous is not None and previous != checksum:
            raise ValueError("BB003 daily source changed within a run")
        self.source_hashes[key] = checksum
        return parse_daily(raw, day)

    def require(self, day):
        return self.read(day)

    def snapshot(self, day):
        """First complete observed daily vintage wins, before F only."""
        day = guard(self.policy.require(day))
        if day not in self.sessions or day > self.as_of:
            raise ValueError("BB003 snapshot outside its completed-session window")
        key = day.strftime("%Y%m%d")
        old = self.store.get("prices", key)
        if old:
            return old
        if self.freeze_at is not None and aware(self.store.clock()) > aware(self.freeze_at):
            raise NotReady("cannot_acquire_daily_after_F")
        path = self.source / f"market/krx_daily/{day:%Y}/{key}.jsonl"
        if not path.is_file():
            raise NotReady("missing_whole_market_archive:" + key)
        raw = read_bytes(path)
        parse_daily(raw, day)
        checksum = self.store.blob(raw)
        proof = self.store.seal("prices", key, {"date": day.date().isoformat(), "input_hashes": {"daily": checksum}})
        return proof["record"]

    def pre_entry(self, pos):
        # Every market file must exist; an empty file is not a suspension.
        if pos < 20:
            raise NotReady("incomplete_20_session_calendar")
        features, counts = bb001._pre_entry(self, pos)
        hashes = {}
        for day in self.sessions[pos - 20:pos]:
            key = day.strftime("%Y%m%d")
            path = self.source / f"market/krx_daily/{day:%Y}/{key}.jsonl"
            raw = read_bytes(path)
            if sha(raw) != self.source_hashes[key]:
                raise ValueError("pre-entry input changed before sealing")
            hashes["daily/" + key] = self.store.blob(raw)
        return features, counts, hashes

    def prepare_entry(self, pos):
        """Freeze the nineteen known sessions; only the final day is missing.

        Keep liquidity observations for the identical BB001 mean/reduction.
        Products and data validity independent of the last day are computed now.
        """
        days = self.sessions[pos - 20:pos - 1]
        if len(days) != 19:
            raise NotReady("incomplete_20_session_calendar")
        history, hashes = [], {}
        for day in days:
            guard(day)
            self.policy.require(day)
            if day > self.as_of:
                raise ValueError("BB003 preparation exceeds its completed-session window")
            path = self.source / f"market/krx_daily/{day:%Y}/{day:%Y%m%d}.jsonl"
            if not path.is_file():
                raise NotReady("missing_whole_market_archive:" + day.strftime("%Y%m%d"))
            raw = read_bytes(path)
            history.append(parse_daily(raw, day))
            hashes["daily/" + day.strftime("%Y%m%d")] = self.store.blob(raw)
        partial = {}
        for code in set.intersection(*(set(rows) for rows in history)):
            values = [rows[code]["trading_value"] for rows in history]
            returns = np.array([rows[code]["ret_1d"] for rows in history])
            adv_valid = bool(np.isfinite(values).all() and min(values) >= 0)
            returns_valid = bool(np.isfinite(returns).all() and (returns >= -1).all())
            partial[code] = {"adv_valid": adv_valid, "returns_valid": returns_valid,
                "trading_values": values if adv_valid else None,
                "gross19": float(np.prod(1 + returns)) if returns_valid else None,
                "gross4": float(np.prod(1 + returns[-4:])) if returns_valid else None}
        return {"entry_date": self.sessions[pos].date().isoformat(),
                "previous_session": self.sessions[pos - 1].date().isoformat(),
                "partial": partial, "input_hashes": hashes}

    def finish_entry(self, prepared):
        """Apply BB001 pre-entry rules using the prepared history + last day."""
        day = pd.Timestamp(prepared["previous_session"])
        current = self.read(day)
        from collections import Counter
        features, counts = {}, Counter()
        for code, row in current.items():
            if (not code.endswith("0") or row.get("market") not in {"KOSPI", "KOSDAQ"}
                    or not isinstance(row.get("stock_name"), str) or not row["stock_name"].strip()
                    or "스팩" in row["stock_name"]):
                counts["not_common_share_market_or_spac"] += 1
                continue
            partial = prepared["partial"].get(code)
            adv, ret = row["trading_value"], row["ret_1d"]
            if partial is None:
                counts["incomplete_verified_liquidity_window"] += 1
            elif not bb001._positive(row["close"]) or row["close"] < 1000:
                counts["price_below_1000"] += 1
            elif (not partial["adv_valid"] or not np.isfinite(adv) or adv < 0
                    or np.mean([*partial["trading_values"], adv]) < 1e8):
                counts["adv_below_100m_or_missing"] += 1
            elif not partial["returns_valid"] or not np.isfinite(ret) or ret < -1:
                counts["incomplete_adjusted_returns"] += 1
            elif not bb001._positive(row["market_cap"]):
                counts["missing_market_cap"] += 1
            else:
                features[code] = {"market": row["market"], "market_cap": row["market_cap"],
                    "pre_close": row["close"], "adv": float(np.mean([*partial["trading_values"], adv])),
                    "prior20": partial["gross19"] * (1 + ret) - 1,
                    "prior5": partial["gross4"] * (1 + ret) - 1}
        counts["eligible"] = len(features)
        raw = read_bytes(self.source / f"market/krx_daily/{day:%Y}/{day:%Y%m%d}.jsonl")
        if sha(raw) != self.source_hashes[day.strftime("%Y%m%d")]:
            raise ValueError("pre-entry input changed before sealing")
        return features, dict(counts), {**prepared["input_hashes"], "daily/" + day.strftime("%Y%m%d"): self.store.blob(raw)}

    def last_trades(self):
        if self._last_trades is None:
            if self.vanish_end is None or pd.Timestamp(self.vanish_end) > self.as_of:
                raise ValueError("BB003 vanish horizon is not fixed")
            last = {}
            for day in self.sessions[(self.sessions >= "2026-12-01") & (self.sessions <= self.vanish_end)]:
                for code, row in self.require(day).items():
                    if bb001._traded(row):
                        last[code] = day
            self._last_trades = last
        return self._last_trades
