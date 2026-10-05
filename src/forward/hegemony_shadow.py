"""HC002 month-end shadow records and offline realised-price evaluation.

Both entry points are read-only unless execute=True. Canonical decisions are
immutable; explicit reruns are a separate audit archive, never observations.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as calendars
import numpy as np
import pandas as pd

from backtesting.experiment_registry import PROJECT_ROOT
from backtesting.experiments import hc002
from src.ingestion import dart_quarterly, krx_market
from src.ingestion.dart_backfill import detail_category
from src.ingestion.storage import atomic_write, file_lock, read_rows, write_rows


STRATEGY_ID = "HC002_phase2_ew_forward"
FORWARD_START = pd.Timestamp("2026-10-01")
KST = ZoneInfo("Asia/Seoul")
PREREGISTRATION = Path("research/experiments/HC002_operating_leverage_monthly/preregistration.md")
RECORD_DIR = Path("forward/hegemony_shadow")


def _now():
    return datetime.now(KST)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _git(*arguments):
    try:
        result = subprocess.run(["git", "-C", str(PROJECT_ROOT), *arguments],
                                capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unknown"


def _calendar(day, last=None):
    end = max(day, last) if last is not None else day
    return calendars.get_calendar("XKRX", start=day - pd.Timedelta(days=180),
                                  end=end + pd.Timedelta(days=90)).sessions


def _date(value):
    day = pd.Timestamp(value)
    if pd.isna(day) or day.tzinfo is not None or day != day.normalize():
        raise ValueError("decision_date must be a date without time or timezone")
    return day


def is_decision_day(day, *, now=None):
    day = _date(day)
    now = _now() if now is None else now.astimezone(KST)
    sessions = _calendar(day)
    monthly = sessions[sessions.to_period("M") == day.to_period("M")]
    return (day == monthly[-1] and day.date() <= now.date()
            and (day.date() < now.date() or now.time() >= time(16)))


def _price_days(data_dir):
    directory = Path(data_dir) / "market/krx_daily"
    return sorted(pd.Timestamp(path.stem) for path in directory.glob("*/*.jsonl")
                  if len(path.stem) == 8 and path.stem.isdigit() and path.stat().st_size)


def _volatility_tags(prices, sessions, codes):
    returns = prices.ret_1d.where(prices.calendar_status.eq("verified")).unstack("stock_code")
    window = returns.reindex(index=sessions, columns=codes)
    volatility = window.std(ddof=1).where(window.count() == 60)
    ordered = sorted((float(value), code) for code, value in volatility.items() if np.isfinite(value))
    quintiles = {code: 1 + rank * 5 // len(ordered) for rank, (_, code) in enumerate(ordered)}
    return {code: {"realised_volatility_60d": float(volatility[code]) if code in quintiles else "unknown",
                   "volatility_quintile_60d": quintiles.get(code, "unknown")} for code in codes}


def _capital_raise_tags(data_dir, start, end, codes):
    directory = Path(data_dir) / "disclosures/dart_full/list"
    days = [day.strftime("%Y%m%d") for day in pd.date_range(start, end)]
    tags, completed, archive = dict.fromkeys(codes, "unknown"), set(), []
    for day in days:
        path = directory / day[:4] / f"{day}.jsonl"
        if not path.is_file():
            continue
        completed.add(day)
        for row in read_rows(path):
            if (row.get("rcept_dt") != day or not isinstance(row.get("report_nm"), str)
                    or not isinstance(row.get("stock_code"), str)):
                raise ValueError(f"invalid DART listing archive: {path}")
            archive.append(row)
            if row["stock_code"] in tags and detail_category(row["report_nm"]) == "capital_raise":
                tags[row["stock_code"]] = True
    complete = directory.is_dir() and set(days).issubset(completed)
    if complete:
        tags = {code: value if value is True else False for code, value in tags.items()}
    return tags, {"available": directory.is_dir(), "complete": complete,
                  "window_start": start.date().isoformat(), "window_end": end.date().isoformat(),
                  "sha256": _digest(archive) if directory.is_dir() else "unknown"}


def _differences(before, after, prefix=""):
    if isinstance(before, dict) and isinstance(after, dict):
        differences = []
        for key in sorted(before.keys() | after.keys()):
            field = f"{prefix}.{key}".lstrip(".")
            if key not in before or key not in after:
                differences.append({"field": field, "before": before.get(key), "after": after.get(key),
                                    "change": "added" if key not in before else "removed"})
            else:
                differences.extend(_differences(before[key], after[key], field))
        return differences
    if before == after:
        return []
    return [{"field": prefix, "before": before, "after": after}]


class DecisionConflict(ValueError):
    def __init__(self, differences):
        self.differences = differences
        super().__init__("immutable decision differs:\n" + _json(differences))


def _save_decision(record, data_dir, *, execute, append_rerun):
    directory = Path(data_dir) / RECORD_DIR
    path = directory / "decisions" / (record["decision_date"][:7].replace("-", "") + ".json")

    def check_and_write():
        candidate = dict(record)
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            # Creation time belongs to the original observation, including on reruns.
            candidate["recorded_at"] = previous["recorded_at"]
            differences = _differences(previous, candidate)
            if not differences:
                return previous
            if not append_rerun:
                raise DecisionConflict(differences)
            candidate.update(recorded_at=record["recorded_at"], record_type="rerun",
                             rerun_of=str(path.name), differences=differences)
            if execute:
                reruns = directory / "reruns" / (path.stem + ".jsonl")
                write_rows(reruns, [*read_rows(reruns), candidate])
        elif execute:
            atomic_write(path, _json(candidate))
        return candidate

    if execute:
        with file_lock(directory / ".decisions.lock"):
            return check_and_write()
    return check_and_write()


def decide(decision_date, data_dir=PROJECT_ROOT / "data", *, execute=False,
           backfill_label=False, append_rerun=False):
    """Freeze the close-known HC002 phase-2 portfolio; never execute orders.

    Recency is checked against stored prices and the current completed session.
    A backfill flag always labels the observation reconstructed, even within the
    three-session grace period. Quintile ties use stock_code; incomplete tag
    histories stay unknown. The tag window includes the decision session.
    """
    day, now = _date(decision_date), _now()
    if day < FORWARD_START:
        raise ValueError("decision_date precedes the preregistered forward period (2026-10-01)")
    if not is_decision_day(day, now=now):
        raise ValueError("decision requires the month's last session, after 16:00 KST")
    days = _price_days(data_dir)
    if not days or days[-1] < day:
        raise ValueError("completed decision-day KRX prices are required locally")
    latest = days[-1]
    sessions = _calendar(day, max(latest, pd.Timestamp(now.date())))
    if latest not in sessions:
        raise ValueError("latest stored KRX day is not an XKRX session")
    position = sessions.get_loc(day)
    delay = sessions.get_loc(latest) - position
    if delay > 3 and not backfill_label:
        raise ValueError(f"backdated decision: {delay} sessions behind latest stored KRX day; use --backfill-label")
    completed_day = pd.Timestamp(now.date()) - pd.Timedelta(days=int(now.time() < time(16)))
    current_delay = sessions.searchsorted(completed_day, side="right") - 1 - position
    if current_delay > 3 and not backfill_label:
        raise ValueError(f"backdated decision: {current_delay} sessions behind current completed XKRX session; use --backfill-label")
    financial_dir = Path(data_dir) / "fundamentals/dart_quarterly"
    if not financial_dir.is_dir():
        raise ValueError("quarterly financial archive is required")
    trailing = sessions[position - 59:position + 1]
    prices = krx_market.load_prices(None, trailing[0].strftime("%Y%m%d"), day.strftime("%Y%m%d"), data_dir)
    missing = sessions[position - 19:position + 1].difference(prices.index.get_level_values("trade_date"))
    if len(missing):
        raise ValueError("missing required ADV sessions: " + ", ".join(date.date().isoformat() for date in missing))
    universe, report = hc002.universe_on_decision(prices, day, data_dir=data_dir)
    current = prices.xs(day, level="trade_date")
    known = dart_quarterly.load_quarterly(day, data_dir=data_dir)
    references = {(row.corp_code, row.fiscal_quarter): row for row in known.itertuples()}
    rows, used_dates = [], []
    for code, signal in universe.sort_index().iterrows():
        prior = references[(signal.corp_code, str(pd.Period(signal.fiscal_quarter, freq="Q") - 4))]
        used_dates.extend([signal.available_date, prior.available_date])
        rows.append({"stock_code": code, "name": str(current.loc[code, "stock_name"]),
                     "phase": int(signal.phase) if pd.notna(signal.phase) else None,
                     "score": float(signal.score), "revenue_yoy": float(signal.revenue_yoy),
                     "operating_income_yoy": float(signal.operating_income_yoy),
                     "fiscal_quarter": signal.fiscal_quarter, "rcept_no": signal.rcept_no,
                     "available_date": signal.available_date, "fs_div": signal.fs_div,
                     "avg_trading_value_20d": float(signal.avg_trading_value_20d),
                     "yoy_reference": {"fiscal_quarter": prior.fiscal_quarter, "rcept_no": prior.rcept_no,
                                       "available_date": prior.available_date}})
    codes = [row["stock_code"] for row in rows]
    volatility = _volatility_tags(prices, trailing, codes)
    capital, listing = _capital_raise_tags(data_dir, trailing[0], day, codes)
    chosen = [row for row in rows if row["phase"] == 2]
    holdings = [{**row, "weight": 1 / len(chosen), "tags": {
        **volatility[row["stock_code"]], "capital_raise_prior_60_sessions": capital[row["stock_code"]]}}
        for row in chosen]
    planned = sessions[position + 1:position + 1 + hc002.HORIZON]
    record = {"strategy_id": STRATEGY_ID, "record_type": "decision",
              "label": "reconstructed" if backfill_label else "forward",
              "recorded_at": now.isoformat(), "recording_delay_sessions": int(delay),
              "preregistration_commit": _git("log", "-1", "--format=%H", "--", str(PREREGISTRATION)),
              "code_commit": _git("rev-parse", "HEAD"),
              "code_snapshot_sha256": _digest({str(path.relative_to(PROJECT_ROOT)):
                  hashlib.sha256(path.read_bytes()).hexdigest() for path in (
                      Path(__file__), PROJECT_ROOT / "scripts/forward/hegemony_shadow.py",
                      PROJECT_ROOT / "backtesting/experiments/hc002.py",
                      PROJECT_ROOT / "backtesting/experiments/hc001.py",
                      PROJECT_ROOT / "backtesting/experiments/common.py",
                      PROJECT_ROOT / "backtesting/signal_eval.py", PROJECT_ROOT / "backtesting/cost_model.py",
                      PROJECT_ROOT / "src/ingestion/dart_quarterly.py", PROJECT_ROOT / "src/ingestion/krx_market.py",
                      PROJECT_ROOT / "src/research/industry_map.py", PROJECT_ROOT / "src/ingestion/dart_backfill.py")}),
              "calendar_version": f"exchange-calendars:{calendars.__version__}:XKRX",
              "decision_date": day.date().isoformat(), "planned_entry_date": planned[0].date().isoformat(),
              "planned_exit_date": planned[-1].date().isoformat(),
              "planned_sessions": [date.date().isoformat() for date in planned],
              "universe_size": len(rows), "universe": rows, "holdings": holdings,
              "data_snapshot": {"latest_krx_day": latest.date().isoformat(),
                  "latest_quarterly_receipt_date_used": max(used_dates) if used_dates else None,
                  "profile_coverage": report["industry_profiles"]["profile_coverage"],
                  "financial_exclusion_applied": report["industry_profiles"]["exclusion_applied"],
                  "universe_report": {key: value for key, value in report.items() if key != "corrections_used"},
                  "dart_listing": listing,
                  "price_history_sha256": hashlib.sha256(prices.to_json(orient="split").encode()).hexdigest(),
                  "known_financials_sha256": hashlib.sha256(known.to_json(orient="split").encode()).hexdigest()},
              "tag_policy": "informational only; trailing 60 sessions inclusive; volatility std(ddof=1), stock_code breaks ties"}
    return _save_decision(record, data_dir, execute=execute, append_rerun=append_rerun)


def _summary(values):
    values = np.asarray(values, dtype=float)
    count = len(values)
    mean = float(values.mean()) if count else None
    std = float(values.std(ddof=1)) if count > 1 else None
    return {"count": count, "mean": mean,
            "t_stat": float(mean / (std / np.sqrt(count))) if std is not None and std > 0 else None,
            "cumulative": float(np.prod(1 + values) - 1) if count else None}


def _running(rows):
    return {**_summary([row["excess"] for row in rows]), "metric": "net_excess",
            "gross": _summary([row["gross"] for row in rows]),
            "net": _summary([row["net"] for row in rows]),
            "turnover_based_excess": _summary([row["turnover_based"]["excess"] for row in rows])}


def _decisions(data_dir):
    directory = Path(data_dir) / RECORD_DIR / "decisions"
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("[0-9]" * 6 + ".json"))]


def evaluate(data_dir=PROJECT_ROOT / "data", *, execute=False):
    """Revalue frozen universes using HC002 policies; no experiment/holdout run.

    Excluded outcomes are reported, with HC002's equal weights over executable
    names. Reconstructed observations never enter forward statistics or reduce
    their turnover costs. Cumulative excess is compounding monthly net excess,
    a diagnostic rather than an investable wealth series.
    """
    records, days = _decisions(data_dir), _price_days(data_dir)
    latest = days[-1] if days else None
    pending, frames, matured = [], [], []
    for record in records:
        day, exit_day = pd.Timestamp(record["decision_date"]), pd.Timestamp(record["planned_exit_date"])
        if exit_day not in days:
            pending.append({"decision_date": record["decision_date"], "reason": "exit_close_not_stored"})
            continue
        sessions = _calendar(day, latest)
        planned = pd.DatetimeIndex(record["planned_sessions"])
        actual = sessions[sessions.get_loc(day) + 1:sessions.get_loc(day) + 1 + hc002.HORIZON]
        if not planned.equals(actual):
            raise ValueError("calendar disagrees with immutable planned holding sessions")
        prices = krx_market.load_prices(None, day.strftime("%Y%m%d"), latest.strftime("%Y%m%d"), data_dir)
        required = pd.DatetimeIndex([day, *planned])
        missing = required.difference(prices.index.get_level_values("trade_date"))
        if len(missing):
            pending.append({"decision_date": record["decision_date"], "reason": "missing_price_sessions",
                            "missing_sessions": [date.date().isoformat() for date in missing]})
            continue
        if not record["universe"]:
            pending.append({"decision_date": record["decision_date"], "reason": "empty_decision_universe"})
            continue
        universe = pd.DataFrame(record["universe"])
        universe["phase"] = pd.array(universe.phase, dtype="Int64")
        universe = universe.assign(decision_date=day, trade_date=pd.Timestamp(record["planned_entry_date"]))
        observations = hc002.forward_observations(prices, universe)
        observations["label"] = record["label"]
        frames.append(observations)
        matured.append(record)
    results = []
    if frames:
        frame = pd.concat(frames, ignore_index=True)
        primary = hc002.hc001.portfolio_metrics(frame)["per_rebalance"]
        secondary = {label: hc002.turnover_metrics(frame.loc[frame.label.eq(label)])
                     for label in ("forward", "reconstructed")}
        for record in matured:
            key = record["decision_date"]
            main = next((row for row in primary if row["trade_date"] == key), None)
            group = frame.loc[frame.trade_date.eq(pd.Timestamp(key))]
            outcomes = [{"stock_code": row.stock_code, "gross": float(row.gross) if np.isfinite(row.gross) else None,
                         "reason": row.reason if pd.notna(row.reason) else None,
                         "actual_exit_date": row.exit_date.date().isoformat() if pd.notna(row.exit_date) else None,
                         **{flag: bool(getattr(row, flag)) for flag in ("unadjusted_fallback", "stale_exit", "halted_after_entry", "limit_down_exit")}}
                        for row in group.itertuples()]
            if main is None:
                results.append({"decision_date": key, "label": record["label"],
                                "status": "no_executable_phase2_names", "outcomes": outcomes})
                continue
            turnover = secondary[record["label"]]
            other = next(row for row in turnover["per_rebalance"] if row["trade_date"] == key)
            churn = next(row for row in turnover["turnover_by_month"] if row["decision_date"] == key)
            results.append({"decision_date": key, "label": record["label"], "status": "evaluated",
                            "planned_holdings_count": len(record["holdings"]), "evaluated_holdings_count": main["count"],
                            "evaluated_universe_size": main["universe_count"], "gross": main["gross"],
                            "cost": main["gross"] - main["net_1.0"], "net": main["net_1.0"],
                            "universe_gross": main["universe_gross"], "excess": main["excess_1.0"],
                            "turnover_based": {**churn, "cost": other["gross"] - other["net_1.0"],
                                               "net": other["net_1.0"], "excess": other["excess_1.0"],
                                               "used_for_judgement": False}, "outcomes": outcomes})
    forward = []
    for row in results:
        if row["label"] == "forward" and row["status"] == "evaluated":
            forward.append(row)
        row["running_summary"] = _running(forward)
    payload = {"strategy_id": STRATEGY_ID, "evaluated_at": _now().isoformat(),
               "latest_krx_day": latest.date().isoformat() if latest is not None else None,
               "decisions": results, "pending": pending, "summary": _running(forward),
               "assumptions": ["HC002 entry exclusions and last-close/no-later-price policies; executable names are equally weighted.",
                   "Full round-trip cost is primary; retained executable names pay zero cost only in the secondary.",
                   "Reconstructed decisions and explicit reruns are excluded from forward statistics.",
                   "Cumulative excess compounds monthly net excess and is not an investable wealth series."]}
    if execute:
        with file_lock(Path(data_dir) / RECORD_DIR / ".evaluation.lock"):
            atomic_write(Path(data_dir) / RECORD_DIR / "evaluation.json", _json(payload))
    return payload


def status(data_dir=PROJECT_ROOT / "data"):
    """Inspect local recorder state without creating directories or querying APIs."""
    records, days = _decisions(data_dir), _price_days(data_dir)
    directory = Path(data_dir) / RECORD_DIR
    path = directory / "evaluation.json"
    evaluation = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    return {"strategy_id": STRATEGY_ID, "data_dir": str(Path(data_dir)), "decisions": len(records),
            "forward_decisions": sum(row["label"] == "forward" for row in records),
            "reconstructed_decisions": sum(row["label"] == "reconstructed" for row in records),
            "latest_decision_date": records[-1]["decision_date"] if records else None,
            "latest_krx_day": days[-1].date().isoformat() if days else None,
            "quarterly_archive_available": (Path(data_dir) / "fundamentals/dart_quarterly").is_dir(),
            "company_profiles_available": (Path(data_dir) / "reference/dart_company/companies.jsonl").is_file(),
            "dart_listing_available": (Path(data_dir) / "disclosures/dart_full/list").is_dir(),
            "evaluation_summary": evaluation["summary"] if evaluation is not None else None}
