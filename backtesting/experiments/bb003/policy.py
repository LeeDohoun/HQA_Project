"""Data-only calendar/tax amendments; the FW001 Python tree never changes.

Session lists are explicit, bounded and source-bound. Unknown dates raise;
there is no weekday approximation in production. All statutory costs use the
unchanged BB001 commission, tick and slippage formula, with these pinned dates.
"""
from datetime import datetime, time, timedelta
import math
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from backtesting import cost_model
from . import END, START, KST, NotReady, aware
from .storage import encoded, sha
from .deployment import verified

FIELDS = {"calendar_start", "calendar_through", "sessions", "tax_through", "tax", "sources"}


class Policy:
    def __init__(self, data):
        if set(data) != FIELDS:
            raise ValueError("BB003 policy permits only calendar/tax data fields")
        self.data = data
        self.sessions = pd.DatetimeIndex(data["sessions"])
        self.start, self.through = map(pd.Timestamp, (data["calendar_start"], data["calendar_through"]))
        if (self.sessions.empty or self.sessions.hasnans or self.sessions.tz is not None
                or not self.sessions.equals(self.sessions.normalize().sort_values().unique())
                or self.sessions[0] < self.start or self.sessions[-1] > self.through):
            raise ValueError("invalid explicit BB003 session calendar")
        if self.start > self.through or not data["tax"]:
            raise ValueError("invalid policy bounds or empty tax table")
        dates = []
        for row in data["tax"]:
            if set(row) != {"from", "KOSPI", "KOSDAQ"}:
                raise ValueError("invalid mechanical tax row")
            dates.append(pd.Timestamp(row["from"]))
            if any(type(row[m]) not in (float, int) or not math.isfinite(row[m]) or not 0 <= row[m] < 1
                   for m in ("KOSPI", "KOSDAQ")):
                raise ValueError("invalid mechanical tax rates")
        if dates != sorted(set(dates)) or dates[-1] > pd.Timestamp(data["tax_through"]):
            raise ValueError("invalid tax effective dates")
        self.digest = sha(encoded(data))

    def require(self, day):
        day = pd.Timestamp(day)
        if not self.start <= day <= self.through:
            raise NotReady("calendar_unverified:" + str(day.date()))
        return day

    def entry(self, public):
        public = self.require(public)
        pos = int(self.sessions.searchsorted(public, side="right"))
        if pos >= len(self.sessions):
            raise NotReady("calendar_unverified:next_session")
        return pos

    def entry_schedule(self, public):
        pos = self.entry(public)
        return {"public_date": pd.Timestamp(public).date().isoformat(), "entry_position": pos,
                "entry_date": self.sessions[pos].date().isoformat(), "horizon": 21,
                "deadline": datetime.combine(self.sessions[pos].date(), time(8, 59), KST).isoformat()}

    def schedule(self, public):
        entry = self.entry_schedule(public)
        pos = entry["entry_position"]
        if pos + 20 >= len(self.sessions):
            raise NotReady("calendar_unverified:entry_inclusive_21st_session")
        return {**entry, "exit_position": pos + 20, "exit_date": self.sessions[pos + 20].date().isoformat()}

    def freeze(self, *, start=START, end=END):
        first = self.entry(end)
        e, v = first + 20, first + 20 + 63
        if v >= len(self.sessions):
            raise NotReady("calendar_unverified:E_plus_63_sessions")
        final = datetime.combine(self.sessions[v].date() + timedelta(days=7), time(23, 59, 59), KST)
        return {"last_entry": self.sessions[first].date().isoformat(), "E": self.sessions[e].date().isoformat(),
                "V": self.sessions[v].date().isoformat(), "F": final.isoformat(),
                "grid": self.sessions[(self.sessions >= start) & (self.sessions <= self.sessions[e])]}

    def recording_freeze(self, day):
        """Only require V/F once the verified run date can reach that horizon."""
        if pd.Timestamp(day) <= pd.Timestamp(END):
            return None
        first = self.entry(END)
        if first + 83 >= len(self.sessions):
            self.require(day)
            # Every date through this run is verified, and there have not yet
            # been 83 sessions after the last entry. V is still in the future.
            return None
        return self.freeze()

    def cost(self, price, market, day, adv, side, *, multiplier=1., version="v2"):
        day = self.require(day)
        if day not in self.sessions or market not in ("KOSPI", "KOSDAQ") or side not in ("buy", "sell"):
            raise ValueError("invalid BB003 cost inputs")
        if not math.isfinite(adv) or adv < 0 or multiplier < 0 or not math.isfinite(multiplier):
            raise ValueError("invalid BB003 liquidity or cost multiplier")
        tax_day = day
        if version == "v2" and side == "sell":
            pos = self.sessions.get_loc(day) + 2
            if pos >= len(self.sessions):
                raise NotReady("calendar_unverified:T_plus_2_settlement")
            tax_day = self.sessions[pos]
        if side == "sell" and not pd.Timestamp(self.data["tax"][0]["from"]) <= tax_day <= pd.Timestamp(self.data["tax_through"]):
            raise NotReady("tax_unverified:" + str(tax_day.date()))
        tax = next(row[market] for row in reversed(self.data["tax"]) if pd.Timestamp(row["from"]) <= tax_day) if side == "sell" else 0.
        config = cost_model.DEFAULT_CONFIG
        slip = next(rate for upper, rate in zip(cost_model.TRADING_VALUE_BUCKETS, config.slippage_rates) if adv < upper)
        return tax + (config.commission_rate + cost_model.tick_size(price, market, day.date(), version) / price + slip) * multiplier


