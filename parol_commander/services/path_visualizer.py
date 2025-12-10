"""
Path visualization service for robot program simulation.

Runs dry-run simulations in isolated subprocesses for safety and non-blocking
execution. Results are collected and applied to SimulationState in the main process.
"""

import asyncio
import logging
import traceback
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

from nicegui import run

from parol_commander.state import (
    simulation_state,
    PathSegment,
    ProgramTarget,
    ui_state,
    robot_state,
    editor_tabs_state,
)
from parol_commander.common.logging_config import TRACE_ENABLED

logger = logging.getLogger(__name__)

# Configuration constants
MAX_PATH_SEGMENTS = 10000
SIMULATION_TIMEOUT_S = 5.0


# Color constants for different move types (CVD-aware palette)
MOVE_TYPE_COLORS = {
    "cartesian": "#2faf7a",  # Green - Cartesian/linear moves
    "move_cartesian": "#2faf7a",
    "joints": "#4a63e0",  # Blue - Joint moves
    "move_joints": "#4a63e0",
    "pose": "#e67e22",  # Orange - Pose/point targets
    "move_pose": "#e67e22",
    "smooth": "#9b59b6",  # Purple - Smooth/interpolated moves
    "smooth_cartesian": "#9b59b6",
    "invalid": "#e74c3c",  # Red - Invalid/error moves
    "unknown": "#95a5a6",  # Gray - Unknown move types
}


def get_color_for_move_type(move_type: str, is_valid: bool = True) -> str:
    """
    Get the visualization color for a given move type.

    Args:
        move_type: The type of move (e.g., "cartesian", "joints", "smooth")
        is_valid: Whether the move is valid (invalid moves are always red)

    Returns:
        Hex color string for the move type
    """
    if not is_valid:
        return MOVE_TYPE_COLORS["invalid"]

    move_type_lower = move_type.lower() if move_type else ""
    return MOVE_TYPE_COLORS.get(move_type_lower, MOVE_TYPE_COLORS["unknown"])


