from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Callable, TypedDict


class ScriptRunConfig(TypedDict):
    """Configuration for running a Python script."""

    filename: str  # absolute path to saved script
    python_exe: str  # sys.executable path
    env: dict[str, str]  # extra environment variables; optional
    cwd: str  # working directory for the script; default project root


class ScriptProcessHandle(TypedDict):
    """Handle for a running script process."""

    proc: asyncio.subprocess.Process
    stdout_task: asyncio.Task
    stderr_task: asyncio.Task
    start_ts: float


async def _stream_output(
    stream: asyncio.StreamReader, callback: Callable[[str], None], prefix: str = ""
) -> None:
    """Read lines from stream and forward to callback with optional prefix."""
    try:
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="ignore").rstrip()
            if line:
                callback(f"{prefix}{line}")
    except Exception as e:
        logging.error("Stream reader error: %s", e)


async def run_script(
    cfg: ScriptRunConfig,
    on_stdout: Callable[[str], None],
    on_stderr: Callable[[str], None],
) -> ScriptProcessHandle:
    """
    Start a Python script as a subprocess and stream output to callbacks.

    Args:
        cfg: Configuration for the script run
        on_stdout: Callback for stdout lines
        on_stderr: Callback for stderr lines

    Returns:
        Handle for managing the process

    Raises:
        FileNotFoundError: If script file doesn't exist
        PermissionError: If Python executable not found/executable
        OSError: If process creation fails
    """
    # Validate configuration
    script_path = Path(cfg["filename"])
    if not script_path.exists():
        raise FileNotFoundError(f"Script file not found: {cfg['filename']}")

    if not script_path.suffix == ".py":
        raise ValueError(f"Script must be a .py file: {cfg['filename']}")

    python_exe = cfg["python_exe"]
    if not Path(python_exe).exists():
        raise FileNotFoundError(f"Python executable not found: {python_exe}")

    # Create the subprocess
    proc = await asyncio.create_subprocess_exec(
        python_exe,
        "-u",  # unbuffered output
        str(script_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cfg["cwd"],
        env={**cfg.get("env", {})},
    )

    # Start streaming tasks
    if proc.stdout:
        stdout_task = asyncio.create_task(_stream_output(proc.stdout, on_stdout))
    else:
        stdout_task = asyncio.create_task(asyncio.sleep(0))  # no-op task

    if proc.stderr:
        stderr_task = asyncio.create_task(
            _stream_output(proc.stderr, on_stderr, "[ERR] ")
        )
    else:
        stderr_task = asyncio.create_task(asyncio.sleep(0))  # no-op task

    handle: ScriptProcessHandle = {
        "proc": proc,
        "stdout_task": stdout_task,
        "stderr_task": stderr_task,
        "start_ts": time.time(),
    }

    logging.info("Started script process: %s (PID: %s)", cfg["filename"], proc.pid)
    return handle


async def stop_script(handle: ScriptProcessHandle, timeout: float = 2.0) -> None:
    """
    Stop a running script process gracefully.

    Args:
        handle: Process handle from run_script
        timeout: Seconds to wait for graceful termination before force kill
    """
    proc = handle["proc"]

    if proc.returncode is not None:
        logging.info("Script process already terminated (code: %s)", proc.returncode)
        return

    try:
        # Graceful termination
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            logging.info("Script process terminated gracefully")
        except asyncio.TimeoutError:
            # Force kill if graceful termination failed
            proc.kill()
            await proc.wait()
            logging.warning("Script process force-killed after timeout")

    except ProcessLookupError:
        # Process already dead
        pass
    except Exception as e:
        logging.error("Error stopping script process: %s", e)

    # Cancel streaming tasks
    for task in [handle["stdout_task"], handle["stderr_task"]]:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logging.error("Error canceling stream task: %s", e)


def create_default_config(filename: str, cwd: str | None = None) -> ScriptRunConfig:
    """Create a default script configuration."""
    return {
        "filename": filename,
        "python_exe": sys.executable,
        "env": {},
        "cwd": cwd or str(Path.cwd()),
    }
