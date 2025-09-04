from __future__ import annotations

import asyncio
import logging

from nicegui import app as ng_app
from nicegui import ui

from app.common.logging_config import attach_ui_log, configure_logging
from app.common.theme import apply_theme, get_theme, inject_layout_css
from app.constants import (
    DEFAULT_COM_PORT,
    JOINT_LIMITS_DEG,
    LOG_LEVEL,
    PAROL6_OFFICIAL_DOC_URL,
    REPO_ROOT,
    UI_PORT,
)
from app.pages.calibrate import CalibratePage
from app.pages.gripper import GripperPage
from app.pages.io import IoPage
from app.pages.move import MovePage
from app.pages.settings import SettingsPage
from app.services.robot_client import client
from app.services.server_manager import server_manager
from app.state import robot_state

# Configure logging early so any startup issues are visible
configure_logging(LOG_LEVEL)

# Register static files for optimized icons and other assets
ng_app.add_static_files("/static", (REPO_ROOT / "app" / "static").as_posix())

# ------------------------ Global UI/state ------------------------

fw_version = "1.0.0"
estop_label: ui.label | None = None

# Status polling control (gated, non-blocking)
status_timer: ui.timer | None = None
status_busy = False
consecutive_failures = 0

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
        await server_manager.start_controller(com_port=com_port)
        # enable status polling now that we are connected
        global status_timer, consecutive_failures
        if status_timer:
            status_timer.active = True
        consecutive_failures = 0
        logging.info("Controller started")
    except Exception as e:
        logging.error("Start controller failed: %s", e)


async def stop_controller() -> None:
    try:
        await server_manager.stop_controller()
        # disable status polling on disconnect
        global status_timer, consecutive_failures
        if status_timer:
            status_timer.active = False
        consecutive_failures = 0
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


def _normalize_joint_progress(angle_deg: float, min_deg: float, max_deg: float) -> float:
    if max_deg <= min_deg:
        return 0.0
    val = (angle_deg - min_deg) / (max_deg - min_deg)
    return max(0.0, min(1.0, val))


async def update_status_async() -> None:
    global status_busy, status_timer, consecutive_failures
    if status_busy:
        return
    status_busy = True

    # run potentially blocking UDP call
    s = await client.get_status()
    if s:
        angles = s.get("angles") or []
        io = s.get("io") or []
        gr = s.get("gripper") or []
        pose = s.get("pose") or []

        # Update robot state
        robot_state.angles = angles or robot_state.angles
        robot_state.pose = pose or robot_state.pose
        robot_state.io = io or robot_state.io
        robot_state.gripper = gr or robot_state.gripper
        robot_state.connected = True

        # Update Move page UI labels
        if angles:
            if move_page_instance.joint_labels and len(angles) >= 6:
                for i, a in enumerate(angles[:6]):
                    if i < len(move_page_instance.joint_labels):
                        move_page_instance.joint_labels[i].text = f"{a:.3f}"
            if move_page_instance.joint_progress_bars and len(angles) >= 6:
                for i, a in enumerate(angles[:6]):
                    if i < len(move_page_instance.joint_progress_bars):
                        lim = JOINT_LIMITS_DEG[i] if i < len(JOINT_LIMITS_DEG) else [-180, 180]
                        move_page_instance.joint_progress_bars[i].value = round(
                            _normalize_joint_progress(a, lim[0], lim[1]), 3
                        )

            # Update URDF viewer with live joint angles
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
                gripper_page_instance.grip_obj_detect_label.text = "Gripper object detection is: -"

        # Update calibrate page go-to-limit button based on connection
        calibrate_page_instance._update_go_to_limit_button()

        # success: speed up polling (but keep reasonable)
        if status_timer and getattr(status_timer, "interval", None) != 0.2:
            status_timer.interval = 0.2
        consecutive_failures = 0
    else:
        robot_state.connected = False
        consecutive_failures += 1
        # slow down polling while offline to keep UI responsive
        if status_timer and getattr(status_timer, "interval", None) != 1.0:
            status_timer.interval = 1.0

        # Update calibrate page go-to-limit button based on disconnection
        calibrate_page_instance._update_go_to_limit_button()

    status_busy = False


def build_header_and_tabs() -> None:
    # Header with left navigation tabs, centered firmware text, right help + theme toggle
    with ui.header().classes("px-3 py-1"), ui.row().classes("w-full items-center justify-between"):
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
            global estop_label
            estop_label = ui.label("E-STOP: unknown").classes("text-sm")
        with ui.row().classes("items-center gap-2"):
            stored_port = ng_app.storage.user.get("com_port", DEFAULT_COM_PORT or "")
            com_input = ui.input(
                label="COM Port (COM5 / /dev/ttyACM0 / /dev/tty.usbmodem0)",
                value=stored_port,
            ).classes("w-80")

            # Persist port on edits
            com_input.on_value_change(
                lambda e: ng_app.storage.user.__setitem__("com_port", com_input.value or "")
            )

            async def handle_set_port():
                ng_app.storage.user["com_port"] = com_input.value or ""
                await set_port(com_input.value or "")

            ui.button("Set Port", on_click=handle_set_port)
            ui.button("Clear error", on_click=send_clear_error).props("color=warning")
            ui.button("Stop", on_click=lambda: asyncio.create_task(send_stop_motion())).props(
                "color=negative"
            )


@ui.page("/")
def compose_ui() -> None:
    apply_theme(get_theme())
    ui.query(".nicegui-content").classes("p-0")
    inject_layout_css()

    # Build header and tabs with panels
    build_header_and_tabs()
    # Need a 0.009 interval to acheive at least 100hz control loop
    ng_app.storage.client["joint_jog_timer"] = ui.timer(
        interval=0.009, callback=move_page_instance.jog_tick, active=False
    )
    ng_app.storage.client["cart_jog_timer"] = ui.timer(
        interval=0.009, callback=move_page_instance.cart_jog_tick, active=False
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
    interval=0.2, callback=update_status_async, active=False
)  # status poll (gated)

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="PAROL6 NiceGUI Commander",
        port=UI_PORT,
        reload=True,
        storage_secret="unnecessary_for_now",
        loop="uvloop",
        http="httptools",
        ws="wsproto",
    )
