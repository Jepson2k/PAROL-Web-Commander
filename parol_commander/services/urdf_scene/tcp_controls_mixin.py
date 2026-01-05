"""
TCP Controls Mixin for UrdfScene.

Provides TCP TransformControls functionality:
- Enable/disable TransformControls on TCP ball
- Handle transform events for direct Cartesian moves (jogging) or IK (editing)
- Frame and mode switching (translate/rotate, WRF/TRF)

The TCP ball behavior depends on RobotAppearanceMode:
- LIVE/SIMULATOR: Streams Cartesian jog moves to backend
- EDITING: Solves IK for target positioning
"""

import asyncio
import logging
import math
from typing import Any, Callable, List, Optional, Tuple

import numpy as np
from scipy.spatial.transform import Rotation as ScipyRotation
from nicegui import ui
from nicegui.helpers import is_user_simulation

from parol_commander.common.theme import SceneColors
from parol_commander.state import robot_state
from .ik_solver import EditingIKSolver

from .config import RobotAppearanceMode

# Pre-allocated conversion constant
_DEG_TO_RAD = math.pi / 180.0


class TCPControlsMixin:
    """Mixin providing TCP TransformControls functionality for UrdfScene."""

    # These attributes are defined in the main UrdfScene class
    scene: Any
    tcp_anchor: Any
    tcp_offset: Any
    _appearance_mode: RobotAppearanceMode
    _editing_angles: List[float]
    joint_groups: dict
    joint_trafos: dict
    joint_names: List[str]

    # Methods from other mixins (for type checking)
    def _sync_robot_state_from_editing(self) -> None: ...

    def _update_edit_bar_values(self, editing_type: str) -> None: ...

    _current_editing_type: Optional[str]

    def _init_tcp_controls_state(self) -> None:
        """Initialize TCP controls state variables."""
        # TCP TransformControls state
        self._tcp_transform_enabled: bool = False
        self._tcp_transform_mode: str = "translate"  # "translate" or "rotate"
        self._tcp_last_position: Optional[Tuple[float, float, float]] = None
        self._tcp_last_rotation: Optional[Tuple[float, float, float]] = None

        self._tcp_cartesian_move_callback: Optional[Callable[[List[float]], None]] = (
            None
        )
        self._tcp_cartesian_move_rel_trf_callback: Optional[
            Callable[[List[float]], None]
        ] = None
        self._tcp_cartesian_move_start_callback: Optional[Callable[[], None]] = None
        self._tcp_cartesian_move_end_callback: Optional[Callable[[], None]] = None
        self._tcp_drag_start_pos: Optional[Tuple[float, float, float]] = None
        self._tcp_drag_start_rot: Optional[Tuple[float, float, float]] = None
        self._tcp_ball: Any | None = None
        self._tcp_ball_dragging: bool = False
        self._control_frame: str = "WRF"  # 'TRF' or 'WRF'
        self._tcp_fk_solver: Optional[EditingIKSolver] = None
        self._editing_rotation: Optional[List[float]] = None

        # Pre-allocated buffers to avoid per-call allocations
        self._angles_rad_buffer: List[float] = [0.0] * 6
        self._target_pos_buffer: np.ndarray = np.zeros(3, dtype=np.float64)
        self._target_orientation_buffer: np.ndarray = np.zeros(3, dtype=np.float64)
        self._pose_mm_buffer: List[float] = [0.0] * 6

    def on_tcp_cartesian_move(self, callback: Callable[[List[float]], None]) -> None:
        """Register callback to receive absolute TCP position for Cartesian moves.

        The callback receives pose as [x_mm, y_mm, z_mm, rx_deg, ry_deg, rz_deg].
        This is used for drag-to-move functionality where the robot should move
        directly to the dragged position.

        Args:
            callback: Function to call with target pose in mm/degrees
        """
        self._tcp_cartesian_move_callback = callback

    def on_tcp_cartesian_move_rel_trf(
        self, callback: Callable[[List[float]], None]
    ) -> None:
        """Register callback to receive relative TCP delta in Tool Reference Frame.

        The callback receives delta as [dx_mm, dy_mm, dz_mm, drx_deg, dry_deg, drz_deg].
        This is used for TRF mode where movement is relative to the tool orientation.

        Args:
            callback: Function to call with delta in mm/degrees (tool frame)
        """
        self._tcp_cartesian_move_rel_trf_callback = callback

    def on_tcp_cartesian_move_start(self, callback: Callable[[], None]) -> None:
        """Register callback to be called when a TCP TransformControls drag starts."""
        self._tcp_cartesian_move_start_callback = callback

    def on_tcp_cartesian_move_end(self, callback: Callable[[], None]) -> None:
        """Register callback to be called when a TCP TransformControls drag ends."""
        self._tcp_cartesian_move_end_callback = callback

    def set_gizmo_visible(self, visible: bool) -> None:
        """Show or hide the TCP gizmo (TransformControls).

        Args:
            visible: True to show, False to hide
        """
        if visible:
            # Ensure jog ball exists and is visible
            self._ensure_tcp_ball()
            if self._tcp_ball:
                self._tcp_ball.visible(True)
            # Enable TransformControls if not already enabled
            if not self._tcp_transform_enabled:
                self.enable_tcp_transform_controls(self._tcp_transform_mode)
        else:
            # Disable TransformControls and hide jog ball
            if self._tcp_transform_enabled:
                self.disable_tcp_transform_controls()
            if self._tcp_ball:
                self._tcp_ball.visible(False)

    def set_control_frame(self, _frame: str) -> None:
        """No-op: Control frame is fixed to WRF for translation gizmo."""
        # Frame is always WRF for TransformControls (world space)
        self._control_frame = "WRF"

    def set_gizmo_display_mode(self, mode: str) -> None:
        """Toggle gizmo display between translation and rotation modes.

        Args:
            mode: Either "TRANSLATE" for translation or "ROTATE" for rotation
        """
        mode = (mode or "").upper()
        if mode not in ("TRANSLATE", "ROTATE"):
            raise ValueError(f"Invalid mode: {mode}. Must be 'TRANSLATE' or 'ROTATE'.")

        self._tcp_transform_mode = "translate" if mode == "TRANSLATE" else "rotate"

        # Update TCP TransformControls mode if enabled
        if self._tcp_transform_enabled:
            self.set_tcp_transform_mode(self._tcp_transform_mode)

    def enable_tcp_transform_controls(self, mode: str = "translate") -> None:
        """Enable TransformControls on the TCP anchor for Cartesian jogging.

        Args:
            mode: "translate" or "rotate"
        """
        if not self.scene:
            logging.warning("Cannot enable TCP transform controls: scene not available")
            return
        # Ensure jog ball exists
        self._ensure_tcp_ball()
        if not self._tcp_ball:
            logging.warning(
                "Cannot enable TCP transform controls: jog ball not available"
            )
            return

        # Store mode
        self._tcp_transform_mode = mode.lower()

        # Get the jog ball object ID
        tcp_object_id = str(self._tcp_ball.id)

        # Sync jog ball to current TCP via FK before enabling (like joint edit)
        self._update_jog_ball_from_robot_state()

        # In NiceGUI user simulation mode, no browser is running; skip JS enablement
        if is_user_simulation():
            try:
                if self._tcp_ball:
                    self._tcp_ball.visible(True)
            except Exception as e:
                logging.debug(
                    "User simulation: jog ball visibility update failed: %s", e
                )
            logging.debug(
                "User simulation detected; skipping TCP TransformControls enablement"
            )
            return

        # Enable TransformControls with a short Python-side retry until the object exists on JS
        async def _enable_with_retry():
            try:
                attempts = 20  # ~1s total at 50ms intervals
                for i in range(attempts):
                    # Try to enable now
                    self.scene.run_method(
                        "enable_transform_controls",
                        tcp_object_id,
                        self._tcp_transform_mode,
                        0.8,
                        None,
                        True,
                    )
                    await asyncio.sleep(0.05)
                    ok = await self.scene.run_method(
                        "has_transform_controls", tcp_object_id
                    )
                    if ok:
                        if self._tcp_ball:
                            self._tcp_ball.visible(True)
                        # Store initial position/rotation for delta calculation
                        self._tcp_last_position = None
                        self._tcp_last_rotation = None
                        self._tcp_transform_enabled = True
                        logging.debug(f"Enabled TCP TransformControls in {mode} mode")
                        return
                logging.warning("Failed to enable TCP TransformControls after retries")
            except (TimeoutError, asyncio.CancelledError):
                # Scene is shutting down, bail out gracefully
                logging.debug("TCP TransformControls enablement cancelled (shutdown)")

        # Use explicit scene context to avoid stale slot errors
        with self.scene:
            ui.timer(0.0, _enable_with_retry, once=True)

    def disable_tcp_transform_controls(self) -> None:
        """Disable TransformControls on the TCP anchor."""
        if not self.scene or not self._tcp_ball:
            return

        tcp_object_id = str(self._tcp_ball.id)

        if hasattr(self.scene, "disable_transform_controls"):
            self.scene.disable_transform_controls(tcp_object_id)

        # Hide jog ball when controls are disabled
        if self._tcp_ball:
            self._tcp_ball.visible(False)

        self._tcp_transform_enabled = False
        self._tcp_last_position = None
        self._tcp_last_rotation = None
        logging.debug("Disabled TCP TransformControls")

    def set_tcp_transform_mode(self, mode: str) -> None:
        """Change the TCP TransformControls mode.

        Args:
            mode: "translate" or "rotate"
        """
        if not self._tcp_transform_enabled:
            return

        if not self.scene or not self._tcp_ball:
            return

        self._tcp_transform_mode = mode.lower()
        tcp_object_id = str(self._tcp_ball.id)

        # Sync ball position/rotation from FK before switching modes
        # This ensures rotate mode starts from the correct orientation
        self._update_tcp_ball_position()

        if hasattr(self.scene, "set_transform_mode"):
            self.scene.set_transform_mode(tcp_object_id, self._tcp_transform_mode)

        # Reset delta tracking
        self._tcp_last_position = None
        self._tcp_last_rotation = None

        logging.debug(f"Changed TCP TransformControls mode to {mode}")

    def _ensure_tcp_ball(self) -> None:
        """Create the unified TCP ball if missing.

        The TCP ball is used for both jogging (LIVE/SIMULATOR) and IK target editing (EDITING).
        """
        if not self.scene:
            return
        if self._tcp_ball:
            return
        with self.scene:
            ball = ui.scene.sphere(
                radius=0.015,
                width_segments=16,
                height_segments=16,
                wireframe=False,
            ).with_name("tcp:ball")
            ball.material(SceneColors.EDIT_GRAY_HEX, 0.9)
            self._tcp_ball = ball
        # Initial position/orientation based on mode
        self._update_tcp_ball_position()

    def _update_tcp_ball_position(self) -> None:
        """Update TCP ball position using FK.

        The position source depends on the appearance mode:
        - LIVE/SIMULATOR: Uses robot_state.angles (live robot position)
        - EDITING: Uses _editing_angles (editing target position)
        """
        if not self._tcp_ball or self._tcp_ball_dragging:
            return

        # Lazy-init FK solver
        if self._tcp_fk_solver is None:
            try:
                self._tcp_fk_solver = EditingIKSolver.from_urdf_scene(self)
            except Exception as e:
                logging.warning("FK solver init failed: %s", e)
                return

        # Get angles based on mode - use pre-allocated buffer
        buf = self._angles_rad_buffer
        if self._appearance_mode == RobotAppearanceMode.EDITING:
            # Use editing angles (already in radians)
            for i in range(6):
                buf[i] = (
                    self._editing_angles[i] if i < len(self._editing_angles) else 0.0
                )
        else:
            # Use live robot angles (deg -> rad) - vectorized conversion
            angles = getattr(robot_state, "angles", None)
            if angles:
                n = min(len(angles), 6)
                for i in range(n):
                    buf[i] = float(angles[i]) * _DEG_TO_RAD
                for i in range(n, 6):
                    buf[i] = 0.0
            else:
                for i in range(6):
                    buf[i] = 0.0

        try:
            ee_pose = self._tcp_fk_solver.forward_kinematics(buf)
            # FK returns [x, y, z, rx, ry, rz] in meters and radians (XYZ Euler)
            self._tcp_ball.move(float(ee_pose[0]), float(ee_pose[1]), float(ee_pose[2]))
            # Use rotate_euler to set XYZ Euler angles directly, avoiding matrix conversion issues
            self._tcp_ball.rotate_euler(
                float(ee_pose[3]), float(ee_pose[4]), float(ee_pose[5]), "XYZ"
            )
        except Exception as e:
            logging.debug("FK update for TCP ball failed: %s", e)

    def _update_jog_ball_from_robot_state(self) -> None:
        """Position the TCP ball using live robot state (LIVE/SIMULATOR modes)."""
        if self._appearance_mode == RobotAppearanceMode.EDITING:
            return  # Don't update from robot state while editing
        self._update_tcp_ball_position()

    def _handle_tcp_transform_for_jog(self, e) -> None:
        """Handle TCP transform events - behavior depends on appearance mode.

        Called from _handle_transform_continuous when TCP ball is being transformed.
        - LIVE/SIMULATOR: Sends direct Cartesian move commands via callback
        - EDITING: Solves IK to update editing angles
        """
        if not self._tcp_transform_enabled:
            return

        object_name = getattr(e, "object_name", "") or ""
        if object_name not in ("tcp:ball", "tcp:jog_ball", "tcp:offset"):
            return

        # Record starting position on first event of drag session
        if not self._tcp_ball_dragging:
            # Get starting position from robot state (convert mm to m)
            self._tcp_drag_start_pos = (
                robot_state.x / 1000.0,
                robot_state.y / 1000.0,
                robot_state.z / 1000.0,
            )
            # Get starting rotation from robot state (convert deg to rad)
            self._tcp_drag_start_rot = (
                math.radians(robot_state.rx),
                math.radians(robot_state.ry),
                math.radians(robot_state.rz),
            )

        # Mark that TCP ball is being dragged - prevents FK from overwriting position
        self._tcp_ball_dragging = True

        # Route to mode-specific handler
        if self._appearance_mode == RobotAppearanceMode.EDITING:
            self._handle_tcp_transform_for_ik(e)
        else:
            self._handle_tcp_transform_for_cartesian(e)

    def _get_current_rotation_matrix(self) -> np.ndarray:
        """Get current tool rotation matrix from FK.

        Returns:
            3x3 rotation matrix representing current tool orientation
        """
        # Lazy-init FK solver
        if self._tcp_fk_solver is None:
            try:
                self._tcp_fk_solver = EditingIKSolver.from_urdf_scene(self)
            except Exception as err:
                logging.warning("FK solver init failed: %s", err)
                return np.eye(3)

        # Get current angles from robot state - use pre-allocated buffer
        buf = self._angles_rad_buffer
        angles = getattr(robot_state, "angles", None)
        if angles:
            n = min(len(angles), 6)
            for i in range(n):
                buf[i] = float(angles[i]) * _DEG_TO_RAD
            for i in range(n, 6):
                buf[i] = 0.0
        else:
            for i in range(6):
                buf[i] = 0.0

        # Use FK to get rotation matrix
        try:
            ee_pose = self._tcp_fk_solver.forward_kinematics(buf)
            # ee_pose is [x, y, z, rx, ry, rz] in meters and radians (XYZ Euler)
            R = ScipyRotation.from_euler(
                "XYZ", [ee_pose[3], ee_pose[4], ee_pose[5]]
            ).as_matrix()
            return R
        except Exception as err:
            logging.debug("FK rotation matrix failed: %s", err)
            return np.eye(3)

    def _handle_tcp_transform_for_cartesian(self, e) -> None:
        """Handle TCP ball drag in LIVE/SIMULATOR mode - stream Cartesian moves.

        Uses absolute world coordinates from the gizmo.
        """
        if self._tcp_transform_mode == "translate":
            # Get world coordinates (prefer wx, wy, wz for absolute position)
            new_x = getattr(e, "wx", None)
            new_y = getattr(e, "wy", None)
            new_z = getattr(e, "wz", None)

            if new_x is None or new_y is None or new_z is None:
                # Fallback to local coordinates
                new_x = e.x if e.x is not None else 0.0
                new_y = e.y if e.y is not None else 0.0
                new_z = e.z if e.z is not None else 0.0

            current_pos = (float(new_x), float(new_y), float(new_z))

            # Record last position (no deadband filtering)
            self._tcp_last_position = current_pos

            # Use absolute world coordinates
            if self._tcp_cartesian_move_callback:
                # Use rotation from drag START to avoid rotation during translation
                if self._tcp_drag_start_rot is not None:
                    rx_deg = math.degrees(self._tcp_drag_start_rot[0])
                    ry_deg = math.degrees(self._tcp_drag_start_rot[1])
                    rz_deg = math.degrees(self._tcp_drag_start_rot[2])
                else:
                    rx_deg = robot_state.rx
                    ry_deg = robot_state.ry
                    rz_deg = robot_state.rz
                # Reuse pre-allocated buffer
                buf = self._pose_mm_buffer
                buf[0] = current_pos[0] * 1000.0  # x: m -> mm
                buf[1] = current_pos[1] * 1000.0  # y: m -> mm
                buf[2] = current_pos[2] * 1000.0  # z: m -> mm
                buf[3] = rx_deg  # Keep orientation from drag start
                buf[4] = ry_deg
                buf[5] = rz_deg
                self._tcp_cartesian_move_callback(buf)

        else:  # rotate mode
            # Get rotation values (radians)
            rx = e.rx if e.rx is not None else 0.0
            ry = e.ry if e.ry is not None else 0.0
            rz = e.rz if e.rz is not None else 0.0

            current_rot = (float(rx), float(ry), float(rz))
            # Record last rotation (no deadband filtering)
            self._tcp_last_rotation = current_rot

            # Use absolute rotation values
            if self._tcp_cartesian_move_callback:
                # Reuse pre-allocated buffer
                buf = self._pose_mm_buffer
                buf[0] = robot_state.x  # Keep current position (already in mm)
                buf[1] = robot_state.y
                buf[2] = robot_state.z
                buf[3] = math.degrees(current_rot[0])  # rx: rad -> deg
                buf[4] = math.degrees(current_rot[1])  # ry: rad -> deg
                buf[5] = math.degrees(current_rot[2])  # rz: rad -> deg
                self._tcp_cartesian_move_callback(buf)

    def _handle_tcp_transform_for_ik(self, e) -> None:
        """Handle TCP ball drag in EDITING mode - solve IK for position and/or orientation."""
        # Ensure FK/IK solver is initialized
        if self._tcp_fk_solver is None:
            try:
                self._tcp_fk_solver = EditingIKSolver.from_urdf_scene(self)
            except Exception as err:
                logging.warning("IK solver init failed: %s", err)
                return

        target_orientation = None
        target_pos = self._target_pos_buffer

        if self._tcp_transform_mode == "rotate":
            # Rotation mode: get orientation from event, keep current position
            rx = e.rx if e.rx is not None else 0.0
            ry = e.ry if e.ry is not None else 0.0
            rz = e.rz if e.rz is not None else 0.0

            # Fill pre-allocated orientation buffer
            orient_buf = self._target_orientation_buffer
            orient_buf[0] = float(rx)
            orient_buf[1] = float(ry)
            orient_buf[2] = float(rz)
            target_orientation = orient_buf

            # Store edited rotation for reference
            if self._editing_rotation is None:
                self._editing_rotation = [0.0, 0.0, 0.0]
            self._editing_rotation[0] = float(rx)
            self._editing_rotation[1] = float(ry)
            self._editing_rotation[2] = float(rz)

            # Get current position from FK (maintain position while rotating)
            fk_result = self._tcp_fk_solver.forward_kinematics(self._editing_angles)
            target_pos[0] = fk_result[0]
            target_pos[1] = fk_result[1]
            target_pos[2] = fk_result[2]
        else:
            # Translate mode: get position from event
            new_x = getattr(e, "wx", None)
            new_y = getattr(e, "wy", None)
            new_z = getattr(e, "wz", None)

            if new_x is None or new_y is None or new_z is None:
                # Fallback to local coordinates
                new_x = e.x if e.x is not None else 0.0
                new_y = e.y if e.y is not None else 0.0
                new_z = e.z if e.z is not None else 0.0

            target_pos[0] = float(new_x)
            target_pos[1] = float(new_y)
            target_pos[2] = float(new_z)

            # If we have a stored rotation from previous rotate mode, use it
            if self._editing_rotation is not None:
                orient_buf = self._target_orientation_buffer
                orient_buf[0] = self._editing_rotation[0]
                orient_buf[1] = self._editing_rotation[1]
                orient_buf[2] = self._editing_rotation[2]
                target_orientation = orient_buf

        # Solve IK with throttling (~30Hz)
        result = self._tcp_fk_solver.solve(
            target_pos=target_pos,
            current_angles=self._editing_angles,
            throttle=True,
            target_orientation=target_orientation,
        )

        # If throttled (returns None), skip this frame
        if result is None:
            return

        if result.success:
            # Update editing angles without repositioning TCP ball (user is dragging it)
            # Copy values in place to avoid list allocation
            for i, a in enumerate(result.angles):
                if i < len(self._editing_angles):
                    self._editing_angles[i] = a
            # Apply to robot joints
            for joint_name, q in zip(self.joint_names, self._editing_angles):
                if joint_name in self.joint_groups and joint_name in self.joint_trafos:
                    t, r = self.joint_trafos[joint_name](q)
                    self.joint_groups[joint_name].move(*t).rotate(*r)
            # Sync robot_state so readouts and control panel update
            self._sync_robot_state_from_editing()
            # Update edit bar delta values
            if self._current_editing_type:
                self._update_edit_bar_values(self._current_editing_type)
