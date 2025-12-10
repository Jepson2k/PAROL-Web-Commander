"""Tests for settings page functionality."""

import asyncio

import pytest
from nicegui.testing import User
from nicegui import ui, app as ng_app
from typing import Any

from tests.helpers.wait import wait_for_page_ready

# Access storage via getattr to satisfy static type checkers (NiceGUI has no typed attr)
app_storage: Any = getattr(ng_app, "storage")


@pytest.mark.integration
async def test_settings_tab_accessible(user: User, reset_robot_state) -> None:
    """Test that Settings tab is accessible in the control panel.

    Verifies that the Settings tab can be found and clicked to reveal
    the settings content with serial port selection.
    """
    await user.open("/")
    await wait_for_page_ready()

    # Settings is embedded in the control panel (bottom-left HUD)
    # The control panel has tabs: "Joint Jog", "Cartesian Jog", "Settings"
    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0.1)

    # Verify the Settings tab panel is now showing by checking for expected content
    # The Serial Port section should be visible
    await user.should_see("Serial Port")
    await user.should_see("Show Route")
    await user.should_see("Theme")


@pytest.mark.integration
async def test_serial_port_select_exists(user: User, reset_robot_state) -> None:
    """Test that the serial port select dropdown exists in Settings.

    Note: The port select auto-saves on change (no Set Port button needed).
    We verify the select element exists with the correct label.
    """
    await user.open("/")
    await wait_for_page_ready()

    # Navigate to Settings tab
    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0.1)

    # Find the serial port select - it has label="Port"
    port_select = user.find(kind=ui.select, content="Port")
    assert port_select is not None, "Serial port select should exist in Settings"


@pytest.mark.integration
async def test_tool_dropdown_exists_in_settings(user: User, reset_robot_state) -> None:
    """Test that the tool selection dropdown exists in Settings.

    Verifies the Tool dropdown is present with the expected label.
    """
    await user.open("/")
    await wait_for_page_ready()

    # Navigate to Settings tab
    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0.1)

    # Verify Tool label and dropdown are visible
    await user.should_see("Tool")
    await user.should_see("Select end effector tool")
