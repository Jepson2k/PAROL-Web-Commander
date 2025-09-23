import logging
from functools import partial

from nicegui import ui

from app.services.robot_client import client
from app.state import robot_state


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
            _ = await client.control_pneumatic_gripper(action, port)
            # Show SET_IO command format in notification
            io_idx = 1 if port == 1 else 2  # OUTPUT 1 = index 1, OUTPUT 2 = index 2
            ui.notify(f"Sent SET_IO|{io_idx}|{state}", color="primary")
            logging.info("OUTPUT%s -> %s", port, action.upper())
        except Exception as e:
            logging.error("Set output failed: %s", e)

    def build(self) -> None:
        """Build the I/O page content."""
        with ui.card().classes("w-full"):
            ui.label("I/O").classes("text-md font-medium")
            with ui.column().classes("gap-2"):
                with ui.row().classes("items-center gap-4"):
                    self.io_in1_label = (
                        ui.label("INPUT 1: -")
                        .bind_text_from(
                            robot_state, "io_in1", backward=lambda v: f"INPUT 1: {v}"
                        )
                        .classes("text-sm")
                    )
                    self.io_in2_label = (
                        ui.label("INPUT 2: -")
                        .bind_text_from(
                            robot_state, "io_in2", backward=lambda v: f"INPUT 2: {v}"
                        )
                        .classes("text-sm")
                    )
                    self.io_estop_label2 = (
                        ui.label("ESTOP: unknown")
                        .bind_text_from(
                            robot_state,
                            "io_estop",
                            backward=lambda v: f"ESTOP: {'OK' if int(v) else 'TRIGGERED'}",
                        )
                        .classes("text-sm")
                    )
                ui.separator()
                with ui.row().classes("items-center gap-4"):
                    self.io_out1_label = (
                        ui.label("OUTPUT 1 is: -")
                        .bind_text_from(
                            robot_state,
                            "io_out1",
                            backward=lambda v: f"OUTPUT 1 is: {v}",
                        )
                        .classes("text-sm")
                    )
                    ui.button("LOW", on_click=partial(self.set_output, 1, 0)).props(
                        "unelevated"
                    )
                    ui.button("HIGH", on_click=partial(self.set_output, 1, 1)).props(
                        "unelevated"
                    )
                with ui.row().classes("items-center gap-4"):
                    self.io_out2_label = (
                        ui.label("OUTPUT 2 is: -")
                        .bind_text_from(
                            robot_state,
                            "io_out2",
                            backward=lambda v: f"OUTPUT 2 is: {v}",
                        )
                        .classes("text-sm")
                    )
                    ui.button("LOW", on_click=partial(self.set_output, 2, 0)).props(
                        "unelevated"
                    )
                    ui.button("HIGH", on_click=partial(self.set_output, 2, 1)).props(
                        "unelevated"
                    )
