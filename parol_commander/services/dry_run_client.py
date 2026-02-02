"""
Dry-run robot client for offline simulation and path preview.

This module provides mock RobotClient implementations that intercept motion commands,
perform local FK/IK, and collect path segments for visualization.

Uses shared geometry generators from parol6.motion.geometry for accurate path
visualization of circles, arcs, splines, and joint-space TCP arcs.
"""

import linecache
import logging
import inspect
import re
from dataclasses import dataclass, field
from typing import Literal, cast, Any
import numpy as np
from numpy.typing import NDArray
from parol6.utils.se3_utils import se3_from_rpy, se3_rpy, so3_rpy

# Eagerly import parol6 dependencies at module level
# This ensures they're in sys.modules BEFORE path_visualizer replaces parol6 with mock
import parol6.PAROL6_ROBOT as PAROL6_ROBOT
from parol6.config import STANDBY_ANGLES_DEG
from parol6.motion.geometry import (
    CircularMotion,
    SplineMotion,
    joint_path_to_tcp_poses,
    PLANE_NORMALS,
)
from parol6.motion.trajectory import JointPath, ProfileType, TrajectoryBuilder
from parol6.utils.ik import check_limits, solve_ik

from parol_commander.common.theme import MOVE_TYPE_COLORS

logger = logging.getLogger(__name__)

# Default standby position in radians - use PAROL6's actual standby position (kept as array)
DEFAULT_STANDBY_RAD = np.deg2rad(STANDBY_ANGLES_DEG)

# Pre-compiled regex patterns for performance
_TARGET_MARKER_RE = re.compile(r"#\s*TARGET:(\w+)")
_LITERAL_LIST_RE = re.compile(
    r"move_(?:joints|cartesian|pose)\s*\(\s*\["
    r"\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
    r"(?:\s*,\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)*\s*\]"
)


def _pad_pose(pose: list[float]) -> list[float]:
    """Pad a pose list to 6 elements with zeros."""
    if len(pose) >= 6:
        return pose[:6]
    return pose + [0.0] * (6 - len(pose))


def get_color_for_move_type(move_type: str, is_valid: bool = True) -> str:
    """Get the appropriate color for a move type.

    Args:
        move_type: The type of move (cartesian, joints, smooth, etc.)
        is_valid: Whether the move is reachable (IK valid)

    Returns:
        Hex color string
    """
    if not is_valid:
        return MOVE_TYPE_COLORS["invalid"]

    move_type_lower = move_type.lower() if move_type else "unknown"

    # Check for exact match first
    if move_type_lower in MOVE_TYPE_COLORS:
        return MOVE_TYPE_COLORS[move_type_lower]

    # Check for partial matches
    if "smooth" in move_type_lower:
        return MOVE_TYPE_COLORS["smooth"]
    if "joint" in move_type_lower:
        return MOVE_TYPE_COLORS["joints"]
    if "cartesian" in move_type_lower or "pose" in move_type_lower:
        return MOVE_TYPE_COLORS["cartesian"]

    return MOVE_TYPE_COLORS["unknown"]


