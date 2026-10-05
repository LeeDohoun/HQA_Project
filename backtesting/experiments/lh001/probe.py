"""Deterministic contamination measurement, never a strategy evaluation."""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import guard_data

MONTHS = pd.period_range("2025-01", "2026-09", freq="M")
READ_NOTE = ("The dedicated contamination reader accesses benchmark index rows and prior-month cap ranks / "
             "month-end closes of the top 30 common stocks, including 2026-01..06, before the holdout. "
             "This is contamination measurement, not a strategy evaluation; guard_period is deliberately "
             "not called for protected months and no holdout claim is consumed.")


class ProbeUnavailable(ValueError):
    pass


def month_ends(month, sessions):
    period = pd.Period(month, freq="M")
    days = pd.DatetimeIndex(sessions).sort_values().unique()
    ends = []
    for part in (period - 1, period):
        observed = days[days.to_period("M") == part]
        if not len(observed):
            raise ProbeUnavailable(f"missing month-end calendar: {part}")
        ends.append(observed[-1])
    return ends


def read_contamination_facts(months, *, data_dir, sessions):
    """The sole swappable pre-holdout exception. No financials, daily paths,
    opens, signals, strategy membership, or strategy returns leave this function.
    Prior-month cap rows must be scanned to identify 30 names; only selected
    stocks' current-month closes are retained. No strategy evaluator is called.
    """
    months = [pd.Period(month, freq="M") for month in months]
    anchors = {str(month): month_ends(month, sessions) for month in months}
    start = min(days[0] for days in anchors.values())
    end = max(days[1] for days in anchors.values())
    if end < pd.Timestamp("2026-01-01"):
        guard_data(start, end)
    benchmark_path = Path(data_dir) / "market_context/benchmarks.jsonl"
    if not benchmark_path.is_file():
        raise ProbeUnavailable("benchmark archive is required")
    month_days = {day for days in anchors.values() for day in days}
    latest = {}
    with benchmark_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            day = pd.Timestamp(row["trade_date"])
            if day in month_days and row["series"] == "KOSPI":
                latest[day, row["index_name"]] = float(row["close"])
    benchmarks = pd.DataFrame([{"trade_date": day, "series": "KOSPI", "index_name": name, "close": close}
                               for (day, name), close in sorted(latest.items())],
                              columns=("trade_date", "series", "index_name", "close"))
    result = {}
    for month in months:
        previous, current = anchors[str(month)]
        prior_path = Path(data_dir) / "market/krx_daily" / str(previous.year) / f"{previous:%Y%m%d}.jsonl"
        current_path = Path(data_dir) / "market/krx_daily" / str(current.year) / f"{current:%Y%m%d}.jsonl"
        if not prior_path.is_file() or not current_path.is_file():
            raise ProbeUnavailable(f"missing month-end prices: {month}")
        prior = {}
        with prior_path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                code = row["stock_code"]
                if code.endswith("0") and "스팩" not in row["stock_name"] and row["market"] in ("KOSPI", "KOSDAQ"):
                    prior[code] = {key: row[key] for key in ("stock_code", "stock_name", "market_cap", "close")}
        top = sorted(prior.values(), key=lambda row: (-float(row["market_cap"]), row["stock_code"]))[:30]
        if len(top) != 30:
            raise ProbeUnavailable(f"fewer than 30 prior-month common stocks: {month}")
        selected = {row["stock_code"] for row in top}
        closes = {}
        with current_path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row["stock_code"] in selected:
                    closes[row["stock_code"]] = float(row["close"])
        stocks = []
        for row in top:
            code, opening = row["stock_code"], float(row["close"])
            if code not in closes or opening <= 0 or closes[code] <= 0:
                raise ProbeUnavailable(f"missing top-30 month-end close: {month}/{code}")
            stocks.append({"stock_code": code, "name": row["stock_name"], "return": closes[code] / opening - 1})
        level_rows = benchmarks.loc[benchmarks.trade_date.eq(current) & benchmarks.series.eq("KOSPI")
                                    & benchmarks.index_name.eq("코스피")]
        if len(level_rows) != 1:
            raise ProbeUnavailable(f"missing exact KOSPI month-end level: {month}")
        sector_rows = benchmarks.loc[benchmarks.series.eq("KOSPI") & ~benchmarks.index_name.str.startswith(("코스피", "코스닥"))]
        wide = sector_rows.pivot(index="trade_date", columns="index_name", values="close").reindex([previous, current])
        returns = wide.iloc[1] / wide.iloc[0] - 1
        sectors = [{"name": name, "return": float(value)} for name, value in returns.items() if np.isfinite(value)]
        result[str(month)] = {"kospi_level": float(level_rows.close.iloc[0]), "sectors": sectors, "stocks": stocks,
                              "read_note": READ_NOTE, "dates": [previous.date().isoformat(), current.date().isoformat()]}
    return result


