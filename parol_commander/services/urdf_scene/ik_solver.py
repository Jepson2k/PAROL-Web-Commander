"""
IK Solver for editing mode using robotics-toolbox-python.

Uses the robotics-toolbox-python library for proper URDF-based
forward and inverse kinematics.
"""

import time
import logging
from dataclasses import dataclass
from typing import List, Optional, Any, cast
import numpy as np

# Import robotics-toolbox
from roboticstoolbox import Robot
from parol6.utils.ik import solve_ik as parol6_solve_ik  # use PAROL6 solver exclusively
from parol6.utils.se3_utils import se3_from_rpy, so3_rpy


@dataclass
class IKResult:
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
    IK solver for editing mode manipulation using robotics-toolbox-python.

    Uses the library's URDF parser and numerical IK solvers for
    accurate forward and inverse kinematics.
    """

    def __init__(self, robot: Robot, num_joints: int = 6):
        """
        Initialize the IK solver.

        Args:
            robot: robotics-toolbox Robot instance loaded from URDF
            num_joints: Number of joints to solve for (default 6)
        """
        self.robot = robot
        self.num_joints = num_joints

        # Pre-allocated buffers to avoid per-call allocations
        self._q_buffer = np.zeros(robot.n, dtype=float)
        self._fk_result_buffer = np.zeros(6, dtype=float)
        self._rpy_buffer = np.zeros(3, dtype=np.float64)
        self._T_target_buffer = np.zeros((4, 4), dtype=np.float64)

        # Throttling
        self._last_solve_time = 0.0
        self._min_solve_interval = 0.033  # ~30Hz

        logging.debug(
            "EditingIKSolver initialized with robotics-toolbox: %d joints",
            self.num_joints,
        )

    @classmethod
    def from_urdf_scene(cls, urdf_scene: Any) -> "EditingIKSolver":
        """
        Create an IK solver from a UrdfScene instance.

        Args:
            urdf_scene: UrdfScene instance with loaded URDF

        Returns:
            Configured EditingIKSolver instance
        """
        # Get the URDF file path from urdf_scene
        urdf_path = urdf_scene.urdf_path

        # Load robot using robotics-toolbox
        try:
            robot = Robot.URDF(str(urdf_path))
            logging.info("Loaded robot from URDF: %s", robot.name)
        except Exception as e:
            logging.error("Failed to load robot from URDF: %s", e)
            raise

        # Get number of actuated joints (first 6)
        num_joints = min(6, robot.n)

        return cls(robot=robot, num_joints=num_joints)

    def forward_kinematics(self, angles: List[float]) -> np.ndarray:
        """
        Compute end effector pose from joint angles.

        Args:
            angles: Joint angles in radians (list of 6 floats)

        Returns:
            End effector pose [x, y, z, rx, ry, rz] in meters and radians (world frame)
            Position is in meters, rotation is Euler angles (XYZ) in radians.
        """
        # Use pre-allocated buffer - zero and fill to avoid allocation
        n_input = min(len(angles), self.num_joints)
        self._q_buffer[:n_input] = angles[:n_input]
        self._q_buffer[n_input:] = 0.0

        # Compute forward kinematics
        T = cast(Any, self.robot).fkine(self._q_buffer)

        # Extract translation (position) - T.t works on spatialmath SE3
        pos = T.t

        # Extract rotation as Euler angles (XYZ convention)
        # T.R gives the rotation matrix, use our so3_rpy utility
        try:
            so3_rpy(T.R, self._rpy_buffer)  # Returns [rx, ry, rz] in radians
        except Exception:
            self._rpy_buffer[0] = 0.0
            self._rpy_buffer[1] = 0.0
            self._rpy_buffer[2] = 0.0

        # Fill pre-allocated result buffer
        self._fk_result_buffer[0] = pos[0]
        self._fk_result_buffer[1] = pos[1]
        self._fk_result_buffer[2] = pos[2]
        self._fk_result_buffer[3] = self._rpy_buffer[0]
        self._fk_result_buffer[4] = self._rpy_buffer[1]
        self._fk_result_buffer[5] = self._rpy_buffer[2]
        return self._fk_result_buffer

    def solve(
        self,
        target_pos: np.ndarray,
        current_angles: List[float],
        throttle: bool = True,
        target_orientation: Optional[np.ndarray] = None,
    ) -> Optional[IKResult]:
        """
        Solve IK for the target position and optionally orientation.

        Args:
            target_pos: Target TCP position [x, y, z] in meters (world frame)
            current_angles: Current joint angles in radians
            throttle: If True, skip solving if called too frequently
            target_orientation: Target orientation [rx, ry, rz] in radians (XYZ Euler).
                               If None, maintains current orientation.

        Returns:
            IKResult with computed angles, or None if throttled
        """
        # Throttle check
        if throttle:
            now = time.time()
            if now - self._last_solve_time < self._min_solve_interval:
                return None
            self._last_solve_time = now

        # Use pre-allocated buffer for q0
        n_input = min(len(current_angles), self.num_joints)
        self._q_buffer[:n_input] = current_angles[:n_input]
        self._q_buffer[n_input:] = 0.0
        q0 = self._q_buffer

        # Get current end effector orientation to maintain it
        T_current = cast(Any, self.robot).fkine(q0)

        # Extract target position (avoid allocation if already ndarray)
        if isinstance(target_pos, np.ndarray):
            target = target_pos
        else:
            target = np.asarray(target_pos, dtype=float)

        # Create target pose with specified or current orientation
        # Zero buffer and fill to avoid allocation
        self._T_target_buffer.fill(0.0)
        if target_orientation is not None:
            # Build 4x4 SE3 from position and XYZ Euler angles
            se3_from_rpy(
                target[0],
                target[1],
                target[2],
                target_orientation[0],
                target_orientation[1],
                target_orientation[2],
                self._T_target_buffer,
            )
        else:
            # Build 4x4 SE3 with current rotation and new translation
            self._T_target_buffer[0, 0] = 1.0
            self._T_target_buffer[1, 1] = 1.0
            self._T_target_buffer[2, 2] = 1.0
            self._T_target_buffer[3, 3] = 1.0
            self._T_target_buffer[:3, :3] = T_current.R
            self._T_target_buffer[:3, 3] = target

        parol_result = parol6_solve_ik(
            robot=cast(Any, self.robot),
            target_pose=self._T_target_buffer,
            current_q=q0,
            quiet_logging=True,
        )

        if (
            getattr(parol_result, "success", False)
            and getattr(parol_result, "q", None) is not None
        ):
            q_solution = np.asarray(parol_result.q, dtype=float)
            # Use residual as error metric
            pos_error = float(getattr(parol_result, "residual", 0.0))
            return IKResult(
                success=True,
                angles=q_solution[: self.num_joints].tolist(),
                error=pos_error,
                iterations=int(getattr(parol_result, "iterations", 0)),
            )

        # No fallback: return failure with current angles
        return IKResult(
            success=False,
            angles=q0[: self.num_joints].tolist(),
            error=float("inf"),
            iterations=int(getattr(parol_result, "iterations", 0)),
        )
