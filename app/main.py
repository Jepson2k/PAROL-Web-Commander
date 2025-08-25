from __future__ import annotations

import asyncio
import os
import re
import sys
import json
from pathlib import Path
from typing import Optional, List, Tuple, TypedDict

from nicegui import ui
from nicegui import app as ng_app
from theme import apply_theme as ctk_apply_theme, set_theme as ctk_set_theme, get_theme as ctk_get_theme, toggle_theme as ctk_toggle_theme
import logging

# Local services (existing)
try:
    from services.server_manager import ServerManager
    from services.robot_client import RobotClient
    from config import Config
except ImportError:
    # During initial scaffolding, the services may not exist yet.
    # Provide minimal fallbacks so this file can run.
    class Config:  # type: ignore[no-redef]
        HOST: str = os.getenv("PAROL6_SERVER_HOST", "127.0.0.1")
        PORT: int = int(os.getenv("PAROL6_SERVER_PORT", "5001"))
        AUTO_START: bool = False
        DEFAULT_COM_PORT: Optional[str] = os.getenv("PAROL6_COM_PORT")
        UI_PORT: int = int(os.getenv("UI_PORT", "8080"))

    class ServerManager:  # type: ignore[no-redef]
        def __init__(self, controller_path: str) -> None:
            self.controller_path = controller_path
            self._pid = None

        def start_controller(self, com_port: Optional[str] = None) -> None:
            ui.notify("ServerManager not yet implemented", color="warning")

        def stop_controller(self) -> None:
            ui.notify("ServerManager not yet implemented", color="warning")

        def is_running(self) -> bool:
            return False

        @property
        def pid(self) -> Optional[int]:
            return self._pid

    class RobotClient:  # type: ignore[no-redef]
        def __init__(self, host: str, port: int, timeout: float = 2.0, retries: int = 1) -> None:
            self.host = host
            self.port = port

        def home(self) -> str: return "HOME sent"
        def stop(self) -> str: return "STOP sent"
        def enable(self) -> str: return "ENABLE sent"
        def disable(self) -> str: return "DISABLE sent"
        def clear_error(self) -> str: return "CLEAR_ERROR sent"
        def get_status(self): return None
        def get_angles(self): return None
        def get_io(self): return None
        def get_gripper_status(self): return None
        def set_com_port(self, port_str: str) -> str: return f"SET_PORT {port_str} sent"
        def jog_joint(self, joint_index: int, speed_percentage: int, duration: Optional[float] = None, distance_deg: Optional[float] = None) -> str: return f"JOG {joint_index}"
        def jog_cartesian(self, frame: str, axis: str, speed_percentage: int, duration: float) -> str: return f"CARTJOG {frame} {axis}"
        def move_joints(self, joint_angles: List[float], duration: Optional[float] = None, speed_percentage: Optional[int] = None) -> str: return "MOVEJOINT"
        def move_pose(self, pose: List[float], duration: Optional[float] = None, speed_percentage: Optional[int] = None) -> str: return "MOVEPOSE"
        def move_cartesian(self, pose: List[float], duration: Optional[float] = None, speed_percentage: Optional[float] = None) -> str: return "MOVECART"
        def control_pneumatic_gripper(self, action: str, port: int) -> str: return f"PNEU {action} {port}"
        def control_electric_gripper(self, action: str, position: Optional[int] = 255, speed: Optional[int] = 150, current: Optional[int] = 500) -> str: return "ELEC"


# Resolve controller path relative to repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_PATH = (REPO_ROOT / "PAROL6-python-API" / "headless_commander.py").as_posix()
# Register static files for optimized icons and other assets
ng_app.add_static_files('/static', (REPO_ROOT / 'app' / 'static').as_posix())

# Expose PAROL6-python-API for joint limits import
sys.path.append((REPO_ROOT / "PAROL6-python-API").as_posix())
try:
    from PAROL6_ROBOT import Joint_limits_degree as JOINT_LIMITS_DEG  # type: ignore[attr-defined]
except Exception:
    JOINT_LIMITS_DEG = [[-180, 180], [-180, 180], [-180, 180], [-180, 180], [-180, 180], [0, 360]]

try:
    config = Config() if callable(Config) else Config  # supports instance export (instance or class)
except TypeError:
    config = Config
server_manager = ServerManager(controller_path=CONTROLLER_PATH)
client = RobotClient(host=config.HOST, port=config.PORT, timeout=0.20, retries=0)

# ------------------------ Global UI/state ------------------------

# Labels and indicators (populated by status poller)
# controller_label = ui.label("Controller: stopped").classes("text-sm")
controller_label = ""
fw_version = "1.0.0"
angles_label = "-"
pose_label = "-"
io_label = "-"
# gripper_label = ui.label("Gripper: -").classes("text-sm")
gripper_label = "-"
estop_indicator = "-"
# estop_indicator = ui.label("E-STOP: unknown").classes("text-sm")

# Response log
log_buffer: List[str] = []
response_log: ui.log
# Grid label references for readouts
tool_labels = {}  # keys: "X","Y","Z","Rx","Ry","Rz" -> ui.label
joint_labels: List = []  # 6 label refs for q1..q6
joint_progress_bars: List = []  # progress bars for q1..q6
# I/O page label refs
io_in1_label = None
io_in2_label = None
io_estop_label2 = None
io_out1_label = None
io_out2_label = None
# Readouts card IO summary
io_summary_label = None
# Gripper page refs
grip_id_label = None
grip_cal_status_label = None
grip_err_status_label = None
grip_pos_feedback_label = None
grip_current_feedback_label = None
grip_obj_detect_label = None
# Gripper control widgets
grip_pos_slider = None
grip_speed_slider = None
grip_current_slider = None
grip_id_input = None
estop_label = None
# Status polling control (gated, non-blocking)
status_timer: Optional[ui.timer] = None
status_busy = False
consecutive_failures = 0

def log_info(msg: str) -> None:
    response_log.push(msg)
    logging.info(msg)

def log_warn(msg: str) -> None:
    response_log.push(f"[WARN] {msg}")
    logging.warn(f"[WARN] {msg}")

def log_err(msg: str) -> None:
    response_log.push(f"[ERR] {msg}")
    logging.error(f"[ERR] {msg}")

# Jog state
pressed_pos = [False] * 6
pressed_neg = [False] * 6
jog_speed_value = 50       # %
jog_accel_value = 50       # % (kept as UI state; server may not consume)
incremental_jog_enabled = False
joint_step_deg = 1.0

# Cartesian jog state
current_frame = "TRF"
pressed_axes = {
    "X+": False, "X-": False,
    "Y+": False, "Y-": False,
    "Z+": False, "Z-": False,
    "RX+": False, "RX-": False,
    "RY+": False, "RY-": False,
    "RZ+": False, "RZ-": False,
}

# Live values for templates
latest_angles: List[float] = []
latest_pose: List[float] = []
latest_io: List[int] = []

# Program editor globals
PROGRAM_DIR = (REPO_ROOT / "PAROL-commander-software" / "GUI" / "files" / "Programs")
if not PROGRAM_DIR.exists():
    PROGRAM_DIR = REPO_ROOT / "programs"
    PROGRAM_DIR.mkdir(parents=True, exist_ok=True)

program_task: Optional[asyncio.Task] = None
program_cancel_event: Optional[asyncio.Event] = None
program_speed_percentage: Optional[int] = None  # set by JointVelSet alias

# Page routing globals
active_page: str = "Move"
header_nav_buttons = {}  # type: ignore[var-annotated]
# Top-level page containers
move_page = None
io_page = None
settings_page = None
calibrate_page = None
gripper_page = None

# --------------- Jog helpers ---------------

def set_joint_pressed(j: int, direction: str, is_pressed: bool) -> None:
    """Press-and-hold jog; if incremental mode is ON, fire one-shot step."""
    global pressed_pos, pressed_neg
    if 0 <= j < 6:
        # if incremental enabled and key down, send one-shot and return
        if incremental_jog_checkbox.value and is_pressed:
            speed = int(jog_speed_value)
            step = abs(float(joint_step_input.value or joint_step_deg))
            index = j if direction == 'pos' else (j + 6)
            client.jog_joint(index, speed_percentage=speed, duration=None, distance_deg=step)
            log_info(f"JOG step joint {j+1} {'+' if direction=='pos' else '-'} {step}deg @ {speed}%")
            return
        # press-and-hold mode
        if direction == 'pos':
            pressed_pos[j] = is_pressed
        else:
            pressed_neg[j] = is_pressed

def set_jog_speed(v) -> None:
    global jog_speed_value
    jog_speed_value = int(v)

def set_jog_accel(v) -> None:
    global jog_accel_value
    jog_accel_value = int(v)

def jog_tick() -> None:
    """Send short jog bursts while plus/minus buttons are pressed."""
    speed = int(jog_speed_value)
    duration = 0.1
    for j in range(6):
        if pressed_pos[j]:
            client.jog_joint(j, speed, duration=duration)
        if pressed_neg[j]:
            client.jog_joint(j + 6, speed, duration=duration)

# --------------- Cartesian jog helpers ---------------

def set_axis_pressed(axis: str, is_pressed: bool) -> None:
    if axis in pressed_axes:
        if incremental_jog_checkbox.value and is_pressed:
            speed = int(jog_speed_value)
            # map "step" to duration heuristic
            step = float(joint_step_input.value or joint_step_deg)
            duration = max(0.02, min(0.5, step / 50.0))
            client.jog_cartesian(current_frame, axis, speed, duration)
            log_info(f"CART JOG step {axis} {duration:.2f}s @ {speed}% frame={current_frame}")
            return
        pressed_axes[axis] = is_pressed

