from typing import Literal

from nicegui import app as ng_app
from nicegui import ui

from parol_commander.common.theme import set_theme
from parol_commander.services.robot_client import client


class SettingsPage:
    """Settings tab page."""

    def build(self) -> None:
        # Theme mode toggle
        ui.label("Theme").classes("text-sm text-[var(--ctk-muted)]")
        with ui.row().classes("items-center gap-2"):

            def _set_mode(m: Literal["system", "light", "dark"]):
                set_theme(m)  # persists and applies

            ui.button("Auto", on_click=lambda: _set_mode("system")).props("unelevated")
            ui.button("Light", on_click=lambda: _set_mode("light")).props("unelevated")
            ui.button("Dark", on_click=lambda: _set_mode("dark")).props("unelevated")

        # Serial port controls
        ui.separator()
        ui.label("Serial Port").classes("text-sm text-[var(--ctk-muted)]")

        stored_port = ng_app.storage.general.get("com_port", "")
        sp_input = ui.input(label="Serial Port", value=stored_port).classes("w-64")

        async def _apply_port():
            ng_app.storage.general["com_port"] = sp_input.value or ""
            await client.set_serial_port(sp_input.value or "")
            ui.notify(f"SET_PORT {sp_input.value or ''}", color="primary")

        with sp_input:
            ui.tooltip("COM5 / /dev/ttyACM0 / /dev/tty.usbmodem0")
        sp_input.on("keydown.enter", _apply_port)
        ui.button("Set Port", on_click=_apply_port).props("unelevated")
