from dataclasses import dataclass, field
from nicegui import binding


@dataclass
class JointAngles:
    values: list[float] = field(default_factory=lambda: [0.0] * 6)


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
    pose: RobotPose | None = None
    joint_angles: JointAngles | None = None
    io: RobotIO | None = None
    gripper: GripperStatus | None = None
    timestamp: float = 0.0


# Extended shared state singletons for cross-module access
@binding.bindable_dataclass
class RobotState:
    angles: list[float] = field(default_factory=list)  # len=6 in degrees
    pose: list[float] = field(
        default_factory=list
    )  # len=16 homogeneous transform flattened
    io: list[int] = field(default_factory=list)  # [in1,in2,out1,out2,estop]
    gripper: list[int] = field(default_factory=list)  # [id,pos,spd,cur,status,obj]
    connected: bool = False
    last_update_ts: float = 0.0
    # Derived scalars for convenient, high-performance UI bindings
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    rz: float = 0.0
    io_in1: int = 0
    io_in2: int = 0
    io_out1: int = 0
    io_out2: int = 0
    io_estop: int = 1
    grip_id: int = 0
    grip_pos: int = 0
    grip_speed: int = 0
    grip_current: int = 0
    grip_obj: int = 0


@dataclass
class ControllerState:
    running: bool = False
    pid: int | None = None
    com_port: str | None = None


@dataclass
class ProgramState:
    running: bool = False
    cancel_event_present: bool = False
    last_speed_pct: int | None = None


# Module-level singletons
robot_state = RobotState()
controller_state = ControllerState()
program_state = ProgramState()
