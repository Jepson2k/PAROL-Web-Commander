import asyncio
import atexit
import json
import logging
import math
import os
import signal
import sys
import time
import contextlib
import argparse
from typing import cast, Callable
import numpy as np

from nicegui import app as ng_app
from nicegui import ui
from nicegui.elements.tooltip import Tooltip
from parol6 import AsyncRobotClient, ServerManager, is_server_running, manage_server
from parol6.server.loop_timer import LoopMetrics, PhaseMetrics, format_hz_summary
from parol6.tools import TOOL_CONFIGS
from importlib.resources import as_file

from parol_commander.common.logging_config import (
    attach_ui_log,
    configure_logging,
    TRACE,
)
from parol_commander.common.theme import (
    apply_theme,
    get_theme,
    inject_layout_css,
    PANEL_RESIZE_CONFIG,
    SceneColors,
    StatusColors,
)
from parol_commander.constants import (
    config,
)

from parol_commander.state import (
    robot_state,
    controller_state,
    ui_state,
    readiness_state,
    global_phase_timer,
)
from parol_commander.components.io import IoPage
from parol_commander.components.gripper import GripperPage
from parol_commander.components.settings import SettingsContent
from parol_commander.components.control import ControlPanel
from parol_commander.components.readout import ReadoutPanel
from parol_commander.components.editor import EditorPanel
from parol_commander.components.help_menu import help_menu
from parol_commander.services.urdf_scene import (
    UrdfScene,
    UrdfSceneConfig,
    ToolPose,
)
from parol_commander.services.keybindings import keybindings_manager, Keybinding
from parol_commander.services.path_visualizer import warm_process_pool
from parol_commander.numba_pipelines import (
    angle_pipeline,
    pose_extraction_pipeline,
    warmup_pipelines,
)
from importlib.resources import files as pkg_files

STATIC_DIR = pkg_files("parol_commander").joinpath("static")
ng_app.add_static_files("/static", str(STATIC_DIR))

# ------------------------ Global UI/state ------------------------

# Global client instance - initialized in main() after CLI parsing
client: AsyncRobotClient

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
control_panel: ControlPanel
readout_panel: ReadoutPanel
editor_panel: EditorPanel
io_page: IoPage | None = None
gripper_page: GripperPage | None = None
settings_page: SettingsContent | None = None

# UI state
urdf_scene = None
response_log: ui.log | None = None
joint_jog_timer: ui.timer | None = None
cart_jog_timer: ui.timer | None = None
bottom_log_drawer: ui.element | None = None

# Main tabs reference for tab_panels
server_manager: ServerManager | None = None

# Persistent connection warning notification
_connection_notification: ui.notification | None = None

# Pre-allocated buffers for numba pipelines (scratch space)
_rotation_matrix_buffer: np.ndarray = np.zeros((3, 3), dtype=np.float64)
_rpy_rad_buffer: np.ndarray = np.zeros(3, dtype=np.float64)
_angles_ordered_buffer: np.ndarray = np.zeros(6, dtype=np.float64)
_pose_result_buffer: np.ndarray = np.zeros(6, dtype=np.float64)  # [x,y,z,rx,ry,rz]
_DEG_TO_RAD: float = math.pi / 180.0

# Angle pipeline config arrays - populated from urdf_scene.config when available
_angle_signs_array: np.ndarray = np.ones(6, dtype=np.float64)
_angle_offsets_array: np.ndarray = np.zeros(6, dtype=np.float64)
_index_mapping_array: np.ndarray = np.arange(6, dtype=np.int32)
_urdf_reorder_array: np.ndarray = np.arange(6, dtype=np.int32)
_angle_pipeline_config_valid: bool = False

# Frontend timing metrics (unified via LoopMetrics)
_ui_metrics = LoopMetrics()
_work_metrics = PhaseMetrics()
_wait_metrics = PhaseMetrics()
_last_work_end: float = 0.0


def _update_connection_notification() -> None:
    """Show or dismiss persistent notification based on robot connection state."""
    global _connection_notification

    # Skip if page not ready - avoid modifying elements during page serialization
    if not readiness_state.page_ready.is_set():
        return

    needs_warning = not robot_state.simulator_active and not robot_state.connected

    if needs_warning and _connection_notification is None:
        _connection_notification = ui.notification(
            message="Robot mode requires a hardware connection. Connect robot or switch to Simulator mode.",
            type="negative",
            close_button=True,
            timeout=0,
        )
    elif not needs_warning and _connection_notification is not None:
        _connection_notification.dismiss()
        _connection_notification = None