def set_frame(frame: str) -> None:
    global current_frame
    if frame in ("TRF", "WRF"):
        current_frame = frame

def cart_jog_tick() -> None:
    """Send short cartesian jog bursts while axis buttons are pressed."""
    speed = int(jog_speed_value)
    duration = 0.1
    for axis, pressed in pressed_axes.items():
        if pressed:
            client.jog_cartesian(current_frame, axis, speed, duration)

# --------------- Controller controls ---------------

async def start_controller(com_port: Optional[str]) -> None:
    try:
        server_manager.start_controller(com_port=com_port)
        await asyncio.sleep(0.2)
        # enable status polling now that we are connected
        global status_timer, consecutive_failures
        try:
            if status_timer:
                status_timer.active = True
            consecutive_failures = 0
        except Exception:
            pass
        controller_label = f"running (pid={getattr(server_manager, 'pid', None)})"
        ui.notify("Controller started", color="positive")
        log_info("Controller started")
    except Exception as e:
        ui.notify(f"Failed to start controller: {e}", color="negative")
        log_err(f"Start controller failed: {e}")

async def stop_controller() -> None:
    try:
        server_manager.stop_controller()
        await asyncio.sleep(0.2)
        # disable status polling on disconnect
        global status_timer, consecutive_failures
        try:
            if status_timer:
                status_timer.active = False
            consecutive_failures = 0
        except Exception:
            pass
        controller_label = "stopped"
        ui.notify("Controller stopped", color="positive")
        log_info("Controller stopped")
    except Exception as e:
        ui.notify(f"Failed to stop controller: {e}", color="negative")
        log_err(f"Stop controller failed: {e}")

async def send_home() -> None:
    try:
        resp = client.home()
        ui.notify(resp, color="primary")
        log_info(resp)
    except Exception as e:
        log_err(f"HOME failed: {e}")

async def send_stop_motion() -> None:
    try:
        resp = client.stop()
        ui.notify(resp, color="warning")
        log_warn(resp)
    except Exception as e:
        log_err(f"STOP failed: {e}")

async def send_enable() -> None:
    try:
        resp = client.enable()
        ui.notify(resp, color="positive")
        log_info(resp)
    except Exception as e:
        log_err(f"ENABLE failed: {e}")

async def send_disable() -> None:
    try:
        resp = client.disable()
        ui.notify(resp, color="warning")
        log_warn(resp)
    except Exception as e:
        log_err(f"DISABLE failed: {e}")

async def send_clear_error() -> None:
    try:
        resp = client.clear_error()
        ui.notify(resp, color="primary")
        log_info(resp)
    except Exception as e:
        log_err(f"CLEAR_ERROR failed: {e}")

async def set_port(port_str: str) -> None:
    if not port_str:
        ui.notify("Provide a COM/tty port", color="warning")
        return
    try:
        resp = client.set_com_port(port_str)
        ui.notify(resp, color="primary")
        log_info(resp)
    except Exception as e:
        log_err(f"SET_PORT failed: {e}")

async def show_received_frame() -> None:
    """Show raw GET_STATUS frame in the log if available."""
    try:
        # best-effort access to raw response
        raw = None
        if hasattr(client, "_request"):
            raw = client._request("GET_STATUS", bufsize=4096)  # type: ignore[attr-defined]
        if raw:
            log_info(f"[FRAME] {raw}")
        else:
            log_warn("No frame received (GET_STATUS unsupported)")
    except Exception as e:
        log_err(f"GET_STATUS raw failed: {e}")

# --------------- Status polling ---------------

async def update_status_async() -> None:
    global latest_angles, latest_pose, latest_io, angles_label, pose_label, io_label, gripper_label
    global status_busy, status_timer, consecutive_failures
    if status_busy:
        return
    status_busy = True
    # run potentially blocking UDP call off the event loop
    s = await asyncio.to_thread(client.get_status)
    if s:
        angles = s.get("angles") or []
        io = s.get("io") or []
        gr = s.get("gripper") or []
        pose = s.get("pose") or []

        latest_angles = angles or latest_angles
        latest_pose = pose or latest_pose
        latest_io = io or latest_io

        if angles:
            angles_label = f"{', '.join(f'{a:.1f}' for a in angles)}"
            if joint_labels and len(angles) >= 1:
                for i, a in enumerate(angles[:6]):
                    if i < len(joint_labels):
                        joint_labels[i].text = f"{a:.1f}"
                    if joint_progress_bars and len(angles) >= 1:
                        for i, a in enumerate(angles[:6]):
                            if i < len(joint_progress_bars):
                                lim = JOINT_LIMITS_DEG[i] if i < len(JOINT_LIMITS_DEG) else [-180, 180]
                                joint_progress_bars[i].value = _normalize_joint_progress(a, lim[0], lim[1])
        else:
            angles_label = "-"

        if pose and len(pose) >= 12:
            # Pose matrix flattened; indices 3,7,11 as XYZ
            x, y, z = pose[3], pose[7], pose[11]
            # Orientation Rx,Ry,Rz not provided explicitly; show zeros if unavailable
            pose_label = f"X={x:.1f} Y={y:.1f} Z={z:.1f} Rx=?.? Ry=?.? Rz=?.?"
            if tool_labels:
                if "X" in tool_labels: tool_labels["X"].text = f"{x:.1f}"
                if "Y" in tool_labels: tool_labels["Y"].text = f"{y:.1f}"
                if "Z" in tool_labels: tool_labels["Z"].text = f"{z:.1f}"
                # Rx/Ry/Rz left as '-' unless provided in future
        else:
            pose_label = "-"

        if len(io) >= 5:
            in1, in2, out1, out2, estop = io[:5]
            estop_text = "OK" if estop else "TRIGGERED"
            io_label = f"IN1={in1} IN2={in2} OUT1={out1} OUT2={out2} ESTOP={estop_text}"
            if estop_label:
                estop_label.text = f"E-STOP: {estop_text}"
                # green for OK, red for TRIGGERED
                estop_label.style("color: #21BA45" if estop else "color: #DB2828")
            if io_in1_label: io_in1_label.text = f"INPUT 1: {in1}"
            if io_in2_label: io_in2_label.text = f"INPUT 2: {in2}"
            if io_out1_label: io_out1_label.text = f"OUTPUT 1 is: {out1}"
            if io_out2_label: io_out2_label.text = f"OUTPUT 2 is: {out2}"
            if io_estop_label2: io_estop_label2.text = f"ESTOP: {estop_text}"
            if io_summary_label: io_summary_label.text = f"IO: IN1={in1} IN2={in2} OUT1={out1} OUT2={out2} ESTOP={estop_text}"
            if estop == 0:
                controller_label = "E-STOP TRIGGERED"
        else:
            io_label = "-"
            if estop_label:
                estop_label.text = "E-STOP: unknown"
                estop_label.style("color: inherit")
            if io_in1_label: io_in1_label.text = "INPUT 1: -"
            if io_in2_label: io_in2_label.text = "INPUT 2: -"
            if io_out1_label: io_out1_label.text = "OUTPUT 1 is: -"
            if io_out2_label: io_out2_label.text = "OUTPUT 2 is: -"
            if io_estop_label2: io_estop_label2.text = "ESTOP: unknown"
            if io_summary_label: io_summary_label.text = "IO: -"

        if len(gr) >= 6:
            gid, pos, spd, cur, status_b, obj = gr[:6]
            gripper_label = f"ID={gid} POS={pos} SPD={spd} CUR={cur} OBJ={obj} STATUS=0b{status_b:08b}"
            if grip_id_label: grip_id_label.text = f"Gripper ID is: {gid}"
            if grip_pos_feedback_label: grip_pos_feedback_label.text = f"Gripper position feedback is: {pos}"
            if grip_current_feedback_label: grip_current_feedback_label.text = f"Gripper current feedback is: {cur}"
            if grip_obj_detect_label: grip_obj_detect_label.text = f"Gripper object detection is: {obj}"
        else:
            gripper_label = "-"
            if grip_id_label: grip_id_label.text = "Gripper ID is: -"
            if grip_pos_feedback_label: grip_pos_feedback_label.text = "Gripper position feedback is: -"
            if grip_current_feedback_label: grip_current_feedback_label.text = "Gripper current feedback is: -"
            if grip_obj_detect_label: grip_obj_detect_label.text = "Gripper object detection is: -"

        # success: speed up polling (but keep reasonable)
        if status_timer and getattr(status_timer, "interval", None) != 0.2:
            status_timer.interval = 0.2
        consecutive_failures = 0
    else:
        # failure: keep pose/estop persistent; clear labels minimally
        angles_label = "-"
        io_label = "-"
        gripper_label = "-"
        consecutive_failures += 1
        # slow down polling while offline to keep UI responsive
        if status_timer and getattr(status_timer, "interval", None) != 1.0:
            status_timer.interval = 1.0
    status_busy = False

# Joint limit utils
def _normalize_joint_progress(angle_deg: float, min_deg: float, max_deg: float) -> float:
    if max_deg <= min_deg:
        return 0.0
    val = (angle_deg - min_deg) / (max_deg - min_deg)
    return max(0.0, min(1.0, val))
