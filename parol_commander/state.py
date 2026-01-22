import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

import numpy as np
from nicegui import binding
from parol6.server.loop_timer import PhaseTimer
from typing_extensions import dataclass_transform

# Type-checking shim for bindable_dataclass to satisfy Pylance without changing runtime
if TYPE_CHECKING:
    from nicegui.elements.timer import Timer
    from parol_commander.services.urdf_scene import UrdfScene
    from parol_commander.components.editor import EditorPanel
    from parol_commander.components.control import ControlPanel
    from parol_commander.components.readout import ReadoutPanel

    @dataclass_transform(field_specifiers=(field,))
    def bindable_dataclass(cls=None, /, **kwargs):
        return cls  # type: ignore[return-value]
else:
    bindable_dataclass = binding.bindable_dataclass


class AngleArray:
    """Dual-representation angle array storing both degrees and radians.

    Provides zero-allocation access to angles in either unit. Conversion
    happens once at update time via set_deg() or set_rad().
    """

    __slots__ = ("_deg", "_rad")

    def __init__(self, size: int = 6) -> None:
        self._deg = np.zeros(size, dtype=np.float64)
        self._rad = np.zeros(size, dtype=np.float64)

    @property
    def deg(self) -> np.ndarray:
        """Angles in degrees."""
        return self._deg

    @property
    def rad(self) -> np.ndarray:
        """Angles in radians."""
        return self._rad

    def set_deg(self, values: np.ndarray) -> None:
        """Set angles from degrees, computing radians in-place."""
        self._deg[:] = values
        np.deg2rad(self._deg, out=self._rad)

    def set_rad(self, values: np.ndarray) -> None:
        """Set angles from radians, computing degrees in-place."""
        self._rad[:] = values
        np.rad2deg(self._rad, out=self._deg)

    def __len__(self) -> int:
        return len(self._deg)

    def __getitem__(self, idx: int) -> float:
        """Index access returns degrees (for backwards compatibility)."""
        return float(self._deg[idx])


class OrientationArray:
    """Dual-representation orientation array storing both degrees and radians.

    Stores rx, ry, rz (roll, pitch, yaw) in both units. Conversion
    happens once at update time via set_deg() or set_rad().
    """

    __slots__ = ("_deg", "_rad")

    def __init__(self) -> None:
        self._deg = np.zeros(3, dtype=np.float64)
        self._rad = np.zeros(3, dtype=np.float64)

    @property
    def deg(self) -> np.ndarray:
        """Orientation in degrees [rx, ry, rz]."""
        return self._deg

    @property
    def rad(self) -> np.ndarray:
        """Orientation in radians [rx, ry, rz]."""
        return self._rad

    def set_deg(self, values: np.ndarray) -> None:
        """Set orientation from degrees, computing radians in-place."""
        self._deg[:] = values
        np.deg2rad(self._deg, out=self._rad)

    def set_rad(self, values: np.ndarray) -> None:
        """Set orientation from radians, computing degrees in-place."""
        self._rad[:] = values
        np.rad2deg(self._rad, out=self._deg)

    def __len__(self) -> int:
        return len(self._deg)

    def __getitem__(self, idx: int) -> float:
        """Index access returns degrees (for backwards compatibility)."""
        return float(self._deg[idx])


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


