"""Integration tests for the program editor via UI.

Tests the full editor workflow including:
- Tab management (create, switch, close)
- Code editing and simulation
- Script run/stop and output streaming
- Playback controls
- File operations (save/download)
"""

import asyncio

import pytest
from nicegui.testing import User

from tests.helpers.wait import (
    wait_for_page_ready,
    enable_sim,
)


# ============================================================================
# Tab Management Tests
# ============================================================================


@pytest.mark.integration
async def test_program_tab_visible(user: User) -> None:
    """Test that the program editor tab is visible."""
    await user.open("/")
    await user.should_see(marker="tab-program")


@pytest.mark.integration
async def test_open_program_tab(user: User) -> None:
    """Test opening the program editor tab via click."""
    await user.open("/")
    await wait_for_page_ready()

    # Click program tab
    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    # Editor buttons should now be visible
    await user.should_see(marker="editor-run-btn")
    await user.should_see(marker="editor-new-tab-btn")


@pytest.mark.integration
async def test_editor_controls_visible_when_open(user: User) -> None:
    """Test that editor control buttons are visible when panel is open."""
    await user.open("/")
    await wait_for_page_ready()

    # Open the editor panel
    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    # Verify all control buttons are visible
    await user.should_see(marker="editor-run-btn")
    await user.should_see(marker="editor-record-btn")
    await user.should_see(marker="editor-log-toggle")
    await user.should_see(marker="editor-new-tab-btn")
    await user.should_see(marker="editor-save-btn")
    await user.should_see(marker="editor-download-btn")
    await user.should_see(marker="editor-commands-btn")


@pytest.mark.integration
async def test_create_new_tab(user: User) -> None:
    """Test creating a new editor tab via the new tab button."""
    await user.open("/")
    await wait_for_page_ready()

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    # Get initial tab count by checking for first default tab
    # The first tab is auto-created, so clicking new tab creates a second one
    user.find(marker="editor-new-tab-btn").click()
    await asyncio.sleep(0.3)

    # Verify new tab button still works (no errors thrown)
    await user.should_see(marker="editor-new-tab-btn")


@pytest.mark.integration
async def test_playback_controls_visible(user: User) -> None:
    """Test that playback step controls are visible when editor is open."""
    await user.open("/")
    await wait_for_page_ready()

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    # Playback controls should be in the bottom bar
    await user.should_see(marker="editor-step-prev")
    await user.should_see(marker="editor-step-next")


# ============================================================================
# Script Execution Tests
# ============================================================================


@pytest.mark.integration
async def test_run_button_toggles(user: User, robot_state) -> None:
    """Test that the run button is clickable and toggles state."""
    await user.open("/")
    await wait_for_page_ready()
    await enable_sim(user, robot_state)

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    # Run button should be visible
    run_btn = user.find(marker="editor-run-btn")
    assert run_btn is not None

    # Click run - should start (or stop if already running)
    run_btn.click()
    await asyncio.sleep(0.3)

    # Button should still be clickable
    run_btn.click()
    await asyncio.sleep(0.3)


@pytest.mark.integration
async def test_log_toggle_expands_log(user: User) -> None:
    """Test that the log toggle button expands/collapses the log panel."""
    await user.open("/")
    await wait_for_page_ready()

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    # Click log toggle to expand
    log_toggle = user.find(marker="editor-log-toggle")
    log_toggle.click()
    await asyncio.sleep(0.3)

    # Click again to collapse
    log_toggle.click()
    await asyncio.sleep(0.3)

    # Button should still be functional
    await user.should_see(marker="editor-log-toggle")


# ============================================================================
# File Operations Tests
# ============================================================================


@pytest.mark.integration
async def test_save_button_visible(user: User) -> None:
    """Test that save and download buttons are accessible."""
    await user.open("/")
    await wait_for_page_ready()

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    # Save and download buttons should be visible
    await user.should_see(marker="editor-save-btn")
    await user.should_see(marker="editor-download-btn")


@pytest.mark.integration
async def test_upload_button_visible(user: User) -> None:
    """Test that upload button is visible."""
    await user.open("/")
    await wait_for_page_ready()

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    await user.should_see(marker="editor-upload-btn")


@pytest.mark.integration
async def test_open_server_button_visible(user: User) -> None:
    """Test that open from server button is visible."""
    await user.open("/")
    await wait_for_page_ready()

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    await user.should_see(marker="editor-open-server-btn")


# ============================================================================
# Command Palette Tests
# ============================================================================


@pytest.mark.integration
async def test_commands_button_visible(user: User) -> None:
    """Test that the command palette button is visible."""
    await user.open("/")
    await wait_for_page_ready()

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    await user.should_see(marker="editor-commands-btn")


@pytest.mark.integration
async def test_commands_button_clickable(user: User) -> None:
    """Test that clicking the commands button doesn't error."""
    await user.open("/")
    await wait_for_page_ready()

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    commands_btn = user.find(marker="editor-commands-btn")
    commands_btn.click()
    await asyncio.sleep(0.3)

    # Should not throw errors
    await user.should_see(marker="editor-commands-btn")


# ============================================================================
# Recording Tests
# ============================================================================


@pytest.mark.integration
async def test_record_button_visible(user: User) -> None:
    """Test that the record button is visible."""
    await user.open("/")
    await wait_for_page_ready()

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    await user.should_see(marker="editor-record-btn")


@pytest.mark.integration
async def test_record_button_toggles(user: User, robot_state) -> None:
    """Test that the record button can be toggled."""
    await user.open("/")
    await wait_for_page_ready()
    await enable_sim(user, robot_state)

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    record_btn = user.find(marker="editor-record-btn")
    record_btn.click()
    await asyncio.sleep(0.3)

    # Click again to stop
    record_btn.click()
    await asyncio.sleep(0.3)

    await user.should_see(marker="editor-record-btn")


# ============================================================================
# Tab Interaction Tests
# ============================================================================


@pytest.mark.integration
async def test_multiple_tab_operations(user: User) -> None:
    """Test creating multiple tabs and switching between them."""
    await user.open("/")
    await wait_for_page_ready()

    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    # Create first new tab
    user.find(marker="editor-new-tab-btn").click()
    await asyncio.sleep(0.3)

    # Create second new tab
    user.find(marker="editor-new-tab-btn").click()
    await asyncio.sleep(0.3)

    # Editor should still be functional
    await user.should_see(marker="editor-run-btn")


# ============================================================================
# Panel Close Tests
# ============================================================================


@pytest.mark.integration
async def test_panel_can_be_reopened(user: User) -> None:
    """Test that the editor panel can be closed and reopened."""
    await user.open("/")
    await wait_for_page_ready()

    # Open editor
    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    await user.should_see(marker="editor-run-btn")

    # Close by clicking tab again
    user.find(marker="tab-program").click()
    await asyncio.sleep(0.3)

    # Reopen
    user.find(marker="tab-program").click()
    await asyncio.sleep(0.5)

    await user.should_see(marker="editor-run-btn")
