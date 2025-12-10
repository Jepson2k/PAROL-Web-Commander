"""Shared async wait helpers for integration tests.

These helpers provide condition-based waiting instead of blind asyncio.sleep(),
making tests more reliable and faster.
"""

import asyncio
import warnings
from typing import Callable

from nicegui.testing import User


async def simulate_click(user: User, marker: str, hold_ms: float = 50) -> None:
    """Simulate a mouse click with proper mousedown/mouseup events.

    The jog buttons use mousedown/mouseup events to detect clicks vs holds.
    NiceGUI's .click() method doesn't trigger these events correctly,
    so we need to manually trigger them.

    Args:
        user: NiceGUI User test fixture
        marker: The marker attribute to find the element
        hold_ms: Time in milliseconds to hold between mousedown and mouseup
    """
    element = user.find(marker=marker)
    element.trigger("mousedown")
    await asyncio.sleep(hold_ms / 1000.0)
    element.trigger("mouseup")


async def wait_for_motion_stable(
    get_value_fn: Callable[[], float],
    timeout_s: float = 3.0,
    tolerance: float = 0.1,
    stable_ticks: int = 10,
) -> float:
    """Wait for a value to stabilize (stop changing).

    Polls a value and waits until it stops changing for several consecutive
    readings, indicating motion has completed.

    Args:
        get_value_fn: Callable returning current value to monitor
        timeout_s: Maximum time to wait
        tolerance: Maximum change between ticks to consider "stable"
        stable_ticks: Number of consecutive stable ticks required

    Returns:
        The final stable value

    Raises:
        ValueError: If value accessor fails (e.g., empty angles list)
    """
    interval = 0.1
    last_value = None
    stable_count = 0

    # Get initial value with error handling
    try:
        last_value = get_value_fn()
    except (IndexError, KeyError, TypeError) as e:
        raise ValueError(
            f"wait_for_motion_stable: Cannot get initial value. "
            f"Ensure robot_state.angles is populated. Error: {e}"
        ) from e

    for _ in range(int(timeout_s / interval)):
        await asyncio.sleep(interval)
        try:
            current = get_value_fn()
        except (IndexError, KeyError, TypeError) as e:
            raise ValueError(
                f"wait_for_motion_stable: Value accessor failed mid-wait. Error: {e}"
            ) from e

        if last_value is not None and abs(current - last_value) < tolerance:
            stable_count += 1
            if stable_count >= stable_ticks:
                return current
        else:
            stable_count = 0
        last_value = current

    # Return last value even if didn't fully stabilize
    return get_value_fn()


