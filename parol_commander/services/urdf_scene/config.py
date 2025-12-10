"""
Configuration dataclasses for UrdfScene.

Contains:
- RobotAppearanceMode: Enum for robot visual states (live, simulator, editing)
- ToolPose: TCP offset and orientation for a tool
- UrdfSceneConfig: Configuration for UrdfScene behavior, appearance, and kinematics
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence


class RobotAppearanceMode(Enum):
    """Robot visual appearance modes.

    LIVE: Normal robot view showing real-time joint angles from robot_state
    SIMULATOR: Amber/ghost appearance, still shows real-time angles
    EDITING: Grey semi-transparent appearance for target editing, shows editing angles
    """

    LIVE = "live"
    SIMULATOR = "simulator"
    EDITING = "editing"


@dataclass
class ToolPose:
    """TCP offset and orientation for a tool."""

    origin: Sequence[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rpy: Sequence[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])


@dataclass
class UrdfSceneConfig:
    """Configuration for UrdfScene behavior, appearance, and kinematics."""

    # --- Mesh and static file settings ---
    meshes_dir: Optional[Path] = None
    """Directory containing mesh files. If None, auto-discover from URDF location."""

    static_url_prefix: str = "/meshes"
    """URL prefix for serving static mesh files."""

    package_map: Dict[str, Path] = field(default_factory=lambda: {})
    """Mapping from package:// names to filesystem paths."""

    mount_static: bool = True
    """Whether to automatically mount meshes as static files."""

    scale_stls: float = 1.0
    """Scale factor for all STL files (e.g., 1e-1 if designed in mm)."""

    # --- Gizmo settings ---
    gizmo_scale: Optional[float] = None
    """Override gizmo size. If None, scales with STL scale."""

    draw_tcp_axes: bool = True
    """Whether to draw coordinate axes at TCP location."""

    # --- Tool pose settings ---
    tool_pose_map: Dict[str, "ToolPose"] = field(default_factory=lambda: {})
    """Mapping from tool names to TCP poses."""

    tool_pose_resolver: Optional[Callable[[str], Optional["ToolPose"]]] = None
    """Function to resolve tool name to TCP pose dynamically."""

    # --- Appearance settings ---
    material: str = "#888"
    """Default material color for robot meshes."""

    background_color: str = "#eee"
    """Scene background color."""

    sim_color: str = "#c77d28"
    """Color for robot in simulator mode (amber ghost)."""

    sim_opacity: float = 0.9
    """Opacity for robot in simulator mode."""

    edit_color: str = "#666666"
    """Color for robot in editing mode (grey ghost)."""

    edit_opacity: float = 0.4
    """Opacity for robot in editing mode."""

    # --- Kinematic mapping settings ---
    joint_name_order: List[str] = field(
        default_factory=lambda: ["L1", "L2", "L3", "L4", "L5", "L6"]
    )
    """Order of joint names for mapping controller angles to URDF joints."""

    deg_to_rad: bool = True
    """Whether to convert angles from degrees to radians."""

    angle_signs: List[int] = field(default_factory=lambda: [1, 1, 1, 1, 1, 1])
    """Sign corrections for each joint angle."""

    angle_offsets: List[float] = field(
        default_factory=lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    )
    """Offset corrections for each joint angle (in degrees if deg_to_rad is True)."""