# --------------- Program editor helpers ---------------

def _get_opt(tokens: List[str], key: str) -> Optional[float]:
    key = key.upper()
    for t in tokens:
        if t.upper().startswith(f"{key}="):
            return float(t.split("=", 1)[1])
    return None

def _parse_csv_floats(s: str) -> Optional[List[float]]:
    return [float(x.strip()) for x in s.split(",") if x.strip() != ""]

def _parse_motion_args(argstr: str) -> Tuple[List[float], dict, List[str]]:
    """
    Strictly parse motion function arguments like:
      "j1,j2,j3,j4,j5,j6, v=50, a=30, t=2.5, trap, speed"
    Returns (values:list[float], opts:dict, errors:list[str])
      - values: exactly 6 numeric values (list of floats)
      - opts keys: v (float|None), a (float|None), t (float|None),
                   profile ('trap'|'poly'|None), tracking ('SPEED'|None)
      - errors: list of validation error strings (non-empty => invalid)
    Rules (to mirror legacy Tkinter behavior):
      - Require exactly 6 leading numeric values.
      - Options may only appear AFTER the 6 numerics.
      - Allowed options:
          v=NN (0..100), a=NN (0..100), t=NN (>0), 'trap'|'poly', 'speed'
      - Unknown tokens, duplicate options, or malformed numerics => error.
      - If both t and v/a given, accept but duration (t) takes precedence at runtime.
    """
    tokens_raw = [t.strip() for t in (argstr or "").split(",") if t.strip() != ""]
    values: List[float] = []
    errors: List[str] = []
    opts = {"v": None, "a": None, "t": None, "profile": None, "tracking": None}

    # 1) Collect exactly 6 leading numeric values
    idx = 0
    while idx < len(tokens_raw) and len(values) < 6:
        tkn = tokens_raw[idx]
        try:
            num = float(tkn)
            values.append(num)
        except Exception:
            errors.append(f"Expected numeric value #{len(values)+1} but got '{tkn}'")
            # Stop early; enforce strict numeric sequence
            break
        idx += 1

    if not errors and len(values) != 6:
        errors.append(f"Expected 6 numeric values, got {len(values)}")

    # 2) Parse options after the 6 numerics
    seen = set()
    while not errors and idx < len(tokens_raw):
        tkn = tokens_raw[idx]
        low = tkn.lower()

        if low.startswith("v="):
            if "v" in seen:
                errors.append("Duplicate 'v' option")
                break
            seen.add("v")
            rhs = tkn.split("=", 1)[1].strip()
            try:
                v = float(rhs)
            except Exception:
                errors.append(f"Malformed v value '{rhs}'")
                break
            if not (0 <= v <= 100):
                errors.append(f"v out of range (0..100): {v}")
                break
            opts["v"] = v
            idx += 1
            continue

        if low.startswith("a="):
            if "a" in seen:
                errors.append("Duplicate 'a' option")
                break
            seen.add("a")
            rhs = tkn.split("=", 1)[1].strip()
            try:
                a = float(rhs)
            except Exception:
                errors.append(f"Malformed a value '{rhs}'")
                break
            if not (0 <= a <= 100):
                errors.append(f"a out of range (0..100): {a}")
                break
            opts["a"] = a
            idx += 1
            continue

        if low.startswith("t="):
            if "t" in seen:
                errors.append("Duplicate 't' option")
                break
            seen.add("t")
            rhs = tkn.split("=", 1)[1].strip()
            try:
                tval = float(rhs)
            except Exception:
                errors.append(f"Malformed t value '{rhs}'")
                break
            if not (tval > 0):
                errors.append(f"t must be > 0: {tval}")
                break
            opts["t"] = tval
            idx += 1
            continue

        if low in ("trap", "poly"):
            if "profile" in seen:
                errors.append("Duplicate profile token")
                break
            seen.add("profile")
            opts["profile"] = low
            idx += 1
            continue

        if low == "speed":
            if "tracking" in seen:
                errors.append("Duplicate tracking token")
                break
            seen.add("tracking")
            opts["tracking"] = "SPEED"
            idx += 1
            continue

        # Unknown or misplaced token
        errors.append(f"Unknown token '{tkn}' (options must be after 6 values)")
        break

    return values, opts, errors

async def load_program(filename: Optional[str] = None) -> None:
    try:
        name = filename or program_filename_input.value or "execute_script.txt"
        text = (PROGRAM_DIR / name).read_text(encoding="utf-8")
        program_textarea.value = text
        ui.notify(f"Loaded {name}", color="primary")
        log_info(f"Loaded program {name}")
    except Exception as e:
        ui.notify(f"Load failed: {e}", color="negative")
        log_err(f"Load failed: {e}")

async def save_program(as_name: Optional[str] = None) -> None:
    try:
        name = as_name or program_filename_input.value or "execute_script.txt"
        (PROGRAM_DIR / name).write_text(program_textarea.value or "", encoding="utf-8")
        ui.notify(f"Saved {name}", color="positive")
        log_info(f"Saved program {name}")
        if as_name:
            program_filename_input.value = as_name
    except Exception as e:
        ui.notify(f"Save failed: {e}", color="negative")
        log_err(f"Save failed: {e}")


# Aliases: Delay(120), JointVelSet(50), JointMove(...), PoseMove(...), etc.
_re_delay = re.compile(r"^\s*Delay\(\s*([0-9]*\.?[0-9]+)\s*\)\s*$", re.IGNORECASE)
_re_joint_vel = re.compile(r"^\s*JointVelSet\(\s*([0-9]+)\s*\)\s*$", re.IGNORECASE)
_re_joint_move = re.compile(r"^\s*JointMove\(\s*([^\)]*)\)\s*$", re.IGNORECASE)
_re_pose_move = re.compile(r"^\s*PoseMove\(\s*([^\)]*)\)\s*$", re.IGNORECASE)
# Legacy CTk command regex
_re_move_joint_legacy = re.compile(r"^\s*MoveJoint\(\s*([^\)]*)\)\s*$", re.IGNORECASE)
_re_move_pose_legacy = re.compile(r"^\s*MovePose\(\s*([^\)]*)\)\s*$", re.IGNORECASE)
_re_move_cart_legacy = re.compile(r"^\s*MoveCart\(\s*([^\)]*)\)\s*$", re.IGNORECASE)
_re_move_cart_rel_trf = re.compile(r"^\s*MoveCartRelTRF\(\s*([^\)]*)\)\s*$", re.IGNORECASE)
_re_speed_joint = re.compile(r"^\s*SpeedJoint\(\s*([0-9]*)\s*\)\s*$", re.IGNORECASE)
_re_speed_cart = re.compile(r"^\s*SpeedCart\(\s*([0-9]*)\s*\)\s*$", re.IGNORECASE)
# Legacy function-style helpers
_re_home_fn = re.compile(r"^\s*Home\(\s*\)\s*$", re.IGNORECASE)
_re_begin_fn = re.compile(r"^\s*Begin\(\s*\)\s*$", re.IGNORECASE)
_re_end_fn = re.compile(r"^\s*End\(\s*\)\s*$", re.IGNORECASE)
_re_loop_fn = re.compile(r"^\s*Loop\(\s*\)\s*$", re.IGNORECASE)
_re_output_fn = re.compile(r"^\s*Output\(\s*(\d+)\s*,\s*(HIGH|LOW)\s*\)\s*$", re.IGNORECASE)
_re_gripper_fn = re.compile(r"^\s*Gripper\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*$", re.IGNORECASE)
_re_gripper_cal_fn = re.compile(r"^\s*Gripper_cal\(\s*\)\s*$", re.IGNORECASE)

