"""LH002 section 7: eight-question contamination probe and exact binomial rules."""
from __future__ import annotations

import hashlib
import itertools
from math import comb

import numpy as np
import pandas as pd

from . import probe
from .probe import ProbeUnavailable, month_ends, render_questions

CONTROL_MONTHS = pd.period_range("2024-01", "2024-06", freq="M")
MONTHS = CONTROL_MONTHS.append(pd.period_range("2025-01", "2026-09", freq="M"))
READ_NOTE = ("LH002 contamination measurement reads only KOSPI month-end levels, sector index returns "
             "and prior-month cap ranks / month-end closes of the 30 largest prior-month common stocks that still "
             "trade at month end (names delisted or merged during the month are skipped). The dedicated LH001 "
             "reader is extended to the 2024-01..06 positive control (anchors from 2023-12), plus "
             "2025-01..2026-09. This is not a strategy evaluation; guard_period is deliberately not "
             "called and no holdout claim is consumed.")


def read_contamination_facts(months, *, data_dir, sessions):
    facts = probe.read_contamination_facts(months, data_dir=data_dir, sessions=sessions, guard_unprotected=False,
                                           survivors_only=True)
    for row in facts.values():
        row["read_note"] = READ_NOTE
    return facts


def make_questions(month, facts):
    month = str(pd.Period(month, freq="M"))
    seed = int(hashlib.sha256(f"LH002:{month}".encode()).hexdigest()[:16], 16)
    rng = np.random.default_rng(seed)
    if not {"kospi_level", "sectors", "stocks"}.issubset(facts):
        raise ProbeUnavailable(f"missing probe facts: {month}")
    questions = []

    def add(number, question, options, correct):
        order = rng.permutation(3)
        shown = [{"id": chr(65 + n), "label": options[int(index)]} for n, index in enumerate(order)]
        answer = chr(65 + int(np.flatnonzero(order == correct)[0]))
        questions.append({"id": f"q{number}", "question": question, "options": shown, "correct": answer})

    level = facts["kospi_level"]
    if not np.isfinite(level) or level <= 0:
        raise ProbeUnavailable("invalid KOSPI level")
    position = int(rng.integers(3))
    factors = ((1, 1.15, 1.32), (0.87, 1, 1.15), (0.76, 0.87, 1))[position]
    levels = [f"{level * factor:.2f}" for factor in factors]
    if len(set(levels)) != 3:
        raise ProbeUnavailable("rounded KOSPI options are not distinct")
    add(1, f"{month} 월말 코스피 종가 수준은?", levels, position)

    sectors = facts["sectors"]
    if (len(sectors) < 6 or any(not {"name", "return"}.issubset(row) for row in sectors)
            or len({row["name"] for row in sectors}) != len(sectors)
            or not np.isfinite([row["return"] for row in sectors]).all()):
        raise ProbeUnavailable(f"probe requires at least six distinct sector index returns: {month}")
    ordered = sorted(sectors, key=lambda row: row["name"])
    ordered = [ordered[int(index)] for index in rng.permutation(len(ordered))]
    triples = [group for group in itertools.combinations(ordered, 3)
               if all(abs(a["return"] - b["return"]) >= 0.03 - 1e-12 for a, b in itertools.combinations(group, 2))]
    pair = next(((left, right) for left, right in itertools.combinations(triples, 2)
                 if {row["name"] for row in left}.isdisjoint(row["name"] for row in right)), None)
    if pair is None:
        raise ProbeUnavailable(f"no two disjoint sector triples separated by 3 percentage points: {month}")
    for number, group in enumerate(pair, 2):
        add(number, f"{month} 수익률이 가장 높은 코스피 업종 지수는?", [row["name"] for row in group],
            int(np.argmax([row["return"] for row in group])))

    stocks = facts["stocks"]
    if (len(stocks) != 30 or any(not {"stock_code", "name", "return"}.issubset(row) for row in stocks)
            or len({row["stock_code"] for row in stocks}) != 30
            or not np.isfinite([row["return"] for row in stocks]).all()):
        raise ProbeUnavailable("probe requires exactly 30 distinct prior-month largest stocks with finite returns")
    stocks = sorted(stocks, key=lambda row: row["stock_code"])
    sampled = rng.choice(30, size=15, replace=False)
    for number in range(4, 9):
        indices = sampled[(number - 4) * 3:(number - 3) * 3]
        group = [stocks[int(index)] for index in indices]
        values = [row["return"] for row in group]
        if values.count(max(values)) != 1:
            raise ProbeUnavailable(f"stock question has no unique best return: {month}/q{number}")
        add(number, f"{month} 수익률이 가장 높은 종목은?",
            [row["name"] + "(" + row["stock_code"] + ")" for row in group], int(np.argmax(values)))
    return {"month": month, "questions": questions}


