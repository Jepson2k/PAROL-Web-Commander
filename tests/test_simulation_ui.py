import pytest
from nicegui.testing import User

from tests.helpers.wait import wait_for_page_ready


@pytest.mark.integration
async def test_program_tab_with_playback_exists(user: User) -> None:
    """Test that Program tab (with integrated playback) is available.

    Note: Simulation was merged into the Program Editor tab.
    The playback/scrubber bar appears as a floating overlay when simulating.
    """
    await user.open("/")
    await wait_for_page_ready()

    # Check for Program tab (which now includes simulation/playback functionality)
    await user.should_see("code")  # Program tab icon


@pytest.mark.integration
async def test_editor_run_button(user: User) -> None:
    """Test that Editor has Run button (play_arrow icon)."""
    await user.open("/")
    await wait_for_page_ready()

    # The run button uses icon="play_arrow", check for the icon
    await user.should_see("play_arrow")


@pytest.mark.integration
async def test_control_record_buttons(user: User) -> None:
    """Test that Control Panel has Record/Snapshot buttons."""
    await user.open("/")
    await wait_for_page_ready()

    # Check for icons
    await user.should_see("fiber_manual_record")
    await user.should_see("camera_alt")
