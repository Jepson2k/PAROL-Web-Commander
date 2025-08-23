from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class JointAngles:
    values: List[float] = field(default_factory=lambda: [0.0] * 6)


@dataclass
class RobotPose:
    x: float = 0.0  # mm
    y: float = 0.0  # mm
    z: float = 0.0  # mm
    rx: float = 0.0  # deg
    ry: float = 0.0  # deg
    rz: float = 0.0  # deg


@dataclass
class RobotIO:
    in1: int = 0
    in2: int = 0
    out1: int = 0
    out2: int = 0
    estop: int = 1  # 1=OK, 0=TRIGGERED


@dataclass
class GripperStatus:
    device_id: int = 0
    position: int = 0
    speed: int = 0
    current: int = 0
    status_byte: int = 0
    object_detected: int = 0  # 0=no, 1=closing, 2=opening


@dataclass
class StatusSnapshot:
    pose: Optional[RobotPose] = None
    joint_angles: Optional[JointAngles] = None
    io: Optional[RobotIO] = None
    gripper: Optional[GripperStatus] = None
    timestamp: float = 0.0
