"""Type definitions for test helpers."""

from typing import Any, TypedDict


class RecordedCall(TypedDict):
    """Record of a single client method call for test assertions."""

    name: str
    """Method name on the client (e.g. 'jog_j', 'home', 'simulator')."""

    args: tuple[Any, ...]
    """Positional arguments passed to the method."""

    kwargs: dict[str, Any]
    """Keyword arguments passed to the method."""

    timestamp: float
    """time.time() at which the call was recorded."""


class RateSample(TypedDict):
    """Sample of control-rate cadence measurements."""

    label: str
    """Logical label for a cadence stream (e.g. 'joint', 'cart')."""

    deltas: list[float]
    """Measured or simulated inter-tick intervals."""