@dataclass
class ProgramTarget:
    id: str  # Unique identifier (UUID)
    line_number: int  # Line number in the editor (1-based)
    pose: list[float]  # [x, y, z, rx, ry, rz]
    move_type: str  # "cartesian", "pose", "joints"
    scene_object_id: str  # ID of the 3D marker object in the scene

    def to_dict(self) -> dict:
        """Serialize to dict for subprocess communication."""
        return {
            "id": self.id,
            "line_number": self.line_number,
            "pose": self.pose,
            "move_type": self.move_type,
            "scene_object_id": self.scene_object_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProgramTarget":
        """Deserialize from dict."""
        return cls(**d)


@dataclass
class PathSegment:
    points: list[list[float]]  # List of [x, y, z] points defining the segment
    color: str  # Hex color code (green, blue, orange, red)
    is_valid: bool  # Whether the segment is reachable (IK valid)
    line_number: int  # Source line number in program
    joints: list[float] | None = None  # Joint angles at end of segment
    move_type: str = "cartesian"  # "cartesian", "joints", "smooth_*"
    is_dashed: bool = True  # Whether to render as dashed line
    show_arrows: bool = True  # Whether to show direction arrows
    # Timing validation fields
    estimated_duration: float | None = None  # Computed duration from trajectory builder
    requested_duration: float | None = None  # User-requested duration
    timing_feasible: bool = True  # Whether motion achievable in requested time

    def to_dict(self) -> dict:
        """Serialize to dict for subprocess communication."""
        return {
            "points": self.points,
            "color": self.color,
            "is_valid": self.is_valid,
            "line_number": self.line_number,
            "joints": self.joints,
            "move_type": self.move_type,
            "is_dashed": self.is_dashed,
            "show_arrows": self.show_arrows,
            "estimated_duration": self.estimated_duration,
            "requested_duration": self.requested_duration,
            "timing_feasible": self.timing_feasible,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PathSegment":
        """Deserialize from dict."""
        return cls(**d)


@dataclass
class PlaybackState:
    """State for unified playback (simulation and robot execution)."""

    is_playing: bool = False
    is_simulating: bool = False  # True = sim mode, False = robot execution
    current_step: int = 0
    total_steps: int = 0
    playback_speed: float = 1.0  # 1.0, 2.0, 4.0, 8.0
    scrub_interactive: bool = True  # False in robot mode


@bindable_dataclass
class SimulationState:
    targets: list[ProgramTarget] = field(default_factory=list)
    path_segments: list[PathSegment] = field(default_factory=list)
    current_step_index: int = 0
    total_steps: int = 0
    is_playing: bool = False
    playback_speed: float = 1.0  # Multiplier
    preview_mode: bool = False  # True=Dry Run, False=Real Execute
    paths_visible: bool = True
    envelope_visible: bool = False
    envelope_mode: str = "auto"  # "auto" | "on" | "off"
    _change_listeners: list[Callable[[], None]] = field(
        default_factory=list, repr=False
    )

    def add_change_listener(self, callback: Callable[[], None]) -> None:
        """Register a callback to be notified when simulation state changes."""
        if callback not in self._change_listeners:
            self._change_listeners.append(callback)

    def remove_change_listener(self, callback: Callable[[], None]) -> None:
        """Unregister a previously registered callback."""
        if callback in self._change_listeners:
            self._change_listeners.remove(callback)

    def notify_changed(self) -> None:
        """Notify all registered listeners that state has changed."""
        for cb in self._change_listeners:
            cb()


@bindable_dataclass
class RecordingState:
    is_recording: bool = False
    mode: str = "manual"  # "manual", "continuous", "post_jog"
    capture_interval_s: float = 0.5


# Extended shared state singletons for cross-module access
# Only scalar fields are bindable - numpy arrays are excluded to avoid comparison issues
@bindable_dataclass(
    bindable_fields=[
        "connected",
        "x",
        "y",
        "z",
        "rx",
        "ry",
        "rz",
        "io_in1",
        "io_in2",
        "io_out1",
        "io_out2",
        "io_estop",
        "grip_id",
        "grip_pos",
        "grip_speed",
        "grip_current",
        "grip_obj",
        "simulator_active",
        "action_current",
        "action_state",
        "editing_mode",
    ]
)
class RobotState:
    # Preallocated arrays for zero-allocation hot path updates
    angles: AngleArray = field(default_factory=AngleArray)  # joint angles (deg/rad)
    orientation: OrientationArray = field(
        default_factory=OrientationArray
    )  # rx/ry/rz (deg/rad)
    pose: np.ndarray = field(
        default_factory=lambda: np.zeros(16, dtype=np.float64)
    )  # homogeneous transform flattened
    io: np.ndarray = field(
        default_factory=lambda: np.zeros(5, dtype=np.int32)
    )  # [in1,in2,out1,out2,estop]
    gripper: np.ndarray = field(
        default_factory=lambda: np.zeros(6, dtype=np.int32)
    )  # [id,pos,spd,cur,status,obj]
    # Movement enablement arrays from STATUS (12 ints each)
    joint_en: np.ndarray = field(default_factory=lambda: np.ones(12, dtype=np.int32))
    cart_en_wrf: np.ndarray = field(default_factory=lambda: np.ones(12, dtype=np.int32))
    cart_en_trf: np.ndarray = field(default_factory=lambda: np.ones(12, dtype=np.int32))
    connected: bool = False
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
    simulator_active: bool = False
    action_current: str = ""
    action_state: str = ""
    last_update_ts: float = 0.0  # timestamp of last STATUS update
    action_queue: list[dict] = field(default_factory=list)
    # Editing mode - when True, x/y/z/angles are controlled by target editor
    editing_mode: bool = False
    _change_listeners: list[Callable[[], None]] = field(
        default_factory=list, repr=False
    )

    def add_change_listener(self, callback: Callable[[], None]) -> None:
        """Register a callback to be notified when robot state changes."""
        if callback not in self._change_listeners:
            self._change_listeners.append(callback)

    def remove_change_listener(self, callback: Callable[[], None]) -> None:
        """Unregister a previously registered callback."""
        if callback in self._change_listeners:
            self._change_listeners.remove(callback)

    def notify_changed(self) -> None:
        """Notify all registered listeners that state has changed."""
        for cb in self._change_listeners:
            cb()


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


@bindable_dataclass
class UiState:
    # URDF scene instance (holds UrdfSceneConfig)
    urdf_scene: "UrdfScene | None" = None
    urdf_joint_names: list[str] | None = None
    urdf_index_mapping: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    current_tool_stls: list[Any] = field(default_factory=list)

    # Control panel UI state
    jog_speed: int = 50
    jog_accel: int = 50
    incremental_jog: bool = False
    joint_step_deg: float = 1.0
    frame: str = "WRF"
    gizmo_visible: bool = True

    # Private storage for timers and panels (set post-build)
    _joint_jog_timer: Any = None
    _cart_jog_timer: Any = None
    _editor_panel: Any = None
    _control_panel: Any = None
    _readout_panel: Any = None

    # Program panel visibility (tracked for tab flash when panel closed)
    program_panel_visible: bool = False

    @property
    def editor_panel(self) -> "EditorPanel":
        """Get editor panel, asserting it's initialized."""
        assert self._editor_panel is not None, "editor_panel not initialized"
        return self._editor_panel

    @editor_panel.setter
    def editor_panel(self, value: "EditorPanel") -> None:
        self._editor_panel = value

    @property
    def control_panel(self) -> "ControlPanel":
        """Get control panel, asserting it's initialized."""
        assert self._control_panel is not None, "control_panel not initialized"
        return self._control_panel

    @control_panel.setter
    def control_panel(self, value: "ControlPanel") -> None:
        self._control_panel = value

    @property
    def readout_panel(self) -> "ReadoutPanel":
        """Get readout panel, asserting it's initialized."""
        assert self._readout_panel is not None, "readout_panel not initialized"
        return self._readout_panel

    @readout_panel.setter
    def readout_panel(self, value: "ReadoutPanel") -> None:
        self._readout_panel = value

    @property
    def joint_jog_timer(self) -> "Timer":
        """Get joint jog timer, asserting it's initialized."""
        assert self._joint_jog_timer is not None, "joint_jog_timer not initialized"
        return self._joint_jog_timer

    @joint_jog_timer.setter
    def joint_jog_timer(self, value: "Timer") -> None:
        self._joint_jog_timer = value

    @property
    def cart_jog_timer(self) -> "Timer":
        """Get cart jog timer, asserting it's initialized."""
        assert self._cart_jog_timer is not None, "cart_jog_timer not initialized"
        return self._cart_jog_timer

    @cart_jog_timer.setter
    def cart_jog_timer(self, value: "Timer") -> None:
        self._cart_jog_timer = value


@dataclass
class EditorTab:
    """State for a single editor tab."""

    id: str  # Unique tab identifier (UUID hex)
    filename: str  # Display name / filename
    file_path: str | None  # Full path if saved to server
    content: str  # Current editor content
    saved_content: str  # Content at last save (for dirty tracking)
    output_log: list[str] = field(default_factory=list)  # Per-tab output log entries
    path_segments: list[PathSegment] = field(
        default_factory=list
    )  # Per-tab simulation paths
    targets: list[ProgramTarget] = field(default_factory=list)  # Per-tab targets
    final_joints_rad: list[float] | None = None  # Final joint position from simulation
    created_at: float = 0.0  # Timestamp

    @property
    def is_dirty(self) -> bool:
        """Return True if content differs from saved content."""
        return self.content != self.saved_content


@bindable_dataclass
class EditorTabsState:
    """State for multi-tab editor."""

    tabs: list[EditorTab] = field(default_factory=list)
    active_tab_id: str | None = None
    _change_listeners: list[Callable[[], None]] = field(
        default_factory=list, repr=False
    )

    def get_active_tab(self) -> EditorTab | None:
        """Get the currently active tab."""
        if not self.active_tab_id:
            return None
        return next((t for t in self.tabs if t.id == self.active_tab_id), None)

    def find_tab_by_path(self, file_path: str | None) -> EditorTab | None:
        """Find a tab with the given file path. Returns None if file_path is None."""
        if file_path is None:
            return None
        return next((t for t in self.tabs if t.file_path == file_path), None)

    def find_tab_by_id(self, tab_id: str) -> EditorTab | None:
        """Find a tab by its ID."""
        return next((t for t in self.tabs if t.id == tab_id), None)

    def add_tab(self, tab: EditorTab) -> None:
        """Add a new tab."""
        self.tabs.append(tab)
        self.notify_changed()

    def remove_tab(self, tab_id: str) -> None:
        """Remove a tab by ID."""
        self.tabs = [t for t in self.tabs if t.id != tab_id]
        if self.active_tab_id == tab_id:
            self.active_tab_id = self.tabs[0].id if self.tabs else None
        self.notify_changed()

    def add_change_listener(self, callback: Callable[[], None]) -> None:
        """Register a callback to be notified when tabs state changes."""
        if callback not in self._change_listeners:
            self._change_listeners.append(callback)

    def remove_change_listener(self, callback: Callable[[], None]) -> None:
        """Unregister a previously registered callback."""
        if callback in self._change_listeners:
            self._change_listeners.remove(callback)

    def notify_changed(self) -> None:
        """Notify all registered listeners that state has changed."""
        for cb in self._change_listeners:
            cb()


@dataclass
class ReadinessState:
    """Tracks application initialization readiness for tests.

    This provides precise synchronization points that tests can await
    instead of using blind sleep() calls.

    Events:
        app_ready: Set when app is fully ready (startup done + backend streaming + page init)
        urdf_scene_ready: Set when URDF 3D scene is fully initialized
    """

    app_ready: asyncio.Event = field(default_factory=asyncio.Event)
    urdf_scene_ready: asyncio.Event = field(default_factory=asyncio.Event)

    app_ready_ts: float = 0.0
    urdf_scene_ready_ts: float = 0.0

    # Internal tracking flags for app_ready
    _startup_done: bool = False
    _backend_done: bool = False
    _page_done: bool = False

    def reset(self) -> None:
        """Reset all events for test isolation."""
        self.app_ready = asyncio.Event()
        self.urdf_scene_ready = asyncio.Event()
        self.app_ready_ts = 0.0
        self.urdf_scene_ready_ts = 0.0
        self._startup_done = False
        self._backend_done = False
        self._page_done = False

    def _check_app_ready(self) -> None:
        """Check if all conditions are met and signal app_ready if so."""
        if self._startup_done and self._backend_done and self._page_done:
            if not self.app_ready.is_set():
                self.app_ready_ts = time.time()
                self.app_ready.set()
                logging.debug("Readiness: app_ready signaled")

    def mark_startup_done(self) -> None:
        """Mark startup as complete (call from _on_startup finally block)."""
        if not self._startup_done:
            self._startup_done = True
            logging.debug("Readiness: startup done")
            self._check_app_ready()

    def mark_backend_done(self) -> None:
        """Mark backend as ready (call from _status_consumer on first valid status)."""
        if not self._backend_done:
            self._backend_done = True
            logging.debug("Readiness: backend done")
            self._check_app_ready()

    def mark_page_done(self) -> None:
        """Mark page as ready (call from index_page after setup)."""
        if not self._page_done:
            self._page_done = True
            logging.debug("Readiness: page done")
            self._check_app_ready()

    def signal_urdf_scene_ready(self) -> None:
        """Signal that URDF scene is ready (call from initialize_urdf_scene)."""
        if not self.urdf_scene_ready.is_set():
            self.urdf_scene_ready_ts = time.time()
            self.urdf_scene_ready.set()
            logging.debug("Readiness: urdf_scene_ready signaled")


# Module-level singletons
robot_state: RobotState = RobotState()
controller_state: ControllerState = ControllerState()
program_state: ProgramState = ProgramState()
ui_state: UiState = UiState()
simulation_state: SimulationState = SimulationState()
recording_state: RecordingState = RecordingState()
playback_state: PlaybackState = PlaybackState()
readiness_state: ReadinessState = ReadinessState()
editor_tabs_state: EditorTabsState = EditorTabsState()

# Global timing instrumentation - import and use from any module
# Usage: with global_phase_timer.phase("my_operation"): ...
global_phase_timer = PhaseTimer(
    [
        "status",  # Receiving/parsing status + updating panels
        "scene",  # 3D scene updates (angles, TCP ball, envelope)
        "jog",  # Joint and cartesian jog API calls
    ]
)