# --------------- URDF Scene Functions ---------------
async def initialize_urdf_scene() -> None:
    """Initialize the URDF scene with error handling."""
    # Resolve URDF from installed parol6 package and clear container if provided
    urdf_res = pkg_files("parol6") / "urdf_model" / "urdf" / "PAROL6.urdf"

    with as_file(urdf_res) as urdf_path:
        assert urdf_path
        # Detect theme and set appropriate colors
        mode = get_theme()
        is_dark = mode != "light"
        bg_color = (
            SceneColors.BACKGROUND_DARK_HEX
            if is_dark
            else SceneColors.BACKGROUND_LIGHT_HEX
        )
        material_color = (
            SceneColors.MATERIAL_DARK_HEX if is_dark else SceneColors.MATERIAL_LIGHT_HEX
        )

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

        # Create UrdfScene config with all settings
        scene_config = UrdfSceneConfig(
            tool_pose_resolver=tool_pose_resolver,
            gizmo_scale=1.35,  # Make gizmo larger (1.0 = default STL scale)
            package_map={"parol6": urdf_path.parent.parent},
            # Appearance settings
            material=material_color,
            background_color=bg_color,
            sim_color=SceneColors.SIM_AMBER_HEX,
            sim_opacity=0.9,
            # Kinematic mapping settings (defaults are fine for PAROL6)
        )

        # Create new scene with config
        ui_state.urdf_scene = UrdfScene(urdf_path, config=scene_config)
        ui_state.urdf_scene.show(
            material=scene_config.material,
            background_color=scene_config.background_color,
        )

        ui_state.urdf_scene.set_gizmo_visible(ui_state.gizmo_visible)

        # Align TCP to current tool from controller
        try:
            result = await client.get_tool()
            tool_val = result.get("tool") if isinstance(result, dict) else None
            if tool_val:
                ui_state.urdf_scene.update_tcp_pose_from_tool(tool_val)
        except Exception as e:
            logging.error("Failed to sync TCP tool pose: %s", e)

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
            scene.line([0, 0, 0], [world_axes_size, 0, 0]).material(
                SceneColors.AXIS_X_HEX
            )  # X
            scene.line([0, 0, 0], [0, world_axes_size, 0]).material(
                SceneColors.AXIS_Y_HEX
            )  # Y
            scene.line([0, 0, 0], [0, 0, world_axes_size]).material(
                SceneColors.AXIS_Z_HEX
            )  # Z

    # Cache joint names for mapping
    ui_state.urdf_joint_names = list(ui_state.urdf_scene.get_joint_names())

    logging.info("URDF scene initialized with joints: %s", ui_state.urdf_joint_names)

    # Signal URDF scene ready for tests
    readiness_state.signal_urdf_scene_ready()

    # Sync gizmo settings to URDF scene now that it's ready
    control_panel.sync_gizmo_to_urdf()

    # Apply simulator appearance if in simulator mode (scene wasn't ready earlier)
    if robot_state.simulator_active:
        ui_state.urdf_scene.set_simulator_appearance(True)


def _init_angle_pipeline_config() -> None:
    """Initialize angle pipeline config arrays from urdf_scene.config.

    Call this once after URDF scene is initialized to precompute the mappings
    needed by the numba angle_pipeline function.
    """
    global _angle_pipeline_config_valid

    if not ui_state.urdf_scene:
        _angle_pipeline_config_valid = False
        return

    try:
        config = ui_state.urdf_scene.config
        index_mapping = ui_state.urdf_index_mapping
        joint_name_order = config.joint_name_order
        urdf_joint_names = ui_state.urdf_scene.joint_names

        # Build combined mapping: for each output position, which input index to use
        # and what sign/offset to apply
        for i in range(6):
            if i < len(index_mapping) and index_mapping[i] < 6:
                controller_idx = index_mapping[i]
                _index_mapping_array[i] = controller_idx

                # Sign correction
                if controller_idx < len(config.angle_signs):
                    _angle_signs_array[i] = (
                        1.0 if config.angle_signs[controller_idx] >= 0 else -1.0
                    )
                else:
                    _angle_signs_array[i] = 1.0

                # Offset
                if controller_idx < len(config.angle_offsets):
                    _angle_offsets_array[i] = config.angle_offsets[controller_idx]
                else:
                    _angle_offsets_array[i] = 0.0
            else:
                _index_mapping_array[i] = i
                _angle_signs_array[i] = 1.0
                _angle_offsets_array[i] = 0.0

        # Build URDF reorder mapping
        for i, joint_name in enumerate(urdf_joint_names[:6]):
            try:
                urdf_idx = joint_name_order.index(joint_name)
                _urdf_reorder_array[i] = urdf_idx if urdf_idx < 6 else i
            except ValueError:
                _urdf_reorder_array[i] = i

        _angle_pipeline_config_valid = True
        logging.debug("Angle pipeline config initialized")

    except Exception as e:
        logging.debug("Failed to init angle pipeline config: %s", e)
        _angle_pipeline_config_valid = False


def update_urdf_angles(angles_deg: np.ndarray) -> None:
    """Update URDF scene with new joint angles (degrees -> radians)."""
    global _angle_pipeline_config_valid

    if not ui_state.urdf_scene or len(angles_deg) < 6:
        return

    # Initialize config on first call
    if not _angle_pipeline_config_valid:
        _init_angle_pipeline_config()

    # Pass numpy array directly to numba pipeline (no copy needed)
    if not angle_pipeline(
        angles_deg,
        _index_mapping_array,
        _angle_signs_array,
        _angle_offsets_array,
        _urdf_reorder_array,
        _angles_ordered_buffer,
    ):
        return

    ui_state.urdf_scene.set_axis_values(_angles_ordered_buffer)


# --------------- Controller controls ---------------


