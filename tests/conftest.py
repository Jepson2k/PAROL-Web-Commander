"""Pytest configuration and shared fixtures for PAROL Web Commander tests."""

import logging
import os
import random
import subprocess
import sys
import asyncio
from collections.abc import Generator
from typing import TYPE_CHECKING

import numpy as np
import pytest
from nicegui import run as nicegui_run
from nicegui.testing import general as nicegui_testing_general
from nicegui.testing.general_fixtures import (
    nicegui_reset_globals,  # noqa: F401 - required by screen fixture
)
from nicegui.testing.screen import Screen
from nicegui.testing.screen_plugin import (
    capabilities,  # noqa: F401
    nicegui_driver,  # noqa: F401 - default driver (per-test browser)
    nicegui_remove_all_screenshots,  # noqa: F401 - clears screenshots before session
    pytest_runtest_makereport,  # noqa: F401
    screen,  # noqa: F401 - default screen fixture (creates browser per test)
)
from parol6.client.manager import is_server_running
from parol6.config import HOME_ANGLES_DEG
from selenium import webdriver as _webdriver

if TYPE_CHECKING:
    from parol6 import AsyncRobotClient, ServerManager

# ============================================================================
# Skip marker for WebGL-dependent tests on macOS CI
# ============================================================================
# SwiftShader WebGL fails with "BindToCurrentSequence failed" on macOS runners
skip_webgl_macos_ci = pytest.mark.skipif(
    sys.platform == "darwin" and "GITHUB_ACTIONS" in os.environ,
    reason="WebGL context creation fails on macOS CI with SwiftShader",
)

# ============================================================================
# Port Configuration (session-randomized to avoid conflicts)
# ============================================================================
# Generate unique ports per test session to avoid conflicts between test runs
_SESSION_PORT_BASE = random.randint(10000, 50000)
CONTROLLER_PORT = _SESSION_PORT_BASE
MULTICAST_PORT = _SESSION_PORT_BASE + 1


def _get_test_ports() -> tuple[int, int]:
    """Get the session-unique ports for controller and multicast."""
    return CONTROLLER_PORT, MULTICAST_PORT


@pytest.fixture
def chrome_options():
    """Base Chrome options required by nicegui screen_plugin."""
    return _webdriver.ChromeOptions()


@pytest.fixture
def nicegui_chrome_options():
    """Chrome options fixture for local nicegui's screen_plugin.

    The local nicegui/ checkout expects this fixture name.
    """
    options = _webdriver.ChromeOptions()
    if not os.environ.get("HEADED"):
        options.add_argument("--headless")
    options.add_argument("disable-search-engine-choice-screen")
    options.add_argument("headless")
    # Use SwiftShader for software WebGL in CI, hardware GL locally
    if "GITHUB_ACTIONS" in os.environ:
        options.add_argument("--use-gl=angle")
        options.add_argument("--use-angle=swiftshader-webgl")
    else:
        options.add_argument("--use-gl=angle")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return options


# Window size for screen tests - full HD for proper layout
TEST_WINDOW_WIDTH = 1920
TEST_WINDOW_HEIGHT = 1080


@pytest.fixture(autouse=True)
def set_screen_window_size(
    request: pytest.FixtureRequest,
) -> None:
    """Set browser window size and page load timeout for screen tests.

    This ensures consistent layout across all browser tests.
    Only runs when a test actually uses the screen fixture.
    """
    if "screen" not in request.fixturenames:
        return
    screen_fixture: Screen = request.getfixturevalue("screen")
    screen_fixture.selenium.set_window_size(TEST_WINDOW_WIDTH, TEST_WINDOW_HEIGHT)
    screen_fixture.selenium.set_page_load_timeout(
        8
    )  # WebGL needs more time on slow runners


@pytest.fixture(scope="session", autouse=True)
def silence_noisy_logging():
    """Reduce verbosity of noisy third-party loggers.

    Selenium debug output includes base64-encoded screenshots.
    Numba debug output floods with SSA/IR details during JIT compilation.
    """
    logging.getLogger("selenium").setLevel(logging.INFO)
    logging.getLogger("selenium.webdriver").setLevel(logging.INFO)
    logging.getLogger("selenium.webdriver.remote").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)
    # Silence numba JIT compilation debug spam
    logging.getLogger("numba").setLevel(logging.WARNING)
    logging.getLogger("numba.core").setLevel(logging.WARNING)
    # Silence toppra debug output
    logging.getLogger("toppra").setLevel(logging.WARNING)
    yield


# ============================================================================
# Class-scoped Browser Fixture for Expensive Browser Tests
# ============================================================================