def _run_simulation_isolated(
    program_text: str,
    initial_joints_rad: list[float] | None = None,
    initial_pose_m: list[float] | None = None,
    max_segments: int = MAX_PATH_SEGMENTS,
) -> dict[str, Any]:
    """
    Run dry-run simulation in isolated subprocess.

    This function is designed to be called via run.cpu_bound() for process
    isolation. It returns serializable results (dicts) rather than modifying
    global state.

    Args:
        program_text: The Python program to simulate
        initial_joints_rad: Initial joint angles in radians (robot's current position)
        initial_pose_m: Initial pose [x,y,z,rx,ry,rz] in meters/degrees (more accurate than FK)
        max_segments: Maximum path segments to collect (prevents memory exhaustion)

    Returns:
        Dict with keys:
        - segments: List of path segment dicts
        - targets: List of program target dicts
        - truncated: Whether results were truncated
        - error: Error message if simulation failed, else None
        - total_steps: Number of segments generated
    """
    import sys
    import asyncio

    # Local collectors (not shared with main process)
    local_segments: list[dict] = []
    local_targets: list[dict] = []
    truncated = False
    error_message: str | None = None

    # Import the dry-run client classes (this happens in the subprocess)
    # These imports are safe because we're in an isolated process
    from parol_commander.services.dry_run_client import (
        DryRunRobotClient,
        AsyncDryRunRobotClient,
    )

    try:
        # Also need types like Axis, Frame if imported
        try:
            import parol6.protocol.types as types
        except ImportError:
            types = None

        # Create shim module for parol6 that uses our collectors
        class Parol6Shim(ModuleType):
            """Shim module that provides DryRunRobotClient with local collectors."""

            # Dynamic attributes set at runtime
            protocol: Any
            Axis: Any
            Frame: Any
            client: Any

            def __init__(self):
                super().__init__("parol6")
                self._segments = local_segments
                self._targets = local_targets
                self._initial_joints = initial_joints_rad
                self._initial_pose = initial_pose_m

            @property
            def RobotClient(self):
                """Return a DryRunRobotClient class that uses local collectors."""
                segments = self._segments
                targets = self._targets
                init_joints = self._initial_joints
                init_pose = self._initial_pose

                class LocalDryRunRobotClient(DryRunRobotClient):
                    def __init__(self, *args, **kwargs):
                        # Ignore host/port args, use local collectors and initial state
                        super().__init__(
                            segment_collector=segments,
                            target_collector=targets,
                            initial_joints=init_joints,
                            initial_pose=init_pose,
                        )

                return LocalDryRunRobotClient

            @property
            def AsyncRobotClient(self):
                """Return an AsyncDryRunRobotClient class that uses local collectors."""
                segments = self._segments
                targets = self._targets
                init_joints = self._initial_joints
                init_pose = self._initial_pose

                class LocalAsyncDryRunRobotClient(AsyncDryRunRobotClient):
                    def __init__(self, *args, **kwargs):
                        super().__init__(
                            segment_collector=segments,
                            target_collector=targets,
                            initial_joints=init_joints,
                            initial_pose=init_pose,
                        )

                return LocalAsyncDryRunRobotClient

        shim_parol6 = Parol6Shim()

        # Add protocol types if available
        if types is not None:
            shim_parol6.protocol = MagicMock()
            shim_parol6.protocol.types = types
            shim_parol6.Axis = types.Axis
            shim_parol6.Frame = types.Frame

        # Also mock the client submodule
        shim_parol6.client = MagicMock()
        shim_parol6.client.RobotClient = shim_parol6.RobotClient
        shim_parol6.client.AsyncRobotClient = shim_parol6.AsyncRobotClient

        # Save original modules
        original_modules = {}
        parol6_module_keys = [
            k for k in sys.modules.keys() if k == "parol6" or k.startswith("parol6.")
        ]
        for key in parol6_module_keys:
            original_modules[key] = sys.modules[key]

        # Replace parol6 in sys.modules (only affects this subprocess)
        sys.modules["parol6"] = shim_parol6
        sys.modules["parol6.client"] = shim_parol6.client

        # Create mock time module and insert into sys.modules
        # This ensures `import time` returns our mock instead of real time module
        import builtins

        class MockTimeModule(ModuleType):
            """Mock time module with no-op sleep for simulation."""

            def __init__(self):
                super().__init__("time")
                self.__file__ = "<mock_time>"
                self.__package__ = ""

            @staticmethod
            def sleep(seconds):
                pass  # No-op in simulation

            @staticmethod
            def time():
                return 0.0

            @staticmethod
            def monotonic():
                return 0.0

            @staticmethod
            def perf_counter():
                return 0.0

            @staticmethod
            def perf_counter_ns():
                return 0

            @staticmethod
            def time_ns():
                return 0

        # Save original time module and replace with mock
        original_time_module = sys.modules.get("time")
        mock_time = MockTimeModule()
        sys.modules["time"] = mock_time

        # Prepare execution environment
        sim_globals = {
            "__name__": "__simulation__",
            "__file__": "simulation_script.py",
            "__builtins__": builtins.__dict__.copy(),
            "print": lambda *args, **kwargs: None,  # Suppress print
        }

        # Populate linecache with program source so DryRunRobotClient can find
        # source lines for TARGET marker detection
        import linecache

        lines = program_text.splitlines(keepends=True)
        # Ensure lines end with newline for linecache compatibility
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        linecache.cache["simulation_script.py"] = (
            len(program_text),  # size
            None,  # mtime
            lines,  # lines
            "simulation_script.py",  # filename
        )

        try:
            # Compile the script with explicit filename so frame inspection works
            # This allows _get_caller_line_number() to find "simulation_script.py" frames
            code = compile(program_text, "simulation_script.py", "exec")

            # Execute the compiled code
            exec(code, sim_globals)

            # Check if there's a main() function and what type
            if "main" in sim_globals:
                main_func = sim_globals["main"]

                if asyncio.iscoroutinefunction(main_func):
                    # Async main - need to run the coroutine
                    # Try asyncio.run() first (subprocess context)
                    try:
                        asyncio.run(main_func())
                    except RuntimeError as e:
                        if "cannot be called from a running event loop" in str(e):
                            # We're in a running loop (fallback in-process mode)
                            # Create a new event loop in a thread
                            import concurrent.futures

                            def run_async_in_thread():
                                """Run the async main in a new event loop in this thread."""
                                return asyncio.run(main_func())

                            with concurrent.futures.ThreadPoolExecutor(
                                max_workers=1
                            ) as pool:
                                future = pool.submit(run_async_in_thread)
                                future.result(timeout=SIMULATION_TIMEOUT_S)
                        else:
                            raise

                elif callable(main_func):
                    # Sync main - just call it
                    main_func()

        except Exception as e:
            error_message = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

        finally:
            # Restore original modules
            for key, mod in original_modules.items():
                sys.modules[key] = mod
            # Clean up any modules we added
            for key in list(sys.modules.keys()):
                if (
                    key == "parol6" or key.startswith("parol6.")
                ) and key not in original_modules:
                    del sys.modules[key]

            # Restore original time module
            if original_time_module is not None:
                sys.modules["time"] = original_time_module
            elif "time" in sys.modules and sys.modules["time"] is mock_time:
                del sys.modules["time"]

    except Exception as e:
        error_message = f"Simulation setup failed: {type(e).__name__}: {e}"

    # Enforce segment limit
    if len(local_segments) > max_segments:
        local_segments = local_segments[:max_segments]
        truncated = True

    return {
        "segments": local_segments,
        "targets": local_targets,
        "truncated": truncated,
        "error": error_message,
        "total_steps": len(local_segments),
    }


