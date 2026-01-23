"""
UrdfScene - Main class integrating all mixins.

This implementation is based on the original MIT-licensed urdf_scene_nicegui project.
Attribution:
- Original authors of 'urdf_scene_nicegui' (MIT License)
- Source idea and structure adapted to integrate with NiceGUI scene and PAROL Commander

This file incorporates extensions:
- Cartesian gizmo (translate/rotate) with press/hold streaming callbacks
- Robust mesh mounting and STL URL mapping
- Visual frame parenting (WRF/TRF) with TCP anchoring and tool-offset support
- Generalized tool pose handling via injection (map/resolver)
"""

import asyncio
import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from nicegui import ui, app  # type: ignore[no-redef]

from parol_commander.common.logging_config import TRACE_ENABLED
from parol_commander.common.theme import SceneColors
from parol_commander.state import simulation_state, robot_state, ui_state
from parol_commander.services.path_visualizer import get_color_for_move_type

from .envelope_mixin import workspace_envelope

from .config import RobotAppearanceMode, ToolPose, UrdfSceneConfig
from .loader import (
    load_urdf,
    resolve_meshes_dir,
    get_transl_and_rpy,
    rot_joint,
    transl_joint,
    normalize_axis,
)
from .editing_mixin import EditingMixin
from .tcp_controls_mixin import TCPControlsMixin
from .envelope_mixin import EnvelopeMixin, ENVELOPE_PROXIMITY_THRESHOLD
from .path_renderer_mixin import PathRendererMixin

logger = logging.getLogger(__name__)


