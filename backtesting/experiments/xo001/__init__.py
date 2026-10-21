"""XO001 measurement recorder. Importing this package does no I/O."""
from datetime import datetime
from zoneinfo import ZoneInfo

EXPERIMENT_ID = "XO001_open_executability"
KST = ZoneInfo("Asia/Seoul")
START, END = "2026-12-01", "2028-09-30"
ACTIVATION_BY = datetime(2026, 11, 30, 23, 59, 59, tzinfo=KST)


class Refused(ValueError):
    """An explicit safety, evidence, or integrity requirement was not met."""


def now():
    return datetime.now(KST)


def aware(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.utcoffset() is None:
        raise Refused("XO001 requires timezone-aware timestamps")
    return stamp.astimezone(KST)
