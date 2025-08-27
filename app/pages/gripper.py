from __future__ import annotations

import logging

from nicegui import ui

from app.services.robot_client import client


class GripperPage:
    """Gripper tab page."""

    def __init__(self) -> None:
        # Status labels
        self.grip_id_label: ui.label | None = None
        self.grip_cal_status_label: ui.label | None = None
        self.grip_err_status_label: ui.label | None = None
        self.grip_pos_feedback_label: ui.label | None = None
        self.grip_current_feedback_label: ui.label | None = None
        self.grip_obj_detect_label: ui.label | None = None

        # Control sliders/inputs
        self.grip_pos_slider: ui.slider | None = None
        self.grip_speed_slider: ui.slider | None = None
        self.grip_current_slider: ui.slider | None = None
        self.grip_id_input: ui.input | None = None

    # ---- Actions ----

    async def _grip_cal(self) -> None:
        try:
            resp = await client.control_electric_gripper("calibrate")
            ui.notify(resp, color="primary")
            logging.info("ELECTRIC CALIBRATE")
        except Exception as e:
            logging.error("Gripper calibrate failed: %s", e)

    def _grip_clear_error(self) -> None:
        ui.notify("Clear gripper error requires server support (TODO)", color="warning")
        logging.warning("Gripper clear error: TODO (server support needed)")

    async def _grip_move(self) -> None:
        try:
            pos = int(self.grip_pos_slider.value or 0) if self.grip_pos_slider else 0
            spd = int(self.grip_speed_slider.value or 0) if self.grip_speed_slider else 0
            cur = int(self.grip_current_slider.value or 100) if self.grip_current_slider else 100
            resp = await client.control_electric_gripper(
                "move", position=pos, speed=spd, current=cur
            )
            ui.notify(resp, color="primary")
            logging.info("ELECTRIC MOVE pos=%s spd=%s cur=%s", pos, spd, cur)
        except Exception as e:
            logging.error("Gripper move failed: %s", e)

    def _grip_change_id(self) -> None:
        try:
            _ = int(self.grip_id_input.value or "0") if self.grip_id_input else 0
            # Placeholder: requires specific API for changing ID
            ui.notify("Change ID requires server support (TODO)", color="warning")
            logging.warning("Change gripper ID: TODO (server support needed)")
        except Exception as e:
            logging.error("Change ID parse failed: %s", e)

    # ---- UI ----

    def build(self) -> None:
        with ui.card().classes("w-full"):
            ui.label("Gripper").classes("text-md font-medium")

            # Device info
            with ui.row().classes("items-center gap-4"):
                self.grip_id_label = ui.label("Gripper ID is: -").classes("text-sm")
                self.grip_cal_status_label = ui.label("Calibration status is: -").classes("text-sm")
                self.grip_err_status_label = ui.label("Error status is: -").classes("text-sm")

            # Actions
            with ui.row().classes("items-center gap-2"):
                ui.button("Calibrate gripper", on_click=self._grip_cal).props("unelevated")
                ui.button("Clear gripper error", on_click=self._grip_clear_error).props(
                    "unelevated"
                )

            # Command parameters
            ui.label("Command parameters").classes("text-sm mt-2")
            with ui.row().classes("items-center gap-2"):
                self.grip_pos_slider = ui.slider(min=0, max=255, value=10, step=1).classes("w-64")
                ui.label("Position").classes("text-xs text-[var(--ctk-muted)]")
            with ui.row().classes("items-center gap-2"):
                self.grip_speed_slider = ui.slider(min=0, max=255, value=50, step=1).classes("w-64")
                ui.label("Speed").classes("text-xs text-[var(--ctk-muted)]")
            with ui.row().classes("items-center gap-2"):
                self.grip_current_slider = ui.slider(min=100, max=1000, value=180, step=10).classes(
                    "w-64"
                )
                ui.label("Current (mA)").classes("text-xs text-[var(--ctk-muted)]")
            with ui.row().classes("items-center gap-2"):
                ui.button("Move GoTo", on_click=self._grip_move).props("unelevated color=primary")
                self.grip_id_input = ui.input(label="Change ID", value="0").classes("w-24")
                ui.button("Apply ID", on_click=self._grip_change_id).props("unelevated")

            # Feedback
            ui.label("Gripper feedback").classes("text-sm mt-2")
            with ui.column().classes("gap-1"):
                self.grip_pos_feedback_label = ui.label("Gripper position feedback is: -").classes(
                    "text-sm"
                )
                self.grip_current_feedback_label = ui.label(
                    "Gripper current feedback is: -"
                ).classes("text-sm")
                self.grip_obj_detect_label = ui.label("Gripper object detection is: -").classes(
                    "text-sm"
                )
