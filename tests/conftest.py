"""Pytest configuration and shared fixtures for PAROL Web Commander tests."""

import os
import sys
from typing import Generator

import pytest

from parol6.client.manager import is_server_running
import subprocess


@pytest.fixture(scope="session", autouse=True)
def test_env_config() -> Generator[None, None, None]:
    """Configure environment variables for deterministic test behavior.

    Sets up fake serial and simulator modes so tests can run without hardware.
    These are only set if not already present in the environment.
    """
    env_defaults: dict[str, str] = {
        "PAROL6_FAKE_SERIAL": "1",  # Use fake serial for controller
        "PAROL_WEBAPP_REQUIRE_READY": "1",
        "PAROL_EXCLUSIVE_START": "1",
        # "PAROL_TRACE": "1",
        "PAROL_LOG_LEVEL": "DEBUG",
    }

    originals: dict[str, str | None] = {}
    for key, default_val in env_defaults.items():
        originals[key] = os.environ.get(key)
        if originals[key] is None:
            os.environ[key] = default_val

    try:
        yield
    finally:
        for key, original_val in originals.items():
            if original_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_val


@pytest.fixture
def robot_state():
    """Provide access to the shared RobotState instance.

    This exposes `parol_commander.state.robot_state` so tests can
    prime or inspect global robot state without importing main.py
    and triggering NiceGUI startup handlers a second time.
    """
    from parol_commander import state as state_module

    return state_module.robot_state


@pytest.fixture
def reset_robot_state(robot_state):
    """Reset robot_state to known defaults before each test.

    This ensures tests start with consistent state and don't interfere
    with each other through shared global state.
    """
    # Pre-test setup
    robot_state.angles = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    robot_state.pose = []
    robot_state.io = [0, 0, 0, 0, 1]  # ESTOP OK by default
    robot_state.gripper = [0, 0, 0, 0, 0, 0]
    robot_state.connected = False
    robot_state.simulator_active = False
    robot_state.x = 0.0
    robot_state.y = 0.0
    robot_state.z = 0.0
    robot_state.rx = 0.0
    robot_state.ry = 0.0
    robot_state.rz = 0.0
    robot_state.io_in1 = 0
    robot_state.io_in2 = 0
    robot_state.io_out1 = 0
    robot_state.io_out2 = 0
    robot_state.io_estop = 1

    yield

    # No special teardown; tests may override fields if needed.


@pytest.fixture(scope="session", autouse=True)
def kill_stale_controllers() -> Generator[None, None, None]:
    """Kill any existing controller processes before and after test session.

    Ensures no stale controllers from previous runs interfere with tests.
    """

    def _kill() -> None:
        try:
            if sys.platform.startswith("linux") or sys.platform == "darwin":
                subprocess.run(
                    ["pkill", "-f", "parol6.server.controller"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        except Exception:
            pass

    # Pre-session cleanup
    _kill()
    try:
        yield
    finally:
        # Post-session cleanup
        _kill()
        # Best-effort verification (non-fatal)
        try:
            from parol_commander.constants import CONTROLLER_HOST, CONTROLLER_PORT

            running = is_server_running(
                host=CONTROLLER_HOST, port=CONTROLLER_PORT, timeout=0.5
            )
            if running:
                _kill()
        except Exception:
            pass