@pytest.fixture(scope="class")
def class_driver(
    request: pytest.FixtureRequest,
) -> Generator[_webdriver.Chrome, None, None]:
    """Class-scoped Chrome webdriver for shared browser tests.

    Creates a single browser instance that persists across all tests in a class.
    CSS animations are disabled for deterministic testing.
    """
    from selenium.webdriver.chrome.service import Service
    import shutil

    options = _webdriver.ChromeOptions()
    if not os.environ.get("HEADED"):
        options.add_argument("headless")
    options.add_argument("disable-search-engine-choice-screen")
    # Use SwiftShader for software WebGL in CI, hardware GL locally
    if "GITHUB_ACTIONS" in os.environ:
        options.add_argument("--use-gl=angle")
        options.add_argument("--use-angle=swiftshader-webgl")
    else:
        options.add_argument("--use-gl=angle")
    options.add_argument("no-sandbox")
    options.add_argument("disable-dev-shm-usage")
    # Disable CSS animations for deterministic testing
    options.add_argument("--disable-animations")

    # Find system chromedriver (same as NiceGUI's approach)
    chromedriver_path = shutil.which("chromedriver")
    if chromedriver_path:
        service = Service(executable_path=chromedriver_path)
        driver = _webdriver.Chrome(service=service, options=options)
    else:
        driver = _webdriver.Chrome(options=options)

    driver.set_window_size(TEST_WINDOW_WIDTH, TEST_WINDOW_HEIGHT)
    driver.implicitly_wait(0)

    yield driver

    driver.quit()


class _StubCaplog:
    """Minimal caplog stub for class-scoped screen fixture."""

    def __init__(self):
        self.records = []

    def clear(self):
        self.records = []


@pytest.fixture(scope="class")
def class_screen(
    request: pytest.FixtureRequest,
    class_driver: _webdriver.Chrome,
) -> Generator["Screen", None, None]:
    """Browser session shared across all tests in a class.

    Use for expensive browser tests that don't need isolation between tests.
    The browser navigates to the app once at class setup and stays open.

    Usage:
        @pytest.mark.browser
        class TestPanelOperations:
            def test_first(self, class_screen):
                # Uses shared browser session
                ...

            def test_second(self, class_screen):
                # Same browser session, state persists from test_first
                ...
    """
    # Set the port env var that NiceGUI's ui.run() expects for screen tests
    os.environ["NICEGUI_SCREEN_TEST_PORT"] = str(Screen.PORT)

    try:
        # Reset NiceGUI globals at class setup (isolation between classes)
        with nicegui_testing_general.nicegui_reset_globals():
            # Create Screen wrapper with class-scoped driver (stub caplog since we share session)
            screen_instance = Screen(class_driver, _StubCaplog(), request)  # type: ignore[arg-type]

            # Set storage keys to bypass first-time dialogs before opening app
            from nicegui import app as ng_app

            from parol_commander.components.help_menu import HelpMenu

            ng_app.storage.general[HelpMenu.FIRST_VISIT_KEY] = True
            ng_app.storage.general[HelpMenu.SAFETY_ACKNOWLEDGED_KEY] = True

            # Navigate to app once for all tests in class
            # CI with SwiftShader needs more time for WebGL initialization
            screen_instance.open("/", timeout=30.0)

            yield screen_instance

            # Stop server before exiting context
            screen_instance.stop_server()
        # NiceGUI globals reset on context exit (class teardown)
        # Re-setup process pool since nicegui_reset_globals calls run.reset()
        nicegui_run.setup()
    finally:
        os.environ.pop("NICEGUI_SCREEN_TEST_PORT", None)


