from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import asyncio
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_llm_queue_limits_concurrent_invocations():
    from src.utils.llm_queue import LLMInvocationQueue, LLMTaskPriority

    queue = LLMInvocationQueue(max_concurrency=1)
    active = 0
    peak = 0
    lock = threading.Lock()

    def run_one() -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return 1

    threads = [
        threading.Thread(target=lambda: queue.run(run_one, priority=LLMTaskPriority.UI_ANALYSIS))
        for _ in range(3)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak == 1


def test_llm_queue_prefers_runtime_priority_when_waiting():
    from src.utils.llm_queue import LLMInvocationQueue, LLMTaskPriority

    queue = LLMInvocationQueue(max_concurrency=1)
    order: list[str] = []
    low_started = threading.Event()

    def low_long() -> str:
        low_started.set()
        time.sleep(0.2)
        order.append("low-long")
        return "low-long"

    def low_waiting() -> str:
        order.append("low-waiting")
        return "low-waiting"

    def runtime_waiting() -> str:
        order.append("runtime-waiting")
        return "runtime-waiting"

    first = threading.Thread(target=lambda: queue.run(low_long, priority=LLMTaskPriority.UI_ANALYSIS))
    first.start()
    assert low_started.wait(timeout=1)

    low = threading.Thread(target=lambda: queue.run(low_waiting, priority=LLMTaskPriority.UI_ANALYSIS))
    high = threading.Thread(target=lambda: queue.run(runtime_waiting, priority=LLMTaskPriority.RUNTIME))
    low.start()
    time.sleep(0.03)
    high.start()
    time.sleep(0.03)

    first.join()
    low.join()
    high.join()

    assert order == ["low-long", "runtime-waiting", "low-waiting"]


@pytest.mark.parametrize("kwargs,tokens", [({"requests_per_minute": 1}, 0), ({"tokens_per_minute": 100}, 60)])
def test_queue_enforces_request_and_token_windows(kwargs, tokens):
    from src.utils.llm_queue import LLMInvocationQueue

    queue = LLMInvocationQueue(window_seconds=0.06, max_wait_seconds=1, **kwargs)
    times = [queue.run(time.monotonic, tokens=tokens), queue.run(time.monotonic, tokens=tokens)]
    assert times[1] - times[0] >= 0.05


def test_queue_times_out_without_running_or_leaking_waiter():
    from src.utils.llm_queue import LLMInvocationQueue, LLMQueueTimeout

    queue = LLMInvocationQueue(requests_per_minute=1, max_wait_seconds=0.01)
    queue.run(lambda: None)
    with pytest.raises(LLMQueueTimeout):
        queue.run(lambda: pytest.fail("must not run before admission"))
    assert queue._waiting == []
    assert queue._active == 0


def test_cancellation_while_waiting_does_not_leak_concurrency_slot():
    from src.utils.llm_queue import LLMInvocationQueue

    async def scenario():
        queue = LLMInvocationQueue(max_concurrency=1)
        release = asyncio.Event()
        started = asyncio.Event()

        async def first():
            started.set()
            await release.wait()

        running = asyncio.create_task(queue.arun(first))
        await started.wait()
        waiting = asyncio.create_task(queue.arun(first))
        await asyncio.sleep(0.02)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        release.set()
        await running
        await asyncio.sleep(0.02)
        assert queue._active == 0
        assert queue._waiting == []
        assert await queue.arun(lambda: asyncio.sleep(0, result="ok")) == "ok"

    asyncio.run(scenario())
