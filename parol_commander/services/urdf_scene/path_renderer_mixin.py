"""Path renderer mixin for UrdfScene.

This mixin handles rendering of path segments with optional dashing and direction arrows.
"""

import math
from typing import Any, List, Optional

import numpy as np
from scipy.spatial.transform import Rotation as ScipyRotation
from nicegui import ui

_DEFAULT_CONE_AXIS = np.array([0.0, 1.0, 0.0], dtype=np.float64)
_PI_ROTATION_RPY = [math.pi, 0.0, 0.0]
_ZERO_ROTATION_RPY = [0.0, 0.0, 0.0]


class PathRendererMixin:
    """Mixin providing path segment rendering functionality."""

    # Type hints for attributes used from main class
    scene: Any

    def _render_path_segment(self, segment) -> List[Any]:
        """Render a path segment with optional dashing and direction arrows.

        Args:
            segment: PathSegment object with points, color, is_dashed, show_arrows

        Returns:
            List of scene objects created for this segment
        """
        objects: List[Any] = []
        points = segment.points
        color = segment.color
        is_dashed = getattr(segment, "is_dashed", True)
        show_arrows = getattr(segment, "show_arrows", True)

        if len(points) < 2:
            return objects

        # Dash parameters
        dash_length = 0.008  # 8mm dash
        gap_length = 0.004  # 4mm gap
        arrow_spacing = 0.05  # Arrow every 50mm
        arrow_scale = 0.006  # Arrow cone size

        accumulated_distance = 0.0
        last_arrow_distance = 0.0

        for i in range(len(points) - 1):
            p1 = np.array(points[i])
            p2 = np.array(points[i + 1])

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
                        # Draw dash
                        dash_end = float(min(current_pos + dash_length, segment_length))  # type: ignore[arg-type]
                        start = p1 + direction * current_pos
                        end = p1 + direction * dash_end
                        line = ui.scene.line(start.tolist(), end.tolist())
                        line.material(color)
                        objects.append(line)
                        current_pos = dash_end
                    else:
                        # Skip gap
                        current_pos += gap_length
                    drawing = not drawing
            else:
                # Draw solid line
                line = ui.scene.line(p1.tolist(), p2.tolist())
                line.material(color)
                objects.append(line)

            # Add direction arrows at intervals
            if show_arrows:
                segment_start_distance = accumulated_distance
                segment_end_distance = accumulated_distance + segment_length

                # Place arrows at regular intervals
                arrow_pos = last_arrow_distance + arrow_spacing
                while arrow_pos < segment_end_distance:
                    if arrow_pos >= segment_start_distance:
                        # Calculate position along this segment
                        local_t = float(
                            (arrow_pos - segment_start_distance) / segment_length
                        )  # type: ignore[assignment]
                        arrow_point = p1 + direction * (local_t * segment_length)

                        # Create small cone pointing in direction of travel
                        cone = self._create_direction_arrow(
                            arrow_point.tolist(), direction.tolist(), arrow_scale, color
                        )
                        if cone:
                            objects.append(cone)

                        last_arrow_distance = arrow_pos
                    arrow_pos += arrow_spacing

                accumulated_distance = segment_end_distance

        return objects

    def _create_direction_arrow(
        self,
        position: List[float],
        direction: List[float],
        scale: float,
        color: str,
    ) -> Optional[Any]:
        """Create a small cone pointing in the given direction.

        Args:
            position: [x, y, z] position for arrow
            direction: [dx, dy, dz] normalized direction vector
            scale: Size of the arrow cone
            color: Hex color for the arrow

        Returns:
            Scene cone object or None
        """
        d = np.asarray(direction, dtype=np.float64)
        d_norm = np.linalg.norm(d)
        if d_norm < 1e-6:
            return None
        d = d / d_norm

        cross = np.cross(_DEFAULT_CONE_AXIS, d)
        dot = np.dot(_DEFAULT_CONE_AXIS, d)

        if np.linalg.norm(cross) < 1e-6:
            if dot > 0:
                rpy = _ZERO_ROTATION_RPY
            else:
                rpy = _PI_ROTATION_RPY
        else:
            angle = float(math.acos(np.clip(dot, -1.0, 1.0)))
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
