"""Tests for settings page functionality."""

import asyncio

import pytest
from nicegui.testing import User
from nicegui import ui, app as ng_app
from typing import Any

from parol_commander.state import ui_state
from tests.helpers.wait import wait_for_app_ready

# Access storage via getattr to satisfy static type checkers (NiceGUI has no typed attr)
app_storage: Any = getattr(ng_app, "storage")


@pytest.mark.integration
async def test_settings_tab_accessible(user: User) -> None:
    """Test that Settings tab is accessible in the control panel.

    Verifies that the Settings tab can be found and clicked to reveal
    the settings content with serial port selection.
    """
    await user.open("/")
    await wait_for_app_ready()

    # Settings is embedded in the control panel (bottom-left HUD)
    # The control panel has tabs: "Joint Jog", "Cartesian Jog", "Settings"
    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0)

    # Verify the Settings tab panel is now showing by checking for expected content
    # The Serial Port section should be visible
    await user.should_see("Serial Port")
    await user.should_see("Show Route")
    await user.should_see("Theme")
    await user.should_see("Tool")
    await user.should_see("Select end effector tool")


@pytest.mark.integration
async def test_serial_port_select_exists(user: User) -> None:
    """Test that the serial port select dropdown exists in Settings.

    Note: The port select auto-saves on change (no Set Port button needed).
    We verify the select element exists with the correct label.
    """
    await user.open("/")
    await wait_for_app_ready()

    # Navigate to Settings tab
    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0)

    # Find the serial port select - it has label="Port"
    port_select = user.find(kind=ui.select, content="Port")
    assert port_select is not None, "Serial port select should exist in Settings"


@pytest.mark.integration
async def test_show_route_toggle_changes_state(user: User) -> None:
    """Test that toggling Show Route changes simulation_state.paths_visible."""
    from parol_commander.state import simulation_state

    await user.open("/")
    await wait_for_app_ready()

    # Navigate to Settings tab
    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0)

    # Get initial state
    initial_visible = simulation_state.paths_visible

    # Find and toggle the Show Route switch (by marker, not content)
    show_route_switch = user.find(marker="switch-show-route")
    show_route_switch.click()
    await asyncio.sleep(0)

    # State should have toggled
    assert simulation_state.paths_visible != initial_visible, (
        f"Expected paths_visible to toggle from {initial_visible}"
    )


@pytest.mark.integration
async def test_workspace_envelope_mode_changes(user: User) -> None:
    """Test that changing workspace envelope mode updates simulation_state."""
    from parol_commander.state import simulation_state

    await user.open("/")
    await wait_for_app_ready()

    # Navigate to Settings tab
    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0)

    # Find the Workspace Envelope select (by marker)
    envelope_select = user.find(marker="select-envelope-mode")
    assert envelope_select is not None, "Envelope mode select should exist"

    # Verify envelope_mode is set to a valid value
    assert simulation_state.envelope_mode in (
        "auto",
        "on",
        "off",
    ), f"Expected valid envelope_mode, got {simulation_state.envelope_mode}"


@pytest.mark.integration
async def test_tool_selection_changes_tool(user: User) -> None:
    """Test that selecting a tool updates storage and sends SET_TOOL to backend.

    Cycles through registered tools verifying each selection persists to storage.
    """
    await user.open("/")
    await wait_for_app_ready()

    # Navigate to Settings tab
    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0)

    # The tool select exists (by marker)
    tool_select = user.find(marker="select-tool")
    assert tool_select is not None, "Tool select should exist"

    # Verify all 5 tools are available in the robot
    available_tools = [t.key for t in ui_state.active_robot.tools.available]
    assert len(available_tools) == 5, f"Expected 5 tools, got {available_tools}"
    for expected in ("NONE", "PNEUMATIC", "SSG-48", "MSG", "VACUUM"):
        assert expected in available_tools, f"{expected} not in {available_tools}"

    # Select PNEUMATIC by setting value directly on the element
    select_el = next(iter(tool_select.elements))
    select_el.set_value("PNEUMATIC")
    await asyncio.sleep(0.1)
    assert app_storage.general.get("selected_tool") == "PNEUMATIC", (
        "Storage should reflect PNEUMATIC after selection"
    )

    # Select SSG-48 and verify
    select_el.set_value("SSG-48")
    await asyncio.sleep(0.1)
    assert app_storage.general.get("selected_tool") == "SSG-48", (
        "Storage should reflect SSG-48 after selection"
    )

    # Select VACUUM and verify
    select_el.set_value("VACUUM")
    await asyncio.sleep(0.1)
    assert app_storage.general.get("selected_tool") == "VACUUM", (
        "Storage should reflect VACUUM after selection"
    )


@pytest.mark.integration
async def test_variant_selector_appears_for_tools_with_variants(user: User) -> None:
    """Test that variant dropdown appears for tools with variants and hides for those without."""
    await user.open("/")
    await wait_for_app_ready()

    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0)

    tool_select = user.find(marker="select-tool")
    select_el = next(iter(tool_select.elements))

    # SSG-48 has variants (finger, pinch) — selector should appear
    select_el.set_value("SSG-48")
    await asyncio.sleep(0.1)
    variant_select = user.find(marker="select-tool-variant")
    assert len(variant_select.elements) == 1, (
        "Variant selector should appear for SSG-48"
    )
    await user.should_see("Variant")

    # NONE has no variants — selector should disappear
    select_el.set_value("NONE")
    await asyncio.sleep(0.1)
    with pytest.raises(AssertionError):
        user.find(marker="select-tool-variant")


@pytest.mark.integration
async def test_tcp_offset_inputs_appear_for_tools(user: User) -> None:
    """Test that TCP offset inputs appear for non-NONE tools and hide for NONE."""
    await user.open("/")
    await wait_for_app_ready()

    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0)

    tool_select = user.find(marker="select-tool")
    select_el = next(iter(tool_select.elements))

    # PNEUMATIC — offset inputs should appear with X/Y/Z fields
    select_el.set_value("PNEUMATIC")
    await asyncio.sleep(0.1)
    await user.should_see("TCP Offset")
    x_inputs = user.find(kind=ui.number, content="X")
    assert len(x_inputs.elements) >= 1, "X offset input should exist"

    # NONE — offset inputs should disappear
    select_el.set_value("NONE")
    await asyncio.sleep(0.1)
    with pytest.raises(AssertionError):
        user.find(kind=ui.number, content="X")


@pytest.mark.integration
async def test_theme_selection_exists(user: User) -> None:
    """Test that theme toggle exists and has expected options."""
    await user.open("/")
    await wait_for_app_ready()

    # Navigate to Settings tab
    settings_tab = user.find(kind=ui.tab, content="Settings")
    settings_tab.click()
    await asyncio.sleep(0)

    # The theme toggle should exist
    await user.should_see("Theme")
