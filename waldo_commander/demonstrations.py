"""Capture and store controller observations without starting robot motion."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from waldoctl.client import RobotClient
from waldoctl.recordings import (
    MAX_RECORDING_SAMPLES,
    Demonstration,
    RecordedSample,
    RecordedTool,
    RecordingEnd,
)
from waldoctl.status import StatusBuffer

MAX_RECORDING_BYTES = 64 * 1024 * 1024


def _sample(status: StatusBuffer) -> RecordedSample:
    tool = status.tool_status
    observation = None
    if tool is not None and getattr(status, "tool_status_present", True):
        observation = RecordedTool(
            key=tool.key,
            variant_key=tool.variant_key,
            positions=tuple(float(v) for v in tool.positions),
            engaged=bool(tool.engaged),
            part_detected=bool(tool.part_detected),
            fault_code=int(tool.fault_code),
            state=int(tool.state),
            channels=tuple(float(v) for v in tool.channels),
        )
    return RecordedSample(
        seq=status.seq,
        observed_ns=status.mono_time_ns,
        received_ns=time.monotonic_ns(),
        joints_deg=tuple(float(v) for v in status.angles),
        tool=observation,
    )


def _tool_identity(sample: RecordedSample) -> tuple[str, str] | None:
    return (sample.tool.key, sample.tool.variant_key) if sample.tool else None


async def record_demonstration(
    client: RobotClient,
    *,
    duration_s: float = 30.0,
    stop: asyncio.Event | None = None,
    gap_threshold_s: float = 0.2,
    stale_timeout_s: float = 2.0,
    max_samples: int = MAX_RECORDING_SAMPLES,
    on_sample: Callable[[RecordedSample], None] | None = None,
) -> Demonstration:
    """Record fresh controller snapshots; disconnect/reference loss ends capture.

    Receipt times mark delivery to this recorder, including scheduling delay.
    The controller snapshot timestamps and publication sequence preserve the
    source cadence. ``stop`` ends acquisition only; it sends no robot command.
    """
    for value in (duration_s, gap_threshold_s, stale_timeout_s):
        if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError("Recording durations must be positive and finite")
    if type(max_samples) is not int or not 1 <= max_samples <= MAX_RECORDING_SAMPLES:
        raise ValueError("Invalid recording sample limit")
    caps = client.skill_capabilities
    backends = [c.removeprefix("backend.") for c in caps if c.startswith("backend.")]
    if "observation.timed" not in caps or len(backends) != 1:
        raise ValueError("This client does not provide identified, timed observations")

    stream = client.stream_status()
    samples: list[RecordedSample] = []
    ended: RecordingEnd = "duration_limit"
    try:
        async with asyncio.timeout(stale_timeout_s):
            rate = await client.status_rate()
            tcp = await client.tcp_transform()
            if rate is None or tcp is None:
                raise ConnectionError("Recording setup readback is unavailable")
            baseline = await anext(stream)
            while True:
                status = await anext(stream)
                if (
                    status.session_id != baseline.session_id
                    or status.seq > baseline.seq
                ):
                    break
        if not status.session_id or not status.mono_time_ns:
            raise ConnectionError("The controller did not supply recording timestamps")
        if not status.enabled or not status.homed:
            raise RuntimeError("Recording requires an enabled, referenced controller")
        session_id = status.session_id
        simulator = status.simulator_active
        first = _sample(status)
        samples.append(first)
        started = time.monotonic()
        if on_sample:
            on_sample(first)

        while len(samples) < max_samples:
            if stop is not None and stop.is_set():
                ended = "stopped"
                break
            remaining = duration_s - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout(min(stale_timeout_s, remaining)):
                    status = await anext(stream)
            except TimeoutError:
                ended = (
                    "stopped"
                    if stop is not None and stop.is_set()
                    else "duration_limit"
                    if remaining <= stale_timeout_s
                    else "disconnected"
                )
                break
            except (OSError, RuntimeError, StopAsyncIteration):
                ended = "disconnected"
                break
            if status.session_id != session_id:
                ended = "session_changed"
                break
            if not status.homed:
                ended = "reference_lost"
                break
            if not status.enabled:
                ended = "disabled"
                break
            if status.simulator_active != simulator:
                ended = "source_changed"
                break
            try:
                sample = _sample(status)
            except ValueError:
                ended = "invalid_observation"
                break
            previous = samples[-1]
            if (
                sample.seq <= previous.seq
                or sample.observed_ns <= previous.observed_ns
                or len(sample.joints_deg) != len(previous.joints_deg)
            ):
                ended = "invalid_observation"
                break
            if _tool_identity(sample) != _tool_identity(first):
                ended = "tool_changed"
                break
            samples.append(sample)
            if on_sample:
                on_sample(sample)
        else:
            ended = "sample_limit"
        return Demonstration(
            backend=backends[0],
            session_id=session_id,
            simulator=simulator,
            tcp_transform=tuple(tcp),
            requested_rate_hz=rate.hz,
            gap_threshold_s=gap_threshold_s,
            ended=ended,
            samples=tuple(samples),
        )
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


def save_demonstration(path: str | Path, recording: Demonstration) -> None:
    """Atomically save the explicit recording to a selected path."""
    data = json.dumps({"schema": 1, **asdict(recording)}, allow_nan=False).encode(
        "utf-8"
    )
    if len(data) > MAX_RECORDING_BYTES:
        raise ValueError("Recording exceeds the portable file size limit")
    path = Path(path)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = output.name
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def load_demonstration(path: str | Path) -> Demonstration:
    """Load observations only; this never connects to or configures a robot."""
    with Path(path).open("rb") as source:
        data = source.read(MAX_RECORDING_BYTES + 1)
    if len(data) > MAX_RECORDING_BYTES:
        raise ValueError("Recording exceeds the portable file size limit")
    return demonstration_from_dict(json.loads(data))


def demonstration_from_dict(document: dict) -> Demonstration:
    """Validate observations from a decoded portable recording document."""
    try:
        if not isinstance(document, dict):
            raise ValueError("Missing recording schema")
        raw = dict(document)
        schema = raw.pop("schema", None)
        if type(schema) is not int or schema != 1:
            raise ValueError("Unsupported recording schema")
        rows = raw.pop("samples")
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_RECORDING_SAMPLES:
            raise ValueError("Invalid recording sample list")
        samples = []
        for row in rows:
            row = dict(row)
            tool = row.pop("tool")
            samples.append(
                RecordedSample(
                    **row, tool=RecordedTool(**tool) if tool is not None else None
                )
            )
        return Demonstration(**raw, samples=tuple(samples))
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Invalid recording file") from error
