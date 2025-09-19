import asyncio
import logging
import math
import time
import contextlib
import argparse

from nicegui import app as ng_app
from nicegui import ui
from parol6 import ensure_server

from app.common.logging_config import attach_ui_log, configure_logging
from app.common.theme import apply_theme, get_theme, inject_layout_css
from app.constants import (
    DEFAULT_COM_PORT,
    JOINT_LIMITS_DEG,
    PAROL6_OFFICIAL_DOC_URL,
    REPO_ROOT,
    SERVER_HOST,
    SERVER_PORT,
    CONTROLLER_HOST,
    CONTROLLER_PORT,
    AUTO_START,
)
from app.pages.calibrate import CalibratePage
from app.pages.gripper import GripperPage
from app.pages.io import IoPage
from app.pages.move import MovePage
from app.pages.settings import SettingsPage
from app.services.robot_client import client
from app.services.server_manager import server_manager
from app.state import robot_state, controller_state

# Runtime configuration (resolved later from CLI/env)
RUNTIME_SERVER_HOST = SERVER_HOST
RUNTIME_SERVER_PORT = SERVER_PORT
RUNTIME_CONTROLLER_HOST = CONTROLLER_HOST
RUNTIME_CONTROLLER_PORT = CONTROLLER_PORT
RUNTIME_LOG_LEVEL = logging.WARNING
RUNTIME_AUTO_START = AUTO_START

# Register static files for optimized icons and other assets
ng_app.add_static_files("/static", (REPO_ROOT / "app" / "static").as_posix())

# ------------------------ Global UI/state ------------------------

fw_version = "1.0.0"
estop_label: ui.label | None = None
controller_status_label: ui.label | None = None
robot_status_label: ui.label | None = None

# Status polling control (gated, non-blocking)
status_timer: ui.timer | None = None
status_busy = False

# Multicast-driven status consumer (runs once per app)
status_consumer_task: asyncio.Task | None = None
# Connectivity via ping (rate-limited)
PING_PERIOD_S: float = 1.0
last_ping_ts: float = 0.0
last_ping_ok: bool = False

# Page instances
move_page_instance = MovePage()
io_page_instance = IoPage()
settings_page_instance = SettingsPage()
calibrate_page_instance = CalibratePage()
gripper_page_instance = GripperPage()

# Main tabs reference for tab_panels
main_tabs = None

# --------------- Controller controls ---------------


async def start_controller(com_port: str | None) -> None:
    try:
        # If AUTO_START requested, ensure a server is running at the target tuple
        if RUNTIME_AUTO_START:
            mgr = await ensure_server(
                host=RUNTIME_CONTROLLER_HOST,
                port=RUNTIME_CONTROLLER_PORT,
                manage=True,
                com_port=com_port,
                extra_env=None,
            )
            # If a local server was spawned, update the shared server_manager reference
            if mgr is not None:
                from app.services import server_manager as sm

                sm.server_manager = mgr
        else:
            # Manual start via ServerManager
            await server_manager.start_controller(
                com_port=com_port,
                server_host=RUNTIME_CONTROLLER_HOST,
                server_port=RUNTIME_CONTROLLER_PORT,
            )

        # enable status polling now that we are connected
        global status_timer, status_consumer_task
        if status_timer:
            status_timer.active = True
        # start multicast consumer
        if status_consumer_task is None or status_consumer_task.done():
            status_consumer_task = asyncio.create_task(_status_consumer())
        controller_state.running = True
        controller_state.com_port = com_port
        if controller_status_label:
            text = "CTRL: running" if com_port else "CTRL: running (no port)"
            controller_status_label.text = text
            controller_status_label.style("color: #21BA45")
        logging.info("Controller started")
    except Exception as e:
        logging.error("Start controller failed: %s", e)


