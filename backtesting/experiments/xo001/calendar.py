"""Resolve only dates used by this run; official special-hour data are explicit."""
from datetime import date, datetime, time, timedelta
from urllib.parse import urlsplit

from . import END, KST, Refused, aware
from .storage import sha

TIME_KEYS = ("roster", "pre_start", "s0830", "s0850", "s0855", "s0858", "s0859",
             "publish", "submit", "open", "first_minute_end", "cancel", "post_end", "flatten", "close")
NORMAL = ("08:25:00", "08:25:00", "08:30:00", "08:50:00", "08:55:00", "08:58:00", "08:59:00",
          "08:59:30", "08:59:05", "09:00:00", "09:01:00", "09:01:00", "09:05:00", "15:20:00", "15:30:00")


class Calendar:
    def __init__(self, store=None, *, shared=None):
        if shared is None:
            from src.runner import trading_calendar as shared
        self.shared, self.store = shared, store

    def day(self, day):
        day = date.fromisoformat(day).isoformat()
        official = self.store.get("calendar", day) if self.store else None
        if official:
            if sha(self.store.bytes(official["notice_sha256"])) != official["notice_sha256"]:
                raise Refused("calendar notice integrity failure")
            return self._official(official)
        try:
            self.shared.require_verified_calendar(day)
            self.shared._check_special_session_coverage(day)
            calendar = self.shared._calendar(date.fromisoformat(day).year)
            if not calendar.is_session(day):
                return {"date": day, "session": False, "source": self.shared.CALENDAR_VERSION}
            close = aware(self.shared.daily_session_close(day))
            if day in self.shared.SPECIAL_CLOSES or close.time() != time(15, 30):
                raise Refused("special day needs explicit XO001 observation/order times")
            opening = aware(calendar.session_open(day).to_pydatetime())
            if opening.time() != time(9):
                raise Refused("special open needs explicit XO001 times")
        except ValueError as exc:
            raise Refused("calendar_unverified:" + day + ":" + str(exc)) from None
        return {"date": day, "session": True, "special": False, "source": self.shared.CALENDAR_VERSION,
                "times": {k: datetime.combine(date.fromisoformat(day), time.fromisoformat(v), KST).isoformat()
                          for k, v in zip(TIME_KEYS, NORMAL)}}

    @staticmethod
    def _official(record):
        day = record["date"]
        if not record["session"]:
            return {"date": day, "session": False, "source": record["notice_sha256"]}
        times = record["times"]
        if set(times) != set(TIME_KEYS) or any(aware(t).date().isoformat() != day for t in times.values()):
            raise Refused("official calendar requires every explicit intraday timestamp")
        t = {k: aware(v) for k, v in times.items()}
        if not (t["roster"] == t["pre_start"] < t["s0830"] < t["s0850"] < t["s0855"] < t["s0858"]
                < t["s0859"] < t["submit"] < t["publish"] < t["open"] < t["cancel"] <= t["post_end"]
                < t["flatten"] < t["close"] and t["first_minute_end"] == t["open"] + timedelta(seconds=60)):
            raise Refused("invalid explicit special-session time ordering")
        normal = {k: day + "T" + v + "+09:00" for k, v in zip(TIME_KEYS, NORMAL)}
        baseline = {k: aware(v) for k, v in normal.items()}
        for key in set(TIME_KEYS) - {"open", "close", "flatten"}:
            if t[key] - t["open"] != baseline[key] - baseline["open"]:
                raise Refused("special sessions must preserve the fixed observation/order offsets from opening")
        if t["close"] - t["flatten"] != timedelta(minutes=10):
            raise Refused("special cleanup must be explicit at ten minutes before official close")
        return {"date": day, "session": True, "special": t != baseline, "source": record["notice_sha256"], "times": times}

    def add_official(self, record, notice):
        if self.store is None:
            raise Refused("official calendar update requires a store")
        url = urlsplit(record["source_url"])
        if (url.scheme != "https" or url.username or url.password or not url.hostname
                or not (url.hostname == "krx.co.kr" or url.hostname.endswith(".krx.co.kr"))
                or aware(record["published_at"]) > aware(self.store.clock()) or not notice):
            raise Refused("calendar needs an already published official KRX notice")
        value = {**record, "notice_sha256": sha(notice)}
        self._official(value)
        self.store.blob(notice)
        return self.store.seal("calendar", record["date"], value)

    def previous(self, day, count):
        result, cursor = [], date.fromisoformat(day)
        while len(result) < count:
            cursor -= timedelta(days=1)
            if self.day(cursor.isoformat())["session"]:
                result.append(cursor.isoformat())
        return list(reversed(result))

    def last_entry(self):
        cursor = date.fromisoformat(END)
        while True:
            cursor += timedelta(days=1)
            if self.day(cursor.isoformat())["session"]:
                return cursor.isoformat()

    def freeze(self):
        last = self.last_entry()
        return datetime.combine(date.fromisoformat(last) + timedelta(days=7), time(23, 59, 59), KST)


def time_map(plan):
    if not plan["session"]:
        raise Refused("orders/capture require a verified opening session")
    return {k: aware(v) for k, v in plan["times"].items()}
