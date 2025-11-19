"""Integration tests for control panel jogging functionality.

These tests use the NiceGUI `user` fixture and the real PAROL6 controller
(in fake-serial mode) to verify that jog controls actually change the
reported robot state, rather than just asserting on client call patterns.
"""
import asyncio

import pytest
from nicegui.testing import User


@pytest.mark.integration
async def test_joint_jog_button_sends_jog_joint(
    user: User, robot_state, reset_robot_state
) -> None:
    """Clicking a joint jog button should result in joint motion.

    Ensures that when simulator mode is active, clicking the J1 + jog
    button causes the reported J1 angle to change.
    """
    await user.open("/")
    await asyncio.sleep(4)

    # Ensure simulator mode is active so jogging is allowed
    assert robot_state.simulator_active
    initial_angles = list(robot_state.angles or [])

    # Click J1 plus button
    user.find(marker="btn-j1-plus").trigger("mousedown")
    await asyncio.sleep(0.5)
    user.find(marker="btn-j1-plus").trigger("mouseup")

    # Wait for controller to process jog and status to update
    await asyncio.sleep(0.7)

    new_angles = list(robot_state.angles or [])

    # We expect at least one joint angle reported and J1 to change
    assert len(new_angles) >= 1, "Expected at least one joint angle reported"
    if initial_angles:
        assert (
            new_angles[0] != initial_angles[0]
        ), "Expected J1 angle to change after jog"


@pytest.mark.integration
async def test_cartesian_jog_icon_sends_jog_cartesian(user: User, robot_state) -> None:
    """Clicking a Cartesian axis icon should move the TCP pose.

    Ensures that in simulator mode, clicking the X+ jog icon causes the
    reported pose to change.
    """
    await user.open("/")
    await asyncio.sleep(4)

    # Wait for initial pose
    assert robot_state.simulator_active
    initial_pose = list(robot_state.pose or [])

    # Click the X+ axis icon (marker is axis-xplus)
    user.find(marker="axis-xplus").click()

    # Wait for motion and status update
    await asyncio.sleep(0.7)

    new_pose = list(robot_state.pose or [])

    # We don't assume exact values, but pose should have changed
    if initial_pose and new_pose:
        assert new_pose != initial_pose, "Expected pose to change after cartesian jog"


@pytest.mark.unit
async def test_jogging_blocked_when_not_connected_or_simulating() -> None:
    """Safety guard should block jogging when neither sim nor robot is active.

    This unit-style test calls ControlPanel.set_joint_pressed directly with
    a RecordingAsyncClient and asserts that:
    - No jog_joint command is sent, and
    - The expected error notification is emitted via ui.notify.
    """
    from nicegui import ui
    from parol_commander.components.control import ControlPanel
    from parol_commander.state import robot_state
    from tests.helpers.fakes import RecordingAsyncClient

    # Set state to disallow motion
    robot_state.simulator_active = False
    robot_state.connected = False

    fake_client = RecordingAsyncClient()
    panel = ControlPanel(fake_client)

    # Capture notifications
    messages: list[str] = []
    original_notify = ui.notify
    ui.notify = lambda message, **kwargs: messages.append(str(message))  # type: ignore[assignment]
    try:
        # Press J1+ (is_pressed=True) should trigger guard and notification
        await panel.set_joint_pressed(0, "pos", True)
    finally:
        ui.notify = original_notify  # type: ignore[assignment]

    # No jog_joint commands should have been recorded
    assert all(c["name"] != "jog_joint" for c in fake_client.calls)

    # Error notification should mention hardware connection requirement
    assert any(
        "Robot mode requires a hardware connection" in m for m in messages
    ), "Expected safety notification when jogging is blocked"


@pytest.mark.integration
async def test_go_to_joint_limit_changes_joint_configuration(
    user: User, robot_state
) -> None:
    """Go-to-limit buttons should change the reported joint configuration.

    Clicking a joint limit button should result in a different set of
    joint angles being reported by the controller.
    """
    await user.open("/")
    await asyncio.sleep(4)

    # Wait for initial status and snapshot current angles
    assert robot_state.simulator_active
    initial_angles = list(robot_state.angles or [])

    # Find and click a limit button - the UI uses first_page/last_page icons
    user.find("first_page").click()

    # Give time for handler and status update
    await asyncio.sleep(3)

    new_angles = list(robot_state.angles or [])

    # We expect the configuration to change when going to a joint limit
    if initial_angles and new_angles:
        assert (
            new_angles != initial_angles
        ), "Expected joint configuration to change after go-to-limit"