async def start_controller(com_port: str | None) -> None:
    """Start the PAROL6 controller or attach to an existing one.

    In EXCLUSIVE_START mode this will *fail hard* if a controller is already
    running at the configured host/port instead of silently reusing it.
    """
    global server_manager
    try:
        # If AUTO_START requested, ensure a server is running at the target tuple
        if config.exclusive_start:
            server_manager = manage_server(
                host=config.controller_host,
                port=config.controller_port,
                com_port=com_port,
                extra_env=None,
                normalize_logs=True,
            )
        else:
            # If a controller is already running, reuse it; otherwise start our own
            if is_server_running(
                host=config.controller_host,
                port=config.controller_port,
            ):
                logging.info(
                    "Controller already running at %s:%s; reusing external server",
                    config.controller_host,
                    config.controller_port,
                )
                server_manager = None

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
            controller_status_label.style(f"color: {StatusColors.POSITIVE}")
        logging.info("Controller started")
    except Exception as e:
        logging.error("Start controller failed: %s", e)


def stop_controller() -> None:
    global server_manager, ping_timer, status_consumer_task
    try:
        if server_manager:
            logging.info("Stopping controller...")
            server_manager.stop_controller()
        server_manager = None

        # Disable ping timer and stop multicast consumer on disconnect
        if ping_timer is not None:
            ping_timer.active = False
        if status_consumer_task is not None and not status_consumer_task.done():
            status_consumer_task.cancel()

        controller_state.running = False
        robot_state.connected = False
        logging.info("Controller stopped")
    except Exception as e:
        logging.error("Stop controller failed: %s", e)


# --------------- Connectivity Check ---------------
async def check_ping() -> None:
    """Check connectivity via PING (1Hz)"""
    global last_ping_ok
    try:
        result = await client.ping()
        last_ping_ok = result["serial_connected"] if result else False
    except Exception:
        last_ping_ok = False

    # Update robot connectivity status
    robot_state.connected = last_ping_ok
    if robot_status_label:
        robot_status_label.text = "ROBOT"
        if robot_tooltip:
            robot_tooltip.text = "connected" if last_ping_ok else "disconnected"
        robot_status_label.style(
            f"color: {StatusColors.POSITIVE}"
            if last_ping_ok
            else f"color: {StatusColors.NEGATIVE}"
        )