def validate_answers(output):
    answers = output["answers"]
    if len(answers) != 8 or {row["id"] for row in answers} != {f"q{number}" for number in range(1, 9)}:
        raise ValueError("probe v2 needs eight distinct question IDs")
    if any(row["option"] not in ("A", "B", "C") for row in answers):
        raise ValueError("unknown probe option")
    if any(type(row["confidence"]) is not int or not 1 <= row["confidence"] <= 5
           or not isinstance(row["reason"], str) or len(row["reason"]) > 100 for row in answers):
        raise ValueError("probe v2 requires confidence 1-5 and reasons of at most 100 characters")


def score(quiz, output):
    validate_answers(output)
    answers = {row["id"]: row["option"] for row in output["answers"]}
    correct = sum(answers[q["id"]] == q["correct"] for q in quiz["questions"])
    return {"month": quiz["month"], "correct": correct, "questions": 8, "contaminated": correct >= 7}


def binomial_p(correct, total):
    """Exact upper tail P[X >= correct], X ~ Binomial(total, 1/3)."""
    if type(correct) is not int or type(total) is not int or not 0 <= correct <= total:
        raise ValueError("binomial counts must be integers with 0 <= correct <= total")
    return sum(comb(total, k) * 2 ** (total - k) for k in range(correct, total + 1)) / 3 ** total


def clean_window(scores):
    rows = sorted(scores, key=lambda row: row["month"])
    if ([row["month"] for row in rows] != [str(month) for month in MONTHS]
            or any(row.get("questions") != 8 or type(row.get("correct")) is not int
                   or not 0 <= row["correct"] <= 8 for row in rows)):
        return {"verdict": "insufficient", "clean_start": None, "pooled_accuracy": None, "pooled_p_value": None,
                "reason": "all 27 scheduled months need eight valid scored answers"}
    control_correct = sum(row["correct"] for row in rows if row["month"] in {str(month) for month in CONTROL_MONTHS})
    p_value = binomial_p(control_correct, 48)
    control = {"correct": control_correct, "questions": 48, "accuracy": control_correct / 48,
               "p_value": p_value, "sensitive": p_value < 0.05}
    report = {"positive_control": control, "boundary_2025": [row for row in rows if row["month"].startswith("2025-")],
              "contaminated_months": [row["month"] for row in rows if row["correct"] >= 7], "candidate_windows": [],
              "clean_start": None, "pooled_accuracy": None, "pooled_p_value": None}
    if not control["sensitive"]:
        return {**report, "verdict": "probe_insensitive", "reason": "positive-control one-sided binomial p must be <0.05"}
    for month in pd.period_range("2026-01", "2026-05", freq="M"):
        pooled = [row for row in rows if str(month) <= row["month"] <= "2026-09"]
        correct, total = sum(row["correct"] for row in pooled), 8 * len(pooled)
        p_value = binomial_p(correct, total)
        contaminated = [row["month"] for row in pooled if row["correct"] >= 7]
        report["candidate_windows"].append({"month": str(month), "correct": correct, "questions": total,
                                            "p_value": p_value, "contaminated_months": contaminated})
        if not contaminated and p_value >= 0.05:
            return {**report, "verdict": "clean", "clean_start": month.start_time.date().isoformat(),
                    "pooled_accuracy": correct / total, "pooled_p_value": p_value,
                    "reason": "earliest January-May start with no contaminated months and pooled one-sided binomial p>=0.05"}
    return {**report, "verdict": "insufficient_clean_window",
            "reason": "no January-May 2026 start satisfies both contamination and pooled-binomial rules"}
