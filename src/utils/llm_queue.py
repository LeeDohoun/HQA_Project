from __future__ import annotations

import asyncio
import contextvars
import os
import threading
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


def _default_max_concurrency() -> int:
    raw = os.getenv("HQA_LLM_MAX_CONCURRENCY", "1").strip()
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(1, value)


class LLMInvocationQueue:
    def __init__(self, max_concurrency: int = 1):
        self.max_concurrency = max(1, max_concurrency)
        self._condition = threading.Condition()
        self._active = 0
        self._counter = count()
        self._waiting: list[tuple[int, int]] = []

    def run(self, fn: Callable[[], T], *, priority: LLMTaskPriority | None = None) -> T:
        request = self._acquire(_current_priority.get() if priority is None else priority)
        try:
            return fn()
        finally:
            self._release(request)

    async def arun(
        self,
        fn: Callable[[], Any],
        *,
        priority: LLMTaskPriority | None = None,
    ) -> Any:
        request = await asyncio.to_thread(self._acquire, _current_priority.get() if priority is None else priority)
        try:
            return await fn()
        finally:
            self._release(request)

    def _acquire(self, priority: LLMTaskPriority) -> tuple[int, int]:
        request = (int(priority), next(self._counter))
        with self._condition:
            self._waiting.append(request)
            while self._active >= self.max_concurrency or min(self._waiting) != request:
                self._condition.wait()
            self._waiting.remove(request)
            self._active += 1
        return request

    def _release(self, _request: tuple[int, int]) -> None:
        with self._condition:
            self._active -= 1
            self._condition.notify_all()


_GLOBAL_QUEUE = LLMInvocationQueue(max_concurrency=_default_max_concurrency())


def run_with_llm_slot(fn: Callable[[], T], *, priority: LLMTaskPriority | None = None) -> T:
    return _GLOBAL_QUEUE.run(fn, priority=priority)


async def arun_with_llm_slot(
    fn: Callable[[], Any],
    *,
    priority: LLMTaskPriority | None = None,
) -> Any:
    return await _GLOBAL_QUEUE.arun(fn, priority=priority)


@contextmanager
def llm_task_priority(priority: LLMTaskPriority) -> Iterator[None]:
    token = _current_priority.set(priority)
    try:
        yield
    finally:
        _current_priority.reset(token)
