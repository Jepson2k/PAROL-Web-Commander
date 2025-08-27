from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.constants import CONTROLLER_PATH


@dataclass
class ServerOptions:
    """Options for launching the headless controller."""

    com_port: str | None = None
    no_autohome: bool = True  # Set PAROL6_NOAUTOHOME=1 by default
    extra_env: dict | None = None


class ServerManager:
    """
    Manages the lifecycle of the headless PAROL6 controller (headless_commander.py).

    - Writes com_port.txt in the controller working directory on Windows to preselect the port.
    - Spawns the controller as a subprocess using sys.executable.
    - Provides stop and liveness checks.
    """

    def __init__(self, controller_path: str) -> None:
        self.controller_path = Path(controller_path).resolve()
        if not self.controller_path.exists():
            raise FileNotFoundError(f"Controller script not found: {self.controller_path}")
        self._proc: subprocess.Popen | None = None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc and self._proc.poll() is None else None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _write_com_port_hint(self, com_port: str) -> None:
        """
        The current headless_commander.py reads com_port.txt on Windows at startup.
        To keep behavior consistent across OSes, we write it unconditionally before launch.
        """
        cwd = self.controller_path.parent
        hint = cwd / "com_port.txt"
        try:
            hint.write_text(com_port.strip() + "\n", encoding="utf-8")
        except Exception as e:
            # Non-fatal: controller can still prompt or auto-detect depending on OS
            logging.warning("ServerManager: failed to write %s: %s", hint, e)

    async def start_controller(
        self, com_port: str | None = None, opts: ServerOptions | None = None
    ) -> None:
        """Start the controller if not already running."""
        if self.is_running():
            return

        options = opts or ServerOptions(com_port=com_port)

        # Working directory should be the controller's folder to keep relative paths (e.g., com_port.txt) consistent
        cwd = self.controller_path.parent

        # Optional COM port preseed (esp. for Windows flow in headless_commander)
        if options.com_port:
            self._write_com_port_hint(options.com_port)

        env = os.environ.copy()
        # Disable autohome unless explicitly overridden
        if options.no_autohome:
            env["PAROL6_NOAUTOHOME"] = "1"
        if options.extra_env:
            env.update(options.extra_env)

        # Unbuffered output for better logging
        env.setdefault("PYTHONUNBUFFERED", "1")

        # Launch the controller
        args = [sys.executable, "-u", str(self.controller_path)]
        try:
            self._proc = subprocess.Popen(
                args,
                cwd=str(cwd),
                env=env,
                stdout=None,  # inherit so controller logs are visible in terminal for debugging
                stderr=None,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to start controller: {e}") from e

        # Give it a brief moment to initialize
        await asyncio.sleep(0.2)

    async def stop_controller(self, timeout: float = 5.0) -> None:
        """Stop the controller process if running."""
        if not self.is_running():
            self._proc = None
            return

        proc = self._proc
        assert proc is not None

        try:
            if os.name == "nt":
                proc.terminate()
            else:
                proc.send_signal(signal.SIGTERM)
        except Exception:
            # Fall back to kill below
            pass

        # Wait for graceful exit
        t0 = time.time()
        while proc.poll() is None and (time.time() - t0) < timeout:
            await asyncio.sleep(0.1)

        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.kill()

        self._proc = None


# Module-level singleton instance
server_manager = ServerManager(controller_path=CONTROLLER_PATH)
