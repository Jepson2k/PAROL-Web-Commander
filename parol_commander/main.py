import asyncio
import logging
import math
import time
import contextlib
import argparse
import os
import sys
from typing import Any, cast
from spatialmath import SO3
import numpy as np

from nicegui import app as ng_app
from nicegui import ui
from nicegui.elements.tooltip import Tooltip
from parol6 import ensure_server, ServerManager
from parol6.tools import TOOL_CONFIGS
from importlib.resources import files, as_file

from parol_commander.common.logging_config import (
    attach_ui_log,
    configure_logging,
    TRACE,
)
from parol_commander.common.theme import apply_theme, get_theme, inject_layout_css
from parol_commander.constants import (
    PAROL6_OFFICIAL_DOC_URL,
    SERVER_HOST,
    SERVER_PORT,
    CONTROLLER_HOST,
    CONTROLLER_PORT,
    AUTO_START,
    LOG_LEVEL,
    WEBAPP_CONTROL_INTERVAL_S,
)
from parol_commander.services.robot_client import client
from parol_commander.state import robot_state, controller_state, ui_state
from parol_commander.components.io import IoPage
from parol_commander.components.gripper import GripperPage
from parol_commander.components.settings import SettingsPage
from parol_commander.components.control import ControlPanel
from parol_commander.components.readout import ReadoutPanel
from parol_commander.components.editor import EditorPanel
from parol_commander.services.script_runner import ScriptProcessHandle
from parol_commander.services.urdf_scene import (
    UrdfScene,
    UrdfSceneConfig,
    ToolPose,
    GizmoEvent,
    GizmoEventKind,
)
from importlib.resources import files as pkg_files


# Runtime configuration (resolved later from CLI/env)
RUNTIME_SERVER_HOST = SERVER_HOST
RUNTIME_SERVER_PORT = SERVER_PORT
RUNTIME_CONTROLLER_HOST = CONTROLLER_HOST
RUNTIME_CONTROLLER_PORT = CONTROLLER_PORT
RUNTIME_AUTO_START = AUTO_START

STATIC_DIR = pkg_files("parol_commander").joinpath("static")
ng_app.add_static_files("/static", str(STATIC_DIR))

# ------------------------ Global UI/state ------------------------

fw_version = "1.0.0"
controller_status_label: ui.label | None = None
robot_status_label: ui.label | None = None

ctrl_tooltip: Tooltip | None = None
robot_tooltip: Tooltip | None = None

# Multicast-driven status consumer (runs once per app)
status_consumer_task: asyncio.Task | None = None
# Connectivity ping timer (1Hz)
ping_timer: ui.timer | None = None
last_ping_ok: bool = False

# Component instances
control_panel = ControlPanel()
readout_panel = ReadoutPanel()
editor_panel = EditorPanel()
io_page: IoPage | None = None
gripper_page: GripperPage | None = None
settings_page: SettingsPage | None = None

# UI state
urdf_scene = None
response_log: ui.log | None = None
joint_jog_timer: ui.timer | None = None
cart_jog_timer: ui.timer | None = None
script_handle: ScriptProcessHandle | None = None
script_running: bool = False
bottom_log_drawer: ui.element | None = None

# Main tabs reference for tab_panels
main_tabs = None
server_manager: ServerManager | None = None

# --------------- URDF Scene Functions ---------------


