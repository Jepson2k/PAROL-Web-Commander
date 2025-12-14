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
from nicegui import ui
from nicegui.helpers import is_user_simulation

from parol_commander.state import robot_state
from .ik_solver import EditingIKSolver

from .config import RobotAppearanceMode


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
    def _sync_robot_state_from_editing(self) -> None:
        ...

    def _update_edit_bar_values(self, editing_type: str) -> None:
        ...

    _current_editing_type: Optional[str]

    def _init_tcp_controls_state(self) -> None:
        """Initialize TCP controls state variables."""
        # TCP TransformControls state
        self._tcp_transform_enabled: bool = False
        self._tcp_transform_mode: str = "translate"  # "translate" or "rotate"
        self._tcp_last_position: Optional[Tuple[float, float, float]] = None
        self._tcp_last_rotation: Optional[Tuple[float, float, float]] = None

        # Direct Cartesian move callback (pose in mm/degrees) - used in LIVE/SIMULATOR
        self._tcp_cartesian_move_callback: Optional[
            Callable[[List[float]], None]
        ] = None
        # Drag-start callback to signal start of a TCP TransformControls operation
        self._tcp_cartesian_move_start_callback: Optional[Callable[[], None]] = None
        # Drag-end callback to signal end of a TCP TransformControls operation
        self._tcp_cartesian_move_end_callback: Optional[Callable[[], None]] = None

        # Unified TCP ball - behavior depends on _appearance_mode
        # LIVE/SIMULATOR: jog ball for Cartesian moves
        # EDITING: IK target ball for positioning
        self._tcp_ball: Any | None = None
        self._tcp_ball_dragging: bool = False

        # Control frame
        self._control_frame: str = "WRF"  # 'TRF' or 'WRF'

        # Python-side FK/IK solver (shared for jog ball positioning and IK)
        self._tcp_fk_solver: Optional[EditingIKSolver] = None

    def on_tcp_cartesian_move(self, callback: Callable[[List[float]], None]) -> None:
        """Register callback to receive absolute TCP position for Cartesian moves.

        The callback receives pose as [x_mm, y_mm, z_mm, rx_deg, ry_deg, rz_deg].
        This is used for drag-to-move functionality where the robot should move
        directly to the dragged position.

        Args:
            callback: Function to call with target pose in mm/degrees
        """
        self._tcp_cartesian_move_callback = callback

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

    def set_control_frame(self, frame: str) -> None:
        """Set the control frame for TCP movements.

        Args:
            frame: Either "WRF" for world reference frame or "TRF" for tool reference frame
        """
        frame = (frame or "").upper()
        if frame not in ("WRF", "TRF"):
            raise ValueError(f"Invalid frame: {frame}. Must be 'WRF' or 'TRF'.")
        self._control_frame = frame
        # Update TransformControls frame
        if self._tcp_transform_enabled:
            self.set_tcp_transform_frame(frame)

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
                        # Make sure jog ball is visible
                        if self._tcp_ball:
                            self._tcp_ball.visible(True)
                        # Set transform space
                        if hasattr(self.scene, "set_transform_space"):
                            space = "local" if self._control_frame == "TRF" else "world"
                            self.scene.set_transform_space(tcp_object_id, space)
                        # Store initial position/rotation for delta calculation
                        self._tcp_last_position = None
                        self._tcp_last_rotation = None
                        self._tcp_transform_enabled = True
                        space_for_log = (
                            "world"
                            if self._tcp_transform_mode == "translate"
                            else ("local" if self._control_frame == "TRF" else "world")
                        )
                        logging.debug(
                            f"Enabled TCP TransformControls in {mode} mode, space={space_for_log}"
                        )
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

        if hasattr(self.scene, "set_transform_mode"):
            self.scene.set_transform_mode(tcp_object_id, self._tcp_transform_mode)
        # Update space to mirror joint edit behavior
        if hasattr(self.scene, "set_transform_space"):
            space = "local" if self._control_frame == "TRF" else "world"
            self.scene.set_transform_space(tcp_object_id, space)

        # Reset delta tracking
        self._tcp_last_position = None
        self._tcp_last_rotation = None

        logging.debug(f"Changed TCP TransformControls mode to {mode}")

    def set_tcp_transform_frame(self, frame: str) -> None:
        """Update TransformControls space based on control frame.

        Args:
            frame: "WRF" or "TRF"
        """
        if not self._tcp_transform_enabled:
            return

        if not self.scene or not self._tcp_ball:
            return

        # Do not change space while dragging
        if self._tcp_ball_dragging:
            return

        tcp_object_id = str(self._tcp_ball.id)
        space = "local" if frame == "TRF" else "world"

        if hasattr(self.scene, "set_transform_space"):
            self.scene.set_transform_space(tcp_object_id, space)

        logging.debug(f"Changed TCP TransformControls space to {space}")

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
            ball.material("#666666", 0.9)
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

        # Get angles based on mode
        if self._appearance_mode == RobotAppearanceMode.EDITING:
            # Use editing angles (already in radians)
            angles_rad = list(self._editing_angles)
        else:
            # Use live robot angles (deg -> rad)
            try:
                angles_deg = list(getattr(robot_state, "angles", [])) or []
            except Exception:
                angles_deg = []
            while len(angles_deg) < 6:
                angles_deg.append(0.0)
            angles_rad = [math.radians(float(a)) for a in angles_deg[:6]]

        try:
            ee_pos = self._tcp_fk_solver.forward_kinematics(angles_rad)
            self._tcp_ball.move(float(ee_pos[0]), float(ee_pos[1]), float(ee_pos[2]))
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

        # Mark that TCP ball is being dragged - prevents FK from overwriting position
        self._tcp_ball_dragging = True

        # Route to mode-specific handler
        if self._appearance_mode == RobotAppearanceMode.EDITING:
            self._handle_tcp_transform_for_ik(e)
        else:
            self._handle_tcp_transform_for_cartesian(e)

    def _handle_tcp_transform_for_cartesian(self, e) -> None:
        """Handle TCP ball drag in LIVE/SIMULATOR mode - stream Cartesian moves."""
        # Get current position from event (world coordinates)
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

            # Use Cartesian move callback if registered
            if self._tcp_cartesian_move_callback:
                # Convert from meters to mm, keep current rotation from robot state
                pose_mm = [
                    current_pos[0] * 1000.0,  # x: m -> mm
                    current_pos[1] * 1000.0,  # y: m -> mm
                    current_pos[2] * 1000.0,  # z: m -> mm
                    robot_state.rx,  # Keep current orientation
                    robot_state.ry,
                    robot_state.rz,
                ]
                self._tcp_cartesian_move_callback(pose_mm)

        else:  # rotate mode
            # Get rotation values (radians)
            rx = e.rx if e.rx is not None else 0.0
            ry = e.ry if e.ry is not None else 0.0
            rz = e.rz if e.rz is not None else 0.0

            current_rot = (float(rx), float(ry), float(rz))
            # Record last rotation (no deadband filtering)
            self._tcp_last_rotation = current_rot

            # Use Cartesian move callback if registered
            if self._tcp_cartesian_move_callback:
                # Keep current position, use rotated orientation (convert radians to degrees)
                pose_mm = [
                    robot_state.x,  # Keep current position (already in mm)
                    robot_state.y,
                    robot_state.z,
                    math.degrees(current_rot[0]),  # rx: rad -> deg
                    math.degrees(current_rot[1]),  # ry: rad -> deg
                    math.degrees(current_rot[2]),  # rz: rad -> deg
                ]
                self._tcp_cartesian_move_callback(pose_mm)

    def _handle_tcp_transform_for_ik(self, e) -> None:
        """Handle TCP ball drag in EDITING mode - solve IK to update editing angles."""
        # Get new TCP position from transform event (world coordinates)
        new_x = getattr(e, "wx", None)
        new_y = getattr(e, "wy", None)
        new_z = getattr(e, "wz", None)

        if new_x is None or new_y is None or new_z is None:
            # Fallback to local coordinates
            new_x = e.x if e.x is not None else 0.0
            new_y = e.y if e.y is not None else 0.0
            new_z = e.z if e.z is not None else 0.0

        target_pos = np.array([float(new_x), float(new_y), float(new_z)])

        # Ensure FK/IK solver is initialized
        if self._tcp_fk_solver is None:
            try:
                self._tcp_fk_solver = EditingIKSolver.from_urdf_scene(self)
            except Exception as err:
                logging.warning("IK solver init failed: %s", err)
                return

        # Solve IK with throttling (~30Hz)
        result = self._tcp_fk_solver.solve(
            target_pos=target_pos,
            current_angles=self._editing_angles,
            throttle=True,
        )

        # If throttled (returns None), skip this frame
        if result is None:
            return

        if result.success:
            # Update editing angles without repositioning TCP ball (user is dragging it)
            self._editing_angles = list(result.angles)
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