async def _run_program() -> None:
    global program_speed_percentage
    program_speed_percentage = None  # reset each run
    lines = (program_textarea.value or "").splitlines()
    for raw in lines:
        if program_cancel_event and program_cancel_event.is_set():
            ui.notify("Program stopped", color="warning")
            log_warn("Program stopped")
            return

        line = raw.strip()
        if not line or line.startswith(("#", "//", ";")):
            continue

        # Function-style legacy commands
        m = _re_home_fn.match(line)
        if m:
            try:
                client.home()
                log_info("Home()")
                await asyncio.sleep(0.1)
            except Exception as e:
                log_err(f"Home() failed: {e}")
            continue
        m = _re_begin_fn.match(line)
        if m:
            log_info("Begin()")
            continue
        m = _re_end_fn.match(line)
        if m:
            log_info("End()")
            # Stop at End() for now
            break
        m = _re_loop_fn.match(line)
        if m:
            log_info("Loop()")
            # Note: full loop semantics are not implemented; accept as no-op
            continue
        m = _re_output_fn.match(line)
        if m:
            try:
                port = int(m.group(1))
                state = m.group(2).upper()
                action = "open" if state == "HIGH" else "close"
                client.control_pneumatic_gripper(action, port)
                log_info(f"Output({port},{state}) -> {action}")
                await asyncio.sleep(0.05)
            except Exception as e:
                log_err(f"Output() failed: {e}")
            continue
        m = _re_gripper_fn.match(line)
        if m:
            try:
                pos = int(m.group(1)); spd = int(m.group(2)); cur = int(m.group(3))
                client.control_electric_gripper("move", position=pos, speed=spd, current=cur)
                log_info(f"Gripper({pos},{spd},{cur})")
                await asyncio.sleep(0.05)
            except Exception as e:
                log_err(f"Gripper() failed: {e}")
            continue
        m = _re_gripper_cal_fn.match(line)
        if m:
            try:
                client.control_electric_gripper("calibrate")
                log_info("Gripper_cal()")
                await asyncio.sleep(0.05)
            except Exception as e:
                log_err(f"Gripper_cal() failed: {e}")
            continue

        # Handle alias forms
        m = _re_delay.match(line)
        if m:
            try:
                sec = float(m.group(1))
                log_info(f"Delay({sec})")
                await asyncio.sleep(sec)
            except Exception as e:
                ui.notify(f"Program error at '{line}': {e}", color="negative")
                log_err(f"Program error at '{line}': {e}")
                return
            continue

        m = _re_joint_vel.match(line)
        if m:
            try:
                program_speed_percentage = int(m.group(1))
                log_info(f"JointVelSet({program_speed_percentage})")
            except Exception as e:
                ui.notify(f"Program error at '{line}': {e}", color="negative")
                log_err(f"Program error at '{line}': {e}")
                return
            continue

        m = _re_joint_move.match(line)
        if m:
            vals = _parse_csv_floats(m.group(1) or "")
            if not vals or len(vals) < 6:
                ui.notify(f"Program error at '{line}': need 6 joint angles", color="negative")
                log_err(f"Program error at '{line}': need 6 joint angles")
                return
            angles = [float(x) for x in vals[:6]]
            # optional duration/speed from alias omitted -> use program_speed_percentage
            client.move_joints(angles, duration=None, speed_percentage=program_speed_percentage)
            log_info(f"JointMove({', '.join(f'{a:.1f}' for a in angles)}) @spd={program_speed_percentage if program_speed_percentage else 'default'}")
            await asyncio.sleep(0.5)
            continue

        m = _re_pose_move.match(line)
        if m:
            vals = _parse_csv_floats(m.group(1) or "")
            if not vals or len(vals) < 6:
                ui.notify(f"Program error at '{line}': need 6 pose values", color="negative")
                log_err(f"Program error at '{line}': need 6 pose values")
                return
            pose = [float(x) for x in vals[:6]]
            client.move_pose(pose, duration=None, speed_percentage=program_speed_percentage)
            log_info(f"PoseMove({', '.join(f'{v:.1f}' for v in pose)}) @spd={program_speed_percentage if program_speed_percentage else 'default'}")
            await asyncio.sleep(0.5)
            continue

        # Legacy CTk commands (MoveJoint/MovePose/MoveCart/MoveCartRelTRF/SpeedJoint/SpeedCart)
        m = _re_move_joint_legacy.match(line)
        if m:
            args = m.group(1) or ""
            values, opts, errors = _parse_motion_args(args)
            if errors:
                msg = f"MoveJoint error: {', '.join(errors)}"
                ui.notify(msg, color="negative")
                log_err(msg)
                return
            if len(values) != 6:
                ui.notify("MoveJoint: need exactly 6 joint angles", color="negative")
                log_err("MoveJoint: need exactly 6 joint angles")
                return
            angles = [float(x) for x in values]
            dur = float(opts["t"]) if opts["t"] is not None else None
            spd = int(opts["v"]) if opts["v"] is not None else (program_speed_percentage if program_speed_percentage is not None and dur is None else None)
            accel = int(opts["a"]) if opts["a"] is not None else None
            profile = opts["profile"].upper() if opts["profile"] else None
            tracking = opts["tracking"]
            client.move_joints(
                angles,
                duration=dur,
                speed_percentage=None if dur is not None else spd,
                accel_percentage=accel,
                profile=profile,
                tracking=tracking,
            )
            log_info(f"MoveJoint({', '.join(f'{a:.1f}' for a in angles)}) dur={dur} spd={spd} a={accel} prof={profile} track={tracking}")
            await asyncio.sleep(dur if dur else 0.5)
            continue

        m = _re_move_pose_legacy.match(line)
        if m:
            args = m.group(1) or ""
            values, opts, errors = _parse_motion_args(args)
            if errors:
                msg = f"MovePose error: {', '.join(errors)}"
                ui.notify(msg, color="negative")
                log_err(msg)
                return
            if len(values) != 6:
                ui.notify("MovePose: need exactly 6 pose values", color="negative")
                log_err("MovePose: need exactly 6 pose values")
                return
            pose = [float(x) for x in values]
            dur = float(opts["t"]) if opts["t"] is not None else None
            spd = int(opts["v"]) if opts["v"] is not None else (program_speed_percentage if program_speed_percentage is not None and dur is None else None)
            accel = int(opts["a"]) if opts["a"] is not None else None
            profile = opts["profile"].upper() if opts["profile"] else None
            tracking = opts["tracking"]
            client.move_pose(
                pose,
                duration=dur,
                speed_percentage=None if dur is not None else spd,
                accel_percentage=accel,
                profile=profile,
                tracking=tracking,
            )
            log_info(f"MovePose({', '.join(f'{v:.1f}' for v in pose)}) dur={dur} spd={spd} a={accel} prof={profile} track={tracking}")
            await asyncio.sleep(dur if dur else 0.5)
            continue

        m = _re_move_cart_legacy.match(line)
        if m:
            args = m.group(1) or ""
            values, opts, errors = _parse_motion_args(args)
            if errors:
                msg = f"MoveCart error: {', '.join(errors)}"
                ui.notify(msg, color="negative")
                log_err(msg)
                return
            if len(values) != 6:
                ui.notify("MoveCart: need exactly 6 pose values", color="negative")
                log_err("MoveCart: need exactly 6 pose values")
                return
            pose = [float(x) for x in values]
            dur = float(opts["t"]) if opts["t"] is not None else None
            spd = int(opts["v"]) if opts["v"] is not None else (program_speed_percentage if program_speed_percentage is not None and dur is None else None)
            accel = int(opts["a"]) if opts["a"] is not None else None
            profile = opts["profile"].upper() if opts["profile"] else None
            tracking = opts["tracking"]
            client.move_cartesian(
                pose,
                duration=dur,
                speed_percentage=None if dur is not None else spd,
                accel_percentage=accel,
                profile=profile,
                tracking=tracking,
            )
            log_info(f"MoveCart({', '.join(f'{v:.1f}' for v in pose)}) dur={dur} spd={spd} a={accel} prof={profile} track={tracking}")
            await asyncio.sleep(dur if dur else 0.5)
            continue

        m = _re_move_cart_rel_trf.match(line)
        if m:
            args = m.group(1) or ""
            values, opts, errors = _parse_motion_args(args)
            if errors:
                msg = f"MoveCartRelTRF error: {', '.join(errors)}"
                ui.notify(msg, color="negative")
                log_err(msg)
                return
            if len(values) != 6:
                ui.notify("MoveCartRelTRF: need exactly 6 deltas", color="negative")
                log_err("MoveCartRelTRF: need exactly 6 deltas")
                return
            deltas = [float(x) for x in values]
            dur = float(opts["t"]) if opts["t"] is not None else None
            spd = int(opts["v"]) if opts["v"] is not None else (program_speed_percentage if program_speed_percentage is not None and dur is None else None)
            accel = int(opts["a"]) if opts["a"] is not None else None
            profile = opts["profile"].upper() if opts["profile"] else None
            tracking = opts["tracking"]
            client.move_cartesian_rel_trf(
                deltas,
                duration=dur,
                speed_percentage=None if dur is not None else spd,
                accel_percentage=accel,
                profile=profile,
                tracking=tracking,
            )
            log_info(f"MoveCartRelTRF({', '.join(f'{v:.1f}' for v in deltas)}) dur={dur} spd={spd} a={accel} prof={profile} track={tracking}")
            await asyncio.sleep(dur if dur else 0.5)
            continue

        m = _re_speed_joint.match(line)
        if m:
            try:
                val = m.group(1)
                program_speed_percentage = int(val) if val and val.strip().isdigit() else int(jog_speed_value)
                log_info(f"SpeedJoint({program_speed_percentage})")
            except Exception as e:
                ui.notify(f"SpeedJoint error: {e}", color="negative")
                log_err(f"SpeedJoint error: {e}")
                return
            continue

        m = _re_speed_cart.match(line)
        if m:
            try:
                val = m.group(1)
                program_speed_percentage = int(val) if val and val.strip().isdigit() else int(jog_speed_value)
                log_info(f"SpeedCart({program_speed_percentage})")
            except Exception as e:
                ui.notify(f"SpeedCart error: {e}", color="negative")
                log_err(f"SpeedCart error: {e}")
                return
            continue

        # Legacy commands remain supported
        tokens = line.split()
        cmd = tokens[0].upper()
        try:
            if cmd == "HOME":
                client.home()
                log_info("HOME")
                await asyncio.sleep(0.1)
            elif cmd == "DELAY" and len(tokens) >= 2:
                sec = float(tokens[1])
                log_info(f"DELAY {sec}")
                await asyncio.sleep(sec)
            elif cmd == "ENABLE":
                client.enable()
                log_info("ENABLE")
                await asyncio.sleep(0.05)
            elif cmd == "DISABLE":
                client.disable()
                log_info("DISABLE")
                await asyncio.sleep(0.05)
            elif cmd == "CLEAR_ERROR":
                client.clear_error()
                log_info("CLEAR_ERROR")
                await asyncio.sleep(0.05)
            elif cmd == "STOP":
                client.stop()
                log_warn("STOP")
                await asyncio.sleep(0.05)
            elif cmd == "MOVEJOINT" and len(tokens) >= 7:
                angles = [float(x) for x in tokens[1:7]]
                dur = _get_opt(tokens[7:], "DURATION")
                spd = _get_opt(tokens[7:], "SPEED")
                if dur is None and spd is None and program_speed_percentage is not None:
                    spd = float(program_speed_percentage)
                client.move_joints(angles, duration=dur, speed_percentage=None if dur is not None else (int(spd) if spd is not None else None))
                log_info(f"MOVEJOINT {angles} dur={dur} spd={spd}")
                await asyncio.sleep(dur if dur else 0.5)
            elif cmd == "MOVEPOSE" and len(tokens) >= 7:
                pose = [float(x) for x in tokens[1:7]]
                dur = _get_opt(tokens[7:], "DURATION")
                spd = _get_opt(tokens[7:], "SPEED")
                if dur is None and spd is None and program_speed_percentage is not None:
                    spd = float(program_speed_percentage)
                client.move_pose(pose, duration=dur, speed_percentage=None if dur is not None else (int(spd) if spd is not None else None))
                log_info(f"MOVEPOSE {pose} dur={dur} spd={spd}")
                await asyncio.sleep(dur if dur else 0.5)
            elif cmd == "MOVECART" and len(tokens) >= 7:
                pose = [float(x) for x in tokens[1:7]]
                dur = _get_opt(tokens[7:], "DURATION")
                spd = _get_opt(tokens[7:], "SPEED")
                client.move_cartesian(pose, duration=dur, speed_percentage=None if dur is not None else (int(spd) if spd is not None else None))
                log_info(f"MOVECART {pose} dur={dur} spd={spd}")
                await asyncio.sleep(dur if dur else 0.5)
            elif cmd == "PNEUMATIC" and len(tokens) >= 3:
                action = tokens[1].lower()
                port = int(tokens[2])
                client.control_pneumatic_gripper(action, port)
                log_info(f"PNEUMATIC {action} {port}")
                await asyncio.sleep(0.05)
            elif cmd == "ELECTRIC" and len(tokens) >= 2:
                sub = tokens[1].upper()
                if sub == "CALIBRATE":
                    client.control_electric_gripper("calibrate")
                    log_info("ELECTRIC CALIBRATE")
                elif sub == "MOVE" and len(tokens) >= 5:
                    pos = int(tokens[2]); spd = int(tokens[3]); cur = int(tokens[4])
                    client.control_electric_gripper("move", position=pos, speed=spd, current=cur)
                    log_info(f"ELECTRIC MOVE pos={pos} spd={spd} cur={cur}")
                await asyncio.sleep(0.05)
            else:
                ui.notify(f"Unknown command: {line}", color="warning")
                log_warn(f"Unknown command: {line}")
                await asyncio.sleep(0.01)
        except Exception as e:
            ui.notify(f"Program error at '{line}': {e}", color="negative")
            log_err(f"Program error at '{line}': {e}")
            return
    ui.notify("Program finished", color="positive")
    log_info("Program finished")

