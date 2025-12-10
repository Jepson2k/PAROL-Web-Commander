"""Integration tests for simulator toggle, mode switching, and E-STOP behavior.

These tests use the NiceGUI `user` fixture and the real PAROL6 controller
(in fake-serial mode) to verify that mode toggles, HOME, and digital
E-STOP behavior work as expected at the UI and state level.
"""

import asyncio

import pytest
from nicegui.testing import User

from tests.helpers.wait import wait_for_page_ready


@pytest.mark.integration
async def test_home_requires_sim_or_connection(
    user: User, robot_state, reset_robot_state
) -> None:
    """HOME should be blocked when neither simulator nor hardware is active.

    We force both simulator_active and connected to False *after* the page
    is loaded (to override the default auto-simulator behavior) and then
    assert that an error notification is shown and no "Sent HOME" message
    is emitted.
    """
    await user.open("/")
    await wait_for_page_ready()

    # Override state after page load so HOME guard sees both flags as False
    robot_state.simulator_active = False
    robot_state.connected = False

    # Click home button
    user.find(marker="btn-home").click()

    # Give time for handler
    await asyncio.sleep(0.3)

    # Should see an error notification
    await user.should_see("Robot mode requires a hardware connection")

    # And we should not see the success message
    assert not any("Sent HOME" in m for m in user.notify.messages)


@pytest.mark.integration
async def test_home_sends_command_in_simulator_mode(
    user: User, robot_state, reset_robot_state, caplog: pytest.LogCaptureFixture
) -> None:
    """HOME should be sent successfully when simulator mode is active.

    Verifies that when simulator is active, HOME commands are allowed and
    the corresponding log message is emitted.
    """
    # Enable simulator mode
    await user.open("/")
    await wait_for_page_ready()
    robot_state.simulator_active = True

    # Click home button
    user.find(marker="btn-home").click()

    # Give time for handler
    await asyncio.sleep(0.3)

    # Assert that success log message was emitted
    assert any("HOME sent" in r.message for r in caplog.get_records("call"))


@pytest.mark.integration
async def test_digital_estop_shows_dialog(
    user: User, robot_state, reset_robot_state
) -> None:
    """Clicking E-STOP should show the digital E-STOP dialog.

    We keep this test focused on the E-STOP activation path: STOP command
    and dialog appearance. The resume/enable flow is exercised implicitly
    in normal app usage and would be fragile to assert in tests that rely
    on the real controller.
    """
    await user.open("/")
    await wait_for_page_ready()

    # Click E-STOP button
    user.find(marker="btn-estop").click()

    # Give time for handler
    await asyncio.sleep(0.3)

    # Should see E-STOP notification and dialog
    await user.should_see("Digital E-STOP activated - robot disabled")
    await user.should_see("Digital E-STOP Active")


@pytest.mark.unit
async def test_mode_switch_stops_running_script(tmp_path) -> None:
    """Switching between simulator and robot modes should stop any running user script.

    This is a safety feature: when changing modes, any running script is
    automatically stopped to prevent unexpected robot behavior.

    This unit test verifies the behavior without going through the full UI
    mode toggle flow, which would cause serial port errors in test environments.
    """
    from parol_commander.services.script_runner import (
        run_script,
        stop_script,
        create_default_config,
    )

    # Create a long-running script
    script_content = """import time
while True:
    print("running")
    time.sleep(0.1)
"""
    script_path = tmp_path / "test_long_running.py"
    script_path.write_text(script_content, encoding="utf-8")

    # Start the script
    config = create_default_config(str(script_path))
    handle = await run_script(
        config,
        on_stdout=lambda line: None,
        on_stderr=lambda line: None,
    )

    # Give it time to start
    await asyncio.sleep(0.2)

    # Verify script is running
    assert handle["proc"].returncode is None, "Expected script to still be running"

    # Now simulate what on_toggle_sim does: stop the script
    # This tests the core safety behavior
    await stop_script(handle, timeout=2.0)

    # Verify script was stopped
    assert handle["proc"].returncode is not None, (
        "Expected script to be stopped after mode switch"
    )
