"""
UrdfScene integrated into parol_commander.

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

import re
import math
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import List, Tuple, Optional, Callable, Dict, Sequence, Union
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from urchin import URDF  # type: ignore[import-untyped]
from scipy.spatial.transform import Rotation as R
from nicegui import ui, app


class GizmoEventKind(Enum):
    """Type of gizmo interaction event."""

    PRESS = "press"
    RELEASE = "release"


class GizmoMode(Enum):
    """Type of gizmo control."""

    TRANSLATE = "translate"
    ROTATE = "rotate"


@dataclass
class GizmoEvent:
    """Event emitted when gizmo handle is pressed or released."""

    kind: GizmoEventKind
    """Whether the handle was pressed or released."""

    mode: GizmoMode
    """Whether this is a translation or rotation control."""

    axis: str
    """Primary axis: 'X', 'Y', or 'Z'."""

    sign: int
    """Direction: +1 or -1."""

    handle: str
    """Handle identifier like 'X+', 'Y-', 'RX+', 'RZ-'."""

    frame: str
    """Current control frame: 'WRF' or 'TRF'."""

    timestamp: float
    """Event timestamp from time.time()."""


@dataclass
class ToolPose:
    """TCP offset and orientation for a tool."""

    origin: Sequence[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rpy: Sequence[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])


@dataclass
class UrdfSceneConfig:
    """Configuration for UrdfScene behavior and dependencies."""

    meshes_dir: Optional[Path] = None
    """Directory containing mesh files. If None, auto-discover from URDF location."""

    static_url_prefix: str = "/meshes"
    """URL prefix for serving static mesh files."""

    package_map: Dict[str, Path] = field(default_factory=lambda: {})
    """Mapping from package:// names to filesystem paths."""

    gizmo_scale: Optional[float] = None
    """Override gizmo size. If None, scales with STL scale."""

    draw_tcp_axes: bool = True
    """Whether to draw coordinate axes at TCP location."""

    tool_pose_map: Dict[str, ToolPose] = field(default_factory=lambda: {})
    """Mapping from tool names to TCP poses."""

    tool_pose_resolver: Optional[Callable[[str], Optional[ToolPose]]] = None
    """Function to resolve tool name to TCP pose dynamically."""

    mount_static: bool = True
    """Whether to automatically mount meshes as static files."""