async def stop_controller() -> None:
    try:
        await server_manager.stop_controller()
        # disable status polling on disconnect and stop consumer
        global status_timer, status_consumer_task
        if status_timer:
            status_timer.active = False
        if status_consumer_task:
            status_consumer_task.cancel()
            with contextlib.suppress(Exception):
                await status_consumer_task
            status_consumer_task = None
        controller_state.running = False
        robot_state.connected = False
        if controller_status_label:
            controller_status_label.text = "CTRL: stopped"
            controller_status_label.style("color: #DB2828")
        logging.info("Controller stopped")
    except Exception as e:
        logging.error("Stop controller failed: %s", e)


async def send_clear_error() -> None:
    try:
        resp = await client.clear_error()
        ui.notify(resp, color="primary")
        logging.info(resp)
    except Exception as e:
        logging.error("CLEAR_ERROR failed: %s", e)


async def send_stop_motion() -> None:
    try:
        resp = await client.stop()
        ui.notify(resp, color="warning")
        logging.warning(resp)
    except Exception as e:
        logging.error("STOP failed: %s", e)


async def set_port(port_str: str) -> None:
    if not port_str:
        ui.notify("Provide a COM/tty port", color="warning")
        return
    try:
        resp = await client.set_com_port(port_str)
        ui.notify(resp, color="primary")
        logging.info(resp)
    except Exception as e:
        logging.error("SET_PORT failed: %s", e)


# --------------- Status polling ---------------


def _normalize_joint_progress(
    angle_deg: float, min_deg: float, max_deg: float
) -> float:
    if max_deg <= min_deg:
        return 0.0
    val = (angle_deg - min_deg) / (max_deg - min_deg)
    return max(0.0, min(1.0, val))