def shared_policy(start, end):
    """Export only already verified shared dates. This never extends to 2027."""
    from src.runner import trading_calendar as shared
    days = shared.session_calendar(start, end)
    return Policy({"calendar_start": start, "calendar_through": end,
        "sessions": [d.date().isoformat() for d in days], "tax_through": "2026-12-31",
        "tax": [{"from": day.isoformat(), **rates} for day, rates in cost_model.SELL_TAX],
        "sources": [{"kind": "shared_pre_2027_baseline", "version": shared.CALENDAR_VERSION,
            "calendar_sha256": sha(Path(shared.__file__).read_bytes()),
            "cost_sha256": sha(Path(cost_model.__file__).read_bytes())}]})


def latest(store):
    keys = store.keys("policy")
    if not keys or keys != [f"{i:06d}" for i in range(len(keys))]:
        raise NotReady("missing_or_noncontiguous_calendar_tax_policy")
    previous, old = None, None
    for key in keys:
        record = store.get("policy", key)
        if not record or record["previous_sha256"] != previous:
            raise ValueError("BB003 policy amendment chain differs")
        policy = Policy(record["policy"])
        if record["after_sha256"] != policy.digest:
            raise ValueError("BB003 policy after hash differs")
        for checksum in record["input_hashes"].values():
            store.bytes(checksum)
        notices = {s["sha256"]: store.bytes(s["sha256"]) for s in policy.data["sources"]}
        if evidence(policy.data, old, notices, aware(record["changed_at"])) != record["changes"]:
            raise ValueError("BB003 policy field/effective-date evidence differs")
        previous = policy.digest
        old = policy
    return policy


def changes(data, old):
    """Bind every changed field to the entire affected effective-date range."""
    result = []
    for field in ("calendar_start", "calendar_through", "tax_through"):
        if old is None or data[field] != old.data[field]:
            first = (old.data[field] if field == "calendar_start" else
                     (pd.Timestamp(old.data[field]) + pd.Timedelta(days=1)).date().isoformat()) if old else (
                         data["calendar_start"] if field.startswith("calendar") else data["tax"][0]["from"])
            result.append({"field": field, "effective_date": first, "through": data[field]})
    different = set(data["sessions"]) ^ (set(old.data["sessions"]) if old else set())
    if different:
        result.append({"field": "sessions", "effective_date": min(different), "through": max(different)})
    before = {r["from"]: r for r in old.data["tax"]} if old else {}
    after = {r["from"]: r for r in data["tax"]}
    for day in sorted(set(before) | set(after)):
        for market in ("KOSPI", "KOSDAQ"):
            if before.get(day, {}).get(market) != after.get(day, {}).get(market):
                result.append({"field": "tax." + market, "effective_date": day, "through": day})
    return result


