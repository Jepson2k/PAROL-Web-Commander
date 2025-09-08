from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
import time
from functools import partial
from typing import TypedDict, cast

from nicegui import app as ng_app
from nicegui import ui

from app.common.theme import get_theme
from app.constants import PAROL6_URDF_PATH, REPO_ROOT
from app.services.robot_client import client
from app.state import robot_state
from urdf_scene_nicegui import UrdfScene  # type: ignore
from parol6.protocol.types import Axis


class MoveLayout(TypedDict):
    left: list[str]
    right: list[str]


class MovePage:
    """Move tab page with drag-and-drop layout."""

    def __init__(self) -> None:
        # UI refs for status polling
        self.tool_labels: dict[str, ui.label] = {}  # keys: "X","Y","Z","Rx","Ry","Rz"
        self.joint_labels: list[ui.label] = []  # 6 label refs for q1..q6
        self.joint_progress_bars: list[
            ui.linear_progress
        ] = []  # progress bars for q1..q6

        # Readouts card IO summary (cross-reference with IoPage for consistency)
        self.io_summary_label: ui.label | None = None

        # Response log
        self.response_log: ui.log | None = None

        # Control widgets referenced by jog logic
        self.incremental_jog_checkbox: ui.switch | None = None
        self.joint_step_input: ui.number | None = None

        # Program editor widgets
        self.program_filename_input: ui.input | None = None
        self.program_textarea: ui.codemirror | None = None

        # Program execution
        self.program_task: asyncio.Task | None = None
        self.program_cancel_event: asyncio.Event | None = None
        self.program_speed_percentage: int | None = None  # set by JointVelSet alias

        # URDF viewer state
        self.urdf_scene = None  # UrdfScene instance
        self.urdf_auto_sync: bool = True
        self.urdf_joint_names: list[str] | None = None
        self.urdf_index_mapping: list[int] = [0, 1, 2, 3, 4, 5]  # J1..J6 -> L1..L6
        self.urdf_config: dict = {
            "urdf_path": str(PAROL6_URDF_PATH),
            "scale_stls": 1.0,
            "material": "#888",
            "background_color": "#eee",
            "auto_sync": True,
            "joint_name_order": ["L1", "L2", "L3", "L4", "L5", "L6"],
            "deg_to_rad": True,
            "angle_signs": [
                1,
                1,
                -1,
                -1,
                -1,
                -1,
            ],  # Sign correction for joint directions
            "angle_offsets": [
                0,
                90,
                180,
                0,
                0,
                180,
            ],  # Zero-reference offsets (degrees) for each joint
        }

        # Drag-and-drop layout
        self.DEFAULT_LAYOUT: MoveLayout = {
            "left": ["jog", "readouts"],
            "right": ["urdf", "editor", "log"],
        }
        self.current_layout = self.DEFAULT_LAYOUT.copy()
        self._drag_id: str | None = None
        self._drag_src: str | None = None

        # Layout containers
        self.left_col_container: ui.column | None = None
        self.right_col_container: ui.column | None = None

        # Program directory
        self.PROGRAM_DIR = (
            REPO_ROOT / "PAROL-commander-software" / "GUI" / "files" / "Programs"
        )
        if not self.PROGRAM_DIR.exists():
            self.PROGRAM_DIR = REPO_ROOT / "programs"
            self.PROGRAM_DIR.mkdir(parents=True, exist_ok=True)

        # Jog UI references and scheduling state
        self._joint_left_imgs: dict[int, ui.image] = {}
        self._joint_right_imgs: dict[int, ui.image] = {}
        self._cart_axis_imgs: dict[str, ui.image] = {}
        self._last_joint_sig: tuple[int, str, int] | None = None
        self._last_cart_sig: tuple[str, int, str] | None = None
        self._tick_stats = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
        self._tick_stats_cart = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}

        # Jog cadence constants (100 Hz)
        self.JOG_TICK_S: float = 0.01
        self.CADENCE_WARN_WINDOW: int = 100
        self.CADENCE_TOLERANCE: float = 0.002
        # Streaming watchdog timeout to use as "duration" while stream_mode is ON
        self.STREAM_TIMEOUT_S: float = 0.1

    # ---- Jog helpers ----

    def _apply_pressed_style(self, widget: ui.image | None, pressed: bool) -> None:
        if not widget:
            return
        if pressed:
            widget.classes(add="is-pressed")
        else:
            widget.classes(remove="is-pressed")

    def _cadence_tick(self, now: float, stats: dict, label: str) -> None:
        last = stats.get("last_ts", 0.0)
        if last > 0.0:
            dt = now - last
            stats["accum"] = stats.get("accum", 0.0) + dt
            stats["count"] = stats.get("count", 0.0) + 1.0
            if stats["count"] >= self.CADENCE_WARN_WINDOW:
                avg = stats["accum"] / stats["count"]
                if abs(avg - self.JOG_TICK_S) > self.CADENCE_TOLERANCE:
                    logging.warning(
                        "[CADENCE] %s avg dt=%.4f s (target=%.4f s, tol=%.4f s)",
                        label,
                        avg,
                        self.JOG_TICK_S,
                        self.CADENCE_TOLERANCE,
                    )
                stats["accum"] = 0.0
                stats["count"] = 0.0
        stats["last_ts"] = now

    def _get_first_pressed_joint(self) -> tuple[int, str] | None:
        """Return (index, 'pos'|'neg') for the first pressed joint, else None."""
        pos = ng_app.storage.client.get("jog_pressed_pos", [False] * 6)
        neg = ng_app.storage.client.get("jog_pressed_neg", [False] * 6)
        if isinstance(pos, list) and isinstance(neg, list):
            for j in range(min(6, len(pos), len(neg))):
                if pos[j]:
                    return (j, "pos")
                if neg[j]:
                    return (j, "neg")
        return None

    def _get_first_pressed_axis(self) -> str | None:
        """Return the first pressed cartesian axis key like 'X+' if any."""
        axes_any = ng_app.storage.client.get("cart_pressed_axes", {})
        if isinstance(axes_any, dict):
            for k, pressed in axes_any.items():
                if bool(pressed):
                    return str(k)
        return None

    async def set_joint_pressed(self, j: int, direction: str, is_pressed: bool) -> None:
        """Press-and-hold jog; if incremental mode is ON, fire one-shot step."""
        storage = ng_app.storage.client
        storage.setdefault("jog_pressed_pos", [False] * 6)
        storage.setdefault("jog_pressed_neg", [False] * 6)
        storage.setdefault("incremental_jog", False)
        storage.setdefault("jog_speed", 50)
        storage.setdefault("joint_step_deg", 1.0)

        if direction == "pos":
            self._apply_pressed_style(self._joint_right_imgs.get(j), bool(is_pressed))
        else:
            self._apply_pressed_style(self._joint_left_imgs.get(j), bool(is_pressed))

        if 0 <= j < 6:
            if storage.get("incremental_jog", False) and is_pressed:
                speed = max(1, min(100, int(storage.get("jog_speed", 50))))
                step = abs(float(storage.get("joint_step_deg", 1.0)))
                index = j if direction == "pos" else (j + 6)
                await client.jog_joint(
                    index, speed_percentage=speed, duration=None, distance_deg=step
                )
                return

            pos_pressed = storage.get("jog_pressed_pos", [False] * 6)
            neg_pressed = storage.get("jog_pressed_neg", [False] * 6)
            if (
                direction == "pos"
                and isinstance(pos_pressed, list)
                and len(pos_pressed) == 6
            ):
                pos_pressed[j] = bool(is_pressed)
                storage["jog_pressed_pos"] = pos_pressed
            elif (
                direction == "neg"
                and isinstance(neg_pressed, list)
                and len(neg_pressed) == 6
            ):
                neg_pressed[j] = bool(is_pressed)
                storage["jog_pressed_neg"] = neg_pressed

            # Toggle per-client joint jog timer based on any pressed joint
            t = storage.get("joint_jog_timer")
            any_pressed = any(storage.get("jog_pressed_pos", [False] * 6)) or any(
                storage.get("jog_pressed_neg", [False] * 6)
            )
            if t:
                t.active = bool(any_pressed)

            if any_pressed:
                await client.stream_on()
            else:
                await client.stream_off()

    def set_jog_speed(self, v) -> None:
        ng_app.storage.client["jog_speed"] = max(1, min(100, int(v)))

    def set_jog_accel(self, v) -> None:
        ng_app.storage.client["jog_accel"] = max(1, min(100, int(v)))

    async def jog_tick(self) -> None:
        """100 Hz: send/update joint streaming jog if any button is pressed."""
        speed = max(1, min(100, int(ng_app.storage.client.get("jog_speed", 50))))
        intent = self._get_first_pressed_joint()
        if intent is not None:
            j, d = intent
            idx = j if d == "pos" else (j + 6)
            await client.jog_joint(
                idx, speed_percentage=speed, duration=self.STREAM_TIMEOUT_S
            )
        # cadence monitor
        self._cadence_tick(time.time(), self._tick_stats, "joint")

    # ---- Cartesian jog helpers ----

    async def set_axis_pressed(self, axis: str, is_pressed: bool) -> None:
        storage = ng_app.storage.client
        storage.setdefault(
            "cart_pressed_axes",
            {
                "X+": False,
                "X-": False,
                "Y+": False,
                "Y-": False,
                "Z+": False,
                "Z-": False,
                "RX+": False,
                "RX-": False,
                "RY+": False,
                "RY-": False,
                "RZ+": False,
                "RZ-": False,
            },
        )
        storage.setdefault("incremental_jog", False)
        storage.setdefault("jog_speed", 50)
        storage.setdefault("joint_step_deg", 1.0)
        storage.setdefault("frame", "TRF")

        self._apply_pressed_style(self._cart_axis_imgs.get(axis), bool(is_pressed))
        axes = storage.get("cart_pressed_axes", {})
        if isinstance(axes, dict) and axis in axes:
            if storage.get("incremental_jog", False) and is_pressed:
                speed = max(1, min(100, int(storage.get("jog_speed", 50))))
                step = max(0.1, min(100.0, float(storage.get("joint_step_deg", 1.0))))
                duration = max(0.02, min(0.5, step / 50.0))
                frame = storage.get("frame", "TRF")
                await client.jog_cartesian(frame, cast(Axis, axis), speed, duration)
                return
            axes[axis] = bool(is_pressed)
            storage["cart_pressed_axes"] = axes

            # Toggle per-client cartesian jog timer based on any pressed axis
            t = storage.get("cart_jog_timer")
            axes_now = storage.get("cart_pressed_axes", {})
            any_pressed = (
                any(bool(v) for v in axes_now.values())
                if isinstance(axes_now, dict)
                else False
            )
            if t:
                t.active = bool(any_pressed)

            if any_pressed:
                await client.stream_on()
            else:
                await client.stream_off()

    def set_frame(self, frame: str) -> None:
        storage = ng_app.storage.client
        if frame in ("TRF", "WRF"):
            storage["frame"] = frame

    async def cart_jog_tick(self) -> None:
        """100 Hz: send/update cartesian streaming jog if any axis is pressed."""
        storage = ng_app.storage.client
        speed = max(1, min(100, int(storage.get("jog_speed", 50))))
        frame = storage.get("frame", "TRF")
        axis = self._get_first_pressed_axis()
        if axis is not None:
            await client.jog_cartesian(frame, cast(Axis, axis), speed, self.STREAM_TIMEOUT_S)
        # cadence monitor
        self._cadence_tick(time.time(), self._tick_stats_cart, "cart")

    # ---- Robot actions ----

    async def send_enable(self) -> None:
        try:
            resp = await client.enable()
            ui.notify(resp, color="positive")
            logging.info(resp)
        except Exception as e:
            logging.error("ENABLE failed: %s", e)

    async def send_disable(self) -> None:
        try:
            resp = await client.disable()
            ui.notify(resp, color="warning")
            logging.warning(resp)
        except Exception as e:
            logging.error("DISABLE failed: %s", e)

    async def send_home(self) -> None:
        try:
            resp = await client.home()
            ui.notify(resp, color="primary")
            logging.info(resp)
        except Exception as e:
            logging.error("HOME failed: %s", e)

    async def show_received_frame(self) -> None:
        """Show raw GET_STATUS frame in the log if available."""
        try:
            # best-effort access to raw response
            raw = None
            if hasattr(client, "_request"):
                raw = await client._request("GET_STATUS", bufsize=4096)
            if raw:
                logging.info("[FRAME] %s", raw)
            else:
                logging.warning("No frame received (GET_STATUS unsupported)")
        except Exception as e:
            logging.error("GET_STATUS raw failed: %s", e)

    # ---- Program helpers ----

    def _get_opt(self, tokens: list[str], key: str) -> float | None:
        key = key.upper()
        for t in tokens:
            if t.upper().startswith(f"{key}="):
                return float(t.split("=", 1)[1])
        return None

    def _parse_csv_floats(self, s: str) -> list[float] | None:
        return [float(x.strip()) for x in s.split(",") if x.strip() != ""]

    def _parse_motion_args(self, argstr: str) -> tuple[list[float], dict, list[str]]:
        """Parse motion function arguments like "j1,j2,j3,j4,j5,j6, v=50, a=30, t=2.5, trap, speed"""
        tokens_raw = [t.strip() for t in (argstr or "").split(",") if t.strip() != ""]
        values: list[float] = []
        errors: list[str] = []
        opts: dict[str, float | str | None] = {
            "v": None,
            "a": None,
            "t": None,
            "profile": None,
            "tracking": None,
        }

        # Collect exactly 6 leading numeric values
        idx = 0
        while idx < len(tokens_raw) and len(values) < 6:
            tkn = tokens_raw[idx]
            try:
                num = float(tkn)
                values.append(num)
            except Exception:
                errors.append(
                    f"Expected numeric value #{len(values) + 1} but got '{tkn}'"
                )
                break
            idx += 1

        if not errors and len(values) != 6:
            errors.append(f"Expected 6 numeric values, got {len(values)}")

        # Parse options after the 6 numerics
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
            elif low.startswith("a="):
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
            elif low.startswith("t="):
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
            elif low in ("trap", "poly"):
                if "profile" in seen:
                    errors.append("Duplicate profile token")
                    break
                seen.add("profile")
                opts["profile"] = low
            elif low == "speed":
                if "tracking" in seen:
                    errors.append("Duplicate tracking token")
                    break
                seen.add("tracking")
                opts["tracking"] = "SPEED"
            else:
                errors.append(f"Unknown token '{tkn}' (options must be after 6 values)")
                break
            idx += 1

        return values, opts, errors

    # ---- Program execution ----

    async def load_program(self, filename: str | None = None) -> None:
        try:
            name = (
                filename
                or (
                    self.program_filename_input.value
                    if self.program_filename_input
                    else ""
                )
                or ""
            )
            text = (self.PROGRAM_DIR / name).read_text(encoding="utf-8")
            if self.program_textarea:
                self.program_textarea.value = text
            ui.notify(f"Loaded {name}", color="primary")
            logging.info("Loaded program %s", name)
        except Exception as e:
            ui.notify(f"Load failed: {e}", color="negative")
            logging.error("Load failed: %s", e)

    async def save_program(self, as_name: str | None = None) -> None:
        try:
            name = (
                as_name
                or (
                    self.program_filename_input.value
                    if self.program_filename_input
                    else ""
                )
                or ""
            )
            content = self.program_textarea.value if self.program_textarea else ""
            (self.PROGRAM_DIR / name).write_text(content, encoding="utf-8")
            ui.notify(f"Saved {name}", color="positive")
            logging.info("Saved program %s", name)
            if as_name and self.program_filename_input:
                self.program_filename_input.value = as_name
        except Exception as e:
            ui.notify(f"Save failed: {e}", color="negative")
            logging.error("Save failed: %s", e)

    # Program regex patterns
    _re_delay = re.compile(r"^\s*Delay\(\s*([0-9]*\.?[0-9]+)\s*\)\s*$", re.IGNORECASE)
    _re_joint_vel = re.compile(r"^\s*JointVelSet\(\s*([0-9]+)\s*\)\s*$", re.IGNORECASE)
    _re_joint_move = re.compile(r"^\s*JointMove\(\s*([^\)]*)\)\s*$", re.IGNORECASE)
    _re_pose_move = re.compile(r"^\s*PoseMove\(\s*([^\)]*)\)\s*$", re.IGNORECASE)
    # Legacy CTk command regex
    _re_move_joint_legacy = re.compile(
        r"^\s*MoveJoint\(\s*([^\)]*)\)\s*$", re.IGNORECASE
    )
    _re_move_pose_legacy = re.compile(r"^\s*MovePose\(\s*([^\)]*)\)\s*$", re.IGNORECASE)
    _re_move_cart_legacy = re.compile(r"^\s*MoveCart\(\s*([^\)]*)\)\s*$", re.IGNORECASE)
    _re_move_cart_rel_trf = re.compile(
        r"^\s*MoveCartRelTRF\(\s*([^\)]*)\)\s*$", re.IGNORECASE
    )
    _re_speed_joint = re.compile(r"^\s*SpeedJoint\(\s*([0-9]*)\s*\)\s*$", re.IGNORECASE)
    _re_speed_cart = re.compile(r"^\s*SpeedCart\(\s*([0-9]*)\s*\)\s*$", re.IGNORECASE)
    # Legacy function-style helpers
    _re_home_fn = re.compile(r"^\s*Home\(\s*\)\s*$", re.IGNORECASE)
    _re_begin_fn = re.compile(r"^\s*Begin\(\s*\)\s*$", re.IGNORECASE)
    _re_end_fn = re.compile(r"^\s*End\(\s*\)\s*$", re.IGNORECASE)
    _re_loop_fn = re.compile(r"^\s*Loop\(\s*\)\s*$", re.IGNORECASE)
    _re_output_fn = re.compile(
        r"^\s*Output\(\s*(\d+)\s*,\s*(HIGH|LOW)\s*\)\s*$", re.IGNORECASE
    )
    _re_gripper_fn = re.compile(
        r"^\s*Gripper\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*$", re.IGNORECASE
    )
    _re_gripper_cal_fn = re.compile(r"^\s*Gripper_cal\(\s*\)\s*$", re.IGNORECASE)

    async def _run_program(self) -> None:
        self.program_speed_percentage = None  # reset each run
        lines = (
            (self.program_textarea.value or "").splitlines()
            if self.program_textarea
            else []
        )

        for raw in lines:
            if self.program_cancel_event and self.program_cancel_event.is_set():
                ui.notify("Program stopped", color="warning")
                logging.warning("Program stopped")
                return

            line = raw.strip()
            if not line or line.startswith(("#", "//", ";")):
                continue

            # Function-style legacy commands
            m = self._re_home_fn.match(line)
            if m:
                try:
                    await client.home()
                    logging.info("Home()")
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logging.error("Home() failed: %s", e)
                continue

            # Handle alias forms
            m = self._re_delay.match(line)
            if m:
                try:
                    sec = float(m.group(1))
                    logging.info("Delay(%s)", sec)
                    await asyncio.sleep(sec)
                except Exception as e:
                    ui.notify(f"Program error at '{line}': {e}", color="negative")
                    logging.error("Program error at '%s': %s", line, e)
                    return
                continue

            m = self._re_joint_vel.match(line)
            if m:
                try:
                    self.program_speed_percentage = int(m.group(1))
                    logging.info("JointVelSet(%s)", self.program_speed_percentage)
                except Exception as e:
                    ui.notify(f"Program error at '{line}': {e}", color="negative")
                    logging.error("Program error at '%s': %s", line, e)
                    return
                continue

            # Legacy commands remain supported
            tokens = line.split()
            if not tokens:
                continue
            cmd = tokens[0].upper()
            try:
                if cmd == "HOME":
                    await client.home()
                    logging.info("HOME")
                    await asyncio.sleep(0.1)
                elif cmd == "DELAY" and len(tokens) >= 2:
                    sec = float(tokens[1])
                    logging.info("DELAY %s", sec)
                    await asyncio.sleep(sec)
                elif cmd == "ENABLE":
                    await client.enable()
                    logging.info("ENABLE")
                    await asyncio.sleep(0.05)
                elif cmd == "DISABLE":
                    await client.disable()
                    logging.info("DISABLE")
                    await asyncio.sleep(0.05)
                elif cmd == "CLEAR_ERROR":
                    await client.clear_error()
                    logging.info("CLEAR_ERROR")
                    await asyncio.sleep(0.05)
                elif cmd == "STOP":
                    await client.stop()
                    logging.warning("STOP")
                    await asyncio.sleep(0.05)
                else:
                    ui.notify(f"Unknown command: {line}", color="warning")
                    logging.warning("Unknown command: %s", line)
                    await asyncio.sleep(0.01)
            except Exception as e:
                ui.notify(f"Program error at '{line}': {e}", color="negative")
                logging.error("Program error at '%s': %s", line, e)
                return

        ui.notify("Program finished", color="positive")
        logging.info("Program finished")

    async def execute_program(self) -> None:
        if self.program_task and not self.program_task.done():
            ui.notify("Program already running", color="warning")
            return
        self.program_cancel_event = asyncio.Event()
        self.program_task = asyncio.create_task(self._run_program())

    async def stop_program(self) -> None:
        if self.program_cancel_event:
            self.program_cancel_event.set()
        if self.program_task:
            await asyncio.wait_for(self.program_task, timeout=0.1)

    # ---- Layout management ----

    def _valid_layout(self, data) -> bool:
        return (
            isinstance(data, dict)
            and isinstance(data.get("left"), list)
            and isinstance(data.get("right"), list)
        )

    def _load_layout(self) -> MoveLayout:
        data = ng_app.storage.user.get("move_layout")
        if self._valid_layout(data):
            return data  # type: ignore[return-value]
        layout = self.DEFAULT_LAYOUT.copy()
        ng_app.storage.user["move_layout"] = layout
        return layout

    def _save_layout(self, layout: MoveLayout) -> None:
        try:
            ng_app.storage.user["move_layout"] = layout
        except Exception as e:
            logging.error("Failed to persist layout to user storage: %s", e)

    def on_dragstart(self, pid: str, src_col: str) -> None:
        self._drag_id = pid
        self._drag_src = src_col

    def on_dragend(self) -> None:
        # Avoid clearing here to prevent race with drop; on_drop_to resets state
        return

    def on_drop_to(self, dst_col: str, index: int) -> None:
        if not self._drag_id or not self._drag_src:
            return

        # Determine source and destination lists
        src_list = (
            self.current_layout["left"]
            if self._drag_src == "left"
            else self.current_layout["right"]
        )
        dst_list = (
            self.current_layout["left"]
            if dst_col == "left"
            else self.current_layout["right"]
        )

        # Compute original source index (if present)
        try:
            src_index = src_list.index(self._drag_id)
        except ValueError:
            src_index = None

        # Adjust target index when moving within the same column and dropping after original
        if self._drag_src == dst_col and src_index is not None and index > src_index:
            index -= 1

        # Remove from source and insert into destination at clamped index
        with contextlib.suppress(Exception):
            src_list.remove(self._drag_id)

        index = max(0, min(index, len(dst_list)))
        dst_list.insert(index, self._drag_id)

        self._save_layout(self.current_layout)
        self.render_move_columns()
        self._drag_id = None
        self._drag_src = None

    # ---- URDF viewer methods ----

    def render_urdf_content(self, pid: str, src_col: str) -> None:
        """Inner content for the URDF Viewer panel"""
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row():
                ui.label("URDF Viewer")
                # Sync toggle
                sync_switch = ui.switch("Auto Sync", value=self.urdf_auto_sync).classes(
                    "p-0"
                )

                def update_sync():
                    self.urdf_auto_sync = bool(sync_switch.value)
                    self.urdf_config["auto_sync"] = self.urdf_auto_sync
                    logging.info("URDF auto sync: %s", self.urdf_auto_sync)

                sync_switch.on_value_change(lambda e: update_sync())
            self.drag_handle(pid, src_col)

        # Initialize URDF scene
        async def init_scene():
            try:
                await self._initialize_urdf_scene()
                logging.info("URDF scene initialized")
            except Exception as e:
                logging.error("URDF initialization failed: %s", e)

        # Use timer to delay initialization until UI is ready
        ui.timer(0.1, init_scene, once=True)

    async def _initialize_urdf_scene(self, container=None) -> None:
        """Initialize the URDF scene with error handling."""
        # Check if URDF file exists
        if not PAROL6_URDF_PATH.exists():
            raise FileNotFoundError(f"URDF file not found: {PAROL6_URDF_PATH}")

        # Clear existing scene
        if container:
            container.clear()

        # Create a temporary URDF file with continuous joints changed to revolute
        # This is needed because urdf_scene_nicegui doesn't support continuous joints
        with open(PAROL6_URDF_PATH) as f:
            urdf_content = f.read()

        # Replace continuous joint type with revolute (L6 joint has limits so it's effectively revolute)
        urdf_content = urdf_content.replace('type="continuous"', 'type="revolute"')

        # Create temporary file in same directory to preserve mesh path resolution
        import os

        temp_urdf_path = PAROL6_URDF_PATH.parent / "PAROL6_temp.urdf"

        try:
            with open(temp_urdf_path, "w") as tmp_file:
                tmp_file.write(urdf_content)

            # Detect theme and set appropriate colors
            mode = get_theme()
            is_dark = mode != "light"
            bg_color = "#212121" if is_dark else "#eeeeee"
            material_color = "#9ca3af" if is_dark else "#666666"

            # Update config with theme-aware colors
            self.urdf_config["background_color"] = bg_color
            self.urdf_config["material"] = material_color

            # Create new scene with current config
            self.urdf_scene = UrdfScene(str(temp_urdf_path))
            assert self.urdf_scene is not None
            # Show method is not async and takes display parameters
            self.urdf_scene.show(
                scale_stls=self.urdf_config.get("scale_stls", 1.0),
                material=self.urdf_config.get("material"),
                background_color=self.urdf_config.get("background_color", "#eee"),
            )

            # Override the scene height and set closer camera position
            if self.urdf_scene.scene:
                scene: ui.scene = self.urdf_scene.scene
                scene._props["grid"] = (10, 100)
                # Remove the viewport-based height and set fixed height
                scene.classes(remove="h-[66vh]").style("height: 375px")
                # Set camera closer to the robot arm with proper look-at positioning
                scene.move_camera(
                    x=0.3,
                    y=0.3,
                    z=0.22,  # Camera position
                    look_at_z=0.22,
                    duration=0.0,  # Instant movement
                )

                # Add large world coordinate frame at origin (fixed)
                world_axes_size = 0.30
                scene.line([0, 0, 0], [world_axes_size, 0, 0]).material(
                    "#ff0000"
                )  # X-axis red
                scene.line([0, 0, 0], [0, world_axes_size, 0]).material(
                    "#00ff00"
                )  # Y-axis green
                scene.line([0, 0, 0], [0, 0, world_axes_size]).material(
                    "#0000ff"
                )  # Z-axis blue

                # Add large end-of-arm coordinate frame
                if self.urdf_scene.joint_names:
                    eef_joint = self.urdf_scene.joint_names[-1]  # Last actuated joint
                    eef_group = self.urdf_scene.joint_groups.get(eef_joint)
                    if eef_group:
                        eef_axes_size = 0.15
                        with eef_group:
                            scene.line([0, 0, 0], [eef_axes_size, 0, 0]).material(
                                "#ff0000"
                            )  # X-axis red
                            scene.line([0, 0, 0], [0, eef_axes_size, 0]).material(
                                "#00ff00"
                            )  # Y-axis green
                            scene.line([0, 0, 0], [0, 0, eef_axes_size]).material(
                                "#0000ff"
                            )  # Z-axis blue

        finally:
            # Clean up temporary file
            if temp_urdf_path.exists():
                os.unlink(temp_urdf_path)

        # Cache joint names for mapping
        if hasattr(self.urdf_scene, "get_joint_names"):
            self.urdf_joint_names = list(self.urdf_scene.get_joint_names())
        else:
            # Fallback to expected joint names
            self.urdf_joint_names = self.urdf_config.get(
                "joint_name_order", ["L1", "L2", "L3", "L4", "L5", "L6"]
            )

        logging.info("URDF scene initialized with joints: %s", self.urdf_joint_names)

    def update_urdf_angles(self, angles_deg: list[float]) -> None:
        """Update URDF scene with new joint angles (degrees -> radians)."""
        if not self.urdf_scene or not self.urdf_auto_sync:
            return

        if not angles_deg or len(angles_deg) < 6:
            return

        try:
            # Validate that all angles are numeric (not strings like "-")
            valid_angles = []
            for angle in angles_deg[:6]:  # Take only first 6 angles
                if isinstance(angle, int | float) and not isinstance(angle, bool):
                    if math.isfinite(angle):
                        valid_angles.append(float(angle))
                    else:
                        return  # Skip update for NaN or infinite values
                else:
                    return  # Skip update for any non-numeric data

            if len(valid_angles) != 6:
                return  # Need all 6 angles

            # Convert degrees to radians, apply sign correction and index mapping
            angles_rad = []
            angle_signs = self.urdf_config.get("angle_signs", [1, 1, 1, 1, 1, 1])
            for i in range(6):
                if i < len(self.urdf_index_mapping) and self.urdf_index_mapping[
                    i
                ] < len(valid_angles):
                    controller_idx = self.urdf_index_mapping[i]
                    angle_deg = valid_angles[controller_idx]
                    # Apply sign correction and offset
                    sign = (
                        1
                        if controller_idx >= len(angle_signs)
                        else (1 if angle_signs[controller_idx] >= 0 else -1)
                    )
                    angle_offsets = self.urdf_config.get(
                        "angle_offsets", [0, 0, 0, 0, 0, 0]
                    )
                    offset = (
                        angle_offsets[controller_idx]
                        if controller_idx < len(angle_offsets)
                        else 0
                    )
                    angle_deg_corrected = angle_deg * sign + offset
                    angle_rad = (
                        math.radians(angle_deg_corrected)
                        if self.urdf_config.get("deg_to_rad", True)
                        else angle_deg_corrected
                    )
                    angles_rad.append(angle_rad)
                else:
                    angles_rad.append(0.0)

            # Create ordered list of angles based on URDF joint names
            # The set_axis_values method expects a list, not a dictionary!
            if hasattr(self.urdf_scene, "set_axis_values") and hasattr(
                self.urdf_scene, "joint_names"
            ):
                urdf_joint_names = list(self.urdf_scene.joint_names)
                angles_ordered = []

                for joint_name in urdf_joint_names:
                    # Map URDF joint name back to our controller index
                    try:
                        urdf_idx = self.urdf_config["joint_name_order"].index(
                            joint_name
                        )
                        if urdf_idx < len(angles_rad):
                            angles_ordered.append(angles_rad[urdf_idx])
                        else:
                            angles_ordered.append(0.0)
                    except (ValueError, KeyError):
                        angles_ordered.append(0.0)

                # Pass list of float values in the order expected by the library
                self.urdf_scene.set_axis_values(angles_ordered)

        except Exception as e:
            logging.error("Failed to update URDF angles: %s", e)

    # ---- File operations ----

    def open_file_picker(self) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label("Open Program from disk")

            def _on_upload(e):
                try:
                    data = e.content.read()
                    name = getattr(e, "name", None) or "uploaded_program.txt"
                    (self.PROGRAM_DIR / name).write_bytes(data)
                    if self.program_filename_input:
                        self.program_filename_input.value = name
                    if self.program_textarea:
                        self.program_textarea.value = data.decode(
                            "utf-8", errors="ignore"
                        )
                    ui.notify(f"Loaded {name}", color="primary")
                except Exception as ex:
                    ui.notify(f"Open failed: {ex}", color="negative")
                finally:
                    dlg.close()

            ui.upload(on_upload=_on_upload).props("accept=.txt,.prog,.gcode,*/*")
            with ui.row().classes("gap-2"):
                ui.button("Cancel", on_click=dlg.close)
        dlg.open()

    # ---- UI render methods ----

    def drag_handle(self, pid: str, src_col: str, extra_classes: str = ""):
        """Create a draggable handle with a gray button look, inline or overlaid."""
        wrapper = (
            ui.element("div")
            .classes(
                f"drag-handle-btn cursor-grab inline-flex items-center justify-center {extra_classes}"
            )
            .props("draggable")
        )
        wrapper.on("dragstart", lambda e, p=pid, s=src_col: self.on_dragstart(p, s))
        wrapper.on("dragend", lambda e: self.on_dragend())
        with wrapper:
            ui.icon("drag_indicator").classes("text-white opacity-90").style(
                "font-size: 18px;"
            )
        return wrapper

    def render_readouts_content(self, pid: str, src_col: str) -> None:
        """Inner content for the readouts + controls panel (no outer card)."""
        # place drag handle at top-right as unobtrusive overlay
        self.drag_handle(pid, src_col).style(
            "position:absolute; right:16px; top:16px; opacity:0.8;"
        )

        # Tools, Joints, Controls in responsive columns
        with ui.element("div").classes("readouts-row"):
            with ui.column().classes("readouts-col"):
                ui.label("Tool positions").classes("text-sm")
                for key in ["X", "Y", "Z", "Rx", "Ry", "Rz"]:
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"{key}:").classes(
                            "text-xs text-[var(--ctk-muted)] w-6"
                        )
                        self.tool_labels[key] = ui.label("-").classes("text-4xl")
            with ui.column().classes("readouts-col"):
                ui.label("Joint positions").classes("text-sm")
                self.joint_labels.clear()
                for i in range(6):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"θ{i + 1}:").classes(
                            "text-xs text-[var(--ctk-muted)] w-6"
                        )
                        self.joint_labels.append(ui.label("-").classes("text-4xl"))
            with ui.column().classes("readouts-controls"):
                ui.label("Controls").classes("text-sm")
                # Sliders
                ui.label("Jog velocity %").classes("text-xs text-[var(--ctk-muted)]")
                jog_speed_slider = (
                    ui.slider(
                        min=1,
                        max=100,
                        value=ng_app.storage.client.get("jog_speed", 50),
                        step=1,
                    )
                    .classes("w-full")
                    .style("width: 100%")
                )
                jog_speed_slider.on_value_change(
                    lambda e: self.set_jog_speed(jog_speed_slider.value)
                )
                ui.label("Jog accel %").classes("text-xs text-[var(--ctk-muted)]")
                jog_accel_slider = (
                    ui.slider(
                        min=1,
                        max=100,
                        value=ng_app.storage.client.get("jog_accel", 50),
                        step=1,
                    )
                    .classes("w-full")
                    .style("width: 100%")
                )
                jog_accel_slider.on_value_change(
                    lambda e: self.set_jog_accel(jog_accel_slider.value)
                )
                # Incremental and step
                with ui.row().classes("items-center gap-4 w-full"):
                    self.incremental_jog_checkbox = ui.switch(
                        "Incremental jog",
                        value=ng_app.storage.client.get("incremental_jog", False),
                    )

                    def update_incremental():
                        if self.incremental_jog_checkbox:
                            ng_app.storage.client["incremental_jog"] = bool(
                                self.incremental_jog_checkbox.value
                            )

                    self.incremental_jog_checkbox.on_value_change(
                        lambda e: update_incremental()
                    )
                    self.joint_step_input = ui.number(
                        label="Step size (deg/mm)",
                        value=ng_app.storage.client.get("joint_step_deg", 1.0),
                        min=0.1,
                        max=100.0,
                        step=0.1,
                    ).style("width: 120px")

                    def update_step_size():
                        if (
                            self.joint_step_input
                            and self.joint_step_input.value is not None
                        ):
                            val = max(
                                0.1, min(100.0, float(self.joint_step_input.value))
                            )
                            ng_app.storage.client["joint_step_deg"] = val

                    self.joint_step_input.on_value_change(lambda e: update_step_size())
                    # IO summary (live-updated)
                    self.io_summary_label = ui.label("IO: -").classes("text-sm")
                # Buttons
                with ui.row().classes("gap-2 w-full"):
                    ui.button("Enable", on_click=self.send_enable).props(
                        "color=positive"
                    )
                    ui.button("Disable", on_click=self.send_disable).props(
                        "color=negative"
                    )
                    ui.button("Home", on_click=self.send_home).props("color=primary")

    def render_log_content(self, pid: str, src_col: str) -> None:
        """Inner content for the Response Log panel (no outer card)."""
        with ui.row().classes("items-center justify-between w-full"):
            ui.label("Response Log").classes("text-md font-medium")
            self.drag_handle(pid, src_col)
        self.response_log = (
            ui.log(max_lines=500)
            .classes("w-full whitespace-pre-wrap break-words")
            .style("height: 190px")
        )
        ui.button("Show received frame", on_click=self.show_received_frame).props(
            "outline"
        )

    def render_jog_content(self, pid: str, src_col: str) -> None:
        """Inner content for the Jog panel (no outer card)."""
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-4"):
                with ui.tabs() as jog_mode_tabs:
                    joint_tab = ui.tab("Joint jog")
                    cart_tab = ui.tab("Cartesian jog")
                jog_mode_tabs.value = joint_tab
                frame_radio = ui.toggle(options=["WRF", "TRF"], value="TRF").props(
                    "dense"
                )
                frame_radio.on_value_change(
                    lambda e: self.set_frame(str(frame_radio.value or "TRF"))
                )
            self.drag_handle(pid, src_col)

        with ui.tab_panels(jog_mode_tabs, value=joint_tab).classes("w-full"):
            with ui.tab_panel(joint_tab).classes("gap-2"):
                joint_names = ["J1", "J2", "J3", "J4", "J5", "J6"]

                def make_joint_row(idx: int, name: str):
                    with ui.element("div").classes("joint-progress-container"):
                        ui.label(name).classes("w-8")
                        left = ui.image("/static/icons/button_arrow_1.webp").style(
                            "width:60px;height:60px;object-fit:contain;transform:rotate(270deg);cursor:pointer;"
                        )
                        bar = (
                            ui.linear_progress(value=0.5)
                            .props("rounded")
                            .classes("joint-progress-bar")
                        )
                        right = ui.image("/static/icons/button_arrow_1.webp").style(
                            "width:60px;height:60px;object-fit:contain;transform:rotate(90deg);cursor:pointer;"
                        )
                        self.joint_progress_bars.append(bar)
                        # store refs and bind async handlers
                        self._joint_left_imgs[idx] = left
                        self._joint_right_imgs[idx] = right
                        left.on(
                            "mousedown",
                            partial(self.set_joint_pressed, idx, "neg", True),
                        )
                        left.on(
                            "mouseup",
                            partial(self.set_joint_pressed, idx, "neg", False),
                        )
                        left.on(
                            "mouseleave",
                            partial(self.set_joint_pressed, idx, "neg", False),
                        )
                        right.on(
                            "mousedown",
                            partial(self.set_joint_pressed, idx, "pos", True),
                        )
                        right.on(
                            "mouseup",
                            partial(self.set_joint_pressed, idx, "pos", False),
                        )
                        right.on(
                            "mouseleave",
                            partial(self.set_joint_pressed, idx, "pos", False),
                        )

                self.joint_progress_bars.clear()
                for i, n in enumerate(joint_names):
                    make_joint_row(i, n)

            with ui.tab_panel(cart_tab).classes("gap-2"):

                def axis_image(src: str, axis: str, rotate_deg: int = 0):
                    img = ui.image(src).style(
                        f"width:72px;height:72px;object-fit:contain;cursor:pointer;transform:rotate({rotate_deg}deg);"
                    )
                    self._cart_axis_imgs[axis] = img
                    img.on("mousedown", partial(self.set_axis_pressed, axis, True))
                    img.on("mouseup", partial(self.set_axis_pressed, axis, False))
                    img.on("mouseleave", partial(self.set_axis_pressed, axis, False))

                with ui.row().classes("items-start gap-8"):
                    with ui.element("div").classes("cart-jog-grid-3"):
                        ui.element("div").style("width:72px;height:72px")
                        axis_image("/static/icons/cart_x_up.webp", "X+")
                        ui.element("div").style("width:72px;height:72px")
                        axis_image("/static/icons/cart_y_left.webp", "Y-")
                        ui.element("div").style("width:72px;height:72px")
                        axis_image("/static/icons/cart_y_right.webp", "Y+")
                        ui.element("div").style("width:72px;height:72px")
                        axis_image("/static/icons/cart_x_down.webp", "X-")
                        ui.element("div").style("width:72px;height:72px")
                    with ui.column().classes("gap-16"):
                        axis_image("/static/icons/cart_z_up.webp", "Z+")
                        axis_image("/static/icons/cart_z_down.webp", "Z-")
                    with ui.column().classes("gap-16"):
                        axis_image("/static/icons/RZ_PLUS.webp", "RZ+")
                        axis_image("/static/icons/RZ_MINUS.webp", "RZ-")
                    with ui.element("div").classes("cart-jog-grid-6"):
                        ui.element("div")
                        axis_image("/static/icons/RX_PLUS.webp", "RX+")
                        ui.element("div")
                        axis_image("/static/icons/RY_PLUS.webp", "RY+")
                        ui.element("div")
                        axis_image("/static/icons/RY_MINUS.webp", "RY-")
                        ui.element("div")
                        axis_image("/static/icons/RX_MINUS.webp", "RX-")

    def build_command_palette_table(self, prefill_toggle) -> None:
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
            {
                "name": "title",
                "label": "Command",
                "field": "title",
                "sortable": True,
                "align": "left",
            },
        ]

        # Scrollable container for the table
        with ui.element("div").classes("overflow-y-auto w-full").style("height: 400px"):
            table = ui.table(
                columns=columns,
                rows=rows,
                row_key="key",  # Use unique key column
            ).props("flat dense separator=horizontal")

        def make_snippet(key: str) -> str:
            current = bool(prefill_toggle.value)

            if key == "MoveJoint":
                if current and robot_state.angles and len(robot_state.angles) >= 6:
                    return (
                        "MoveJoint("
                        + ", ".join(f"{a:.1f}" for a in robot_state.angles[:6])
                        + ")"
                    )
                return "MoveJoint(0, 0, 0, 0, 0, 0)"
            elif key == "SpeedJoint":
                return "SpeedJoint(50)"
            elif key == "MovePose":
                if current and robot_state.pose and len(robot_state.pose) >= 12:
                    x, y, z = (
                        robot_state.pose[3],
                        robot_state.pose[7],
                        robot_state.pose[11],
                    )
                    return f"MovePose({x:.1f}, {y:.1f}, {z:.1f}, 0, 0, 0)"
                return "MovePose(0, 0, 0, 0, 0, 0)"
            elif key == "Home":
                return "Home()"
            elif key == "Delay":
                return "Delay(1.0)"
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

                if key and self.program_textarea:
                    snippet = make_snippet(key)
                    val = self.program_textarea.value
                    if val and not val.endswith("\n"):
                        val += "\n"
                    self.program_textarea.value = val + snippet + "\n"
                    logging.info("Added command: %s", snippet)
            except Exception as ex:
                ui.notify(f"Click handler error: {ex}", color="negative")
                logging.error("Click handler error: %s", ex)

        table.on("rowClick", insert_from_row)

    def render_editor_content(self, pid: str, src_col: str) -> None:
        """Inner content for the Program Editor panel (no outer card)."""
        with ui.element("div").classes("editor-layout"):
            with ui.column().classes("editor-main"):
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.label("Program:").classes("text-md font-medium")
                    self.program_filename_input = ui.input(
                        label="Filename", value=""
                    ).classes("text-sm font-small flex-1")
                    ui.button("Open", on_click=self.open_file_picker).props(
                        "unelevated"
                    )
                self.program_textarea = (
                    ui.codemirror(
                        value="",
                        line_wrapping=True,
                    )
                    .classes("w-full")
                    .style("height: 360px")
                )
                # Initialize CodeMirror theme based on theme/system
                try:
                    mode = get_theme()
                    effective = "light" if mode == "light" else "dark"
                    self.program_textarea.theme = (
                        "basicLight" if effective == "light" else "oneDark"
                    )
                except Exception:
                    self.program_textarea.theme = "oneDark"
                with ui.row().classes("gap-2"):
                    ui.button("Start", on_click=self.execute_program).props(
                        "unelevated color=positive"
                    )
                    ui.button("Stop", on_click=self.stop_program).props(
                        "unelevated color=negative"
                    )
                    ui.button("Save", on_click=self.save_program).props("unelevated")

                    def save_as():
                        async def do_save_as():
                            name = save_as_input.value.strip() or "program.txt"
                            await self.save_program(as_name=name)
                            save_as_dialog.close()

                        save_as_dialog = ui.dialog()
                        with save_as_dialog, ui.card():
                            ui.label("Save As")
                            save_as_input = ui.input(
                                label="New filename",
                                value=self.program_filename_input.value
                                if self.program_filename_input
                                else "",
                            ).classes("w-80")
                            with ui.row().classes("gap-2"):
                                ui.button("Cancel", on_click=save_as_dialog.close)
                                ui.button("Save", on_click=do_save_as).props(
                                    "color=positive"
                                )
                        save_as_dialog.open()

                    ui.button("Save as", on_click=save_as).props("unelevated")
            with ui.column().classes("editor-palette"):
                with ui.row().classes("editor-palette-header"):
                    prefill_toggle = ui.switch("Current Pose", value=True)
                    self.drag_handle(pid, src_col)
                self.build_command_palette_table(prefill_toggle)

    def draggable_card(
        self, title: str, pid: str, src_col: str, render_body_fn, card_classes: str = ""
    ) -> None:
        """Create a card whose drag handle is integrated into its header/body, not a separate row."""
        card = ui.card().classes(f"w-full relative {card_classes}")
        with card:
            render_body_fn(pid, src_col)

    def render_panel_contents(self, pid: str, src_col: str) -> None:
        # Use a draggable header handle and place content-only bodies inside.
        if pid == "jog":
            self.draggable_card(
                "Jog", pid, src_col, self.render_jog_content, "min-h-[500px]"
            )
        elif pid == "editor":
            self.draggable_card(
                "Program editor",
                pid,
                src_col,
                self.render_editor_content,
                "min-h-[500px]",
            )
        elif pid == "readouts":
            self.draggable_card(
                "Readouts & Controls", pid, src_col, self.render_readouts_content
            )
        elif pid == "log":
            self.draggable_card("Response Log", pid, src_col, self.render_log_content)
        elif pid == "urdf":
            self.draggable_card(
                "URDF Viewer", pid, src_col, self.render_urdf_content, "min-h-[500px]"
            )

    def render_panel_wrapper(self, parent, pid: str, src_col: str):
        # Render the draggable card (dragging limited to header handle).
        with parent:
            self.render_panel_contents(pid, src_col)

    def render_drop_spacer(self, parent, col_name: str, index: int):
        # Create the spacer inside the parent so it occupies space and can push siblings down
        with parent:
            spacer = (
                ui.element("div")
                .classes("drop-spacer m-0 p-0 b-0")
                .style("width: 100%;")
            )
            spacer.on("dragenter", lambda e, s=spacer: s.classes(add="active"))
            spacer.on("dragover.prevent", lambda e, s=spacer: s.classes(add="active"))
            spacer.on("dragleave", lambda e, s=spacer: s.classes(remove="active"))

            def handle_drop(e, c=col_name, i=index, s=spacer, cont=parent):
                cont.classes(remove="highlight")
                s.classes(remove="active")
                self.on_drop_to(c, i)

            spacer.on("drop.prevent", handle_drop)

    def render_one_column(self, container, col_name: str):
        # column-level highlight
        container.on(
            "dragover.prevent", lambda e, c=container: c.classes(add="highlight")
        )
        container.on("dragleave", lambda e, c=container: c.classes(remove="highlight"))

        # interleave spacers and panels
        items = (
            self.current_layout["left"]
            if col_name == "left"
            else self.current_layout["right"]
        )
        self.render_drop_spacer(container, col_name, 0)
        for i, pid in enumerate(items):
            self.render_panel_wrapper(container, pid, col_name)
            self.render_drop_spacer(container, col_name, i + 1)

    def render_move_columns(self) -> None:
        if self.left_col_container:
            self.left_col_container.clear()
        if self.right_col_container:
            self.right_col_container.clear()
        self.render_one_column(self.left_col_container, "left")
        self.render_one_column(self.right_col_container, "right")

    def build(self) -> None:
        """Build the Move page with drag-and-drop layout."""
        # Load per-user layout once in page context
        try:
            self.current_layout = self._load_layout()
        except Exception:
            self.current_layout = self.DEFAULT_LAYOUT.copy()
        # Use CSS Grid for responsive 2-column to 1-column layout
        with ui.element("div").classes("move-layout-container"):
            self.left_col_container = ui.column().classes("gap-2 min-h-[100px]")
            self.right_col_container = ui.column().classes("gap-2 min-h-[100px]")
        self.render_move_columns()
