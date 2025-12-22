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
    from parol_commander.services.stepping_client import (
        SteppingClientWrapper,
        StepIO,
    )

    step_io = StepIO(session_id)

    # Import parol6 and patch RobotClient
    try:
        import parol6
        from parol6 import RobotClient as OriginalRobotClient

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

        # Patch parol6 module
        parol6.RobotClient = WrappedRobotClient
        if hasattr(parol6, "client"):
            parol6.client.RobotClient = WrappedRobotClient

        # Also patch sys.modules entries
        if "parol6" in sys.modules:
            sys.modules["parol6"].RobotClient = WrappedRobotClient  # type: ignore[attr-defined]
        if "parol6.client" in sys.modules:
            sys.modules["parol6.client"].RobotClient = WrappedRobotClient  # type: ignore[attr-defined]

    except ImportError as e:
        print(f"Failed to import parol6: {e}", file=sys.stderr)
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
