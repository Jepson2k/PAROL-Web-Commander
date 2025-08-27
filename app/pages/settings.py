from __future__ import annotations

import logging

from nicegui import app as ng_app
from nicegui import ui

from app.common.theme import ThemeMode, get_theme, set_theme


class SettingsPage:
    """Settings tab page."""

    def build(self) -> None:
        with ui.card().classes("w-full"):
            ui.label("Settings").classes("text-md font-medium")
            with ui.row().classes("items-center gap-2"):
                # Read theme from user storage or default to current theme mode
                saved_mode = ng_app.storage.user.get("theme_mode", get_theme())
                start_value = (
                    "System"
                    if saved_mode == "system"
                    else ("Light" if saved_mode == "light" else "Dark")
                )
                mode_toggle = ui.toggle(
                    options=["System", "Light", "Dark"], value=start_value
                ).props("dense")

                def _on_mode() -> None:
                    val = (mode_toggle.value or "System").lower()
                    mode: ThemeMode = (
                        "system"
                        if val.startswith("s")
                        else ("light" if val.startswith("l") else "dark")
                    )
                    set_theme(mode)
                    ng_app.storage.user["theme_mode"] = mode
                    logging.debug(f"Set theme to mode: {mode}")

                mode_toggle.on_value_change(lambda e: _on_mode())
