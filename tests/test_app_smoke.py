"""Smoke tests for PAROL Web Commander app startup and basic UI presence."""
import pytest
from nicegui.testing import User


@pytest.mark.integration
async def test_root_page_loads(user: User) -> None:
    """Test that the root page loads successfully and returns HTTP 200.

    This is a basic smoke test to ensure the app starts without errors.
    """
    await user.open("/")
    # User fixture automatically asserts HTTP 200


@pytest.mark.integration
async def test_core_ui_markers_present(user: User) -> None:
    """Test that core UI elements are present on the main page.

    Verifies that key control panel buttons, tabs, and readout elements
    are rendered and visible using their marker attributes.
    """
    await user.open("/")

    # Control panel buttons
    await user.should_see(marker="btn-home")
    await user.should_see(marker="btn-robot-toggle")
    await user.should_see(marker="btn-estop")

    # Side tabs
    await user.should_see(marker="tab-program")
    await user.should_see(marker="tab-io")
    await user.should_see(marker="tab-settings")
    await user.should_see(marker="tab-gripper")

    # Readout panel (at least one coordinate)
    await user.should_see(marker="readout-x")


@pytest.mark.integration
async def test_joint_jog_buttons_present(user: User) -> None:
    """Test that joint jog buttons are rendered for all joints.

    Verifies that the joint control interface is properly built.
    """
    await user.open("/")

    # Check that at least J1 plus and minus buttons exist
    await user.should_see(marker="btn-j1-plus")
    await user.should_see(marker="btn-j1-minus")

    # Check that J6 exists (last joint)
    await user.should_see(marker="btn-j6-plus")
    await user.should_see(marker="btn-j6-minus")
