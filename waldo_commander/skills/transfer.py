"""Explicit pick/transfer/place sequences using ordinary composed skills."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable

from waldoctl import RobotClient
from waldoctl.setup import Pose
from waldoctl.signals import DigitalSignal
from waldoctl.skills import report_progress, skill

from waldo_commander.skills._motion import validate_motion
from waldo_commander.skills.gripper import _gripper, gripper_close, gripper_open
from waldo_commander.skills.motion import approach, retract
from waldo_commander.skills.signals import SignalFixture, read_signal, write_signal


def _validate(
    pick: Pose, place: Pose, clearance_mm: float, speed: float, timeout: float
) -> None:
    validate_motion(speed, timeout)
    if pick.frame != "WRF" or place.frame != "WRF":
        raise ValueError("Resolve both transfer poses to WRF before execution")
    if (
        isinstance(clearance_mm, bool)
        or not math.isfinite(clearance_mm)
        or clearance_mm <= 0
    ):
        raise ValueError("Transfer clearance must be finite and positive")


async def _transfer(
    rbt: RobotClient,
    pick: Pose,
    place: Pose,
    acquire: Callable[[], Awaitable[object]],
    release: Callable[[], Awaitable[object]],
    clearance_mm: float,
    speed: float,
    timeout: float,
) -> int:
    report_progress("Approaching pickup", fraction=0.0)
    await approach.async_call(
        rbt, target=pick, clearance_mm=clearance_mm, speed=speed, timeout=timeout
    )
    await acquire()
    await retract.async_call(
        rbt, distance_mm=clearance_mm, speed=speed, timeout=timeout
    )
    report_progress("Transferring to placement", fraction=0.5)
    await approach.async_call(
        rbt, target=place, clearance_mm=clearance_mm, speed=speed, timeout=timeout
    )
    await release()
    index = await retract.async_call(
        rbt, distance_mm=clearance_mm, speed=speed, timeout=timeout
    )
    report_progress(
        "Transfer commands completed; no grasp or placement observation inferred",
        fraction=1.0,
    )
    return index


@skill(
    id="waldo.transfer",
    version="1.0.0",
    requires=frozenset({"motion.linear", "tool.gripper"}),
)
async def transfer(
    rbt: RobotClient,
    *,
    pick: Pose,
    place: Pose,
    clearance_mm: float = 30.0,
    speed: float = 0.2,
    timeout: float = 30.0,
) -> int:
    """Approach, close, retract, approach, open, retract with the selected gripper.

    Begin with an empty, open tool. Clearance is positive tool Z at each
    target. Every leg uses native planning/collision checks. The returned
    index confirms the final command, not a sensed grasp or successful place.
    A failed or cancelled sequence makes no automatic recovery move.
    """
    _validate(pick, place, clearance_mm, speed, timeout)
    _gripper(rbt)
    return await _transfer(
        rbt,
        pick,
        place,
        lambda: gripper_close.async_call(rbt, timeout=timeout),
        lambda: gripper_open.async_call(rbt, timeout=timeout),
        clearance_mm,
        speed,
        timeout,
    )


@skill(
    id="waldo.transfer_with_signal",
    version="1.0.0",
    requires=frozenset({"motion.linear", "io.digital"}),
)
async def transfer_with_signal(
    rbt: RobotClient,
    *,
    pick: Pose,
    place: Pose,
    grip: DigitalSignal,
    closed_value: bool = True,
    clearance_mm: float = 30.0,
    speed: float = 0.2,
    timeout: float = 30.0,
    closed_fixture: SignalFixture | None = None,
    open_fixture: SignalFixture | None = None,
) -> int:
    """Transfer using a named output for grip/release, with electrical readback.

    Start with the output/tool in its open state. Preview requires separate
    explicit open and closed SignalFixtures. Readback is the electrical level;
    it does not confirm a grasp or physical part placement.
    """
    _validate(pick, place, clearance_mm, speed, timeout)
    grip.encode(closed_value)
    preview = "execution.preview" in rbt.skill_capabilities
    if not preview and (open_fixture is not None or closed_fixture is not None):
        raise ValueError("Signal fixtures require a preview client")
    await read_signal.async_call(
        rbt, grip, timeout=min(timeout, 1.0), fixture=open_fixture
    )
    # Refuse incomplete preview data before planning any transfer motion.
    if preview:
        await read_signal.async_call(rbt, grip, fixture=closed_fixture)
    return await _transfer(
        rbt,
        pick,
        place,
        lambda: write_signal.async_call(
            rbt, grip, closed_value, timeout=timeout, fixture=closed_fixture
        ),
        lambda: write_signal.async_call(
            rbt, grip, not closed_value, timeout=timeout, fixture=open_fixture
        ),
        clearance_mm,
        speed,
        timeout,
    )