async def update_status_async() -> None:
    global status_busy, status_timer
    if status_busy:
        return
    status_busy = True

    # Rate-limited connectivity check via PING (provides SERIAL=0/1)
    global last_ping_ts, last_ping_ok
    _t = time.time()
    if (_t - last_ping_ts) >= PING_PERIOD_S:
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

    # Apply latest multicast snapshot (no gating)
    angles = robot_state.angles or []
    pose = robot_state.pose or []
    io = robot_state.io or []
    gr = robot_state.gripper or []
    serial_ok = bool(last_ping_ok)

    # Update Move page UI labels (angles + progress bars)
    if angles:
        if move_page_instance.joint_labels and len(angles) >= 6:
            for i, a in enumerate(angles[:6]):
                if i < len(move_page_instance.joint_labels):
                    move_page_instance.joint_labels[i].text = f"{a:.3f}"
        if move_page_instance.joint_progress_bars and len(angles) >= 6:
            for i, a in enumerate(angles[:6]):
                if i < len(move_page_instance.joint_progress_bars):
                    lim = (
                        JOINT_LIMITS_DEG[i]
                        if i < len(JOINT_LIMITS_DEG)
                        else [-180, 180]
                    )
                    move_page_instance.joint_progress_bars[i].value = round(
                        _normalize_joint_progress(a, lim[0], lim[1]), 3
                    )
        move_page_instance.update_urdf_angles(angles)

    if pose and len(pose) >= 12:
        # Pose matrix flattened; indices 3,7,11 as XYZ
        x, y, z = pose[3], pose[7], pose[11]
        if move_page_instance.tool_labels:
            if "X" in move_page_instance.tool_labels:
                move_page_instance.tool_labels["X"].text = f"{x:.3f}"
            if "Y" in move_page_instance.tool_labels:
                move_page_instance.tool_labels["Y"].text = f"{y:.3f}"
            if "Z" in move_page_instance.tool_labels:
                move_page_instance.tool_labels["Z"].text = f"{z:.3f}"

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

                rx_deg = math.degrees(rx)
                ry_deg = math.degrees(ry)
                rz_deg = math.degrees(rz)

                if "Rx" in move_page_instance.tool_labels:
                    move_page_instance.tool_labels["Rx"].text = f"{rx_deg:.3f}"
                if "Ry" in move_page_instance.tool_labels:
                    move_page_instance.tool_labels["Ry"].text = f"{ry_deg:.3f}"
                if "Rz" in move_page_instance.tool_labels:
                    move_page_instance.tool_labels["Rz"].text = f"{rz_deg:.3f}"

    if len(io) >= 5:
        in1, in2, out1, out2, estop = io[:5]
        estop_text = "OK" if estop else "TRIGGERED"

        # Update footer E-STOP
        if estop_label:
            estop_label.text = f"E-STOP: {estop_text}"
            estop_label.style("color: #21BA45" if estop else "color: #DB2828")

        # Update IO page labels
        if io_page_instance.io_in1_label:
            io_page_instance.io_in1_label.text = f"INPUT 1: {in1}"
        if io_page_instance.io_in2_label:
            io_page_instance.io_in2_label.text = f"INPUT 2: {in2}"
        if io_page_instance.io_out1_label:
            io_page_instance.io_out1_label.text = f"OUTPUT 1 is: {out1}"
        if io_page_instance.io_out2_label:
            io_page_instance.io_out2_label.text = f"OUTPUT 2 is: {out2}"
        if io_page_instance.io_estop_label2:
            io_page_instance.io_estop_label2.text = f"ESTOP: {estop_text}"

        # Update Move page IO summary
        if move_page_instance.io_summary_label:
            move_page_instance.io_summary_label.text = (
                f"IO: IN1={in1} IN2={in2} OUT1={out1} OUT2={out2} ESTOP={estop_text}"
            )
    else:
        # Clear labels on failure
        if estop_label:
            estop_label.text = "E-STOP: unknown"
            estop_label.style("color: inherit")
        if io_page_instance.io_in1_label:
            io_page_instance.io_in1_label.text = "INPUT 1: -"
        if io_page_instance.io_in2_label:
            io_page_instance.io_in2_label.text = "INPUT 2: -"
        if io_page_instance.io_out1_label:
            io_page_instance.io_out1_label.text = "OUTPUT 1 is: -"
        if io_page_instance.io_out2_label:
            io_page_instance.io_out2_label.text = "OUTPUT 2 is: -"
        if io_page_instance.io_estop_label2:
            io_page_instance.io_estop_label2.text = "ESTOP: unknown"
        if move_page_instance.io_summary_label:
            move_page_instance.io_summary_label.text = "IO: -"

    if len(gr) >= 6:
        gid, pos, spd, cur, status_b, obj = gr[:6]
        if gripper_page_instance.grip_id_label:
            gripper_page_instance.grip_id_label.text = f"Gripper ID is: {gid}"
        if gripper_page_instance.grip_pos_feedback_label:
            gripper_page_instance.grip_pos_feedback_label.text = (
                f"Gripper position feedback is: {pos}"
            )
        if gripper_page_instance.grip_current_feedback_label:
            gripper_page_instance.grip_current_feedback_label.text = (
                f"Gripper current feedback is: {cur}"
            )
        if gripper_page_instance.grip_obj_detect_label:
            gripper_page_instance.grip_obj_detect_label.text = (
                f"Gripper object detection is: {obj}"
            )
    else:
        if gripper_page_instance.grip_id_label:
            gripper_page_instance.grip_id_label.text = "Gripper ID is: -"
        if gripper_page_instance.grip_pos_feedback_label:
            gripper_page_instance.grip_pos_feedback_label.text = (
                "Gripper position feedback is: -"
            )
        if gripper_page_instance.grip_current_feedback_label:
            gripper_page_instance.grip_current_feedback_label.text = (
                "Gripper current feedback is: -"
            )
        if gripper_page_instance.grip_obj_detect_label:
            gripper_page_instance.grip_obj_detect_label.text = (
                "Gripper object detection is: -"
            )

    # Footer robot connectivity
    robot_state.connected = serial_ok
    if robot_status_label:
        robot_status_label.text = (
            "ROBOT: connected" if serial_ok else "ROBOT: disconnected"
        )
        robot_status_label.style("color: #21BA45" if serial_ok else "color: #DB2828")

    # Update calibrate page button state
    calibrate_page_instance._update_go_to_limit_button()
    status_busy = False
    return


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
            global estop_label, controller_status_label, robot_status_label
            controller_status_label = ui.label("CTRL: unknown").classes("text-sm")
            robot_status_label = ui.label("ROBOT: unknown").classes("text-sm")
            estop_label = ui.label("E-STOP: unknown").classes("text-sm")
        with ui.row().classes("items-center gap-2"):
            stored_port = ng_app.storage.user.get("com_port", DEFAULT_COM_PORT or "")
            com_input = ui.input(
                label="COM Port (COM5 / /dev/ttyACM0 / /dev/tty.usbmodem0)",
                value=stored_port,
            ).classes("w-80")

            # Persist port on edits
            com_input.on_value_change(
                lambda e: ng_app.storage.user.__setitem__(
                    "com_port", com_input.value or ""
                )
            )

            async def handle_set_port():
                ng_app.storage.user["com_port"] = com_input.value or ""
                await set_port(com_input.value or "")

            ui.button("Set Port", on_click=handle_set_port)
            ui.button("Clear error", on_click=send_clear_error).props("color=warning")
            ui.button("Stop", on_click=send_stop_motion).props("color=negative")