class UrdfScene(
    EditingMixin,
    TCPControlsMixin,
    EnvelopeMixin,
    PathRendererMixin,
):
    """Load a URDF file as a NiceGUI Scene

    Core features:
    - Render URDF meshes (STL) using NiceGUI scene
    - Set individual/all joint axis values to animate the model
    - Add interactive Cartesian gizmo (translate arrows + rotation rings)
    - Visual parenting to WRF (world) or TRF (tool/end-effector) frame
    - TCP offset/orientation updates on tool change
    - Configurable tool pose handling via injection (no hard dependencies)
    """

    # Axis color mapping (class constant)
    _AXIS_COLORS = {
        "X": SceneColors.AXIS_X_HEX,
        "Y": SceneColors.AXIS_Y_HEX,
        "Z": SceneColors.AXIS_Z_HEX,
    }

    def __init__(
        self, path: Union[str, Path], config: Optional[UrdfSceneConfig] = None
    ):
        """Load a URDF file to construct a nicegui scene.

        Args:
            path: Path to URDF file
            config: Optional configuration for scene behavior and dependencies
        """
        path = Path(path)
        self.config = config or UrdfSceneConfig()

        # Load URDF with package resolution
        self.urdf_model = load_urdf(path, package_map=self.config.package_map)

        # Store URDF path for IK solver
        self.urdf_path = path

        # Determine and mount mesh directory
        self.meshes_dir = resolve_meshes_dir(path, self.config.meshes_dir)
        self.meshes_url = f"{self.config.static_url_prefix}/{self.urdf_model.name}"
        self.joint_names = self.urdf_model.actuated_joint_names

        if self.config.mount_static:
            try:
                app.add_static_files(self.meshes_url, str(self.meshes_dir))  # type: ignore[attr-defined]
            except Exception as e:
                msg = str(e).lower()
                # Ignore duplicate registration across tests; re-raise other errors
                if "already" in msg and "register" in msg:
                    logger.debug(
                        "Static files already registered for %s; continuing",
                        self.meshes_url,
                    )
                else:
                    raise

        # Scene-related state
        self.joint_groups: dict[str, Any] = {}
        self.joint_pos_limits: dict = {}
        self.joint_trafos: dict = {}
        self.scene: Any | None = None
        # Persist normalized joint axes (from URDF) by joint name
        self.joint_axes: Dict[str, np.ndarray] = {}
        # Pre-populate joint axes directly from URDF so axes are available before scene build
        for _name in self.joint_names[:6]:
            _j = next((jj for jj in self.urdf_model.joints if jj.name == _name), None)
            _raw = getattr(_j, "axis", None) if _j is not None else None
            try:
                self.joint_axes[_name] = normalize_axis(_raw)
            except (ValueError, TypeError, AttributeError) as e:
                logger.warning(
                    "Failed to normalize axis for joint '%s' in __init__: %s", _name, e
                )
                self.joint_axes[_name] = np.array([0.0, 0.0, 1.0], dtype=float)

        # STL scale
        self._stl_scale: float = 1.0

        # TCP anchoring: created on end-link under last joint
        self.tcp_anchor: Any | None = None
        self.tcp_offset: Any | None = None

        # Simulation visualization
        self.simulation_group: Any | None = None
        self.path_group: Any | None = None
        self.targets_group: Any | None = None
        self._path_objects: List[Any] = []
        self._rendered_segment_count: int = 0

        # Track robot mesh objects for material changes
        self._robot_meshes: List[ui.scene.stl] = []

        # Robot appearance mode (unified state machine)
        self._appearance_mode: RobotAppearanceMode = RobotAppearanceMode.LIVE

        # Editing mode state
        self._editing_angles: List[float] = [0.0] * 6  # Joint angles during editing
        self._pre_edit_angles: List[float] = [
            0.0
        ] * 6  # Saved angles to restore on exit
        self._editing_target_type: str = "cartesian"  # "cartesian" or "joint"
        self._joint_ring_touched: bool = False  # True if user rotated any joint ring

        # Scene wrapper for proper positioning of overlays
        self._scene_wrapper: Any | None = None

        # Initialize mixin states
        self._init_editing_state()
        self._init_tcp_controls_state()
        self._init_envelope_state()

        # Register as listener for simulation state changes (event-driven updates)
        simulation_state.add_change_listener(self._update_simulation_view)

    def show(self, scale_stls: float = 1.0, material=None, background_color=None):
        """Plot a nicegui 3D scene from loaded URDF.

        Args:
            scale_stls: Scale factor for all STL files (e.g., 1e-1 if designed in mm)
            material: Color for the whole URDF (overrides mesh colors in STLs if defined)
            background_color: Scene background color (defaults to config value)
        """
        self._stl_scale = float(scale_stls)
        # Use config background color if not specified
        if background_color is None:
            background_color = self.config.background_color
        # Wrap scene in element to host context menu and edit bar
        self._scene_wrapper = ui.element("div").classes("relative w-full h-full")
        with self._scene_wrapper:
            # Create context menu - clear on hide so it doesn't auto-show with stale content
            self.context_menu = ui.context_menu()
            self.context_menu.on("hide", lambda: self.context_menu.clear())
            # Use polar grid sized to robot's approximate workspace (~536mm reach)
            default_radius = 0.55  # Workspace radius in meters
            with (
                ui.scene(
                    grid=False,  # Disable rectangular grid
                    polar_grid=(default_radius, 12, 6),  # (radius, sectors, rings)
                    background_color=background_color,
                    on_click=self._handle_scene_click,
                    click_events=[
                        "mousedown",
                        "mouseup",
                        "mouseleave",
                        "contextmenu",
                    ],
                )
                .classes("w-full h-[66vh]")
                .on_transform_end(self._handle_transform_event) as self.scene
            ):
                # Ground plane for contrast with background
                ui.scene.cylinder(
                    default_radius, default_radius, 0.001, radial_segments=64
                ).material(self.config.ground_color, opacity=0.5).rotate(
                    math.pi / 2, 0, 0
                )

                # Base link
                self._plot_stls(
                    self.urdf_model.base_link, scale=self._stl_scale, material=material
                )
                # Recursively add rest
                next_joints = self._get_next_joints(
                    self.urdf_model, self.urdf_model.base_link
                )
                for joint in next_joints:
                    self._recursively_add_subtree(
                        self.urdf_model,
                        joint,
                        scale_stls=self._stl_scale,
                        material=material,
                    )

                # Make simulation group
                with ui.scene.group().with_name("simulation:root") as sim_grp:
                    self.simulation_group = sim_grp
                    with ui.scene.group().with_name("simulation:paths") as path_grp:
                        self.path_group = path_grp
                    with ui.scene.group().with_name(
                        "simulation:targets"
                    ) as targets_grp:
                        self.targets_group = targets_grp

            # Position orientation inset
            try:
                if self.scene and hasattr(self.scene, "set_axes_inset"):
                    self.scene.set_axes_inset(
                        {
                            "enabled": True,
                            "anchor": "bottom-left",
                            "marginX": 48,
                            "marginY": -12,
                            "size": 120,
                        }
                    )
                    if hasattr(self.scene, "set_axes_labels"):
                        self.scene.set_axes_labels({"enabled": True})
            except Exception as e:
                logger.debug("set_axes_inset configuration failed: %s", e)

            # Pre-generate workspace envelope for immediate rendering when enabled
            # Skip in tests that don't need it (PAROL_SKIP_ENVELOPE=1)
            if (
                not os.environ.get("PAROL_SKIP_ENVELOPE")
                and not workspace_envelope._generated
            ):
                workspace_envelope.generate()

            # Add keyboard handler for ESC to deselect TransformControls
            ui.keyboard(on_key=self._handle_keyboard)

            # Register IK solved event listener for ghost robot IK
            self.scene.on("ik_solved", self._on_ik_solved)
            # Use on_transform_start for proper typed SceneTransformEventArguments
            self.scene.on_transform_start(self._handle_transform_start)

            # Register continuous transform event listener for live ghost robot updates
            self.scene.on_transform(self._handle_transform_continuous)

    def _handle_transform_continuous(self, e) -> None:
        """Handle continuous transform events for TCP ball and joint controls.

        This handler receives transform events during drag (not just at end)
        and is used for TCP ball movement (jogging or IK) and joint ring rotation.
        Does NOT handle pose targets - those use on_transform_end only.
        """
        object_name = getattr(e, "object_name", "") or ""

        # Check if this is the unified TCP ball being transformed
        # Behavior depends on appearance mode (jogging vs IK)
        if object_name in ("tcp:ball", "tcp:jog_ball", "tcp:offset"):
            self._handle_tcp_transform_for_jog(e)
            return

        # Check if this is a joint group being rotated (TransformControls on robot joints)
        if object_name.startswith("edit_joint_group:"):
            self._on_joint_group_transform(e)
            return

    def _handle_transform_start(self, e) -> None:
        """Handle TransformControls transform_start events to manage orbit and mutex."""
        object_name = getattr(e, "object_name", "") or ""
        if object_name in ("tcp:ball", "ghost:tcp_ball"):
            # Disable orbit controls as soon as TCP transform starts
            if self.scene and hasattr(self.scene, "set_orbit_enabled"):
                self.scene.set_orbit_enabled(False)
            self._tcp_ball_dragging = True
            # Suspend joint controls during TCP ball manipulation in editing mode
            if self._appearance_mode == RobotAppearanceMode.EDITING:
                if not getattr(self, "_joint_controls_suspended", False):
                    self._disable_joint_transform_controls()
                    self._joint_controls_suspended = True
        elif object_name == "tcp:jog_ball":
            self._tcp_ball_dragging = True

    def _handle_transform_event(self, e) -> None:
        """Handle TransformControls transform_end events for targets.

        This handler fires only at the END of a drag operation.
        TCP ball and joint transforms are handled by _handle_transform_continuous for live updates.
        """
        object_name = getattr(e, "object_name", "") or ""
        event_type = getattr(e, "type", "")

        # Unified TCP ball - on transform_end re-enable orbit and joint controls
        if object_name in ("tcp:ball", "ghost:tcp_ball"):
            if event_type == "transform_end":
                self._tcp_ball_dragging = False
                # Clear drag start rotation
                self._tcp_drag_start_rot_deg = None
                # Re-enable orbit controls when TCP transform ends
                if self.scene and hasattr(self.scene, "set_orbit_enabled"):
                    self.scene.set_orbit_enabled(True)
                if getattr(self, "_joint_controls_suspended", False):
                    # Re-enable joint rotation controls
                    self._enable_joint_transform_controls()
                    self._joint_controls_suspended = False
                # Notify drag-end to consumers (for jogging mode)
                if self._appearance_mode != RobotAppearanceMode.EDITING:
                    cb = getattr(self, "_tcp_cartesian_move_end_callback", None)
                    if callable(cb):
                        try:
                            cb()
                        except Exception as err:
                            logger.error(
                                "TCP cartesian move end callback error: %s", err
                            )
            return

        # Legacy jog ball name handling
        if object_name in ("tcp:jog_ball", "tcp:offset"):
            if event_type == "transform_end":
                self._tcp_ball_dragging = False
                # Clear drag start rotation
                self._tcp_drag_start_rot_deg = None
                # Notify drag-end to consumers
                cb = getattr(self, "_tcp_cartesian_move_end_callback", None)
                if callable(cb):
                    try:
                        cb()
                    except Exception as err:
                        logger.error("TCP cartesian move end callback error: %s", err)
            return

        # Joint rings handled by continuous handler - skip here
        if object_name.startswith("ghost_ring_group:"):
            return

        # Check if this is a target group being transformed
        if object_name.startswith("targetgroup:"):
            target_id = object_name.split("targetgroup:", 1)[1]
            target = self._find_target_by_id(target_id)
            if target:
                # Only update position if provided (translate mode)
                if e.x is not None:
                    target.pose[0] = e.x
                if e.y is not None:
                    target.pose[1] = e.y
                if e.z is not None:
                    target.pose[2] = e.z

                # Only update rotation if provided (rotate mode)
                if len(target.pose) >= 6:
                    if e.rx is not None:
                        target.pose[3] = e.rx
                    if e.ry is not None:
                        target.pose[4] = e.ry
                    if e.rz is not None:
                        target.pose[5] = e.rz

                # Only sync to editor on transform_end to avoid too many updates
                if event_type == "transform_end":
                    # Ensure pose has no None values before syncing
                    clean_pose = [v if v is not None else 0.0 for v in target.pose]
                    ui_state.editor_panel.sync_code_from_target(target_id, clean_pose)

    def _handle_scene_click(self, e) -> None:
        """Handle mouse events for target deselection, context menu, and joint target editing."""
        click_type = getattr(e, "click_type", "")
        hits = getattr(e, "hits", []) or []

        if click_type == "mousedown":
            clicked_transform_controls = False
            clicked_ghost_part = False

            for h in hits:
                name = getattr(h, "object_name", "") or ""
                object_id = getattr(h, "object_id", "") or ""

                # Check if clicked on ghost robot parts
                if (
                    name.startswith("ghost:")
                    or name.startswith("ghost_ring_")
                    or name.startswith("ghost_joint_group:")
                ):
                    clicked_ghost_part = True
                    continue

                # Check if clicked on TransformControls gizmo
                if object_id.startswith("transformcontrols:"):
                    clicked_transform_controls = True
                    continue

            # Handle target editing - clicking away does NOT auto-confirm
            if self._editing_unified_target:
                if not clicked_ghost_part and not clicked_transform_controls:
                    return  # Don't process other click handlers

        elif click_type == "contextmenu":
            # Record position and event when contextmenu fires
            # Menu only shows when populated - we decide on mouseup whether to populate
            screen_x = getattr(e, "screen_x", None)
            screen_y = getattr(e, "screen_y", None)
            if screen_x is not None and screen_y is not None:
                self._right_click_start_pos = (float(screen_x), float(screen_y))
            else:
                client_x = getattr(e, "client_x", 0)
                client_y = getattr(e, "client_y", 0)
                self._right_click_start_pos = (float(client_x), float(client_y))
            # Store event for populating menu on mouseup
            self._pending_context_menu_event = e

        elif click_type == "mouseup":
            button = getattr(e, "button", 0)
            if button == 2 and self._right_click_start_pos is not None:
                # Check if this was a drag
                _screen_x = getattr(e, "screen_x", None)
                _screen_y = getattr(e, "screen_y", None)
                if _screen_x is None or _screen_y is None:
                    screen_x = float(getattr(e, "client_x", 0))
                    screen_y = float(getattr(e, "client_y", 0))
                else:
                    screen_x = float(_screen_x)
                    screen_y = float(_screen_y)

                start_x, start_y = self._right_click_start_pos
                dx = screen_x - start_x
                dy = screen_y - start_y
                distance = math.hypot(dx, dy)

                self._right_click_start_pos = None

                if distance <= self._right_click_drag_threshold:
                    # Was a simple click - populate the menu (which makes it show)
                    if self._pending_context_menu_event:
                        self._populate_context_menu(self._pending_context_menu_event)
                # If drag, don't populate - menu stays empty and won't show

                self._pending_context_menu_event = None

    def _update_simulation_view(self) -> None:
        """Update simulation visualization (paths, etc.) based on state."""
        if not self.scene or not self.simulation_group:
            return

        # Check if THIS scene's client still exists before modifying scene
        if self.scene.is_deleted:
            return

        # Check if event loop is still running
        try:
            loop = asyncio.get_running_loop()
            if loop.is_closed():
                return
        except RuntimeError:
            return

        # Wrap all scene operations in try/except to handle client deletion during shutdown
        try:
            self._do_update_simulation_view()
        except RuntimeError as e:
            if "client" in str(e).lower() and "deleted" in str(e).lower():
                return  # Client deleted during shutdown - safe to ignore
            raise

    def _do_update_simulation_view(self) -> None:
        """Internal implementation of simulation view update."""
        # Keep TCP jog ball aligned with current robot TCP when not dragging
        self._update_jog_ball_from_robot_state()

        # Update Workspace Envelope based on envelope_mode
        envelope_mode = simulation_state.envelope_mode
        approaching_positions: List[Tuple[float, float, float]] = []

        if envelope_mode == "auto":
            if workspace_envelope._generated and workspace_envelope.max_reach > 0:
                max_reach = workspace_envelope.max_reach
                boundary_distance = max_reach - ENVELOPE_PROXIMITY_THRESHOLD

                # Check robot TCP position (convert mm to m)
                tcp_x = robot_state.x / 1000.0
                tcp_y = robot_state.y / 1000.0
                tcp_z = robot_state.z / 1000.0
                tcp_dist = math.hypot(tcp_x, tcp_y, tcp_z)

                if tcp_dist >= boundary_distance:
                    approaching_positions.append((tcp_x, tcp_y, tcp_z))

                # Check each target position
                for target in simulation_state.targets:
                    if len(target.pose) >= 3:
                        tx, ty, tz = target.pose[0], target.pose[1], target.pose[2]
                        target_dist = math.hypot(tx, ty, tz)
                        if target_dist >= boundary_distance:
                            approaching_positions.append((tx, ty, tz))

        # Update envelope using mixin method
        self._update_envelope_in_simulation_view(approaching_positions)

        # Visibility check for paths and targets
        if not simulation_state.paths_visible:
            if self.path_group is not None:
                self.path_group.visible(False)
            if self.targets_group is not None:
                self.targets_group.visible(False)
            return

        if self.path_group is not None:
            self.path_group.visible(True)
        if self.targets_group is not None:
            self.targets_group.visible(True)

        # Rebuild paths if changed
        current_count = len(simulation_state.path_segments)
        prev_rendered = self._rendered_segment_count

        if TRACE_ENABLED:
            logger.trace(  # type: ignore[attr-defined]
                "SCENE: _update_simulation_view tick - current_segments=%d, "
                "rendered_count=%d, path_objects=%d",
                current_count,
                prev_rendered,
                len(self._path_objects),
            )

        if current_count == 0:
            if TRACE_ENABLED and self._path_objects:
                logger.trace(  # type: ignore[attr-defined]
                    "SCENE: Clearing %d path objects (segments went to 0)",
                    len(self._path_objects),
                )
            for obj in self._path_objects:
                self._safe_delete(obj)
            self._path_objects.clear()
            self._rendered_segment_count = 0

        elif current_count > self._rendered_segment_count:
            if TRACE_ENABLED:
                logger.trace(  # type: ignore[attr-defined]
                    "SCENE: Adding segments %d-%d (new segments arrived)",
                    self._rendered_segment_count,
                    current_count - 1,
                )
            if self.path_group and self.scene:
                with self.scene:
                    with self.path_group:
                        for i in range(self._rendered_segment_count, current_count):
                            segment = simulation_state.path_segments[i]
                            objs = self._render_path_segment(segment)
                            self._path_objects.extend(objs)
                            if TRACE_ENABLED:
                                logger.trace(  # type: ignore[attr-defined]
                                    "SCENE: Rendered segment %d -> %d objects, "
                                    "total_path_objects=%d",
                                    i,
                                    len(objs),
                                    len(self._path_objects),
                                )
            self._rendered_segment_count = current_count

        elif current_count < self._rendered_segment_count:
            if TRACE_ENABLED:
                logger.trace(  # type: ignore[attr-defined]
                    "SCENE: Resetting - current(%d) < rendered(%d), "
                    "clearing %d objects",
                    current_count,
                    self._rendered_segment_count,
                    len(self._path_objects),
                )
            for obj in self._path_objects:
                self._safe_delete(obj)
            self._path_objects.clear()
            self._rendered_segment_count = 0

        # Update targets - preserve existing targets to maintain TransformControls
        if self.targets_group and self.scene:
            active_ids = set()
            for target in simulation_state.targets:
                active_ids.add(target.id)
                if target.id not in self._target_objects:
                    # Create new target
                    with self.scene:
                        with self.targets_group:
                            target_group = ui.scene.group().with_name(
                                f"targetgroup:{target.id}"
                            )
                            with target_group:
                                sphere = ui.scene.sphere(0.008)
                                target_color = get_color_for_move_type(target.move_type)
                                sphere.material(target_color)
                                sphere.with_name(f"target:{target.id}")

                        target_group.move(
                            target.pose[0], target.pose[1], target.pose[2]
                        )

                        self._target_objects[target.id] = {
                            "group": target_group,
                            "sphere": sphere,
                        }
                else:
                    # Target exists - update position only if NOT currently being edited
                    target_data = self._target_objects[target.id]
                    if target.id != self._editing_target_id:
                        target_data["group"].move(
                            target.pose[0], target.pose[1], target.pose[2]
                        )

            # Remove stale targets
            for tid in list(self._target_objects.keys()):
                if tid not in active_ids:
                    target_data = self._target_objects[tid]
                    if self.scene:
                        try:
                            self.scene.disable_transform_controls(
                                target_data["group"].id
                            )
                        except RuntimeError:
                            pass  # Client deleted during shutdown
                    self._safe_delete(target_data["group"])
                    del self._target_objects[tid]
                    if self._editing_target_id == tid:
                        self._editing_target_id = None

    def _safe_delete(self, obj: Any) -> None:
        """Safely delete a scene object, handling cases where it's already deleted."""
        if self.scene is None:
            return
        try:
            # Check if object ID still exists in scene before deleting
            if hasattr(obj, "id") and obj.id in self.scene.objects:
                obj.delete()
        except (KeyError, RuntimeError):
            # KeyError: Object was already deleted from scene
            # RuntimeError: Client was deleted (shutdown race condition)
            pass

    @property
    def initialized(self) -> bool:
        """Check if scene has been initialized via show()."""
        return self.scene is not None

    @property
    def last_actuated_joint_name(self) -> Optional[str]:
        """Get the name of the last actuated joint."""
        return self.joint_names[-1] if self.joint_names else None

    @property
    def last_actuated_group(self) -> Optional[ui.scene.group]:
        """Get the scene group for the last actuated joint."""
        last_joint = self.last_actuated_joint_name
        return self.joint_groups.get(last_joint) if last_joint else None

    def invalidate_paths(self) -> None:
        """Clear rendered paths and reset cache, forcing a full re-render on next update.

        Call this when switching tabs or when the path data has completely changed.
        """
        for obj in self._path_objects:
            self._safe_delete(obj)
        self._path_objects.clear()
        self._rendered_segment_count = 0

    # --------- Public API ---------

    def update_from_robot_state(self) -> None:
        """Update scene elements that depend on robot state.

        This method should be called directly from the status update loop
        in main.py to ensure reliable updates without context issues.
        """
        self._update_jog_ball_from_robot_state()
        self._update_envelope_from_robot_state()

    def set_axis_value(self, joint_name: str, val: float) -> None:
        """Set a single joint axis value.

        Args:
            joint_name: Name of the joint to move
            val: Joint value (radians for revolute, meters for prismatic)
        """
        t, r = self.joint_trafos[joint_name](val)
        self.joint_groups[joint_name].move(*t).rotate(*r)

    def set_axis_values(self, val: Union[List, np.ndarray]) -> None:
        """Set all axes values by passing an array or list.

        Args:
            val: Array or list of joint values in order matching self.joint_names

        Note:
            This method is guarded - it will not update the robot during editing mode
            to prevent live updates from overwriting user manipulations.
        """
        # Don't update robot joints during editing mode - user is manipulating them
        if self._appearance_mode == RobotAppearanceMode.EDITING:
            return

        for joint_name, q in zip(self.joint_names, val):
            joint_TF = self.joint_trafos[joint_name]
            joint_i = self.joint_groups[joint_name]
            t, r = joint_TF(q)
            joint_i.move(*t).rotate(*r)

    def _apply_joint_angles(self, angles_rad: List[float]) -> None:
        """Apply joint angles to the main robot joint groups.

        Internal method used by both live updates and editing mode.

        Args:
            angles_rad: Joint angles in radians, ordered by self.joint_names
        """
        for joint_name, q in zip(self.joint_names, angles_rad):
            if joint_name in self.joint_groups and joint_name in self.joint_trafos:
                t, r = self.joint_trafos[joint_name](q)
                self.joint_groups[joint_name].move(*t).rotate(*r)

    def set_editing_angles(self, angles: List[float]) -> None:
        """Set joint angles for editing mode (radians).

        Updates the robot visualization when in EDITING mode.

        Args:
            angles: List of joint angles in radians
        """
        # Ensure we have 6 angles
        self._editing_angles = list(angles) + [0.0] * (6 - len(angles))
        self._editing_angles = self._editing_angles[:6]

        # Only apply to robot if in editing mode
        if self._appearance_mode == RobotAppearanceMode.EDITING:
            self._apply_joint_angles(self._editing_angles)
            # Update TCP ball position via FK
            self._update_tcp_ball_position_from_editing()

    def get_editing_angles(self) -> List[float]:
        """Get current editing joint angles.

        Returns:
            List of joint angles in radians
        """
        return list(self._editing_angles)

    def _update_tcp_ball_position_from_editing(self) -> None:
        """Update TCP ball position based on editing angles using FK.

        This positions the unified TCP ball when in editing mode.
        """
        # Use the unified TCP ball position update method
        self._update_tcp_ball_position()

    def get_joint_names(self) -> List[str]:
        """Get list of actuated joint names in order."""
        return list(self.joint_groups.keys())

    def get_joint_limits(self) -> Dict[str, Dict[str, Optional[float]]]:
        """Get joint position limits."""
        return {
            name: {"min": limits.get("min"), "max": limits.get("max")}
            for name, limits in self.joint_pos_limits.items()
        }

    def set_tcp_pose(self, origin: Sequence[float], rpy: Sequence[float]) -> None:
        """Directly set TCP offset pose.

        Args:
            origin: Translation offset [x, y, z] in meters
            rpy: Rotation offset [roll, pitch, yaw] in radians
        """
        if not self.tcp_offset:
            logger.warning("TCP offset group not initialized; cannot set pose")
            return
        if len(origin) != 3 or len(rpy) != 3:
            raise ValueError("origin and rpy must each have exactly 3 elements")
        self.tcp_offset.move(*origin).rotate(*rpy)

    def update_tcp_pose_from_tool(self, tool: str) -> None:
        """Move/rotate the TCP offset based on selected tool's TCP config.

        Args:
            tool: Tool identifier string
        """
        if not self.tcp_offset:
            logger.warning("TCP offset group not initialized; cannot update from tool")
            return

        # Default: reset offsets
        origin = [0.0, 0.0, 0.0]
        rpy = [0.0, 0.0, 0.0]

        tool_pose: Optional[ToolPose] = None

        # Try resolver first
        if self.config.tool_pose_resolver is not None:
            tool_pose = self.config.tool_pose_resolver(tool)

        # Fall back to map
        if tool_pose is None and tool in self.config.tool_pose_map:
            tool_pose = self.config.tool_pose_map[tool]

        # Apply pose if found
        if tool_pose is not None:
            if tool_pose.origin and len(tool_pose.origin) == 3:
                origin = list(tool_pose.origin)
            if tool_pose.rpy and len(tool_pose.rpy) == 3:
                rpy = list(tool_pose.rpy)

        self.tcp_offset.move(*origin).rotate(*rpy)

        # Track tool offset for envelope calculations
        self._current_tool = tool or "none"
        self._current_tool_offset_z = origin[2] if len(origin) > 2 else 0.0

        # Update envelope sphere if it exists
        self._update_envelope_radius()

    def set_appearance_mode(self, mode: RobotAppearanceMode) -> None:
        """Set robot appearance mode.

        Args:
            mode: The appearance mode to set (LIVE, SIMULATOR, or EDITING)
        """
        self._appearance_mode = mode

        # Get appearance settings based on mode
        if mode == RobotAppearanceMode.LIVE:
            color = self.config.material
            opacity = 1.0
        elif mode == RobotAppearanceMode.SIMULATOR:
            color = self.config.sim_color
            opacity = self.config.sim_opacity
        else:  # EDITING
            color = self.config.edit_color
            opacity = self.config.edit_opacity

        # Apply to all robot meshes
        for mesh in self._robot_meshes:
            mesh.material(color, opacity)

        logger.debug("Robot appearance mode set to %s", mode.value)

    def set_simulator_appearance(self, active: bool) -> None:
        """Apply or remove simulator visual appearance (amber ghosting).

        Args:
            active: True to apply simulator appearance, False to restore default
        """
        # Don't change mode if currently in EDITING mode
        if self._appearance_mode == RobotAppearanceMode.EDITING:
            logger.debug("Ignoring set_simulator_appearance while in EDITING mode")
            return

        if active:
            self.set_appearance_mode(RobotAppearanceMode.SIMULATOR)
        else:
            self.set_appearance_mode(RobotAppearanceMode.LIVE)

    # --------- Internal URDF building ---------

    def _get_next_joints(self, urdf, link_obj):
        """Get joints that have link_obj as parent."""
        return [j for j in urdf.joints if j.parent == link_obj.name]

    def _recursively_add_subtree(
        self, urdf, joint, scale_stls: float = 1, material=None
    ):
        """Recursively add joint and child link to scene."""
        t, r = get_transl_and_rpy(joint.origin)
        # Static transform from parent link to this joint frame
        with ui.scene.group().move(*t).rotate(*r):
            # Dynamic transform for joint value (q)
            with ui.scene.group() as joint_trafo:
                if joint.joint_type != "fixed":
                    self.joint_groups[joint.name] = joint_trafo

                    if joint.joint_type == "prismatic":
                        self.joint_trafos[joint.name] = (
                            lambda q, axis=joint.axis: transl_joint(axis, q)
                        )
                        self.joint_pos_limits[joint.name] = {
                            "min": joint.limit.lower,
                            "max": joint.limit.upper,
                        }
                    elif joint.joint_type in ("revolute", "continuous"):
                        self.joint_trafos[joint.name] = (
                            lambda q, axis=joint.axis: rot_joint(axis, q)
                        )
                        if joint.joint_type == "continuous":
                            self.joint_pos_limits[joint.name] = {
                                "min": None,
                                "max": None,
                            }
                        else:
                            self.joint_pos_limits[joint.name] = {
                                "min": joint.limit.lower,
                                "max": joint.limit.upper,
                            }
                    else:
                        raise NotImplementedError(
                            f"Unsupported joint type '{joint.joint_type}' for joint "
                            f"'{joint.name}'. Supported types: 'fixed', 'prismatic', "
                            f"'revolute', 'continuous'."
                        )

                    # Persist normalized joint axis
                    try:
                        self.joint_axes[joint.name] = normalize_axis(
                            getattr(joint, "axis", None)
                        )
                    except (ValueError, TypeError, AttributeError) as e:
                        logger.warning(
                            "Failed to normalize axis for joint '%s': %s",
                            joint.name,
                            e,
                        )

                child_link = next(
                    (link for link in urdf.links if link.name == joint.child), None
                )
                if child_link:
                    self._plot_stls(child_link, scale=scale_stls, material=material)

                    if child_link not in urdf.end_links:
                        # Continue recursion
                        joints = self._get_next_joints(urdf, child_link)
                        for sub_joint in joints:
                            self._recursively_add_subtree(
                                urdf,
                                sub_joint,
                                scale_stls=scale_stls,
                                material=material,
                            )
                    else:
                        # End link reached: place a TCP anchor
                        with joint_trafo:
                            anchor = ui.scene.group().with_name("tcp:anchor")
                            self.tcp_anchor = anchor
                            with anchor:
                                offset = ui.scene.group().with_name("tcp:offset")
                                self.tcp_offset = offset
                        # Optional: small axes at TCP
                        if self.config.draw_tcp_axes:
                            self._draw_scene_cos(scale=0.05)

    def _plot_stls(self, link, scale: float = 1, material=None):
        """Add all visual STLs from a link to the scene."""
        for visual in link.visuals:
            obj = ui.scene.stl(
                self._stl_to_url(visual.geometry.geometry.filename)
            ).scale(scale)
            if material is not None:
                obj.material(material)
            # Track mesh object for simulator appearance changes
            self._robot_meshes.append(obj)

    def _stl_to_url(self, stl_path: str) -> str:
        """Convert STL file path to URL, preferring _simplified variants if they exist."""
        # Handle file:// URIs
        if stl_path.startswith("file://"):
            stl_path = stl_path[7:]

        # Get path relative to meshes_dir
        stl_full = Path(stl_path)
        if stl_full.is_absolute():
            try:
                rel_path = stl_full.relative_to(self.meshes_dir)
            except ValueError:
                rel_path = Path(stl_full.name)
        else:
            rel_path = stl_full

        # Check for _simplified variant (e.g., part.STL -> part_simplified.stl)
        # Try both original extension case and lowercase .stl
        for ext in [rel_path.suffix, ".stl"]:
            simplified_name = rel_path.stem + "_simplified" + ext
            simplified_path = rel_path.with_name(simplified_name)
            full_simplified = self.meshes_dir / simplified_path

            if full_simplified.exists():
                rel_path = simplified_path
                logger.debug("Using simplified mesh: %s", simplified_path)
                break

        return os.path.join(self.meshes_url, str(rel_path).replace("\\", "/"))

    def _draw_scene_cos(
        self, scale: float = 0.3, translate: Optional[Sequence[float]] = None
    ):
        """Draw coordinate system axes at specified location."""
        scene = self.scene
        if scene is None:
            return
        tx, ty, tz = translate if translate is not None else (0.0, 0.0, 0.0)
        origin = [tx, ty, tz]
        scene.line(origin, [tx + scale, ty, tz]).material(SceneColors.AXIS_X_HEX)
        scene.line(origin, [tx, ty + scale, tz]).material(SceneColors.AXIS_Y_HEX)
        scene.line(origin, [tx, ty, tz + scale]).material(SceneColors.AXIS_Z_HEX)

    def _axis_color(self, axis_letter: str) -> str:
        """Get standard color for coordinate axis (CVD-aware palette)."""
        return self._AXIS_COLORS.get(axis_letter.upper(), SceneColors.MATERIAL_DARK_HEX)