# --------------- UI Update Functions ---------------
def update_ui_from_status() -> None:
    """Update UI elements from robot_state (called from multicast consumer)"""
    # Skip position/angle updates when in editing mode (editing sync handles these)
    skip_position_updates = robot_state.editing_mode

    # Update URDF scene with new angles and TCP ball
    if not skip_position_updates:
        with global_phase_timer.phase("scene"):
            update_urdf_angles(robot_state.angles.deg)
            if ui_state.urdf_scene:
                ui_state.urdf_scene.update_from_robot_state()

    if not skip_position_updates:
        # robot_state.pose is already numpy float64 - pass directly to numba
        pose_extraction_pipeline(
            robot_state.pose,
            _rotation_matrix_buffer,
            _rpy_rad_buffer,
            _pose_result_buffer,
        )

        robot_state.x = _pose_result_buffer[0]
        robot_state.y = _pose_result_buffer[1]
        robot_state.z = _pose_result_buffer[2]
        # Set both scalar fields (for UI binding) and OrientationArray (for rad access)
        robot_state.rx = _pose_result_buffer[3]
        robot_state.ry = _pose_result_buffer[4]
        robot_state.rz = _pose_result_buffer[5]
        robot_state.orientation.set_deg(_pose_result_buffer[3:6])

    # Push IO derived fields into bindable RobotState (numpy int32 array)
    robot_state.io_in1 = int(robot_state.io[0])
    robot_state.io_in2 = int(robot_state.io[1])
    robot_state.io_out1 = int(robot_state.io[2])
    robot_state.io_out2 = int(robot_state.io[3])
    robot_state.io_estop = int(robot_state.io[4])

    # Push gripper derived fields into bindable RobotState (numpy int32 array)
    robot_state.grip_id = int(robot_state.gripper[0])
    robot_state.grip_pos = int(robot_state.gripper[1])
    robot_state.grip_speed = int(robot_state.gripper[2])
    robot_state.grip_current = int(robot_state.gripper[3])
    robot_state.grip_obj = int(robot_state.gripper[5])

    # Monitor E-STOP state changes and show/hide dialog as needed
    control_panel.check_estop_state_change()

    # Notify listeners that robot state has changed (for envelope proximity updates)
    # Must wrap with client context since this may be called from background task
    # Skip if page not ready to avoid race with NiceGUI page serialization
    if not readiness_state.page_ready.is_set():
        return

    try:
        with ui.context.client:
            _update_connection_notification()
            robot_state.notify_changed()
    except RuntimeError:
        # No client context available (background task) - skip UI updates
        pass


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
    # Add keybindings focus detection script
    ui.add_head_html('<script src="/static/js/keybindings.js" defer></script>')

    with ui.column().classes("relative w-screen h-screen overflow-hidden gap-0"):
        with ui.column().classes("absolute inset-0 z-0"):
            # Initialize URDF scene
            async def _init():
                global urdf_scene
                await initialize_urdf_scene()
                urdf_scene = ui_state.urdf_scene

            ui.timer(0.05, _init, once=True)

        # Main content area - contains overlay panels and UI elements
        with (
            ui.column().classes("absolute inset-0 z-20").style("pointer-events: none;")
        ):
            # Wrapper div for panel coupling state (CSS class toggling)
            with (
                ui.element("div")
                .classes("panels-wrap absolute inset-0 z-30")
                .style("pointer-events: none;") as panels_wrap
            ):
                # Top tab bar - positioned independently
                with (
                    ui.tabs()
                    .props("vertical")
                    .classes("side-tab-bar absolute left-0 top-0 z-40") as side_tabs
                ):
                    program_tab = ui.tab(name="program", label="", icon="code")
                    program_tab.mark("tab-program")
                    io_tab = ui.tab(
                        name="io", label="", icon="settings_input_component"
                    )
                    io_tab.mark("tab-io")
                    gripper_tab = ui.tab(name="gripper", label="")
                    with gripper_tab:
                        ui.image("/static/icons/robotic-claw.svg").classes(
                            "gripper-icon"
                        ).style(
                            "width: 24px; height: 24px; transform: rotate(180deg); filter: brightness(0) invert(1) opacity(0.8);"
                        )
                    gripper_tab.mark("tab-gripper")

                # Top panels container - absolute positioned, anchored to top
                with (
                    ui.tab_panels(side_tabs, value=None)
                    .props(
                        "vertical animated transition-prev=slide-right transition-next=slide-right"
                    )
                    .classes(
                        "left-panels-container top-panels-container z-30"
                    ) as top_panels
                ):

                    def close_top_panels():
                        side_tabs.value = None
                        top_panels.value = None
                        # Clean up layout classes when closing top panel
                        panels_wrap.classes(remove="coupled")
                        # Track program panel visibility for tab flash
                        ui_state.program_panel_visible = False
                        # Explicitly save closed state (update:model-value doesn't fire for None)
                        ui.run_javascript("PanelResize.onTabChange('top', '')")

                    with ui.tab_panel("program").classes(
                        "overlay-card program-panel resizable-panel p-0"
                    ):
                        # Editor panel handles its own header row with title, tabs, and close button
                        editor_panel.build(close_callback=close_top_panels)
                        # Resize handles - JS module will attach events
                        ui.element("div").classes("resize-handle-right")
                        ui.element("div").classes("resize-handle-bottom")
                        ui.element("div").classes("resize-handle-corner")

                    with ui.tab_panel("io").classes("overlay-card overflow-hidden"):
                        with ui.row().classes("w-full"):
                            ui.label("I/O").classes("text-lg font-medium")
                            ui.space()
                            ui.button(icon="close", on_click=close_top_panels).props(
                                "flat round dense color=white"
                            )
                        io_page = io_page or IoPage(client)
                        io_page.build()

                    with ui.tab_panel("gripper").classes(
                        "overlay-card overflow-hidden"
                    ):
                        with ui.row().classes("w-full"):
                            ui.label("Gripper").classes("text-lg font-medium")
                            ui.space()
                            ui.button(icon="close", on_click=close_top_panels).props(
                                "flat round dense color=white"
                            )
                        gripper_page = gripper_page or GripperPage(client)
                        gripper_page.build()

                    # Bind top panel tab changes for visibility tracking
                    def update_top_layout():
                        # Track program panel visibility for tab flash
                        ui_state.program_panel_visible = side_tabs.value == "program"

                    side_tabs.on("update:model-value", lambda e: update_top_layout())

                    # Handle tab switching via PanelResize module
                    def handle_tab_change(e):
                        to_tab = e.args or ""
                        ui.run_javascript(f"PanelResize.onTabChange('top', '{to_tab}')")

                    side_tabs.on("update:model-value", handle_tab_change)

                    # Initial sync to avoid invisible overlay blocking scene
                    update_top_layout()

                # Bottom vertical tabs and panels - anchored at bottom-left
                # Tabs positioned to match top tabs: side-tab-bar has margin: 12px
                with (
                    ui.tabs()
                    .props("vertical")
                    .classes(
                        "side-tab-bar absolute bottom-0 left-0 z-50"
                    ) as bottom_tabs
                ):
                    resp_tab = ui.tab(name="response", label="", icon="article")
                    resp_tab.tooltip("Log")
                    resp_tab.mark("tab-log")
                    # Help tab opens dialog instead of panel
                    help_tab = ui.tab(name="help", label="", icon="help_outline")
                    help_tab.tooltip("Help")
                    help_tab.mark("tab-help")
                    help_tab.on("click", lambda: help_menu.show_help_dialog())

                # Panels positioned above tabs - styling in theme.py .bottom-panels-container
                with (
                    ui.tab_panels(bottom_tabs, value=None)
                    .props(
                        "vertical animated transition-prev=slide-up transition-next=slide-down"
                    )
                    .classes(
                        "left-panels-container bottom-panels-container"
                    ) as bottom_panels
                ):

                    def close_bottom_panels():
                        bottom_tabs.value = None
                        bottom_panels.value = None
                        # Update classes to restore layout
                        panels_wrap.classes(remove="coupled")
                        # Explicitly save closed state (update:model-value doesn't fire for None)
                        ui.run_javascript("PanelResize.onTabChange('bottom', '')")

                    with ui.tab_panel("response").classes(
                        "overlay-card response-panel resizable-panel"
                    ):
                        with ui.row().classes("w-full"):
                            ui.label("Log").classes("text-lg font-medium")
                            ui.space()
                            ui.button(icon="close", on_click=close_bottom_panels).props(
                                "flat round dense color=white"
                            )
                        response_log = (
                            ui.log(max_lines=1000)
                            .classes("w-full h-full")
                            .classes("no-x-scroll")
                            .style(
                                "min-height: 200px !important; width: 100% !important; background: rgba(0, 0, 0, 0.65); border-radius: 10px;"
                            )
                        )
                        # Resize handles - JS module will attach events
                        ui.element("div").classes("resize-handle-top")
                        ui.element("div").classes("resize-handle-right")
                        ui.element("div").classes("resize-handle-corner")

                    # Bind bottom tab changes to adjust layout via CSS classes
                    def update_bottom_layout():
                        is_open = bool(bottom_tabs.value)
                        # Check if a resizable top panel is active (currently only "program")
                        top_is_resizable = side_tabs.value == "program"
                        if is_open and top_is_resizable:
                            # Couple heights when both resizable panels are open
                            panels_wrap.classes(add="coupled")
                        else:
                            panels_wrap.classes(remove="coupled")

                    bottom_tabs.on(
                        "update:model-value", lambda _: update_bottom_layout()
                    )

                    # Handle tab switching via PanelResize module
                    def handle_bottom_tab_change(e):
                        to_tab = e.args or ""
                        ui.run_javascript(
                            f"PanelResize.onTabChange('bottom', '{to_tab}')"
                        )

                    bottom_tabs.on("update:model-value", handle_bottom_tab_change)

                    # Initial sync on load to avoid stale half-height state
                    update_bottom_layout()

        # Top-right HUD: Pose readouts
        readout_panel.build("tr")

        # Bottom-right HUD: control panel
        control_panel.build("br")

        # Configure panel resize module with app-specific settings
        ui.run_javascript(f"PanelResize.configure({json.dumps(PANEL_RESIZE_CONFIG)})")

        # Restore active tabs from localStorage before signaling app ready
        ui_client = ui.context.client

        async def restore_active_tabs():
            with ui_client:
                try:
                    saved_tabs = await ui.run_javascript("PanelResize.getActiveTabs()")
                    if saved_tabs:
                        # Restore top panel state (including closed state when top is null)
                        if "top" in saved_tabs:
                            top_tab = saved_tabs["top"]
                            side_tabs.value = top_tab
                            top_panels.value = top_tab
                            update_top_layout()
                            if top_tab:
                                # Trigger size restoration for open panels
                                ui.run_javascript(
                                    f"PanelResize.onTabChange('top', '{top_tab}')"
                                )
                        # Restore bottom panel state (including closed state)
                        if "bottom" in saved_tabs:
                            bottom_tab = saved_tabs["bottom"]
                            bottom_tabs.value = bottom_tab
                            bottom_panels.value = bottom_tab
                            update_bottom_layout()
                            if bottom_tab:
                                ui.run_javascript(
                                    f"PanelResize.onTabChange('bottom', '{bottom_tab}')"
                                )
                        logging.debug("Restored active tabs: %s", saved_tabs)
                except Exception as e:
                    logging.debug("Could not restore active tabs: %s", e)
                # Signal app ready after tabs are restored
                ui.run_javascript("PanelResize.onAppReady()")

        ui.timer(0.5, lambda: asyncio.create_task(restore_active_tabs()), once=True)

    # Set up global keybindings
    _setup_keybindings()


