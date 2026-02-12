"""Generic robot control interface — Protocol + supporting types.

This module defines the contract between PAROL-Web-Commander and any robot
backend.  The web commander's UI code types against ``Robot`` (a Protocol),
so mypy enforces that only generic operations are called.  Concrete classes
(e.g. ``parol6.Robot``) satisfy this Protocol structurally — no inheritance
required.

Architecture
------------
- **Robot Protocol**: the single entry point — identity, joints, tools,
  kinematics, lifecycle, and client factories.
- **RobotClient Protocol**: async control operations (move, jog, I/O, …)
- **DryRunClient Protocol**: offline motion simulation for path preview
- **Supporting Protocols**: StatusBuffer, PingResult, ToolResult
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Literal,
    Protocol,
    runtime_checkable,
)

import numpy as np
from numpy.typing import NDArray


# ===========================================================================
# Result types — Protocol contracts + concrete dataclasses for tests
# ===========================================================================


@runtime_checkable
class IKResult(Protocol):
    """Result of an inverse kinematics solve."""

    q: NDArray[np.float64]
    """Joint angles in radians."""
    success: bool
    """Whether the solver converged within tolerance."""
    violations: str | None
    """Description of limit violations, or None."""


@runtime_checkable
class DryRunResult(Protocol):
    """Result from a dry-run motion command (path preview)."""

    tcp_poses: NDArray[np.float64]
    """(N, 6) — TCP trajectory [x, y, z, rx, ry, rz] in meters + radians."""
    end_joints_rad: NDArray[np.float64]
    """(num_joints,) — final joint angles in radians."""
    duration: float
    """Trajectory duration in seconds."""
    error: str | None
    """Error message (IK failure, etc.), or None on success."""


@dataclass
class IKResultData:
    """Concrete IKResult for use in tests and adapters."""

    q: NDArray[np.float64]
    success: bool
    violations: str | None = None


@dataclass
class DryRunResultData:
    """Concrete DryRunResult for use in tests and adapters."""

    tcp_poses: NDArray[np.float64]
    end_joints_rad: NDArray[np.float64]
    duration: float
    error: str | None = None


# ===========================================================================
# Joint configuration hierarchy
# ===========================================================================


@runtime_checkable
class PositionLimits(Protocol):
    """Joint position limits in multiple unit systems.

    All arrays have shape ``(num_joints, 2)`` where columns are
    ``[lower, upper]``.
    """

    @property
    def deg(self) -> NDArray[np.float64]:
        """``(N, 2)`` — position limits in degrees."""
        ...

    @property
    def rad(self) -> NDArray[np.float64]:
        """``(N, 2)`` — position limits in radians."""
        ...


@runtime_checkable
class KinodynamicLimits(Protocol):
    """Per-joint velocity, acceleration, and jerk limits.

    All arrays have shape ``(num_joints,)`` in SI units (rad/s family).
    """

    @property
    def velocity(self) -> NDArray[np.float64]:
        """``(N,)`` — max joint velocities in rad/s."""
        ...

    @property
    def acceleration(self) -> NDArray[np.float64]:
        """``(N,)`` — max joint accelerations in rad/s²."""
        ...

    @property
    def jerk(self) -> NDArray[np.float64] | None:
        """``(N,)`` — max joint jerks in rad/s³, or None if not applicable."""
        ...


@runtime_checkable
class JointLimitsSpec(Protocol):
    """All joint limits — position and kinodynamic."""

    @property
    def position(self) -> PositionLimits:
        """Position limits in degrees and radians."""
        ...

    @property
    def hard(self) -> KinodynamicLimits:
        """Hardware kinodynamic limits (maximum capability)."""
        ...

    @property
    def jog(self) -> KinodynamicLimits:
        """Jog kinodynamic limits (reduced for manual operation)."""
        ...


@runtime_checkable
class HomePosition(Protocol):
    """Home / standby position in multiple unit systems.

    All arrays have shape ``(num_joints,)``.
    """

    @property
    def deg(self) -> NDArray[np.float64]:
        """``(N,)`` — home position in degrees."""
        ...

    @property
    def rad(self) -> NDArray[np.float64]:
        """``(N,)`` — home position in radians."""
        ...


@runtime_checkable
class JointsSpec(Protocol):
    """Complete joint configuration for a robot.

    All array properties have their first dimension equal to ``count``.
    """

    @property
    def count(self) -> int:
        """Number of actuated joints."""
        ...

    @property
    def names(self) -> tuple[str, ...]:
        """Per-joint display names, length == ``count``."""
        ...

    @property
    def limits(self) -> JointLimitsSpec:
        """Position and kinodynamic limits for all joints."""
        ...

    @property
    def home(self) -> HomePosition:
        """Home / standby position."""
        ...


# ---------------------------------------------------------------------------
# Concrete joint dataclass implementations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SimplePositionLimits:
    """Concrete PositionLimits backed by numpy arrays."""

    deg: NDArray[np.float64]
    """``(N, 2)`` — position limits in degrees."""
    rad: NDArray[np.float64]
    """``(N, 2)`` — position limits in radians."""


@dataclass(frozen=True, slots=True)
class SimpleKinodynamicLimits:
    """Concrete KinodynamicLimits backed by numpy arrays."""

    velocity: NDArray[np.float64]
    """``(N,)`` — max velocities in rad/s."""
    acceleration: NDArray[np.float64]
    """``(N,)`` — max accelerations in rad/s²."""
    jerk: NDArray[np.float64] | None = None
    """``(N,)`` — max jerks in rad/s³, or None."""


@dataclass(frozen=True, slots=True)
class SimpleJointLimits:
    """Concrete JointLimitsSpec."""

    position: SimplePositionLimits
    hard: SimpleKinodynamicLimits
    jog: SimpleKinodynamicLimits


@dataclass(frozen=True, slots=True)
class SimpleHomePosition:
    """Concrete HomePosition backed by numpy arrays."""

    deg: NDArray[np.float64]
    """``(N,)`` — home position in degrees."""
    rad: NDArray[np.float64]
    """``(N,)`` — home position in radians."""


@dataclass(frozen=True, slots=True)
class SimpleJointsSpec:
    """Concrete JointsSpec backed by numpy arrays."""

    count: int
    names: tuple[str, ...]
    limits: SimpleJointLimits
    home: SimpleHomePosition


# ===========================================================================
# Tool / gripper hierarchy
# ===========================================================================


class ToolType(Enum):
    """Tool categories the web commander has GUI support for.

    Determines which panel (if any) is rendered for the tool.
    """

    NONE = "none"
    """Bare flange or passive tool — TCP offset + 3D visual only, no panel."""
    GRIPPER = "gripper"
    """Dedicated gripper control panel."""


class GripperType(Enum):
    """Gripper sub-types — each gets different UI controls."""

    PNEUMATIC = "pneumatic"
    ELECTRIC = "electric"
    PARALLEL = "parallel"


@runtime_checkable
class ToolSpec(Protocol):
    """Base contract every tool must satisfy.

    ``key`` is unique per tool instance (e.g. ``"pneumatic_left"``).
    ``tool_type`` determines which GUI panel category the tool belongs to.
    Multiple tools can share the same ``tool_type``.
    """

    @property
    def key(self) -> str:
        """Unique instance identifier."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name for UI display."""
        ...

    @property
    def description(self) -> str:
        """Short description of the tool."""
        ...

    @property
    def tool_type(self) -> ToolType:
        """GUI category — determines which panel (if any) is shown."""
        ...

    @property
    def tcp_origin(self) -> tuple[float, float, float]:
        """(x, y, z) translation from flange to TCP in meters."""
        ...

    @property
    def tcp_rpy(self) -> tuple[float, float, float]:
        """(roll, pitch, yaw) orientation from flange to TCP in radians."""
        ...


