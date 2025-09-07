from __future__ import annotations

import logging
from functools import partial

from nicegui import ui

from app.services.robot_client import client


class IoPage:
    """I/O tab page."""

    def __init__(self) -> None:
        # Labels updated by status polling
        self.io_in1_label: ui.label | None = None
        self.io_in2_label: ui.label | None = None
        self.io_estop_label2: ui.label | None = None
        self.io_out1_label: ui.label | None = None
        self.io_out2_label: ui.label | None = None
        # Readouts card IO summary (shown on Move tab, but we expose here for consistency)
        self.io_summary_label: ui.label | None = None

    async def set_output(self, port: int, state: int) -> None:
        """Map Output 1/2 via pneumatic gripper actions through the UDP API."""
        try:
            action = "open" if state else "close"
            resp = await client.control_pneumatic_gripper(action, port)
            ui.notify(resp, color="primary")
            logging.info("OUTPUT%s -> %s", port, action.upper())
        except Exception as e:
            logging.error("Set output failed: %s", e)

    def build(self) -> None:
        """Build the I/O page content."""
        with ui.card().classes("w-full"):
            ui.label("I/O").classes("text-md font-medium")
            with ui.column().classes("gap-2"):
                with ui.row().classes("items-center gap-4"):
                    self.io_in1_label = ui.label("INPUT 1: -").classes("text-sm")
                    self.io_in2_label = ui.label("INPUT 2: -").classes("text-sm")
                    self.io_estop_label2 = ui.label("ESTOP: unknown").classes("text-sm")
                ui.separator()
                with ui.row().classes("items-center gap-4"):
                    self.io_out1_label = ui.label("OUTPUT 1 is: -").classes("text-sm")
                    ui.button("LOW", on_click=partial(self.set_output, 1, 0)).props(
                        "unelevated"
                    )
                    ui.button("HIGH", on_click=partial(self.set_output, 1, 1)).props(
                        "unelevated"
                    )
                with ui.row().classes("items-center gap-4"):
                    self.io_out2_label = ui.label("OUTPUT 2 is: -").classes("text-sm")
                    ui.button("LOW", on_click=partial(self.set_output, 2, 0)).props(
                        "unelevated"
                    )
                    ui.button("HIGH", on_click=partial(self.set_output, 2, 1)).props(
                        "unelevated"
                    )
