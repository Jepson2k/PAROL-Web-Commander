"""Completion deadlines shared by managed dispatch and motion waits."""

import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class CompletionBudget:
    def __init__(self, timeout: float | None) -> None:
        if timeout is not None and (
            isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0
        ):
            raise ValueError("Completion timeout must be positive and finite")
        self.timeout = timeout
        self._clock: Callable[[], float] = time.monotonic
        self._start = self._clock()
        self._owner: object | None = None
        self.confirmed_index: int | None = None

    def bind(self, owner: object, clock: Callable[[], float]) -> None:
        if self._owner is not None:
            if self._owner != owner:
                raise RuntimeError(
                    "A completion budget cannot span controller sessions"
                )
            return
        elapsed = self._clock() - self._start
        self._owner = owner
        self._clock = clock
        self._start = clock() - elapsed

    @property
    def remaining(self) -> float:
        if self.timeout is None:
            return math.inf
        return max(0.0, self.timeout - (self._clock() - self._start))


current_budget: ContextVar[CompletionBudget | None] = ContextVar(
    "waldo_completion_budget", default=None
)


@contextmanager
def completion_scope(timeout: float) -> Iterator[CompletionBudget]:
    budget = CompletionBudget(timeout)
    token = current_budget.set(budget)
    try:
        yield budget
    finally:
        current_budget.reset(token)
