"""Single-use holdout access with a durable ledger and scoped repeated reads."""
from __future__ import annotations

import json
import os
from contextvars import ContextVar, Token
from datetime import date, datetime, timezone
from pathlib import Path
from types import TracebackType

from backtesting.experiment_registry import PROJECT_ROOT, _locked_append, _resolve_path, verify_preregistration


HOLDOUT_START = date(2026, 1, 1)
HOLDOUT_END = date(2026, 6, 30)
LEDGER_PATH = Path("research/experiments/holdout_ledger.jsonl")
_SESSIONS: ContextVar[tuple[HoldoutSession, ...]] = ContextVar("holdout_sessions", default=())


def _parse_date(value: str | date) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str) and len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date()
    raise ValueError("period dates must be dates or YYYYMMDD strings")


def _claim(experiment_id: str, root: Path, ledger: Path, start: date, end: date) -> None:
    preregistration = verify_preregistration(experiment_id, root)
    if preregistration.fields["uses_holdout"] is not True:
        raise ValueError("preregistration must declare uses_holdout: true")
    with _locked_append(ledger) as handle:
        for number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid holdout ledger JSON on line {number}") from exc
            if not isinstance(row, dict) or not all(key in row for key in
                                                   ("experiment_id", "recorded_at", "from_date", "to_date")):
                raise ValueError(f"invalid holdout ledger entry on line {number}")
            if row["experiment_id"] == experiment_id:
                raise ValueError(f"holdout already used by experiment: {experiment_id}")
        handle.write(json.dumps({"experiment_id": experiment_id, "recorded_at": datetime.now(timezone.utc).isoformat(),
                                 "from_date": start.isoformat(), "to_date": end.isoformat()},
                                ensure_ascii=False, allow_nan=False) + "\n")


def guard_period(
    from_date: str | date,
    to_date: str | date,
    *,
    experiment_id: str | None = None,
    repo_root: str | Path = PROJECT_ROOT,
    ledger_path: str | Path = LEDGER_PATH,
) -> None:
    start, end = _parse_date(from_date), _parse_date(to_date)
    if start > end:
        raise ValueError("from_date must not be after to_date")
    if end < HOLDOUT_START or start > HOLDOUT_END:
        return
    if experiment_id is None:
        raise ValueError("holdout access requires experiment_id")
    root = Path(repo_root).resolve()
    ledger = _resolve_path(root, ledger_path)
    for session in _SESSIONS.get():
        if (session._open and session._pid == os.getpid() and session.experiment_id == experiment_id
                and session.repo_root == root and session.ledger_path == ledger):
            return
    _claim(experiment_id, root, ledger, start, end)


class HoldoutSession:
    """Consume one evaluation on entry, even if the evaluation later fails."""

    def __init__(
        self,
        experiment_id: str,
        *,
        repo_root: str | Path = PROJECT_ROOT,
        ledger_path: str | Path = LEDGER_PATH,
    ) -> None:
        self.experiment_id = experiment_id
        self.repo_root = Path(repo_root).resolve()
        self.ledger_path = _resolve_path(self.repo_root, ledger_path)
        self._open = False
        self._used = False
        self._pid: int | None = None
        self._token: Token | None = None

    def __enter__(self) -> HoldoutSession:
        if self._used:
            raise ValueError("a HoldoutSession cannot be reopened")
        _claim(self.experiment_id, self.repo_root, self.ledger_path, HOLDOUT_START, HOLDOUT_END)
        self._used = True
        self._open = True
        self._pid = os.getpid()
        self._token = _SESSIONS.set((*_SESSIONS.get(), self))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._open = False
        if self._token is not None:
            _SESSIONS.reset(self._token)

    def guard_period(self, from_date: str | date, to_date: str | date) -> None:
        if not self._open or self._pid != os.getpid():
            raise ValueError("HoldoutSession is not open")
        guard_period(from_date, to_date, experiment_id=self.experiment_id,
                     repo_root=self.repo_root, ledger_path=self.ledger_path)