def _setup_keybindings() -> None:
    """Set up global keyboard shortcuts and focus detection."""
    # Add global keyboard handler
    ui.keyboard(on_key=keybindings_manager.handle_key)

    # Set up JavaScript callback for focus detection
    def on_focus_change(focused: bool) -> None:
        keybindings_manager.set_editor_focused(focused)

    # Expose the callback to JavaScript and initialize the focus detector
    ui.run_javascript(
        """
        if (window.KeybindingsFocusDetector) {
            window.KeybindingsFocusDetector.init(function(focused) {
                // Send focus state to Python
                emitEvent('keybindings_focus_change', { focused: focused });
            });
        }
        """
    )

    # Listen for focus change events from JavaScript
    ui.on(
        "keybindings_focus_change",
        lambda e: on_focus_change(e.args.get("focused", False)),
    )

    # Register all keybindings
    _register_keybindings()

    # Set up first-time tutorial dialog
    _setup_first_time_tutorial()


def _setup_first_time_tutorial() -> None:
    """Set up the first-time tutorial dialog for new users."""
    ui_client = ui.context.client

    async def check_first_visit():
        with ui_client:
            help_menu.check_first_visit()

    asyncio.create_task(check_first_visit())


def _register_keybindings() -> None:
    """Register all global keybindings."""

    # Robot Control
    keybindings_manager.register(
        Keybinding(
            key="h",
            display="H",
            description="Home robot",
            action=lambda: asyncio.create_task(control_panel.send_home()),
            category="Robot Control",
        )
    )

    keybindings_manager.register(
        Keybinding(
            key="Escape",
            display="Esc",
            description="Emergency Stop",
            action=lambda: asyncio.create_task(control_panel.on_estop_click()),
            category="Robot Control",
        )
    )

    # Playback Controls
    keybindings_manager.register(
        Keybinding(
            key=" ",
            display="Space",
            description="Play/Pause",
            action=lambda: asyncio.create_task(editor_panel._toggle_play()),
            category="Playback",
        )
    )

    keybindings_manager.register(
        Keybinding(
            key="s",
            display="S",
            description="Step forward",
            action=lambda: editor_panel._step_forward(),
            category="Playback",
            # Only active when script is running
            enabled_check=lambda: editor_panel.script_running,
        )
    )

    # Cartesian Jog - WASD + Q/E
    # These are holdable: click = single step, hold = continuous jog
    _register_cartesian_jog_keybindings()

    # Speed Control
    keybindings_manager.register(
        Keybinding(
            key="]",
            display="]",
            description="Increase jog speed",
            action=_increase_jog_speed,
            category="Speed Control",
        )
    )

    keybindings_manager.register(
        Keybinding(
            key="[",
            display="[",
            description="Decrease jog speed",
            action=_decrease_jog_speed,
            category="Speed Control",
        )
    )

    # Target insertion
    keybindings_manager.register(
        Keybinding(
            key="t",
            display="T",
            description="Add target at current position",
            action=lambda: ui_state.urdf_scene._show_unified_target_editor(
                use_click_position=False
            )
            if ui_state.urdf_scene
            else None,
            category="Recording",
        )
    )


