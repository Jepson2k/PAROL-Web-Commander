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
from spatialmath import SE3
from parol6.utils.ik import solve_ik as parol6_solve_ik  # use PAROL6 solver exclusively


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
        q = np.array(angles[: self.num_joints], dtype=float)

        # Pad with zeros if needed
        while len(q) < self.robot.n:
            q = np.append(q, 0.0)

        # Compute forward kinematics
        T = cast(Any, self.robot).fkine(q)

        # Extract translation (position)
        pos = np.array(T.t)

        # Extract rotation as Euler angles (XYZ convention)
        try:
            # SE3 object has rpy() method for roll-pitch-yaw (XYZ Euler angles)
            rpy = T.rpy(order="xyz")  # Returns [rx, ry, rz] in radians
        except Exception:
            rpy = [0.0, 0.0, 0.0]

        return np.array([pos[0], pos[1], pos[2], rpy[0], rpy[1], rpy[2]])

    def solve(
        self,
        target_pos: np.ndarray,
        current_angles: List[float],
        throttle: bool = True,
    ) -> Optional[IKResult]:
        """
        Solve IK for the target position.

        Args:
            target_pos: Target TCP position [x, y, z] in meters (world frame)
            current_angles: Current joint angles in radians
            throttle: If True, skip solving if called too frequently

        Returns:
            IKResult with computed angles, or None if throttled
        """
        # Throttle check
        if throttle:
            now = time.time()
            if now - self._last_solve_time < self._min_solve_interval:
                return None
            self._last_solve_time = now

        target = np.array(target_pos, dtype=float)
        q0 = np.array(current_angles[: self.num_joints], dtype=float)

        # Pad with zeros if needed
        while len(q0) < self.robot.n:
            q0 = np.append(q0, 0.0)

        # Get current end effector orientation to maintain it
        T_current = cast(Any, self.robot).fkine(q0)

        # Create target pose with current orientation but new position
        T_target = SE3.Rt(T_current.R, target)

        # Use PAROL6 IK helper exclusively to enforce safety and unwrapping
        parol_result = parol6_solve_ik(
            robot=cast(Any, self.robot),
            target_pose=T_target,
            current_q=q0,
            jogging=True,
            safety_margin_rad=0.03,
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
