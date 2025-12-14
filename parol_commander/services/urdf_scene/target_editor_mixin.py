"""
Target Editor Mixin for UrdfScene.

Provides target editing functionality:
- Context menu for right-click actions
- Edit bar for target confirmation
- Target CRUD operations (add, edit, delete)
- Unified/joint/pose target editors
"""

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from nicegui import ui

from parol_commander.state import (
    simulation_state,
    ProgramTarget,
    ui_state,
    robot_state,
)


class TargetEditorMixin:
    """Mixin providing target editing functionality for UrdfScene."""

    # These attributes are defined in the main UrdfScene class or other mixins
    scene: Any
    targets_group: Any
    _target_objects: Dict[str, Dict[str, Any]]
    # Editing mode attributes (from EditingModeMixin)
    _editing_angles: List[float]
    _pre_edit_angles: List[float]
    _editing_target_type: str
    _joint_ring_touched: bool
    _tcp_fk_solver: Any
    _tcp_ball: Any
    _original_editing_joints: Optional[List[float]]
    _original_editing_pose: Optional[List[float]]
    context_menu: Any
    _scene_wrapper: Any
    # Target editor state
    _right_click_start_pos: Tuple[float, float] | None
    _editing_target_id: Optional[str]

    # Method references from other mixins (type annotations only - no method body!)
    # These are provided by EditingModeMixin, TCPControlsMixin, or UrdfScene
    enter_editing_mode: Any  # (joint_angles: List[float]) -> None
    exit_editing_mode: Any  # () -> None
    get_editing_angles: Any  # () -> List[float]
    _init_ik_solver: Any  # () -> None
    enable_tcp_transform_controls: Any  # (mode: str) -> None
    _enable_joint_transform_controls: Any  # () -> None
    _disable_joint_transform_controls: Any  # () -> None
    _cleanup_editing: Any  # () -> None
    _ik_for_position: Any  # (target_pos: List[float]) -> Optional[List[float]]

    def _init_target_editor_state(self) -> None:
        """Initialize target editor state variables."""
        # Context menu for right-click actions
        self.context_menu: Any | None = None
        self._last_click_coords: Tuple[float, float, float] | None = None
        # Right-click drag detection (screen coordinates)
        self._right_click_start_pos: Tuple[float, float] | None = None
        self._right_click_drag_threshold: float = 5.0  # pixels
        self._pending_context_menu_event: Any | None = None

        # Target objects tracking
        self._target_objects: Dict[str, Dict[str, Any]] = {}

        # Transform mode for targets (controlled via UI)
        self._target_transform_mode: str = "translate"  # 'translate' or 'rotate'

        # Track which target is currently being edited (has TransformControls visible)
        self._editing_target_id: Optional[str] = None

        # Track joint target editing mode (editing mode with rings visible)
        self._editing_joint_target: bool = False

        # Unified target editor state (consolidates cart + joint target placement)
        self._editing_unified_target: bool = False
        self._unified_target_mode: str = "cartesian"  # "cartesian" or "joint"
        self._joint_ring_touched: bool = False  # True when any joint ring is rotated

        # Pose target editing via editing mode (editing an existing target)
        self._editing_pose_target: bool = False
        self._editing_pose_target_id: str | None = None  # ID of target being edited

        # Store original values for delta display in edit bar
        self._original_editing_pose: List[float] | None = None  # [x, y, z, rx, ry, rz]
        self._original_editing_joints: List[float] | None = None  # radians

        # Track whether we're editing an existing target vs creating a new one
        self._editing_existing_target: bool = False

        # Edit confirmation bar UI elements
        self._edit_bar: Any | None = None
        self._edit_bar_label: Any | None = None
        self._edit_bar_values: Any | None = None
        self._edit_bar_mode_toggle: Any | None = None
        self._edit_bar_container: Any | None = None  # Parent container for positioning
        self._current_editing_type: str | None = (
            None  # Track editing type for callbacks
        )

    def _populate_context_menu(self, e) -> None:
        """Populate context menu based on what was clicked."""
        if not self.context_menu:
            return

        hits = getattr(e, "hits", []) or []
        self.context_menu.clear()

        # Check if we clicked on a target
        target_id = None
        for h in hits:
            name = getattr(h, "object_name", "") or ""

            # Skip envelope/non-pickable objects
            if self._is_envelope_hit(name):
                continue

            if name.startswith("target:"):
                target_id = name.split("target:", 1)[1]
                break

        # Use ground_point for click coordinates (ray-plane intersection with Z=0)
        ground_point = getattr(e, "ground_point", None)
        if ground_point:
            self._last_click_coords = (
                float(ground_point.x),
                float(ground_point.y),
                float(ground_point.z),
            )

        with self.context_menu:
            if target_id:
                # Clicked on a target - show target-specific options
                target = next(
                    (t for t in simulation_state.targets if t.id == target_id), None
                )
                if target:
                    ui.item(f"Target (Line {target.line_number})").classes(
                        "font-bold text-sm"
                    )
                    ui.separator()
                    # Capture target_id in closures using function factory
                    captured_tid: str = target_id

                    def make_edit_handler(tid: str):
                        return lambda: self._show_edit_target_dialog(tid)

                    def make_delete_handler(tid: str):
                        return lambda: self._delete_target(tid)

                    ui.menu_item(
                        "Edit Target...", on_click=make_edit_handler(captured_tid)
                    )
                    ui.menu_item(
                        "Delete Target", on_click=make_delete_handler(captured_tid)
                    )
            else:
                # Clicked on empty space or robot - show add options
                ui.item("Add Target").classes("font-bold text-sm")
                ui.separator()

                # Option 1: Place at current robot position
                ui.menu_item(
                    "Place Target at Robot Position...",
                    on_click=lambda: self._show_unified_target_editor(
                        use_click_position=False
                    ),
                )

                # Option 2: Place at clicked ground position (if valid)
                if self._last_click_coords and self._last_click_coords != (
                    0.0,
                    0.0,
                    0.0,
                ):
                    x_mm, y_mm, z_mm = [c * 1000 for c in self._last_click_coords]
                    # Capture coords in closure
                    coords = self._last_click_coords
                    ui.menu_item(
                        f"Place Target Here ({x_mm:.0f}, {y_mm:.0f}, {z_mm:.0f})...",
                        on_click=lambda c=coords: self._show_unified_target_editor(
                            use_click_position=True
                        ),
                    )

    def _delete_target(self, target_id: str) -> None:
        """Delete a target after confirmation."""

        def confirm():
            if ui_state.editor_panel:
                ui_state.editor_panel.delete_target_code(target_id)
            dialog.close()

        dialog = ui.dialog()
        with dialog, ui.card():
            ui.label("Delete Target?")
            with ui.row():
                ui.button("Cancel", on_click=dialog.close)
                ui.button("Delete", on_click=confirm, color="negative")
        dialog.open()

    def _show_edit_target_dialog(self, target_id: str) -> None:
        """Show editing mode editor for visually editing a target's position."""
        # Prevent multiple edit sessions
        if (
            self._editing_pose_target
            or self._editing_unified_target
            or self._editing_joint_target
        ):
            return

        target = next((t for t in simulation_state.targets if t.id == target_id), None)
        if not target:
            ui.notify("Target not found", color="negative")
            return

        # Store original pose for delta display
        self._original_editing_pose = list(target.pose) if target.pose else [0.0] * 6
        self._editing_pose_target = True
        self._editing_pose_target_id = target_id
        self._editing_existing_target = True  # Editing existing target shows deltas

        # Position at target's current pose using IK
        target_pose = target.pose  # [x, y, z, rx, ry, rz]
        tcp_pos_meters = [target_pose[0], target_pose[1], target_pose[2]]

        # Find joint angles using IK to reach target position
        initial_angles = self._ik_for_position(tcp_pos_meters)
        if initial_angles is None:
            # Fallback to current robot angles
            angles_deg = list(robot_state.angles) if robot_state.angles else [0.0] * 6
            while len(angles_deg) < 6:
                angles_deg.append(0.0)
            initial_angles = [math.radians(a) for a in angles_deg[:6]]

        # Store original angles for delta display
        self._original_editing_joints = list(initial_angles)

        # Show editing mode at computed position
        self.enter_editing_mode(initial_angles)

        # Show edit bar (same style as unified editor)
        self._create_edit_bar("pose_edit")

        # Initialize IK chain and enable TransformControls after a delay
        async def enable_controls_after_render():
            await asyncio.sleep(0.15)  # Wait for JS to create scene objects
            # Early exit if editing was cancelled before timer fired
            if not self._editing_pose_target:
                logging.debug("Pose target editor controls skipped (editing cancelled)")
                return
            self._init_ik_solver()
            self.enable_tcp_transform_controls("translate")
            self._enable_joint_transform_controls()
            # Re-sync now that FK solver is ready (for x/y/z computation)
            self._sync_robot_state_from_editing()
            logging.info("Pose target editor controls enabled (pose edit mode)")

        # Use scene context to avoid parent slot errors on early cancel
        with self.scene:
            ui.timer(0.0, enable_controls_after_render, once=True)
        logging.info(f"Pose target editor started for target {target_id}")

    def _enable_target_transform_controls(self, target_id: str) -> None:
        """Enable TransformControls on a specific target."""
        if target_id not in self._target_objects:
            logging.warning(f"Target {target_id} not found in scene objects")
            return

        # Disable any previously editing target's TransformControls
        if self._editing_target_id and self._editing_target_id != target_id:
            self._disable_target_transform_controls(self._editing_target_id)

        # Enable TransformControls on this target's group
        target_data = self._target_objects[target_id]
        target_group = target_data["group"]

        if self.scene and hasattr(self.scene, "enable_transform_controls"):
            mode = self._target_transform_mode  # "translate" or "rotate"
            self.scene.enable_transform_controls(target_group.id, mode)
            self._editing_target_id = target_id
            logging.debug(
                f"Enabled TransformControls on target {target_id} with mode {mode}"
            )

    def _disable_target_transform_controls(self, target_id: str) -> None:
        """Disable TransformControls on a specific target."""
        if target_id not in self._target_objects:
            return

        target_data = self._target_objects[target_id]
        target_group = target_data["group"]

        if self.scene and hasattr(self.scene, "disable_transform_controls"):
            self.scene.disable_transform_controls(target_group.id)
            if self._editing_target_id == target_id:
                self._editing_target_id = None
            logging.debug(f"Disabled TransformControls on target {target_id}")

    def _disable_all_target_transform_controls(self) -> None:
        """Disable TransformControls on all targets."""
        if self._editing_target_id:
            self._disable_target_transform_controls(self._editing_target_id)

    def _add_target_at_robot_position(self) -> None:
        """Add a new target at the current robot position."""
        # Get current robot pose from state (convert mm -> m for scene coordinates)
        pose = [
            robot_state.x / 1000.0,  # mm -> m
            robot_state.y / 1000.0,  # mm -> m
            robot_state.z / 1000.0,  # mm -> m
            robot_state.rx,
            robot_state.ry,
            robot_state.rz,
        ]
        self._add_target_with_pose(pose, "pose")

    def _add_target_at_position(self, x: float, y: float, z: float) -> None:
        """Add a new target at the specified 3D position with current robot orientation."""
        # Use clicked position but current robot orientation
        logging.info(f"Adding target at clicked position: ({x:.3f}, {y:.3f}, {z:.3f})")
        pose = [
            x,
            y,
            z,
            robot_state.rx,
            robot_state.ry,
            robot_state.rz,
        ]
        self._add_target_with_pose(pose, "pose")

    def _add_target_with_pose(self, pose: List[float], move_type: str) -> None:
        """Add a new target with the given pose and insert code into editor.

        Args:
            pose: Pose in scene coordinates [x_m, y_m, z_m, rx_deg, ry_deg, rz_deg]
            move_type: Type of move command ("pose", "joints", etc.)
        """
        if not ui_state.editor_panel:
            ui.notify("Editor panel not available", color="negative")
            return

        # Convert pose from scene units (meters) to user units (mm) for code insertion
        pose_for_code = [
            pose[0] * 1000.0 if len(pose) > 0 else 0.0,  # x: m → mm
            pose[1] * 1000.0 if len(pose) > 1 else 0.0,  # y: m → mm
            pose[2] * 1000.0 if len(pose) > 2 else 0.0,  # z: m → mm
            pose[3] if len(pose) > 3 else 0.0,  # rx: degrees (unchanged)
            pose[4] if len(pose) > 4 else 0.0,  # ry: degrees (unchanged)
            pose[5] if len(pose) > 5 else 0.0,  # rz: degrees (unchanged)
        ]

        # Insert code into editor and get marker_id
        marker_id = ui_state.editor_panel.add_target_code(pose_for_code, move_type)

        if marker_id is None:
            ui.notify("Failed to add target code", color="negative")
            return

        # Create target in state
        new_target = ProgramTarget(
            id=marker_id,
            line_number=0,
            pose=pose,
            move_type=move_type,
            scene_object_id="",
        )
        simulation_state.targets.append(new_target)
        simulation_state.notify_changed()

        # Enable TransformControls on the newly created target after a short delay
        async def enable_transform_after_render():
            await asyncio.sleep(0.1)
            # Check if target still exists before enabling controls
            if marker_id not in [t.id for t in simulation_state.targets]:
                logging.debug("Target transform controls skipped (target removed)")
                return
            self._enable_target_transform_controls(marker_id)

        # Use scene context to avoid parent slot errors
        with self.scene:
            ui.timer(0.0, enable_transform_after_render, once=True)

        ui.notify(f"Target added (marker: {marker_id})", color="positive")

    def _add_joint_target_at_robot_position(self) -> None:
        """Add a new joint target at the current robot joint angles."""
        if not ui_state.editor_panel:
            ui.notify("Editor panel not available", color="negative")
            return

        # Get current joint angles from robot state (degrees)
        joint_angles = list(robot_state.angles) if robot_state.angles else [0.0] * 6
        while len(joint_angles) < 6:
            joint_angles.append(0.0)

        # Insert code into editor and get marker_id
        marker_id = ui_state.editor_panel.add_joint_target_code(joint_angles)

        if marker_id is None:
            ui.notify("Failed to add joint target code", color="negative")
            return

        ui.notify(f"Joint target added (marker: {marker_id})", color="positive")

    def _create_edit_bar(self, editing_type: str) -> None:
        """Create and show the bottom edit confirmation bar.

        Args:
            editing_type: "joint" for joint target editing, "pose" for pose target editing
        """
        if self._edit_bar:
            # Already have an edit bar - update it instead
            self._update_edit_bar_content(editing_type)
            return

        # Create the edit bar container inside the scene wrapper for proper positioning
        if not self._edit_bar_container:
            if self._scene_wrapper:
                with self._scene_wrapper:
                    self._edit_bar_container = ui.element("div").classes(
                        "absolute bottom-4 left-1/2 -translate-x-1/2 z-50 pointer-events-auto"
                    )
            else:
                # Fallback: create at current context
                self._edit_bar_container = ui.element("div").classes(
                    "absolute bottom-4 left-1/2 -translate-x-1/2 z-50 pointer-events-auto"
                )

        with self._edit_bar_container:
            self._edit_bar = ui.row().classes(
                "overlay-card items-center gap-3 px-3 py-2"
            )

            with self._edit_bar:
                # Left: Label showing what's being edited
                self._edit_bar_label = ui.label("").classes("text-sm font-medium")

                # Center: Current values display (flex-nowrap prevents wrapping)
                self._edit_bar_values = ui.row().classes(
                    "gap-2 items-center flex-1 flex-nowrap"
                )

                # No mode toggle needed - editing mode arm handles all editing naturally
                self._edit_bar_mode_toggle = None

                # Spacer
                ui.space()

                # Right: Cancel (red X) and Confirm (green checkmark) icon buttons
                ui.button(icon="close", on_click=self._on_edit_bar_cancel).props(
                    "round flat color=red"
                )
                ui.button(icon="check", on_click=self._on_edit_bar_confirm).props(
                    "round color=positive"
                )

        # Set initial content and store editing type for callbacks
        self._current_editing_type = editing_type
        self._update_edit_bar_content(editing_type)

        # Initial sync of robot_state with editing values
        self._sync_robot_state_from_editing()
        self._update_edit_bar_values(editing_type)

    def _sync_robot_state_from_editing(self) -> None:
        """Update robot_state with current editing values.

        This allows the readout panel and control panel to show editing
        values during target editing instead of live robot values.
        """
        if not robot_state.editing_mode:
            logging.debug(
                "_sync_robot_state_from_editing: skipped (editing_mode=False)"
            )
            return

        try:
            # Get editing joint angles
            angles_rad = self.get_editing_angles()
            angles_deg = [math.degrees(a) for a in angles_rad]

            # Update robot_state.angles (degrees)
            logging.debug(
                "_sync_robot_state_from_editing: setting angles to %s",
                [f"{a:.1f}" for a in angles_deg],
            )
            robot_state.angles = angles_deg

            # Compute and update TCP position
            if self._tcp_fk_solver:
                fk_result = self._tcp_fk_solver.forward_kinematics(angles_rad)
                if fk_result is not None:
                    tool_offset = getattr(self, "_current_tool_offset_z", 0.0)
                    robot_state.x = fk_result[0] * 1000  # mm
                    robot_state.y = fk_result[1] * 1000
                    robot_state.z = (fk_result[2] + tool_offset) * 1000
                    # Also update rotation if available (fk_result now has orientation)
                    if len(fk_result) >= 6:
                        robot_state.rx = math.degrees(fk_result[3])
                        robot_state.ry = math.degrees(fk_result[4])
                        robot_state.rz = math.degrees(fk_result[5])
                    logging.debug(
                        "_sync_robot_state_from_editing: x=%.1f, y=%.1f, z=%.1f, rx=%.1f, ry=%.1f, rz=%.1f",
                        robot_state.x,
                        robot_state.y,
                        robot_state.z,
                        robot_state.rx,
                        robot_state.ry,
                        robot_state.rz,
                    )
                    # Update workspace envelope visibility based on new TCP position
                    if hasattr(self, "_update_envelope_from_robot_state"):
                        self._update_envelope_from_robot_state()
                else:
                    logging.debug("_sync_robot_state_from_editing: FK returned None")
            else:
                logging.debug("_sync_robot_state_from_editing: no FK solver available")
        except Exception as e:
            logging.debug("Error in _sync_robot_state_from_editing: %s", e)

    def _update_edit_bar_content(self, editing_type: str) -> None:
        """Update the edit bar content based on editing type."""
        if not self._edit_bar_label:
            return

        if editing_type == "joint":
            self._edit_bar_label.text = "Editing Joint Target"
        elif editing_type == "unified":
            self._edit_bar_label.text = "Place Target"
        elif editing_type == "pose_edit":
            self._edit_bar_label.text = "Editing Target Position"
        else:
            self._edit_bar_label.text = "Editing Target"

    def _update_edit_bar_values(self, editing_type: str) -> None:
        """Update the displayed values in the edit bar.

        Shows deltas when editing an existing target, nothing for new targets
        (since absolute values are shown in readout/control panels).
        """
        if not self._edit_bar_values:
            return

        self._edit_bar_values.clear()

        # Only show deltas when editing an existing target
        is_editing_existing = getattr(self, "_editing_existing_target", False)
        if not is_editing_existing:
            return  # No values to show for new targets

        def format_delta(delta: float, unit: str = "") -> str:
            """Format delta with +/- sign and color."""
            if abs(delta) < 0.1:
                return f"0.0{unit}"
            sign = "+" if delta > 0 else ""
            return f"{sign}{delta:.1f}{unit}"

        def delta_color(delta: float) -> str:
            """Return color class based on delta sign."""
            if abs(delta) < 0.1:
                return "text-gray-400"
            return "text-green-400" if delta > 0 else "text-red-400"

        with self._edit_bar_values:
            # Decide whether to show joint deltas or cartesian deltas
            show_joints = editing_type == "joint" or (
                editing_type == "unified" and self._unified_target_mode == "joint"
            )

            if show_joints:
                # Show joint angle deltas
                angles_rad = self.get_editing_angles()
                angles_deg = [math.degrees(a) for a in angles_rad]
                orig_deg = [0.0] * 6
                if self._original_editing_joints:
                    orig_deg = [math.degrees(a) for a in self._original_editing_joints]

                for i, angle in enumerate(angles_deg[:6]):
                    delta = angle - orig_deg[i]
                    ui.label(f"ΔJ{i + 1}: {format_delta(delta, '°')}").classes(
                        f"text-xs font-mono whitespace-nowrap {delta_color(delta)}"
                    )
            else:  # pose / cartesian
                # Compute TCP position from editing angles via FK
                angles_rad = self.get_editing_angles()
                tcp_pos_mm = [0.0, 0.0, 0.0]

                if self._tcp_fk_solver:
                    try:
                        fk_result = self._tcp_fk_solver.forward_kinematics(angles_rad)
                        if fk_result is not None:
                            tool_offset = getattr(self, "_current_tool_offset_z", 0.0)
                            tcp_pos_mm = [
                                fk_result[0] * 1000,
                                fk_result[1] * 1000,
                                (fk_result[2] + tool_offset) * 1000,
                            ]
                    except Exception:
                        pass

                # Get original position for deltas
                orig_mm = [0.0, 0.0, 0.0]
                if (
                    self._original_editing_pose
                    and len(self._original_editing_pose) >= 3
                ):
                    orig_mm = [p * 1000 for p in self._original_editing_pose[:3]]

                axis_labels = ["X", "Y", "Z"]
                for i in range(3):
                    delta = tcp_pos_mm[i] - orig_mm[i]
                    ui.label(f"Δ{axis_labels[i]}: {format_delta(delta, 'mm')}").classes(
                        f"text-xs font-mono whitespace-nowrap {delta_color(delta)}"
                    )

    def _get_editing_target(self):
        """Get the target currently being edited."""
        target_id = self._editing_target_id or self._editing_pose_target_id
        if not target_id:
            return None
        return next(
            (t for t in simulation_state.targets if t.id == target_id),
            None,
        )

    def _on_edit_bar_cancel(self) -> None:
        """Handle Cancel button click on edit bar."""
        if self._editing_unified_target:
            self._cancel_unified_target_editing()
        elif self._editing_joint_target:
            self._cancel_joint_target_editing()
        elif self._editing_pose_target:
            self._cancel_pose_editing()
        elif self._editing_target_id:
            self._cancel_pose_target_editing()

        self._hide_edit_bar()

    def _on_edit_bar_confirm(self) -> None:
        """Handle Confirm button click on edit bar."""
        if self._editing_unified_target:
            # Confirm based on current mode (cartesian or joint)
            if self._unified_target_mode == "joint":
                self._confirm_unified_as_joint()
            else:
                self._confirm_unified_as_cartesian()
        elif self._editing_joint_target:
            self._confirm_joint_target_from_bar()
        elif self._editing_pose_target:
            self._confirm_pose_editing()
        elif self._editing_target_id:
            self._confirm_pose_target_editing()

        self._hide_edit_bar()

    def _confirm_joint_target_from_bar(self) -> None:
        """Confirm and insert the joint target code from editing mode position (via bar)."""
        if not self._editing_joint_target:
            return

        # Get final joint angles from editing (convert radians to degrees)
        angles_rad = self.get_editing_angles()
        angles_deg_final = [math.degrees(a) for a in angles_rad]

        if ui_state.editor_panel:
            marker_id = ui_state.editor_panel.add_joint_target_code(angles_deg_final)
            if marker_id:
                ui.notify(
                    f"Joint target inserted (marker: {marker_id})", color="positive"
                )

        # Clean up pose editing state
        self._editing_joint_target = False
        self._original_editing_joints = None
        self._cleanup_editing()
        self._hide_edit_bar()
        self.exit_editing_mode()
        logging.debug("Joint target confirmed from bar")

    def _update_edit_bar_mode_indicator(self) -> None:
        """Update the edit bar when mode changes.

        Note: Label no longer shows mode text - just "Place Target".
        This method is kept for compatibility with editing_mode_mixin calls.
        """
        pass  # No-op - label doesn't show mode anymore

    def _show_unified_target_editor(self, use_click_position: bool = False) -> None:
        """Show the unified target editor with editing mode.

        Args:
            use_click_position: If True, solve IK for the clicked ground position.
                              If False (default), use current robot position.

        Defaults to Cartesian output mode. Auto-switches to Joint mode
        if user rotates any joint ring.
        """
        # Already editing? Don't start again
        if self._editing_unified_target or self._editing_joint_target:
            return

        # Reset unified editor state
        self._editing_unified_target = True
        self._unified_target_mode = "cartesian"
        self._joint_ring_touched = False
        self._editing_existing_target = (
            False  # Creating new target, show absolute values
        )

        # Determine initial angles
        if use_click_position and self._last_click_coords:
            # Solve IK for clicked position
            target_pos = list(self._last_click_coords)
            ik_result = self._ik_for_position(target_pos)
            if ik_result:
                initial_angles = ik_result
                logging.info(
                    "Using IK solution for clicked position: %s",
                    [f"{math.degrees(a):.1f}" for a in initial_angles],
                )
            else:
                # Fall back to current robot position if IK fails
                angles_deg = (
                    list(robot_state.angles) if robot_state.angles else [0.0] * 6
                )
                while len(angles_deg) < 6:
                    angles_deg.append(0.0)
                initial_angles = [math.radians(a) for a in angles_deg[:6]]
                logging.warning("IK failed for clicked position, using robot position")
        else:
            # Use current robot angles as starting point (convert degrees to radians)
            angles_deg = list(robot_state.angles) if robot_state.angles else [0.0] * 6
            while len(angles_deg) < 6:
                angles_deg.append(0.0)
            initial_angles = [math.radians(a) for a in angles_deg[:6]]

        # Store original angles for delta display
        self._original_editing_joints = list(initial_angles)

        # Show editing mode at current position
        self.enter_editing_mode(initial_angles)

        # Show edit bar with Cancel/Confirm buttons
        self._create_edit_bar("unified")

        # Initialize IK chain and enable TransformControls after a delay
        async def enable_controls_after_render():
            await asyncio.sleep(0.15)  # Wait for JS to create scene objects
            # Early exit if editing was cancelled before timer fired
            if not self._editing_unified_target:
                logging.debug(
                    "Unified target editor controls skipped (editing cancelled)"
                )
                return
            self._init_ik_solver()
            self.enable_tcp_transform_controls("translate")
            self._enable_joint_transform_controls()
            # Re-sync robot_state now that FK solver is ready
            self._sync_robot_state_from_editing()
            logging.info("Unified target editor controls enabled")

        # Use scene context to avoid parent slot errors on early cancel
        with self.scene:
            ui.timer(0.0, enable_controls_after_render, once=True)
        logging.info("Unified target editor started (mode: cartesian)")

    def _confirm_unified_as_cartesian(self) -> None:
        """Confirm unified target as cartesian (rbt.move_cartesian)."""
        if not self._editing_unified_target:
            return

        # Get TCP position from editing robot FK
        angles_rad = self.get_editing_angles()

        # Use IK solver for FK to get TCP position
        tcp_pos = None
        if self._tcp_fk_solver:
            tcp_pos = self._tcp_fk_solver.forward_kinematics(angles_rad)

        if tcp_pos is None:
            # Fallback: use TCP ball position directly
            if self._tcp_ball:
                tcp_pos = [
                    self._tcp_ball.x,
                    self._tcp_ball.y,
                    self._tcp_ball.z,
                ]
            else:
                ui.notify("Could not determine TCP position", color="negative")
                return

        # Build pose: TCP position (meters) + current robot orientation (degrees)
        pose = [
            tcp_pos[0],  # x in meters
            tcp_pos[1],  # y in meters
            tcp_pos[2],  # z in meters
            robot_state.rx if robot_state.rx is not None else 0.0,
            robot_state.ry if robot_state.ry is not None else 0.0,
            robot_state.rz if robot_state.rz is not None else 0.0,
        ]

        # Insert cartesian target code
        self._add_target_with_pose(pose, "cartesian")

        # Clean up
        self._cancel_unified_target_editing()

    def _confirm_unified_as_joint(self) -> None:
        """Confirm unified target as joint (rbt.move_joints)."""
        if not self._editing_unified_target:
            return

        # Get final joint angles from editing (convert radians to degrees)
        angles_rad = self.get_editing_angles()
        angles_deg_final = [math.degrees(a) for a in angles_rad]

        if ui_state.editor_panel:
            marker_id = ui_state.editor_panel.add_joint_target_code(angles_deg_final)
            if marker_id:
                ui.notify(
                    f"Joint target inserted (marker: {marker_id})", color="positive"
                )

        # Clean up
        self._cancel_unified_target_editing()

    def _cancel_unified_target_editing(self) -> None:
        """Cancel unified target editing and hide editing mode."""
        if not self._editing_unified_target:
            return

        self._editing_unified_target = False
        self._unified_target_mode = "cartesian"
        self._joint_ring_touched = False
        self._original_editing_joints = None
        self._cleanup_editing()
        self._hide_edit_bar()
        self.exit_editing_mode()
        logging.debug("Unified target editor cancelled/closed")

    def _cancel_pose_target_editing(self) -> None:
        """Cancel pose target editing without saving (legacy TransformControls mode)."""
        if not self._editing_target_id:
            return

        # Disable TransformControls
        self._disable_target_transform_controls(self._editing_target_id)
        self._hide_edit_bar()
        self._original_editing_pose = None
        logging.debug("Pose target editing cancelled")

    def _cancel_pose_editing(self) -> None:
        """Cancel pose target editing."""
        if not self._editing_pose_target:
            return

        self._editing_pose_target = False
        self._editing_pose_target_id = None
        self._original_editing_pose = None
        self._original_editing_joints = None
        self._cleanup_editing()
        self._hide_edit_bar()
        self.exit_editing_mode()
        logging.debug("Pose editing cancelled")

    def _confirm_pose_editing(self) -> None:
        """Confirm pose target editing and sync to editor."""
        if not self._editing_pose_target or not self._editing_pose_target_id:
            return

        target = self._get_editing_target()
        if not target:
            ui.notify("Target not found", color="negative")
            self._cancel_pose_editing()
            return

        # Get TCP position from editing robot FK
        angles_rad = self.get_editing_angles()

        # Use IK solver for FK to get TCP position
        tcp_pos = None
        if self._tcp_fk_solver:
            tcp_pos = self._tcp_fk_solver.forward_kinematics(angles_rad)

        if tcp_pos is None:
            # Fallback: use TCP ball position directly
            if self._tcp_ball:
                tcp_pos = [
                    self._tcp_ball.x,
                    self._tcp_ball.y,
                    self._tcp_ball.z,
                ]
            else:
                ui.notify("Could not determine TCP position", color="negative")
                self._cancel_pose_editing()
                return

        # Build new pose: TCP position + original orientation
        original_pose = self._original_editing_pose or [0.0] * 6
        new_pose = [
            tcp_pos[0],  # x in meters
            tcp_pos[1],  # y in meters
            tcp_pos[2],  # z in meters
            original_pose[3] if len(original_pose) > 3 else 0.0,  # rx
            original_pose[4] if len(original_pose) > 4 else 0.0,  # ry
            original_pose[5] if len(original_pose) > 5 else 0.0,  # rz
        ]

        # Update target marker position in scene
        target_id = self._editing_pose_target_id
        if target_id in self._target_objects:
            target_data = self._target_objects[target_id]
            target_group = target_data.get("group")
            if target_group:
                # Move target group to new position (convert to mm for scene)
                target_group.move(
                    tcp_pos[0] * 1000, tcp_pos[1] * 1000, tcp_pos[2] * 1000
                )

        # Update target in simulation_state
        target.pose = new_pose

        # Sync to editor
        if ui_state.editor_panel and hasattr(
            ui_state.editor_panel, "sync_code_from_target"
        ):
            ui_state.editor_panel.sync_code_from_target(target_id, new_pose)
            ui.notify("Target position updated", color="positive")

        # Clean up
        self._editing_pose_target = False
        self._editing_pose_target_id = None
        self._original_editing_pose = None
        self._original_editing_joints = None
        self._cleanup_editing()
        self._hide_edit_bar()
        self.exit_editing_mode()
        logging.debug("Pose pose editing confirmed for target %s", target_id)

    def _confirm_pose_target_editing(self) -> None:
        """Confirm pose target editing and sync to editor (legacy TransformControls mode)."""
        if not self._editing_target_id:
            return

        target = self._get_editing_target()
        if target and ui_state.editor_panel:
            if hasattr(ui_state.editor_panel, "sync_code_from_target"):
                # Ensure pose has no None values before syncing
                clean_pose = [v if v is not None else 0.0 for v in target.pose]
                ui_state.editor_panel.sync_code_from_target(
                    self._editing_target_id, clean_pose
                )
                ui.notify("Pose target updated", color="positive")

        # Disable TransformControls
        self._disable_target_transform_controls(self._editing_target_id)
        self._original_editing_pose = None
        logging.debug("Pose target confirmed from bar")

    def _hide_edit_bar(self) -> None:
        """Hide and clean up the edit bar."""
        # Clear editing type
        self._current_editing_type = None

        # Delete bar elements
        if self._edit_bar:
            self._edit_bar.delete()
            self._edit_bar = None

        if self._edit_bar_container:
            self._edit_bar_container.delete()
            self._edit_bar_container = None

        self._edit_bar_label = None
        self._edit_bar_values = None
        self._edit_bar_mode_toggle = None

    def _show_joint_target_editor(self) -> None:
        """Show the visual joint target editor in the 3D scene.

        This displays:
        1. Robot with semi-transparent grey editing appearance
        2. Blue TCP ball for IK-driven positioning (drag to move)
        3. Rotation rings at each joint for direct manipulation
        4. Bottom edit bar with Cancel/Confirm buttons
        """
        # Already editing? Don't start again
        if self._editing_joint_target:
            return

        # Get current robot angles as starting point (convert degrees to radians)
        angles_deg = list(robot_state.angles) if robot_state.angles else [0.0] * 6
        while len(angles_deg) < 6:
            angles_deg.append(0.0)
        initial_angles = [math.radians(a) for a in angles_deg[:6]]

        # Store original angles for delta display
        self._original_editing_joints = list(initial_angles)

        # Show editing mode at current position
        self.enter_editing_mode(initial_angles)

        # Set editing flags
        self._editing_joint_target = True
        self._editing_existing_target = False  # New target, not editing existing

        # Show edit bar with Cancel/Confirm buttons
        self._create_edit_bar("joint")

        # Initialize IK chain and enable TransformControls after a delay
        async def enable_controls_after_render():
            await asyncio.sleep(0.15)
            # Early exit if editing was cancelled before timer fired
            if not self._editing_joint_target:
                logging.debug(
                    "Joint target editor controls skipped (editing cancelled)"
                )
                return
            self._init_ik_solver()
            self.enable_tcp_transform_controls("translate")
            self._enable_joint_transform_controls()
            # Re-sync robot_state now that FK solver is ready
            self._sync_robot_state_from_editing()
            logging.info("Joint target editor controls enabled")

        # Use scene context to avoid parent slot errors on early cancel
        with self.scene:
            ui.timer(0.0, enable_controls_after_render, once=True)

        logging.info("Joint target editor started")

    def _confirm_joint_target(self) -> None:
        """Confirm and insert the joint target code from editing mode position."""
        if not self._editing_joint_target:
            return

        # Get final joint angles from editing (convert radians to degrees)
        angles_rad = self.get_editing_angles()
        angles_deg_final = [math.degrees(a) for a in angles_rad]

        if ui_state.editor_panel:
            marker_id = ui_state.editor_panel.add_joint_target_code(angles_deg_final)
            if marker_id:
                ui.notify(
                    f"Joint target inserted (marker: {marker_id})", color="positive"
                )

        # Clean up
        self._cancel_joint_target_editing()

    def _cancel_joint_target_editing(self) -> None:
        """Cancel joint target editing and hide editing mode."""
        if not self._editing_joint_target:
            return

        self._editing_joint_target = False
        self._original_editing_joints = None
        self._cleanup_editing()
        self._hide_edit_bar()
        self.exit_editing_mode()
        logging.debug("Joint target editor cancelled/closed")

    def _is_envelope_hit(self, object_name: str) -> bool:
        """Check if an object name belongs to the workspace envelope (non-pickable)."""
        return object_name == "envelope:sphere"

    def _handle_keyboard(self, e) -> None:
        """Handle keyboard events for the scene (e.g., ESC to deselect/cancel)."""
        if e.key == "Escape" and e.action.keydown:
            # If editing a joint target, ESC cancels it
            if self._editing_joint_target:
                self._cancel_joint_target_editing()
                return
            # Otherwise, deselect any active TransformControls
            self._disable_all_target_transform_controls()

        # J key starts joint target editor (for testing and power users)
        if (
            e.key == "j"
            and e.action.keydown
            and not e.modifiers.ctrl
            and not e.modifiers.shift
        ):
            if not self._editing_joint_target:
                self._show_joint_target_editor()

    # Methods provided by other mixins (declared for type checking only):
    # - enter_editing_mode(joint_angles: List[float]) -> None  [EditingModeMixin]
    # - exit_editing_mode() -> None  [EditingModeMixin]
    # - set_editing_angles(angles: List[float]) -> None  [urdf_scene]
    # - get_editing_angles() -> List[float]  [urdf_scene]
    # - _init_ik_solver() -> None  [EditingModeMixin]
    # - enable_tcp_transform_controls(mode: str) -> None  [TCPControlsMixin]
    # - _enable_joint_transform_controls() -> None  [EditingModeMixin]
    # - _disable_joint_transform_controls() -> None  [EditingModeMixin]
    # - _cleanup_editing() -> None  [EditingModeMixin]
    # - _ik_for_position(target_pos: List[float]) -> Optional[List[float]]  [EditingModeMixin]
    # - _update_tcp_ball_position() -> None  [TCPControlsMixin]
