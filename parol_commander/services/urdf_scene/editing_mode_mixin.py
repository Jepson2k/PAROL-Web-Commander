"""
Editing Mode Mixin for UrdfScene.

Provides target editing mode functionality:
- Enter/exit editing mode (changes robot appearance and joint control)
- Joint angle manipulation via rotation rings
- TCP ball for IK-driven positioning
- Preserves pre-edit state for restoration on exit

This replaces the old GhostRobotMixin - instead of building a duplicate ghost
robot, we now reuse the main robot with appearance mode changes.
"""

import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np

from parol_commander.common.logging_config import TRACE_ENABLED
from parol_commander.state import robot_state
from .ik_solver import EditingIKSolver

from .config import RobotAppearanceMode
from .loader import normalize_axis


class EditingModeMixin:
    """Mixin providing target editing mode functionality for UrdfScene."""

    # These attributes are defined in the main UrdfScene class
    scene: Any
    urdf_model: Any
    urdf_path: Any
    joint_names: List[str]
    joint_axes: Dict[str, np.ndarray]
    joint_groups: Dict[str, Any]
    joint_trafos: Dict
    joint_pos_limits: Dict[str, Dict[str, Optional[float]]]
    _stl_scale: float
    _robot_meshes: List[Any]
    _appearance_mode: RobotAppearanceMode
    _editing_angles: List[float]
    _pre_edit_angles: List[float]
    _editing_target_type: str
    _joint_ring_touched: bool
    _tcp_ball: Any
    _tcp_ball_dragging: bool
    _tcp_fk_solver: Optional[EditingIKSolver]
    config: Any
    
    # Methods from other mixins/classes (declared for type checking)
    def set_appearance_mode(self, mode: RobotAppearanceMode) -> None: ...
    def _ensure_tcp_ball(self) -> None: ...
    def _update_tcp_ball_position(self) -> None: ...
    def enable_tcp_transform_controls(self, mode: str = "translate") -> None: ...
    
    # Optional attributes from TargetEditorMixin
    _unified_target_mode: str
    _current_editing_type: Optional[str]
    def _sync_robot_state_from_editing(self) -> None: ...
    def _update_edit_bar_values(self, edit_type: str) -> None: ...
    def _update_edit_bar_mode_indicator(self) -> None: ...

    def _init_editing_mode_state(self) -> None:
        """Initialize editing mode state variables."""
        # Joint transform control groups (TransformControls per joint)
        self._joint_control_groups: Dict[int, Any] = {}

        # Flag indicating joint TransformControls are temporarily disabled while TCP ball is active
        self._joint_controls_suspended: bool = False

    def enter_editing_mode(self, joint_angles: List[float]) -> None:
        """Enter editing mode at specified joint angles.

        Saves current robot state, switches to editing appearance, and
        positions the robot at the target angles.

        Args:
            joint_angles: List of 6 joint angles in radians
        """
        # Save current live angles for restoration on exit
        try:
            angles_deg = list(getattr(robot_state, "angles", [])) or []
            while len(angles_deg) < 6:
                angles_deg.append(0.0)
            self._pre_edit_angles = [math.radians(float(a)) for a in angles_deg[:6]]
        except Exception:
            self._pre_edit_angles = [0.0] * 6

        # Set editing angles
        self._editing_angles = list(joint_angles) + [0.0] * (6 - len(joint_angles))
        self._editing_angles = self._editing_angles[:6]

        # Reset target type tracking
        self._editing_target_type = "cartesian"
        self._joint_ring_touched = False

        # Enable editing mode - readouts/control panel will show editing values
        robot_state.editing_mode = True

        # Switch to editing appearance
        self.set_appearance_mode(RobotAppearanceMode.EDITING)

        # Apply editing angles to main robot
        self._apply_joint_angles(self._editing_angles)

        # Ensure TCP ball exists and is visible
        self._ensure_tcp_ball()
        if self._tcp_ball:
            self._tcp_ball.visible(True)
            # Change color to blue for editing mode
            self._tcp_ball.material("#4a63e0", 0.9)

        # Position TCP ball at end effector
        self._update_tcp_ball_position()

        # Enable TransformControls on TCP ball
        self.enable_tcp_transform_controls("translate")

        logging.debug("Entered editing mode with angles: %s", self._editing_angles)

    def exit_editing_mode(self) -> None:
        """Exit editing mode and restore pre-edit robot state."""
        # Disable joint transform controls
        self._disable_joint_transform_controls()

        # Restore TCP ball to grey for normal mode
        if self._tcp_ball:
            self._tcp_ball.material("#666666", 0.9)

        # Restore pre-edit angles to robot
        self._apply_joint_angles(self._pre_edit_angles)

        # Restore appearance based on whether we should be in simulator mode
        # Check if simulator is active to determine correct mode
        if robot_state.simulator_active:
            self.set_appearance_mode(RobotAppearanceMode.SIMULATOR)
        else:
            self.set_appearance_mode(RobotAppearanceMode.LIVE)

        # Clear editing state
        self._joint_ring_touched = False
        self._editing_target_type = "cartesian"
        self._joint_controls_suspended = False

        # Disable editing mode - readouts/control panel return to showing robot values
        robot_state.editing_mode = False

        logging.debug("Exited editing mode, restored pre-edit angles")

    def _apply_joint_angles(self, angles_rad: List[float]) -> None:
        """Apply joint angles to the main robot joint groups.

        Args:
            angles_rad: Joint angles in radians, ordered by self.joint_names
        """
        for joint_name, q in zip(self.joint_names, angles_rad):
            if joint_name in self.joint_groups and joint_name in self.joint_trafos:
                t, r = self.joint_trafos[joint_name](q)
                self.joint_groups[joint_name].move(*t).rotate(*r)

    def _get_joint_axes_letters(self) -> list[str]:
        """Derive the rotation axis letters for each joint.

        Prefers persisted normalized axes captured during scene build.
        Falls back to URDF joint.axis if missing.
        """
        axis_letters: list[str] = []
        for joint_name in self.joint_names[:6]:
            # Prefer persisted axis
            if joint_name in self.joint_axes:
                vec = self.joint_axes[joint_name]
            else:
                # Fallback to URDF
                joint = next(
                    (j for j in self.urdf_model.joints if j.name == joint_name), None
                )
                raw_axis = getattr(joint, "axis", None) if joint is not None else None
                vec = normalize_axis(raw_axis)

            # Principal component by absolute value
            idx = int(np.argmax(np.abs(vec[:3])))
            letter = ["X", "Y", "Z"][idx]
            axis_letters.append(letter)
        return axis_letters

    def _get_joint_limits(self) -> list[tuple[float, float]]:
        """Get joint limits for IK chain.

        Returns list of (min, max) tuples in radians.
        """
        limits = []
        for joint_name in self.joint_names[:6]:
            joint_limits = self.joint_pos_limits.get(joint_name, {})
            min_val = joint_limits.get("min", -math.pi)
            max_val = joint_limits.get("max", math.pi)
            limits.append(
                (
                    min_val if min_val is not None else -math.pi,
                    max_val if max_val is not None else math.pi,
                )
            )
        return limits

    def _ik_for_position(self, target_pos: List[float]) -> Optional[List[float]]:
        """Solve IK to find joint angles that reach the target position.

        Args:
            target_pos: Target TCP position [x, y, z] in meters

        Returns:
            Joint angles in radians, or None if IK fails
        """
        # Ensure IK solver is initialized
        if not self._tcp_fk_solver:
            try:
                self._tcp_fk_solver = EditingIKSolver.from_urdf_scene(self)
            except Exception as e:
                logging.error("Failed to initialize IK solver for position: %s", e)
                return None

        # Use current robot angles as seed
        angles_deg = list(robot_state.angles) if robot_state.angles else [0.0] * 6
        while len(angles_deg) < 6:
            angles_deg.append(0.0)
        current_angles = [math.radians(a) for a in angles_deg[:6]]

        # Solve IK
        result = self._tcp_fk_solver.solve(
            target_pos=np.array(target_pos),
            current_angles=current_angles,
            throttle=False,  # Don't throttle for initial positioning
        )

        if result and result.success:
            return list(result.angles)
        else:
            logging.warning("IK solve failed for position %s", target_pos)
            return None

    def _enable_joint_transform_controls(self) -> None:
        """Enable TransformControls (rotate) on each main robot joint group."""
        if not self.scene or not self.joint_groups:
            return
        # Reset map
        self._joint_control_groups = {}
        axes = self._get_joint_axes_letters()

        # 5 degrees in radians for rotation snap
        rotation_snap_radians = math.radians(5.0)

        for i, joint_name in enumerate(self.joint_names[:6]):
            group = self.joint_groups.get(joint_name)
            if not group:
                continue
            # Name the group so transform events can be identified
            if hasattr(group, "with_name"):
                group.with_name(f"edit_joint_group:{i}")
            axis = axes[i] if i < len(axes) else "Z"
            group_id = str(group.id)
            # Enable TransformControls rotate with only the joint's axis visible
            self.scene.enable_transform_controls(
                group_id,
                mode="rotate",
                size=0.6,
                visible_axes=[axis],
            )
            # Ensure rotation happens in joint local space (align with URDF axis)
            if hasattr(self.scene, "set_transform_space"):
                self.scene.set_transform_space(group_id, "local")
            # Set 5 deg rotation snap for more controlled joint adjustment
            self.scene.set_transform_rotation_snap(group_id, rotation_snap_radians)
            self._joint_control_groups[i] = group
            logging.debug(
                "Enabled TransformControls (rotate, axis=%s, snap=5 deg) on joint %d (%s)",
                axis,
                i,
                joint_name,
            )

    def _disable_joint_transform_controls(self) -> None:
        """Disable TransformControls on all joint groups."""
        if not self.scene or not self._joint_control_groups:
            return
        for _, group in list(self._joint_control_groups.items()):
            if hasattr(self.scene, "disable_transform_controls"):
                self.scene.disable_transform_controls(group.id)
        self._joint_control_groups.clear()

    def _cleanup_editing(self) -> None:
        """Clean up editing state (disable controls)."""
        # Disable joint TransformControls
        self._disable_joint_transform_controls()

    def _on_joint_group_transform(self, e) -> None:
        """Handle TransformControls rotate events from joint groups."""
        # Only process if in editing mode
        if self._appearance_mode != RobotAppearanceMode.EDITING:
            return

        if not self.scene:
            return

        # Ignore joint ring events while TCP ball is being dragged
        if getattr(self, "_joint_controls_suspended", False):
            return

        # Mark that joint ring was touched - switches target type to "joint"
        if not self._joint_ring_touched:
            self._joint_ring_touched = True
            self._editing_target_type = "joint"
            # Also update unified target mode if in unified editor
            if hasattr(self, "_unified_target_mode"):
                self._unified_target_mode = "joint"
            if hasattr(self, "_update_edit_bar_mode_indicator"):
                self._update_edit_bar_mode_indicator()

        object_name = getattr(e, "object_name", "") or ""

        # Handle both old ghost_ and new edit_ prefixes
        if object_name.startswith("edit_joint_group:"):
            prefix = "edit_joint_group:"
        elif object_name.startswith("ghost_joint_group:"):
            prefix = "ghost_joint_group:"
        else:
            return

        try:
            joint_index = int(object_name.split(prefix)[1])
        except (ValueError, IndexError):
            return

        axes = self._get_joint_axes_letters()
        axis = axes[joint_index] if joint_index < len(axes) else "Z"

        rx = e.rx if e.rx is not None else 0.0
        ry = e.ry if e.ry is not None else 0.0
        rz = e.rz if e.rz is not None else 0.0

        if axis == "X":
            angle_change = rx
        elif axis == "Y":
            angle_change = ry
        else:
            angle_change = rz

        if 0 <= joint_index < len(self._editing_angles):
            self._editing_angles[joint_index] = angle_change
            # Update TCP ball position to follow end effector after joint rotation
            self._update_tcp_ball_position()
            # Sync robot_state and edit bar with new editing values
            self._sync_robot_state_from_editing()
            if self._current_editing_type:
                self._update_edit_bar_values(self._current_editing_type)
            if TRACE_ENABLED:
                logger = logging.getLogger(__name__)
                logger.trace(  # type: ignore[attr-defined]
                    "Joint %d rotated: axis=%s, angle=%.2f rad",
                    joint_index,
                    axis,
                    angle_change,
                )

    def _on_ik_solved(self, e) -> None:
        """Handle IK solution event and update robot visualization.

        This is called when the JavaScript IK solver completes.
        """
        args = e.args if hasattr(e, "args") else {}
        chain_id = args.get("chain_id", "")

        # Only handle ghost IK (editing mode IK)
        if chain_id != "ghost_ik":
            return

        angles = args.get("angles", [])
        if angles:
            # Update editing angles cache - JS solver has already updated object rotations
            self._editing_angles = list(angles)
            logging.debug(
                "IK solved: angles=%s", [f"{math.degrees(a):.1f}" for a in angles]
            )
            # Sync robot_state and edit bar with new editing values
            self._sync_robot_state_from_editing()
            if self._current_editing_type:
                self._update_edit_bar_values(self._current_editing_type)

    def get_joint_ids(self) -> list[str]:
        """Get IDs of the main robot joint groups.

        Returns:
            List of joint group IDs in kinematic order
        """
        result = []
        for joint_name in self.joint_names[:6]:
            if joint_name in self.joint_groups:
                group = self.joint_groups[joint_name]
                result.append(str(group.id) if hasattr(group, "id") else "")
            else:
                result.append("")
        return result

    def _init_ik_solver(self) -> None:
        """Initialize the IK solver for editing mode.

        Creates a EditingIKSolver instance if not already initialized.
        """
        if self._tcp_fk_solver is None:
            try:
                self._tcp_fk_solver = EditingIKSolver.from_urdf_scene(self)
                logging.info(
                    "Initialized IK solver with %d joints",
                    self._tcp_fk_solver.num_joints,
                )
            except Exception as e:
                logging.error("Failed to initialize IK solver: %s", e)
