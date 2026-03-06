"""Path renderer mixin for UrdfScene.

This mixin handles rendering of path segments with optional dashing and direction arrows.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
from scipy.spatial.transform import Rotation as ScipyRotation
from nicegui import ui

if TYPE_CHECKING:
    from parol_commander.state import PathSegment, ToolAction

_DEFAULT_CONE_AXIS = np.array([0.0, 1.0, 0.0], dtype=np.float64)
_PI_ROTATION_RPY = [math.pi, 0.0, 0.0]
_ZERO_ROTATION_RPY = [0.0, 0.0, 0.0]


class PathRendererMixin:
    """Mixin providing path segment rendering functionality."""

    # Type hints for attributes used from main class
    scene: Any

    def _render_path_segment(
        self,
        segment: PathSegment,
        point_pair_colors: list[str] | None = None,
    ) -> tuple[list[Any], list[str]]:
        """Render a path segment with optional dashing and direction arrows.

        Args:
            segment: PathSegment object with points, color, is_dashed, show_arrows
            point_pair_colors: Optional per-point-pair colors (one per consecutive
                point pair). If None, uses segment.color for all objects.

        Returns:
            Tuple of (scene objects, display color per object)
        """
        objects: list[Any] = []
        object_colors: list[str] = []
        points = segment.points
        default_color = segment.color
        is_dashed = segment.is_dashed
        show_arrows = segment.show_arrows

        if len(points) < 2:
            return objects, object_colors

        # Dash parameters
        dash_length = 0.008  # 8mm dash
        gap_length = 0.004  # 4mm gap
        arrow_interval = 20  # Arrow every N point-pairs (density tracks speed)
        arrow_scale = 0.003  # Arrow cone size

        # Convert to numpy array once to avoid per-iteration allocation
        pts = np.asarray(points, dtype=np.float64)

        for i in range(len(points) - 1):
            color = point_pair_colors[i] if point_pair_colors else default_color

            p1 = pts[i]
            p2 = pts[i + 1]

            segment_vec = p2 - p1
            segment_length = float(np.linalg.norm(segment_vec))

            if segment_length < 1e-6:
                continue

            direction = segment_vec / segment_length

            if is_dashed:
                # Draw dashed line segments
                current_pos = 0.0
                drawing = True  # Start with a dash

                while current_pos < segment_length:
                    if drawing:
                        dash_end = min(current_pos + dash_length, segment_length)
                        start = p1 + direction * current_pos
                        end = p1 + direction * dash_end
                        line = ui.scene.line(start, end)
                        line.material(color)
                        objects.append(line)
                        object_colors.append(color)
                        current_pos = dash_end
                    else:
                        # Skip gap
                        current_pos += gap_length
                    drawing = not drawing
            else:
                # Draw solid line
                line = ui.scene.line(p1, p2)
                line.material(color)
                objects.append(line)
                object_colors.append(color)

            # Add direction arrows at regular point intervals
            if show_arrows and i % arrow_interval == 0:
                midpoint = (p1 + p2) * 0.5
                cone = self._create_direction_cone(
                    midpoint, direction, arrow_scale, color
                )
                objects.append(cone)
                object_colors.append(color)

        return objects, object_colors

    def _create_direction_cone(
        self,
        position: np.ndarray,
        direction: np.ndarray,
        scale: float,
        color: str,
    ) -> Any:
        """Create a small cone pointing in the given direction.

        Args:
            position: [x, y, z] position for the cone
            direction: [dx, dy, dz] normalized direction vector
            scale: Size of the cone
            color: Hex color for the cone

        Returns:
            Scene cone object
        """
        cross = np.cross(_DEFAULT_CONE_AXIS, direction)
        dot = np.dot(_DEFAULT_CONE_AXIS, direction)

        if np.linalg.norm(cross) < 1e-6:
            if dot > 0:
                rpy = _ZERO_ROTATION_RPY
            else:
                rpy = _PI_ROTATION_RPY
        else:
            angle = math.acos(max(-1.0, min(1.0, float(dot))))
            axis = cross / np.linalg.norm(cross)
            rot = ScipyRotation.from_rotvec(angle * axis)
            rpy = rot.as_euler("xyz", degrees=False).tolist()

        cone = ui.scene.cylinder(
            top_radius=0.0,
            bottom_radius=scale,
            height=scale * 2,
            radial_segments=8,
            height_segments=1,
            wireframe=False,
        )
        cone.move(*position)
        cone.rotate(*rpy)
        cone.material(color, 0.9)

        return cone

    def render_tool_action(
        self,
        action: ToolAction,
        color: str = "#FF9800",
    ) -> list[Any]:
        """Render a tool action as arrows at the TCP position.

        For linear motions: paired arrows showing jaw direction.
        For rotary motions: not yet implemented (ignored).

        Args:
            action: ToolAction with tcp_pose, motions, target_positions, etc.
            color: Hex color for the arrows.

        Returns:
            List of scene objects created.
        """
        objects: list[Any] = []
        if action.tcp_pose is None or len(action.tcp_pose) < 3:
            return objects

        px, py, pz = action.tcp_pose[0], action.tcp_pose[1], action.tcp_pose[2]

        for idx, motion in enumerate(action.motions):
            target = (
                action.target_positions[idx]
                if idx < len(action.target_positions)
                else 0.0
            )
            axis = motion.get("axis", (0, 0, 1))

            if motion.get("type") == "linear":
                travel = motion.get("travel_m", 0.01)
                arrow_length = travel * 0.5
                head_length = min(0.003, arrow_length * 0.4)
                head_width = head_length

                # Opening (target=1) → outward arrows, closing (target=0) → inward arrows
                outward = target > 0.5

                if motion.get("symmetric", True):
                    for sign in [1.0, -1.0]:
                        d = [axis[0] * sign, axis[1] * sign, axis[2] * sign]
                        if not outward:
                            d = [-d[0], -d[1], -d[2]]
                        offset = travel * 0.5 * sign
                        arrow_origin = [
                            px + axis[0] * offset,
                            py + axis[1] * offset,
                            pz + axis[2] * offset,
                        ]
                        arrow = ui.scene.arrow_helper(
                            direction=d,
                            origin=arrow_origin,
                            length=arrow_length,
                            head_length=head_length,
                            head_width=head_width,
                        )
                        arrow.material(color, 0.9)
                        objects.append(arrow)
                else:
                    d = list(axis) if outward else [-axis[0], -axis[1], -axis[2]]
                    arrow = ui.scene.arrow_helper(
                        direction=d,
                        origin=[px, py, pz],
                        length=arrow_length,
                        head_length=head_length,
                        head_width=head_width,
                    )
                    arrow.material(color, 0.9)
                    objects.append(arrow)

        return objects