@ui.page("/")
def compose_ui() -> None:
    apply_theme(get_theme())
    ui.query(".nicegui-content").classes("p-0")
    inject_layout_css()

    # Build header and tabs with panels
    build_header_and_tabs()
    # 100hz control loop
    ng_app.storage.client["joint_jog_timer"] = ui.timer(
        interval=0.01, callback=move_page_instance.jog_tick, active=False
    )
    ng_app.storage.client["cart_jog_timer"] = ui.timer(
        interval=0.01, callback=move_page_instance.cart_jog_tick, active=False
    )

    # Attach logging handler to move page response log
    if move_page_instance.response_log:
        attach_ui_log(move_page_instance.response_log)

    build_footer()

    # Auto-connect using stored COM port
    try:
        port = ng_app.storage.user.get("com_port", DEFAULT_COM_PORT or "")
    except Exception:
        port = DEFAULT_COM_PORT or ""
    asyncio.create_task(start_controller(port))


status_timer = ui.timer(
    interval=0.05, callback=update_status_async, active=False
)  # status poll (gated)


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
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set log level",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable DEBUG logging"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Enable WARNING logging"
    )
    parser.add_argument(
        "--auto-start",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable automatic controller start (overrides PAROL6_AUTO_START env var)",
    )
    args, _ = parser.parse_known_args()

    # Resolve runtime values
    RUNTIME_SERVER_HOST = args.host
    RUNTIME_SERVER_PORT = int(args.port)
    RUNTIME_CONTROLLER_HOST = args.controller_host
    RUNTIME_CONTROLLER_PORT = int(args.controller_port)

    # Resolve AUTO_START: CLI flag overrides environment variable
    if args.auto_start is not None:
        RUNTIME_AUTO_START = args.auto_start

    client.host = RUNTIME_SERVER_HOST
    client.port = RUNTIME_SERVER_PORT

    # Resolve log level priority: explicit --log-level > -v/-q > env default from constants
    if args.log_level:
        RUNTIME_LOG_LEVEL = getattr(logging, args.log_level)
    elif args.verbose:
        RUNTIME_LOG_LEVEL = logging.DEBUG
    elif args.quiet:
        RUNTIME_LOG_LEVEL = logging.WARNING
    else:
        from app.constants import LOG_LEVEL as _ENV_LOG_LEVEL

        RUNTIME_LOG_LEVEL = _ENV_LOG_LEVEL

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
        storage_secret="unnecessary_for_now",
        loop="uvloop",
        http="httptools",
        ws="wsproto",
        binding_refresh_interval=0.05,
    )
