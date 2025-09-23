import asyncio
import logging
import math
import time
import contextlib
import argparse
import os
import sys

from nicegui import app as ng_app
from nicegui import ui
from nicegui.elements.tooltip import Tooltip
from parol6 import ensure_server, ServerManager

from app.common.logging_config import attach_ui_log, configure_logging, TRACE
from app.common.theme import apply_theme, get_theme, inject_layout_css
from app.constants import (
    PAROL6_OFFICIAL_DOC_URL,
    REPO_ROOT,
    SERVER_HOST,
    SERVER_PORT,
    CONTROLLER_HOST,
    CONTROLLER_PORT,
    AUTO_START,
    LOG_LEVEL,
    WEBAPP_CONTROL_INTERVAL_S,
)
from app.pages.calibrate import CalibratePage
from app.pages.gripper import GripperPage
from app.pages.io import IoPage
from app.pages.move import MovePage
from app.pages.settings import SettingsPage
from app.services.robot_client import client
from app.state import robot_state, controller_state

# Runtime configuration (resolved later from CLI/env)
RUNTIME_SERVER_HOST = SERVER_HOST
RUNTIME_SERVER_PORT = SERVER_PORT
RUNTIME_CONTROLLER_HOST = CONTROLLER_HOST
RUNTIME_CONTROLLER_PORT = CONTROLLER_PORT
RUNTIME_AUTO_START = AUTO_START

# Register static files for optimized icons and other assets
ng_app.add_static_files("/static", (REPO_ROOT / "app" / "static").as_posix())

# ------------------------ Global UI/state ------------------------

fw_version = "1.0.0"
estop_label: ui.label | None = None
controller_status_label: ui.label | None = None
robot_status_label: ui.label | None = None

ctrl_tooltip: Tooltip | None = None
robot_tooltip: Tooltip | None = None
estop_tooltip: Tooltip | None = None
com_input_tooltip: Tooltip | None = None

# Multicast-driven status consumer (runs once per app)
status_consumer_task: asyncio.Task | None = None
# Connectivity ping timer (1Hz)
ping_timer: ui.timer | None = None
last_ping_ok: bool = False

# Page instances
move_page_instance = MovePage()
io_page_instance = IoPage()
settings_page_instance = SettingsPage()
calibrate_page_instance = CalibratePage()
gripper_page_instance = GripperPage()

# Main tabs reference for tab_panels
main_tabs = None
server_manager: ServerManager | None = None

# --------------- Controller controls ---------------


async def start_controller(com_port: str | None) -> None:
    global server_manager
    try:
        # If AUTO_START requested, ensure a server is running at the target tuple
        if RUNTIME_AUTO_START:
            server_manager = await ensure_server(
                host=RUNTIME_CONTROLLER_HOST,
                port=RUNTIME_CONTROLLER_PORT,
                manage=True,
                com_port=com_port,
                extra_env=None,
                normalize_logs=True,
            )

        # enable ping timer now that we are connected
        global ping_timer, status_consumer_task
        if ping_timer:
            ping_timer.active = True
        # start multicast consumer
        if status_consumer_task is None or status_consumer_task.done():
            status_consumer_task = asyncio.create_task(_status_consumer())
        controller_state.running = True
        controller_state.com_port = com_port
        if controller_status_label:
            tip = "running" if com_port else "running (no port)"
            controller_status_label.text = "CTRL"
            if ctrl_tooltip:
                ctrl_tooltip.text = tip
            controller_status_label.style("color: #21BA45")
        logging.info("Controller started")
    except Exception as e:
        logging.error("Start controller failed: %s", e)


