"""A point-in-time copy of the PAPER data, readable by the live loader at a past decision time.

The live loader refuses a historical as_of (`historical_replay_requires_versioned_price_and_universe_store`):
its stores record when each row was observed, and every stored row was observed in September or
October 2026. This module writes a separate data directory in which each row carries the time
PAPER would have observed it, and changes nothing else:

- KIS daily bars (unadjusted, never revised) are observed at their own session close;
- DART filings and news are observed at 00:30 KST the day after their receipt or publication date,
  because PAPER's collection window ends yesterday (same-day documents arrive after midnight); a
  document's later revisions keep the time PAPER really collected them;
- annual financial statements are observed the same way from the receipt date in their DART
  receipt number (the collected rows carry no published_at).

Theme targets, document text and metadata are copied unchanged. `ReplayAnalysisData` is the live
`LocalAnalysisData` with only its "now" check lifted for one call at a time.
"""
from __future__ import annotations

import json
import shutil
import threading
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from src.runner import analysis_data
from src.runner.analysis_data import LocalAnalysisData, read_jsonl, source_time

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
OHLCV = ("open", "high", "low", "close", "volume")
# Documents and statements become visible to PAPER after midnight following their date.
NEXT_DAY_OBSERVATION = time(0, 30)


def next_day_observation(day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), NEXT_DAY_OBSERVATION, tzinfo=KST)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            # Key order is kept: DART structured fields are verified against their text line by line.
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _current_generation_dir(data_dir: Path, theme: str) -> Path:
    pointer = json.loads((data_dir / "canonical_index" / theme / "current.json").read_text(encoding="utf-8"))
    return data_dir / "canonical_index" / theme / "generations" / pointer["generation"]


def _bar_key(row: dict) -> tuple[str, str]:
    meta = row.get("metadata") or {}
    day = meta.get("trade_date") or source_time(row["timestamp"]).astimezone(KST).date().isoformat()
    return str(row["stock_code"]), str(day)


def _bar_values(row: dict) -> tuple[float, ...]:
    return tuple(float(str(row[field]).replace(",", "")) for field in OHLCV)


def _observed(row: dict, at: str) -> dict:
    meta = dict(row.get("metadata") or {})
    if meta.get("source") != "kis" or meta.get("price_basis") != "unadjusted" or not meta.get("bar_at"):
        raise ValueError(f"replay prices must be labelled KIS unadjusted bars: {_bar_key(row)}")
    meta.update(collected_at=at, available_at=at)
    out = {key: value for key, value in row.items() if key not in {"collected_at", "observed_at"}}
    out["metadata"] = meta
    return out


def _collected(row: dict) -> datetime:
    return source_time((row.get("metadata") or {}).get("collected_at"), naive_zone=UTC)


def _next_session_open(day: str) -> datetime:
    from src.runner.trading_calendar import daily_session_open, is_trading_day

    current = date.fromisoformat(day)
    for _ in range(30):
        current += timedelta(days=1)
        if is_trading_day(current.isoformat()):
            return daily_session_open(current.isoformat())
    raise ValueError(f"no trading session within 30 days after {day}")


