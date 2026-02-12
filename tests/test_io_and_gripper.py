"""Tests for I/O and gripper functionality."""

import asyncio

import pytest
from nicegui.testing import User

from tests.helpers.wait import wait_for_app_ready


@pytest.mark.integration
async def test_io_tab_high_low_buttons_send_commands(user: User, robot_state) -> None:
    """Clicking HIGH/LOW buttons in the I/O tab should send SET_IO commands.

    Verifies the full integration from UI button to controller by checking
    for SET_IO notifications and (if available) updated IO state.
    """
    await user.open("/")
    await wait_for_app_ready()

    # Open the I/O tab
    user.find(marker="tab-io").click()

    # Give time for tab to open and initial IO state
    await asyncio.sleep(0)
    initial_out1 = int(getattr(robot_state, "io_out1", 0))

    # Find and click HIGH button for OUTPUT 1 (button text is "HIGH")
    user.find("HIGH").click()

    # Give time for handler and status propagation
    await asyncio.sleep(0.1)

    # Assert that a SET_IO notification was emitted
    assert any("Sent SET_IO" in m for m in user.notify.messages), (
        "Expected SET_IO notification after HIGH click"
    )

    # Optionally assert that IO output changed
    new_out1 = int(getattr(robot_state, "io_out1", 0))
    if initial_out1 != new_out1:
        assert new_out1 in (0, 1), "Expected IO OUT1 to be 0 or 1"


@pytest.mark.integration
async def test_gripper_tab_calibrate_button_sends_notification(user: User) -> None:
    """Clicking Calibrate gripper should produce a notification.

    Uses the real controller and asserts that a notification is emitted
    when the calibrate button is clicked in the gripper tab.
    """
    await user.open("/")
    await wait_for_app_ready()

    # Open the Gripper tab
    user.find(marker="tab-gripper").click()

    # Give time for tab to open
    await asyncio.sleep(0)

    before = list(user.notify.messages)

    # Find and click the calibrate button
    user.find("Calibrate gripper").click()

    # Give time for handler to process and notification to propagate
    await asyncio.sleep(0.1)

    # At least one new notification should have been emitted
    assert len(user.notify.messages) > len(before), (
        "Expected a notification after clicking Calibrate gripper"
    )


@pytest.mark.integration
async def test_gripper_move_goto_sends_notification(user: User) -> None:
    """Clicking Move GoTo should produce a notification.

    Uses the real controller and asserts that a notification is emitted
    when the Move GoTo button is clicked in the gripper tab.
    """
    await user.open("/")
    await wait_for_app_ready()

    # Open the Gripper tab
    user.find(marker="tab-gripper").click()

    # Give time for tab to open
    await asyncio.sleep(0)

    before = list(user.notify.messages)

    # Find and click the Move GoTo button (uses current slider values)
    user.find("Move GoTo").click()

    # Give time for handler to process and notification to propagate
    await asyncio.sleep(0.1)

    # At least one new notification should have been emitted
    assert len(user.notify.messages) > len(before), (
        "Expected a notification after clicking Move GoTo"
    )