async def execute_program() -> None:
    global program_task, program_cancel_event
    if program_task and not program_task.done():
        ui.notify("Program already running", color="warning")
        return
    program_cancel_event = asyncio.Event()
    program_task = asyncio.create_task(_run_program())

async def stop_program() -> None:
    global program_task, program_cancel_event
    if program_cancel_event:
        program_cancel_event.set()
    if program_task:
        try:
            await asyncio.wait_for(program_task, timeout=0.1)
        except Exception:
            pass

# ------------------------ UI Layout ------------------------
# -------------------------------
# THEME: mimic CustomTkinter "dark-blue" style
# -------------------------------
CTK_PRIMARY = '#1F6AA5'   # primary blue (buttons, highlights)
CTK_PRIMARY_DARK = '#184F7A'
CTK_ACCENT = '#22D3EE'
CTK_BG = '#2B2B2B'        # window background
CTK_SURFACE = '#3A3A3A'   # panels/cards
CTK_TEXT = '#E5E7EB'      # near-white text
CTK_MUTED = '#9CA3AF'

def apply_theme() -> None:
    # Apply palette to NiceGUI/Quasar.
    ui.dark_mode().enable()
    ui.colors(
        primary=CTK_PRIMARY,
        secondary=CTK_PRIMARY_DARK,
        accent=CTK_ACCENT,
        positive='#21BA45',
        negative='#DB2828',
        info='#31CCEC',
        warning='#F2C037',
    )

    # Global CSS to nudge shapes/spacing to CustomTkinter vibe
    ui.add_css(f"""
      body, .q-page {{ background: {CTK_BG}; color: {CTK_TEXT}; }}
      .q-header, .q-footer {{ background: {CTK_SURFACE}; }}
      .q-card, .q-field, .q-toolbar, .q-item {{ background: {CTK_SURFACE}; color: {CTK_TEXT}; }}
      .q-btn:not(.q-btn--round) {{ border-radius: 8px; }}
      .q-btn--flat, .q-btn--outline {{ color: {CTK_TEXT}; }}
      .q-input .q-field__native, .q-textarea .q-field__native {{ color: {CTK_TEXT}; }}
      .q-field__control {{ border-radius: 8px; }}
      .q-slider__track-container {{ border-radius: 9999px; }}
      .q-separator {{ background: {CTK_MUTED}; }}
    """)

def build_header() -> None:
    # Header with left navigation tabs, centered firmware text, right help + theme toggle
    with ui.header().classes("px-3 py-1"):
        with ui.row().classes("w-full items-center justify-between"):
            # Left: navigation tabs
            with ui.tabs() as nav_tabs:
                ui.tab("Move")
                ui.tab("I/O")
                ui.tab("Settings")
                ui.tab("Calibrate")
                ui.tab("Gripper")
                nav_tabs.value = "Move"  # Set initial active tab
                nav_tabs.on_value_change(lambda e: set_active_page(nav_tabs.value))
            # Center: firmware label
            ui.label(f"Source controller fw version: {fw_version}").classes("text-sm text-center")
            # Right: theme toggle and help
            with ui.row().classes("items-center gap-2"):
                theme_toggle = ui.toggle(options=["System", "Light", "Dark"], value="System").props("dense")
                def _on_theme_change():
                    val = (theme_toggle.value or "System").lower()
                    mode = "system" if val.startswith("s") else ("light" if val.startswith("l") else "dark")
                    ctk_set_theme(mode)
                    ui.run_javascript(f"localStorage.setItem('ctk_theme_mode', '{mode}')")
                    # Update code editor theme
                    program_textarea.theme = "default" if mode == "light" else "oneDark"
                theme_toggle.on_value_change(lambda e: _on_theme_change())
                ui.button("?", on_click=lambda: ui.notify("Help: PAROL6 NiceGUI Commander", color="primary")).props("round unelevated")

def open_file_picker() -> None:
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label("Open Program from disk")
        def _on_upload(e):
            try:
                data = e.content.read()
                name = getattr(e, "name", None) or "uploaded_program.txt"
                (PROGRAM_DIR / name).write_bytes(data)
                program_filename_input.value = name
                program_textarea.value = data.decode('utf-8', errors='ignore')
                ui.notify(f"Loaded {name}", color="primary")
            except Exception as ex:
                ui.notify(f"Open failed: {ex}", color="negative")
            finally:
                dlg.close()
        ui.upload(on_upload=_on_upload).props("accept=.txt,.prog,.gcode,*/*")
        with ui.row().classes("gap-2"):
            ui.button("Cancel", on_click=dlg.close)
    dlg.open()