@runtime_checkable
class GripperTool(ToolSpec, Protocol):
    """Contract for gripper tools (``tool_type == ToolType.GRIPPER``).

    ``gripper_type`` selects which sub-protocol applies and which
    gripper panel variant to render.
    """

    @property
    def gripper_type(self) -> GripperType:
        """Gripper sub-type."""
        ...


@runtime_checkable
class ElectricGripperTool(GripperTool, Protocol):
    """Electric gripper — position/speed/current sliders."""

    @property
    def position_range(self) -> tuple[float, float]:
        """(min, max) position range."""
        ...

    @property
    def speed_range(self) -> tuple[float, float]:
        """(min, max) speed range."""
        ...

    @property
    def current_range(self) -> tuple[int, int]:
        """(min, max) current range in mA."""
        ...


@runtime_checkable
class PneumaticGripperTool(GripperTool, Protocol):
    """Pneumatic gripper — open/close via I/O port."""

    @property
    def io_port(self) -> int:
        """Digital I/O port number for open/close control."""
        ...


@runtime_checkable
class ToolsSpec(Protocol):
    """Collection of available tools for a robot.

    Supports membership testing by ``ToolType`` (category) or ``str`` (key).
    """

    @property
    def available(self) -> tuple[ToolSpec, ...]:
        """All available tool specifications, ordered for display."""
        ...

    @property
    def default(self) -> ToolSpec:
        """Default tool (typically bare flange / "NONE")."""
        ...

    def __getitem__(self, key: str) -> ToolSpec:
        """Look up a tool by its key. Raises ``KeyError`` if not found."""
        ...

    def __contains__(self, item: object) -> bool:
        """Test membership by ``ToolType`` (any tool of that category?)
        or ``str`` (specific key exists?).
        """
        ...

    def by_type(self, tool_type: ToolType) -> tuple[ToolSpec, ...]:
        """Return all tools matching the given category."""
        ...