def _register_cartesian_jog_keybindings() -> None:
    """Register WASD + Q/E keybindings for cartesian jogging."""
    # Map keys to axes: W/S = Y, A/D = X, Q/E = Z
    jog_key_map = {
        "w": "Y+",
        "s": "Y-",
        "a": "X-",
        "d": "X+",
        "q": "Z-",
        "e": "Z+",
    }

    for key, axis in jog_key_map.items():
        # S key is context-aware: jog when not running, step when running
        enabled_check = (
            (lambda: not editor_panel.script_running) if key == "s" else None
        )

        keybindings_manager.register(
            Keybinding(
                key=key,
                display=key.upper(),
                description=f"Jog {axis}",
                action=_make_jog_action(axis),
                on_release=_make_jog_release(axis),
                category="Cartesian Jog",
                holdable=True,
                enabled_check=enabled_check,
            )
        )


def _make_jog_action(axis: str) -> Callable:
    """Create a jog action callback for the given axis."""

    def action(is_press: bool = True, is_click: bool = False) -> None:
        _handle_jog_key(axis, is_press, is_click)

    return action


def _make_jog_release(axis: str) -> Callable:
    """Create a jog release callback for the given axis."""

    def release() -> None:
        _handle_jog_key_release(axis)

    return release


def _handle_jog_key(axis: str, is_press: bool = True, is_click: bool = False) -> None:
    """Handle jog key press/click for cartesian movement."""
    if is_click:
        # Single step movement
        asyncio.create_task(control_panel.set_axis_pressed(axis, True))

        # Small delay then release
        async def release():
            await asyncio.sleep(0.05)
            await control_panel.set_axis_pressed(axis, False)

        asyncio.create_task(release())
    elif is_press:
        # Start continuous jog
        asyncio.create_task(control_panel.set_axis_pressed(axis, True))


def _handle_jog_key_release(axis: str) -> None:
    """Handle jog key release to stop continuous jogging."""
    asyncio.create_task(control_panel.set_axis_pressed(axis, False))


def _increase_jog_speed() -> None:
    """Increase jog speed by 10%."""
    current = ui_state.jog_speed
    new_speed = min(100, current + 10)
    ui_state.jog_speed = new_speed
    ui.notify(f"Jog speed: {new_speed}%", position="bottom-right", timeout=1000)


def _decrease_jog_speed() -> None:
    """Decrease jog speed by 10%."""
    current = ui_state.jog_speed
    new_speed = max(1, current - 10)
    ui_state.jog_speed = new_speed
    ui.notify(f"Jog speed: {new_speed}%", position="bottom-right", timeout=1000)


# Guard against duplicate startup/shutdown handler registration during tests
# When NiceGUI fails to reset between tests, runpy.run_path() re-executes main.py


def _register_handlers() -> None:
    """Register startup/shutdown handlers only once.

    Skip registration if NiceGUI is already started (e.g., during test reruns
    when NiceGUI didn't fully reset between tests).
    """
    # If NiceGUI is already started, we can't register new handlers
    if ng_app.is_started:
        return

    @ng_app.on_startup
    async def _on_startup() -> None:
        """NiceGUI startup hook.

        Any failure to start the controller (including "server already running")
        is treated as a hard error so tests cannot silently proceed in a bad state.
        """
        global server_manager

        # Pre-warm process pool workers with RTB imports (runs in background)
        # This makes subsequent path visualizations fast since workers are reused
        asyncio.create_task(warm_process_pool())

        # Start controller and streaming on server startup
        try:
            port = ng_app.storage.general.get("com_port", "")
        except Exception:
            port = ""
        try:
            if not controller_state.running:
                await start_controller(port)

            # Ensure controller is responsive before deciding initial mode
            with contextlib.suppress(Exception):
                await client.wait_for_server_ready(timeout=5.0)

            await client.stream_on()

            # Determine initial mode based on persisted port and serial connectivity
            serial_connected = False
            try:
                result = await client.ping()
                serial_connected = result["serial_connected"] if result else False
            except Exception:
                serial_connected = False

            robot_state.connected = bool(serial_connected)

            # Startup policy:
            # - If no serial port specified -> start simulator
            # - If serial port specified and serial connected -> start robot
            # - If serial port specified but not connected -> start simulator
            if not port or not serial_connected:
                if not robot_state.simulator_active:
                    logging.debug(
                        "startup: enabling simulator (no port or serial not connected)"
                    )
                    try:
                        await client.simulator_on()
                    except Exception as e:
                        logging.error("startup: simulator_on failed: %s", e)
                robot_state.simulator_active = True
                # Controller now waits for first frame, so no extra delay needed
                try:
                    await client.enable()
                except Exception as e:
                    logging.warning("startup: enable failed (may retry): %s", e)
            else:
                # Robot mode (physical serial connected)
                robot_state.simulator_active = False
                try:
                    await client.enable()
                except Exception as e:
                    logging.warning("startup: enable failed (may retry): %s", e)

            # Set saved motion profile
            try:
                saved_profile = ng_app.storage.general.get("motion_profile", "TOPPRA")
                await client.set_profile(saved_profile)
                logging.debug("startup: set motion profile to %s", saved_profile)
            except Exception as e:
                logging.warning("startup: set_profile failed: %s", e)

        except Exception as e:
            logging.error("App startup init failed: %s", e)
            # Clean up server_manager if startup failed
            if server_manager:
                server_manager.stop_controller()
                server_manager = None
            # Re-raise so tests and callers see a hard failure
            raise

    @ng_app.on_shutdown
    async def _on_shutdown() -> None:
        """NiceGUI shutdown hook - ensure controller and child processes are stopped."""
        logging.debug("Nicegui Shutting Down...")

        # Stop any running script processes first
        try:
            if (
                editor_panel
                and editor_panel.script_running
                and editor_panel.script_handle
            ):
                logging.info("Stopping running script process during shutdown...")
                from parol_commander.services.script_runner import stop_script

                await stop_script(editor_panel.script_handle, timeout=2.0)
                editor_panel.script_handle = None
                editor_panel.script_running = False
                # Clean up stepping controller if active
                if hasattr(editor_panel, "_cleanup_stepping"):
                    editor_panel._cleanup_stepping()
        except Exception as e:
            logging.warning("Error stopping script during shutdown: %s", e)

        stop_controller()
        await client.close()


