from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from enum import IntEnum
from itertools import count
from typing import Any, Callable, Iterator, TypeVar

T = TypeVar("T")


class LLMTaskPriority(IntEnum):
    RUNTIME = 0
    UI_ANALYSIS = 10
    BACKGROUND = 20


_current_priority: contextvars.ContextVar[LLMTaskPriority] = contextvars.ContextVar(
    "hqa_llm_task_priority",
    default=LLMTaskPriority.UI_ANALYSIS,
)


def _positive_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


class LLMQueueTimeout(TimeoutError):
    pass


def current_llm_priority() -> LLMTaskPriority:
    return _current_priority.get()


class LLMInvocationQueue:
    def __init__(
        self,
        max_concurrency: int = 8,
        *,
        requests_per_minute: int = 120,
        tokens_per_minute: int = 200_000,
        max_wait_seconds: float = 45,
        window_seconds: float = 60,
    ):
        if min(max_concurrency, requests_per_minute, tokens_per_minute, max_wait_seconds, window_seconds) <= 0:
            raise ValueError("LLM admission limits must be positive")
        self.max_concurrency = max_concurrency
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.max_wait_seconds = max_wait_seconds
        self.window_seconds = window_seconds
        self._condition = threading.Condition()
        self._active = 0
        self._counter = count()
        self._waiting: list[tuple[int, int]] = []
        self._admitted: deque[tuple[float, int]] = deque()

    def run(self, fn: Callable[[], T], *, priority: LLMTaskPriority | None = None, tokens: int = 0) -> T:
        request = self._acquire(_current_priority.get() if priority is None else priority, tokens)
        try:
            return fn()
        finally:
            self._release(request)

    async def arun(
        self,
        fn: Callable[[], Any],
        *,
        priority: LLMTaskPriority | None = None,
        tokens: int = 0,
    ) -> Any:
        cancelled = threading.Event()
        acquisition = asyncio.create_task(asyncio.to_thread(
            self._acquire, _current_priority.get() if priority is None else priority, tokens, cancelled
        ))
        try:
            request = await asyncio.shield(acquisition)
        except asyncio.CancelledError:
            cancelled.set()
            with self._condition:
                self._condition.notify_all()

            def release_late_slot(task: asyncio.Task) -> None:
                try:
                    late_request = task.result()
                except (Exception, asyncio.CancelledError):
                    return
                self._release(late_request)

            acquisition.add_done_callback(release_late_slot)
            raise
        try:
            return await fn()
        finally:
            self._release(request)

    def _acquire(self, priority: LLMTaskPriority, tokens: int = 0, cancelled: threading.Event | None = None) -> tuple[int, int]:
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0 or tokens > self.tokens_per_minute:
            raise ValueError("Request tokens must fit the configured TPM limit")
        request = (int(priority), next(self._counter))
        deadline = time.monotonic() + self.max_wait_seconds
        with self._condition:
            self._waiting.append(request)
            try:
                while True:
                    now = time.monotonic()
                    if cancelled is not None and cancelled.is_set():
                        raise RuntimeError("LLM queue admission cancelled")
                    while self._admitted and self._admitted[0][0] <= now - self.window_seconds:
                        self._admitted.popleft()
                    rate_ready = (len(self._admitted) < self.requests_per_minute and
                                  sum(entry[1] for entry in self._admitted) + tokens <= self.tokens_per_minute)
                    if self._active < self.max_concurrency and min(self._waiting) == request and rate_ready:
                        self._active += 1
                        self._admitted.append((now, tokens))
                        break
                    remaining = deadline - now
                    if remaining <= 0:
                        raise LLMQueueTimeout("LLM admission deadline exceeded")
                    rate_wait = self._admitted[0][0] + self.window_seconds - now if self._admitted else remaining
                    self._condition.wait(timeout=min(remaining, max(0.001, rate_wait)))
            finally:
                self._waiting.remove(request)
                self._condition.notify_all()
        return request

    def _release(self, _request: tuple[int, int]) -> None:
        with self._condition:
            self._active -= 1
            self._condition.notify_all()


_GLOBAL_QUEUE = LLMInvocationQueue(
    max_concurrency=_positive_env("HQA_LLM_MAX_CONCURRENCY", 8),
    requests_per_minute=_positive_env("HQA_LLM_RPM", 120),
    tokens_per_minute=_positive_env("HQA_LLM_TPM", 200_000),
    max_wait_seconds=_positive_env("HQA_LLM_QUEUE_TIMEOUT_SECONDS", 45),
)


def run_with_llm_slot(fn: Callable[[], T], *, priority: LLMTaskPriority | None = None, tokens: int = 0) -> T:
    return _GLOBAL_QUEUE.run(fn, priority=priority, tokens=tokens)


async def arun_with_llm_slot(
    fn: Callable[[], Any],
    *,
    priority: LLMTaskPriority | None = None,
    tokens: int = 0,
) -> Any:
    return await _GLOBAL_QUEUE.arun(fn, priority=priority, tokens=tokens)


@contextmanager
def llm_task_priority(priority: LLMTaskPriority) -> Iterator[None]:
    token = _current_priority.set(priority)
    try:
        yield
    finally:
        _current_priority.reset(token)