async def stop_controller() -> None:
    global server_manager
    try:
        if server_manager:
            await server_manager.stop_controller()
        server_manager = None
        # disable ping timer on disconnect and stop consumer
        global ping_timer, status_consumer_task
        if ping_timer:
            ping_timer.active = False
        if status_consumer_task:
            status_consumer_task.cancel()
            with contextlib.suppress(Exception):
                await status_consumer_task
            status_consumer_task = None
        controller_state.running = False
        robot_state.connected = False
        if controller_status_label:
            controller_status_label.text = "CTRL"
            if ctrl_tooltip:
                ctrl_tooltip.text = "stopped"
            controller_status_label.style("color: #DB2828")
        logging.info("Controller stopped")
    except Exception as e:
        logging.error("Stop controller failed: %s", e)


async def send_clear_error() -> None:
    try:
        _ = await client.clear_error()
        ui.notify("Sent CLEAR_ERROR", color="primary")
        logging.info("CLEAR_ERROR sent")
    except Exception as e:
        logging.error("CLEAR_ERROR failed: %s", e)


async def send_stop_motion() -> None:
    try:
        _ = await client.stop()
        ui.notify("Sent STOP", color="warning")
        logging.warning("STOP sent")
    except Exception as e:
        logging.error("STOP failed: %s", e)


async def set_port(port_str: str) -> None:
    if not port_str:
        ui.notify("Provide a COM/tty port", color="warning")
        return
    try:
        _ = await client.set_serial_port(port_str)
        ui.notify(f"Sent SET_PORT {port_str}", color="primary")
        logging.info("SET_PORT sent")
    except Exception as e:
        logging.error("SET_PORT failed: %s", e)


async def handle_sim_toggle(value: str):
    try:
        if value == "Simulator":
            # Stop user's script if running (GUI safety)
            if move_page_instance.script_running:
                await move_page_instance._stop_script_process()
            # Best effort: stop real robot motion before switching
            with contextlib.suppress(Exception):
                await client.stop()
            await client.simulator_on()
        else:
            await client.simulator_off()
        ui.notify("Toggled Simulator Mode", color="primary")
    except Exception as ex:
        ui.notify(f"Failed to toggle Simulator Mode: {ex}", color="negative")


async def on_sim_toggle_change(e):
    await handle_sim_toggle(e.value)


# --------------- Connectivity Check ---------------


async def check_ping() -> None:
    """Check connectivity via PING (1Hz)"""
    global last_ping_ok
    try:
        pong = await client.ping()
        serial = False
        if isinstance(pong, str):
            if "SERIAL=" in pong:
                serial = pong.split("SERIAL=", 1)[-1].strip().startswith("1")
        elif isinstance(pong, dict):
            payload = pong.get("payload")
            if isinstance(payload, dict):
                val = payload.get("serial") or payload.get("serial_connected")
                if isinstance(val, (int, bool)):
                    serial = bool(val)
        last_ping_ok = bool(serial)
    except Exception:
        last_ping_ok = False

    # Update robot connectivity status
    robot_state.connected = last_ping_ok
    if robot_status_label:
        robot_status_label.text = "ROBOT"
        if robot_tooltip:
            robot_tooltip.text = "connected" if last_ping_ok else "disconnected"
        robot_status_label.style("color: #21BA45" if last_ping_ok else "color: #DB2828")


# --------------- UI Update Functions ---------------


def _normalize_joint_progress(
    angle_deg: float, min_deg: float, max_deg: float
) -> float:
    if max_deg <= min_deg:
        return 0.0
    val = (angle_deg - min_deg) / (max_deg - min_deg)
    return max(0.0, min(1.0, val))