# Register handlers at module load
_register_handlers()


def _cleanup_script_processes_sync() -> None:
    """Synchronously kill any running script subprocess.

    This is called from atexit and signal handlers as a last-resort cleanup.
    """
    try:
        if editor_panel and editor_panel.script_handle:
            proc = editor_panel.script_handle.get("proc")
            if proc and proc.returncode is None:
                logging.info("Killing orphaned script process (PID: %s)", proc.pid)
                try:
                    # On Unix, try to kill the entire process group
                    if sys.platform != "win32" and proc.pid:
                        try:
                            pgid = os.getpgid(proc.pid)
                            os.killpg(pgid, signal.SIGKILL)
                            logging.debug("Killed process group %s", pgid)
                        except (ProcessLookupError, OSError):
                            proc.kill()
                    else:
                        proc.kill()
                except ProcessLookupError:
                    pass
                except Exception as e:
                    logging.debug("Error killing script process: %s", e)
    except Exception as e:
        logging.debug("Error in script cleanup: %s", e)


# Register atexit cleanup for last-resort process termination
atexit.register(_cleanup_script_processes_sync)


@ui.page("/")
async def index_page():
    global ping_timer, joint_jog_timer, cart_jog_timer
    # Theme and layout
    apply_theme(get_theme())
    ui.query(".nicegui-content").classes("p-0")
    inject_layout_css()

    # Build UI
    build_page_content()

    # Determine initial mode based on connectivity
    try:
        # Quick ping to check if robot is connected
        result = await client.ping()
        serial = result.serial_connected if result else False

        robot_state.connected = bool(serial)

        # Reflect current simulator appearance only (authoritative mode was chosen at startup)
        # Guard against client-deleted errors during test teardown
        with contextlib.suppress(RuntimeError):
            if ui_state.urdf_scene:
                ui_state.urdf_scene.set_simulator_appearance(
                    bool(robot_state.simulator_active)
                )

            # Update button visuals to match current mode
            if callable(getattr(control_panel, "_update_robot_btn_visual", None)):
                control_panel._update_robot_btn_visual()
    except Exception as e:
        logging.warning("Connectivity check failed: %s", e)
        robot_state.connected = False
        # Reflect current simulator appearance only (do not toggle mode here)
        # Guard against client-deleted errors during test teardown
        with contextlib.suppress(RuntimeError):
            if ui_state.urdf_scene:
                ui_state.urdf_scene.set_simulator_appearance(
                    bool(robot_state.simulator_active)
                )
            if callable(getattr(control_panel, "_update_robot_btn_visual", None)):
                control_panel._update_robot_btn_visual()

    # Create jog timers
    joint_jog_timer = ui.timer(
        interval=config.webapp_control_interval_s,
        callback=control_panel.jog_tick,
        active=False,
    )
    cart_jog_timer = ui.timer(
        interval=config.webapp_control_interval_s,
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

    # Signal full page readiness for tests
    async def _signal_page_ready():
        await asyncio.sleep(0)  # Yield to event loop to ensure timers are wired
        readiness_state.signal_page_ready()

    asyncio.create_task(_signal_page_ready())


async def _status_consumer() -> None:
    """Consume multicast status and update shared robot_state."""
    global _last_work_end

    try:
        # Wait for server to be responsive before subscribing to multicast
        await client.wait_for_server_ready(timeout=5.0)
        async for status in client.status_stream_shared():
            try:
                # Track loop timing via LoopMetrics
                now = time.perf_counter()
                _ui_metrics.tick(now)

                # Track true wait time (end of last work → now)
                if _last_work_end > 0:
                    true_wait_s = now - _last_work_end
                    _wait_metrics.record(true_wait_s)

                # Rate-limited debug log every 3s
                if _ui_metrics.should_log(now, 3.0):
                    # Compute stats before logging
                    _work_metrics.compute_stats()
                    _wait_metrics.compute_stats()
                    for p in global_phase_timer.phases.values():
                        p.compute_stats()

                    # Build phase timing string for non-zero phases
                    phase_strs = []
                    for name, phase in global_phase_timer.phases.items():
                        if phase.mean_s > 0.00001:
                            phase_strs.append(f"{name}={phase.mean_s * 1000:.2f}")

                    logging.debug(
                        "ui: %s | work=%.2f(p99=%.1f) wait=%.1f(p99=%.1f) | %s",
                        format_hz_summary(_ui_metrics),
                        _work_metrics.mean_s * 1000,
                        _work_metrics.p99_s * 1000,
                        _wait_metrics.mean_s * 1000,
                        _wait_metrics.p99_s * 1000,
                        " ".join(phase_strs),
                    )

                # Direct timing for total work (bypass PhaseTimer nesting issues)
                _work_start = time.perf_counter()

                # Wrap all processing to measure total work time
                with global_phase_timer.phase("status"):
                    # Copy status data (in-place fills to avoid allocations)
                    if not robot_state.editing_mode:
                        robot_state.angles.set_deg(status.angles)
                    robot_state.pose[:] = status.pose
                    robot_state.io[:] = status.io
                    robot_state.gripper[:] = status.gripper

                    # Signal backend ready on first valid STATUS with non-zero angles
                    if robot_state.angles[0] != 0.0 or robot_state.angles[5] != 0.0:
                        readiness_state.signal_backend_ready()
                        readiness_state.signal_simulator_ready()

                    # Movement enablement arrays
                    robot_state.joint_en[:] = status.joint_en
                    robot_state.cart_en_wrf[:] = status.cart_en_wrf
                    robot_state.cart_en_trf[:] = status.cart_en_trf

                    robot_state.action_current = status.action_current
                    robot_state.action_state = status.action_state or "IDLE"
                    robot_state.last_update_ts = time.time()

                    # Update UI from status
                    update_ui_from_status()

                    # Update panels
                    readout_panel.update_conn_io()
                    readout_panel.update_action_visibility()
                    control_panel.refresh_joint_enablement()
                    control_panel.sync_cartesian_button_states()

                # Record direct work time
                _work_end = time.perf_counter()
                _work_metrics.record(_work_end - _work_start)
                _last_work_end = _work_end  # For true wait tracking

            except Exception as e:
                logging.debug("Status consumer parse error: %s", e)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error("Status consumer error: %s", e)


def main():
    global client, control_panel, readout_panel, editor_panel
    global io_page, gripper_page, settings_page

    # CLI: web bind, controller target, and log level
    # Defaults come from config (lazy evaluation - reads env vars at access time)
    parser = argparse.ArgumentParser(description="PAROL6 NiceGUI Webserver")
    parser.add_argument(
        "--host", default=config.server_host, help="Webserver bind host"
    )
    parser.add_argument(
        "--port", type=int, default=config.server_port, help="Webserver bind port"
    )
    parser.add_argument(
        "--controller-host",
        default=config.controller_host,
        help="Controller host to connect to",
    )
    parser.add_argument(
        "--controller-port",
        type=int,
        default=config.controller_port,
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
        "--reload",
        action="store_true",
        help="Enable auto-reload on file changes (dev mode)",
    )
    args, _ = parser.parse_known_args()

    # Apply CLI overrides to config (these take precedence over env vars)
    config.set("server_host", args.host)
    config.set("server_port", args.port)
    config.set("controller_host", args.controller_host)
    config.set("controller_port", args.controller_port)

    # Resolve log level priority: explicit --log-level > -v/-q > env default
    if args.log_level:
        if args.log_level == "TRACE":
            config.set("log_level", TRACE)
        else:
            config.set("log_level", getattr(logging, args.log_level))
    elif args.verbose >= 3:
        os.environ["PAROL_TRACE"] = "1"
        config.set("log_level", TRACE)
    elif args.verbose >= 2:
        config.set("log_level", logging.DEBUG)
    elif args.verbose == 1:
        config.set("log_level", logging.INFO)
    elif args.quiet:
        config.set("log_level", logging.WARNING)
    # else: use env var default (no override needed)

    # Initialize client and component instances with final controller target
    client = AsyncRobotClient(host=config.controller_host, port=config.controller_port)
    control_panel = ControlPanel(client)
    readout_panel = ReadoutPanel()
    editor_panel = EditorPanel(client)
    # Store panels in ui_state for cross-module access
    ui_state.control_panel = control_panel
    ui_state.editor_panel = editor_panel
    ui_state.readout_panel = readout_panel
    io_page = None
    gripper_page = None
    settings_page = None

    # Configure logging
    configure_logging(config.log_level)
    logging.info(
        "Webserver bind: host=%s port=%s", config.server_host, config.server_port
    )
    logging.info(
        "Controller target: host=%s port=%s",
        config.controller_host,
        config.controller_port,
    )

    # Pre-compile numba functions to avoid JIT lag during hot path
    warmup_pipelines()

    try:
        ui.run(
            title="PAROL6 NiceGUI Commander",
            host=config.server_host,
            port=config.server_port,
            reload=args.reload,
            uvicorn_reload_excludes=".*, .py[cod], .sw.*, ~*, programs/*",
            show=False,
            loop="uvloop" if sys.platform != "win32" else "asyncio",
            http="httptools",
            binding_refresh_interval=0.05,
        )
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ in {"__main__", "__mp_main__"}:
    main()
