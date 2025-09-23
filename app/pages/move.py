from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from functools import partial
from typing import TypedDict, cast

from nicegui import app as ng_app
from nicegui import ui
from nicegui import binding

from app.common.theme import get_theme
from app.constants import (
    PAROL6_URDF_PATH,
    REPO_ROOT,
    JOINT_LIMITS_DEG,
    WEBAPP_CONTROL_INTERVAL_S,
    WEBAPP_CONTROL_RATE_HZ,
)
from app.state import robot_state
from app.services.robot_client import client
from urdf_scene_nicegui import UrdfScene  # type: ignore
from app.services.script_runner import (
    ScriptProcessHandle,
    run_script,
    create_default_config,
    stop_script,
)
from parol6.protocol.types import Axis, Frame


class MoveLayout(TypedDict):
    left: list[str]
    right: list[str]


class MovePage:
    """Move tab page with drag-and-drop layout."""

    # Bindable control properties
    jog_speed = binding.BindableProperty()
    jog_accel = binding.BindableProperty()
    incremental_jog = binding.BindableProperty()
    joint_step_deg = binding.BindableProperty()
    frame = binding.BindableProperty()

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

        # Program execution - legacy (to be removed)
        self.program_task: asyncio.Task | None = None
        self.program_cancel_event: asyncio.Event | None = None
        self.program_speed_percentage: int | None = None  # set by JointVelSet alias

        # Script execution via subprocess
        self.script_handle: ScriptProcessHandle | None = None
        self.script_running: bool = False

        # URDF viewer state
        self.urdf_scene = None  # UrdfScene instance
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

        # Jog cadence constants (derived from webapp config; default 50 Hz)
        self.JOG_TICK_S: float = WEBAPP_CONTROL_INTERVAL_S
        self.CADENCE_WARN_WINDOW: int = max(1, int(WEBAPP_CONTROL_RATE_HZ))
        self.CADENCE_TOLERANCE: float = 0.002
        # Streaming watchdog timeout to use as "duration" while stream_mode is ON
        self.STREAM_TIMEOUT_S: float = 0.1

        # Single-user runtime preferences and state (no per-client storage)
        self.jog_speed = 50
        self.jog_accel = 50
        self.incremental_jog = False
        self.joint_step_deg = 1.0
        self.frame = "TRF"

        # Press/hold state for jog controls
        self._jog_pressed_pos: list[bool] = [False] * 6
        self._jog_pressed_neg: list[bool] = [False] * 6
        self._cart_pressed_axes: dict[str, bool] = {
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
        }

        # Jog timers (assigned by main)
        self.joint_jog_timer: ui.timer | None = None
        self.cart_jog_timer: ui.timer | None = None

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
        pos = self._jog_pressed_pos
        neg = self._jog_pressed_neg
        for j in range(6):
            if j < len(pos) and pos[j]:
                return (j, "pos")
            if j < len(neg) and neg[j]:
                return (j, "neg")
        return None

    def _get_first_pressed_axis(self) -> str | None:
        """Return the first pressed cartesian axis key like 'X+' if any."""
        for k, pressed in self._cart_pressed_axes.items():
            if pressed:
                return k
        return None

    async def set_joint_pressed(self, j: int, direction: str, is_pressed: bool) -> None:
        """Press-and-hold jog; if incremental mode is ON, fire one-shot step."""

        if direction == "pos":
            self._apply_pressed_style(self._joint_right_imgs.get(j), bool(is_pressed))
        else:
            self._apply_pressed_style(self._joint_left_imgs.get(j), bool(is_pressed))

        if 0 <= j < 6:
            if self.incremental_jog and is_pressed:
                speed = max(1, min(100, int(self.jog_speed)))
                step = abs(float(self.joint_step_deg))
                index = j if direction == "pos" else (j + 6)
                await client.jog_joint(
                    index, speed_percentage=speed, duration=None, distance_deg=step
                )
                return

            pos_pressed = self._jog_pressed_pos
            neg_pressed = self._jog_pressed_neg
            if (
                direction == "pos"
                and isinstance(pos_pressed, list)
                and len(pos_pressed) == 6
            ):
                pos_pressed[j] = bool(is_pressed)
            elif (
                direction == "neg"
                and isinstance(neg_pressed, list)
                and len(neg_pressed) == 6
            ):
                neg_pressed[j] = bool(is_pressed)

            # Toggle per-client joint jog timer based on any pressed joint
            t = self.joint_jog_timer
            any_pressed = any(self._jog_pressed_pos) or any(self._jog_pressed_neg)
            if t:
                if any_pressed and not t.active:
                    # Reset cadence stats on (re)activation to avoid first-window warning
                    self._tick_stats = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
                t.active = bool(any_pressed)

    async def jog_tick(self) -> None:
        """100 Hz: send/update joint streaming jog if any button is pressed."""
        speed = max(1, min(100, int(self.jog_speed)))
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
        self._apply_pressed_style(self._cart_axis_imgs.get(axis), bool(is_pressed))
        axes = self._cart_pressed_axes
        if isinstance(axes, dict) and axis in axes:
            if self.incremental_jog and is_pressed:
                speed = max(1, min(100, int(self.jog_speed)))
                step = max(0.1, min(100.0, float(self.joint_step_deg)))
                duration = max(0.02, min(0.5, step / 50.0))
                frame = cast(Frame, self.frame)
                await client.jog_cartesian(frame, cast(Axis, axis), speed, duration)
                return
            axes[axis] = bool(is_pressed)

            # Toggle per-client cartesian jog timer based on any pressed axis
            t = self.cart_jog_timer
            axes_now = self._cart_pressed_axes
            any_pressed = any(bool(v) for v in axes_now.values())
            if t:
                if any_pressed and not t.active:
                    # Reset cadence stats on (re)activation to avoid first-window warning
                    self._tick_stats_cart = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
                t.active = bool(any_pressed)

    async def cart_jog_tick(self) -> None:
        """100 Hz: send/update cartesian streaming jog if any axis is pressed."""
        speed = max(1, min(100, int(self.jog_speed)))
        frame = cast(Frame, self.frame)
        axis = self._get_first_pressed_axis()
        if axis is not None:
            await client.jog_cartesian(
                frame, cast(Axis, axis), speed, self.STREAM_TIMEOUT_S
            )
        # cadence monitor
        self._cadence_tick(time.time(), self._tick_stats_cart, "cart")

    # ---- Robot actions ----

    async def send_enable(self) -> None:
        try:
            _ = await client.enable()
            ui.notify("Sent ENABLE", color="positive")
            logging.info("ENABLE sent")
        except Exception as e:
            logging.error("ENABLE failed: %s", e)

    async def send_disable(self) -> None:
        try:
            _ = await client.disable()
            ui.notify("Sent DISABLE", color="warning")
            logging.warning("DISABLE sent")
        except Exception as e:
            logging.error("DISABLE failed: %s", e)

    async def send_home(self) -> None:
        try:
            _ = await client.home()
            ui.notify("Sent HOME", color="primary")
            logging.info("HOME sent")
        except Exception as e:
            logging.error("HOME failed: %s", e)

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

    def _default_python_snippet(self) -> str:
        """Generate the initial pre-filled Python code with inlined controller host/port."""
        from app.constants import CONTROLLER_HOST, CONTROLLER_PORT

        return f"""from parol6 import RobotClient

rbt = RobotClient(host={CONTROLLER_HOST!r}, port={CONTROLLER_PORT})

print("Moving to home position...")
rbt.home()

status = rbt.get_status()
print(f"Robot status: {{status}}")
"""

    async def _start_script_process(self) -> None:
        """Save current editor content and start it as a Python subprocess."""
        if self.script_running:
            ui.notify("Script already running", color="warning")
            return

        try:
            # Get filename, default to program.py if empty
            filename = (
                self.program_filename_input.value.strip()
                if self.program_filename_input
                else ""
            ) or "program.py"

            # Ensure .py extension
            if not filename.endswith(".py"):
                filename += ".py"

            # Save script content to file
            content = self.program_textarea.value if self.program_textarea else ""
            script_path = self.PROGRAM_DIR / filename
            script_path.write_text(content, encoding="utf-8")

            # Update filename input
            if self.program_filename_input:
                self.program_filename_input.value = filename

            config = create_default_config(str(script_path), str(REPO_ROOT))

            # Start the script process with log callbacks
            def on_stdout(line: str):
                if self.response_log:
                    self.response_log.push(line)

            def on_stderr(line: str):
                if self.response_log:
                    self.response_log.push(line)

            await client.stream_off()
            self.script_handle = await run_script(config, on_stdout, on_stderr)
            self.script_running = True

            # Launch monitor task to reset state when script finishes
            h = self.script_handle  # capture
            asyncio.create_task(self._monitor_script_completion(h, filename))

            ui.notify(f"Started script: {filename}", color="positive")
            logging.info("Started script: %s", filename)

        except Exception as e:
            ui.notify(f"Failed to start script: {e}", color="negative")
            logging.error("Failed to start script: %s", e)

    async def _monitor_script_completion(
        self, handle: ScriptProcessHandle, filename: str
    ) -> None:
        """Monitor script subprocess completion and reset state when it finishes."""
        try:
            rc = await handle["proc"].wait()
            # Let stream reader tasks finish
            for t in (handle["stdout_task"], handle["stderr_task"]):
                with contextlib.suppress(Exception):
                    await t
            # Only reset state if this handle is still the active one
            if self.script_handle is handle:
                self.script_handle = None
                self.script_running = False
                ui.notify(
                    f"Script finished: {filename} (exit {rc})",
                    color="positive" if rc == 0 else "warning",
                )
                logging.info("Script %s finished with code %s", filename, rc)
                await client.stream_on()
        except Exception as e:
            logging.error("Error monitoring script process: %s", e)
            # Best-effort reset if still active
            if self.script_handle is handle:
                self.script_handle = None
                self.script_running = False

    async def _stop_script_process(self) -> None:
        """Stop the running script process."""
        if not self.script_running or not self.script_handle:
            ui.notify("No script running", color="warning")
            return

        try:
            handle = self.script_handle  # capture
            # Clear UI state up-front; monitor will see this and stay silent
            self.script_handle = None
            self.script_running = False

            if handle:
                await stop_script(handle)
            await client.stream_on()

            ui.notify("Script stopped", color="warning")
            logging.info("Script stopped by user")

        except Exception as e:
            ui.notify(f"Error stopping script: {e}", color="negative")
            logging.error("Error stopping script: %s", e)
            # State already cleared above

    def _insert_python_snippet(self, key: str) -> str:
        """Get Python code snippet for the given key."""
        snippets = {
            "enable": "rbt.enable()",
            "disable": "rbt.disable()",
            "home": "rbt.home()",
            "stop": "rbt.stop()",
            "clear_error": "rbt.clear_error()",
            "delay": "time.sleep(1.0)",
            "get_status": "status = rbt.get_status()\nprint(status)",
            "get_angles": "angles = rbt.get_angles()\nprint(f'Joint angles: {angles}')",
            "move_joint": "rbt.move_joint([0, 0, 0, 0, 0, 0])",
            "jog_joint": "rbt.jog_joint(0, speed_percentage=50, duration=1.0)",
            "set_speed": "rbt.set_speed(50)",
            "comment": "# Add your robot commands here",
        }
        return snippets.get(key, f"# {key}")

    # ---- Layout management ----

    def _valid_layout(self, data) -> bool:
        return (
            isinstance(data, dict)
            and isinstance(data.get("left"), list)
            and isinstance(data.get("right"), list)
        )

    def _load_layout(self) -> MoveLayout:
        data = ng_app.storage.general.get("move_layout")
        if self._valid_layout(data):
            return data  # type: ignore[return-value]
        layout = self.DEFAULT_LAYOUT.copy()
        ng_app.storage.general["move_layout"] = layout
        return layout

    def _save_layout(self, layout: MoveLayout) -> None:
        try:
            ng_app.storage.general["move_layout"] = layout
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
            ui.label("URDF Viewer")
            self.drag_handle(pid, src_col)

        # Initialize URDF scene
        async def init_scene():
            try:
                await self._initialize_urdf_scene()
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
        if not self.urdf_scene:
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
                            "text-sm text-[var(--ctk-muted)] w-6"
                        )
                        self.tool_labels[key] = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state,
                                {
                                    "X": "x",
                                    "Y": "y",
                                    "Z": "z",
                                    "Rx": "rx",
                                    "Ry": "ry",
                                    "Rz": "rz",
                                }[key],
                                backward=lambda v: f"{v:.3f}",
                            )
                            .classes("text-4xl")
                        )
            with ui.column().classes("readouts-col"):
                ui.label("Joint positions").classes("text-sm")
                self.joint_labels.clear()
                for i in range(6):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"θ{i + 1}:").classes(
                            "text-sm text-[var(--ctk-muted)] w-6"
                        )
                        self.joint_labels.append(
                            ui.label("-")
                            .bind_text_from(
                                robot_state,
                                "angles",
                                backward=lambda a, i=i: (  # type: ignore
                                    f"{float(a[i]):.3f}"
                                    if isinstance(a, list)
                                    and len(a) > i
                                    and isinstance(a[i], (int, float))
                                    and math.isfinite(float(a[i]))
                                    else "-"
                                ),
                            )
                            .classes("text-4xl")
                        )
            with ui.column().classes("readouts-controls"):
                ui.label("Controls").classes("text-sm")
                # Sliders
                ui.label("Jog velocity %").classes("text-xs text-[var(--ctk-muted)]")
                ui.slider(
                    min=1,
                    max=100,
                    value=self.jog_speed,
                    step=1,
                ).bind_value(self, "jog_speed").classes("w-full").style("width: 100%")
                ui.label("Jog accel %").classes("text-xs text-[var(--ctk-muted)]")
                ui.slider(
                    min=1,
                    max=100,
                    value=self.jog_accel,
                    step=1,
                ).bind_value(self, "jog_accel").classes("w-full").style("width: 100%")
                # Incremental and step
                with ui.row().classes("items-center gap-4 w-full"):
                    ui.switch(
                        "Incremental jog",
                        value=self.incremental_jog,
                    ).bind_value(self, "incremental_jog")
                    ui.number(
                        label="Step size (deg/mm)",
                        value=self.joint_step_deg,
                        min=0.1,
                        max=100.0,
                        step=0.1,
                    ).bind_value(self, "joint_step_deg").style("width: 120px")

                    # IO summary (live-updated via binding to robot_state.io)
                    self.io_summary_label = (
                        ui.label("IO: -")
                        .bind_text_from(
                            robot_state,
                            "io",
                            backward=lambda io: (
                                f"IO: IN1={io[0] if len(io) > 0 else '-'} "
                                f"IN2={io[1] if len(io) > 1 else '-'} "
                                f"OUT1={io[2] if len(io) > 2 else '-'} "
                                f"OUT2={io[3] if len(io) > 3 else '-'}"
                            ),
                        )
                        .classes("text-sm")
                    )
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

    def render_jog_content(self, pid: str, src_col: str) -> None:
        """Inner content for the Jog panel (no outer card)."""
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-4"):
                with ui.tabs().props("dense") as jog_mode_tabs:
                    joint_tab = ui.tab("Joint jog")
                    cart_tab = ui.tab("Cartesian jog")
                jog_mode_tabs.value = joint_tab
                ui.toggle(
                    options=["WRF", "TRF"],
                    value=self.frame,
                ).bind_value(self, "frame").props("dense")
            self.drag_handle(pid, src_col)

        with ui.tab_panels(jog_mode_tabs, value=joint_tab).classes("w-full"):
            with ui.tab_panel(joint_tab).classes("gap-1"):
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
                            .bind_value_from(
                                robot_state,
                                "angles",
                                backward=lambda a, i=idx: round(  # type: ignore
                                    0.0
                                    if not (
                                        isinstance(a, list)
                                        and len(a) > i
                                        and isinstance(a[i], (int, float))
                                    )
                                    or (
                                        JOINT_LIMITS_DEG[i][1] <= JOINT_LIMITS_DEG[i][0]
                                    )
                                    else max(
                                        0.0,
                                        min(
                                            1.0,
                                            (float(a[i]) - JOINT_LIMITS_DEG[i][0])
                                            / (
                                                JOINT_LIMITS_DEG[i][1]
                                                - JOINT_LIMITS_DEG[i][0]
                                            ),
                                        ),
                                    ),
                                    3,
                                ),
                            )
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
        # Python snippet palette with useful robot commands
        rows = [
            {"key": "enable", "title": "rbt.enable()"},
            {"key": "home", "title": "rbt.home()"},
            {"key": "move_joint", "title": "rbt.move_joint([...])"},
            {"key": "jog_joint", "title": "rbt.jog_joint(...)"},
            {"key": "get_status", "title": "rbt.get_status()"},
            {"key": "get_angles", "title": "rbt.get_angles()"},
            {"key": "delay", "title": "time.sleep(...)"},
            {"key": "disable", "title": "rbt.disable()"},
            {"key": "stop", "title": "rbt.stop()"},
            {"key": "clear_error", "title": "rbt.clear_error()"},
            {"key": "set_speed", "title": "rbt.set_speed(...)"},
        ]

        columns = [
            {
                "name": "title",
                "label": "Python Command",
                "field": "title",
                "sortable": True,
                "align": "left",
            },
        ]

        # Scrollable container for the table
        with ui.element("div").classes("overflow-y-auto w-full").style("height: 260px"):
            table = ui.table(
                columns=columns,
                rows=rows,
                row_key="key",  # Use unique key column
            ).props("flat dense separator=horizontal")

        def insert_from_row(e) -> None:
            try:
                row_data = e.args[1] if len(e.args) >= 2 else {}
                key = row_data.get("key", "")

                if key and self.program_textarea:
                    snippet = self._insert_python_snippet(key)
                    val = self.program_textarea.value
                    if val and not val.endswith("\n"):
                        val += "\n"
                    self.program_textarea.value = val + snippet + "\n"
                    logging.info("Added Python snippet: %s", snippet)
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
                        value=self._default_python_snippet(),
                        language="Python",
                        line_wrapping=True,
                    )
                    .classes("w-full")
                    .style("height: 210px")
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
                    ui.button("Start", on_click=self._start_script_process).props(
                        "unelevated color=positive"
                    )
                    ui.button("Stop", on_click=self._stop_script_process).props(
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
                "min-h-[350px]",
            )
        elif pid == "readouts":
            self.draggable_card(
                "Readouts & Controls",
                pid,
                src_col,
                self.render_readouts_content,
                "min-h-[350px]",
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