def build_command_palette_table(prefill_toggle) -> None:
    # Simplified structure with unique keys for row_key
    rows = [
        {"key": "MoveJoint", "title": "MoveJoint()"},
        {"key": "MovePose", "title": "MovePose()"},
        {"key": "SpeedJoint", "title": "SpeedJoint()"},
        {"key": "MoveCart", "title": "MoveCart()"},
        {"key": "MoveCartRelTRF", "title": "MoveCartRelTRF()"},
        {"key": "SpeedCart", "title": "SpeedCart()"},
        {"key": "Home", "title": "Home()"},
        {"key": "Delay", "title": "Delay()"},
        {"key": "End", "title": "End()"},
        {"key": "Loop", "title": "Loop()"},
        {"key": "Begin", "title": "Begin()"},
        {"key": "Input", "title": "Input()"},
        {"key": "Output", "title": "Output()"},
        {"key": "Gripper", "title": "Gripper()"},
        {"key": "Gripper_cal", "title": "Gripper_cal()"},
        {"key": "Get_data", "title": "Get_data()"},
        {"key": "Timeouts", "title": "Timeouts()"},
    ]
    
    columns = [
        {"name": "title", "label": "Command", "field": "title", "sortable": True, "align": "left"},
    ]

    # Scrollable container for the table
    with ui.element('div').classes("overflow-y-auto w-full").style("height: 400px"):
        table = ui.table(
            columns=columns, 
            rows=rows, 
            row_key='key',  # Use unique key column
        ).props("flat dense separator=horizontal")

    def make_snippet(key: str) -> str:
        current = bool(prefill_toggle.value)

        if key == "MoveJoint":
            if current and latest_angles and len(latest_angles) >= 6:
                return "MoveJoint(" + ", ".join(f"{a:.1f}" for a in latest_angles[:6]) + ")"
            return "MoveJoint(0, 0, 0, 0, 0, 0)"
        elif key == "SpeedJoint":
            return "SpeedJoint(50)"
        elif key == "MovePose":
            if current and latest_pose and len(latest_pose) >= 12:
                x, y, z = latest_pose[3], latest_pose[7], latest_pose[11]
                return f"MovePose({x:.1f}, {y:.1f}, {z:.1f}, 0, 0, 0)"
            return "MovePose(0, 0, 0, 0, 0, 0)"
        elif key == "MoveCart":
            if current and latest_pose and len(latest_pose) >= 12:
                x, y, z = latest_pose[3], latest_pose[7], latest_pose[11]
                return f"MoveCart({x:.1f}, {y:.1f}, {z:.1f}, 0, 0, 0)"
            return "MoveCart(0, 0, 0, 0, 0, 0)"
        elif key == "MoveCartRelTRF":
            return "MoveCartRelTRF(0, 0, 0, 0, 0, 0)"
        elif key == "SpeedCart":
            return "SpeedCart(50)"
        elif key == "Home":
            return "Home()"
        elif key == "Delay":
            return "Delay(1.0)"
        elif key == "End":
            return "End()"
        elif key == "Loop":
            return "Loop()"
        elif key == "Begin":
            return "Begin()"
        elif key == "Input":
            return "Input()"
        elif key == "Output":
            return "Output(1,HIGH)"
        elif key == "Gripper":
            return "Gripper(10,50,180)"
        elif key == "Gripper_cal":
            return "Gripper_cal()"
        elif key == "Get_data":
            return "Get_data()"
        elif key == "Timeouts":
            return "Timeouts()"
        else:
            # Fallback to row title
            for row in rows:
                if row.get("key") == key:
                    return row.get("title", key)
            return key

    def insert_from_row(e) -> None:
        try:
            row_data = e.args[1] if len(e.args) >= 2 else {}
            key = row_data.get("key", "")
            
            if key:
                snippet = make_snippet(key)
                val = program_textarea.value
                if val and not val.endswith("\n"):
                    val += "\n"
                program_textarea.value = val + snippet + "\n"
                log_info(f"Added command: {snippet}")
        except Exception as ex:
            ui.notify(f"Click handler error: {ex}", color="negative")
            log_err(f"Click handler error: {ex}")

    table.on('rowClick', insert_from_row)

def set_active_page(page: str) -> None:
    global active_page
    active_page = page
    # Toggle container visibility
    if move_page: move_page.visible = (page == "Move")
    if io_page: io_page.visible = (page == "I/O")
    if settings_page: settings_page.visible = (page == "Settings")
    if calibrate_page: calibrate_page.visible = (page == "Calibrate")
    if gripper_page: gripper_page.visible = (page == "Gripper")
    # Update header nav styles
    for name, btn in header_nav_buttons.items():
        btn.props("unelevated color=primary").classes("text-white")

def set_output(port: int, state: int) -> None:
    """Map Output 1/2 via pneumatic gripper actions through the UDP API."""
    try:
        action = "open" if state else "close"
        resp = client.control_pneumatic_gripper(action, port)
        ui.notify(resp, color="primary")
        log_info(f"OUTPUT{port} -> {action.upper()}")
    except Exception as e:
        log_err(f"Set output failed: {e}")

def build_io_panel() -> None:
    global io_in1_label, io_in2_label, io_estop_label2, io_out1_label, io_out2_label
    with ui.card().classes("w-full"):
        ui.label("I/O").classes("text-md font-medium")
        with ui.column().classes("gap-2"):
            with ui.row().classes("items-center gap-4"):
                io_in1_label = ui.label("INPUT 1: -").classes("text-sm")
                io_in2_label = ui.label("INPUT 2: -").classes("text-sm")
                io_estop_label2 = ui.label("ESTOP: unknown").classes("text-sm")
            ui.separator()
            with ui.row().classes("items-center gap-4"):
                io_out1_label = ui.label("OUTPUT 1 is: -").classes("text-sm")
                ui.button("LOW", on_click=lambda: set_output(1, 0)).props("unelevated")
                ui.button("HIGH", on_click=lambda: set_output(1, 1)).props("unelevated")
            with ui.row().classes("items-center gap-4"):
                io_out2_label = ui.label("OUTPUT 2 is: -").classes("text-sm")
                ui.button("LOW", on_click=lambda: set_output(2, 0)).props("unelevated")
                ui.button("HIGH", on_click=lambda: set_output(2, 1)).props("unelevated")

def build_settings_panel() -> None:
    with ui.card().classes("w-full"):
        ui.label("Settings").classes("text-md font-medium")
        with ui.row().classes("items-center gap-2"):
            mode_toggle = ui.toggle(options=["System", "Light", "Dark"], value="System").props("dense")
            def _on_mode():
                val = (mode_toggle.value or "System").lower()
                mode = "system" if val.startswith("s") else ("light" if val.startswith("l") else "dark")
                ctk_set_theme(mode)
                ui.run_javascript(f"localStorage.setItem('ctk_theme_mode', '{mode}')")
            mode_toggle.on_value_change(lambda e: _on_mode())
            ui.label("Tip: Use browser zoom for UI scaling.").classes("text-sm text-[var(--ctk-muted)]")

def build_calibrate_panel() -> None:
    with ui.card().classes("w-full"):
        ui.label("Calibrate").classes("text-md font-medium")
        with ui.row().classes("items-center gap-2"):
            ui.button("Enable motor", on_click=lambda: asyncio.create_task(send_enable())).props("unelevated color=positive")
            ui.button("Disable motor", on_click=lambda: asyncio.create_task(send_disable())).props("unelevated color=negative")
            ui.button("Go to limit").props("unelevated disable")
        with ui.row().classes("items-center gap-2"):
            ui.select(options=[f"Joint {i}" for i in range(1, 7)], label="Joint").props("dense")
        ui.label("Note: 'Go to limit' requires server support and is not available yet.").classes("text-xs text-[var(--ctk-muted)]")

def build_gripper_panel() -> None:
    global grip_id_label, grip_cal_status_label, grip_err_status_label
    global grip_pos_feedback_label, grip_current_feedback_label, grip_obj_detect_label
    global grip_pos_slider, grip_speed_slider, grip_current_slider, grip_id_input
    with ui.card().classes("w-full"):
        ui.label("Gripper").classes("text-md font-medium")
        # Device info
        with ui.row().classes("items-center gap-4"):
            grip_id_label = ui.label("Gripper ID is: -").classes("text-sm")
            grip_cal_status_label = ui.label("Calibration status is: -").classes("text-sm")
            grip_err_status_label = ui.label("Error status is: -").classes("text-sm")
        # Actions
        with ui.row().classes("items-center gap-2"):
            def _grip_cal():
                try:
                    resp = client.control_electric_gripper("calibrate")
                    ui.notify(resp, color="primary")
                    log_info("ELECTRIC CALIBRATE")
                except Exception as e:
                    log_err(f"Gripper calibrate failed: {e}")
            ui.button("Calibrate gripper", on_click=_grip_cal).props("unelevated")
            def _grip_clear_error():
                ui.notify("Clear gripper error requires server support (TODO)", color="warning")
                log_warn("Gripper clear error: TODO (server support needed)")
            ui.button("Clear gripper error", on_click=_grip_clear_error).props("unelevated")
        # Command parameters
        ui.label("Command parameters").classes("text-sm mt-2")
        with ui.row().classes("items-center gap-2"):
            grip_pos_slider = ui.slider(min=0, max=255, value=10, step=1).classes("w-64")
            ui.label("Position").classes("text-xs text-[var(--ctk-muted)]")
        with ui.row().classes("items-center gap-2"):
            grip_speed_slider = ui.slider(min=0, max=255, value=50, step=1).classes("w-64")
            ui.label("Speed").classes("text-xs text-[var(--ctk-muted)]")
        with ui.row().classes("items-center gap-2"):
            grip_current_slider = ui.slider(min=100, max=1000, value=180, step=10).classes("w-64")
            ui.label("Current (mA)").classes("text-xs text-[var(--ctk-muted)]")
        with ui.row().classes("items-center gap-2"):
            def _grip_move():
                try:
                    pos = int(grip_pos_slider.value or 0)
                    spd = int(grip_speed_slider.value or 0)
                    cur = int(grip_current_slider.value or 100)
                    resp = client.control_electric_gripper("move", position=pos, speed=spd, current=cur)
                    ui.notify(resp, color="primary")
                    log_info(f"ELECTRIC MOVE pos={pos} spd={spd} cur={cur}")
                except Exception as e:
                    log_err(f"Gripper move failed: {e}")
            ui.button("Move GoTo", on_click=_grip_move).props("unelevated color=primary")
            grip_id_input = ui.input(label="Change ID", value="0").classes("w-24")
            def _grip_change_id():
                try:
                    _ = int(grip_id_input.value or "0")
                    # Placeholder: requires specific API for changing ID
                    ui.notify("Change ID requires server support (TODO)", color="warning")
                    log_warn("Change gripper ID: TODO (server support needed)")
                except Exception as e:
                    log_err(f"Change ID parse failed: {e}")
            ui.button("Apply ID", on_click=_grip_change_id).props("unelevated")
        # Feedback
        ui.label("Gripper feedback").classes("text-sm mt-2")
        with ui.column().classes("gap-1"):
            grip_pos_feedback_label = ui.label("Gripper position feedback is: -").classes("text-sm")
            grip_current_feedback_label = ui.label("Gripper current feedback is: -").classes("text-sm")
            grip_obj_detect_label = ui.label("Gripper object detection is: -").classes("text-sm")