class PathVisualizer:
    """Visualizes robot path from program simulation."""

    def __init__(self):
        self._simulation_lock = asyncio.Lock()
        self._simulation_count = 0

    async def update_path_visualization(self, program_text: str) -> str | None:
        """
        Run the dry-run simulation for the given program text and update SimulationState.

        Executes simulation in an isolated subprocess for safety, then applies
        the results to the global simulation state.

        Returns:
            Error message if simulation failed, None otherwise.
        """
        async with self._simulation_lock:
            self._simulation_count += 1
            sim_id = self._simulation_count

            # Ensure process pool is available (idempotent - safe to call multiple times)
            # This handles both production (NiceGUI startup) and test contexts (pytest-xdist)
            run.setup()

            logger.info("Starting isolated path visualization (sim_id=%d)...", sim_id)

            if TRACE_ENABLED:
                segments_before = len(simulation_state.path_segments)
                targets_before = len(simulation_state.targets)
                logger.trace(  # type: ignore[attr-defined]
                    "PATHVIZ[%d]: Before simulation - segments=%d, targets=%d",
                    sim_id,
                    segments_before,
                    targets_before,
                )

            # Clear current state (but DON'T notify yet - wait until new data is populated
            # to avoid a transient "empty" state that destroys scene objects like TransformControls)
            simulation_state.path_segments.clear()
            simulation_state.targets.clear()
            simulation_state.current_step_index = 0
            simulation_state.total_steps = 0
            # NOTE: No notify_changed() here - we notify once at the end after new data arrives

            # Get current robot joint angles for initial position
            # robot_state.angles is in degrees, convert to radians
            initial_joints_rad: list[float] | None = None
            if robot_state.angles:
                import numpy as np

                initial_joints_rad = np.deg2rad(robot_state.angles).tolist()
                logger.debug(
                    "Using current robot joints as initial: %s deg", robot_state.angles
                )

            # Get current robot pose for initial position (more accurate than FK)
            # robot_state.x/y/z is in mm, convert to meters for internal use
            initial_pose_m: list[float] | None = None
            if (
                robot_state.x is not None
                and robot_state.y is not None
                and robot_state.z is not None
            ):
                initial_pose_m = [
                    robot_state.x / 1000.0,  # mm -> m
                    robot_state.y / 1000.0,  # mm -> m
                    robot_state.z / 1000.0,  # mm -> m
                    robot_state.rx if robot_state.rx is not None else 0.0,
                    robot_state.ry if robot_state.ry is not None else 0.0,
                    robot_state.rz if robot_state.rz is not None else 0.0,
                ]
                logger.debug(
                    "Using current robot pose as initial: [%.1f, %.1f, %.1f, %.1f, %.1f, %.1f] (mm/deg)",
                    robot_state.x,
                    robot_state.y,
                    robot_state.z,
                    robot_state.rx or 0.0,
                    robot_state.ry or 0.0,
                    robot_state.rz or 0.0,
                )

            try:
                # Run simulation in subprocess via NiceGUI's cpu_bound
                result = await asyncio.wait_for(
                    run.cpu_bound(
                        _run_simulation_isolated,
                        program_text,
                        initial_joints_rad,
                        initial_pose_m,
                    ),
                    timeout=SIMULATION_TIMEOUT_S
                    + 2.0,  # Extra buffer for process overhead
                )
            except asyncio.TimeoutError:
                logger.error("Simulation subprocess timed out (sim_id=%d)", sim_id)
                return "Simulation timed out"
            except Exception as e:
                logger.error("Simulation subprocess failed (sim_id=%d): %s", sim_id, e)
                return f"Simulation failed: {e}"

            # Handle errors
            if result.get("error"):
                logger.error(
                    "Simulation error (sim_id=%d): %s", sim_id, result["error"]
                )

            # Handle truncation warning
            if result.get("truncated"):
                logger.warning(
                    "Simulation truncated to %d segments (sim_id=%d)",
                    MAX_PATH_SEGMENTS,
                    sim_id,
                )

            # Convert dicts to objects and update state
            simulation_state.path_segments = [
                PathSegment.from_dict(d) for d in result["segments"]
            ]
            simulation_state.targets = [
                ProgramTarget.from_dict(d) for d in result["targets"]
            ]
            simulation_state.total_steps = result["total_steps"]

            logger.info(
                "Simulation complete (sim_id=%d). Generated %d path segments.",
                sim_id,
                len(simulation_state.path_segments),
            )

            # Also store results in active tab for per-tab isolation
            active_tab = editor_tabs_state.get_active_tab()
            if active_tab:
                active_tab.path_segments = list(simulation_state.path_segments)
                active_tab.targets = list(simulation_state.targets)

            # Reset scene tracking counter and clear old path objects
            if ui_state.urdf_scene and hasattr(
                ui_state.urdf_scene, "_rendered_segment_count"
            ):
                ui_state.urdf_scene._rendered_segment_count = 0
                # Clear old path scene objects so they don't accumulate
                if hasattr(ui_state.urdf_scene, "_path_objects"):
                    for obj in ui_state.urdf_scene._path_objects:
                        obj.delete()
                    ui_state.urdf_scene._path_objects.clear()

            # Trigger scene update via event-driven notification
            simulation_state.notify_changed()

            # Return error message if any
            return result.get("error")


# Singleton instance
path_visualizer = PathVisualizer()
