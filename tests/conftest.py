from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

pytest_plugins = ["nicegui.testing.user_plugin"]


if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="module")
def headless_server() -> Iterator[subprocess.Popen]:
    """
    Spawn the headless server in a subprocess with:
      - cwd = PAROL6-python-API
      - env: PAROL6_NOAUTOHOME=1, PAROL_LOG_LEVEL=WARNING
    Ensure proper cleanup.
    """
    repo_root = Path(__file__).resolve().parent.parent

    candidates = [
        repo_root
        / "external"
        / "PAROL6-python-API"
        / "parol6"
        / "server"
        / "controller.py",
        repo_root
        / "external"
        / "PAROL6-python-API"
        / "parol6"
        / "server"
        / "headless_commander.py",
        repo_root / "PAROL6-python-API" / "parol6" / "server" / "controller.py",
        repo_root / "PAROL6-python-API" / "parol6" / "server" / "headless_commander.py",
    ]
    server_script = next((p for p in candidates if p.exists()), None)
    assert server_script is not None, (
        "Missing headless server. Checked paths:\n"
        + "\n".join(str(p) for p in candidates)
    )

    env = os.environ.copy()
    env["PAROL6_NOAUTOHOME"] = "1"
    env["PAROL_LOG_LEVEL"] = "WARNING"
    # Enable hardware-free simulation so IK commands can run end-to-end at 100 Hz
    env["PAROL6_FAKE_SERIAL"] = "1"
    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        cwd=str(server_script.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1,
    )

    # Give it a moment to bind sockets
    time.sleep(1.5)

    try:
        yield proc
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass


@pytest.fixture(scope="session", autouse=True)
def webapp_env_session() -> None:
    """
    Global test defaults for the NiceGUI webapp (set at session start via os.environ):
      - Disable auto simulator toggle at startup (webapp should not call SIMULATOR|ON)
      - Disable readiness/stream_on waits in tests (prevents timeouts when server not running yet)
    These can still be overridden per-test with monkeypatch.setenv if needed.
    """
    os.environ["PAROL_WEBAPP_AUTO_SIMULATOR"] = "0"
    os.environ["PAROL_WEBAPP_REQUIRE_READY"] = "0"