# ===========================================================================
# DryRunClient Protocol — offline motion simulation
# ===========================================================================


@runtime_checkable
class DryRunClient(Protocol):
    """Offline motion client for path preview / dry-run simulation.

    Concrete implementations run the real command pipeline against a
    simulated controller state without hardware.  Each motion method
    returns a ``DryRunResult`` containing the TCP trajectory and final
    joint state.
    """

    def home(self, **kwargs: Any) -> DryRunResult:
        """Simulate homing motion."""
        ...

    def moveJ(
        self,
        target: list[float],
        *,
        duration: float = 0.0,
        speed: float = 0.0,
        accel: float = 1.0,
        **kwargs: Any,
    ) -> DryRunResult:
        """Simulate joint-space motion."""
        ...

    def moveL(
        self,
        pose: list[float],
        *,
        duration: float = 0.0,
        speed: float = 0.0,
        accel: float = 1.0,
        **kwargs: Any,
    ) -> DryRunResult:
        """Simulate Cartesian linear motion."""
        ...


# ===========================================================================
# Supporting Protocols — status streaming types
# ===========================================================================


@runtime_checkable
class StatusBuffer(Protocol):
    """Status snapshot yielded by ``status_stream_shared()``.

    Each field is a numpy array for zero-copy access in the hot path.
    """

    pose: np.ndarray
    """(16,) float64 — flattened 4x4 homogeneous transform."""
    angles: np.ndarray
    """(N,) float64 — joint angles in degrees."""
    speeds: np.ndarray
    """(N,) float64 — joint speeds."""
    io: np.ndarray
    """(5,) int32 — [in1, in2, out1, out2, estop]."""
    gripper: np.ndarray
    """(6,) int32 — [id, pos, spd, cur, status, obj]."""
    joint_en: np.ndarray
    """(12,) int32 — joint enable envelope."""
    cart_en: dict[str, np.ndarray]
    """Frame name → (12,) int32 Cartesian enable envelope."""
    action_current: str
    """Currently executing action name."""
    action_state: str
    """State of the current action."""
    executing_index: int
    """Index of the command currently being executed (-1 if idle)."""
    completed_index: int
    """Index of the last completed command (-1 if none)."""
    last_checkpoint: str
    """Label of the last checkpoint reached (empty if none)."""


@runtime_checkable
class PingResult(Protocol):
    """Result of a connectivity check."""

    serial_connected: bool
    """Whether the controller has a live serial link to the robot."""


@runtime_checkable
class ToolResult(Protocol):
    """Result of a tool query."""

    tool: str
    """Currently active tool name."""
    available: list[str]
    """All available tool names."""


# ===========================================================================
# RobotClient Protocol — async control operations
# ===========================================================================


