"""Explicit in-memory/fake fixtures. Never open project data or start a process."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from backtesting.experiments.ld002 import CODEX_VERSION, MODEL, Refused
from backtesting.experiments.ld002.ledger import initial_state
from backtesting.experiments.ld002.storage import aware, verified

CODES = [f"{i * 10:06d}" for i in range(1, 22)]


def output(codes=None, level=100):
    return {"invest_level": level, "holdings": [{"code": c, "conviction": 1} for c in (CODES[:10] if codes is None else codes)],
            "rationale": "합성 판단", "memo": "합성 메모"}


def snapshot(day="2026-11-16", codes=None):
    codes = CODES[:20] if codes is None else codes
    return {"decision_day": day, "as_of": FakeCalendar().previous(day), "universe": codes,
        "candidates": [{"code": c, "name": "합성", "close": 10000, "adv20": 1e10} for c in codes],
        "market": [], "disclosures": {"omitted_for_entire_window": True}, "ledger": {}, "rules": "v1",
        "adv20": dict.fromkeys(CODES, 1e10), "av": {"decision_sha256": "fixture-av", "codes": codes[:10],
            "weights": dict.fromkeys(codes[:10], .1)}}


def quote(price=10000, **kwargs):
    return {"close": price, "market": "KOSPI", "halted": False, "limit_up": False, "limit_down": False, **kwargs}


def portfolio(positions=None, cash=0):
    state = initial_state()
    state["cash"] = cash
    state["positions"] = {c: {"quantity": quantity, "avg_cost": price, "opened_on": "2026-11-12",
        "last_close": price, "last_seen": "2026-11-13", "stale_reason": None, "stale_days": 0, "stale_calendar_days": 0}
        for c, (quantity, price) in (positions or {}).items()}
    state["nav"] = cash + sum(q * p for q, p in (positions or {}).values())
    state["peak_nav"] = state["nav"]
    return state


def free_cost(*args, **kwargs):
    return 0.0


class FakeCalendar:
    days = pd.bdate_range("2024-01-01", "2028-01-31").strftime("%Y-%m-%d").tolist()

    def sessions(self, start, end):
        return [d for d in self.days if str(start) <= d <= str(end)]

    def previous(self, day):
        return max(d for d in self.days if d < day)

    def next(self, day):
        return min(d for d in self.days if d > day)

    def check(self, day):
        if not "2026-11-16" <= day <= "2027-11-15" or day not in self.days:
            raise Refused("fixture non-session")

    def close(self, day):
        return aware(day + "T15:30:00+09:00")

    def monthly(self, day):
        return day == "2026-11-16" or day == self.sessions(day[:8] + "01", day)[0]


class Clock:
    def __init__(self, value="2026-11-16T09:00:00+09:00"):
        self.value = aware(value)

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = aware(value)


def publication_clock(marker):
    return aware(verified(marker)["prepared_at"])


def identity():
    return {"model": MODEL, "codex_version": CODEX_VERSION, "executable": "/fixture/codex",
            "executable_sha256": "fixture-binary-sha", "artifacts": {"v1": "fixture-prompt-sha"}}


class FakeCodex:
    def __init__(self, answers=None, *, extra_events=(), after=None):
        self.answers = list(answers or [output()])
        self.calls, self.extra_events, self.after = [], list(extra_events), after

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        answer = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer(command, **kwargs)
        assert not list(Path(kwargs["cwd"]).iterdir())
        Path(command[command.index("-o") + 1]).write_text(json.dumps(answer))
        events = [{"type": "thread.started"}, {"type": "turn.started"}, *self.extra_events,
                  {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}},
                  {"type": "turn.completed"}]
        if self.after:
            self.after()
        return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(e) for e in events), stderr="")