async def initialize_urdf_scene(container=None) -> None:
    """Initialize the URDF scene with error handling."""
    # Resolve URDF from installed parol6 package and clear container if provided
    urdf_res = files("parol6") / "urdf_model" / "urdf" / "PAROL6.urdf"
    if container:
        container.clear()

    with as_file(urdf_res) as urdf_path:
        assert urdf_path
        # Detect theme and set appropriate colors
        mode = get_theme()
        is_dark = mode != "light"
        bg_color = "#212121" if is_dark else "#eeeeee"
        material_color = "#9ca3af" if is_dark else "#666666"

        # Update config with theme-aware colors
        ui_state.urdf_config["background_color"] = bg_color
        ui_state.urdf_config["material"] = material_color

        # Create tool pose resolver to bridge PAROL6 TOOL_CONFIGS
        def tool_pose_resolver(tool: str) -> ToolPose | None:
            """Convert PAROL6 tool config to ToolPose format."""
            if not tool or tool.upper() == "NONE":
                return None
            cfg = TOOL_CONFIGS.get(tool, {})
            if not isinstance(cfg, dict):
                return None
            # Prefer nested 'tcp' dict if available
            origin = [0.0, 0.0, 0.0]
            rpy = [0.0, 0.0, 0.0]
            if isinstance(cfg.get("tcp"), dict):
                o_val = cfg["tcp"].get("origin", origin)
                r_val = cfg["tcp"].get("rpy", rpy)
                origin = cast(list[float], o_val)
                rpy = cast(list[float], r_val)
            else:
                # Fallback to top-level keys if present
                origin = cast(
                    list[float], cfg.get("tcp_origin", cfg.get("origin", origin))
                )
                rpy = cast(list[float], cfg.get("tcp_rpy", cfg.get("rpy", rpy)))
            return ToolPose(origin=origin, rpy=rpy)

        # Create UrdfScene config with tool pose resolver and larger gizmo scale
        urdf_config = UrdfSceneConfig(
            tool_pose_resolver=tool_pose_resolver,
            gizmo_scale=1.35,  # Make gizmo larger (1.0 = default STL scale)
        )

        # Create new scene with config
        ui_state.urdf_scene = UrdfScene(urdf_path, config=urdf_config)
        # Render the scene inside the provided container if available
        if container:
            with container:
                ui_state.urdf_scene.show(
                    material=ui_state.urdf_config.get("material"),
                    background_color=ui_state.urdf_config.get(
                        "background_color", "#eee"
                    ),
                )
        else:
            ui_state.urdf_scene.show(
                material=ui_state.urdf_config.get("material"),
                background_color=ui_state.urdf_config.get("background_color", "#eee"),
            )

        ui_state.urdf_scene.set_control_frame(ui_state.frame)
        ui_state.urdf_scene.set_gizmo_visible(ui_state.gizmo_visible)

        # Hook gizmo events
        def _handle_gizmo_event(event: GizmoEvent) -> None:
            # Bridge to control panel directly with client context
            is_pressed = event.kind == GizmoEventKind.PRESS
            if ui_state.client:
                with ui_state.client:
                    asyncio.create_task(
                        control_panel.set_axis_pressed(event.handle, is_pressed)
                    )

        ui_state.urdf_scene.on_gizmo_event(_handle_gizmo_event)

        # Align TCP to current tool from controller
        try:
            result = await client.get_tool()
            tool_val = result.get("tool") if isinstance(result, dict) else None
            if tool_val:
                ui_state.urdf_scene.update_tcp_pose_from_tool(tool_val)
        except Exception as _e:
            logging.error("Failed to sync TCP tool pose: %s", _e)

        # Override the scene height and set closer camera position
        if ui_state.urdf_scene.scene:
            scene: ui.scene = ui_state.urdf_scene.scene
            scene._props["grid"] = (10, 100)
            # Fill parent container (absolute canvas): width/height 100%
            scene.classes(remove="h-[66vh]").style(
                "width: 100%; height: 100%; margin: 0; display: block;"
            )
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
            scene.line([0, 0, 0], [world_axes_size, 0, 0]).material("#ff0000")  # X
            scene.line([0, 0, 0], [0, world_axes_size, 0]).material("#00ff00")  # Y
            scene.line([0, 0, 0], [0, 0, world_axes_size]).material("#0000ff")  # Z

    # Cache joint names for mapping
    if hasattr(ui_state.urdf_scene, "get_joint_names"):
        ui_state.urdf_joint_names = list(ui_state.urdf_scene.get_joint_names())
    else:
        # Fallback to expected joint names
        ui_state.urdf_joint_names = ui_state.urdf_config.get(
            "joint_name_order", ["L1", "L2", "L3", "L4", "L5", "L6"]
        )

    logging.info("URDF scene initialized with joints: %s", ui_state.urdf_joint_names)

    # Sync gizmo settings to URDF scene now that it's ready
    control_panel.sync_gizmo_to_urdf()