@dataclass
class DryRunRobotClient:
    """
    Mock RobotClient for offline simulation and path preview (Synchronous).

    Intercepts motion commands, performs local FK/IK, and collects path segment
    data into the provided collectors. Designed to run in isolated subprocesses.

    Args:
        segment_collector: List to append path segment dicts to (optional)
        target_collector: List to append program target dicts to (optional)
        initial_joints: Initial joint angles in radians (optional, defaults to standby)
        initial_pose: Initial pose [x,y,z,rx,ry,rz] in meters/degrees (optional, overrides FK)
        host: Ignored (for API compatibility)
        port: Ignored (for API compatibility)
    """

    # Output collectors (injected, not global state)
    segment_collector: list[dict] = field(default_factory=list)
    target_collector: list[dict] = field(default_factory=list)

    # Initial joint state (optional - if None, uses DEFAULT_STANDBY_RAD)
    initial_joints: list[float] | np.ndarray | None = None

    # Initial pose (optional - if provided, use directly instead of FK computation)
    # Format: [x, y, z, rx, ry, rz] where x/y/z in meters, rx/ry/rz in degrees
    initial_pose: list[float] | None = None

    # Internal state tracking (mocking the real robot state)
    # Use numpy array for joints to avoid repeated conversions
    _current_joints: np.ndarray = field(
        default_factory=lambda: np.zeros(6, dtype=np.float64)
    )
    _current_pose: list[float] = field(
        default_factory=lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    )
    _rpy_buffer: np.ndarray = field(
        default_factory=lambda: np.zeros(3, dtype=np.float64)
    )
    _tool_name: str = "NONE"

    # Configuration (for API compatibility)
    host: str = "127.0.0.1"
    port: int = 5001

    def __post_init__(self):
        logger.debug("DryRunRobotClient initialized (isolated collector mode)")
        # Use provided initial joints or fall back to standby position
        if self.initial_joints is not None:
            self._current_joints = np.asarray(self.initial_joints, dtype=np.float64)
            logger.debug("  Using provided initial joints: %s", self._current_joints)
        else:
            self._current_joints = DEFAULT_STANDBY_RAD.copy()
            logger.debug("  Using default standby position")

        # Use provided initial pose directly if available (more accurate than FK)
        # Otherwise compute from joints using FK
        if self.initial_pose is not None and len(self.initial_pose) >= 6:
            self._current_pose = list(self.initial_pose)
            logger.debug("  Using provided initial pose: %s", self._current_pose)
        else:
            self._update_pose_from_joints()
            logger.debug("  Computed initial pose from FK: %s", self._current_pose)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass

    def close(self):
        pass

    def _update_pose_from_joints(self):
        """Update internal cartesian pose based on current joint angles using FK."""
        try:
            if PAROL6_ROBOT.robot is None:
                logger.warning("Robot model not initialized, using default pose")
                return

            robot = cast(Any, PAROL6_ROBOT.robot)
            T = robot.fkine(self._current_joints)

            so3_rpy(T[:3, :3], self._rpy_buffer)
            self._current_pose = [
                float(T[0, 3]),
                float(T[1, 3]),
                float(T[2, 3]),
                float(np.degrees(self._rpy_buffer[0])),
                float(np.degrees(self._rpy_buffer[1])),
                float(np.degrees(self._rpy_buffer[2])),
            ]
        except Exception as e:
            logger.warning("FK calculation failed: %s, keeping current pose", e)

    def _get_current_pose_mm(self) -> list[float]:
        """Get current pose in mm/deg format for geometry generators."""
        return [
            self._current_pose[0] * 1000.0,
            self._current_pose[1] * 1000.0,
            self._current_pose[2] * 1000.0,
            self._current_pose[3],
            self._current_pose[4],
            self._current_pose[5],
        ]

    def _get_caller_line_number(self) -> int:
        """Attempt to find the line number in the executed script."""
        try:
            frame = inspect.currentframe()
            while frame:
                if frame.f_code.co_filename == "simulation_script.py":
                    return frame.f_lineno
                frame = frame.f_back
        except Exception:
            pass
        return 0

    def _get_source_line(self, line_no: int) -> str:
        """Get the source code line from the executed script."""
        try:
            line = linecache.getline("simulation_script.py", line_no)
            if line:
                return line.strip()
        except Exception:
            pass
        return ""

    def _extract_target_marker(self, line: str) -> str | None:
        """Extract TARGET:uuid marker from a code line comment."""
        match = _TARGET_MARKER_RE.search(line)
        return match.group(1) if match else None

    def _has_literal_list_args(self, line: str) -> bool:
        """Check if move command has literal list arguments (not variables)."""
        return bool(_LITERAL_LIST_RE.search(line))

    def _collect_segment(
        self,
        start_pose: list[float],
        end_pose: list[float],
        valid: bool,
        move_type: str,
    ):
        """Add a path segment to the collector.

        Path segments are always created (for visualization).
        Interactive targets are only created if the line has a TARGET marker
        and literal list arguments.
        """
        line_no = self._get_caller_line_number()
        source_line = self._get_source_line(line_no)

        # Create path segment dict (serializable)
        segment = {
            "points": [
                [start_pose[0], start_pose[1], start_pose[2]],
                [end_pose[0], end_pose[1], end_pose[2]],
            ],
            "color": get_color_for_move_type(move_type, valid),
            "is_valid": valid,
            "line_number": line_no,
            "joints": self._current_joints.tolist(),
            "move_type": move_type,
            "is_dashed": True,
            "show_arrows": True,
        }
        self.segment_collector.append(segment)

        # Create interactive target for moves with literal args
        # Targets allow 3D editing of positions in the scene
        marker_id = self._extract_target_marker(source_line)
        has_literal_args = self._has_literal_list_args(source_line)

        if has_literal_args:
            # Use explicit marker or auto-generate one for literal moves
            target_id = marker_id or f"auto_{line_no}"
            target = {
                "id": target_id,
                "line_number": line_no,
                "pose": list(end_pose),
                "move_type": move_type,
                "scene_object_id": "",
            }
            self.target_collector.append(target)
            if marker_id:
                logger.debug("Created target %s at line %d", target_id, line_no)
            else:
                logger.debug("Auto-generated target %s at line %d", target_id, line_no)
        elif marker_id:
            # Has marker but uses variables - can't create editable target
            logger.debug(
                "Skipped target %s - line has variable args (not editable)", marker_id
            )

    def _collect_multipoint_segment(
        self,
        poses_m: NDArray | list[list[float]],
        valid: bool,
        move_type: str,
        estimated_duration: float | None = None,
        requested_duration: float | None = None,
    ):
        """Add a multi-point path segment for smooth motion visualization.

        Args:
            poses_m: (N, 6) array or list of [x_m, y_m, z_m, rx, ry, rz] poses
            valid: Whether the entire path is reachable
            move_type: Type of motion (smooth_circle, smooth_arc, etc.)
            estimated_duration: Computed duration from trajectory builder
            requested_duration: User-requested duration
        """
        line_no = self._get_caller_line_number()

        # Convert to points format: [[x, y, z], [x, y, z], ...]
        if isinstance(poses_m, np.ndarray):
            points = poses_m[:, :3].tolist()
        else:
            points = [[p[0], p[1], p[2]] for p in poses_m]

        # Determine color based on validity and timing feasibility
        timing_feasible = (
            estimated_duration is None
            or requested_duration is None
            or estimated_duration <= requested_duration * 1.1  # 10% tolerance
        )

        if not valid:
            color = MOVE_TYPE_COLORS["invalid"]
        elif not timing_feasible:
            color = MOVE_TYPE_COLORS["timing_warning"]
        else:
            color = get_color_for_move_type(move_type, valid)

        segment = {
            "points": points,
            "color": color,
            "is_valid": valid,
            "line_number": line_no,
            "joints": self._current_joints.tolist(),
            "move_type": move_type,
            "is_dashed": False,  # Solid line for actual geometry paths
            "show_arrows": True,
            "estimated_duration": estimated_duration,
            "requested_duration": requested_duration,
            "timing_feasible": timing_feasible,
        }
        self.segment_collector.append(segment)

        # Create interactive target for moves with literal args (like _collect_segment)
        source_line = self._get_source_line(line_no)
        marker_id = self._extract_target_marker(source_line)
        has_literal_args = self._has_literal_list_args(source_line)

        if has_literal_args:
            # Use endpoint of path as target position
            if isinstance(poses_m, np.ndarray):
                end_pose = poses_m[-1].tolist()
            else:
                end_pose = list(poses_m[-1])

            target_id = marker_id or f"auto_{line_no}"
            target = {
                "id": target_id,
                "line_number": line_no,
                "pose": end_pose,
                "move_type": move_type,
                "scene_object_id": "",
            }
            self.target_collector.append(target)
            if marker_id:
                logger.debug("Created target %s at line %d", target_id, line_no)
            else:
                logger.debug("Auto-generated target %s at line %d", target_id, line_no)
        elif marker_id:
            logger.debug(
                "Skipped target %s - line has variable args (not editable)", marker_id
            )

    def _validate_path_ik(
        self,
        poses_mm_deg: NDArray,
    ) -> tuple[NDArray | None, bool]:
        """Validate each pose in path via IK, returning joint positions and validity.

        Args:
            poses_mm_deg: (N, 6) array of [x_mm, y_mm, z_mm, rx_deg, ry_deg, rz_deg]

        Returns:
            (joint_positions, all_valid): Joint path array and overall validity
        """
        if PAROL6_ROBOT.robot is None:
            return None, False

        robot = cast(Any, PAROL6_ROBOT.robot)
        n_poses = len(poses_mm_deg)
        joint_positions = np.empty((n_poses, 6), dtype=np.float64)
        all_valid = True
        q_prev = self._current_joints.copy()

        T_target = np.zeros((4, 4), dtype=np.float64)
        for i, pose in enumerate(poses_mm_deg):
            se3_from_rpy(
                pose[0] / 1000.0,  # mm -> m
                pose[1] / 1000.0,
                pose[2] / 1000.0,
                np.radians(pose[3]),
                np.radians(pose[4]),
                np.radians(pose[5]),
                T_target,
            )
            ik_res = solve_ik(robot, T_target, q_prev, quiet_logging=True)
            if ik_res.success and ik_res.q is not None:
                joint_positions[i] = ik_res.q
                q_prev = ik_res.q
            else:
                joint_positions[i] = q_prev  # Use last good position
                all_valid = False

        return joint_positions, all_valid

    def _validate_timing(
        self,
        joint_positions: NDArray,
        requested_duration: float | None,
        velocity_percent: float = 100.0,
    ) -> tuple[float | None, bool]:
        """Validate if motion is achievable in requested time using TrajectoryBuilder.

        Args:
            joint_positions: (N, 6) joint angles in radians
            requested_duration: User-requested duration in seconds
            velocity_percent: Velocity scaling (default 100%)

        Returns:
            (estimated_duration, is_feasible): Computed duration and feasibility
        """
        try:
            joint_path = JointPath(positions=joint_positions)
            builder = TrajectoryBuilder(
                joint_path=joint_path,
                profile=ProfileType.TOPPRA,
                velocity_percent=velocity_percent,
            )
            trajectory = builder.build()
            estimated = trajectory.duration

            feasible = (
                requested_duration is None or estimated <= requested_duration * 1.1
            )
            return estimated, feasible
        except Exception as e:
            logger.debug("Timing validation failed: %s", e)
            return None, True  # Assume feasible if validation fails

    # --- Motion Interface Implementation ---

    def move_joints(
        self,
        joint_angles: list[float],
        duration: float | None = None,
        speed: int | None = None,
        accel: int | None = None,
        profile: str | None = None,
        tracking: str | None = None,
        wait: bool = True,
    ) -> bool:
        try:
            target_rad = np.deg2rad(joint_angles)

            try:
                valid = check_limits(
                    self._current_joints, target_rad, allow_recovery=True, log=True
                )
            except Exception as e:
                logger.warning("Limit check failed: %s, assuming valid", e)
                valid = True

            # Generate TCP arc for joint-space motion (joints interpolate linearly,
            # but TCP traces an arc in Cartesian space)
            n_samples = 20
            t = np.linspace(0, 1, n_samples)
            joint_path = self._current_joints + np.outer(
                t, target_rad - self._current_joints
            )

            # Convert to TCP poses via FK
            tcp_poses_mm = joint_path_to_tcp_poses(joint_path)
            # Convert mm to m for internal storage
            tcp_poses_m = tcp_poses_mm.copy()
            tcp_poses_m[:, :3] /= 1000.0

            # Timing validation
            estimated_duration, _ = self._validate_timing(
                joint_path, duration, velocity_percent=speed or 100.0
            )

            # Collect as multi-point segment (shows actual TCP arc)
            self._collect_multipoint_segment(
                tcp_poses_m,
                valid,
                "joints",
                estimated_duration=estimated_duration,
                requested_duration=duration,
            )

            # Update state
            self._current_joints = target_rad
            self._update_pose_from_joints()
            return True
        except Exception as e:
            logger.error("move_joints simulation failed: %s", e)
            return False

    def move_cartesian(
        self,
        pose: list[float],
        duration: float | None = None,
        speed: float | None = None,
        accel: int | None = None,
        profile: str | None = None,
        tracking: str | None = None,
        wait: bool = True,
    ) -> bool:
        try:
            start_pose = self._current_pose

            pos_m = [pose[0] / 1000.0, pose[1] / 1000.0, pose[2] / 1000.0]
            T_target = np.zeros((4, 4), dtype=np.float64)
            se3_from_rpy(
                pos_m[0],
                pos_m[1],
                pos_m[2],
                np.radians(pose[3]),
                np.radians(pose[4]),
                np.radians(pose[5]),
                T_target,
            )

            if PAROL6_ROBOT.robot is None:
                logger.warning("Robot model not initialized for IK")
                end_pose_m = [pos_m[0], pos_m[1], pos_m[2], pose[3], pose[4], pose[5]]
                self._collect_segment(start_pose, end_pose_m, False, "cartesian")
                return False

            robot = cast(Any, PAROL6_ROBOT.robot)
            ik_res = solve_ik(robot, T_target, self._current_joints)

            valid = ik_res.success
            end_pose_m = [pos_m[0], pos_m[1], pos_m[2], pose[3], pose[4], pose[5]]

            if valid and ik_res.q is not None:
                self._current_joints = ik_res.q
                self._current_pose = end_pose_m

            self._collect_segment(start_pose, end_pose_m, valid, "cartesian")
            return True
        except Exception as e:
            logger.error("move_cartesian simulation failed: %s", e)
            return False

    def move_pose(
        self,
        pose: list[float],
        duration: float | None = None,
        speed: int | None = None,
        accel: int | None = None,
        profile: str | None = None,
        tracking: str | None = None,
        wait: bool = True,
    ) -> bool:
        """Move to cartesian pose (alias for move_cartesian)."""
        return self.move_cartesian(
            pose, duration, speed, accel, profile, tracking, wait
        )

    def move_cartesian_rel_trf(
        self,
        deltas: list[float],
        duration: float | None = None,
        speed: float | None = None,
        accel: int | None = None,
        profile: str | None = None,
        tracking: str | None = None,
        wait: bool = True,
    ) -> bool:
        """Move relative to tool reference frame.

        Deltas are in mm for translation and degrees for rotation.
        Uses post-multiplication for TRF: target = current * delta
        """
        try:
            # Current pose is stored in meters for x/y/z
            # _current_pose = [x_m, y_m, z_m, rx_deg, ry_deg, rz_deg]
            T_current = np.zeros((4, 4), dtype=np.float64)
            se3_from_rpy(
                self._current_pose[0],  # x in meters
                self._current_pose[1],  # y in meters
                self._current_pose[2],  # z in meters
                np.radians(self._current_pose[3]),  # rx in radians
                np.radians(self._current_pose[4]),  # ry in radians
                np.radians(self._current_pose[5]),  # rz in radians
                T_current,
            )

            # Create delta transform in tool frame
            # Deltas: [dx, dy, dz] in mm -> convert to meters
            # Deltas: [rx, ry, rz] in degrees
            delta_se3 = np.zeros((4, 4), dtype=np.float64)
            se3_from_rpy(
                deltas[0] / 1000.0,  # dx mm -> m
                deltas[1] / 1000.0,  # dy mm -> m
                deltas[2] / 1000.0,  # dz mm -> m
                np.radians(deltas[3] if len(deltas) > 3 else 0.0),
                np.radians(deltas[4] if len(deltas) > 4 else 0.0),
                np.radians(deltas[5] if len(deltas) > 5 else 0.0),
                delta_se3,
            )

            # Apply in TRF: post-multiply for tool-relative motion
            T_target = T_current @ delta_se3

            # Extract new pose [x_m, y_m, z_m, rx_deg, ry_deg, rz_deg]
            trans = T_target[:3, 3]
            rpy_rad = np.zeros(3, dtype=np.float64)
            se3_rpy(T_target, rpy_rad)
            rpy_deg = np.degrees(rpy_rad)
            new_pose_m = [
                float(trans[0]),
                float(trans[1]),
                float(trans[2]),
                float(rpy_deg[0]),
                float(rpy_deg[1]),
                float(rpy_deg[2]),
            ]

            # Call move_cartesian with pose in mm (it expects mm input)
            new_pose_mm = [
                new_pose_m[0] * 1000.0,  # x m -> mm
                new_pose_m[1] * 1000.0,  # y m -> mm
                new_pose_m[2] * 1000.0,  # z m -> mm
                new_pose_m[3],  # rx in degrees
                new_pose_m[4],  # ry in degrees
                new_pose_m[5],  # rz in degrees
            ]
            return self.move_cartesian(
                new_pose_mm, duration, speed, accel, profile, tracking, wait
            )
        except Exception as e:
            logger.error("move_cartesian_rel_trf simulation failed: %s", e)
            return False

    # --- Smooth Motion Methods (basic visualization) ---

    def smooth_waypoints(
        self,
        waypoints: list[list[float]],
        blend_radii: Any = "AUTO",
        blend_mode: str = "parabolic",
        via_modes: list[str] | None = None,
        max_velocity: float = 100.0,
        max_acceleration: float = 500.0,
        frame: str = "WRF",
        trajectory_type: str = "quintic",
        duration: float | None = None,
        wait: bool = True,
    ) -> bool:
        """Create path segments for smooth waypoint motion."""
        if not waypoints:
            return True

        for wp in waypoints:
            wp_full = _pad_pose(wp)
            pos_m = [wp_full[0] / 1000.0, wp_full[1] / 1000.0, wp_full[2] / 1000.0]
            end_pose_m = [
                pos_m[0],
                pos_m[1],
                pos_m[2],
                wp_full[3],
                wp_full[4],
                wp_full[5],
            ]
            start_pose = self._current_pose

            try:
                T_target = np.zeros((4, 4), dtype=np.float64)
                se3_from_rpy(
                    pos_m[0],
                    pos_m[1],
                    pos_m[2],
                    np.radians(end_pose_m[3]),
                    np.radians(end_pose_m[4]),
                    np.radians(end_pose_m[5]),
                    T_target,
                )

                if PAROL6_ROBOT.robot is not None:
                    robot = cast(Any, PAROL6_ROBOT.robot)
                    ik_res = solve_ik(robot, T_target, self._current_joints)
                    valid = ik_res.success
                    if valid and ik_res.q is not None:
                        self._current_joints = ik_res.q
                else:
                    valid = True
            except Exception:
                valid = True

            self._current_pose = end_pose_m
            self._collect_segment(start_pose, end_pose_m, valid, "smooth_waypoints")

        return True

    def smooth_spline(
        self,
        waypoints: list[list[float]],
        frame: str = "WRF",
        start_pose: list[float] | None = None,
        duration: float | None = None,
        speed: float | None = None,
        trajectory_type: str = "cubic",
        jerk_limit: float | None = None,
        wait: bool = True,
    ) -> bool:
        """Create full spline path using geometry generator."""
        if not waypoints or len(waypoints) < 2:
            return True

        try:
            current_mm = self._get_current_pose_mm()

            # Prepend current position if first waypoint is far
            wps = [_pad_pose(wp) for wp in waypoints]
            first_wp_dist = np.linalg.norm(
                np.array(wps[0][:3]) - np.array(current_mm[:3])
            )
            if first_wp_dist > 5.0:  # More than 5mm away
                wps = [current_mm] + wps

            # Generate spline geometry
            gen = SplineMotion(control_rate=50)
            poses_mm = gen.generate_spline(waypoints=wps, duration=duration)

            # Validate IK for each point
            joint_positions, all_valid = self._validate_path_ik(poses_mm)

            # Timing validation
            estimated_duration = None
            if joint_positions is not None:
                estimated_duration, _ = self._validate_timing(
                    joint_positions, duration, velocity_percent=speed or 100.0
                )

            # Convert to meters for internal storage
            poses_m = poses_mm.copy()
            poses_m[:, :3] /= 1000.0

            self._collect_multipoint_segment(
                poses_m,
                all_valid,
                "smooth_spline",
                estimated_duration=estimated_duration,
                requested_duration=duration,
            )

            # Update state
            self._current_pose = poses_m[-1].tolist()
            if joint_positions is not None and all_valid:
                self._current_joints = joint_positions[-1]

            return True
        except Exception as e:
            logger.error("smooth_spline simulation failed: %s", e)
            return False

    def smooth_circle(
        self,
        center: list[float],
        radius: float,
        plane: str = "XY",
        frame: str = "WRF",
        center_mode: str = "ABSOLUTE",
        entry_mode: str = "NONE",
        start_pose: list[float] | None = None,
        duration: float | None = None,
        speed: float | None = None,
        clockwise: bool = False,
        trajectory_type: str = "cubic",
        jerk_limit: float | None = None,
        wait: bool = True,
    ) -> bool:
        """Create full circle path using geometry generator."""
        try:
            current_mm = self._get_current_pose_mm()

            # Handle center_mode
            if center_mode == "TOOL":
                actual_center = current_mm[:3]
            elif center_mode == "RELATIVE":
                actual_center = [
                    current_mm[0] + center[0],
                    current_mm[1] + center[1],
                    current_mm[2] + (center[2] if len(center) > 2 else 0.0),
                ]
            else:  # ABSOLUTE
                actual_center = list(center[:3]) if len(center) >= 3 else center + [0.0]

            # Get plane normal
            normal = PLANE_NORMALS.get(plane.upper(), PLANE_NORMALS["XY"])

            # Generate circle geometry (returns poses in mm)
            gen = CircularMotion(control_rate=50)  # Lower rate for preview
            geom_duration = duration if duration is not None else 4.0
            poses_mm = gen.generate_circle(
                center=actual_center,
                radius=radius,
                normal=normal,
                duration=geom_duration,
                start_point=current_mm,
            )

            if clockwise:
                poses_mm = poses_mm[::-1]

            # Update orientations to match current pose
            poses_mm[:, 3:] = current_mm[3:]

            # Validate IK for each point
            joint_positions, all_valid = self._validate_path_ik(poses_mm)

            # Timing validation
            estimated_duration = None
            if joint_positions is not None:
                estimated_duration, _ = self._validate_timing(
                    joint_positions, duration, velocity_percent=speed or 100.0
                )

            # Convert to meters for internal storage
            poses_m = poses_mm.copy()
            poses_m[:, :3] /= 1000.0

            self._collect_multipoint_segment(
                poses_m,
                all_valid,
                "smooth_circle",
                estimated_duration=estimated_duration,
                requested_duration=duration,
            )

            # Update state to end of circle (back to start)
            self._current_pose = poses_m[-1].tolist()
            if joint_positions is not None and all_valid:
                self._current_joints = joint_positions[-1]

            return True
        except Exception as e:
            logger.error("smooth_circle simulation failed: %s", e)
            return False

    def smooth_arc_center(
        self,
        end_pose: list[float],
        center: list[float],
        frame: str = "WRF",
        start_pose: list[float] | None = None,
        duration: float | None = None,
        speed: float | None = None,
        clockwise: bool = False,
        trajectory_type: str = "cubic",
        jerk_limit: float | None = None,
        wait: bool = True,
    ) -> bool:
        """Create full arc path using geometry generator (center-defined)."""
        try:
            current_mm = self._get_current_pose_mm()
            ep = _pad_pose(end_pose)

            # Generate arc geometry (returns poses in mm)
            gen = CircularMotion(control_rate=50)
            geom_duration = duration if duration is not None else 2.0
            poses_mm = gen.generate_arc(
                start_pose=current_mm,
                end_pose=ep,
                center=center[:3],
                clockwise=clockwise,
                duration=geom_duration,
            )

            # Validate IK for each point
            joint_positions, all_valid = self._validate_path_ik(poses_mm)

            # Timing validation
            estimated_duration = None
            if joint_positions is not None:
                estimated_duration, _ = self._validate_timing(
                    joint_positions, duration, velocity_percent=speed or 100.0
                )

            # Convert to meters for internal storage
            poses_m = poses_mm.copy()
            poses_m[:, :3] /= 1000.0

            self._collect_multipoint_segment(
                poses_m,
                all_valid,
                "smooth_arc",
                estimated_duration=estimated_duration,
                requested_duration=duration,
            )

            # Update state
            self._current_pose = poses_m[-1].tolist()
            if joint_positions is not None and all_valid:
                self._current_joints = joint_positions[-1]

            return True
        except Exception as e:
            logger.error("smooth_arc_center simulation failed: %s", e)
            return False

    def smooth_arc_param(
        self,
        end_pose: list[float],
        radius: float,
        arc_angle: float,
        frame: str = "WRF",
        start_pose: list[float] | None = None,
        duration: float | None = None,
        speed: float | None = None,
        trajectory_type: str = "cubic",
        jerk_limit: float | None = None,
        clockwise: bool = False,
        wait: bool = True,
    ) -> bool:
        """Create full arc path using geometry generator (parameter-defined)."""
        try:
            current_mm = self._get_current_pose_mm()
            ep = _pad_pose(end_pose)

            # Generate arc geometry from endpoints and radius
            gen = CircularMotion(control_rate=50)
            geom_duration = duration if duration is not None else 2.0
            poses_mm = gen.generate_arc_from_endpoints(
                start_pose=current_mm,
                end_pose=ep,
                radius=radius,
                clockwise=clockwise,
                duration=geom_duration,
            )

            # Validate IK for each point
            joint_positions, all_valid = self._validate_path_ik(poses_mm)

            # Timing validation
            estimated_duration = None
            if joint_positions is not None:
                estimated_duration, _ = self._validate_timing(
                    joint_positions, duration, velocity_percent=speed or 100.0
                )

            # Convert to meters for internal storage
            poses_m = poses_mm.copy()
            poses_m[:, :3] /= 1000.0

            self._collect_multipoint_segment(
                poses_m,
                all_valid,
                "smooth_arc",
                estimated_duration=estimated_duration,
                requested_duration=duration,
            )

            # Update state
            self._current_pose = poses_m[-1].tolist()
            if joint_positions is not None and all_valid:
                self._current_joints = joint_positions[-1]

            return True
        except Exception as e:
            logger.error("smooth_arc_param simulation failed: %s", e)
            return False

    def smooth_helix(
        self,
        center: list[float],
        radius: float,
        pitch: float,
        height: float,
        frame: str = "WRF",
        trajectory_type: str = "cubic",
        jerk_limit: float | None = None,
        start_pose: list[float] | None = None,
        duration: float | None = None,
        speed: float | None = None,
        clockwise: bool = False,
        wait: bool = True,
    ) -> bool:
        """Create simplified path segment for helix motion."""
        center_m = [
            center[0] / 1000.0,
            center[1] / 1000.0,
            center[2] / 1000.0 if len(center) > 2 else 0.0,
        ]
        radius_m = radius / 1000.0
        height_m = height / 1000.0

        current = self._current_pose
        end = [
            center_m[0] + radius_m,
            center_m[1],
            current[2] + height_m,
            current[3],
            current[4],
            current[5],
        ]
        self._collect_segment(current, end, True, "smooth_helix")
        self._current_pose = end
        return True

    def smooth_blend(
        self,
        segments: list[dict],
        blend_time: float = 0.5,
        frame: str = "WRF",
        start_pose: list[float] | None = None,
        duration: float | None = None,
        speed: float | None = None,
        wait: bool = True,
    ) -> bool:
        """Create path segments for blended motion."""
        for seg in segments:
            if "pose" in seg:
                p = _pad_pose(seg["pose"])
                end_m = [p[0] / 1000.0, p[1] / 1000.0, p[2] / 1000.0, p[3], p[4], p[5]]
                self._collect_segment(self._current_pose, end_m, True, "smooth")
                self._current_pose = end_m
        return True

    # --- Jog Methods (no path visualization for interactive jog) ---

    def jog_joint(
        self,
        joint_index: int,
        speed: int,
        duration: float,
    ) -> bool:
        """Jog single joint - no path visualization (interactive command)."""
        return True

    def jog_cartesian(
        self,
        frame: Any,
        axis: Any,
        speed: int,
        duration: float,
        wait: bool = True,
    ) -> bool:
        """Jog in cartesian space - no path visualization (interactive command)."""
        return True

    def jog_multiple(
        self,
        joints: list[int],
        speeds: list[float],
        duration: float,
        wait: bool = True,
    ) -> bool:
        """Jog multiple joints - no path visualization."""
        return True

    # --- Stubbed methods to satisfy interface ---

    def home(self, wait: bool = True) -> bool:
        """Move to home position. Creates path segment if not first command."""
        start_pose = self._current_pose
        self._current_joints = DEFAULT_STANDBY_RAD.copy()
        self._update_pose_from_joints()

        if self.segment_collector:
            self._collect_segment(start_pose, self._current_pose, True, "joints")

        return True

    def enable(self) -> bool:
        return True

    def disable(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def start(self) -> bool:
        return True

    def stream_on(self) -> bool:
        return True

    def stream_off(self) -> bool:
        return True

    def simulator_on(self) -> bool:
        return True

    def simulator_off(self) -> bool:
        return True

    def set_serial_port(self, port_str: str) -> bool:
        return True

    def set_tool(self, tool_name: str) -> bool:
        self._tool_name = tool_name
        try:
            PAROL6_ROBOT.apply_tool(tool_name)
        except Exception as e:
            logger.warning("Could not apply tool: %s", e)
        return True

    def get_tool(self) -> dict | None:
        return {"tool": self._tool_name, "available": []}

    def get_pose(self, frame: Literal["WRF", "TRF"] = "WRF") -> list[float] | None:
        # Convert [x,y,z,rx,ry,rz] in mm/deg to 4x4 matrix (flattened)
        T = np.zeros((4, 4), dtype=np.float64)
        se3_from_rpy(
            self._current_pose[0],
            self._current_pose[1],
            self._current_pose[2],
            np.radians(self._current_pose[3]),
            np.radians(self._current_pose[4]),
            np.radians(self._current_pose[5]),
            T,
        )
        return T.flatten().tolist()

    def get_angles(self) -> list[float] | None:
        return np.rad2deg(self._current_joints).tolist()

    def get_io(self) -> list[int] | None:
        return [0, 0, 0, 0, 1]

    def get_gripper_status(self) -> list[int] | None:
        return [0, 0, 0, 0, 0, 0]

    def get_speeds(self) -> list[float] | None:
        return [0.0] * 6

    def get_gripper(self) -> list[int] | None:
        return [0, 0, 0, 0, 0, 0]

    def get_status(self) -> dict | None:
        pose_matrix = self.get_pose()
        return {
            "pose": pose_matrix,
            "angles": self.get_angles(),
            "io": self.get_io(),
            "gripper": self.get_gripper(),
        }

    def get_pose_rpy(self) -> list[float] | None:
        return list(self._current_pose)

    def get_pose_xyz(self) -> list[float] | None:
        return list(self._current_pose[:3])

    def is_estop_pressed(self) -> bool:
        return False

    def is_robot_stopped(self, threshold_speed: float = 2.0) -> bool:
        return True

    def wait_motion_complete(
        self,
        timeout: float = 90.0,
        settle_window: float = 0.25,
        speed_threshold: float = 2.0,
        angle_threshold: float = 0.5,
    ) -> bool:
        return True

    def control_pneumatic_gripper(self, action: str, port: int) -> bool:
        return True

    def control_electric_gripper(
        self,
        action: str,
        position: int | None = 255,
        speed: int | None = 150,
        current: int | None = 500,
    ) -> bool:
        return True

    def execute_gcode(self, gcode_line: str) -> bool:
        return True

    def execute_gcode_program(self, program_lines: list[str]) -> bool:
        return True

    def load_gcode_file(self, filepath: str) -> bool:
        return True

    def get_gcode_status(self) -> dict | None:
        return {"running": False, "line": 0}

    def pause_gcode_program(self) -> bool:
        return True

    def resume_gcode_program(self) -> bool:
        return True

    def stop_gcode_program(self) -> bool:
        return True

    def delay(self, seconds: float) -> bool:
        return True

    def set_io(self, index: int, value: int) -> bool:
        return True

    def set_work_coordinate_offset(
        self,
        coordinate_system: str,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
    ) -> bool:
        return True

    def zero_work_coordinates(self, coordinate_system: str = "G54") -> bool:
        return True

    def ping(self) -> str | None:
        return "PONG"

    def get_current_action(self) -> dict | None:
        return {"current": "", "state": "", "next": ""}

    def get_queue(self) -> dict | None:
        return {"non_streamable": [], "size": 0}

    def get_loop_stats(self) -> dict | None:
        return {}

    def wait_for_server_ready(
        self, timeout: float = 5.0, interval: float = 0.05
    ) -> bool:
        return True

    def wait_for_status(self, predicate: Any, timeout: float = 5.0) -> bool:
        return True

    def send_raw(
        self, message: str, await_reply: bool = False, timeout: float = 2.0
    ) -> bool | str | None:
        return True if not await_reply else ""


class AsyncDryRunRobotClient:
    """Async wrapper around DryRunRobotClient."""

    def __init__(
        self,
        segment_collector: list[dict] | None = None,
        target_collector: list[dict] | None = None,
        initial_joints: list[float] | np.ndarray | None = None,
        initial_pose: list[float] | None = None,
        host: str = "127.0.0.1",
        port: int = 5001,
    ):
        # Note: Must use `is None` check, not `or []`, because empty lists are falsy!
        self._sync_client = DryRunRobotClient(
            segment_collector=[] if segment_collector is None else segment_collector,
            target_collector=[] if target_collector is None else target_collector,
            initial_joints=initial_joints,
            initial_pose=initial_pose,
            host=host,
            port=port,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def close(self):
        pass

    @property
    def segment_collector(self) -> list[dict]:
        return self._sync_client.segment_collector

    @property
    def target_collector(self) -> list[dict]:
        return self._sync_client.target_collector

    def __getattr__(self, name):
        """Delegate attribute access to sync client, wrapping callables with async."""
        attr = getattr(self._sync_client, name)

        if callable(attr) and name != "close":

            async def wrapper(*args, **kwargs):
                return attr(*args, **kwargs)

            return wrapper

        return attr
