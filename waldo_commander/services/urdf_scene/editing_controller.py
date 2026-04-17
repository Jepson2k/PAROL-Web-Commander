"""
Editing controller for UrdfScene.

Merged from EditingMixin + TCPControlsMixin — they share 7 instance variables
and 17 cross-calls, so keeping them split artificially required forward
declarations in both directions.  One class eliminates the stubs.

Provides:
- Target editing (enter/exit editing mode, edit bar UI, target CRUD)
- Joint angle manipulation via rotation rings
- TCP TransformControls (gizmo) for IK-driven positioning in EDITING mode
  and direct Cartesian jogging in LIVE/SIMULATOR modes
"""

import asyncio
import logging
import math
from typing import Any, Callable

import numpy as np
from nicegui import ui
from nicegui.helpers import is_user_simulation
from pinokin import arrays_equal_n

from waldo_commander.common.theme import SceneColors
from waldo_commander.state import (
    ProgramTarget,
    robot_state,
    simulation_state,
    ui_state,
)

from .config import RobotAppearanceMode
from .ik_solver import EditingIKSolver
from .loader import normalize_axis

logger = logging.getLogger(__name__)


class EditingController:
    """Target editing + TCP gizmo controller (used as a mixin on UrdfScene).

    The 7 previously-shared state fields (_ik_solver, _tcp_ball,
    _tcp_ball_dragging, _editing_angles, _editing_rotation,
    _editing_rotation_set, _appearance_mode) now all live in this single
    class, so no forward declarations are needed between editing and TCP
    gizmo logic.
    """

    # Attributes still provided by UrdfScene
    scene: Any
    urdf_model: Any
    urdf_path: Any
    joint_names: list[str]
    joint_axes: dict[str, np.ndarray]
    joint_groups: dict[str, Any]
    joint_trafos: dict
    joint_pos_limits: dict[str, dict[str, float | None]]
    _stl_scale: float
    _robot_meshes: list[Any]
    _appearance_mode: RobotAppearanceMode
    _editing_angles: list[float]
    _pre_edit_angles: list[float]
    config: Any
    targets_group: Any
    _scene_wrapper: Any
    _current_tool_offset_z: float

    # Sub-controllers from main class (composition)
    envelope: Any

    # Methods still on the main class
    set_editing_angles: Any
    get_editing_angles: Any
    set_appearance_mode: Any
    _apply_joint_angles: Any

    def _init_editing_state(self) -> None:
        """Initialize all editing state variables."""
        # Joint transform control groups
        self._joint_control_groups: dict[int, Any] = {}
        self._joint_controls_suspended: bool = False

        # Context menu (set from UrdfScene.show() to a ui.context_menu instance)
        self.context_menu: Any = None
        self._last_click_coords: tuple[float, float, float] | None = None
        self._right_click_start_pos: tuple[float, float] | None = None
        self._right_click_drag_threshold: float = 5.0
        self._pending_context_menu_event: Any | None = None

        # Target objects
        self._target_objects: dict[str, dict[str, Any]] = {}

        # Unified target editor state
        self._editing_unified_target: bool = False
        self._editing_target_id: str | None = None
        self._unified_target_mode: str = "cartesian"
        self._joint_ring_touched: bool = False
        self._editing_target_type: str = "cartesian"

        # Original values for delta display
        self._original_editing_pose: list[float] | None = None
        self._original_editing_joints: list[float] | None = None

        # Edit bar UI
        self._edit_bar: Any | None = None
        self._edit_bar_label: Any | None = None
        self._edit_bar_values: Any | None = None
        self._edit_bar_mode_toggle: Any | None = None
        self._edit_bar_container: Any | None = None
        self._current_editing_type: str | None = None

        # Cached values
        self._cached_joint_axes_letters: list[str] | None = None

    # -------------------------------------------------------------------------
    # Core editing mode
    # -------------------------------------------------------------------------

    def enter_editing_mode(self, joint_angles: list[float]) -> None:
        """Enter editing mode at specified joint angles."""
        n = len(self.joint_names)
        # Save current angles for restoration
        self._pre_edit_angles = list(robot_state.angles.rad[:n])

        # Set editing angles (pad or truncate to joint count)
        self._editing_angles = list(joint_angles) + [0.0] * (n - len(joint_angles))
        self._editing_angles = self._editing_angles[:n]

        # Reset state
        self._editing_target_type = "cartesian"
        self._joint_ring_touched = False
        self._editing_rotation_set = False

        robot_state.editing_mode = True
        self.set_appearance_mode(RobotAppearanceMode.EDITING)
        self._apply_joint_angles(self._editing_angles)

        # Force FK recomputation — the cached pose is from LIVE mode
        self.invalidate_fk_cache()

        # Setup TCP ball
        self._ensure_tcp_ball()
        if self._tcp_ball:
            self._tcp_ball.visible(True)
            self._tcp_ball.material(SceneColors.TCP_ACTIVE_HEX, 0.9)
        self._update_tcp_ball_position()
        self.enable_tcp_transform_controls("translate")

    def exit_editing_mode(self) -> None:
        """Exit editing mode and restore pre-edit state."""
        self._disable_joint_transform_controls()

        if self._tcp_ball:
            self._tcp_ball.material(SceneColors.TCP_INACTIVE_HEX, 0.9)

        self._apply_joint_angles(self._pre_edit_angles)

        if robot_state.simulator_active:
            self.set_appearance_mode(RobotAppearanceMode.SIMULATOR)
        else:
            self.set_appearance_mode(RobotAppearanceMode.LIVE)

        self._joint_ring_touched = False
        self._editing_target_type = "cartesian"
        self._joint_controls_suspended = False
        self._editing_rotation_set = False

        robot_state.editing_mode = False

        # Snap TCP ball back to robot's live position
        self.invalidate_fk_cache()
        self._update_tcp_ball_position()

    def _cleanup_editing(self) -> None:
        """Clean up editing state."""
        self._disable_joint_transform_controls()

    # -------------------------------------------------------------------------
    # Joint controls
    # -------------------------------------------------------------------------

    def _get_joint_axes_letters(self) -> list[str]:
        """Get rotation axis letter for each joint (cached)."""
        if self._cached_joint_axes_letters is not None:
            return self._cached_joint_axes_letters

        axis_letters: list[str] = []
        for joint_name in self.joint_names:
            if joint_name in self.joint_axes:
                vec = self.joint_axes[joint_name]
            else:
                joint = next(
                    (j for j in self.urdf_model.joints if j.name == joint_name), None
                )
                raw_axis = getattr(joint, "axis", None) if joint else None
                vec = normalize_axis(raw_axis)
            idx = int(np.argmax(np.abs(vec[:3])))
            axis_letters.append(["X", "Y", "Z"][idx])

        self._cached_joint_axes_letters = axis_letters
        return axis_letters

    def _get_joint_limits(self) -> list[tuple[float, float]]:
        """Get joint limits in radians."""
        limits = []
        for joint_name in self.joint_names:
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

    def _enable_joint_transform_controls(self) -> None:
        """Enable rotation controls on each joint."""
        if not self.scene or not self.joint_groups:
            return
        self._joint_control_groups = {}
        axes = self._get_joint_axes_letters()
        rotation_snap = math.radians(5.0)

        for i, joint_name in enumerate(self.joint_names):
            group = self.joint_groups.get(joint_name)
            if not group:
                continue
            group.with_name(f"edit_joint_group:{i}")
            axis = axes[i] if i < len(axes) else "Z"
            group_id = str(group.id)
            self.scene.enable_transform_controls(
                group_id, mode="rotate", size=0.6, visible_axes=[axis]
            )
            self.scene.set_transform_space(group_id, "local")
            self.scene.set_transform_rotation_snap(group_id, rotation_snap)
            self._joint_control_groups[i] = group

    def _disable_joint_transform_controls(self) -> None:
        """Disable rotation controls on all joints."""
        if not self.scene or not self._joint_control_groups:
            return
        for _, group in list(self._joint_control_groups.items()):
            self.scene.disable_transform_controls(str(group.id))
        self._joint_control_groups.clear()

    def _on_joint_group_transform(self, e) -> None:
        """Handle joint ring rotation events."""
        if self._appearance_mode != RobotAppearanceMode.EDITING:
            return
        if not self.scene or self._joint_controls_suspended:
            return

        if not self._joint_ring_touched:
            self._joint_ring_touched = True
            self._editing_target_type = "joint"
            self._unified_target_mode = "joint"

        object_name = getattr(e, "object_name", "") or ""
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

        angle_change = rx if axis == "X" else (ry if axis == "Y" else rz)

        if 0 <= joint_index < len(self._editing_angles):
            self._editing_angles[joint_index] = angle_change
            self._update_tcp_ball_position()
            self._sync_robot_state_from_editing()
            if self._current_editing_type:
                self._update_edit_bar_values(self._current_editing_type)

    def _on_ik_solved(self, e) -> None:
        """Handle IK solution event."""
        args = e.args if hasattr(e, "args") else {}
        if args.get("chain_id") != "ghost_ik":
            return
        angles = args.get("angles", [])
        if angles is not None and len(angles) >= len(self.joint_names):
            self._editing_angles = list(angles)
            self._sync_robot_state_from_editing()
            if self._current_editing_type:
                self._update_edit_bar_values(self._current_editing_type)

    def apply_editing_home(self) -> None:
        """Move editing robot to home position and sync state/UI."""
        home_rad = ui_state.active_robot.joints.home.rad.tolist()
        self.set_editing_angles(home_rad)
        self._sync_robot_state_from_editing()
        if self._current_editing_type:
            self._update_edit_bar_values(self._current_editing_type)

    # -------------------------------------------------------------------------
    # IK
    # -------------------------------------------------------------------------

    def _ik_for_position(self, target_pos: list[float]) -> list[float] | None:
        """Solve IK for target position. Returns joint angles in radians."""
        if not self._ensure_ik_solver():
            return None
        assert self._ik_solver is not None

        current_angles = self._get_robot_angles_rad()
        result = self._ik_solver.solve(
            target_pos=np.array(target_pos, dtype=np.float64),
            current_angles=current_angles,
            throttle=False,
        )
        if result and result.success:
            return list(result.angles)
        return None

    # -------------------------------------------------------------------------
    # Context menu
    # -------------------------------------------------------------------------

    def _populate_context_menu(self, e) -> None:
        """Populate context menu based on click target."""
        if not self.context_menu:
            return

        hits = getattr(e, "hits", []) or []
        self.context_menu.clear()

        target_id = None
        for h in hits:
            name = getattr(h, "object_name", "") or ""
            if self._is_envelope_hit(name):
                continue
            if name.startswith("target:"):
                target_id = name.split("target:", 1)[1]
                break

        ground_point = getattr(e, "ground_point", None)
        if ground_point:
            self._last_click_coords = (
                float(ground_point.x),
                float(ground_point.y),
                float(ground_point.z),
            )

        with self.context_menu:
            if target_id:
                target = self._find_target_by_id(target_id)
                if target:
                    ui.item(f"Target (Line {target.line_number})").classes(
                        "font-bold text-sm"
                    )
                    ui.separator()
                    tid = target_id

                    def make_edit(t=tid):
                        return lambda: self._show_unified_target_editor(
                            edit_target_id=t
                        )

                    def make_delete(t=tid):
                        return lambda: self._delete_target(t)

                    ui.menu_item("Edit Target...", on_click=make_edit())
                    ui.menu_item("Delete Target", on_click=make_delete())
            else:
                ui.item("Add Target").classes("font-bold text-sm")
                ui.separator()
                ui.menu_item(
                    "Place Target at Robot Position...",
                    on_click=lambda: self._show_unified_target_editor(
                        use_click_position=False
                    ),
                )
                if self._last_click_coords and self._last_click_coords != (
                    0.0,
                    0.0,
                    0.0,
                ):
                    x_mm, y_mm = [c * 1000 for c in self._last_click_coords[:2]]
                    ui.menu_item(
                        f"Place Target Here ({x_mm:.0f}, {y_mm:.0f})...",
                        on_click=lambda: self._show_unified_target_editor(
                            use_click_position=True
                        ),
                    )

    def _is_envelope_hit(self, object_name: str) -> bool:
        """Check if object is the workspace envelope."""
        return object_name == "envelope:hull"

    def _delete_target(self, target_id: str) -> None:
        """Delete a target after confirmation."""

        def confirm():
            ui_state.editor_panel.delete_target_code(target_id)
            dialog.close()

        dialog = ui.dialog()
        with dialog, ui.card():
            ui.label("Delete Target?")
            with ui.row():
                ui.button("Cancel", on_click=dialog.close)
                ui.button("Delete", on_click=confirm, color="negative")
        dialog.open()

    # -------------------------------------------------------------------------
    # Unified target editor
    # -------------------------------------------------------------------------

    def _show_unified_target_editor(
        self,
        use_click_position: bool = False,
        edit_target_id: str | None = None,
    ) -> None:
        """Show target editor for creating or editing targets."""
        if self._editing_unified_target:
            return

        self._editing_unified_target = True
        self._editing_target_id = edit_target_id
        self._unified_target_mode = "cartesian"
        self._joint_ring_touched = False

        # Determine initial angles
        if edit_target_id:
            target = self._find_target_by_id(edit_target_id)
            if target and target.pose:
                self._original_editing_pose = list(target.pose)
                initial_angles = self._ik_for_position(target.pose[:3])
                if initial_angles is None:
                    initial_angles = self._get_robot_angles_rad()
            else:
                initial_angles = self._get_robot_angles_rad()
        elif use_click_position and self._last_click_coords:
            click_pos = list(self._last_click_coords)
            ik_result = self._ik_for_position(click_pos)
            if ik_result is None and abs(click_pos[2]) < 0.001:
                tcp_z = self._get_current_tcp_z()
                if tcp_z is not None:
                    click_pos[2] = tcp_z
                    ik_result = self._ik_for_position(click_pos)
            initial_angles = (
                ik_result if ik_result is not None else self._get_robot_angles_rad()
            )
        else:
            initial_angles = self._get_robot_angles_rad()

        self._original_editing_joints = [math.degrees(a) for a in initial_angles]
        self.enter_editing_mode(initial_angles)

        bar_type = "pose_edit" if edit_target_id else "unified"
        self._create_edit_bar(bar_type)

        async def enable_controls():
            await asyncio.sleep(0.15)
            if not self._editing_unified_target:
                return
            self._ensure_ik_solver()
            self.enable_tcp_transform_controls("translate")
            self._enable_joint_transform_controls()
            self._sync_robot_state_from_editing()

        with self.scene:
            ui.timer(0.0, enable_controls, once=True)

    def _confirm_unified_as_cartesian(self) -> None:
        """Confirm as cartesian target."""
        if not self._editing_unified_target:
            return

        pose = self._get_editing_tcp_pose()
        if pose is None:
            return

        if self._editing_target_id:
            target = self._find_target_by_id(self._editing_target_id)
            if target:
                if self._editing_target_id in self._target_objects:
                    group = self._target_objects[self._editing_target_id].get("group")
                    if group:
                        group.move(pose[0], pose[1], pose[2])
                target.pose = pose
                ui_state.editor_panel.sync_code_from_target(
                    self._editing_target_id, pose
                )
        else:
            self._add_target_with_pose(pose, "cartesian")

        self._end_editing_session()

    def _confirm_unified_as_joint(self) -> None:
        """Confirm as joint target (converts move_l→move_j if editing existing)."""
        if not self._editing_unified_target:
            return

        if self._editing_target_id:
            # Editing existing target — convert to move_j in place
            target = self._find_target_by_id(self._editing_target_id)
            n = len(self.joint_names)
            angles_deg = list(robot_state.angles.deg[:n])
            if target:
                pose = target.pose
                ui_state.editor_panel.sync_code_from_target(
                    self._editing_target_id,
                    pose,
                    move_type="joints",
                    joint_angles_deg=angles_deg,
                )
            self._end_editing_session()
            return

        self._insert_joint_target_from_editing()
        self._end_editing_session()

    def _end_editing_session(self) -> None:
        """End editing and clean up."""
        self._editing_unified_target = False
        self._editing_target_id = None
        self._unified_target_mode = "cartesian"
        self._joint_ring_touched = False
        self._original_editing_joints = None
        self._original_editing_pose = None
        self._cleanup_editing()
        self._hide_edit_bar()
        self.exit_editing_mode()

    def _handle_keyboard(self, e) -> None:
        """Handle keyboard events."""
        if e.key == "Escape" and e.action.keydown:
            if self._editing_unified_target:
                self._end_editing_session()

    # -------------------------------------------------------------------------
    # Edit bar UI
    # -------------------------------------------------------------------------

    def _create_edit_bar(self, editing_type: str) -> None:
        """Create the edit confirmation bar."""
        if self._edit_bar:
            self._update_edit_bar_content(editing_type)
            return

        if not self._edit_bar_container:
            parent: Any = self._scene_wrapper if self._scene_wrapper else ui
            with parent:  # ty: ignore[invalid-context-manager]
                self._edit_bar_container = ui.element("div").classes(
                    "absolute bottom-4 left-1/2 -translate-x-1/2 z-50 pointer-events-auto"
                )

        with self._edit_bar_container:
            self._edit_bar = ui.row().classes(
                "overlay-card items-center gap-3 px-3 py-2"
            )
            with self._edit_bar:
                self._edit_bar_label = ui.label("").classes("text-sm font-medium")
                self._edit_bar_values = ui.row().classes(
                    "gap-2 items-center flex-1 flex-nowrap"
                )
                ui.space()
                ui.button(icon="close", on_click=self._on_edit_bar_cancel).props(
                    "round flat color=red"
                )
                ui.button(icon="check", on_click=self._on_edit_bar_confirm).props(
                    "round color=positive"
                )

        self._current_editing_type = editing_type
        self._update_edit_bar_content(editing_type)
        self._sync_robot_state_from_editing()
        self._update_edit_bar_values(editing_type)

    def _update_edit_bar_content(self, editing_type: str) -> None:
        """Update edit bar label."""
        if not self._edit_bar_label:
            return
        labels = {
            "joint": "Editing Joint Target",
            "unified": "Place Target",
            "pose_edit": "Editing Target Position",
        }
        self._edit_bar_label.text = labels.get(editing_type, "Editing Target")

    def _update_edit_bar_values(self, editing_type: str) -> None:
        """Update edit bar delta values."""
        if not self._edit_bar_values:
            return
        self._edit_bar_values.clear()

        if not self._editing_target_id:
            return

        def fmt(delta: float, unit: str = "") -> str:
            if abs(delta) < 0.1:
                return f"0.0{unit}"
            return f"{'+' if delta > 0 else ''}{delta:.1f}{unit}"

        def color(delta: float) -> str:
            if abs(delta) < 0.1:
                return "text-gray-400"
            return "text-green-400" if delta > 0 else "text-red-400"

        with self._edit_bar_values:
            show_joints = editing_type == "joint" or (
                editing_type == "unified" and self._unified_target_mode == "joint"
            )

            if show_joints:
                # Use pre-computed degrees from robot_state (synced from editing angles)
                n = len(self.joint_names)
                angles_deg = list(robot_state.angles.deg[:n])
                orig_deg = self._original_editing_joints or [0.0] * n
                for i, angle in enumerate(angles_deg):
                    delta = angle - orig_deg[i]
                    ui.label(f"ΔJ{i + 1}: {fmt(delta, '°')}").classes(
                        f"text-xs font-mono whitespace-nowrap {color(delta)}"
                    )
            else:
                tcp_pos_mm = [0.0, 0.0, 0.0]
                if self._ik_solver:
                    fk = self._ik_solver.forward_kinematics(self.get_editing_angles())
                    if fk is not None:
                        offset = self._current_tool_offset_z
                        tcp_pos_mm = [
                            fk[0] * 1000,
                            fk[1] * 1000,
                            (fk[2] + offset) * 1000,
                        ]

                orig_mm = [
                    p * 1000 for p in (self._original_editing_pose or [0.0] * 3)[:3]
                ]
                for i, label in enumerate(["X", "Y", "Z"]):
                    delta = tcp_pos_mm[i] - orig_mm[i]
                    ui.label(f"Δ{label}: {fmt(delta, 'mm')}").classes(
                        f"text-xs font-mono whitespace-nowrap {color(delta)}"
                    )

    def _hide_edit_bar(self) -> None:
        """Hide and clean up the edit bar."""
        self._current_editing_type = None
        if self._edit_bar:
            self._edit_bar.delete()
            self._edit_bar = None
        if self._edit_bar_container:
            self._edit_bar_container.delete()
            self._edit_bar_container = None
        self._edit_bar_label = None
        self._edit_bar_values = None

    def _on_edit_bar_cancel(self) -> None:
        """Handle cancel button."""
        self._end_editing_session()

    def _on_edit_bar_confirm(self) -> None:
        """Handle confirm button."""
        if not self._editing_unified_target:
            return
        if self._unified_target_mode == "joint":
            self._confirm_unified_as_joint()
        else:
            self._confirm_unified_as_cartesian()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _find_target_by_id(self, target_id: str) -> ProgramTarget | None:
        """Find a target by ID in simulation_state.targets."""
        for t in simulation_state.targets:
            if t.id == target_id:
                return t
        return None

    def _get_robot_angles_rad(self) -> list[float]:
        """Get robot angles in radians."""
        n = len(self.joint_names)
        if len(robot_state.angles) >= n:
            return list(robot_state.angles.rad[:n])
        return [0.0] * n

    def _get_current_tcp_z(self) -> float | None:
        """Get current TCP z position in meters via FK."""
        if not self._ensure_ik_solver() or not self._ik_solver:
            return None
        fk = self._ik_solver.forward_kinematics(self._get_robot_angles_rad())
        return float(fk[2]) if fk is not None else None

    def _get_editing_tcp_pose(self) -> list[float] | None:
        """Get TCP pose from robot_state (already synced from editing angles).

        Returns [x, y, z, rx, ry, rz] with position in meters and rotation in degrees.
        """
        # Subtract tool offset since sync adds it to z
        offset = self._current_tool_offset_z
        return [
            robot_state.x / 1000,  # mm -> m
            robot_state.y / 1000,
            (robot_state.z / 1000) - offset,
            robot_state.rx,
            robot_state.ry,
            robot_state.rz,
        ]

    def _add_target_with_pose(self, pose: list[float], move_type: str) -> None:
        """Add target and insert code."""
        pose_mm = [
            pose[0] * 1000,
            pose[1] * 1000,
            pose[2] * 1000,
            pose[3] if len(pose) > 3 else 0.0,
            pose[4] if len(pose) > 4 else 0.0,
            pose[5] if len(pose) > 5 else 0.0,
        ]

        line_number = ui_state.editor_panel.add_target_code(pose_mm, move_type)
        if line_number:
            new_target = ProgramTarget(
                id=f"pending_{line_number}",
                line_number=line_number,
                pose=pose,
                move_type=move_type,
                scene_object_id="",
            )
            simulation_state.targets.append(new_target)
            simulation_state.notify_changed()

    def _insert_joint_target_from_editing(self) -> None:
        """Insert joint target code."""
        # Use pre-computed degrees from robot_state (synced from editing angles)
        n = len(self.joint_names)
        ui_state.editor_panel.add_joint_target_code(list(robot_state.angles.deg[:n]))

    def _sync_robot_state_from_editing(self) -> None:
        """Sync robot_state with editing values."""
        if not robot_state.editing_mode:
            return

        try:
            angles_rad = self.get_editing_angles()
            robot_state.angles.set_rad(np.asarray(angles_rad))

            if self._ik_solver:
                fk = self._ik_solver.forward_kinematics(angles_rad)
                if fk is not None:
                    offset = self._current_tool_offset_z
                    robot_state.x = fk[0] * 1000
                    robot_state.y = fk[1] * 1000
                    robot_state.z = (fk[2] + offset) * 1000
                    if len(fk) >= 6:
                        # Set orientation from radians (computes degrees internally)
                        robot_state.orientation.set_rad(np.asarray(fk[3:6]))
                        # Also set scalar fields for UI binding
                        robot_state.rx = robot_state.orientation.deg[0]
                        robot_state.ry = robot_state.orientation.deg[1]
                        robot_state.rz = robot_state.orientation.deg[2]
                    self.envelope.update_from_robot_state()
        except (TypeError, ValueError, AttributeError) as e:
            logger.debug("_sync_robot_state_from_editing failed: %s", e)

    # ============================================================
    # TCP TransformControls (merged from TCPControlsMixin)
    # ============================================================

    @property
    def tcp_transform_mode(self) -> str:
        """Current TCP transform mode ('translate' or 'rotate')."""
        return self._tcp_transform_mode

    def _init_tcp_controls_state(self) -> None:
        """Initialize TCP controls state variables."""
        # TCP TransformControls state
        self._tcp_transform_enabled: bool = False
        self._tcp_enable_in_progress: bool = (
            False  # Guard against concurrent enablement
        )
        self._tcp_transform_mode: str = "translate"  # "translate" or "rotate"

        self._tcp_cartesian_move_callback: Callable[[list[float]], None] | None = None
        self._tcp_cartesian_move_start_callback: Callable[[], None] | None = None
        self._tcp_cartesian_move_end_callback: Callable[[], None] | None = None
        self._tcp_drag_start_rot_deg: tuple[float, float, float] | None = None
        self._tcp_ball: Any | None = None
        self._tcp_ball_dragging: bool = False
        self._ik_solver: EditingIKSolver | None = None
        self._editing_rotation: list[float] = [0.0, 0.0, 0.0]
        self._editing_rotation_set: bool = False  # Track if rotation was explicitly set

        # Pre-allocated buffers to avoid per-call allocations
        self._target_pos_buffer: np.ndarray = np.zeros(3, dtype=np.float64)
        self._target_orientation_buffer: np.ndarray = np.zeros(3, dtype=np.float64)
        self._pose_mm_buffer: list[float] = [0.0] * 6

        # FK dirty checking cache - skip FK when angles unchanged
        self._last_fk_angles_tuple: tuple[float, ...] | None = None
        self._last_fk_angles_raw: np.ndarray | None = (
            None  # For fast LIVE mode comparison
        )
        self._last_fk_pose: tuple[float, ...] | None = None  # cached FK result

    def _ensure_ik_solver(self) -> EditingIKSolver | None:
        """Lazy-initialize the FK/IK solver, returning it or None on failure."""
        if self._ik_solver is None:
            try:
                self._ik_solver = EditingIKSolver.from_urdf_scene(self)
            except Exception as e:
                logger.warning("FK/IK solver init failed: %s", e)
        return self._ik_solver

    def invalidate_fk_cache(self) -> None:
        """Force TCP ball FK recomputation on next update cycle."""
        self._last_fk_angles_tuple = None
        self._last_fk_angles_raw = None

    def on_tcp_cartesian_move(self, callback: Callable[[list[float]], None]) -> None:
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
        # Guard against concurrent enablement attempts
        if self._tcp_transform_enabled or self._tcp_enable_in_progress:
            return

        if not self.scene:
            logger.warning("Cannot enable TCP transform controls: scene not available")
            return
        # Ensure jog ball exists
        self._ensure_tcp_ball()
        if not self._tcp_ball:
            logger.warning(
                "Cannot enable TCP transform controls: jog ball not available"
            )
            return

        # Mark enablement in progress to prevent concurrent attempts
        self._tcp_enable_in_progress = True

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
                logger.debug(
                    "User simulation: jog ball visibility update failed: %s", e
                )
            logger.debug(
                "User simulation detected; skipping TCP TransformControls enablement"
            )
            self._tcp_enable_in_progress = False
            return

        # Enable TransformControls with a short Python-side retry until the object exists on JS
        async def _enable_with_retry():
            try:
                attempts = 20  # ~1s total at 50ms intervals
                for _ in range(attempts):
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
                        self._tcp_transform_enabled = True
                        logger.debug("Enabled TCP TransformControls in %s mode", mode)
                        return
                logger.warning("Failed to enable TCP TransformControls after retries")
            except (TimeoutError, asyncio.CancelledError):
                # Scene is shutting down, bail out gracefully
                logger.debug("TCP TransformControls enablement cancelled (shutdown)")
            finally:
                self._tcp_enable_in_progress = False

        # Use explicit scene context to avoid stale slot errors
        with self.scene:
            ui.timer(0.0, _enable_with_retry, once=True)

    def disable_tcp_transform_controls(self) -> None:
        """Disable TransformControls on the TCP anchor."""
        if not self.scene or not self._tcp_ball:
            return

        tcp_object_id = str(self._tcp_ball.id)

        self.scene.disable_transform_controls(tcp_object_id)

        # Hide jog ball when controls are disabled
        if self._tcp_ball:
            self._tcp_ball.visible(False)

        self._tcp_transform_enabled = False
        self._tcp_enable_in_progress = False  # Reset guard on disable
        logger.debug("Disabled TCP TransformControls")

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

        self.scene.set_transform_mode(tcp_object_id, self._tcp_transform_mode)

        logger.debug("Changed TCP TransformControls mode to %s", mode)

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
                radius=0.008,
                width_segments=16,
                height_segments=16,
                wireframe=False,
            ).with_name("tcp:ball")
            ball.material(SceneColors.EDIT_GRAY_HEX, 0.9)
            self._tcp_ball = ball
        # Initial position/orientation based on mode
        self._update_tcp_ball_position()

    def _snap_tcp_to_fk(self) -> None:
        """Snap TCP ball back to FK position from current editing angles.

        Called on transform_end in editing mode to correct the ball
        position when IK failed and the ball was dragged to an
        unreachable position.
        """
        self.invalidate_fk_cache()
        self._update_tcp_ball_position()

    def _update_tcp_ball_position(self) -> None:
        """Update TCP ball position from FK, with drift correction.

        Recomputes FK only when joint angles change.  If angles are
        unchanged but the ball was moved (e.g. by a drag), the cached
        pose is re-applied without recomputing FK.
        """
        if self._tcp_ball_dragging or not self._tcp_ball:
            return

        # Determine whether angles changed since last FK
        n = len(self.joint_names)
        angles_changed = False
        if self._appearance_mode == RobotAppearanceMode.EDITING:
            angles_rad: list[float] | np.ndarray = self._editing_angles
            key = tuple(angles_rad[:n])
            if key != self._last_fk_angles_tuple:
                self._last_fk_angles_tuple = key
                angles_changed = True
        else:
            angles_deg = robot_state.angles.deg
            if self._last_fk_angles_raw is None or not arrays_equal_n(
                angles_deg[:n], self._last_fk_angles_raw
            ):
                self._last_fk_angles_raw = angles_deg[:n].copy()
                angles_rad = robot_state.angles.rad
                angles_changed = True

        if angles_changed:
            if not self._ensure_ik_solver() or self._ik_solver is None:
                return
            try:
                ee = self._ik_solver.forward_kinematics(angles_rad)
                self._last_fk_pose = tuple(float(v) for v in ee[:6])
            except Exception as e:
                logger.debug("FK failed: %s", e)
                return

        # Apply pose if ball drifted (or FK just computed a new one)
        p = self._last_fk_pose
        if p and (
            self._tcp_ball.x != p[0]
            or self._tcp_ball.y != p[1]
            or self._tcp_ball.z != p[2]
        ):
            self._tcp_ball.move(p[0], p[1], p[2])
            self._tcp_ball.rotate_euler(p[3], p[4], p[5], "XYZ")

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

        # Record starting rotation on first event of drag session
        if not self._tcp_ball_dragging:
            # Get starting rotation from robot state (pre-computed degrees)
            self._tcp_drag_start_rot_deg = tuple(robot_state.orientation.deg)
            # Mark that TCP ball is being dragged - prevents FK from overwriting position
            self._tcp_ball_dragging = True
            # Notify drag-start to consumers (for jogging mode)
            if self._appearance_mode != RobotAppearanceMode.EDITING:
                if self._tcp_cartesian_move_start_callback:
                    self._tcp_cartesian_move_start_callback()

        # Route to mode-specific handler
        if self._appearance_mode == RobotAppearanceMode.EDITING:
            self._handle_tcp_transform_for_ik(e)
        else:
            self._handle_tcp_transform_for_cartesian(e)

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

            # Use absolute world coordinates
            if self._tcp_cartesian_move_callback:
                # Reuse pre-allocated buffer
                buf = self._pose_mm_buffer
                buf[0] = float(new_x) * 1000.0  # x: m -> mm
                buf[1] = float(new_y) * 1000.0  # y: m -> mm
                buf[2] = float(new_z) * 1000.0  # z: m -> mm
                # Use rotation from drag START to avoid rotation during translation
                if self._tcp_drag_start_rot_deg is not None:
                    buf[3] = self._tcp_drag_start_rot_deg[0]
                    buf[4] = self._tcp_drag_start_rot_deg[1]
                    buf[5] = self._tcp_drag_start_rot_deg[2]
                else:
                    buf[3] = robot_state.rx
                    buf[4] = robot_state.ry
                    buf[5] = robot_state.rz
                self._tcp_cartesian_move_callback(buf)

        else:  # rotate mode
            # Use absolute rotation values
            if self._tcp_cartesian_move_callback:
                # Get rotation values (radians from event)
                rx = e.rx if e.rx is not None else 0.0
                ry = e.ry if e.ry is not None else 0.0
                rz = e.rz if e.rz is not None else 0.0

                # Reuse pre-allocated buffer
                buf = self._pose_mm_buffer
                buf[0] = robot_state.x  # Keep current position (already in mm)
                buf[1] = robot_state.y
                buf[2] = robot_state.z
                buf[3] = math.degrees(rx)  # rx: rad -> deg
                buf[4] = math.degrees(ry)  # ry: rad -> deg
                buf[5] = math.degrees(rz)  # rz: rad -> deg
                self._tcp_cartesian_move_callback(buf)

    def _handle_tcp_transform_for_ik(self, e) -> None:
        """Handle TCP ball drag in EDITING mode - solve IK for position and/or orientation."""
        # Ensure FK/IK solver is initialized
        if not self._ensure_ik_solver():
            return
        assert self._ik_solver is not None

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
            self._editing_rotation[0] = float(rx)
            self._editing_rotation[1] = float(ry)
            self._editing_rotation[2] = float(rz)
            self._editing_rotation_set = True

            # Get current position from FK (maintain position while rotating)
            fk_result = self._ik_solver.forward_kinematics(self._editing_angles)
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
            if self._editing_rotation_set:
                orient_buf = self._target_orientation_buffer
                orient_buf[0] = self._editing_rotation[0]
                orient_buf[1] = self._editing_rotation[1]
                orient_buf[2] = self._editing_rotation[2]
                target_orientation = orient_buf

        # Solve IK with throttling (~30Hz)
        result = self._ik_solver.solve(
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
            n = len(self.joint_names)
            self._editing_angles[:n] = result.angles[:n]
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