def evidence(data, old, notices, at):
    sources = data["sources"]
    for source in sources:
        if set(source) != {"kind", "url", "sha256", "published_at", "fields", "effective_from", "effective_through"}:
            raise ValueError("initial and amended policy need exact official-source records")
        fields = {"calendar_start", "calendar_through", "sessions"} if source["kind"] == "calendar" else {"tax_through", "tax.KOSPI", "tax.KOSDAQ"}
        if (source["kind"] not in {"calendar", "tax"} or not isinstance(source["fields"], list)
                or not source["fields"] or not set(source["fields"]) <= fields):
            raise ValueError("official evidence fields differ from its kind")
        url = urlparse(source["url"])
        domains = ("krx.co.kr",) if source["kind"] == "calendar" else ("law.go.kr", "moef.go.kr")
        if (url.scheme != "https" or url.username or url.password
                or not any(url.hostname == d or (url.hostname or "").endswith("." + d) for d in domains)):
            raise ValueError("mechanical amendment requires an official KRX/statutory URL")
        raw = notices.get(source["sha256"])
        if raw is None or sha(raw) != source["sha256"] or aware(source["published_at"]) > at:
            raise ValueError("missing/hash-mismatched/future official notice bytes")
        if pd.Timestamp(source["effective_from"]) > pd.Timestamp(source["effective_through"]):
            raise ValueError("invalid official effective-date range")
    bound = []
    for change in changes(data, old):
        covered, matches = pd.Timestamp(change["effective_date"]), []
        for source in sorted(sources, key=lambda s: s["effective_from"]):
            if (change["field"] in source["fields"] and pd.Timestamp(source["effective_from"]) <= covered
                    and pd.Timestamp(source["effective_through"]) >= covered):
                matches.append(source["sha256"])
                covered = pd.Timestamp(source["effective_through"]) + pd.Timedelta(days=1)
                if covered > pd.Timestamp(change["through"]):
                    break
        if covered <= pd.Timestamp(change["through"]):
            raise ValueError("missing official evidence for changed field/effective date: " + change["field"])
        bound.append({**change, "source_sha256": sorted(set(matches))})
    return bound


@verified("setup")
def append(store, data, *, notices=None, reason):
    """Pin reviewed data, never Python. Supplied official notices stay offline.

    Unlike masking data nodes in a Python AST, the strict data schema cannot
    carry executable changes at all. Already elapsed sessions/taxes are pinned.
    """
    policy, keys = Policy(data), store.keys("policy")
    at, hashes = aware(store.clock()), {}
    old = latest(store) if keys else None
    if old:
        if (policy.start != old.start or policy.through < old.through
                or pd.Timestamp(data["tax_through"]) < pd.Timestamp(old.data["tax_through"])):
            raise ValueError("mechanical amendments cannot shorten coverage")
        past = pd.Timestamp(at.date())
        if not old.sessions[old.sessions < past].equals(policy.sessions[(policy.sessions < past) & (policy.sessions <= old.through)]):
            raise ValueError("mechanical amendment changes elapsed sessions")
        if [r for r in old.data["tax"] if pd.Timestamp(r["from"]) < past] != [r for r in data["tax"] if pd.Timestamp(r["from"]) < past]:
            raise ValueError("mechanical amendment changes elapsed tax rates")
    bound = evidence(data, old, notices or {}, at)
    for source in data["sources"]:
        hashes[source["sha256"]] = store.blob(notices[source["sha256"]])
    if not reason.strip():
        raise ValueError("mechanical amendment needs a reason")
    return store.seal("policy", f"{len(keys):06d}", {"policy": data, "previous_sha256": old.digest if old else None,
        "after_sha256": policy.digest, "changed_at": at.isoformat(), "reason": reason,
        "changes": bound, "input_hashes": hashes})
