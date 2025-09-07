from __future__ import annotations

import asyncio
import logging

from nicegui import ui

from app.constants import JOINT_LIMITS_DEG
from app.services.robot_client import client
from app.state import robot_state


class CalibratePage:
    """Calibrate tab page."""

    def __init__(self) -> None:
        self.joint_selector: ui.select | None = None
        self.limit_selector: ui.select | None = None
        self.go_to_limit_button: ui.button | None = None

    # ---- Actions ----

    async def _send_enable(self) -> None:
        try:
            resp = await client.enable()
            ui.notify(resp, color="positive")
            logging.info(resp)
        except Exception as e:
            logging.error("ENABLE failed: %s", e)
            ui.notify(f"ENABLE failed: {e}", color="negative")

    async def _send_disable(self) -> None:
        try:
            resp = await client.disable()
            ui.notify(resp, color="warning")
            logging.warning(resp)
        except Exception as e:
            logging.error("DISABLE failed: %s", e)
            ui.notify(f"DISABLE failed: {e}", color="negative")

    async def go_to_limit(self) -> None:
        """Move the selected joint to its limit position using JOINT_LIMITS_DEG."""
        try:
            if not self.joint_selector or not self.limit_selector:
                ui.notify(
                    "Joint or direction selector not initialized", color="negative"
                )
                return

            angles = robot_state.angles or []
            if len(angles) < 6:
                ui.notify("No robot position data available", color="warning")
                return

            # Parse selected joint (e.g., "Joint 1" -> 0)
            joint_text = self.joint_selector.value
            if not joint_text or not str(joint_text).startswith("Joint "):
                ui.notify("Please select a joint", color="warning")
                return
            joint_index = (
                int(str(joint_text).split()[-1]) - 1
            )  # Convert to 0-based index

            # Get joint limits
            if joint_index < 0 or joint_index >= len(JOINT_LIMITS_DEG):
                ui.notify(f"Invalid joint index: {joint_index + 1}", color="negative")
                return

            min_limit, max_limit = JOINT_LIMITS_DEG[joint_index]
            current_angle = float(angles[joint_index])

            # Determine target based on direction selection
            direction = self.limit_selector.value
            if direction == "Minimum limit":
                target_angle = float(min_limit)
            elif direction == "Maximum limit":
                target_angle = float(max_limit)
            else:
                ui.notify("Please select a direction", color="warning")
                return

            # Use slow speed (25% as a reasonable default for limit moves)
            speed = 25

            # Create target angles array (copy current, change only selected joint)
            target_angles = list(angles[:6])
            target_angles[joint_index] = target_angle

            # Send move command
            await client.move_joints(
                target_angles, duration=None, speed_percentage=speed
            )
            ui.notify(
                f"Moving Joint {joint_index + 1} to {str(direction).lower()} ({target_angle:.1f}°)",
                color="primary",
            )
            logging.info(
                "GO_TO_LIMIT: Joint %s from %.1f° to %.1f° @ %s%%",
                joint_index + 1,
                current_angle,
                target_angle,
                speed,
            )
        except Exception as e:
            ui.notify(f"Go to limit failed: {e}", color="negative")
            logging.error("GO_TO_LIMIT failed: %s", e)

    # ---- UI ----

    def _update_go_to_limit_button(self) -> None:
        if (
            not self.go_to_limit_button
            or not self.joint_selector
            or not self.limit_selector
        ):
            return
        has_joint = self.joint_selector.value is not None
        has_direction = self.limit_selector.value is not None
        has_connection = len(robot_state.angles or []) >= 6
        if has_joint and has_direction and has_connection:
            self.go_to_limit_button.props("unelevated")
        else:
            self.go_to_limit_button.props("unelevated disable")

    def build(self) -> None:
        with ui.card().classes("w-full"):
            ui.label("Calibrate").classes("text-md font-medium")
            with ui.row().classes("items-center gap-2"):
                ui.button(
                    "Enable", on_click=lambda: asyncio.create_task(self._send_enable())
                ).props("unelevated color=positive")
                ui.button(
                    "Disable",
                    on_click=lambda: asyncio.create_task(self._send_disable()),
                ).props("unelevated color=negative")
                self.go_to_limit_button = ui.button(
                    "Go to limit",
                    on_click=lambda: asyncio.create_task(self.go_to_limit()),
                ).props("unelevated disable")

            with ui.row().classes("items-center gap-2"):
                self.joint_selector = ui.select(
                    options=[f"Joint {i}" for i in range(1, 7)],
                    label="Joint",
                    value="Joint 1",
                ).props("dense")
                self.limit_selector = ui.select(
                    options=["Minimum limit", "Maximum limit"],
                    label="Direction",
                    value="Maximum limit",
                ).props("dense")

            # Enable/disable Go-to-limit button when selections or connection change
            self.joint_selector.on_value_change(
                lambda: self._update_go_to_limit_button()
            )
            self.limit_selector.on_value_change(
                lambda: self._update_go_to_limit_button()
            )
            # Initial state
            self._update_go_to_limit_button()