async def enable_sim(user: User, robot_state, timeout_s: float = 5.0) -> None:
    """Enable simulator mode and wait for backend to be ready.

    This helper handles race conditions between test fixtures and app startup:
    1. Waits for initial backend readiness from startup
    2. Validates we have valid angles (6 values)
    3. Toggles simulator if needed and waits for simulator_ready event

    Args:
        user: NiceGUI User test fixture
        robot_state: The RobotState instance to check
        timeout_s: Maximum time to wait for simulator to activate

    Raises:
        TimeoutError: If simulator doesn't become ready within timeout
    """
    from parol_commander.state import readiness_state

    def _has_valid_angles() -> bool:
        angles = robot_state.angles
        return isinstance(angles, list) and len(angles) >= 6

    # Wait for initial backend readiness from startup
    try:
        await asyncio.wait_for(readiness_state.backend_ready.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass  # May not be set yet, continue

    # If we have valid angles and simulator is active, we're good
    if _has_valid_angles() and robot_state.simulator_active:
        return

    # Need to ensure simulator is enabled
    readiness_state.reset_simulator_ready()

    if not robot_state.simulator_active:
        user.find(marker="btn-robot-toggle").click()
        await asyncio.sleep(0.1)

    # Wait for simulator_ready event (set when STATUS arrives after toggle)
    try:
        await asyncio.wait_for(
            readiness_state.simulator_ready.wait(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        if not _has_valid_angles():
            raise TimeoutError(
                f"enable_sim: Simulator not ready after {timeout_s}s. "
                f"simulator_active={robot_state.simulator_active}, "
                f"angles={robot_state.angles}"
            ) from None

    # Final validation - poll for angles to be populated
    for _ in range(20):
        if _has_valid_angles():
            return
        await asyncio.sleep(0.1)

    if not _has_valid_angles():
        raise TimeoutError(
            f"enable_sim: No valid angles after waiting. angles={robot_state.angles}"
        )


async def wait_for_backend_ready(timeout_s: float = 5.0) -> None:
    """Wait for backend streaming to be active with valid robot data.

    This waits for the first valid STATUS message with 6 angles,
    indicating the backend controller is streaming data.

    Args:
        timeout_s: Maximum time to wait

    Raises:
        TimeoutError: If backend doesn't become ready within timeout
    """
    from parol_commander.state import readiness_state

    try:
        await asyncio.wait_for(readiness_state.backend_ready.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Backend not ready after {timeout_s}s. "
            f"backend_ready_ts={readiness_state.backend_ready_ts}"
        ) from None


async def wait_for_urdf_ready(timeout_s: float = 5.0) -> None:
    """Wait for URDF scene initialization to complete.

    This waits for the URDF 3D scene to be fully loaded and configured,
    including joint mappings and camera setup.

    Args:
        timeout_s: Maximum time to wait

    Raises:
        TimeoutError: If URDF scene doesn't initialize within timeout
    """
    from parol_commander.state import readiness_state

    try:
        await asyncio.wait_for(
            readiness_state.urdf_scene_ready.wait(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"URDF scene not ready after {timeout_s}s. "
            f"urdf_scene_ready_ts={readiness_state.urdf_scene_ready_ts}"
        ) from None


async def wait_for_page_ready(timeout_s: float = 5.0) -> None:
    """Wait for full page initialization including all components.

    This is the most comprehensive wait - use for tests that need
    everything initialized (backend + URDF + UI timers).

    Args:
        timeout_s: Maximum time to wait

    Raises:
        TimeoutError: If page doesn't become fully ready within timeout
    """
    from parol_commander.state import readiness_state

    try:
        await asyncio.wait_for(readiness_state.page_ready.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Page not ready after {timeout_s}s. "
            f"backend={readiness_state.backend_ready.is_set()}, "
            f"urdf={readiness_state.urdf_scene_ready.is_set()}, "
            f"page_ready_ts={readiness_state.page_ready_ts}"
        ) from None


async def wait_for_simulator_ready(timeout_s: float = 2.0) -> None:
    """Wait for simulator to be fully operational after toggle.

    This waits for the simulator_ready event, which is signaled when the
    first valid STATUS message arrives after a simulator toggle.

    Args:
        timeout_s: Maximum time to wait

    Raises:
        TimeoutError: If simulator doesn't become ready within timeout
    """
    from parol_commander.state import readiness_state

    try:
        await asyncio.wait_for(
            readiness_state.simulator_ready.wait(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Simulator not ready after {timeout_s}s. "
            f"simulator_ready_ts={readiness_state.simulator_ready_ts}"
        ) from None


async def wait_for_motion_start(
    robot_state,
    timeout_s: float = 2.0,
    require_detection: bool = False,
) -> bool:
    """Wait for motion to begin after a command is issued.

    In the NiceGUI test environment, robot_state may not be updated from
    STATUS messages because _status_consumer runs as a separate async task.

    This function:
    1. Records initial angles for comparison
    2. Yields control repeatedly to allow background tasks to run
    3. Checks if action_state becomes EXECUTING
    4. Checks if any joint angle has changed from baseline

    Args:
        robot_state: The RobotState instance to check
        timeout_s: Maximum time to wait
        require_detection: If True, raises TimeoutError if motion not detected

    Returns:
        True if motion was detected, False otherwise (unless require_detection=True)

    Raises:
        TimeoutError: If require_detection=True and no motion detected
    """
    import time

    # Record initial state for comparison
    initial_angles = list(robot_state.angles) if robot_state.angles else []
    start_time = time.time()

    # Brief delay to allow command to be sent
    await asyncio.sleep(0.1)

    # Yield control multiple times to give _status_consumer a chance to run
    interval = 0.05
    for _ in range(int(timeout_s / interval)):
        await asyncio.sleep(interval)

        # Check if action_state indicates motion
        if robot_state.action_state == "EXECUTING":
            return True

        # Check if timestamp updated since we started
        if robot_state.last_update_ts > start_time:
            # Check if any joint angle changed
            current_angles = robot_state.angles
            if current_angles and len(current_angles) >= 6 and initial_angles:
                for i in range(min(6, len(current_angles), len(initial_angles))):
                    if abs(current_angles[i] - initial_angles[i]) > 0.01:
                        return True

    # Motion not detected within timeout
    if require_detection:
        raise TimeoutError(
            f"wait_for_motion_start: No motion detected after {timeout_s}s. "
            f"action_state={robot_state.action_state}, "
            f"initial_angles={initial_angles[:3] if initial_angles else []}, "
            f"current_angles={robot_state.angles[:3] if robot_state.angles else []}"
        )

    # Continue anyway and let wait_for_motion_stable handle detection
    return False


async def wait_for_value_change(
    get_value_fn: Callable[[], float],
    timeout_s: float = 1.0,
    min_delta: float = 0.05,
) -> float:
    """Wait for a value to change from its initial reading.

    Alternative to wait_for_motion_start when action_state isn't reliable
    (e.g., quick incremental moves that complete before polling catches them).

    Args:
        get_value_fn: Callable returning current value to monitor
        timeout_s: Maximum time to wait
        min_delta: Minimum change required to consider "started"

    Returns:
        The new value after change detected

    Raises:
        TimeoutError: If no change detected within timeout
    """
    baseline = get_value_fn()
    interval = 0.05
    for _ in range(int(timeout_s / interval)):
        await asyncio.sleep(interval)
        current = get_value_fn()
        if abs(current - baseline) >= min_delta:
            return current
    raise TimeoutError(
        f"Value did not change by {min_delta} within {timeout_s}s. "
        f"baseline={baseline}, current={get_value_fn()}"
    )


async def ensure_robot_ready_for_motion(robot_state, timeout_s: float = 5.0) -> None:
    """Validate robot state is ready for motion testing.

    This is a comprehensive check that should be called after enable_sim()
    to ensure all prerequisites for motion commands are met.

    Args:
        robot_state: The RobotState instance to check
        timeout_s: Maximum time to wait for backend_ready

    Raises:
        TimeoutError: If backend_ready not signaled within timeout
        AssertionError: If robot state is invalid for motion
    """
    from parol_commander.state import readiness_state

    # Wait for backend to be streaming
    try:
        await asyncio.wait_for(readiness_state.backend_ready.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"ensure_robot_ready_for_motion: backend_ready not signaled after {timeout_s}s. "
            f"backend_ready_ts={readiness_state.backend_ready_ts}"
        ) from None

    # Validate angles
    angles = robot_state.angles
    assert isinstance(angles, list) and len(angles) >= 6, (
        f"ensure_robot_ready_for_motion: Invalid angles. "
        f"Expected list with >=6 elements, got: {angles}"
    )

    # Validate motion mode is active
    assert robot_state.simulator_active or robot_state.connected, (
        "ensure_robot_ready_for_motion: No motion mode active. "
        f"simulator_active={robot_state.simulator_active}, "
        f"connected={robot_state.connected}"
    )