def update_urdf_angles(angles_deg: list[float]) -> None:
    """Update URDF scene with new joint angles (degrees -> radians)."""
    if not ui_state.urdf_scene:
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
        angle_signs = ui_state.urdf_config.get("angle_signs", [1, 1, 1, 1, 1, 1])
        for i in range(6):
            if i < len(ui_state.urdf_index_mapping) and ui_state.urdf_index_mapping[
                i
            ] < len(valid_angles):
                controller_idx = ui_state.urdf_index_mapping[i]
                angle_deg = valid_angles[controller_idx]
                # Apply sign correction and offset
                sign = (
                    1
                    if controller_idx >= len(angle_signs)
                    else (1 if angle_signs[controller_idx] >= 0 else -1)
                )
                angle_offsets = ui_state.urdf_config.get(
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
                    if ui_state.urdf_config.get("deg_to_rad", True)
                    else angle_deg_corrected
                )
                angles_rad.append(angle_rad)
            else:
                angles_rad.append(0.0)

        # Create ordered list of angles based on URDF joint names
        if hasattr(ui_state.urdf_scene, "set_axis_values") and hasattr(
            ui_state.urdf_scene, "joint_names"
        ):
            urdf_joint_names = list(ui_state.urdf_scene.joint_names)
            angles_ordered = []

            for joint_name in urdf_joint_names:
                # Map URDF joint name back to our controller index
                try:
                    urdf_idx = ui_state.urdf_config["joint_name_order"].index(
                        joint_name
                    )
                    if urdf_idx < len(angles_rad):
                        angles_ordered.append(angles_rad[urdf_idx])
                    else:
                        angles_ordered.append(0.0)
                except (ValueError, KeyError):
                    angles_ordered.append(0.0)

            # Pass list of float values in the order expected by the library
            ui_state.urdf_scene.set_axis_values(angles_ordered)

    except Exception as e:
        logging.error("Failed to update URDF angles: %s", e)


async def on_tool_changed(new_tool: str) -> None:
    """Handle tool selection change."""
    try:
        # Send command to server to update tool
        await client.set_tool(new_tool)

        # Update visualization
        await update_tool_visualization(new_tool)

        # Align TCP markers to the new tool (if supported by scene)
        try:
            if ui_state.urdf_scene and hasattr(
                ui_state.urdf_scene, "update_tcp_pose_from_tool"
            ):
                ui_state.urdf_scene.update_tcp_pose_from_tool(new_tool)
        except Exception as _e:
            logging.error("Failed to update TCP pose after tool change: %s", _e)

        ui.notify(f"Tool changed to: {new_tool}", color="positive")
        logging.info(f"Tool changed to: {new_tool}")
    except Exception as e:
        ui.notify(f"Tool change failed: {e}", color="negative")
        logging.error(f"Tool change failed: {e}")


