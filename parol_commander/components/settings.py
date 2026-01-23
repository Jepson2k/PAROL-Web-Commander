"""Settings component for serial port, theme, and visualization preferences."""

import logging
from typing import Literal, cast

from nicegui import app as ng_app
from nicegui import ui

from parol_commander.common.theme import set_theme
from parol_commander.state import simulation_state, ui_state
from parol6 import AsyncRobotClient
from parol6.motion.trajectory import ProfileType
from parol6.tools import TOOL_CONFIGS


def get_available_serial_ports() -> list[str]:
    """Detect available serial ports on the system.

    Returns:
        List of port device names (e.g., ['/dev/ttyACM0', '/dev/ttyUSB0', 'COM3'])
    """
    try:
        import serial.tools.list_ports

        ports = serial.tools.list_ports.comports()
        return [port.device for port in ports]
    except ImportError:
        logging.warning("pyserial not installed - cannot detect serial ports")
        return []
    except Exception as e:
        logging.error(f"Error detecting serial ports: {e}")
        return []


class SettingsContent:
    """Settings content that can be embedded in the control panel."""

    def __init__(self, client: AsyncRobotClient) -> None:
        self.client = client
        self._port_select: ui.select | None = None
        self._envelope_buttons: dict[str, ui.button] = {}
        self._theme_buttons: dict[str, ui.button] = {}
        self._refresh_timer: ui.timer | None = None

    def _load_preferences(self) -> dict:
        """Load persisted preferences from storage."""
        return {
            "com_port": ng_app.storage.general.get("com_port", ""),
            "show_route": ng_app.storage.general.get("show_route", True),
            "envelope_mode": ng_app.storage.general.get("envelope_mode", "auto"),
            "theme_mode": ng_app.storage.general.get("theme_mode", "system"),
            "motion_profile": ng_app.storage.general.get("motion_profile", "TOPPRA"),
        }

    def _refresh_serial_ports(self) -> None:
        """Refresh the available serial ports in the dropdown."""
        if self._port_select:
            ports = get_available_serial_ports()
            self._port_select.options = ports
            self._port_select.update()

    def cleanup(self) -> None:
        """Cancel background timers during shutdown."""
        if self._refresh_timer is not None:
            self._refresh_timer.cancel()

    def _update_envelope_button_styles(self, active_mode: str) -> None:
        """Update envelope button group styling based on active mode."""
        for mode, btn in self._envelope_buttons.items():
            if mode == active_mode:
                btn.props("color=primary")
            else:
                btn.props("color=grey-6")

    def _update_theme_button_styles(self, active_mode: str) -> None:
        """Update theme button group styling based on active mode."""
        for mode, btn in self._theme_buttons.items():
            if mode == active_mode:
                btn.props("color=primary")
            else:
                btn.props("color=grey-6")

    def build_embedded(self) -> None:
        """Build the settings content for embedding in control panel."""
        prefs = self._load_preferences()

        # Serial Port Selection
        with ui.row().classes("items-center justify-between w-full overflow-hidden"):
            with ui.column().classes("gap-0 overflow-hidden flex-shrink"):
                ui.label("Serial Port").classes("text-sm font-medium truncate")
                ui.label("Select robot communication port").classes(
                    "text-xs text-gray-500 dark:text-gray-400 truncate"
                )

            available_ports = get_available_serial_ports()
            stored_port = prefs["com_port"]

            # Use select with new_value_mode to allow custom input
            self._port_select = (
                ui.select(
                    options=available_ports,
                    value=stored_port if stored_port in available_ports else None,
                    label="Port",
                    new_value_mode="add-unique",
                    clearable=True,
                )
                .classes("w-32")
                .props("dense")
            )

            # If stored port isn't in the list but exists, set it
            if stored_port and stored_port not in available_ports:
                self._port_select.value = stored_port

            # Capture reference for closure
            port_select_ref = self._port_select

            async def _apply_port():
                port_val = port_select_ref.value or ""
                ng_app.storage.general["com_port"] = port_val
                await self.client.set_serial_port(port_val)
                ui.notify(f"SET_PORT {port_val}", color="primary")

            port_select_ref.on("update:model-value", lambda e: _apply_port())

        # Auto-refresh serial ports every 10 seconds
        self._refresh_timer = ui.timer(10.0, self._refresh_serial_ports)

        ui.separator().classes("my-1")

        # Show Route Switch
        def _on_show_route_change(e):
            val = bool(e.value) if hasattr(e, "value") else bool(e.args)
            simulation_state.paths_visible = val
            ng_app.storage.general["show_route"] = val
            simulation_state.notify_changed()

        with ui.row().classes("items-center justify-between w-full overflow-hidden"):
            with ui.column().classes("gap-0 overflow-hidden"):
                ui.label("Show Route").classes("text-sm font-medium truncate")
                ui.label("Display path visualization in 3D view").classes(
                    "text-xs text-gray-500 dark:text-gray-400 truncate"
                )
            ui.switch(
                value=prefs["show_route"],
                on_change=_on_show_route_change,
            ).props("dense").mark("switch-show-route")

        # Sync initial state
        simulation_state.paths_visible = prefs["show_route"]
        simulation_state.notify_changed()

        ui.separator().classes("my-1")

        # Workspace Envelope Selection - dropdown
        def _on_envelope_mode_change(e):
            mode = e.value if hasattr(e, "value") else str(e.args)
            simulation_state.envelope_mode = mode
            ng_app.storage.general["envelope_mode"] = mode
            # Update envelope_visible based on mode
            if mode == "off":
                simulation_state.envelope_visible = False
            elif mode == "on":
                simulation_state.envelope_visible = True
            # "auto" is handled in urdf_scene update logic
            simulation_state.notify_changed()

        with ui.row().classes("items-center justify-between w-full overflow-hidden"):
            with ui.column().classes("gap-0 overflow-hidden flex-shrink"):
                ui.label("Workspace Envelope").classes("text-sm font-medium truncate")
                ui.label("Show reachable workspace boundary").classes(
                    "text-xs text-gray-500 dark:text-gray-400 truncate"
                )
            ui.select(
                options={"auto": "Auto", "on": "On", "off": "Off"},
                value=prefs["envelope_mode"],
                on_change=_on_envelope_mode_change,
            ).classes("w-24").props("dense").mark("select-envelope-mode")

        # Sync initial state
        simulation_state.envelope_mode = prefs["envelope_mode"]
        if prefs["envelope_mode"] == "off":
            simulation_state.envelope_visible = False
        elif prefs["envelope_mode"] == "on":
            simulation_state.envelope_visible = True
        simulation_state.notify_changed()

        ui.separator().classes("my-1")

        # Tool Selection - dropdown
        def _on_tool_change(e):
            tool = e.value if hasattr(e, "value") else str(e.args)
            ng_app.storage.general["selected_tool"] = tool

            # Update TCP pose (gizmo position) via urdf_scene
            if ui_state.urdf_scene and hasattr(
                ui_state.urdf_scene, "update_tcp_pose_from_tool"
            ):
                ui_state.urdf_scene.update_tcp_pose_from_tool(tool)

            # Update envelope sphere radius if tool changes TCP Z offset
            # The envelope sphere will be updated on next simulation state change
            simulation_state.notify_changed()

            ui.notify(f"Tool: {tool}", color="primary")

        # Build tool options from TOOL_CONFIGS
        tool_options = {"none": "None"}
        for tool_name in TOOL_CONFIGS.keys():
            # Format tool name for display (capitalize, replace underscores)
            display_name = tool_name.replace("_", " ").title()
            tool_options[tool_name] = display_name

        stored_tool = ng_app.storage.general.get("selected_tool", "none")

        with ui.row().classes("items-center justify-between w-full overflow-hidden"):
            with ui.column().classes("gap-0 overflow-hidden flex-shrink"):
                ui.label("Tool").classes("text-sm font-medium truncate")
                ui.label("Select end effector tool").classes(
                    "text-xs text-gray-500 dark:text-gray-400 truncate"
                )
            ui.select(
                options=tool_options,
                value=stored_tool,
                on_change=_on_tool_change,
            ).classes("w-32").props("dense").mark("select-tool")

        # Sync initial tool state
        if stored_tool and stored_tool != "none":
            if ui_state.urdf_scene and hasattr(
                ui_state.urdf_scene, "update_tcp_pose_from_tool"
            ):
                ui_state.urdf_scene.update_tcp_pose_from_tool(stored_tool)

        ui.separator().classes("my-1")

        # Motion Profile Selection - dropdown
        async def _on_motion_profile_change(e):
            profile = e.value if hasattr(e, "value") else str(e.args)
            ng_app.storage.general["motion_profile"] = profile
            await self.client.set_profile(profile)

        # Build profile options from ProfileType enum
        motion_profile_options = {p.value.upper(): p.name.title() for p in ProfileType}

        with ui.row().classes("items-center justify-between w-full overflow-hidden"):
            with ui.column().classes("gap-0 overflow-hidden flex-shrink"):
                ui.label("Motion Profile").classes("text-sm font-medium truncate")
                ui.label("Trajectory generation algorithm").classes(
                    "text-xs text-gray-500 dark:text-gray-400 truncate"
                )
            ui.select(
                options=motion_profile_options,
                value=prefs["motion_profile"],
                on_change=_on_motion_profile_change,
            ).classes("w-32").props("dense").mark("select-motion-profile")

        ui.separator().classes("my-1")

        # Theme Selection - dropdown
        def _on_theme_change(e):
            mode = e.value if hasattr(e, "value") else str(e.args)
            set_theme(
                cast(Literal["system", "light", "dark"], mode)
            )  # persists and applies

        with ui.row().classes("items-center justify-between w-full overflow-hidden"):
            with ui.column().classes("gap-0 overflow-hidden flex-shrink"):
                ui.label("Theme").classes("text-sm font-medium truncate")
                ui.label("Application color scheme").classes(
                    "text-xs text-gray-500 dark:text-gray-400 truncate"
                )
            ui.select(
                options={"system": "Auto", "light": "Light", "dark": "Dark"},
                value=prefs["theme_mode"],
                on_change=_on_theme_change,
            ).classes("w-24").props("dense")

        ui.separator().classes("my-1")

        # Translation Reference Frame (locked to WRF)
        with ui.row().classes("items-center justify-between w-full overflow-hidden"):
            with ui.column().classes("gap-0 overflow-hidden flex-shrink"):
                ui.label("Translation RF").classes("text-sm font-medium truncate")
                ui.label("Reference frame for translation moves").classes(
                    "text-xs text-gray-500 dark:text-gray-400 truncate"
                )
            # Wrap in span so tooltip works on disabled element
            with ui.element("span").tooltip(
                "Mode is currently locked but will be configurable in a future update"
            ):
                ui.select(
                    options={"WRF": "World", "TRF": "Tool"},
                    value="WRF",
                ).classes("w-24").props("dense disable")

        ui.separator().classes("my-1")

        # Rotation Reference Frame (locked to TRF)
        with ui.row().classes("items-center justify-between w-full overflow-hidden"):
            with ui.column().classes("gap-0 overflow-hidden flex-shrink"):
                ui.label("Rotation RF").classes("text-sm font-medium truncate")
                ui.label("Reference frame for rotation moves").classes(
                    "text-xs text-gray-500 dark:text-gray-400 truncate"
                )
            # Wrap in span so tooltip works on disabled element
            with ui.element("span").tooltip(
                "Mode is currently locked but will be configurable in a future update"
            ):
                ui.select(
                    options={"WRF": "World", "TRF": "Tool"},
                    value="TRF",
                ).classes("w-24").props("dense disable")