def update_ui_from_status() -> None:
    """Update UI elements from robot_state (called from multicast consumer)"""
    angles = robot_state.angles or []
    pose = robot_state.pose or []
    io = robot_state.io or []
    gr = robot_state.gripper or []

    # Update Move page UI labels (angles + progress bars)
    if angles:
        move_page_instance.update_urdf_angles(angles)

    if pose and len(pose) >= 12:
        # Pose matrix flattened; indices 3,7,11 as XYZ
        x, y, z = pose[3], pose[7], pose[11]
        robot_state.x = float(x)
        robot_state.y = float(y)
        robot_state.z = float(z)

        # Compute Rx/Ry/Rz if full rotation matrix present
        if len(pose) >= 16:
            r11 = pose[0]
            r21, r22, r23 = pose[4], pose[5], pose[6]
            r31, r32, r33 = pose[8], pose[9], pose[10]

            sy = math.sqrt(r11 * r11 + r21 * r21)
            if sy > 1e-6:  # Not at gimbal lock
                rx = math.atan2(r32, r33)
                ry = math.atan2(-r31, sy)
                rz = math.atan2(r21, r11)
            else:  # Gimbal lock case
                rx = math.atan2(-r23, r22)
                ry = math.atan2(-r31, sy)
                rz = 0.0

            robot_state.rx = math.degrees(rx)
            robot_state.ry = math.degrees(ry)
            robot_state.rz = math.degrees(rz)

    if len(io) >= 5:
        in1, in2, out1, out2, estop = io[:5]
        estop_text = "OK" if estop else "TRIGGERED"

        # Update footer E-STOP
        if estop_label:
            estop_label.text = "E-STOP"
            if estop_tooltip:
                estop_tooltip.text = estop_text
            estop_label.style("color: #21BA45" if estop else "color: #DB2828")

        # Push IO derived fields into bindable RobotState
        robot_state.io_in1 = int(in1)
        robot_state.io_in2 = int(in2)
        robot_state.io_out1 = int(out1)
        robot_state.io_out2 = int(out2)
        robot_state.io_estop = int(estop)
    else:
        # Footer fallback on failure
        if estop_label:
            estop_label.text = "E-STOP"
            if estop_tooltip:
                estop_tooltip.text = "unknown"
            estop_label.style("color: #9E9E9E")

    if len(gr) >= 6:
        gid, pos, spd, cur, status_b, obj = gr[:6]
        # Push gripper derived fields into bindable RobotState
        robot_state.grip_id = int(gid)
        robot_state.grip_pos = int(pos)
        robot_state.grip_speed = int(spd)
        robot_state.grip_current = int(cur)
        robot_state.grip_obj = int(obj)

    # Update calibrate page button state
    calibrate_page_instance._update_go_to_limit_button()


def build_header_and_tabs() -> None:
    # Header with left navigation tabs, centered firmware text, right help + theme toggle
    with (
        ui.header().classes("p-0"),
        ui.row().classes("w-full items-center justify-between"),
    ):
        # Left: navigation tabs (will be returned for tab_panels)
        global main_tabs
        with ui.tabs() as main_tabs:
            move_tab = ui.tab("Move")
            io_tab = ui.tab("I/O")
            calibrate_tab = ui.tab("Calibrate")
            gripper_tab = ui.tab("Gripper")
            settings_tab = ui.tab("Settings")
        # Center: firmware label
        ui.label(f"FW version: {fw_version}").classes("text-sm text-center")
        # Right: theme toggle and help
        with ui.row().classes("items-center gap-2"):
            ui.button(
                "?",
                on_click=lambda: ui.run_javascript(
                    f"window.open('{PAROL6_OFFICIAL_DOC_URL}', '_blank')"
                ),
            ).props("round unelevated")

    # Tab panels
    with ui.tab_panels(main_tabs, value=move_tab).classes("w-full"):
        with ui.tab_panel(move_tab):
            move_page_instance.build()
        with ui.tab_panel(io_tab):
            io_page_instance.build()
        with ui.tab_panel(calibrate_tab):
            calibrate_page_instance.build()
        with ui.tab_panel(gripper_tab):
            gripper_page_instance.build()
        with ui.tab_panel(settings_tab):
            settings_page_instance.build()