def build_footer() -> None:
    # Footer: Simulator/Real, Connect/Disconnect, Clear error, E-stop
    with ui.footer().classes("justify-between items-center px-3 py-1"):
        with ui.row().classes("items-center gap-4"):
            mode_radio = ui.toggle(options=["Simulator", "Real robot"], value="Real robot").props("dense")
            global estop_label
            estop_label = ui.label("E-STOP: unknown").classes("text-sm")
        with ui.row().classes("items-center gap-2"):
            com_input = ui.input(
                label="COM Port (COM5 / /dev/ttyACM0 / /dev/tty.usbmodem0)",
                value=getattr(config, "DEFAULT_COM_PORT", "") or "",
            ).classes("w-80")
            ui.button("Set Port", on_click=lambda: asyncio.create_task(set_port(com_input.value)))
            ui.separator().props("vertical")
            ui.button("Connect", on_click=lambda: asyncio.create_task(start_controller(com_input.value))).props("color=positive")
            ui.button("Disconnect", on_click=lambda: asyncio.create_task(stop_controller())).props("color=negative")
            ui.button("Clear error", on_click=lambda: asyncio.create_task(send_clear_error())).props("color=warning")
            ui.button("Stop Motion", on_click=lambda: asyncio.create_task(send_stop_motion())).props("color=negative")

# ------------------- Drag-and-drop layout (Move page) -------------------

class MoveLayout(TypedDict):
    left: list[str]
    right: list[str]

LAYOUT_PATH = (REPO_ROOT / "app" / "layout.json")
DEFAULT_LAYOUT: MoveLayout = {"left": ["jog", "readouts"], "right": ["editor", "log"]}

def load_layout() -> MoveLayout:
    try:
        return json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_LAYOUT.copy()

def save_layout(layout: MoveLayout) -> None:
    try:
        LAYOUT_PATH.write_text(json.dumps(layout, indent=2), encoding="utf-8")
    except Exception as e:
        logging.error(f"Failed to save layout: {e}")

current_layout: MoveLayout = load_layout()
_drag_id: Optional[str] = None
_drag_src: Optional[str] = None  # "left" or "right"

left_col_container = None  # type: ignore
right_col_container = None  # type: ignore

def render_readouts_content(pid: str, src_col: str) -> None:
    """Inner content for the readouts + controls panel (no outer card)."""
    # place drag handle at top-right as unobtrusive overlay
    drag_handle(pid, src_col).style("position:absolute; right:16px; top:16px; opacity:0.8;")
    global incremental_jog_checkbox, joint_step_input, io_summary_label
    # Tools, Joints, Controls in three vertical columns
    with ui.row().classes("gap-8 items-start"):
        with ui.column().classes("gap-1 w-[8vw]"):
            ui.label("Tool positions").classes("text-sm")
            for key in ["X", "Y", "Z", "Rx", "Ry", "Rz"]:
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"{key}:").classes("text-xs text-[var(--ctk-muted)] w-6")
                    tool_labels[key] = ui.label("-").classes("text-4xl")
        with ui.column().classes("gap-1 w-[8vw]"):
            ui.label("Joint positions").classes("text-sm")
            joint_labels.clear()
            for i in range(6):
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"θ{i+1}:").classes("text-xs text-[var(--ctk-muted)] w-6")
                    joint_labels.append(ui.label("-").classes("text-4xl"))
        with ui.column().classes("gap-2"):
            ui.label("Controls").classes("text-sm")
            # Sliders
            ui.label("Jog velocity %").classes("text-xs text-[var(--ctk-muted)]")
            jog_speed_slider = ui.slider(min=1, max=100, value=jog_speed_value, step=1)
            jog_speed_slider.on_value_change(lambda e: set_jog_speed(jog_speed_slider.value))
            ui.label("Jog accel %").classes("text-xs text-[var(--ctk-muted)]")
            jog_accel_slider = ui.slider(min=1, max=100, value=jog_accel_value, step=1)
            jog_accel_slider.on_value_change(lambda e: set_jog_accel(jog_accel_slider.value))
            # Incremental and step
            with ui.row().classes("items-center gap-4 w-full"):
                incremental_jog_checkbox = ui.checkbox("Incremental jog", value=False)
                joint_step_input = ui.number(label="Step size (deg/mm)", value=joint_step_deg, min=0.1, max=100.0, step=0.1).classes("w-30")
                # IO summary (live-updated)
                global io_summary_label
                io_summary_label = ui.label(f"IO: {io_label}").classes("text-sm")
            # Buttons
            with ui.row().classes("gap-2"):
                ui.button("Enable", on_click=lambda: asyncio.create_task(send_enable())).props("color=positive")
                ui.button("Disable", on_click=lambda: asyncio.create_task(send_disable())).props("color=negative")
                ui.button("Home", on_click=lambda: asyncio.create_task(send_home())).props("color=primary")

def render_log_content(pid: str, src_col: str) -> None:
    """Inner content for the Response Log panel (no outer card)."""
    global response_log
    with ui.row().classes("items-center justify-between w-full"):
        ui.label("Response Log").classes("text-md font-medium")
        drag_handle(pid, src_col)
    response_log = ui.log(max_lines=500).classes("w-full").style("height: 190px")
    # replay buffered log lines so moves don't lose history
    for line in log_buffer:
        response_log.push(line)
    ui.button("Show received frame", on_click=lambda: asyncio.create_task(show_received_frame())).props("outline")

def render_jog_content(pid: str, src_col: str) -> None:
    """Inner content for the Jog panel (no outer card)."""
    global jog_mode_radio, joint_jog_section, cart_jog_section, jog_speed_text, frame_text, cart_speed_text
    with ui.row().classes("w-full items-center justify-between"):
        with ui.row().classes("items-center gap-4"):
            with ui.tabs() as jog_mode_tabs:
                ui.tab("Joint jog")
                ui.tab("Cartesian jog")
                jog_mode_tabs.value = "Joint jog"
            frame_radio = ui.toggle(options=["WRF", "TRF"], value="TRF").props("dense")
            frame_radio.on_value_change(lambda e: set_frame(frame_radio.value))
        drag_handle(pid, src_col)
    joint_jog_section = ui.column().classes("gap-2")
    with joint_jog_section:
        joint_names = ['J1', 'J2', 'J3', 'J4', 'J5', 'J6']

        def make_joint_row(idx: int, name: str):
            with ui.row().classes("items-center gap-2"):
                ui.label(name).classes("w-8")
                left = ui.image("/static/icons/button_arrow_1.webp").style("width:60px;height:60px;object-fit:contain;transform:rotate(270deg);cursor:pointer;")
                bar = ui.linear_progress(value=0.5).props("rounded").classes("w-[25rem]")
                right = ui.image("/static/icons/button_arrow_1.webp").style("width:60px;height:60px;object-fit:contain;transform:rotate(90deg);cursor:pointer;")
                joint_progress_bars.append(bar)
                left.on('mousedown', lambda e, i=idx: set_joint_pressed(i, 'neg', True))
                left.on('mouseup',   lambda e, i=idx: set_joint_pressed(i, 'neg', False))
                left.on('mouseleave',lambda e, i=idx: set_joint_pressed(i, 'neg', False))
                right.on('mousedown', lambda e, i=idx: set_joint_pressed(i, 'pos', True))
                right.on('mouseup',   lambda e, i=idx: set_joint_pressed(i, 'pos', False))
                right.on('mouseleave',lambda e, i=idx: set_joint_pressed(i, 'pos', False))

        joint_progress_bars.clear()
        for i, n in enumerate(joint_names):
            make_joint_row(i, n)

    cart_jog_section = ui.column().classes("gap-2")
    with cart_jog_section:
        def axis_image(src: str, axis: str, rotate_deg: int = 0):
            img = ui.image(src).style(f"width:72px;height:72px;object-fit:contain;cursor:pointer;transform:rotate({rotate_deg}deg);")
            img.on('mousedown', lambda e, a=axis: set_axis_pressed(a, True))
            img.on('mouseup',   lambda e, a=axis: set_axis_pressed(a, False))
            img.on('mouseleave',lambda e, a=axis: set_axis_pressed(a, False))

        with ui.row().classes("items-start gap-8"):
            with ui.element('div').style('display:grid; grid-template-columns:72px 72px 50px; gap:8px;'):
                ui.element('div').style('width:72px;height:72px')
                axis_image("/static/icons/cart_x_up.webp", "X+")
                ui.element('div').style('width:72px;height:72px')
                axis_image("/static/icons/cart_y_left.webp", "Y-")
                ui.element('div').style('width:72px;height:72px')
                axis_image("/static/icons/cart_y_right.webp", "Y+")
                ui.element('div').style('width:72px;height:72px')
                axis_image("/static/icons/cart_x_down.webp", "X-")
                ui.element('div').style('width:72px;height:72px')
            with ui.column().classes("gap-16"):
                axis_image("/static/icons/cart_z_up.webp", "Z+")
                axis_image("/static/icons/cart_z_down.webp", "Z-")

            with ui.column().classes("gap-16"):
                axis_image("/static/icons/RZ_PLUS.webp", "RZ+")
                axis_image("/static/icons/RZ_MINUS.webp", "RZ-")
            with ui.element('div').style('display:grid; grid-template-columns:60px 60px 60px; gap:8px;'):
                ui.element('div')
                axis_image("/static/icons/RX_PLUS.webp", "RX+")
                ui.element('div')
                axis_image("/static/icons/RY_PLUS.webp", "RY+")
                ui.element('div')
                axis_image("/static/icons/RY_MINUS.webp", "RY-")
                ui.element('div')
                axis_image("/static/icons/RX_MINUS.webp", "RX-")

    def update_jog_visibility() -> None:
        if jog_mode_tabs.value == "Joint jog":
            joint_jog_section.visible = True
            cart_jog_section.visible = False
        else:
            joint_jog_section.visible = False
            cart_jog_section.visible = True

    jog_mode_tabs.on_value_change(lambda e: update_jog_visibility())
    update_jog_visibility()

