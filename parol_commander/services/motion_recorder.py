"""Motion recorder for capturing robot actions as code during teaching."""

import logging
import time
import uuid
from dataclasses import dataclass

import numpy as np
from pinokin import se3_rpy

from parol_commander.state import (
    recording_state,
    robot_state,
    ui_state,
)
from parol_commander.common.logging_config import TRACE_ENABLED

logger = logging.getLogger(__name__)

# Methods whose recorded code gets TARGET:uuid markers for interactive 3D editing.
# Only methods with literal list arguments (positions/angles) benefit from markers,
# since those can be interactively edited by dragging targets in the 3D scene.
EDITABLE_TARGET_METHODS = frozenset({"moveJ", "moveL"})


@dataclass
class ActiveJog:
    """Tracks an in-progress jog action."""

    start_time: float
    move_type: str  # "joint" or "cartesian"
    axis_info: str  # e.g., "J1+", "X+", "RZ-"


class MotionRecorder:
    """Records robot actions as code snippets.

    Visualization is delegated to the dry-run simulation - this recorder
    only generates code. When code is inserted, the editor's debounced
    simulation will update the 3D visualization automatically.
    """

    def __init__(self):
        self._active_jog: ActiveJog | None = None
        self._estimated_done_time: float = 0.0

    def _get_wrf_pose(self) -> list[float]:
        """Get current TCP pose in World Reference Frame (always WRF).

        Returns [x, y, z, rx, ry, rz] in mm/deg with RPY order='xyz'.
        """
        T = robot_state.pose.reshape(4, 4)
        rpy_rad = np.zeros(3, dtype=np.float64)
        se3_rpy(T, rpy_rad)
        rpy_deg = np.degrees(rpy_rad)
        return [
            float(T[0, 3]),
            float(T[1, 3]),
            float(T[2, 3]),
            float(rpy_deg[0]),
            float(rpy_deg[1]),
            float(rpy_deg[2]),
        ]

    def _get_current_angles(self) -> list[float]:
        """Get current joint angles as list."""
        n = ui_state.active_robot.joints.count
        return (
            list(robot_state.angles.deg[:n])
            if len(robot_state.angles) >= n
            else [0.0] * n
        )

    def toggle_recording(self) -> None:
        """Toggle recording state on/off."""
        if recording_state.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        """Start a new recording session."""
        recording_state.is_recording = True
        self._active_jog = None
        self._estimated_done_time = 0.0

        # Log the initial position for reference
        if len(robot_state.angles) >= ui_state.active_robot.joints.count:
            logger.info(
                "Recording started - initial joints: %s deg",
                [f"{a:.1f}" for a in robot_state.angles.deg],
            )
        logger.info(
            "Recording started - initial pose: [%.1f, %.1f, %.1f, %.1f, %.1f, %.1f] (mm/deg)",
            robot_state.x,
            robot_state.y,
            robot_state.z,
            robot_state.rx,
            robot_state.ry,
            robot_state.rz,
        )

        # Always insert anchor moveJ to establish the recording start position.
        # This ensures the path visualizer shows correct transitions — without it,
        # the viz draws a line from the script's last move directly to the first
        # recorded move, even if the robot was jogged elsewhere in between.
        if len(robot_state.angles) >= ui_state.active_robot.joints.count:
            angles = self._get_current_angles()
            args = ", ".join(f"{a:.2f}" for a in angles)
            spd = ui_state.jog_speed / 100.0
            acc = ui_state.jog_accel / 100.0
            anchor_snippet = f"rbt.moveJ([{args}], speed={spd}, accel={acc})  # Recording start position"
            self._insert_snippet(anchor_snippet)
            logger.info(
                "Inserted recording start anchor at joints: %s",
                [f"{a:.1f}" for a in angles],
            )

    def _stop_recording(self) -> None:
        """Stop recording session."""
        # If there's an active jog, end it first
        if self._active_jog:
            self.on_jog_end()

        recording_state.is_recording = False
        self._estimated_done_time = 0.0
        logger.info("Recording stopped")

    def record_action(self, action_type: str, **params) -> None:
        """Record any robot action when recording is active.

        Args:
            action_type: One of "moveJ", "moveL", "home",
                        "gripper", "io", "delay"
            **params: Action-specific parameters
        """
        if not recording_state.is_recording:
            return
        self._record_action_impl(action_type, **params)

    def _record_action_impl(self, action_type: str, **params) -> None:
        """Core recording logic (no is_recording guard)."""
        # Auto-insert delay if significant time gap (>0.5s)
        now = time.time()
        if self._estimated_done_time > 0:
            gap = now - self._estimated_done_time
            if gap > 0.5:
                delay_snippet = f"time.sleep({gap:.2f})"
                self._insert_snippet(delay_snippet)
                if TRACE_ENABLED:
                    logger.log(
                        5, "RECORDER: Auto-inserted delay of %.2fs", gap
                    )  # TRACE level

        # Get duration for motion commands (to estimate when action completes)
        duration = params.get("duration", 0.0)
        is_motion_action = action_type in EDITABLE_TARGET_METHODS

        # Update timestamp to estimated completion time for motion commands,
        # or current time for instant commands (home, gripper, io)
        if is_motion_action and duration > 0:
            self._estimated_done_time = now + duration
        else:
            self._estimated_done_time = now

        # Generate marker for motion commands (for interactive targets)
        marker_id = uuid.uuid4().hex[:8] if is_motion_action else None

        # Generate and insert code
        snippet = self._generate_code(action_type, params, marker_id)
        self._insert_snippet(snippet)

        if TRACE_ENABLED:
            logger.log(
                5, "RECORDER: Recorded action %s with params %s", action_type, params
            )  # TRACE level
        logger.debug("Recorded action: %s", action_type)

    def _generate_code(
        self, action_type: str, params: dict, marker_id: str | None
    ) -> str:
        """Generate Python code snippet for an action.

        Args:
            action_type: Type of action
            params: Action parameters
            marker_id: UUID marker for interactive targets (motion commands only)

        Returns:
            Python code snippet string
        """
        marker = f"  # TARGET:{marker_id}" if marker_id else ""

        if action_type == "moveJ":
            angles = params["angles"]
            spd = ui_state.jog_speed / 100.0
            acc = ui_state.jog_accel / 100.0
            args = ", ".join(f"{a:.2f}" for a in angles)
            return f"rbt.moveJ([{args}], speed={spd}, accel={acc}){marker}"

        elif action_type == "moveL":
            pose = params["pose"]
            spd = ui_state.jog_speed / 100.0
            acc = ui_state.jog_accel / 100.0
            args = ", ".join(f"{p:.3f}" for p in pose)
            return f"rbt.moveL([{args}], speed={spd}, accel={acc}){marker}"

        elif action_type == "home":
            return "rbt.home()"

        elif action_type == "gripper":
            if params.get("calibrate"):
                return "rbt.tool.calibrate()"
            pos = params["position"]
            if pos <= 0.0:
                return "rbt.tool.open()"
            if pos >= 1.0:
                return "rbt.tool.close()"
            kwargs = []
            spd = params.get("speed")
            cur = params.get("current")
            if spd is not None:
                kwargs.append(f"speed={spd}")
            if cur is not None:
                kwargs.append(f"current={cur}")
            kwargs_str = ", ".join(kwargs)
            if kwargs_str:
                return f"rbt.tool.set_position({pos}, {kwargs_str})"
            return f"rbt.tool.set_position({pos})"

        elif action_type == "io":
            port = params["port"]
            state = params["state"]
            return f"rbt.set_io({port}, {state})"

        elif action_type == "delay":
            seconds = params["seconds"]
            return f"time.sleep({seconds:.2f})"

        else:
            return f"# Unknown action: {action_type}"

    def on_jog_start(self, move_type: str, axis_info: str) -> None:
        """Called when a jog action starts.

        Args:
            move_type: "joint" or "cartesian"
            axis_info: Axis identifier like "J1+", "J3-", "X+", "RZ-"
        """
        if not recording_state.is_recording:
            return

        # If there's already an active jog, end it first
        if self._active_jog:
            self.on_jog_end()

        self._active_jog = ActiveJog(
            start_time=time.time(), move_type=move_type, axis_info=axis_info
        )
        logger.debug("Jog started: %s %s", move_type, axis_info)

    def on_jog_end(self) -> None:
        """Called when a jog action ends. Records the move as code."""
        if not recording_state.is_recording or not self._active_jog:
            return

        end_time = time.time()
        duration = end_time - self._active_jog.start_time

        # Only record if there was actual movement (> 0.1s)
        if duration > 0.1:
            if self._active_jog.move_type == "joint":
                self.record_action(
                    "moveJ", angles=self._get_current_angles(), duration=duration
                )
            else:
                self.record_action(
                    "moveL", pose=self._get_wrf_pose(), duration=duration
                )

            logger.debug(
                "Jog ended: %s - recorded move (%.2fs)",
                self._active_jog.axis_info,
                duration,
            )
        else:
            logger.debug(
                "Jog ended: %s - too short to record (%.2fs)",
                self._active_jog.axis_info,
                duration,
            )

        self._active_jog = None

    def capture_current_pose(self, move_type: str = "cartesian") -> None:
        """Capture current robot pose and insert as move command.

        Args:
            move_type: "cartesian" or "joints"
        """
        self._estimated_done_time = 0.0
        if move_type == "joints":
            self._record_action_impl(
                "moveJ", angles=self._get_current_angles(), duration=1.0
            )
        else:
            self._record_action_impl("moveL", pose=self._get_wrf_pose(), duration=1.0)

    def _insert_snippet(self, snippet: str) -> None:
        """Insert code snippet into the editor and flash the new line."""

        if ui_state.editor_panel.program_textarea:
            textarea = ui_state.editor_panel.program_textarea
            val = textarea.value or ""

            # Count lines before insertion for flash highlighting
            lines_before = len(val.splitlines())

            if val and not val.endswith("\n"):
                val += "\n"
            new_value = val + snippet + "\n"
            # Direct assignment - NiceGUI's binding handles the update
            # This will trigger the editor's on_change -> debounced simulation
            textarea.value = new_value

            # Flash the newly added line
            new_line_number = lines_before + 1
            ui_state.editor_panel.flash_editor_lines([new_line_number])
        else:
            logger.error("Editor textarea not ready - open Program tab first")


# Singleton
motion_recorder = MotionRecorder()
