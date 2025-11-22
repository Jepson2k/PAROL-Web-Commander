"""Unit tests for script runner service."""

import sys
from pathlib import Path

import pytest


@pytest.mark.unit
async def test_run_script_happy_path(tmp_path: Path) -> None:
    """Test that run_script successfully executes a simple script.

    Verifies basic process execution and stdout capture.
    """
    from parol_commander.services.script_runner import run_script

    # Write a simple test script that prints to stdout
    script_path = tmp_path / "test_script.py"
    script_path.write_text(
        """
print("Line 1")
print("Line 2")
print("Line 3")
"""
    )

    # Collect stdout lines
    stdout_lines = []

    def on_stdout(line: str) -> None:
        stdout_lines.append(line)

    # Run the script with config
    from parol_commander.services.script_runner import create_default_config

    config = create_default_config(str(script_path))
    handle = await run_script(
        config,
        on_stdout=on_stdout,
        on_stderr=lambda line: None,
    )

    # Wait for completion
    if handle and handle["proc"]:
        await handle["proc"].wait()

    # Assert stdout was captured in order
    assert len(stdout_lines) >= 3, "Expected at least 3 lines of stdout"
    assert "Line 1" in stdout_lines[0]
    assert "Line 2" in stdout_lines[1]
    assert "Line 3" in stdout_lines[2]


@pytest.mark.unit
async def test_run_script_missing_file_raises() -> None:
    """Test that run_script raises FileNotFoundError for non-existent script.

    Verifies error handling for missing files.
    """
    from parol_commander.services.script_runner import run_script, create_default_config

    # Try to run a non-existent file
    config = create_default_config("/path/to/nonexistent/script.py")
    with pytest.raises(FileNotFoundError):
        await run_script(
            config,
            on_stdout=lambda line: None,
            on_stderr=lambda line: None,
        )


@pytest.mark.unit
async def test_stop_script_terminates_process(tmp_path: Path) -> None:
    """Test that stop_script successfully terminates a running process.

    Verifies that long-running scripts can be stopped cleanly.
    """
    from parol_commander.services.script_runner import (
        run_script,
        stop_script,
        create_default_config,
    )

    # Write a long-running script
    script_path = tmp_path / "long_script.py"
    script_path.write_text(
        """
import time
for i in range(100):
    print(f"Iteration {i}")
    time.sleep(0.1)
"""
    )

    # Run the script
    config = create_default_config(str(script_path))
    handle = await run_script(
        config,
        on_stdout=lambda line: None,
        on_stderr=lambda line: None,
    )

    # Give it time to start
    import asyncio

    await asyncio.sleep(0.2)

    # Stop the script
    await stop_script(handle, timeout=2.0)

    # Assert that the process has terminated
    assert handle["proc"].returncode is not None, "Expected process to be terminated"


@pytest.mark.unit
def test_create_default_config() -> None:
    """Test that create_default_config returns a valid configuration.

    Verifies that default configuration has all required fields.
    """
    from parol_commander.services.script_runner import create_default_config

    config = create_default_config("/tmp/test.py")

    # Assert required fields are present
    assert "filename" in config
    assert "python_exe" in config
    assert config["filename"] == "/tmp/test.py"
    assert config["python_exe"] == sys.executable
