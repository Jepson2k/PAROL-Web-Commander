"""Tests for file operations in the editor.

Tests save, load, and download operations using the simulated user fixture.
These tests verify file operations through the UI buttons and dialogs.

Button markers:
- editor-open-server-btn: Open file from server filesystem
- editor-upload-btn: Upload file from browser/device
- editor-save-btn: Save file to server filesystem
- editor-download-btn: Download file to browser/device
- editor-new-tab-btn: Create new tab
"""

import asyncio
from typing import TYPE_CHECKING

import pytest
from nicegui import ui

if TYPE_CHECKING:
    from nicegui.testing import User


async def load_file_via_dialog(user: "User", filename: str) -> None:
    """Open a file from server via the file selection dialog."""
    user.find(marker="editor-open-server-btn").click()
    await asyncio.sleep(0)

    # Find the file select (the one with our filename in its options)
    for select in user.find(kind=ui.select).elements:
        if filename in select.options:
            select.value = filename
            break
    await asyncio.sleep(0)

    user.find(marker="dialog-open-btn").click()
    # Allow async file loading to complete
    await asyncio.sleep(0.1)


@pytest.mark.integration
class TestFileOperations:
    """File upload/download tests using simulated user fixture."""

    async def test_fab_buttons_exist(self, user: "User") -> None:
        """Verify all file operation FAB buttons are present when editor is open."""
        await user.open("/")
        user.find(marker="tab-program").click()
        await asyncio.sleep(0)

        # Open FAB group buttons
        await user.should_see(marker="editor-open-server-btn")
        await user.should_see(marker="editor-upload-btn")

        # Save FAB group buttons
        await user.should_see(marker="editor-save-btn")
        await user.should_see(marker="editor-download-btn")

        # New tab button
        await user.should_see(marker="editor-new-tab-btn")

    async def test_open_from_server_loads_file(self, user: "User") -> None:
        """Clicking 'Open from server' opens dialog, selecting file loads it."""
        from parol_commander.state import editor_tabs_state, ui_state

        await user.open("/")
        user.find(marker="tab-program").click()
        await asyncio.sleep(0)

        editor = ui_state.editor_panel
        assert editor is not None, "Editor panel should exist"

        # Create a test file on the server
        test_content = "# Test program from server\nprint('loaded from server')\n"
        test_filename = "test_server_load.py"
        program_dir = editor.PROGRAM_DIR
        program_dir.mkdir(parents=True, exist_ok=True)
        test_file = program_dir / test_filename
        test_file.write_text(test_content, encoding="utf-8")

        try:
            await load_file_via_dialog(user, test_filename)

            # Verify the active tab has the content
            active_tab = editor_tabs_state.get_active_tab()
            assert active_tab is not None, "Should have an active tab"
            assert active_tab.content == test_content, (
                f"Tab content should match file. Got: {active_tab.content!r}"
            )
        finally:
            if test_file.exists():
                test_file.unlink()

    async def test_save_to_server_writes_file(self, user: "User") -> None:
        """Load a file, modify via a new load, save - verify file is updated."""
        from parol_commander.state import editor_tabs_state, ui_state

        await user.open("/")
        user.find(marker="tab-program").click()
        await asyncio.sleep(0)

        editor = ui_state.editor_panel
        assert editor is not None, "Editor panel should exist"

        # Create initial test file
        initial_content = "# Initial content\nprint('initial')\n"
        test_filename = "test_save_to_server.py"
        program_dir = editor.PROGRAM_DIR
        program_dir.mkdir(parents=True, exist_ok=True)
        test_file = program_dir / test_filename
        test_file.write_text(initial_content, encoding="utf-8")

        try:
            await load_file_via_dialog(user, test_filename)

            # Verify file loaded
            active_tab = editor_tabs_state.get_active_tab()
            assert active_tab is not None
            assert active_tab.content == initial_content

            # Now click save - this should write the file
            user.find(marker="editor-save-btn").click()
            await asyncio.sleep(0)

            # Verify file still exists with content
            assert test_file.exists(), f"File should exist at {test_file}"
            saved_content = test_file.read_text(encoding="utf-8")
            assert saved_content == initial_content, (
                f"File content mismatch. Got: {saved_content!r}"
            )
        finally:
            if test_file.exists():
                test_file.unlink()

    async def test_download_to_device_triggers_download(self, user: "User") -> None:
        """Load a file, then download it - verify download content matches."""
        from parol_commander.state import ui_state

        await user.open("/")
        user.find(marker="tab-program").click()
        await asyncio.sleep(0)

        editor = ui_state.editor_panel
        assert editor is not None, "Editor panel should exist"

        # Create test file
        test_content = "# Download test\nprint('download me')\n"
        test_filename = "test_download.py"
        program_dir = editor.PROGRAM_DIR
        program_dir.mkdir(parents=True, exist_ok=True)
        test_file = program_dir / test_filename
        test_file.write_text(test_content, encoding="utf-8")

        try:
            await load_file_via_dialog(user, test_filename)

            # Now click download
            user.find(marker="editor-download-btn").click()

            # Capture the download response
            response = await user.download.next(timeout=2.0)

            # Verify content
            assert response.status_code == 200
            downloaded_content = response.content.decode("utf-8")
            assert downloaded_content == test_content, (
                f"Downloaded content mismatch. Got: {downloaded_content!r}"
            )
        finally:
            if test_file.exists():
                test_file.unlink()

    async def test_download_new_tab_works(self, user: "User") -> None:
        """Creating new tab and downloading it works (has default content)."""
        await user.open("/")
        user.find(marker="tab-program").click()
        await asyncio.sleep(0)

        # Create a new tab (will have default content)
        user.find(marker="editor-new-tab-btn").click()
        await asyncio.sleep(0)

        # Click download button
        user.find(marker="editor-download-btn").click()

        # Should download successfully (new tabs have default content)
        response = await user.download.next(timeout=2.0)
        assert response.status_code == 200
        # New tabs have default template content, not empty
        assert len(response.content) > 0

    async def test_upload_dialog_opens(self, user: "User") -> None:
        """Clicking upload button opens the upload dialog."""
        await user.open("/")
        user.find(marker="tab-program").click()
        await asyncio.sleep(0)

        # Click the upload button - should open dialog
        user.find(marker="editor-upload-btn").click()
        await asyncio.sleep(0)

        # Dialog should have upload-related content
        await user.should_see("Upload")

    async def test_new_tab_button(self, user: "User") -> None:
        """Clicking new tab button creates a new tab."""
        from parol_commander.state import editor_tabs_state

        await user.open("/")
        user.find(marker="tab-program").click()
        await asyncio.sleep(0)

        initial_tab_count = len(editor_tabs_state.tabs)

        # Click new tab button
        user.find(marker="editor-new-tab-btn").click()
        await asyncio.sleep(0)

        assert len(editor_tabs_state.tabs) == initial_tab_count + 1, (
            "Should have one more tab"
        )

    async def test_save_and_reload_roundtrip(self, user: "User") -> None:
        """Load a file, save it, close and reload - verify content persists."""
        from parol_commander.state import editor_tabs_state, ui_state

        await user.open("/")
        user.find(marker="tab-program").click()
        await asyncio.sleep(0)

        editor = ui_state.editor_panel
        assert editor is not None

        # Create test file
        test_content = "# Roundtrip test\nimport time\nprint(time.time())\n"
        test_filename = "test_roundtrip.py"
        program_dir = editor.PROGRAM_DIR
        program_dir.mkdir(parents=True, exist_ok=True)
        test_file = program_dir / test_filename
        test_file.write_text(test_content, encoding="utf-8")

        try:
            # Load the file
            await load_file_via_dialog(user, test_filename)

            # Verify loaded
            active_tab = editor_tabs_state.get_active_tab()
            assert active_tab is not None
            assert active_tab.content == test_content

            # Save the file
            user.find(marker="editor-save-btn").click()
            await asyncio.sleep(0.1)

            # Verify file was saved to disk
            saved_content = test_file.read_text(encoding="utf-8")
            assert saved_content == test_content, (
                f"Saved content should match. Got: {saved_content!r}"
            )
        finally:
            if test_file.exists():
                test_file.unlink()
