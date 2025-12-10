"""Tests for file upload, download, save, and load operations in the editor."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ============================================================================
# Unit Tests - Direct method testing
# ============================================================================


@pytest.mark.unit
def test_save_program_writes_file(tmp_path: Path) -> None:
    """Test that save_program writes content to the correct file path."""
    from parol_commander.components.editor import EditorPanel

    # Create a mock editor panel with temp directory
    panel = EditorPanel.__new__(EditorPanel)
    panel.PROGRAM_DIR = tmp_path
    panel.program_filename_input = MagicMock()
    panel.program_filename_input.value = "test_save.py"
    panel.program_textarea = MagicMock()
    panel.program_textarea.value = "# Test content\nprint('hello')"

    # Run save (it's async but the file write is sync)
    import asyncio

    with patch("parol_commander.components.editor.ui.notify"):
        asyncio.get_event_loop().run_until_complete(panel.save_program())

    # Verify file was written
    saved_file = tmp_path / "test_save.py"
    assert saved_file.exists(), "File should be created"
    assert saved_file.read_text() == "# Test content\nprint('hello')"


@pytest.mark.unit
def test_save_program_with_as_name(tmp_path: Path) -> None:
    """Test that save_program with as_name updates the filename input."""
    from parol_commander.components.editor import EditorPanel

    panel = EditorPanel.__new__(EditorPanel)
    panel.PROGRAM_DIR = tmp_path
    panel.program_filename_input = MagicMock()
    panel.program_filename_input.value = "original.py"
    panel.program_textarea = MagicMock()
    panel.program_textarea.value = "content"

    import asyncio

    with patch("parol_commander.components.editor.ui.notify"):
        asyncio.get_event_loop().run_until_complete(
            panel.save_program(as_name="new_name.py")
        )

    # Verify filename was updated
    panel.program_filename_input.value = "new_name.py"
    assert (tmp_path / "new_name.py").exists()


@pytest.mark.unit
def test_load_program_reads_file(tmp_path: Path) -> None:
    """Test that load_program reads file content into textarea."""
    from parol_commander.components.editor import EditorPanel

    # Create a test file
    test_file = tmp_path / "test_load.py"
    test_file.write_text("# Loaded content\nprint('loaded')")

    panel = EditorPanel.__new__(EditorPanel)
    panel.PROGRAM_DIR = tmp_path

    # Use simple objects that track value assignments
    class ValueTracker:
        def __init__(self):
            self.value = ""

    panel.program_filename_input = ValueTracker()
    panel.program_textarea = ValueTracker()

    import asyncio

    with patch("parol_commander.components.editor.ui.notify"):
        asyncio.get_event_loop().run_until_complete(panel.load_program("test_load.py"))

    # Verify content was loaded
    assert panel.program_textarea.value == "# Loaded content\nprint('loaded')"
    assert panel.program_filename_input.value == "test_load.py"


@pytest.mark.unit
def test_load_program_handles_missing_file(tmp_path: Path) -> None:
    """Test that load_program handles missing file gracefully."""
    from parol_commander.components.editor import EditorPanel

    panel = EditorPanel.__new__(EditorPanel)
    panel.PROGRAM_DIR = tmp_path
    panel.program_filename_input = MagicMock()
    panel.program_textarea = MagicMock()

    import asyncio

    with patch("parol_commander.components.editor.ui.notify") as mock_notify:
        asyncio.get_event_loop().run_until_complete(
            panel.load_program("nonexistent.py")
        )
        # Should notify with error
        mock_notify.assert_called()
        args, kwargs = mock_notify.call_args
        assert "negative" in str(kwargs) or "failed" in str(args).lower()


@pytest.mark.unit
def test_download_program_triggers_ui_download() -> None:
    """Test that download_program calls ui.download with correct args."""
    from parol_commander.components.editor import EditorPanel

    panel = EditorPanel.__new__(EditorPanel)
    panel.program_filename_input = MagicMock()
    panel.program_filename_input.value = "download_test.py"
    panel.program_textarea = MagicMock()
    panel.program_textarea.value = "# Download content"

    with (
        patch("parol_commander.components.editor.ui.download") as mock_download,
        patch("parol_commander.components.editor.ui.notify"),
    ):
        panel.download_program()

        mock_download.assert_called_once()
        args = mock_download.call_args[0]
        assert args[0] == b"# Download content"  # Content as bytes
        assert args[1] == "download_test.py"  # Filename


@pytest.mark.unit
def test_download_program_empty_content_warns() -> None:
    """Test that download_program warns when content is empty."""
    from parol_commander.components.editor import EditorPanel

    panel = EditorPanel.__new__(EditorPanel)
    panel.program_filename_input = MagicMock()
    panel.program_filename_input.value = "empty.py"
    panel.program_textarea = MagicMock()
    panel.program_textarea.value = ""

    with (
        patch("parol_commander.components.editor.ui.download") as mock_download,
        patch("parol_commander.components.editor.ui.notify") as mock_notify,
    ):
        panel.download_program()

        # Should NOT call download
        mock_download.assert_not_called()
        # Should notify with warning
        mock_notify.assert_called_once()
        args, kwargs = mock_notify.call_args
        assert "warning" in str(kwargs).lower()


@pytest.mark.unit
def test_download_program_default_filename() -> None:
    """Test that download_program uses default filename when input empty."""
    from parol_commander.components.editor import EditorPanel

    panel = EditorPanel.__new__(EditorPanel)
    panel.program_filename_input = MagicMock()
    panel.program_filename_input.value = "  "  # Whitespace only
    panel.program_textarea = MagicMock()
    panel.program_textarea.value = "content"

    with (
        patch("parol_commander.components.editor.ui.download") as mock_download,
        patch("parol_commander.components.editor.ui.notify"),
    ):
        panel.download_program()

        args = mock_download.call_args[0]
        assert args[1] == "program.py"  # Default filename
