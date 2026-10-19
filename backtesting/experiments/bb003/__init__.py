"""BB003 forward buyback records. Importing this package performs no work."""
from datetime import datetime
from zoneinfo import ZoneInfo

EXPERIMENT_ID = "BB003_buyback_forward"
KST = ZoneInfo("Asia/Seoul")
START, END = "2026-12-01", "2028-09-30"
ACTIVATION_BY = datetime(2026, 11, 30, 23, 59, 59, tzinfo=KST)
MINIMUM_EFFECT = 0.009366926306807792


class NotReady(ValueError):
    """An identified required input is unavailable; never substitute data."""


def now():
    return datetime.now(KST)


def aware(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if stamp.utcoffset() is None:
        raise ValueError("BB003 timestamps must have a timezone")
    return stamp.astimezone(KST)
