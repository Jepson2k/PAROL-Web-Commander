"""Pytest configuration and shared fixtures for PAROL Web Commander tests."""

import logging
import os
import shutil
import sys
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from parol6.client.manager import is_server_running
from parol6.config import HOME_ANGLES_DEG
import subprocess
from nicegui import run as nicegui_run

if TYPE_CHECKING:
    from selenium import webdriver
    from nicegui.testing.screen import Screen


# ============================================================================
# Browser Test (Screen Plugin) Configuration
# ============================================================================

# Conditionally import screen plugin fixtures for browser tests
# This allows browser tests to run with the 'screen' fixture when selenium is available
SELENIUM_AVAILABLE = False
SCREEN_PORT = 3392  # Must match nicegui.testing.screen.Screen.PORT
try:
    from selenium import webdriver as _webdriver

    # Import general fixtures first (provides nicegui_reset_globals)
    from nicegui.testing.general_fixtures import (
        nicegui_reset_globals,  # noqa: F401 - required by screen fixture
    )

    # Import screen plugin fixtures - these are needed for browser tests
    from nicegui.testing.screen_plugin import (
        screen,  # noqa: F401 - fixture for browser tests
        nicegui_chrome_options,  # noqa: F401
        nicegui_driver,  # noqa: F401
        nicegui_remove_all_screenshots,  # noqa: F401
        pytest_runtest_makereport,  # noqa: F401
        capabilities,  # noqa: F401
    )
    from nicegui.testing.screen import Screen

    SCREEN_PORT = Screen.PORT
    SELENIUM_AVAILABLE = True

    @pytest.fixture
    def chrome_options() -> _webdriver.ChromeOptions:
        """Provide base Chrome options (required by nicegui_chrome_options)."""
        return _webdriver.ChromeOptions()

    @pytest.fixture(scope="session", autouse=True)
    def silence_selenium_logging():
        """Reduce Selenium/urllib3/webdriver logging verbosity.

        This prevents excessive debug output that can cause browser tests
        to freeze and produce too much output.
        """
        logging.getLogger("selenium").setLevel(logging.WARNING)
        logging.getLogger("selenium.webdriver").setLevel(logging.WARNING)
        logging.getLogger("selenium.webdriver.remote").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
        yield

    # Note: We import capabilities from screen_plugin above, but override it here
    # to reduce browser console logging from ALL to SEVERE only
    @pytest.fixture
    def capabilities(capabilities: dict) -> dict:  # type: ignore[no-redef]
        """Override browser logging to capture only SEVERE errors."""
        capabilities["goog:loggingPrefs"] = {"browser": "SEVERE"}
        return capabilities

except ImportError:
    # selenium not installed - browser tests will be skipped
    pass


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "browser: marks tests that require a real browser (via Selenium)"
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselected by default with -m 'not slow')",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip browser tests if selenium is not available, and set screen port."""
    if not SELENIUM_AVAILABLE:
        skip_browser = pytest.mark.skip(reason="selenium not installed")
        for item in items:
            if "browser" in item.keywords:
                item.add_marker(skip_browser)
    else:
        # Set PAROL_SERVER_PORT to match the Screen plugin's port for browser tests
        # This must happen before the app module is imported
        os.environ["PAROL_SERVER_PORT"] = str(SCREEN_PORT)


def _get_worker_ports(worker_id: str) -> tuple[int, int, int]:
    """Compute unique ports for a pytest-xdist worker.

    Args:
        worker_id: The xdist worker ID (e.g., "master", "gw0", "gw1", ...).

    Returns:
        Tuple of (controller_port, server_port, mcast_port) unique to this worker.
    """
    base_controller = 5001
    base_server = 8080
    base_mcast = 50510

    if worker_id == "master" or not worker_id:
        # Not running in parallel, use defaults
        return base_controller, base_server, base_mcast

    # worker_id is like "gw0", "gw1", etc.
    try:
        worker_num = int(worker_id.replace("gw", ""))
    except ValueError:
        return base_controller, base_server, base_mcast

    # Offset ports by worker number (e.g., gw0 -> 5001/8080/50510, gw1 -> 5011/8090/50520, ...)
    return (
        base_controller + (worker_num * 10),
        base_server + (worker_num * 10),
        base_mcast + (worker_num * 10),
    )


@pytest.fixture(scope="session")
def worker_id(request: pytest.FixtureRequest) -> str:
    """Return the xdist worker ID, or 'master' if not running in parallel."""
    # pytest-xdist sets this; falls back to "master" for non-parallel runs
    return getattr(request.config, "workerinput", {}).get("workerid", "master")