def render_editor_content(pid: str, src_col: str) -> None:
    """Inner content for the Program Editor panel (no outer card)."""
    global program_filename_input, program_textarea
    with ui.row():
        with ui.column().classes("w-[35vw]"):
            with ui.row().classes("items-center gap-2"):
                ui.label("Program:").classes("text-md font-medium")
                program_filename_input = ui.input(label="Filename", value="execute_script.txt").classes("text-sm font-small").style("width: 450px")
                ui.button("Open", on_click=open_file_picker).props("unelevated")
            program_textarea = ui.codemirror(
                value="",
                line_wrapping=True,
            ).classes("w-full").style("height: 340px")
            # Initialize CodeMirror theme based on CTk theme/system
            try:
                mode = ctk_get_theme()
                effective = "light" if mode == "light" else "dark"
                program_textarea.theme = "default" if effective == "light" else "oneDark"
            except Exception:
                program_textarea.theme = "oneDark"
            with ui.row().classes("gap-2"):
                ui.button("Start", on_click=lambda: asyncio.create_task(execute_program())).props("unelevated color=positive")
                ui.button("Stop", on_click=lambda: asyncio.create_task(stop_program())).props("unelevated color=negative")
                ui.button("Save", on_click=lambda: asyncio.create_task(save_program())).props("unelevated")
                def save_as():
                    async def do_save_as():
                        name = save_as_input.value.strip() or "program.txt"
                        await save_program(as_name=name)
                        save_as_dialog.close()
                    save_as_dialog = ui.dialog()
                    with save_as_dialog, ui.card():
                        ui.label("Save As")
                        save_as_input = ui.input(label="New filename", value=program_filename_input.value).classes("w-80")
                        with ui.row().classes("gap-2"):
                            ui.button("Cancel", on_click=save_as_dialog.close)
                            ui.button("Save", on_click=lambda: asyncio.create_task(do_save_as())).props("color=positive")
                    save_as_dialog.open()
                ui.button("Save as", on_click=save_as).props("unelevated")
        with ui.column().classes("w-[10vw]"):
            with ui.row().classes("items-center w-full justify-between gap-0"):
                prefill_toggle = ui.switch("Current Pose", value=True)
                drag_handle(pid, src_col)
            build_command_palette_table(prefill_toggle)

def drag_handle(pid: str, src_col: str, extra_classes: str = ""):
    """Create a draggable handle with a gray button look, inline or overlaid."""
    wrapper = ui.element('div').classes(f"drag-handle-btn cursor-grab inline-flex items-center justify-center {extra_classes}").props("draggable")
    wrapper.on('dragstart', lambda e, p=pid, s=src_col: on_dragstart(p, s))
    wrapper.on('dragend', lambda e: on_dragend())
    with wrapper:
        ui.icon("drag_indicator").classes("text-white opacity-90").style("font-size: 18px;")
    return wrapper

def draggable_card(title: str, pid: str, src_col: str, render_body_fn, card_classes: str = "") -> None:
    """Create a card whose drag handle is integrated into its header/body, not a separate row."""
    card = ui.card().classes(f"w-full relative {card_classes}")
    with card:
        render_body_fn(pid, src_col)

def on_dragstart(pid: str, src_col: str) -> None:
    global _drag_id, _drag_src
    _drag_id = pid
    _drag_src = src_col

def on_dragend() -> None:
    global _drag_id, _drag_src
    _drag_id = None
    _drag_src = None

def on_drop_to(dst_col: str, index: int) -> None:
    global current_layout, _drag_id, _drag_src
    if not _drag_id or not _drag_src:
        return
    # remove from source
    current_layout[_drag_src].remove(_drag_id)  # type: ignore[index]
    # clamp and insert
    index = max(0, min(index, len(current_layout[dst_col])))  # type: ignore[index]
    current_layout[dst_col].insert(index, _drag_id)  # type: ignore[index]
    save_layout(current_layout)
    render_move_columns()
    _drag_id = None
    _drag_src = None

def render_drop_spacer(parent, col_name: str, index: int):
    spacer = ui.element('div').classes('drop-spacer')
    spacer.on('dragover.prevent', lambda e, s=spacer: s.classes(add='active'))
    spacer.on('dragleave',       lambda e, s=spacer: s.classes(remove='active'))
    spacer.on('drop',            lambda e, c=col_name, i=index, s=spacer: (s.classes(remove='active'), on_drop_to(c, i)))
    with parent:
        spacer

def render_panel_contents(pid: str, src_col: str) -> None:
    # Use a draggable header handle and place content-only bodies inside.
    if pid == 'jog':
        draggable_card("Jog", pid, src_col, render_jog_content, "min-h-[500px]")
    elif pid == 'editor':
        draggable_card("Program editor", pid, src_col, render_editor_content, "min-h-[500px]")
    elif pid == 'readouts':
        draggable_card("Readouts & Controls", pid, src_col, render_readouts_content)
    elif pid == 'log':
        draggable_card("Response Log", pid, src_col, render_log_content)

def render_panel_wrapper(parent, pid: str, src_col: str):
    # Render the draggable card (dragging limited to header handle).
    with parent:
        render_panel_contents(pid, src_col)

def render_one_column(container, col_name: str):
    # column-level highlight
    container.on('dragover.prevent', lambda e, c=container: c.classes(add='highlight'))
    container.on('dragleave',        lambda e, c=container: c.classes(remove='highlight'))
    container.on('drop',             lambda e, n=col_name, c=container: (c.classes(remove='highlight'), on_drop_to(n, len(current_layout[n]))))
    # interleave spacers and panels
    items = current_layout[col_name]
    render_drop_spacer(container, col_name, 0)
    for i, pid in enumerate(items):
        render_panel_wrapper(container, pid, col_name)
        render_drop_spacer(container, col_name, i + 1)

def render_move_columns() -> None:
    left_col_container.clear()
    right_col_container.clear()
    render_one_column(left_col_container, 'left')
    render_one_column(right_col_container, 'right')

def compose_ui() -> None:
    # lightweight CSS for the drag handle "grey button" look
    ui.add_css("""
.drag-handle-btn {
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 8px;
  padding: 4px;
  transition: background .15s ease, border-color .15s ease, box-shadow .15s ease;
}
body.body--light .drag-handle-btn {
  background: rgba(0,0,0,0.06);
  border-color: rgba(0,0,0,0.12);
}
.drag-handle-btn:hover {
  background: rgba(255,255,255,0.16);
  border-color: rgba(255,255,255,0.24);
  box-shadow: 0 1px 2px rgba(0,0,0,0.3);
}
body.body--light .drag-handle-btn:hover {
  background: rgba(0,0,0,0.12);
  border-color: rgba(0,0,0,0.20);
}
.drag-handle-btn:active {
  cursor: grabbing;
}
""")
    build_header()
    global move_page, io_page, settings_page, calibrate_page, gripper_page

    # Move page (DnD layout)
    move_page = ui.column().classes("w-full")
    with move_page:
        with ui.row().classes("gap-4 items-start"):
            global left_col_container, right_col_container
            left_col_container  = ui.column().classes("droppable-col w-[48vw] gap-4 min-h-[100px]")
            right_col_container = ui.column().classes("droppable-col w-[48vw] gap-4 min-h-[100px]")
        render_move_columns()

    # I/O page (scaffold)
    io_page = ui.column().classes("w-full")
    with io_page:
        build_io_panel()

    # Settings page (scaffold)
    settings_page = ui.column().classes("w-full")
    with settings_page:
        build_settings_panel()

    # Calibrate page (scaffold)
    calibrate_page = ui.column().classes("w-full")
    with calibrate_page:
        build_calibrate_panel()

    # Gripper page (scaffold)
    gripper_page = ui.column().classes("w-full")
    with gripper_page:
        build_gripper_panel()

    # Initial visibility
    if move_page: move_page.visible = True
    if io_page: io_page.visible = False
    if settings_page: settings_page.visible = False
    if calibrate_page: calibrate_page.visible = False
    if gripper_page: gripper_page.visible = False

    # Ensure header nav reflects active tab initially
    set_active_page("Move")

    build_footer()

# Build UI (preserve original behavior: build at import, run in __main__)
ctk_apply_theme("system")
compose_ui()

# # Timers
ui.timer(interval=0.1, callback=jog_tick)        # joint jog press-and-hold tick
ui.timer(interval=0.1, callback=cart_jog_tick)   # cartesian jog press-and-hold tick
status_timer = ui.timer(interval=0.5, callback=lambda: asyncio.create_task(update_status_async()), active=False)   # status poll (gated)

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="PAROL6 NiceGUI Commander", port=getattr(config, "UI_PORT", 8080), reload=True)