class UrdfScene:
    """Load a URDF file as a NiceGUI Scene

    Core features:
    - Render URDF meshes (STL) using NiceGUI scene
    - Set individual/all joint axis values to animate the model
    - Add interactive Cartesian gizmo (translate arrows + rotation rings)
    - Visual parenting to WRF (world) or TRF (tool/end-effector) frame
    - TCP offset/orientation updates on tool change
    - Configurable tool pose handling via injection (no hard dependencies)
    """

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
        self.urdf_model = self._load_urdf(path, package_map=self.config.package_map)

        # Determine and mount mesh directory
        self.meshes_dir = self._resolve_meshes_dir(path)
        self.meshes_url = f"{self.config.static_url_prefix}/{self.urdf_model.name}"
        self.joint_names = self.urdf_model.actuated_joint_names

        if self.config.mount_static:
            app.add_static_files(self.meshes_url, str(self.meshes_dir))

        # Scene-related state
        self.joint_groups: dict[str, ui.scene.group] = {}
        self.joint_pos_limits: dict = {}
        self.joint_trafos: dict = {}
        self.scene: ui.scene | None = None

        # Cartesian gizmo
        self.gizmo_group: ui.scene.group | None = None  # root group for gizmo parts
        self.gizmo_translate_group: ui.scene.group | None = (
            None  # subgroup for translation arrows
        )
        self.gizmo_rotate_group: ui.scene.group | None = (
            None  # subgroup for rotation rings
        )
        self._gizmo_handles: Dict[str, List] = {}  # axis_key -> list of Object3D
        self._gizmo_callbacks: List[Callable[[GizmoEvent], None]] = []
        self._active_axis: Optional[str] = None
        self._hover_axis: Optional[str] = None
        self._handle_base_colors: Dict[str, str] = {}  # axis_key -> base hex color
        self._gizmo_visible: bool = True
        self._gizmo_display_mode: str = "TRANSLATE"  # 'TRANSLATE' or 'ROTATE'
        self._control_frame: str = "TRF"  # 'TRF' or 'WRF'
        self._stl_scale: float = 1.0

        # TCP anchoring: created on end-link under last joint
        self.tcp_anchor: ui.scene.group | None = (
            None  # attached under last joint transform (flange)
        )
        self.tcp_offset: ui.scene.group | None = (
            None  # child of tcp_anchor for tool-specific offset/orientation
        )

    def _resolve_meshes_dir(self, urdf_path: Path) -> Path:
        """Resolve mesh directory from config or URDF location."""
        if self.config.meshes_dir is not None:
            meshes_dir = Path(self.config.meshes_dir)
            if not meshes_dir.exists():
                raise NotADirectoryError(
                    f"Configured meshes_dir does not exist: {meshes_dir}"
                )
            return meshes_dir

        # Auto-discover: check sibling "meshes" directory
        meshes_link = urdf_path.parent / "meshes"
        if meshes_link.exists():
            return meshes_link

        # Try parent directory
        meshes_link = urdf_path.parent.parent / "meshes"
        if meshes_link.exists():
            return meshes_link

        raise NotADirectoryError(
            f"Could not find meshes directory. Checked: {urdf_path.parent}/meshes "
            f"and {urdf_path.parent.parent}/meshes"
        )

    def show(self, scale_stls: float = 1.0, material=None, background_color="#004191"):
        """ "Plot a nicegui 3D scene from loaded URDF.

        Args:
            scale_stls: Scale factor for all STL files (e.g., 1e-1 if designed in mm)
            material: Color for the whole URDF (overrides mesh colors in STLs if defined)
            background_color: Scene background color
        """
        self._stl_scale = float(scale_stls)
        with ui.scene(
            grid=(10, 100),
            background_color=background_color,
            on_click=self._handle_scene_click,
            click_events=["mousedown", "mouseup", "mouseleave", "mousemove"],
        ).classes("w-full h-[66vh]") as self.scene:
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
            # Make gizmo
            self._build_gizmo()

    @property
    def initialized(self) -> bool:
        """Check if scene has been initialized via show()."""
        return self.scene is not None

    @property
    def last_actuated_joint_name(self) -> Optional[str]:
        """Get the name of the last actuated joint (useful for end-effector attachment)."""
        return self.joint_names[-1] if self.joint_names else None

    @property
    def last_actuated_group(self) -> Optional[ui.scene.group]:
        """Get the scene group for the last actuated joint."""
        last_joint = self.last_actuated_joint_name
        return self.joint_groups.get(last_joint) if last_joint else None

    # --------- Public API ---------

    def set_axis_value(self, joint_name: str, val: float) -> None:
        """Set a single joint axis value.

        Args:
            joint_name: Name of the joint to move
            val: Joint value (radians for revolute, meters for prismatic)
        """
        t, r = self.joint_trafos[joint_name](val)
        self.joint_groups[joint_name].move(*t).rotate(*r)

    def set_axis_values(self, val: Union[List, np.ndarray]) -> None:
        """Set all axes values by passing an array or list, ordered by self.joint_names.

        Args:
            val: Array or list of joint values in order matching self.joint_names
        """
        for joint_name, q in zip(self.joint_names, list(val)):
            joint_TF = self.joint_trafos[joint_name]
            joint_i = self.joint_groups[joint_name]
            t, r = joint_TF(q)
            joint_i.move(*t).rotate(*r)

    def get_joint_names(self) -> List[str]:
        """Get list of actuated joint names in order."""
        return list(self.joint_groups.keys())

    def get_joint_limits(self) -> Dict[str, Dict[str, Optional[float]]]:
        """Get joint position limits.

        Returns:
            Dictionary mapping joint names to dicts with 'min' and 'max' keys.
            Values are None for unlimited joints (e.g., continuous).
        """
        return {
            name: {"min": limits.get("min"), "max": limits.get("max")}
            for name, limits in self.joint_pos_limits.items()
        }

    def on_gizmo_event(self, cb: Callable[[GizmoEvent], None]) -> None:
        """Register callback to receive gizmo interaction events.

        Args:
            cb: Callback function receiving GizmoEvent on press/release
        """
        self._gizmo_callbacks.append(cb)

    def set_gizmo_visible(self, visible: bool) -> None:
        """Show or hide the TCP gizmo.

        Args:
            visible: True to show, False to hide
        """
        self._gizmo_visible = bool(visible)
        if self.gizmo_group:
            self.gizmo_group.visible(self._gizmo_visible)

    def set_control_frame(self, frame: str) -> None:
        """Visually parent gizmo to WRF (world) or TRF (tool).

        Args:
            frame: Either "WRF" for world reference frame or "TRF" for tool reference frame
        """
        frame = (frame or "").upper()
        if frame not in ("WRF", "TRF"):
            raise ValueError(f"Invalid frame: {frame}. Must be 'WRF' or 'TRF'.")
        self._control_frame = frame
        self._update_gizmo_parent()

    def set_gizmo_display_mode(self, mode: str) -> None:
        """Toggle gizmo display between translation arrows and rotation rings.

        Args:
            mode: Either "TRANSLATE" for translation arrows or "ROTATE" for rotation rings
        """
        mode = (mode or "").upper()
        if mode not in ("TRANSLATE", "ROTATE"):
            raise ValueError(f"Invalid mode: {mode}. Must be 'TRANSLATE' or 'ROTATE'.")

        # Clear any active or hover state before switching
        self._deactivate_active_axis()
        self._hover_axis = None

        self._gizmo_display_mode = mode

        # Show/hide appropriate subgroups
        if self.gizmo_translate_group and self.gizmo_rotate_group:
            if mode == "TRANSLATE":
                self.gizmo_translate_group.visible(True)
                self.gizmo_rotate_group.visible(False)
            else:
                self.gizmo_translate_group.visible(False)
                self.gizmo_rotate_group.visible(True)

    def set_tcp_pose(self, origin: Sequence[float], rpy: Sequence[float]) -> None:
        """Directly set TCP offset pose.

        Args:
            origin: Translation offset [x, y, z] in meters
            rpy: Rotation offset [roll, pitch, yaw] in radians
        """
        if not self.tcp_offset:
            logging.warning("TCP offset group not initialized; cannot set pose")
            return
        if len(origin) != 3 or len(rpy) != 3:
            raise ValueError("origin and rpy must each have exactly 3 elements")
        self.tcp_offset.move(*origin).rotate(*rpy)

    def update_tcp_pose_from_tool(self, tool: str) -> None:
        """Move/rotate the TCP offset based on selected tool's TCP config.

        Uses tool_pose_resolver if configured, then tool_pose_map, otherwise no-op.

        Args:
            tool: Tool identifier string
        """
        if not self.tcp_offset:
            logging.warning("TCP offset group not initialized; cannot update from tool")
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

    # --------- Internal URDF building ---------

    def _get_next_joints(self, urdf, link_obj):
        """Get joints that have link_obj as parent."""
        return [j for j in urdf.joints if j.parent == link_obj.name]

    def _recursively_add_subtree(
        self, urdf, joint, scale_stls: float = 1, material=None
    ):
        """Recursively add joint and child link to scene."""
        t, r = UrdfScene.get_transl_and_rpy(joint.origin)
        # Static transform from parent link to this joint frame
        with ui.scene.group().move(*t).rotate(*r):
            # Dynamic transform for joint value (q)
            with ui.scene.group() as joint_trafo:
                if joint.joint_type != "fixed":
                    self.joint_groups[joint.name] = joint_trafo

                    if joint.joint_type == "prismatic":
                        self.joint_trafos[joint.name] = (
                            lambda q, axis=joint.axis: UrdfScene.transl_joint(axis, q)  # type: ignore[misc]
                        )
                        self.joint_pos_limits[joint.name] = {
                            "min": joint.limit.lower,
                            "max": joint.limit.upper,
                        }
                    elif joint.joint_type in ("revolute", "continuous"):
                        self.joint_trafos[joint.name] = (
                            lambda q, axis=joint.axis: UrdfScene.rot_joint(axis, q)  # type: ignore[misc]
                        )
                        if joint.joint_type == "continuous":
                            # Continuous joints have no limits
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
                            f"Unsupported joint type '{joint.joint_type}' for joint '{joint.name}'. "
                            f"Supported types: 'fixed', 'prismatic', 'revolute', 'continuous'."
                        )

                child_link = [link for link in urdf.links if link.name == joint.child]
                if child_link:
                    child_link = child_link[0]
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
                        # End link reached: place a TCP anchor under this joint's dynamic transform
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

    def _stl_to_url(self, stl_path: str) -> str:
        """Convert STL file path to URL, preserving nested structure."""
        # Handle file:// URIs
        if stl_path.startswith("file://"):
            stl_path = stl_path[7:]

        # Get path relative to meshes_dir
        stl_full = Path(stl_path)
        if stl_full.is_absolute():
            # Try to make relative to meshes_dir
            try:
                rel_path = stl_full.relative_to(self.meshes_dir)
            except ValueError:
                # Not relative to meshes_dir; use basename only
                rel_path = Path(stl_full.name)
        else:
            rel_path = stl_full

        # Build URL preserving nested structure
        return os.path.join(self.meshes_url, str(rel_path).replace("\\", "/"))

    def _draw_scene_cos(self, scale=0.3, translate=np.array([0, 0, 0])):
        """Draw coordinate system axes at specified location."""
        scene = self.scene
        if scene is None:
            return
        scene.line(
            translate.tolist(), (np.array([scale, 0, 0]) + translate).tolist()
        ).material("#d94c3f")
        scene.line(
            translate.tolist(), (np.array([0, scale, 0]) + translate).tolist()
        ).material("#2faf7a")
        scene.line(
            translate.tolist(), (np.array([0, 0, scale]) + translate).tolist()
        ).material("#4a63e0")

    # --------- Gizmo creation and parenting ---------

    def _axis_color(self, axis_letter: str) -> str:
        """Get standard color for coordinate axis (CVD-aware palette)."""
        return {"X": "#d94c3f", "Y": "#2faf7a", "Z": "#4a63e0"}.get(
            axis_letter.upper(), "#888888"
        )

    def _build_gizmo(self) -> None:
        """Build interactive gizmo for cartesian control."""
        if not self.scene:
            return
        with ui.scene.group() as gizmo_root:
            gizmo_root.with_name("gizmo:root")
        self.gizmo_group = gizmo_root
        self._gizmo_handles.clear()

        # Create subgroups for translation and rotation
        with self.gizmo_group:
            self.gizmo_translate_group = ui.scene.group().with_name("gizmo:translate")  # type: ignore[assignment,attr-defined]
            self.gizmo_rotate_group = ui.scene.group().with_name("gizmo:rotate")  # type: ignore[assignment,attr-defined]

        # Use config gizmo_scale if provided, else scale with STLs
        s = (
            self.config.gizmo_scale
            if self.config.gizmo_scale is not None
            else self._stl_scale
        )
        shaft_len = 0.065 * s
        shaft_r = 0.0006 * s
        head_len = 0.03 * s
        head_r = 0.02 * s
        rot_r_mid = 0.075 * s
        rot_arc_thickness = 0.004 * s
        rot_head_r = 0.010 * s
        rot_head_len = 0.025 * s

        # Translation handles
        for axis in ("X", "Y", "Z"):
            self._create_translate_handle(
                axis, "+", shaft_len, shaft_r, head_len, head_r
            )
            self._create_translate_handle(
                axis, "-", shaft_len, shaft_r, head_len, head_r
            )

        # Rotation curved arrows
        for axis in ("X", "Y", "Z"):
            self._create_rotation_half(
                axis, "+", rot_r_mid, rot_arc_thickness, rot_head_r, rot_head_len
            )
            self._create_rotation_half(
                axis, "-", rot_r_mid, rot_arc_thickness, rot_head_r, rot_head_len
            )

        # Parent according to selected frame
        self._update_gizmo_parent()
        self.set_gizmo_visible(self._gizmo_visible)
        # Set initial display mode
        self.set_gizmo_display_mode(self._gizmo_display_mode)

    def _create_translate_handle(
        self,
        axis: str,
        sign: str,
        shaft_len: float,
        shaft_r: float,
        head_len: float,
        head_r: float,
    ) -> None:
        """Create a translation handle (arrow) for the gizmo."""
        gt = self.gizmo_translate_group
        if not gt:
            return
        axis = axis.upper()
        sign = "+" if sign == "+" else "-"
        dir_sign = 1.0 if sign == "+" else -1.0
        color = self._axis_color(axis)
        axis_key = f"{axis}{sign}"

        with gt:
            group = ui.scene.group().with_name(f"gizmo:{axis_key}")

        # orient from default +Y to desired axis
        if axis == "X":
            group.rotate(0.0, 0.0, -math.pi / 2.0)  # Y -> X
        elif axis == "Y":
            group.rotate(0.0, 0.0, 0.0)
        elif axis == "Z":
            group.rotate(math.pi / 2.0, 0.0, 0.0)  # Y -> Z

        with group:
            shaft = ui.scene.cylinder(
                top_radius=shaft_r,
                bottom_radius=shaft_r,
                height=shaft_len,
                radial_segments=50,
                height_segments=10,
                wireframe=False,
            ).with_name(f"gizmo:{axis_key}")
            shaft.material(color, 0.9)
            shaft.move(0.0, dir_sign * (shaft_len / 2.0), 0.0)

            if sign == "+":
                head = ui.scene.cylinder(
                    top_radius=0.0,
                    bottom_radius=head_r,
                    height=head_len,
                    radial_segments=50,
                    height_segments=10,
                    wireframe=False,
                ).with_name(f"gizmo:{axis_key}")
                head.move(0.0, dir_sign * (shaft_len + head_len / 2.0), 0.0)
            else:
                head = ui.scene.cylinder(
                    top_radius=head_r,
                    bottom_radius=0.0,
                    height=head_len,
                    radial_segments=50,
                    height_segments=10,
                    wireframe=False,
                ).with_name(f"gizmo:{axis_key}")
                head.move(0.0, dir_sign * (shaft_len + head_len / 2.0), 0.0)
            head.material(color, 0.9)

        self._gizmo_handles.setdefault(axis_key, []).extend([shaft, head])
        self._handle_base_colors[axis_key] = color

    def _create_rotation_half(
        self,
        axis: str,
        sign: str,
        r_mid: float,
        arc_thickness: float,
        head_r: float,
        head_len: float,
    ) -> None:
        """Create a rotation handle as a curved arrow for the gizmo."""
        gr = self.gizmo_rotate_group
        if not gr:
            return
        axis = axis.upper()
        sign = "+" if sign == "+" else "-"
        axis_key = f"R{axis}{sign}"
        color = self._axis_color(axis)

        with gr:
            arrow_group = ui.scene.group().with_name(f"gizmo:{axis_key}")

        # Default lies in XY plane (normal +Z)
        if axis == "X":
            arrow_group.rotate(0.0, math.pi / 2.0, 0.0)  # normal along X
        elif axis == "Y":
            arrow_group.rotate(-math.pi / 2.0, 0.0, 0.0)  # normal along Y

        # Arc spans 90 degrees, centered in each half
        if sign == "+":
            theta_start = math.pi / 4  # 45 degrees
        else:
            theta_start = 5 * math.pi / 4  # 225 degrees
        theta_len = math.pi / 2  # 90 degrees

        # Place arrowhead at different ends for + and -
        if sign == "+":
            # For +: place at arc end, pointing forward (counter-clockwise)
            head_theta = theta_start + theta_len
            tangent_angle = head_theta + math.pi / 2.0
        else:
            # For -: place at arc start, pointing forward (clockwise)
            head_theta = theta_start
            tangent_angle = head_theta - math.pi / 2.0

        with arrow_group:
            # Curved shaft: thin ring segment
            r_in = r_mid - arc_thickness / 2.0
            r_out = r_mid + arc_thickness / 2.0
            curved_shaft = ui.scene.ring(
                inner_radius=r_in,
                outer_radius=r_out,
                theta_segments=64,
                phi_segments=1,
                theta_start=theta_start,
                theta_length=theta_len,
                wireframe=False,
            ).with_name(f"gizmo:{axis_key}")
            curved_shaft.material(color, 0.75, side="both")

            # Arrowhead: cone positioned tangentially
            # Base position at chosen arc point
            head_x = r_mid * math.cos(head_theta)
            head_y = r_mid * math.sin(head_theta)

            # Add small offset along tangent so cone tip doesn't overlap arc
            offset = 0.5 * head_len
            dx = offset * math.cos(tangent_angle)
            dy = offset * math.sin(tangent_angle)

            # Create cone (default points along +Y)
            arrowhead = ui.scene.cylinder(
                top_radius=0.0,
                bottom_radius=head_r,
                height=head_len,
                radial_segments=16,
                height_segments=4,
                wireframe=False,
            ).with_name(f"gizmo:{axis_key}")

            # Position and rotate to point tangentially
            arrowhead.move(head_x + dx, head_y + dy, 0.0)
            # Rotate cone to point along tangent (cone default is +Y, so rotate to tangent_angle)
            arrowhead.rotate(0.0, 0.0, tangent_angle - math.pi / 2.0)
            arrowhead.material(color, 0.9)

        self._gizmo_handles.setdefault(axis_key, []).extend([curved_shaft, arrowhead])
        self._handle_base_colors[axis_key] = color

    def _reparent_gizmo(self, parent) -> None:
        """Attach gizmo to parent and reset its local transform."""
        if not self.gizmo_group or parent is None:
            return
        self.gizmo_group.attach(parent)
        # Reset local transform relative to the new parent
        self.gizmo_group.move(0.0, 0.0, 0.0).rotate(0.0, 0.0, 0.0)

    def _update_gizmo_parent(self) -> None:
        """Update gizmo parent based on control frame setting."""
        if not self.gizmo_group:
            return
        if self._control_frame == "TRF":
            # Prefer TCP offset, then anchor, then last joint transform
            if self.tcp_offset:
                self._reparent_gizmo(self.tcp_offset)
            elif self.tcp_anchor:
                self._reparent_gizmo(self.tcp_anchor)
            else:
                parent = self.last_actuated_group
                if parent is not None:
                    self._reparent_gizmo(parent)
        else:
            # World frame: detach and reset local transform so origin is world origin
            self.gizmo_group.detach()
            self.gizmo_group.move(0.0, 0.0, 0.0).rotate(0.0, 0.0, 0.0)

    # --------- Click and hover handling ---------

    def _handle_scene_click(self, e) -> None:
        """Handle mouse events for gizmo interaction and hover effects."""
        click_type = getattr(e, "click_type", "")
        hits = getattr(e, "hits", []) or []

        if click_type == "mousedown":
            axis = None
            for h in hits:
                name = getattr(h, "object_name", "") or ""
                if name.startswith("gizmo:"):
                    candidate = name.split("gizmo:", 1)[1]
                    # Filter by current display mode
                    if self._is_handle_visible(candidate):
                        axis = candidate
                        break
            if axis:
                self._activate_axis(axis)
        elif click_type in ("mouseup", "mouseleave"):
            self._deactivate_active_axis()
            # Force clear hover state on mouseleave
            self._force_clear_hover()
        elif click_type == "mousemove":
            # Update hover state based on what's under the cursor
            axis = None
            for h in hits:
                name = getattr(h, "object_name", "") or ""
                if name.startswith("gizmo:"):
                    candidate = name.split("gizmo:", 1)[1]
                    # Filter by current display mode
                    if self._is_handle_visible(candidate):
                        axis = candidate
                        break
            self._set_hover_axis(axis)

    def _is_handle_visible(self, axis_key: str) -> bool:
        """Check if a handle should be interactive based on current display mode.

        Args:
            axis_key: Handle identifier like 'X+', 'Y-', 'RX+', 'RZ-'

        Returns:
            True if handle is in the currently visible mode
        """
        is_rotation = axis_key.startswith("R")
        if self._gizmo_display_mode == "TRANSLATE":
            return not is_rotation
        else:  # ROTATE mode
            return is_rotation

    def _activate_axis(self, axis_key: str) -> None:
        """Activate gizmo axis and notify callbacks with GizmoEvent."""
        self._active_axis = axis_key
        self._set_handle_pressed(axis_key, True)

        # Emit GizmoEvent
        event = self._create_gizmo_event(axis_key, GizmoEventKind.PRESS)
        self._dispatch_gizmo_event(event)

    def _deactivate_active_axis(self) -> None:
        """Deactivate active gizmo axis and notify callbacks with GizmoEvent."""
        if not self._active_axis:
            return
        axis = self._active_axis
        self._active_axis = None
        self._set_handle_pressed(axis, False)

        # Emit GizmoEvent
        event = self._create_gizmo_event(axis, GizmoEventKind.RELEASE)
        self._dispatch_gizmo_event(event)

    def _create_gizmo_event(self, axis_key: str, kind: GizmoEventKind) -> GizmoEvent:
        """Create a GizmoEvent from axis_key and kind."""
        # Parse axis_key to determine mode, axis, and sign
        if axis_key.startswith("R"):
            # Rotation handle like "RX+", "RY-", etc.
            mode = GizmoMode.ROTATE
            axis = axis_key[1]  # X, Y, or Z
            sign = 1 if axis_key[2] == "+" else -1
        else:
            # Translation handle like "X+", "Y-", etc.
            mode = GizmoMode.TRANSLATE
            axis = axis_key[0]  # X, Y, or Z
            sign = 1 if axis_key[1] == "+" else -1

        return GizmoEvent(
            kind=kind,
            mode=mode,
            axis=axis,
            sign=sign,
            handle=axis_key,
            frame=self._control_frame,
            timestamp=time.time(),
        )

    def _dispatch_gizmo_event(self, event: GizmoEvent) -> None:
        """Dispatch event to all registered callbacks."""
        for callback in self._gizmo_callbacks:
            try:
                callback(event)
            except Exception as e:
                logging.error(
                    "Error in gizmo event callback for handle %s: %s",
                    event.handle,
                    e,
                    exc_info=True,
                )

    def _force_clear_hover(self) -> None:
        """Force clear all hover states and reset ALL handle visuals to default."""
        # Clear hover state
        self._hover_axis = None
        # Reset ALL gizmo handles to their default appearance
        for axis_key in self._gizmo_handles.keys():
            self._update_handle_visuals(axis_key)

    def _set_hover_axis(self, axis_key: Optional[str]) -> None:
        """Update hover state and visual feedback."""
        if axis_key == self._hover_axis:
            return  # No change

        # Clear old hover
        if self._hover_axis:
            self._update_handle_visuals(self._hover_axis)

        # Set new hover
        self._hover_axis = axis_key
        if self._hover_axis:
            self._update_handle_visuals(self._hover_axis)

    def _set_handle_pressed(self, axis_key: str, pressed: bool) -> None:
        """Update pressed state and visual feedback."""
        self._update_handle_visuals(axis_key)

    def _update_handle_visuals(self, axis_key: str) -> None:
        """Update handle visuals based on current state (pressed > hover > normal)."""
        parts = self._gizmo_handles.get(axis_key, [])
        if not parts:
            return

        # Determine state precedence: pressed > hover > normal
        is_pressed = axis_key == self._active_axis
        is_hovered = (axis_key == self._hover_axis) and not is_pressed

        # Get stored base color for this handle (fallback to parts[0].color if not found)
        base_color = self._handle_base_colors.get(
            axis_key, parts[0].color if parts else "#888888"
        )

        # Determine if this is a rotation handle
        is_rotation = axis_key.startswith("R")

        # Compute color and opacity based on state
        if is_pressed:
            color = base_color
            opacity = 1.0
        elif is_hovered:
            color = self._lighten_color(base_color, 0.25)
            # Slight opacity boost on hover
            opacity = 0.85 if is_rotation else 0.95
        else:
            # Normal state: different default opacities for rotation vs translation
            color = base_color
            opacity = 0.75 if is_rotation else 0.90

        # Apply to all parts of this handle
        # For rotation rings, preserve side="both" rendering
        for p in parts:
            if is_rotation:
                p.material(color, opacity, side="both")
            else:
                p.material(color, opacity)

    @staticmethod
    def _lighten_color(hex_color: str, factor: float) -> str:
        """Lighten a hex color by blending with white.

        Args:
            hex_color: Hex color string like "#ff0000"
            factor: Blend factor 0-1 (0=original, 1=white)

        Returns:
            Lightened hex color string
        """
        # Remove # if present
        hex_color = hex_color.lstrip("#")

        # Parse RGB
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        # Blend with white
        r = int(r + (255 - r) * factor)
        g = int(g + (255 - g) * factor)
        b = int(b + (255 - b) * factor)

        # Return as hex
        return f"#{r:02x}{g:02x}{b:02x}"

    # --------- Helper math and URDF loading ---------

    @classmethod
    def _load_urdf(
        cls,
        path: Path,
        *,
        package_map: Optional[Dict[str, Path]] = None,
        lazy: bool = True,
    ) -> URDF:
        """Load a URDF file into memory, expanding package:// URIs to absolute paths.

        Args:
            path: Path to URDF file
            package_map: Optional mapping from package names to filesystem paths
            lazy: Whether to lazy-load meshes

        Returns:
            Loaded URDF object

        Raises:
            FileNotFoundError: If URDF file not found
            ValueError: If package:// URI found but package not in map and no fallback possible
        """
        package_map = package_map or {}

        if not path.exists():
            raise FileNotFoundError(f"URDF file not found: {path}")

        # Read URDF and find all package:// references
        with open(path, "r") as file:
            content = file.read()

        # Find all unique package names
        package_names = set(re.findall(r"package://(\w+)", content))

        if not package_names:
            # No package URIs, load directly
            return URDF.load(path, lazy_load_meshes=lazy)

        # Replace package:// URIs
        modified_content = content
        for pkg_name in package_names:
            if pkg_name in package_map:
                # Use provided mapping
                pkg_path = package_map[pkg_name]
                replacement = pkg_path.as_uri()
            else:
                # Fallback: use URDF parent directory
                replacement = path.parent.as_uri()
                logging.warning(
                    f"Package '{pkg_name}' not in package_map; using fallback: {replacement}"
                )

            modified_content = modified_content.replace(
                f"package://{pkg_name}", replacement
            )

        # Write to temporary file and load
        with tempfile.TemporaryDirectory() as tmpdirname:
            tmp_file_path = Path(tmpdirname) / path.name
            tmp_file_path.write_text(modified_content, encoding="utf-8")
            return URDF.load(tmp_file_path, lazy_load_meshes=lazy)

    @classmethod
    def get_transl_and_rpy(cls, mat) -> Tuple[np.ndarray, np.ndarray]:
        """Return translation and Euler rpy from 4x4 homogeneous transformation.

        Args:
            mat: 4x4 homogeneous transformation matrix

        Returns:
            Tuple of (translation, rpy) where translation is [x,y,z] and rpy is [roll,pitch,yaw]
        """
        trans = mat[:3, 3]
        rpy = R.from_matrix(mat[:3, :3]).as_euler("xyz", degrees=False)
        return trans, rpy

    @classmethod
    def rot_joint(
        cls, axis: np.ndarray, rot_rad: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Transformation for rotatory joint around `axis` with value `rot_rad` [rad].

        Args:
            axis: Joint axis of rotation (3D vector)
            rot_rad: Rotation angle in radians

        Returns:
            Tuple of (translation, rpy) - translation is zero for revolute joints
        """
        norm_axis = axis / np.linalg.norm(axis)
        rpy = R.from_rotvec(rot_rad * norm_axis).as_euler("xyz", degrees=False)
        t = np.zeros_like(rpy)
        return t, rpy

    @classmethod
    def transl_joint(
        cls, axis: np.ndarray, transl: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Transformation for translational joint along `axis` with value `transl` [m].

        Args:
            axis: Joint axis of translation (3D vector)
            transl: Translation distance in meters

        Returns:
            Tuple of (translation, rpy) - rpy is zero for prismatic joints
        """
        norm_axis = axis / np.linalg.norm(axis)
        t = transl * norm_axis
        rpy = np.zeros_like(t)
        return t, rpy
