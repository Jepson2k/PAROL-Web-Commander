"""
UrdfScene package - Modular URDF scene visualization for NiceGUI.

This package provides:
- UrdfScene: Main class for URDF-based robot visualization
- UrdfSceneConfig: Configuration dataclass
- ToolPose: TCP offset dataclass
- RobotAppearanceMode: Enum for robot visual states
"""

from parol_commander.services.urdf_scene.angle_pipeline import (
    init_buffers as init_angle_buffers,
    update_urdf_angles,
)
from parol_commander.services.urdf_scene.config import (
    RobotAppearanceMode,
    ToolPose,
    UrdfSceneConfig,
)
from parol_commander.services.urdf_scene.urdf_scene import UrdfScene

__all__ = [
    "UrdfScene",
    "UrdfSceneConfig",
    "ToolPose",
    "RobotAppearanceMode",
    "init_angle_buffers",
    "update_urdf_angles",
]
