"""
IK Solver for editing mode using pinokin.

Uses pinokin (Pinocchio) for URDF-based forward and inverse kinematics.
"""

import time
import logging
from dataclasses import dataclass
from typing import Any, List, Optional
import numpy as np

from pinokin import Robot
from parol6.utils.ik import solve_ik as parol6_solve_ik
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
    IK solver for editing mode manipulation using pinokin.

    Uses pinokin's URDF parser and numerical IK solvers for
    accurate forward and inverse kinematics.
    """

    def __init__(self, robot: Robot, num_joints: int = 6):
        """
        Initialize the IK solver.

        Args:
            robot: pinokin Robot instance loaded from URDF
            num_joints: Number of joints to solve for (default 6)
        """
        self.robot = robot
        self.num_joints = num_joints

        # Pre-allocated buffers to avoid per-call allocations
        self._q_buffer = np.zeros(robot.nq, dtype=float)
        self._fk_result_buffer = np.zeros(6, dtype=float)
        self._rpy_buffer = np.zeros(3, dtype=np.float64)
        self._T_fk_buffer = np.asfortranarray(np.zeros((4, 4), dtype=np.float64))
        self._T_target_buffer = np.zeros((4, 4), dtype=np.float64)

        # Throttling
        self._last_solve_time = 0.0
        self._min_solve_interval = 0.033  # ~30Hz

        logging.debug(
            "EditingIKSolver initialized with pinokin: %d joints",
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

        try:
            robot = Robot(str(urdf_path))
            logging.info("Loaded robot from URDF: %s", robot.name or urdf_path)
        except Exception as e:
            logging.error("Failed to load robot from URDF: %s", e)
            raise

        num_joints = min(6, robot.nq)

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

        # Compute forward kinematics into pre-allocated buffer
        self.robot.fkine_into(self._q_buffer, self._T_fk_buffer)
        T = self._T_fk_buffer

        try:
            so3_rpy(T[:3, :3], self._rpy_buffer)
        except Exception:
            self._rpy_buffer[0] = 0.0
            self._rpy_buffer[1] = 0.0
            self._rpy_buffer[2] = 0.0

        # Fill pre-allocated result buffer
        self._fk_result_buffer[0] = T[0, 3]
        self._fk_result_buffer[1] = T[1, 3]
        self._fk_result_buffer[2] = T[2, 3]
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

        # Extract target position (avoid allocation if already ndarray)
        if isinstance(target_pos, np.ndarray):
            target = target_pos
        else:
            target = np.asarray(target_pos, dtype=float)

        # Create target pose with specified or current orientation
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
            self.robot.fkine_into(q0, self._T_fk_buffer)
            self._T_target_buffer[:] = self._T_fk_buffer
            self._T_target_buffer[0, 3] = target[0]
            self._T_target_buffer[1, 3] = target[1]
            self._T_target_buffer[2, 3] = target[2]

        result = parol6_solve_ik(
            robot=self.robot,
            target_pose=self._T_target_buffer,
            current_q=q0,
            quiet_logging=True,
        )

        if result.success:
            return IKResult(
                success=True,
                angles=result.q[: self.num_joints].tolist(),
                error=result.residual,
                iterations=result.iterations,
            )

        return IKResult(
            success=False,
            angles=q0[: self.num_joints].tolist(),
            error=float("inf"),
            iterations=result.iterations,
        )
