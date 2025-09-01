from __future__ import annotations

import re
from dataclasses import dataclass

_CMD_ID_RE = re.compile(r"^[0-9a-f]{8}$")


@dataclass(frozen=True)
class AckEvent:
    cmd_id: str
    status: str  # "QUEUED" | "EXECUTING" | "COMPLETED" | "FAILED" | "INVALID" | "CANCELLED"
    details: str
    t: float  # monotonic timestamp when received

    def __post_init__(self) -> None:
        if not self.status or not isinstance(self.status, str):
            raise ValueError("AckEvent.status must be a non-empty string")
        if not _CMD_ID_RE.match(self.cmd_id):  # noqa: SIM102
            # Be permissive during parsing; allow non-matching but not empty
            if not self.cmd_id:
                raise ValueError("AckEvent.cmd_id must be non-empty")


@dataclass(frozen=True)
class RateResult:
    duration_s: float
    count: int
    hz: float

    def __post_init__(self) -> None:
        if self.duration_s <= 0:
            raise ValueError("RateResult.duration_s must be > 0")
        if self.count < 0:
            raise ValueError("RateResult.count must be >= 0")
        # hz computed consistency check (not enforcing equality to allow rounding)
        if self.hz < 0:
            raise ValueError("RateResult.hz must be >= 0")