async def update_tool_visualization(tool_name: str) -> None:
    """Dynamically swap tool STLs in 3D scene when supported."""
    if not ui_state.urdf_scene:
        return
    # If using a standalone Three.js viewer, skip STL swapping for now
    if not hasattr(ui_state.urdf_scene, "scene") or not hasattr(
        ui_state.urdf_scene, "joint_groups"
    ):
        logging.info(
            "Custom URDF viewer active; skipping tool STL swap for %s", tool_name
        )
        return

    try:
        # Remove existing tool STLs
        for stl in ui_state.current_tool_stls:
            try:
                stl.delete()
            except Exception as e:
                logging.warning("Failed to delete STL: %s", e)
        ui_state.current_tool_stls.clear()

        # Get tool configuration
        tool_config = TOOL_CONFIGS.get(tool_name, {})
        stl_files = tool_config.get("stl_files", [])

        if not stl_files:
            logging.info(f"No STLs for tool: {tool_name}")
            return

        # Get L6 group (last joint group where tools attach) - only for NiceGUI scene backend
        l6_group = getattr(ui_state.urdf_scene, "joint_groups", {}).get("L6")

        if not l6_group:
            logging.warning("Could not find L6 joint group for tool attachment")
            return

        # Add new tool STLs (NiceGUI scene backend only)
        scene = cast(Any, getattr(ui_state.urdf_scene, "scene", None))
        if scene is None:
            logging.warning("NiceGUI scene missing; skipping STL tool add")
            return
        material_color = ui_state.urdf_config.get("material", "#888")

        for stl_info in stl_files:
            with l6_group:
                stl = scene.stl(f"/meshes/parol6/{stl_info['file']}").scale(
                    ui_state.urdf_config.get("scale_stls", 1.0)
                )

                # Apply material color
                stl.material(material_color)

                # Apply origin and rotation if specified
                origin = stl_info.get("origin", [0, 0, 0])
                rpy = stl_info.get("rpy", [0, 0, 0])
                stl.move(*origin).rotate(*rpy)

                ui_state.current_tool_stls.append(stl)

        logging.info(
            f"Tool visualization updated: {tool_name} ({len(ui_state.current_tool_stls)} STLs)"
        )
    except Exception as e:
        logging.error(f"Failed to update tool visualization: {e}")


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
    global script_handle, script_running
    try:
        if value == "Simulator":
            # Stop user's script if running (GUI safety)
            if script_running and script_handle:
                script_handle["proc"].terminate()
                script_handle = None
                script_running = False
            await client.simulator_on()
            robot_state.simulator_active = True
            control_panel._show_sim_banner()
            # Enable after switching to simulator
            with contextlib.suppress(Exception):
                await asyncio.sleep(0.05)  # Brief delay for transport swap
                await client.enable()
        else:
            await client.simulator_off()
            robot_state.simulator_active = False
            control_panel._hide_sim_banner()
            # Enable after switching back to robot mode
            with contextlib.suppress(Exception):
                await asyncio.sleep(0.05)  # Brief delay for transport swap
                await client.enable()

        # Update button visuals to match mode
        if callable(getattr(control_panel, "_update_robot_btn_visual", None)):
            control_panel._update_robot_btn_visual()

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
            # Parse format: PONG|SERIAL={0|1}
            if "SERIAL=" in pong:
                serial_part = pong.split("SERIAL=", 1)[-1].split("|")[0].strip()
                serial = serial_part.startswith("1")
        elif isinstance(pong, dict):
            payload = pong.get("payload")
            if isinstance(payload, dict):
                val = payload.get("serial") or payload.get("serial_connected")
                if isinstance(val, (int, bool)):
                    serial = bool(val)

        # Robot connected = physical hardware connected (SERIAL=1)
        # Note: SERIAL=0 in simulator mode, so connected will be False
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
def update_ui_from_status() -> None:
    """Update UI elements from robot_state (called from multicast consumer)"""
    angles = robot_state.angles or []
    pose = robot_state.pose or []
    io = robot_state.io or []
    gr = robot_state.gripper or []

    # Update URDF scene with new angles using proper update function with mapping/signs
    if angles:
        update_urdf_angles(angles)

    if pose and len(pose) >= 12:
        # Pose matrix flattened; indices 3,7,11 as XYZ (always from multicast as WRF)
        # If TRF mode, transform to tool frame representation
        if len(pose) >= 16:
            try:
                # Extract rotation matrix (row-major: rows are [0,1,2], [4,5,6], [8,9,10])
                R = np.array(
                    [
                        [pose[0], pose[1], pose[2]],
                        [pose[4], pose[5], pose[6]],
                        [pose[8], pose[9], pose[10]],
                    ]
                )

                # Extract translation (mm)
                t_mm = np.array([pose[3], pose[7], pose[11]])

                # Check frame mode
                if ui_state.frame == "TRF":
                    # Compute inverse transform: T_inv = [R^T | -R^T * t]
                    R_inv = R.T
                    t_m = t_mm / 1000.0  # Convert to meters for computation
                    t_inv_m = -(R_inv @ t_m)
                    t_inv_mm = t_inv_m * 1000.0  # Back to mm

                    # Update state with TRF values
                    robot_state.x = float(t_inv_mm[0])
                    robot_state.y = float(t_inv_mm[1])
                    robot_state.z = float(t_inv_mm[2])
                    robot_state.rx, robot_state.ry, robot_state.rz = SO3(R_inv).rpy(
                        order="xyz", unit="deg"
                    )
                else:
                    # WRF: use original values
                    robot_state.x = float(t_mm[0])
                    robot_state.y = float(t_mm[1])
                    robot_state.z = float(t_mm[2])
                    robot_state.rx, robot_state.ry, robot_state.rz = SO3(R).rpy(
                        order="xyz", unit="deg"
                    )
            except Exception:
                # Fallback to zeros on extraction error
                robot_state.x = 0.0
                robot_state.y = 0.0
                robot_state.z = 0.0
                robot_state.rx = 0.0
                robot_state.ry = 0.0
                robot_state.rz = 0.0
        else:
            # Partial pose data, extract translation only
            robot_state.x = float(pose[3])
            robot_state.y = float(pose[7])
            robot_state.z = float(pose[11])

    if len(io) >= 5:
        in1, in2, out1, out2, estop = io[:5]

        # Push IO derived fields into bindable RobotState
        robot_state.io_in1 = int(in1)
        robot_state.io_in2 = int(in2)
        robot_state.io_out1 = int(out1)
        robot_state.io_out2 = int(out2)
        robot_state.io_estop = int(estop)

    if len(gr) >= 6:
        gid, pos, spd, cur, status_b, obj = gr[:6]
        # Push gripper derived fields into bindable RobotState
        robot_state.grip_id = int(gid)
        robot_state.grip_pos = int(pos)
        robot_state.grip_speed = int(spd)
        robot_state.grip_current = int(cur)
        robot_state.grip_obj = int(obj)

    # Monitor E-STOP state changes and show/hide dialog as needed
    control_panel.check_estop_state_change()


