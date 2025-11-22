"""Tests for settings page functionality."""

import asyncio

import pytest
from nicegui.testing import User
from nicegui import ui, app as ng_app
from typing import Any

# Access storage via getattr to satisfy static type checkers (NiceGUI has no typed attr)
app_storage: Any = getattr(ng_app, "storage")


@pytest.mark.integration
async def test_serial_port_persistence_and_set_port(
    user: User, reset_robot_state
) -> None:
    """Test that serial port can be configured via the Settings tab.

    Verifies that clicking "Set Port" results in a corresponding notification
    and that the stored port value is updated.
    """
    # Ensure a clean starting value in storage
    app_storage.general["com_port"] = ""

    await user.open("/")

    # Open the Settings tab
    user.find(marker="tab-settings").click()

    # Find the serial port input and type a test port
    test_port = "/dev/ttyTEST0"
    # Select the input element by type and label content
    port_input = user.find(kind=ui.input, content="Serial Port")
    port_input.type(test_port)

    # Click Set Port button
    user.find("Set Port").click()

    # Give time for handler
    await asyncio.sleep(0.3)

    # Assert that a SET_PORT notification was emitted
    assert any(
        test_port in m and "SET_PORT" in m for m in user.notify.messages
    ), "Expected SET_PORT notification containing the test port value"

    # And that the value is persisted in NiceGUI's general storage
    assert (
        app_storage.general.get("com_port") == test_port
    ), "Expected com_port to be stored in NiceGUI general storage"
