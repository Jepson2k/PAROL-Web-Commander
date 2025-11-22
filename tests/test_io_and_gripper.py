"""Tests for I/O and gripper functionality."""

import asyncio

import pytest
from nicegui.testing import User

from tests.helpers.fakes import RecordingAsyncClient


@pytest.mark.unit
def test_io_page_set_output_calls_control_pneumatic_gripper() -> None:
    """Test that IoPage.set_output maps to the correct client call.

    This is a pure unit test that verifies the mapping logic without
    requiring the full app.
    """
    from parol_commander.components.io import IoPage

    fake_client = RecordingAsyncClient()
    io_page = IoPage(fake_client)

    # Call set_output for port 1, state HIGH
    asyncio.run(io_page.set_output(1, 1))

    # Assert control_pneumatic_gripper was called
    calls = [c for c in fake_client.calls if c["name"] == "control_pneumatic_gripper"]
    assert len(calls) == 1, "Expected one control_pneumatic_gripper call"

    call = calls[0]
    assert call["args"][0] == "open", "Expected action=open for state=1"
    assert call["args"][1] == 1, "Expected port=1"


@pytest.mark.integration
async def test_io_tab_high_low_buttons_send_commands(
    user: User, robot_state, reset_robot_state
) -> None:
    """Clicking HIGH/LOW buttons in the I/O tab should send SET_IO commands.

    Verifies the full integration from UI button to controller by checking
    for SET_IO notifications and (if available) updated IO state.
    """
    await user.open("/")

    # Open the I/O tab
    user.find(marker="tab-io").click()

    # Give time for tab to open and initial IO state
    await asyncio.sleep(0.3)
    initial_out1 = int(getattr(robot_state, "io_out1", 0))

    # Find and click HIGH button for OUTPUT 1 (button text is "HIGH")
    user.find("HIGH").click()

    # Give time for handler and status propagation
    await asyncio.sleep(0.5)

    # Assert that a SET_IO notification was emitted
    assert any(
        "Sent SET_IO" in m for m in user.notify.messages
    ), "Expected SET_IO notification after HIGH click"

    # Optionally assert that IO output changed
    new_out1 = int(getattr(robot_state, "io_out1", 0))
    if initial_out1 != new_out1:
        assert new_out1 in (0, 1), "Expected IO OUT1 to be 0 or 1"


@pytest.mark.unit
def test_gripper_page_calibrate_calls_control_electric_gripper() -> None:
    """Test that GripperPage._grip_cal calls control_electric_gripper correctly.

    Verifies the calibrate command mapping.
    """
    from parol_commander.components.gripper import GripperPage

    fake_client = RecordingAsyncClient()
    gripper_page = GripperPage(fake_client)

    # Call _grip_cal
    asyncio.run(gripper_page._grip_cal())

    # Assert control_electric_gripper was called with calibrate action
    calls = [c for c in fake_client.calls if c["name"] == "control_electric_gripper"]
    assert len(calls) == 1, "Expected one control_electric_gripper call"

    call = calls[0]
    assert call["args"][0] == "calibrate", "Expected action=calibrate"


@pytest.mark.integration
async def test_gripper_tab_calibrate_button_sends_notification(
    user: User, reset_robot_state
) -> None:
    """Clicking Calibrate gripper should produce a notification.

    Uses the real controller and asserts that a notification is emitted
    when the calibrate button is clicked in the gripper tab.
    """
    await user.open("/")

    # Open the Gripper tab
    user.find(marker="tab-gripper").click()

    # Give time for tab to open
    await asyncio.sleep(0.3)

    before = list(user.notify.messages)

    # Find and click the calibrate button
    user.find("Calibrate gripper").click()

    # Give time for handler
    await asyncio.sleep(0.5)

    # At least one new notification should have been emitted
    assert len(user.notify.messages) > len(
        before
    ), "Expected a notification after clicking Calibrate gripper"


@pytest.mark.integration
async def test_gripper_move_goto_sends_notification(
    user: User, reset_robot_state
) -> None:
    """Clicking Move GoTo should produce a notification.

    Uses the real controller and asserts that a notification is emitted
    when the Move GoTo button is clicked in the gripper tab.
    """
    await user.open("/")

    # Open the Gripper tab
    user.find(marker="tab-gripper").click()

    # Give time for tab to open
    await asyncio.sleep(0.3)

    before = list(user.notify.messages)

    # Find and click the Move GoTo button (uses current slider values)
    user.find("Move GoTo").click()

    # Give time for handler
    await asyncio.sleep(0.5)

    # At least one new notification should have been emitted
    assert len(user.notify.messages) > len(
        before
    ), "Expected a notification after clicking Move GoTo"
