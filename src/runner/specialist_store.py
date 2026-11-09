"""Specialist answers kept on disk by request key (`HQA_SPECIALIST_CACHE_PATH`).

The service's in-memory cache already reuses an answer while the process runs, but a restart or an
eviction loses it. The 2026-11 live review found that 33% of analyst and 74% of quant calls re-sent an
input the model had already answered, and that a repeated answer moved by under one point on average.
An answer stored here is reused for the same request key (role, input, prompt version, factor version
and model settings) for `max_age`; older ones are asked again.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

UTC = timezone.utc


class SpecialistResultStore:
    def __init__(self, path: str | Path, *, max_age: timedelta = timedelta(days=7),
                 clock: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_age = max_age
        self.clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS specialist_results (key TEXT PRIMARY KEY, role TEXT NOT NULL, "
                       "created_at TEXT NOT NULL, result TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key: str) -> dict | None:
        """The stored answer for `key`, unless it is older than `max_age`."""
        oldest = (self.clock() - self.max_age).isoformat()
        with self._lock, self._connect() as db:
            row = db.execute("SELECT result FROM specialist_results WHERE key = ? AND created_at >= ?",
                             (key, oldest)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, role: str, result: dict) -> None:
        body = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO specialist_results(key, role, created_at, result) VALUES (?, ?, ?, ?)",
                       (key, role, self.clock().isoformat(), body))
