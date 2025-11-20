"""Integration tests for simulator toggle, mode switching, and E-STOP behavior.

These tests use the NiceGUI `user` fixture and the real PAROL6 controller
(in fake-serial mode) to verify that mode toggles, HOME, and digital
E-STOP behavior work as expected at the UI and state level.
"""
import asyncio

import pytest
from nicegui.testing import User


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
    user: User, robot_state, reset_robot_state
) -> None:
    """HOME should be sent successfully when simulator mode is active.

    Verifies that when simulator is active, HOME commands are allowed and
    the corresponding notification is shown.
    """
    # Enable simulator mode
    await user.open("/")
    robot_state.simulator_active = True

    # Click home button
    user.find(marker="btn-home").click()

    # Give time for handler
    await asyncio.sleep(0.3)

    # Assert that success notification is shown
    await user.should_see("Sent HOME")


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

    # Click E-STOP button
    user.find(marker="btn-estop").click()

    # Give time for handler
    await asyncio.sleep(0.3)

    # Should see E-STOP notification and dialog
    await user.should_see("Digital E-STOP activated - robot disabled")
    await user.should_see("Digital E-STOP Active")
