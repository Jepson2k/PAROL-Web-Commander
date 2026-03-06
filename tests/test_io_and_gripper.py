"""Tests for I/O and gripper functionality."""

import asyncio

import pytest
from nicegui.testing import User

from tests.helpers.wait import wait_for_app_ready, wait_for_tool_key


@pytest.mark.integration
async def test_io_tab_high_low_buttons_send_commands(user: User, robot_state) -> None:
    """Clicking HIGH/LOW buttons in the I/O tab should send SET_IO commands.

    Verifies the full integration from UI button to controller by checking
    that the IO output state changes after clicking HIGH/LOW.
    """
    await user.open("/")
    await wait_for_app_ready()

    # Open the I/O tab
    user.find(marker="tab-io").click()
    await asyncio.sleep(0)

    # Click HIGH for OUTPUT 1 and wait for status propagation
    user.find("HIGH").click()
    for _ in range(20):
        await asyncio.sleep(0.05)
        if robot_state.io_outputs and int(robot_state.io_outputs[0]) == 1:
            break
    assert robot_state.io_outputs and int(robot_state.io_outputs[0]) == 1, (
        f"Expected OUTPUT 1 = HIGH (1) after click, got {robot_state.io_outputs}"
    )


@pytest.mark.integration
async def test_gripper_tab_operations(user: User, robot_state) -> None:
    """Test gripper calibrate and move send commands to the controller."""
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_app_ready()

    # Set tool on the controller and wait for status loop to propagate
    await ui_state.control_panel.client.set_tool("SSG-48")
    await wait_for_tool_key(robot_state, "SSG-48")

    # Open the Gripper tab
    user.find(marker="tab-gripper").click()
    await asyncio.sleep(0)

    # --- Calibrate (icon button with marker) ---
    user.find(marker="btn-grip-cal").click()
    await asyncio.sleep(0.1)
    # Calibrate should not produce an error notification
    assert not any("failed" in m.lower() for m in user.notify.messages), (
        f"Calibrate produced error: {user.notify.messages}"
    )

    # --- Move (icon button with marker) ---
    user.find(marker="btn-grip-move").click()
    await asyncio.sleep(0.1)
    # Move should not produce an error notification
    assert not any("failed" in m.lower() for m in user.notify.messages), (
        f"Move produced error: {user.notify.messages}"
    )


@pytest.mark.integration
async def test_gripper_panel_layout_elements(user: User, robot_state) -> None:
    """Gripper panel should show chart, status readouts, and controls."""
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_app_ready()

    # Set tool to SSG-48 and wait for status loop to propagate
    await ui_state.control_panel.client.set_tool("SSG-48")
    await wait_for_tool_key(robot_state, "SSG-48")

    # Open the Gripper tab
    user.find(marker="tab-gripper").click()
    await asyncio.sleep(0)

    # Chart element should exist
    await user.should_see(marker="gripper-chart")

    # Camera section exists (placeholder when no camera)
    await user.should_see(marker="gripper-camera-section")

    # Action buttons
    await user.should_see(marker="btn-grip-open")
    await user.should_see(marker="btn-grip-close")
    await user.should_see(marker="btn-grip-cal")
    await user.should_see(marker="btn-grip-move")


@pytest.mark.integration
async def test_control_panel_tool_quick_actions(user: User, robot_state) -> None:
    """Control panel should show tool quick-action box when a tool is active."""
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_app_ready()

    # Set tool to SSG-48 and wait for status loop to propagate
    await ui_state.control_panel.client.set_tool("SSG-48")
    await wait_for_tool_key(robot_state, "SSG-48")

    # Tool toggle button should be visible
    await user.should_see(marker="btn-tool-toggle")

    # Force jog buttons should be visible for electric grippers
    await user.should_see(marker="btn-tool-force-minus")
    await user.should_see(marker="btn-tool-force-plus")
