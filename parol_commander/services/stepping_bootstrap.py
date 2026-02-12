#!/usr/bin/env python3
"""
Bootstrap script for running user scripts with stepping wrapper.

This script is run as the main entry point when GUI-controlled stepping is enabled.
It patches parol6.RobotClient to wrap it with SteppingClientWrapper, then executes
the user's script.

Usage:
    python stepping_bootstrap.py <script_path>

Environment:
    PAROL_STEP_SESSION: Required. Session ID for IPC with GUI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    """Bootstrap and run user script with stepping wrapper."""
    if len(sys.argv) < 2:
        print("Usage: stepping_bootstrap.py <script_path>", file=sys.stderr)
        sys.exit(1)

    script_path = Path(sys.argv[1])
    if not script_path.exists():
        print(f"Script not found: {script_path}", file=sys.stderr)
        sys.exit(1)

    session_id = os.environ.get("PAROL_STEP_SESSION")
    if not session_id:
        print("PAROL_STEP_SESSION environment variable not set", file=sys.stderr)
        sys.exit(1)

    # Import and set up the stepping wrapper
    import importlib

    from parol_commander.services.stepping_client import (
        SteppingClientWrapper,
        StepIO,
    )

    step_io = StepIO(session_id)

    # Read backend package from environment (set by the GUI process)
    backend_package = os.environ.get("PAROL_BACKEND_PACKAGE", "parol6")

    # Import the backend and patch RobotClient
    try:
        backend = importlib.import_module(backend_package)
        OriginalRobotClient = backend.RobotClient

        # Store original for reference
        _original_robot_client = OriginalRobotClient

        # Create a factory that wraps the client
        class WrappedRobotClient:
            """RobotClient replacement that wraps with SteppingClientWrapper."""

            def __new__(cls, *args, **kwargs):
                # Create the original client
                original = _original_robot_client(*args, **kwargs)
                # Wrap it with stepping wrapper
                return SteppingClientWrapper(original, step_io)

        # Patch backend module
        setattr(backend, "RobotClient", WrappedRobotClient)
        if hasattr(backend, "client"):
            setattr(backend.client, "RobotClient", WrappedRobotClient)

        # Also patch sys.modules entries
        if backend_package in sys.modules:
            setattr(sys.modules[backend_package], "RobotClient", WrappedRobotClient)
        client_mod_name = f"{backend_package}.client"
        if client_mod_name in sys.modules:
            setattr(sys.modules[client_mod_name], "RobotClient", WrappedRobotClient)

    except ImportError as e:
        print(f"Failed to import {backend_package}: {e}", file=sys.stderr)
        sys.exit(1)

    # Prepare execution environment for the user script
    # Remove our bootstrap script from argv so the user script sees correct args
    sys.argv = [str(script_path)] + sys.argv[2:]

    # Set up globals for exec
    script_globals = {
        "__name__": "__main__",
        "__file__": str(script_path),
        "__builtins__": __builtins__,
    }

    # Read and execute the user's script
    script_code = script_path.read_text(encoding="utf-8")

    try:
        # Compile with the script's filename for proper tracebacks
        code = compile(script_code, str(script_path), "exec")
        exec(code, script_globals)
    except SystemExit:
        # Let SystemExit propagate (normal script termination)
        raise
    except Exception:
        # Re-raise to show traceback in user script
        raise


if __name__ == "__main__":
    main()
