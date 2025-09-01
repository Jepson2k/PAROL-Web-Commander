from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .types import RateResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


async def measure_async_rate(
    async_fn: Callable[[], Awaitable[None]], period_s: float, step_s: float
) -> RateResult:
    """
    Repeatedly await async_fn every step_s seconds for approximately period_s seconds.
    Returns RateResult where count is the number of calls and hz = count / actual_duration.
    """
    if period_s <= 0 or step_s <= 0:
        raise ValueError("period_s and step_s must be > 0")
    loop = asyncio.get_running_loop()
    start = loop.time()
    end = start + period_s
    count = 0
    next_tick = start
    while True:
        now = loop.time()
        if now >= end:
            break
        await async_fn()
        count += 1
        next_tick += step_s
        # sleep until next_tick (avoid drift)
        sleep_for = max(0.0, next_tick - loop.time())
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
    duration = loop.time() - start
    hz = (count / duration) if duration > 0 else 0.0
    return RateResult(duration_s=duration, count=count, hz=hz)


def assert_min_hz(result: RateResult, min_hz: float, tolerance_hz: float = 0.0) -> None:
    """
    Assert that result.hz >= min_hz - tolerance_hz. Raises AssertionError with helpful diagnostics.
    """
    effective_min = max(0.0, min_hz - max(0.0, tolerance_hz))
    if result.hz < effective_min:
        raise AssertionError(
            f"Rate too low: {result.hz:.2f} Hz (min {min_hz:.2f} Hz, tolerance {tolerance_hz:.2f} Hz); "
            f"duration={result.duration_s:.3f}s, count={result.count}"
        )