def toggle_bottom_log() -> None:
    """Toggle bottom log panel visibility."""
    global bottom_log_drawer, root_container
    drawer = bottom_log_drawer
    if isinstance(drawer, ui.element):
        is_open = "open" in drawer._classes
        if is_open:
            drawer.classes(remove="open")
        else:
            drawer.classes(add="open")


def build_page_content() -> None:
    """Build the Move page UI inline"""
    global \
        urdf_scene, \
        response_log, \
        bottom_log_drawer, \
        io_page, \
        gripper_page, \
        settings_page

    # Add Lottie player script for E-STOP dialog animations (load in HEAD early)
    ui.add_head_html(
        '<script type="module" defer src="https://unpkg.com/@lottiefiles/lottie-player@latest/dist/lottie-player.js"></script>'
    )

    with ui.column().classes("relative w-screen h-screen overflow-hidden gap-0"):
        with ui.column().classes("absolute inset-0 z-0"):
            # Initialize URDF scene
            async def _init():
                global urdf_scene
                try:
                    await initialize_urdf_scene()
                    urdf_scene = ui_state.urdf_scene
                except Exception as e:
                    logging.error("URDF init failed: %s", e)

            ui.timer(0.05, _init, once=True)

        # Main content area - flex column to share space between left and bottom panels
        with (
            ui.column()
            .classes("absolute inset-0 z-10 flex flex-col")
            .style("pointer-events: none;")
        ):
            # Left-side vertical tabs and panels - grows to fill available space
            with (
                ui.row()
                .classes("flex-1 min-h-0 relative")
                .style("pointer-events: none;")
            ):
                with ui.tabs().props("vertical").style(
                    "background: transparent !important; pointer-events: auto;"
                ).classes("absolute left-0 top-0 bottom-0 w-[72px] z-30") as side_tabs:
                    ui.tab(name="program", label="", icon="code")
                    ui.tab(name="io", label="", icon="settings_input_component")
                    ui.tab(name="settings", label="", icon="settings")
                    ui.tab(name="gripper", label="", icon="pan_tool_alt")
                    ui.element(tag="q-route-tab").props(
                        f"icon='help' href='{PAROL6_OFFICIAL_DOC_URL}'"
                    ).tooltip("Open PAROL6 documentation")

                # Left content wrapper: reserve space for tabs (72px) and fill remaining area
                with ui.column().classes(
                    "pl-[72px] w-full h-full overflow-hidden"
                ).style("pointer-events: none;") as left_wrap:
                    with ui.tab_panels(side_tabs, value=None).props(
                        "vertical animated transition-prev=slide-up transition-next=slide-down"
                    ).classes("left-panels h-auto max-h-full overflow-auto").style(
                        "pointer-events: none;"
                    ) as left_panels:

                        def close_left_panels():
                            side_tabs.value = None
                            left_panels.value = None

                        with ui.tab_panel("program").classes(
                            "overlay-card overflow-hidden"
                        ):
                            with ui.row().classes("w-full"):
                                ui.label("Program").classes("text-lg font-medium")
                                ui.space()
                                ui.button(
                                    icon="close", on_click=close_left_panels
                                ).props("flat round dense color=white")
                            with (
                                ui.element("div")
                                .classes("overflow-y-auto")
                                .style("max-height: calc(100vh - 120px);")
                            ):
                                editor_panel.build()

                        with ui.tab_panel("io").classes("overlay-card overflow-hidden"):
                            with ui.row().classes("w-full"):
                                ui.label("I/O").classes("text-lg font-medium")
                                ui.space()
                                ui.button(
                                    icon="close", on_click=close_left_panels
                                ).props("flat round dense color=white")
                            with (
                                ui.element("div")
                                .classes("overflow-y-auto")
                                .style("max-height: calc(100vh - 120px);")
                            ):
                                io_page = io_page or IoPage()
                                io_page.build()

                        with ui.tab_panel("settings").classes(
                            "overlay-card overflow-hidden"
                        ):
                            with ui.row().classes("w-full"):
                                ui.label("Settings").classes("text-lg font-medium")
                                ui.space()
                                ui.button(
                                    icon="close", on_click=close_left_panels
                                ).props("flat round dense color=white")
                            with (
                                ui.element("div")
                                .classes("overflow-y-auto")
                                .style("max-height: calc(100vh - 120px);")
                            ):
                                settings_page = settings_page or SettingsPage()
                                settings_page.build()

                        with ui.tab_panel("gripper").classes(
                            "overlay-card overflow-hidden"
                        ):
                            with ui.row().classes("w-full"):
                                ui.label("Gripper").classes("text-lg font-medium")
                                ui.space()
                                ui.button(
                                    icon="close", on_click=close_left_panels
                                ).props("flat round dense color=white")
                            with (
                                ui.element("div")
                                .classes("overflow-y-auto")
                                .style("max-height: calc(100vh - 120px);")
                            ):
                                gripper_page = gripper_page or GripperPage()
                                gripper_page.build()

                        # Bind left tab changes to enable only when a left panel is open
                        def update_left_layout():
                            has_left = bool(side_tabs.value)
                            if has_left:
                                # Enable interactions inside the left panels area
                                left_wrap.style("pointer-events: auto;")
                                left_panels.style("pointer-events: auto;")
                            else:
                                # Disable the entire left content area so clicks pass through to the scene
                                left_wrap.style("pointer-events: none;")
                                left_panels.style("pointer-events: none;")

                        side_tabs.on(
                            "update:model-value", lambda e: update_left_layout()
                        )
                        side_tabs.on(
                            "update:modelValue", lambda e: update_left_layout()
                        )
                        # Initial sync to avoid invisible overlay blocking scene
                        update_left_layout()

            # Bottom vertical tabs and panels - anchored at bottom-left
            with ui.column().classes("absolute bottom-0 left-0 z-40"):
                # Tabs stay at bottom corner
                with ui.row().classes(
                    "absolute bottom-0 left-0 z-50 pointer-events-auto"
                ):
                    with ui.tabs().props("vertical").style(
                        "background: transparent !important"
                    ) as bottom_tabs:
                        ui.tab(name="response", label="", icon="article").tooltip("Log")

                # Panels positioned above tabs, offset to right of tab column
                with ui.tab_panels(bottom_tabs, value=None).props(
                    "vertical animated transition-prev=slide-up transition-next=slide-down"
                ).classes("bottom-panels").style(
                    "position: absolute; bottom: 12px; left: 72px; height: 50vh; width: calc(50vw - 72px); pointer-events: none;"
                ) as bottom_panels:

                    def close_bottom_panels():
                        bottom_tabs.value = None
                        bottom_panels.value = None
                        # Ensure top panels restore to full height and bottom panel stops intercepting
                        left_wrap.style("height: 100%;")
                        bottom_panels.style("pointer-events: none;")
                        # Re-run layout sync to be extra sure state is consistent
                        if "update_bottom_layout" in locals():
                            update_bottom_layout()

                    with ui.tab_panel("response").classes("overlay-card h-full w-full"):
                        with ui.row().classes("w-full"):
                            ui.label("Log").classes("text-lg font-medium")
                            ui.space()
                            ui.button(icon="close", on_click=close_bottom_panels).props(
                                "flat round dense color=white"
                            )
                        response_log = ui.log(max_lines=1000).classes(
                            "w-full whitespace-pre-wrap break-words h-full"
                        )

                        # Bind bottom tab changes to adjust layout and interactivity
                        def update_bottom_layout():
                            is_open = bool(bottom_tabs.value)
                            if is_open:
                                left_wrap.style("height: calc(100% - 50vh - 12px);")
                                bottom_panels.style("pointer-events: auto;")
                            else:
                                left_wrap.style("height: 100%;")
                                bottom_panels.style("pointer-events: none;")

                        # Bind to both event casings to ensure compatibility
                        bottom_tabs.on(
                            "update:model-value", lambda e: update_bottom_layout()
                        )
                        bottom_tabs.on(
                            "update:modelValue", lambda e: update_bottom_layout()
                        )

                        # Initial sync on load to avoid stale half-height state
                        update_bottom_layout()

        # Top-right HUD: Pose readouts
        readout_panel.build("tr")

        # Bottom-right HUD: control panel
        control_panel.build("br")


