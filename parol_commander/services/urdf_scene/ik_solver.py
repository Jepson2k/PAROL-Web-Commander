"""
IK Solver for editing mode.

Uses the Robot protocol for forward and inverse kinematics,
eliminating direct backend imports.
"""

import time
import logging
from dataclasses import dataclass
from typing import Any, List, Optional
import numpy as np

from parol_commander.robot_interface import Robot


@dataclass
class EditingIKResult:
    """Result of an IK solve operation."""

    success: bool
    """Whether the solver converged within tolerance."""

    angles: List[float]
    """Computed joint angles in radians."""

    error: float
    """Final distance from target in meters."""

    iterations: int
    """Number of iterations performed."""


class EditingIKSolver:
    """
    IK solver for editing mode manipulation.

    Uses the Robot protocol for forward and inverse kinematics.
    """

    def __init__(self, robot: Robot, num_joints: int = 6):
        """
        Initialize the IK solver.

        Args:
            robot: Robot providing FK/IK
            num_joints: Number of joints to solve for (default 6)
        """
        self.robot = robot
        self.num_joints = num_joints

        # Pre-allocated buffers
        self._fk_result_buffer = np.zeros(6, dtype=np.float64)
        self._pose_buf = np.zeros(6, dtype=np.float64)

        # Throttling
        self._last_solve_time = 0.0
        self._min_solve_interval = 0.033  # ~30Hz

        logging.debug(
            "EditingIKSolver initialized: %d joints",
            self.num_joints,
        )

    @classmethod
    def from_urdf_scene(cls, urdf_scene: Any) -> "EditingIKSolver":
        """
        Create an IK solver from a UrdfScene instance.

        Uses the Robot from ui_state (set at startup).

        Args:
            urdf_scene: UrdfScene instance with loaded URDF

        Returns:
            Configured EditingIKSolver instance
        """
        from parol_commander.state import ui_state

        robot = ui_state.active_robot
        return cls(robot=robot, num_joints=robot.joints.count)

    def forward_kinematics(self, angles: List[float]) -> np.ndarray:
        """
        Compute end effector pose from joint angles.

        Args:
            angles: Joint angles in radians (list of 6 floats)

        Returns:
            End effector pose [x, y, z, rx, ry, rz] in meters and radians (world frame)
        """
        q = np.asarray(angles[: self.num_joints], dtype=np.float64)
        result = self.robot.fk(q)
        self._fk_result_buffer[:] = result
        return self._fk_result_buffer

    def solve(
        self,
        target_pos: np.ndarray,
        current_angles: List[float],
        throttle: bool = True,
        target_orientation: Optional[np.ndarray] = None,
    ) -> Optional[EditingIKResult]:
        """
        Solve IK for the target position and optionally orientation.

        Args:
            target_pos: Target TCP position [x, y, z] in meters (world frame)
            current_angles: Current joint angles in radians
            throttle: If True, skip solving if called too frequently
            target_orientation: Target orientation [rx, ry, rz] in radians (XYZ Euler).
                               If None, maintains current orientation.

        Returns:
            EditingIKResult with computed angles, or None if throttled
        """
        if throttle:
            now = time.time()
            if now - self._last_solve_time < self._min_solve_interval:
                return None
            self._last_solve_time = now

        q_current = np.asarray(current_angles[: self.num_joints], dtype=np.float64)

        if target_orientation is not None:
            self._pose_buf[0] = target_pos[0]
            self._pose_buf[1] = target_pos[1]
            self._pose_buf[2] = target_pos[2]
            self._pose_buf[3] = target_orientation[0]
            self._pose_buf[4] = target_orientation[1]
            self._pose_buf[5] = target_orientation[2]
        else:
            current_fk = self.robot.fk(q_current)
            self._pose_buf[0] = target_pos[0]
            self._pose_buf[1] = target_pos[1]
            self._pose_buf[2] = target_pos[2]
            self._pose_buf[3] = current_fk[3]
            self._pose_buf[4] = current_fk[4]
            self._pose_buf[5] = current_fk[5]

        result = self.robot.ik(self._pose_buf, q_current)

        if result.success:
            return EditingIKResult(
                success=True,
                angles=result.q[: self.num_joints].tolist(),
                error=getattr(result, "residual", 0.0),
                iterations=getattr(result, "iterations", 0),
            )

        return EditingIKResult(
            success=False,
            angles=current_angles[: self.num_joints],
            error=float("inf"),
            iterations=getattr(result, "iterations", 0),
        )