def merge_prices(sources: list[list[dict]]) -> tuple[list[dict], dict]:
    """The bars PAPER saw, at the times it could have seen them.

    KIS revises a recent bar's close a day or two after the session (volume unchanged). A bar first
    observed before the next session's open came from the daily collection, so every observation
    keeps its real time and a replay sees the value PAPER saw then. A bar first observed later was
    backfilled; its last observation stands in for it from its own session close. Disagreements
    between observations are counted, never hidden."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for rows in sources:
        for row in rows:
            groups.setdefault(_bar_key(row), []).append(row)
    out: list[dict] = []
    revised, realtime = [], 0
    for key in sorted(groups):
        observations = sorted(groups[key], key=_collected)
        values = list(dict.fromkeys(_bar_values(row) for row in observations))
        if len(values) > 1:
            revised.append({"stock_code": key[0], "trade_date": key[1], "values": values})
        close = source_time(observations[0]["metadata"]["bar_at"])
        if _collected(observations[0]) <= _next_session_open(key[1]):
            realtime += 1
            seen: set[tuple[float, ...]] = set()
            for row in observations:
                if _bar_values(row) not in seen:  # first time each value was observed
                    seen.add(_bar_values(row))
                    out.append(_observed(row, max(_collected(row), close).isoformat()))
        else:
            out.append(_observed(observations[-1], close.isoformat()))
    days = sorted({key[1] for key in groups})
    return out, {"bars": len(groups), "rows": len(out), "realtime_observed_bars": realtime,
                 "first_trade_date": days[0] if days else None, "last_trade_date": days[-1] if days else None,
                 "stocks": len({key[0] for key in groups}), "revised_bars": revised}


def final_bars(rows: list[dict]) -> dict[tuple[str, str], tuple[float, ...]]:
    """The last observed OHLCV of every bar, for executing trades."""
    latest: dict[tuple[str, str], tuple[datetime, tuple[float, ...]]] = {}
    for row in rows:
        key, at = _bar_key(row), _collected(row)
        if key not in latest or at >= latest[key][0]:
            latest[key] = (at, _bar_values(row))
    return {key: value for key, (_, value) in latest.items()}


def _document_collected(row: dict) -> datetime | None:
    meta = row.get("metadata") or {}
    try:
        return source_time(row.get("collected_at") or meta.get("collected_at"), naive_zone=UTC)
    except (TypeError, ValueError):
        return None


def _document_observed(row: dict, first_revision: bool = True) -> dict:
    """A document's first revision is seen 00:30 KST the day after its date, or when it was really
    collected if that was earlier; a later revision keeps its real collection time, as PAPER saw it
    (giving every revision the same time would make them conflict)."""
    meta = dict(row.get("metadata") or {})
    raw = row.get("published_at") or meta.get("published_at")
    out = dict(row)
    try:
        published = source_time(raw)
    except (TypeError, ValueError):
        return out  # the live loader rejects it with its own reason, as in PAPER
    collected = _document_collected(row)
    observed = next_day_observation(published.astimezone(KST).date())
    if collected is not None and (collected < observed or not first_revision):
        observed = collected
    meta["collected_at"] = observed.isoformat()
    out["collected_at"] = observed.isoformat()
    out["metadata"] = meta
    return out


def observe_documents(rows: list[dict]) -> list[dict]:
    """Every row with its replay observation time. Revisions are grouped as the live loader groups
    them (source type and URL); the ones collected first count as the first revision."""
    def logical(row: dict) -> tuple:
        meta = row.get("metadata") or {}
        return (row.get("source_type") or meta.get("source_type"), row.get("url") or meta.get("url"))

    earliest: dict[tuple, datetime | None] = {}
    for row in rows:
        at, key = _document_collected(row), logical(row)
        if key not in earliest or (at is not None and (earliest[key] is None or at < earliest[key])):
            earliest[key] = at
    return [_document_observed(row, first_revision=_document_collected(row) == earliest[logical(row)])
            for row in rows]


def _financial_observed(row: dict) -> dict:
    meta = dict(row.get("metadata") or {})
    receipt = str(meta.get("rcept_no") or "")
    out = dict(row)
    if len(receipt) == 14 and receipt.isdigit():
        filed = date(int(receipt[:4]), int(receipt[4:6]), int(receipt[6:8]))
        meta["published_at"] = datetime.combine(filed, time(0, 0), tzinfo=KST).isoformat()
        meta["published_at_source"] = "dart_rcept_no_date"
        meta["collected_at"] = next_day_observation(filed).isoformat()
    out["metadata"] = meta
    return out


def build_pit_data(*, out_dir: Path, live_data_dir: Path, history_data_dir: Path | None = None) -> dict:
    """Write the replay data directory and return its manifest (also saved as manifest.json)."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    targets_dir = live_data_dir / "raw" / "theme_targets"
    themes = sorted(path.stem for path in targets_dir.glob("*.jsonl"))
    manifest: dict[str, Any] = {"built_at": datetime.now(UTC).isoformat(), "live_data_dir": str(live_data_dir),
                                "history_data_dir": str(history_data_dir) if history_data_dir else None,
                                "observation_rules": {
                                    "prices": "observed at their own KRX session close",
                                    "documents": "observed 00:30 KST the day after the publication or DART receipt date",
                                    "financials": "observed 00:30 KST the day after the DART receipt date in rcept_no"},
                                "themes": {}}
    for theme in themes:
        for suffix in (".jsonl", ".meta.json"):
            source = targets_dir / f"{theme}{suffix}"
            if source.exists():
                target = out_dir / "raw" / "theme_targets" / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        generation = _current_generation_dir(live_data_dir, theme)
        sources = []
        if history_data_dir is not None and (history_data_dir / "raw" / "chart" / f"{theme}.jsonl").exists():
            sources.append(read_jsonl(history_data_dir / "raw" / "chart" / f"{theme}.jsonl"))
        sources.append(read_jsonl(generation / "chart.jsonl"))
        prices, price_report = merge_prices(sources)
        _write_jsonl(out_dir / "market_data" / theme / "chart.jsonl", prices)
        documents = observe_documents(read_jsonl(generation / "documents.jsonl"))
        _write_jsonl(out_dir / "canonical_index" / theme / "documents.jsonl", documents)
        kinds: dict[str, int] = {}
        for row in documents:
            kind = str(row.get("source_type") or (row.get("metadata") or {}).get("source_type"))
            kinds[kind] = kinds.get(kind, 0) + 1
        manifest["themes"][theme] = {"generation": generation.name, "prices": price_report, "documents": kinds}
    financial_rows = 0
    for path in sorted((live_data_dir / "raw" / "financials").glob("*.jsonl")):
        rows = [_financial_observed(row) for row in read_jsonl(path)]
        financial_rows += len(rows)
        _write_jsonl(out_dir / "raw" / "financials" / path.name, rows)
    manifest["financial_rows"] = financial_rows
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