def build_footer() -> None:
    # Footer: Simulator/Real, Connect/Disconnect, Clear error, E-stop
    with ui.footer().classes("justify-between items-center px-3 py-1"):
        with ui.row().classes("items-center gap-4"):
            global \
                estop_label, \
                controller_status_label, \
                robot_status_label, \
                ctrl_tooltip, \
                robot_tooltip, \
                estop_tooltip, \
                com_input_tooltip
            controller_status_label = ui.label("CTRL").classes("text-sm")
            with controller_status_label:
                ctrl_tooltip = ui.tooltip("unknown")
            ui.label("|").classes("text-sm text-[var(--ctk-muted)]")
            robot_status_label = ui.label("ROBOT").classes("text-sm")
            with robot_status_label:
                robot_tooltip = ui.tooltip("unknown")
            ui.label("|").classes("text-sm text-[var(--ctk-muted)]")
            estop_label = ui.label("E-STOP").classes("text-sm")
            with estop_label:
                estop_tooltip = ui.tooltip("unknown")
        with ui.row().classes("items-center gap-2"):
            stored_port = ng_app.storage.general.get("com_port", "")
            com_input = ui.input(label="Serial Port", value=stored_port)
            with com_input:
                com_input_tooltip = ui.tooltip(
                    "COM5 / /dev/ttyACM0 / /dev/tty.usbmodem0"
                )

            async def handle_set_port():
                ng_app.storage.general["com_port"] = com_input.value or ""
                await set_port(com_input.value or "")

            # Enter-to-apply
            com_input.on("keydown.enter", handle_set_port)

            # Simulator toggle - default based on whether port is empty
            ui.toggle(
                options=["Robot", "Simulator"],
                value="Simulator" if not stored_port else "Robot",
                on_change=on_sim_toggle_change,
            ).props("dense")

            ui.button("Set Port", on_click=handle_set_port)
            ui.button("Clear error", on_click=send_clear_error).props("color=warning")
            ui.button("Stop", on_click=send_stop_motion).props("color=negative")


async def _app_startup() -> None:
    apply_theme(get_theme())
    ui.query(".nicegui-content").classes("p-0")
    inject_layout_css()

    # Build header and tabs with panels
    build_header_and_tabs()
    # 50 Hz Web GUI to controller loop (configurable via PAROL_WEBAPP_CONTROL_RATE_HZ)
    move_page_instance.joint_jog_timer = ui.timer(
        interval=WEBAPP_CONTROL_INTERVAL_S,
        callback=move_page_instance.jog_tick,
        active=False,
    )
    move_page_instance.cart_jog_timer = ui.timer(
        interval=WEBAPP_CONTROL_INTERVAL_S,
        callback=move_page_instance.cart_jog_tick,
        active=False,
    )

    # Attach logging handler to move page response log
    if move_page_instance.response_log:
        attach_ui_log(move_page_instance.response_log)

    build_footer()

    try:
        port = ng_app.storage.general.get("com_port", "")
    except Exception:
        port = ""
    await start_controller(port)
    # Evaluate runtime env flags (allow overriding constants at test/runtime)
    auto_sim = os.getenv("PAROL_WEBAPP_AUTO_SIMULATOR", "1").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    require_ready = os.getenv("PAROL_WEBAPP_REQUIRE_READY", "1").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    # Default to simulator when no port is configured (opt-in)
    if not port and auto_sim:
        await client.simulator_on()

    # Ensure streaming mode is ON during UI operation (opt-in)
    if require_ready:
        await client.wait_for_server_ready(timeout=3.0)
        await client.stream_on()


ng_app.on_startup(_app_startup)

# Create ping timer (1Hz) for connectivity checks only
ping_timer = ui.timer(interval=1.0, callback=check_ping, active=False)


