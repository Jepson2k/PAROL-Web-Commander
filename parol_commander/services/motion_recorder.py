"""Motion recorder for capturing robot actions as code during teaching."""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, List
from parol_commander.state import editor_tabs_state
import numpy as np
from parol6.utils.se3_utils import so3_rpy

from parol_commander.state import (
    recording_state,
    robot_state,
    ui_state,
)
from parol_commander.common.logging_config import TRACE_ENABLED

logger = logging.getLogger(__name__)


@dataclass
class ActiveJog:
    """Tracks an in-progress jog action."""

    start_time: float
    move_type: str  # "joint" or "cartesian"
    axis_info: str  # e.g., "J1+", "X+", "RZ-"


@dataclass
class RecordedAction:
    """A captured robot action during recording."""

    timestamp: float
    action_type: str  # move_joints, move_cartesian, home, gripper, io, delay
    params: dict = field(default_factory=dict)
    marker_id: Optional[str] = None  # UUID for TARGET marker (motion commands only)


class MotionRecorder:
    """Records robot actions as code snippets.

    Visualization is delegated to the dry-run simulation - this recorder
    only generates code. When code is inserted, the editor's debounced
    simulation will update the 3D visualization automatically.
    """

    def __init__(self):
        self._active_jog: Optional[ActiveJog] = None
        self._last_action_time: float = 0.0

    def _get_wrf_pose(self) -> List[float]:
        """Get current TCP pose in World Reference Frame (always WRF).

        Returns [x, y, z, rx, ry, rz] in mm/deg with RPY order='xyz'.
        """

        pose = robot_state.pose
        if pose and len(pose) >= 12:
            # Extract translation (column 4 of 4x4 matrix, indices 3, 7, 11)
            x_mm = float(pose[3])
            y_mm = float(pose[7])
            z_mm = float(pose[11])

            # Extract rotation matrix (3x3 upper-left)
            if len(pose) >= 11:
                R = np.array(
                    [
                        [float(pose[0]), float(pose[1]), float(pose[2])],
                        [float(pose[4]), float(pose[5]), float(pose[6])],
                        [float(pose[8]), float(pose[9]), float(pose[10])],
                    ]
                )
                rx, ry, rz = so3_rpy(R, degrees=True)
                return [x_mm, y_mm, z_mm, float(rx), float(ry), float(rz)]

        # Fallback to current displayed values
        logger.warning(
            "_get_wrf_pose: falling back to display values (pose data incomplete)"
        )
        return [
            robot_state.x,
            robot_state.y,
            robot_state.z,
            robot_state.rx,
            robot_state.ry,
            robot_state.rz,
        ]

    def _get_current_angles(self) -> List[float]:
        """Get current joint angles as list."""
        return list(robot_state.angles) if robot_state.angles else [0.0] * 6

    def toggle_recording(self) -> None:
        """Toggle recording state on/off."""
        if recording_state.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _should_insert_anchor(self) -> bool:
        """Check if anchor move_joints is needed using cached simulation results.

        Uses the pre-computed final_joints_rad from the active tab's simulation
        instead of running a blocking subprocess. This makes recording start instant.

        Returns:
            True if anchor should be inserted (positions differ), False otherwise.
        """
        if not robot_state.angles:
            return True

        current_angles_deg = self._get_current_angles()

        # Get cached final position from active tab
        active_tab = editor_tabs_state.get_active_tab()
        if not active_tab:
            return True

        if active_tab.final_joints_rad is None:
            return True

        simulated_angles_deg = np.rad2deg(active_tab.final_joints_rad).tolist()
        return self._compare_positions(simulated_angles_deg, current_angles_deg)

    def _compare_positions(
        self, script_end_deg: List[float], current_deg: List[float]
    ) -> bool:
        """Compare script end position with current robot position.

        Returns:
            True if anchor is needed (positions differ), False otherwise.
        """
        deltas = [script - cur for script, cur in zip(script_end_deg, current_deg)]
        max_delta = max(abs(d) for d in deltas)

        logger.info(
            "Anchor check:\n"
            "  Script ends at: [%s]\n"
            "  Robot now at:   [%s]\n"
            "  Deltas:         [%s]\n"
            "  Max delta: %.2f deg (threshold: 0.5)",
            ", ".join(f"{a:.1f}" for a in script_end_deg),
            ", ".join(f"{a:.1f}" for a in current_deg),
            ", ".join(f"{d:+.2f}" for d in deltas),
            max_delta,
        )

        if max_delta > 0.5:
            return True
        return False

    def _start_recording(self) -> None:
        """Start a new recording session."""
        recording_state.is_recording = True
        self._active_jog = None
        self._last_action_time = 0.0

        # Log the initial position for reference
        if robot_state.angles:
            logger.info(
                "Recording started - initial joints: %s deg",
                [f"{a:.1f}" for a in robot_state.angles],
            )
        if (
            robot_state.x is not None
            and robot_state.y is not None
            and robot_state.z is not None
        ):
            logger.info(
                "Recording started - initial pose: [%.1f, %.1f, %.1f, %.1f, %.1f, %.1f] (mm/deg)",
                robot_state.x,
                robot_state.y,
                robot_state.z,
                robot_state.rx or 0.0,
                robot_state.ry or 0.0,
                robot_state.rz or 0.0,
            )

        # Insert anchor move_joints command only if current position differs from
        # where the script would end (simulated using dry run client)
        if robot_state.angles and self._should_insert_anchor():
            angles = self._get_current_angles()
            args = ", ".join(f"{a:.2f}" for a in angles)
            anchor_snippet = (
                f"rbt.move_joints([{args}], duration=0.50)  # Recording start position"
            )
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
        self._last_action_time = 0.0
        logger.info("Recording stopped")

    def record_action(self, action_type: str, **params) -> None:
        """Record any robot action when recording is active.

        Args:
            action_type: One of "move_joints", "move_cartesian", "home",
                        "gripper", "io", "delay"
            **params: Action-specific parameters
        """
        if not recording_state.is_recording:
            return

        # Auto-insert delay if significant time gap (>0.5s)
        # Gap is measured from when the LAST action completed to when THIS action started
        now = time.time()
        if self._last_action_time > 0:
            gap = now - self._last_action_time
            if gap > 0.5:
                delay_snippet = f"time.sleep({gap:.2f})"
                self._insert_snippet(delay_snippet)
                if TRACE_ENABLED:
                    logger.log(
                        5, "RECORDER: Auto-inserted delay of %.2fs", gap
                    )  # TRACE level

        # Get duration for motion commands (to estimate when action completes)
        duration = params.get("duration", 0.0)

        # Update _last_action_time to estimated completion time for motion commands
        # For instant commands (home, gripper, io), use current time
        if action_type in ("move_joints", "move_cartesian") and duration > 0:
            self._last_action_time = now + duration
        else:
            self._last_action_time = now

        # Generate marker for motion commands (for interactive targets)
        marker_id = None
        if action_type in ("move_joints", "move_cartesian"):
            marker_id = uuid.uuid4().hex[:8]  # Short UUID

        # Generate and insert code
        snippet = self._generate_code(action_type, params, marker_id)
        self._insert_snippet(snippet)

        if TRACE_ENABLED:
            logger.log(
                5, "RECORDER: Recorded action %s with params %s", action_type, params
            )  # TRACE level
        logger.debug("Recorded action: %s", action_type)

    def _generate_code(
        self, action_type: str, params: dict, marker_id: Optional[str]
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

        if action_type == "move_joints":
            angles = params.get("angles", [0.0] * 6)
            dur = params.get("duration", 1.0)
            # Ensure minimum duration for safety
            dur = max(0.5, dur)
            args = ", ".join(f"{a:.2f}" for a in angles)
            return f"rbt.move_joints([{args}], duration={dur:.2f}){marker}"

        elif action_type == "move_cartesian":
            pose = params.get("pose", [0.0] * 6)
            dur = params.get("duration", 1.0)
            # Ensure minimum duration for safety
            dur = max(0.5, dur)
            args = ", ".join(f"{p:.3f}" for p in pose)
            return f"rbt.move_cartesian([{args}], duration={dur:.2f}){marker}"

        elif action_type == "home":
            return "rbt.home()"

        elif action_type == "gripper":
            if params.get("calibrate"):
                return 'rbt.control_electric_gripper("calibrate")'
            pos = params.get("position", 0)
            spd = params.get("speed", 50)
            cur = params.get("current", 100)
            return f'rbt.control_electric_gripper("move", position={pos}, speed={spd}, current={cur})'

        elif action_type == "io":
            action = "open" if params.get("state") else "close"
            port = params.get("port", 1)
            return f'rbt.control_pneumatic_gripper("{action}", {port})'

        elif action_type == "delay":
            seconds = params.get("seconds", 1.0)
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
                    "move_joints", angles=self._get_current_angles(), duration=duration
                )
            else:
                self.record_action(
                    "move_cartesian", pose=self._get_wrf_pose(), duration=duration
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
        # Temporarily enable recording for single capture
        was_recording = recording_state.is_recording
        recording_state.is_recording = True

        if move_type == "joints":
            self.record_action(
                "move_joints", angles=self._get_current_angles(), duration=1.0
            )
        else:
            self.record_action(
                "move_cartesian", pose=self._get_wrf_pose(), duration=1.0
            )

        # Restore recording state
        recording_state.is_recording = was_recording

    def _insert_snippet(self, snippet: str) -> None:
        """Insert code snippet into the editor and flash the new line."""

        if ui_state.editor_panel.program_textarea:
            textarea = ui_state.editor_panel.program_textarea
            val = textarea.value or ""

            # Count lines before insertion for flash highlighting
            lines_before = len(val.splitlines()) if val else 0

            if val and not val.endswith("\n"):
                val += "\n"
            new_value = val + snippet + "\n"
            # Direct assignment - NiceGUI's binding handles the update
            # This will trigger the editor's on_change -> debounced simulation
            textarea.value = new_value

            # Flash the newly added line
            new_line_number = lines_before + 1
            ui_state.editor_panel._flash_editor_lines([new_line_number])
        else:
            logger.error("Editor textarea not ready - open Program tab first")


# Singleton
motion_recorder = MotionRecorder()