@runtime_checkable
class RobotClient(Protocol):
    """Generic async robot control interface.

    The web commander's UI components type against this Protocol so they
    only see generic operations.  Concrete classes (e.g.
    ``parol6.AsyncRobotClient``) satisfy this Protocol structurally — no
    inheritance required.

    **Command palette integration:** Methods that should appear in the editor's
    command palette must include ``Category:`` and ``Example:`` sections in
    their docstrings.  The editor parses these at startup to build the palette.

    - ``Category: <name>`` — groups the command in the palette UI.
    - ``Example:`` — the first indented line becomes the insertion snippet.

    Use concrete values from your robot in the Example (e.g. valid joint
    angles, reachable Cartesian poses).  Placeholders below (``<joint_angles_deg>``,
    ``<tcp_pose_mm_deg>``, etc.) show what each example should contain.
    Methods without both sections are excluded from the palette.
    """

    # -- Connection & lifecycle ---------------------------------------------

    async def close(self) -> None:
        """Release resources and disconnect."""
        ...

    async def ping(self) -> PingResult | None:
        """Check connectivity.  Returns None if unreachable.

        Category: Query

        Example:
            rbt.ping()
        """
        ...

    async def wait_ready(self, timeout: float = 5.0, interval: float = 0.05) -> bool:
        """Block until the robot backend is reachable or *timeout* expires."""
        ...

    # -- Status streaming ---------------------------------------------------

    def status_stream_shared(self) -> AsyncIterator[StatusBuffer]:
        """Async iterator of real-time status snapshots (shared across consumers)."""
        ...

    # -- Motion commands (trajectory-planned) ---------------------------------

    async def moveJ(
        self,
        target: list[float],
        *,
        pose: list[float] | None = None,
        duration: float = 0.0,
        speed: float = 0.0,
        accel: float = 1.0,
        r: float = 0,
        rel: bool = False,
        wait: bool = False,
        **wait_kwargs: Any,
    ) -> int:
        """Joint-space move. *target*: joint angles in degrees.

        If *pose* is given, performs joint-interpolated move to Cartesian target.
        Returns the command index (≥ 0) on success, -1 on failure.

        Category: Motion

        Example:
            rbt.moveJ(<joint_angles_deg>, speed=0.5)
        """
        ...

    async def moveL(
        self,
        pose: list[float],
        *,
        frame: str = "WRF",
        duration: float = 0.0,
        speed: float = 0.0,
        accel: float = 1.0,
        r: float = 0,
        rel: bool = False,
        wait: bool = False,
        **wait_kwargs: Any,
    ) -> int:
        """Linear Cartesian move to [x, y, z, rx, ry, rz].

        Returns the command index (≥ 0) on success, -1 on failure.

        Category: Motion

        Example:
            rbt.moveL(<tcp_pose_mm_deg>, speed=0.5)
        """
        ...

    async def home(self, wait: bool = False, **wait_kwargs: Any) -> int:
        """Move to the robot's home position.

        Returns the command index (≥ 0) on success, -1 on failure.

        Category: Motion

        Example:
            rbt.home()
        """
        ...

    async def wait_motion_complete(
        self,
        timeout: float = 10.0,
        **kwargs: Any,
    ) -> bool:
        """Block until the robot has stopped moving or *timeout* expires.

        Category: Synchronization

        Example:
            rbt.wait_motion_complete()
        """
        ...

    # -- Servo commands (streaming position, fire-and-forget) ---------------

    async def servoJ(
        self,
        target: list[float],
        *,
        pose: list[float] | None = None,
        speed: float = 1.0,
        accel: float = 1.0,
    ) -> int:
        """Streaming joint position target (fire-and-forget).

        *target*: 6 joint angles in degrees (ignored if *pose* is set).
        If *pose* is given, dispatches to SERVOJ_POSE (Cartesian target via IK).

        Category: Streaming

        Example:
            rbt.servoJ(<joint_angles_deg>)
        """
        ...

    async def servoL(
        self,
        pose: list[float],
        *,
        speed: float = 1.0,
        accel: float = 1.0,
    ) -> int:
        """Streaming linear Cartesian position target (fire-and-forget).

        *pose*: [x, y, z, rx, ry, rz] in mm and degrees.

        Category: Streaming

        Example:
            rbt.servoL(<tcp_pose_mm_deg>)
        """
        ...

    # -- Jog commands (streaming velocity) ----------------------------------

    async def jogJ(
        self,
        joint: int,
        speed: float = 0.0,
        duration: float = 0.1,
        *,
        joints: list[int] | None = None,
        speeds: list[float] | None = None,
        accel: float = 1.0,
    ) -> int:
        """Joint velocity jog. Single-joint or multi-joint.

        Single joint: ``jogJ(0, 0.5, 1.0)``
        Multi joint:  ``jogJ(joints=[0, 1], speeds=[0.5, -0.3], duration=1.0)``

        *joint*: 0-based joint number (single-joint mode).
        *speed*: signed, ``-1.0`` to ``1.0`` (single-joint mode).
        *duration*: seconds per pulse.
        *joints*: list of joint indices (multi-joint mode).
        *speeds*: list of signed speed fractions (multi-joint mode).
        *accel*: acceleration fraction 0–1.

        Category: Jog

        Example:
            rbt.jogJ(<joint_index>, speed=0.5, duration=1.0)
        """
        ...

    async def jogL(
        self,
        frame: str,
        axis: str | None = None,
        speed: float = 0.0,
        duration: float = 0.1,
        *,
        axes: list[str] | None = None,
        speeds_list: list[float] | None = None,
        accel: float = 1.0,
    ) -> int:
        """Cartesian velocity jog. Single-axis or multi-axis.

        Single axis: ``jogL("WRF", "X", 0.5, 1.0)``
        Multi axis:  ``jogL("WRF", axes=["X", "Y"], speeds_list=[0.5, -0.3])``

        *frame*: reference frame (``"WRF"`` or ``"TRF"``).
        *axis*: axis name for single-axis jog.
        *speed*: signed, ``-1.0`` to ``1.0`` (single-axis mode).
        *duration*: seconds per pulse.
        *axes*: list of axis names (multi-axis mode).
        *speeds_list*: list of signed speed fractions (multi-axis mode).
        *accel*: acceleration fraction 0-1.

        Category: Jog

        Example:
            rbt.jogL("WRF", "X", speed=0.5, duration=1.0)
        """
        ...

    # -- Safety & mode ------------------------------------------------------

    async def resume(self) -> int:
        """Re-enable the robot after an e-stop or disable.

        Category: Control

        Example:
            rbt.resume()
        """
        ...

    async def halt(self) -> int:
        """Immediate stop — halt all motion and disable.

        Category: Control

        Example:
            rbt.halt()
        """
        ...

    async def simulator_on(self) -> int:
        """Enable simulator mode.

        Category: Control

        Example:
            rbt.simulator_on()
        """
        ...

    async def simulator_off(self) -> int:
        """Disable simulator mode.

        Category: Control

        Example:
            rbt.simulator_off()
        """
        ...

    async def set_freedrive(self, enabled: bool) -> int:
        """Enable or disable freedrive / teach mode."""
        ...

    # -- Configuration ------------------------------------------------------

    async def set_serial_port(self, port_str: str) -> int:
        """Set the serial port for hardware communication.

        Category: Configuration

        Example:
            rbt.set_serial_port("/dev/ttyUSB0")
        """
        ...

    async def set_profile(self, profile: str) -> int:
        """Set the motion profile (e.g. ``"TOPPRA"``).

        Category: Configuration

        Example:
            rbt.set_profile("TOPPRA")
        """
        ...

    async def get_tool(self) -> ToolResult | None:
        """Get current tool and available tools.

        Category: Query

        Example:
            tool = rbt.get_tool()
        """
        ...

    # -- Gripper / I/O ------------------------------------------------------

    async def control_pneumatic_gripper(
        self, action: str, port: int, wait: bool = False, **wait_kwargs: Any
    ) -> int:
        """Control pneumatic gripper.  *action*: ``"open"`` or ``"close"``.

        Category: Gripper

        Example:
            rbt.control_pneumatic_gripper("open", port=1)
        """
        ...

    async def control_electric_gripper(
        self,
        action: str,
        position: float = 0.0,
        speed: float = 0.5,
        current: int = 500,
        wait: bool = False,
        **wait_kwargs: Any,
    ) -> int:
        """Control electric gripper.  *action*: ``"calibrate"``, ``"move"``, etc.

        Category: Gripper

        Example:
            rbt.control_electric_gripper("move", position=0.5)
        """
        ...