@ng_app.on_startup
async def _on_startup() -> None:
    # Start controller and streaming on server startup
    try:
        port = ng_app.storage.general.get("com_port", "")
    except Exception:
        port = ""
    try:
        if not controller_state.running:
            await start_controller(port)

        # Honor runtime flags on startup
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
        if require_ready:
            with contextlib.suppress(Exception):
                await client.wait_for_server_ready(timeout=3.0)
                await client.stream_on()

        if not port and auto_sim:
            with contextlib.suppress(Exception):
                await client.simulator_on()
                robot_state.simulator_active = True
                await asyncio.sleep(0.05)
                await client.enable()

    except Exception as e:
        logging.error("App startup init failed: %s", e)


@ng_app.on_shutdown
async def _on_shutdown() -> None:
    # Clean shutdown of controller when server stops
    with contextlib.suppress(Exception):
        await stop_controller()


@ui.page("/")
async def index_page():
    global ping_timer, joint_jog_timer, cart_jog_timer
    # Theme and layout
    apply_theme(get_theme())
    ui.query(".nicegui-content").classes("p-0")
    inject_layout_css()

    # Build UI
    build_page_content()

    # Store client context for background tasks
    ui_state.client = ui.context.client

    # Determine initial mode based on connectivity
    try:
        # Quick ping to check if robot is connected
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

        robot_state.connected = bool(serial)

        # Default to Simulator mode if not connected
        if not serial:
            robot_state.simulator_active = True
            with contextlib.suppress(Exception):
                await client.simulator_on()
            control_panel._show_sim_banner()
        else:
            robot_state.simulator_active = False
            control_panel._hide_sim_banner()

        # Update button visuals to match mode
        if callable(getattr(control_panel, "_update_robot_btn_visual", None)):
            control_panel._update_robot_btn_visual()
    except Exception as e:
        logging.warning(
            "Initial connectivity check failed: %s - defaulting to Simulator mode", e
        )
        robot_state.connected = False
        robot_state.simulator_active = True
        with contextlib.suppress(Exception):
            await client.simulator_on()
        control_panel._show_sim_banner()
        if callable(getattr(control_panel, "_update_robot_btn_visual", None)):
            control_panel._update_robot_btn_visual()

    # Create jog timers
    joint_jog_timer = ui.timer(
        interval=WEBAPP_CONTROL_INTERVAL_S,
        callback=control_panel.jog_tick,
        active=False,
    )
    cart_jog_timer = ui.timer(
        interval=WEBAPP_CONTROL_INTERVAL_S,
        callback=control_panel.cart_jog_tick,
        active=False,
    )

    # Wire timers to ui_state so control panel can access them
    ui_state.joint_jog_timer = joint_jog_timer
    ui_state.cart_jog_timer = cart_jog_timer

    # Attach logging handler to response log
    if response_log:
        attach_ui_log(response_log)

    # Page-scoped connectivity check (1 Hz)
    ping_timer = ui.timer(interval=1.0, callback=check_ping, active=True)


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

                # Propagate task fields from STATUS if present
                robot_state.action_current = status.get("action_current") or ""
                robot_state.action_state = status.get("action_state") or "IDLE"
                robot_state.last_update_ts = time.time()

                # Update UI directly from multicast consumer
                update_ui_from_status()
            except Exception as e:
                logging.debug("Status consumer parse error: %s", e)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error("Status consumer error: %s", e)


def main():
    global \
        RUNTIME_SERVER_HOST, \
        RUNTIME_SERVER_PORT, \
        RUNTIME_CONTROLLER_HOST, \
        RUNTIME_CONTROLLER_PORT, \
        RUNTIME_AUTO_START, \
        RUNTIME_LOG_LEVEL
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
        reload=True,
        show=False,
        loop="uvloop" if sys.platform != "win32" else "asyncio",
        http="httptools",
        binding_refresh_interval=0.05,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