async def _status_consumer() -> None:
    """Consume multicast status and update shared robot_state."""
    try:
        # Wait for server to be responsive
        await client.wait_for_server_ready(timeout=5.0)
        async for status in client.status_stream():
            try:
                angles = status.get("angles") or []
                pose = status.get("pose") or []
                io_val = status.get("io") or []
                gr_val = status.get("gripper") or []

                # Coerce to expected list[int]/list[float] types for RobotState
                angles_list = angles if isinstance(angles, list) else robot_state.angles
                pose_list = pose if isinstance(pose, list) else robot_state.pose

                if isinstance(io_val, list) and all(
                    isinstance(x, (int, bool)) for x in io_val
                ):
                    io_list = [int(x) for x in io_val]
                else:
                    io_list = robot_state.io

                if isinstance(gr_val, list) and all(
                    isinstance(x, (int, bool)) for x in gr_val
                ):
                    gr_list = [int(x) for x in gr_val]
                else:
                    gr_list = robot_state.gripper

                robot_state.angles = angles_list or robot_state.angles
                robot_state.pose = pose_list or robot_state.pose
                robot_state.io = io_list
                robot_state.gripper = gr_list
                robot_state.last_update_ts = time.time()

                # Update UI directly from multicast consumer
                update_ui_from_status()
            except Exception as e:
                logging.debug("Status consumer parse error: %s", e)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error("Status consumer error: %s", e)


if __name__ in {"__main__", "__mp_main__"}:
    # CLI: web bind, controller target, and log level
    parser = argparse.ArgumentParser(description="PAROL6 NiceGUI Webserver")
    parser.add_argument("--host", default=SERVER_HOST, help="Webserver bind host")
    parser.add_argument(
        "--port", type=int, default=SERVER_PORT, help="Webserver bind port"
    )
    parser.add_argument(
        "--controller-host",
        default=CONTROLLER_HOST,
        help="Controller host to connect to",
    )
    parser.add_argument(
        "--controller-port",
        type=int,
        default=CONTROLLER_PORT,
        help="Controller UDP port",
    )
    parser.add_argument(
        "--log-level",
        choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set log level",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity; -v=INFO, -vv=DEBUG",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Enable WARNING logging"
    )
    parser.add_argument(
        "--disable-auto-start",
        action="store_false",
        default=True,
        help="Disable automatic controller start (overrides PAROL_AUTO_START env var)",
    )
    args, _ = parser.parse_known_args()

    # Resolve runtime values
    RUNTIME_SERVER_HOST = args.host
    RUNTIME_SERVER_PORT = int(args.port)
    RUNTIME_CONTROLLER_HOST = args.controller_host
    RUNTIME_CONTROLLER_PORT = int(args.controller_port)

    # Resolve AUTO_START: CLI flag overrides environment variable
    if args.disable_auto_start is not None:
        RUNTIME_AUTO_START = args.disable_auto_start

    client.host = RUNTIME_CONTROLLER_HOST
    client.port = RUNTIME_CONTROLLER_PORT

    # Resolve log level priority: explicit --log-level > -v/-q > env default from constants
    if args.log_level:
        # Include TRACE support via imported TRACE level constant
        if args.log_level == "TRACE":
            RUNTIME_LOG_LEVEL = TRACE
        else:
            RUNTIME_LOG_LEVEL = getattr(logging, args.log_level)
    elif args.verbose >= 3:
        os.environ["PAROL_TRACE"] = "1"
        RUNTIME_LOG_LEVEL = TRACE
    elif args.verbose >= 2:
        RUNTIME_LOG_LEVEL = logging.DEBUG
    elif args.verbose == 1:
        RUNTIME_LOG_LEVEL = logging.INFO
    elif args.quiet:
        RUNTIME_LOG_LEVEL = logging.WARNING
    else:
        RUNTIME_LOG_LEVEL = LOG_LEVEL

    # Configure logging
    configure_logging(RUNTIME_LOG_LEVEL)
    logging.info(
        f"Webserver bind: host={RUNTIME_SERVER_HOST} port={RUNTIME_SERVER_PORT}"
    )
    logging.info(
        f"Controller target: host={RUNTIME_CONTROLLER_HOST} port={RUNTIME_CONTROLLER_PORT}"
    )

    ui.run(
        title="PAROL6 NiceGUI Commander",
        host=RUNTIME_SERVER_HOST,
        port=RUNTIME_SERVER_PORT,
        reload=False,
        show=False,
        loop="uvloop" if sys.platform != "win32" else "asyncio",
        http="httptools",
        ws="wsproto",
        binding_refresh_interval=0.05,
    )