# ===========================================================================
# Robot Protocol — the single entry point
# ===========================================================================


@runtime_checkable
class Robot(Protocol):
    """Unified robot interface — the single entry point for any backend.

    Combines identity, joint configuration, tool definitions, kinematics,
    lifecycle management, and client factories into one Protocol.

    Replaces the former ``RobotProfile``, ``RobotModel``, and
    ``BackendManager`` Protocols.
    """

    # -- Identity -----------------------------------------------------------

    @property
    def name(self) -> str:
        """Human-readable robot name, e.g. ``"PAROL6"``."""
        ...

    # -- Structured sub-objects ---------------------------------------------

    @property
    def joints(self) -> JointsSpec:
        """Joint configuration: count, names, limits, home position."""
        ...

    @property
    def tools(self) -> ToolsSpec:
        """Available end-effector tools and their capabilities."""
        ...

    # -- Unit preferences ---------------------------------------------------

    @property
    def position_unit(self) -> Literal["mm", "m"]:
        """How this robot's users think about distance (display hint)."""
        ...

    # -- Capability flags ---------------------------------------------------

    @property
    def has_force_torque(self) -> bool:
        """Whether force / torque readout is available."""
        ...

    @property
    def has_freedrive(self) -> bool:
        """Whether a freedrive / teach mode is available."""
        ...

    @property
    def digital_outputs(self) -> int:
        """Number of digital output pins."""
        ...

    @property
    def digital_inputs(self) -> int:
        """Number of digital input pins."""
        ...

    # -- Visualization ------------------------------------------------------

    @property
    def urdf_path(self) -> str:
        """Path to the URDF file for 3-D rendering."""
        ...

    @property
    def mesh_dir(self) -> str:
        """Directory containing STL / mesh files referenced by the URDF."""
        ...

    @property
    def joint_index_mapping(self) -> tuple[int, ...]:
        """Maps URDF joint indices to control joint indices."""
        ...

    # -- Motion configuration -----------------------------------------------

    @property
    def motion_profiles(self) -> tuple[str, ...]:
        """Available motion profile names, e.g. ``("TRAPEZOIDAL", "S_CURVE")``."""
        ...

    @property
    def cartesian_frames(self) -> tuple[str, ...]:
        """Available Cartesian reference frames for jogging."""
        ...

    # -- Backend injection --------------------------------------------------

    @property
    def backend_package(self) -> str:
        """Python package used by user scripts and subprocess workers."""
        ...

    @property
    def sync_client_class(self) -> type:
        """The synchronous client class (e.g. ``RobotClient``).

        Used for editor autocomplete discovery and stepping wrapper.
        Convention: backends export this class at their package level.
        """
        ...

    @property
    def async_client_class(self) -> type:
        """The asynchronous client class (e.g. ``AsyncRobotClient``).

        Used for editor command discovery (introspecting available methods).
        Convention: backends export this class at their package level.
        """
        ...

    # -- Kinematics ---------------------------------------------------------

    def fk(self, q_rad: NDArray[np.float64]) -> NDArray[np.float64]:
        """Forward kinematics.

        *q_rad*: joint angles in radians ``(num_joints,)``.

        Returns ``(6,)`` — ``[x, y, z, rx, ry, rz]`` in meters + radians.
        """
        ...

    def ik(
        self, pose: NDArray[np.float64], q_seed_rad: NDArray[np.float64]
    ) -> IKResult:
        """Inverse kinematics.

        *pose*: ``[x, y, z, rx, ry, rz]`` — meters + radians.
        *q_seed_rad*: current joint angles in radians (seed).

        Returns an ``IKResult`` with ``q`` in radians.
        """
        ...

    def check_limits(self, q_rad: NDArray[np.float64]) -> bool:
        """Return ``True`` if all joints are within limits."""
        ...

    def fk_batch(self, joint_path_rad: NDArray[np.float64]) -> NDArray[np.float64]:
        """Batch FK: ``(N, num_joints)`` radians → ``(N, 6)`` poses (m + rad)."""
        ...

    def ik_batch(
        self,
        poses: NDArray[np.float64],
        q_start_rad: NDArray[np.float64],
    ) -> list[IKResult]:
        """Batch IK: ``(N, 6)`` poses → list of ``IKResult`` (radians)."""
        ...

    # -- Lifecycle ----------------------------------------------------------

    def start(self, **kwargs: Any) -> None:
        """Start the backend process / connection (blocking).

        What "start" means is backend-specific: spawn a subprocess,
        connect to a remote server, launch a ROS node, etc.
        """
        ...

    def stop(self) -> None:
        """Stop the backend process and release resources."""
        ...

    def is_available(self, **kwargs: Any) -> bool:
        """Check if the backend is reachable / ready."""
        ...

    # -- Factories ----------------------------------------------------------

    def create_client(self, **kwargs: Any) -> RobotClient:
        """Create an async client connected to this backend."""
        ...

    def create_dry_run_client(self, **kwargs: Any) -> DryRunClient | None:
        """Create an offline simulation client, or None if unsupported."""
        ...