@pytest.fixture(autouse=True)
def restore_process_pool_after_nicegui_fixtures(
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """Re-setup process pool after tests using NiceGUI's user or screen fixtures.

    These fixtures use nicegui_reset_globals which calls run.reset(),
    clearing the process pool. This fixture re-sets it up after such tests.
    """
    yield
    # Re-setup if this test used user or screen fixture (not class_screen, which handles it)
    uses_nicegui_fixture = "user" in request.fixturenames or (
        "screen" in request.fixturenames and "class_screen" not in request.fixturenames
    )
    if uses_nicegui_fixture:
        nicegui_run.setup()


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "browser: marks tests that require a real browser (via Selenium)"
    )


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
def test_env_config() -> Generator[None, None, None]:
    """Configure environment variables for deterministic test behavior.

    Sets up fake serial and simulator modes so tests can run without hardware.
    These are only set if not already present in the environment.
    """
    from pathlib import Path

    # Pre-create .nicegui storage directory and file. NiceGUI's user/screen fixtures
    # don't create this - they rely on it existing. NiceGUI persists storage
    # asynchronously via ThreadPoolExecutor, which logs ERROR if the file is missing.
    # This prevents spurious ERROR logs that would fail tests using the screen fixture.
    nicegui_storage_dir = Path(".nicegui")
    nicegui_storage_dir.mkdir(exist_ok=True)
    storage_file = nicegui_storage_dir / "storage-general.json"
    if not storage_file.exists():
        storage_file.write_text("{}")

    controller_port, multicast_port = _get_test_ports()
    env_defaults: dict[str, str] = {
        "PAROL6_FAKE_SERIAL": "1",  # Use fake serial for controller
        "PAROL_WEBAPP_REQUIRE_READY": "1",
        "PAROL_EXCLUSIVE_START": "0",  # Allow reusing session-scoped controller
        "PAROL_LOG_LEVEL": "DEBUG",
        # Connect webapp to the session-randomized controller port
        "PAROL_CONTROLLER_PORT": str(controller_port),
        "PAROL6_CONTROLLER_PORT": str(controller_port),
        "PAROL6_STATUS_MULTICAST_PORT": str(multicast_port),
        # Skip slow envelope generation by default (tests that need it enable explicitly)
        "PAROL_SKIP_ENVELOPE": "1",
        # Reduce status broadcast rate for tests (50Hz is for human-perceived real-time,
        # 20Hz is sufficient for automated tests and reduces CI load)
        "PAROL6_STATUS_RATE_HZ": "20",
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


@pytest.fixture(autouse=True)
def reset_state(request: pytest.FixtureRequest):
    """Reset all shared state between tests for isolation.

    Skips reset for class_screen tests since the app persists across tests.
    This unified fixture replaces individual reset_* fixtures in test classes.
    """
    # Don't reset state for class_screen tests - app persists across tests
    if "class_screen" in request.fixturenames:
        yield
        return

    from nicegui import app as ng_app

    from parol_commander import state as state_module
    from parol_commander.components.help_menu import HelpMenu
    from parol_commander.state import readiness_state

    # Mark first visit and safety as acknowledged so dialogs don't appear
    ng_app.storage.general[HelpMenu.FIRST_VISIT_KEY] = True
    ng_app.storage.general[HelpMenu.SAFETY_ACKNOWLEDGED_KEY] = True

    # Reset readiness events
    readiness_state.reset()

    # Reset robot state - use proper array types
    state_module.robot_state.angles.set_deg(np.array(HOME_ANGLES_DEG, dtype=np.float64))
    state_module.robot_state.orientation.set_deg(np.zeros(3, dtype=np.float64))
    state_module.robot_state.pose = np.zeros(16, dtype=np.float64)
    state_module.robot_state.io = np.array([0, 0, 0, 0, 1], dtype=np.int32)  # ESTOP OK
    state_module.robot_state.gripper = np.zeros(6, dtype=np.int32)
    state_module.robot_state.connected = False
    state_module.robot_state.x = 0.0
    state_module.robot_state.y = 0.0
    state_module.robot_state.z = 0.0
    state_module.robot_state.rx = 0.0
    state_module.robot_state.ry = 0.0
    state_module.robot_state.rz = 0.0
    state_module.robot_state.io_in1 = 0
    state_module.robot_state.io_in2 = 0
    state_module.robot_state.io_out1 = 0
    state_module.robot_state.io_out2 = 0
    state_module.robot_state.io_estop = 1
    state_module.robot_state.joint_en = np.ones(12, dtype=np.int32)
    state_module.robot_state.cart_en_wrf = np.ones(12, dtype=np.int32)
    state_module.robot_state.cart_en_trf = np.ones(12, dtype=np.int32)
    state_module.robot_state.last_update_ts = 0.0
    state_module.robot_state.action_state = ""
    state_module.robot_state.action_current = ""

    # Reset simulation state
    state_module.simulation_state.targets.clear()
    state_module.simulation_state.path_segments.clear()
    state_module.simulation_state.current_step_index = 0
    state_module.simulation_state.total_steps = 0
    state_module.simulation_state.is_playing = False
    state_module.simulation_state.playback_speed = 1.0
    state_module.simulation_state.preview_mode = False
    state_module.simulation_state.paths_visible = True
    state_module.simulation_state.envelope_visible = False
    state_module.simulation_state.envelope_mode = "auto"

    # Reset recording state
    state_module.recording_state.is_recording = False

    # Reset editor/UI state
    state_module.editor_tabs_state.tabs = []
    state_module.editor_tabs_state.active_tab_id = None
    state_module.ui_state.urdf_scene = None

    yield


@pytest.fixture(scope="session", autouse=True)
def kill_stale_controllers() -> Generator[None, None, None]:
    """Kill any existing controller processes before and after test session.

    Ensures no stale controllers from previous runs interfere with tests.
    """
    controller_port, _ = _get_test_ports()

    def _kill() -> None:
        try:
            if sys.platform.startswith("linux") or sys.platform == "darwin":
                # Kill all controller processes
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
            from parol_commander.constants import config

            running = is_server_running(
                host=config.controller_host, port=controller_port, timeout=0.5
            )
            if running:
                _kill()
        except Exception:
            pass


@pytest.fixture(scope="session", autouse=True)
def session_controller(
    test_env_config: None,
    kill_stale_controllers: None,
    warmup_jit_cache: None,
) -> Generator["ServerManager", None, None]:
    """Session-scoped controller shared by all tests.

    Starts the controller once per test session and keeps it running.
    The app's start_controller() will detect it and reuse it (via PAROL_EXCLUSIVE_START=0).
    This saves ~4 seconds per test (2s start + 2s stop).
    """
    from parol6 import manage_server

    controller_port, multicast_port = _get_test_ports()

    # Start controller once for entire session
    server_manager = manage_server(
        host="127.0.0.1",
        port=controller_port,
        com_port=None,
        normalize_logs=True,
        extra_env={"PAROL6_STATUS_MULTICAST_PORT": str(multicast_port)},
    )

    try:
        yield server_manager
    finally:
        server_manager.stop_controller()


@pytest.fixture(scope="session", autouse=True)
def warmup_jit_cache(silence_noisy_logging: None) -> None:
    """Pre-warm numba JIT cache before controller starts.

    Without cache, JIT compilation takes 20+ seconds which exceeds the 10s
    controller startup timeout. By warming up first, we populate the cache
    so the controller's warmup is fast.
    """
    from parol6.utils.warmup import warmup_jit

    warmup_jit()


@pytest.fixture(scope="session", autouse=True)
def session_client(
    session_controller: "ServerManager",
) -> Generator["AsyncRobotClient", None, None]:
    """Session-scoped async client connected to the session controller.

    Performs initial setup (simulator_on, stream_on, enable) once per session.
    The controller_reset fixture can be used for per-test reset if needed.
    """
    from parol6 import AsyncRobotClient

    controller_port, _ = _get_test_ports()
    # Use longer timeout for CI environments where scheduling can cause delays
    client = AsyncRobotClient(host="127.0.0.1", port=controller_port, timeout=5.0)

    # Initial setup - wait for controller and enable simulator
    async def setup():
        await client.wait_for_server_ready(timeout=10.0)
        await client.simulator_on()
        await client.stream_on()
        await client.enable()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(setup())
    finally:
        loop.close()

    try:
        yield client
    finally:
        # Use asyncio.run() for cleaner event loop handling during teardown
        # This avoids issues with new_event_loop() during interpreter shutdown on Python 3.14
        try:
            asyncio.run(client.close())
        except RuntimeError:
            # Event loop may already be closed or unavailable during shutdown
            pass


@pytest.fixture(autouse=True)
async def controller_reset(
    request: pytest.FixtureRequest,
    session_controller: "ServerManager",
):
    """Per-test fixture that resets the shared controller state.

    Runs automatically before each test that uses user or screen fixtures.
    Much faster than full controller restart (~0.001s vs ~4s).

    Note: class_screen tests share browser state across all tests in the class,
    so we only reset/home once when the class_screen fixture is set up.
    """
    from parol6 import AsyncRobotClient

    # Skip reset for class_screen tests - they share state across tests in a class
    # The controller is reset once when the class_screen fixture sets up
    if "class_screen" in request.fixturenames:
        yield
        return

    # Only reset for tests that use NiceGUI app (user or screen fixture)
    if "user" in request.fixturenames or "screen" in request.fixturenames:
        controller_port, _ = _get_test_ports()
        # Create a fresh client on this test's event loop
        # Use longer timeout for CI environments where scheduling can cause delays
        async with AsyncRobotClient(
            host="127.0.0.1", port=controller_port, timeout=5.0
        ) as client:
            await client.reset()
            await client.enable()
            # Home the robot to ensure valid joint angles (0.0 is invalid for some joints)
            # Use short timeouts since simulator homing is instant
            await client.home(
                wait=True,
                timeout=10.0,
                motion_start_timeout=0.5,  # Allow more time for motion to start on slow CI
                settle_window=0.05,  # Robot settles instantly in simulator
            )

    yield


@pytest.fixture
def enable_envelope() -> Generator[None, None, None]:
    """Enable envelope generation for tests that specifically need it.

    By default, PAROL_SKIP_ENVELOPE=1 is set to speed up tests.
    Use this fixture for tests that verify envelope functionality.
    """
    original = os.environ.pop("PAROL_SKIP_ENVELOPE", None)
    yield
    if original is not None:
        os.environ["PAROL_SKIP_ENVELOPE"] = original
    else:
        os.environ["PAROL_SKIP_ENVELOPE"] = "1"