def make_questions(month, facts):
    month = str(pd.Period(month, freq="M"))
    rng = np.random.default_rng(int(month.replace("-", "")))
    questions = []
    def add(number, question, options, correct):
        order = rng.permutation(3)
        shown = [{"id": chr(65 + n), "label": options[int(index)]} for n, index in enumerate(order)]
        answer = chr(65 + int(np.flatnonzero(order == correct)[0]))
        questions.append({"id": f"q{number}", "question": question, "options": shown, "correct": answer})
    level = facts["kospi_level"]
    if not np.isfinite(level) or level <= 0:
        raise ProbeUnavailable("invalid KOSPI level")
    levels = [f"{level * factor:.2f}" for factor in (1.0, 0.8, 1.25)]
    if len(set(levels)) != 3:
        raise ProbeUnavailable("rounded KOSPI options are not distinct")
    add(1, f"{month} 월말 코스피 종가 수준은?", levels, 0)
    sectors = sorted(facts["sectors"], key=lambda row: row["name"])
    sectors = [sectors[int(index)] for index in rng.permutation(len(sectors))]
    triple = next((list(group) for group in itertools.combinations(sectors, 3)
                   if all(abs(a["return"] - b["return"]) >= 0.03 - 1e-12 for a, b in itertools.combinations(group, 2))), None)
    if triple is None:
        raise ProbeUnavailable(f"no three sector index returns separated by 3 percentage points: {month}")
    add(2, f"{month} 수익률이 가장 높은 업종 지수는?", [row["name"] for row in triple],
        int(np.argmax([row["return"] for row in triple])))
    stocks = sorted(facts["stocks"], key=lambda row: row["stock_code"])
    if len(stocks) != 30 or len({row["stock_code"] for row in stocks}) != 30:
        raise ProbeUnavailable("probe requires exactly 30 distinct prior-month largest stocks")
    sampled = rng.choice(30, size=6, replace=False)
    for number, indices in ((3, sampled[:3]), (4, sampled[3:])):
        group = [stocks[int(index)] for index in indices]
        values = [row["return"] for row in group]
        if not np.isfinite(values).all() or len(set(values)) != 3:
            raise ProbeUnavailable("stock question contains missing returns or a tie")
        add(number, f"{month} 수익률이 가장 높은 종목은?", [row["name"] + "(" + row["stock_code"] + ")" for row in group],
            int(np.argmax(values)))
    return {"month": month, "questions": questions}


def render_questions(quiz):
    return json.dumps({"month": quiz["month"], "questions": [{k: v for k, v in q.items() if k != "correct"}
                                                           for q in quiz["questions"]]}, ensure_ascii=False, separators=(",", ":"))


def validate_answers(output):
    answers = output["answers"]
    if len(answers) != 4 or {row["id"] for row in answers} != {"q1", "q2", "q3", "q4"}:
        raise ValueError("probe needs four distinct question IDs")
    if any(row["option"] not in ("A", "B", "C") for row in answers):
        raise ValueError("unknown probe option")


def score(quiz, output):
    validate_answers(output)
    answers = {row["id"]: row["option"] for row in output["answers"]}
    correct = sum(answers[q["id"]] == q["correct"] for q in quiz["questions"])
    return {"month": quiz["month"], "correct": correct, "questions": 4, "contaminated": correct >= 3}


def clean_window(scores, *, expected_months=MONTHS):
    rows = sorted(scores, key=lambda row: row["month"])
    expected = [str(month) for month in expected_months]
    if ([row["month"] for row in rows] != expected or any(row.get("questions") != 4
            or type(row.get("correct")) is not int or not 0 <= row["correct"] <= 4 for row in rows)):
        return {"verdict": "insufficient", "clean_start": None, "pooled_accuracy": None,
                "reason": "all scheduled months need four valid scored answers"}
    contaminated = [pd.Period(row["month"], freq="M") for row in rows if row["correct"] >= 3]
    start = max(pd.Period("2026-01", freq="M"), max(contaminated) + 1 if contaminated else pd.Period("2026-01", freq="M"))
    pushes, accuracy = [], None
    last = max(pd.Period(row["month"], freq="M") for row in rows)
    while start <= last:
        pooled = [row for row in rows if pd.Period(row["month"], freq="M") >= start]
        accuracy = sum(row["correct"] for row in pooled) / (4 * len(pooled))
        if accuracy <= 0.45:
            break
        pushes.append({"month": str(start), "accuracy": accuracy})
        start += 1
        accuracy = None
    verdict = "insufficient_clean_window" if start > pd.Period("2026-05", freq="M") else "clean"
    return {"verdict": verdict, "clean_start": start.start_time.date().isoformat(), "pooled_accuracy": accuracy,
            "last_contaminated_month": str(max(contaminated)) if contaminated else None, "pooled_pushes": pushes,
            "reason": "clean start later than May 2026" if verdict != "clean" else "contamination and pooled-accuracy rules satisfied"}
