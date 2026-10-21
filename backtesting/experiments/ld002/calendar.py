"""Only the shared verified exchange calendar establishes sessions."""
from datetime import date, timedelta

from src.runner import trading_calendar
from . import END, START, Refused


class Calendar:
    def sessions(self, start, end):
        return trading_calendar.session_calendar(start, end).strftime("%Y-%m-%d").tolist()

    def close(self, day):
        return trading_calendar.daily_session_close(day)

    def previous(self, day):
        end = date.fromisoformat(day) - timedelta(days=1)
        return self.sessions(end - timedelta(days=20), end)[-1]

    def next(self, day):
        start = date.fromisoformat(day) + timedelta(days=1)
        return self.sessions(start, start + timedelta(days=20))[0]

    def check(self, day):
        if not START <= day <= END:
            raise Refused("outside the fixed LD002 observation window")
        self.close(day)

    def monthly(self, day):
        return day == START or day == self.sessions(day[:8] + "01", day)[0]