@pytest.fixture(scope="session", autouse=True)
def isolate_ports_for_parallel(worker_id: str) -> Generator[None, None, None]:
    """Set unique ports for each xdist worker to avoid collisions.

    This must run before test_env_config to ensure ports are set before
    the app reads them. Includes the multicast port for STATUS isolation.
    """
    controller_port, server_port, mcast_port = _get_worker_ports(worker_id)

    # Store original values
    orig_controller = os.environ.get("PAROL_CONTROLLER_PORT")
    orig_server = os.environ.get("PAROL_SERVER_PORT")
    orig_mcast = os.environ.get("PAROL6_MCAST_PORT")

    # Set worker-specific ports BEFORE any parol6 imports
    os.environ["PAROL_CONTROLLER_PORT"] = str(controller_port)
    os.environ["PAROL_SERVER_PORT"] = str(server_port)
    os.environ["PAROL6_MCAST_PORT"] = str(mcast_port)

    # Force parol6.config to re-read the env vars by reloading the module
    # This is necessary because config values are cached at import time
    import importlib

    try:
        import parol6.config as cfg_module

        importlib.reload(cfg_module)
    except ImportError:
        pass  # Module not imported yet, will pick up env vars on first import

    try:
        yield
    finally:
        # Restore original values
        if orig_controller is None:
            os.environ.pop("PAROL_CONTROLLER_PORT", None)
        else:
            os.environ["PAROL_CONTROLLER_PORT"] = orig_controller

        if orig_server is None:
            os.environ.pop("PAROL_SERVER_PORT", None)
        else:
            os.environ["PAROL_SERVER_PORT"] = orig_server

        if orig_mcast is None:
            os.environ.pop("PAROL6_MCAST_PORT", None)
        else:
            os.environ["PAROL6_MCAST_PORT"] = orig_mcast


@pytest.fixture(scope="session", autouse=True)
def setup_nicegui_process_pool() -> Generator[None, None, None]:
    """Enable NiceGUI's process pool for cpu_bound() calls in tests.

    This allows tests to use `run.cpu_bound()` for subprocess isolation,
    matching production behavior for path visualization simulations.
    """
    nicegui_run.setup()
    yield
    nicegui_run.reset()


@pytest.fixture(scope="session", autouse=True)
def test_env_config(isolate_ports_for_parallel: None) -> Generator[None, None, None]:
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
    # Pre-test setup - use HOME_ANGLES_DEG which is within all joint limits
    # and matches the simulator's standby position
    robot_state.angles = list(HOME_ANGLES_DEG)
    robot_state.pose = []
    robot_state.io = [0, 0, 0, 0, 1]  # ESTOP OK by default
    robot_state.gripper = [0, 0, 0, 0, 0, 0]
    robot_state.connected = False
    # Note: Do NOT reset simulator_active here - let app startup control this.
    # Resetting it causes race conditions with startup auto-enable.
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
    # Movement enablement arrays (all enabled by default)
    robot_state.joint_en = [1] * 12
    robot_state.cart_en_wrf = [1] * 12
    robot_state.cart_en_trf = [1] * 12
    # Reset timestamps and status fields so tests can detect fresh updates
    robot_state.last_update_ts = 0.0
    robot_state.action_state = ""
    robot_state.action_current = ""

    yield

    # No special teardown; tests may override fields if needed.


@pytest.fixture(autouse=True)
def reset_readiness_state():
    """Reset readiness events between tests for isolation.

    This ensures each test starts with fresh asyncio.Event objects
    so that readiness signals from previous tests don't affect subsequent tests.
    """
    from parol_commander.state import readiness_state

    readiness_state.reset()
    yield
    # Note: Do NOT reset after test - events that were legitimately set during
    # test execution should remain set for any cleanup/teardown that needs them.


@pytest.fixture(scope="session", autouse=True)
def kill_stale_controllers(
    worker_id: str, isolate_ports_for_parallel: None
) -> Generator[None, None, None]:
    """Kill any existing controller processes before and after test session.

    Ensures no stale controllers from previous runs interfere with tests.
    When running in parallel, only kills controllers on this worker's port.
    """
    controller_port, _, _ = _get_worker_ports(worker_id)

    def _kill() -> None:
        try:
            if sys.platform.startswith("linux") or sys.platform == "darwin":
                if worker_id == "master" or not worker_id.startswith("gw"):
                    # Non-parallel: kill all controller processes
                    subprocess.run(
                        ["pkill", "-f", "parol6.server.controller"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    # Parallel: only kill controllers listening on our port
                    # Use fuser to find and kill processes on specific port
                    subprocess.run(
                        ["fuser", "-k", f"{controller_port}/udp"],
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
            from parol_commander.constants import CONTROLLER_HOST

            running = is_server_running(
                host=CONTROLLER_HOST, port=controller_port, timeout=0.5
            )
            if running:
                _kill()
        except Exception:
            pass