class _FrozenNow(datetime):
    frozen: datetime | None = None

    @classmethod
    def now(cls, tz=None):  # noqa: D102 - same signature as datetime.now
        assert cls.frozen is not None
        return cls.frozen if tz is None else cls.frozen.astimezone(tz)


_NOW_LOCK = threading.Lock()


@contextmanager
def frozen_analysis_now(as_of: datetime) -> Iterator[None]:
    """Make the live loader's clock read `as_of` for the duration of one call."""
    if as_of.tzinfo is None:
        raise ValueError("replay as_of must include a timezone")
    with _NOW_LOCK:
        original = analysis_data.datetime
        _FrozenNow.frozen = as_of
        analysis_data.datetime = _FrozenNow
        try:
            yield
        finally:
            analysis_data.datetime = original
            _FrozenNow.frozen = None


class ReplayAnalysisData(LocalAnalysisData):
    """The live loader on the point-in-time copy; only its "now" check reads the replay clock."""

    def load_universe(self, as_of: datetime):
        with frozen_analysis_now(as_of):
            return super().load_universe(as_of)


def allow_sessions_before_first_bar(first_bar_day: str) -> None:
    """The calendar refuses 2021-2023 Novembers (no CSAT notices recorded). Those sessions precede
    every stored bar, so no feature or missing-session check reads them; only they are allowed."""
    from src.runner import trading_calendar

    original = getattr(trading_calendar._check_special_session_coverage, "__wrapped__",
                       trading_calendar._check_special_session_coverage)

    def check(day: str) -> None:
        if day < first_bar_day and "2021" <= day[:4] <= "2023" and day[5:7] == "11":
            return
        original(day)

    check.__wrapped__ = original
    trading_calendar._check_special_session_coverage = check
