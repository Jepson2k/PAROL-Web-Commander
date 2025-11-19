"""Bottom-left control panel component for jog speed, step size, and robot control buttons."""

import asyncio
import contextlib
import logging
import time
import os
import re
import math
from functools import partial
from typing import Any, cast
import importlib.resources as pkg_resources

from nicegui import ui
from parol6 import AsyncRobotClient

from parol_commander.constants import (
    JOINT_LIMITS_DEG,
    WEBAPP_CONTROL_INTERVAL_S,
    WEBAPP_CONTROL_RATE_HZ,
)
from parol_commander.state import robot_state, ui_state
from parol6.protocol.types import Axis, Frame


class ControlPanel:
    """Bottom-left control panel for jog settings and robot control."""

    def __init__(self, client: AsyncRobotClient) -> None:
        """Initialize control panel with jog state and required robot client."""
        self.client = client

        # Jog UI references
        self._joint_left_btns: dict[int, ui.button] = {}
        self._joint_right_btns: dict[int, ui.button] = {}
        self._cart_axis_imgs: dict[str, ui.element] = {}

        # Jog state tracking
        self._jog_pressed_pos: list[bool] = [False] * 6
        self._jog_pressed_neg: list[bool] = [False] * 6
        self._cart_pressed_axes: dict[str, bool] = {
            "X+": False,
            "X-": False,
            "Y+": False,
            "Y-": False,
            "Z+": False,
            "Z-": False,
            "RX+": False,
            "RX-": False,
            "RY+": False,
            "RY-": False,
            "RZ+": False,
            "RZ-": False,
        }

        # Cartesian button slots/elements and assignment (layout fixed; labels/colors/actions dynamic)
        self._cart_slot_elems: dict[str, ui.element] = {}
        self._cart_slot_meta: dict[str, dict] = {}
        # Assignment of axes to fixed slots: 'ud1' (first up/down column), 'lr' (left/right row), 'ud2' (second up/down column)
        self._cart_assignment: dict[str, str] = {"ud1": "X", "lr": "Y", "ud2": "Z"}
        self._axis_classes = {
            "x": "tcp-x",
            "y": "tcp-y",
            "z": "tcp-z",
            "rx": "tcp-rx",
            "ry": "tcp-ry",
            "rz": "tcp-rz",
        }

        # Hybrid click/hold state for joint controls
        self.CLICK_HOLD_THRESHOLD_S: float = 0.25
        self._holding_active: set[tuple[int, str]] = set()
        self._hold_timers: dict[tuple[int, str], ui.timer] = {}

        # Hold timers for cartesian axes (click vs hold)
        self._hold_timers_cart: dict[str, ui.timer] = {}
        self._holding_active_cart: set[str] = set()

        # Jog cadence constants
        self.JOG_TICK_S: float = WEBAPP_CONTROL_INTERVAL_S
        self.CADENCE_WARN_WINDOW: int = max(1, int(WEBAPP_CONTROL_RATE_HZ))
        self.CADENCE_TOLERANCE: float = 0.002
        self.STREAM_TIMEOUT_S: float = 0.1

        # Cadence tracking
        self._tick_stats = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
        self._tick_stats_cart = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}

        # Visual update callback
        self._update_robot_btn_visual: Any = None

        # E-STOP dialog tracking
        self._estop_dialog: ui.dialog | None = None
        self._estop_dialog_is_physical: bool = False
        self._last_estop_state: int = 1  # Track previous io_estop value

    # ---- Helper methods ----

    def _apply_pressed_style(self, widget: ui.element | None, pressed: bool) -> None:
        if not widget:
            return
        if pressed:
            widget.classes(add="is-pressed")
        else:
            widget.classes(remove="is-pressed")

    def _cadence_tick(self, now: float, stats: dict, label: str) -> None:
        last = stats.get("last_ts", 0.0)
        if last > 0.0:
            dt = now - last
            stats["accum"] = stats.get("accum", 0.0) + dt
            stats["count"] = stats.get("count", 0.0) + 1.0
            if stats["count"] >= self.CADENCE_WARN_WINDOW:
                avg = stats["accum"] / stats["count"]
                if abs(avg - self.JOG_TICK_S) > self.CADENCE_TOLERANCE:
                    logging.warning(
                        "[CADENCE] %s avg dt=%.4f s (target=%.4f s, tol=%.4f s)",
                        label,
                        avg,
                        self.JOG_TICK_S,
                        self.CADENCE_TOLERANCE,
                    )
                stats["accum"] = 0.0
                stats["count"] = 0.0
        stats["last_ts"] = now

    def _get_first_pressed_joint(self) -> tuple[int, str] | None:
        """Return (index, 'pos'|'neg') for the first pressed joint, else None."""
        for j in range(6):
            if j < len(self._jog_pressed_pos) and self._jog_pressed_pos[j]:
                return (j, "pos")
            if j < len(self._jog_pressed_neg) and self._jog_pressed_neg[j]:
                return (j, "neg")
        return None

    def _get_first_pressed_axis(self) -> str | None:
        """Return the first pressed cartesian axis key like 'X+' if any."""
        for k, pressed in self._cart_pressed_axes.items():
            if pressed:
                return k
        return None

    def _get_joint_limits(self, i: int) -> tuple[float, float]:
        """Return (lo, hi) for joint i with safe defaults."""
        try:
            limits = JOINT_LIMITS_DEG[i]
            lo = float(limits[0])
            hi = float(limits[1])
            return lo, hi
        except Exception:
            return (-360.0, 360.0)

    # ---- Cartesian helpers (icons, orientation, refresh) ----

    def _axis_color_class_for(self, letter: str, rotation: bool = False) -> str:
        """Return CSS class for axis letter using theme tokens."""
        k = ("r" if rotation else "") + letter.lower()
        return self._axis_classes.get(k, "tcp-x")

    def _axis_string_for(
        self, assign_key: str, sign: str, rotation: bool = False
    ) -> str:
        """Compose axis string like 'X+' or 'RX-' for a given slot assignment."""
        letter = self._cart_assignment.get(assign_key, "X").upper()
        return f"R{letter}{sign}" if rotation else f"{letter}{sign}"

    def _read_icon_svg(self, svg_filename: str) -> tuple[str, list[int]]:
        """Load SVG text via package resources with filesystem fallback and extract viewBox size."""
        raw = ""
        try:
            raw = (
                pkg_resources.files("parol_commander.static.icons") / svg_filename
            ).read_text(encoding="utf-8")
        except Exception:
            icons_dir = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "static", "icons")
            )
            with open(
                os.path.join(icons_dir, svg_filename), "r", encoding="utf-8"
            ) as f:
                raw = f.read()
        m = re.search(r'viewBox="\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*"', raw)
        w1 = int(m.group(1)) if m else 24
        h1 = int(m.group(2)) if m else 24
        w2 = int(m.group(3)) if m else 24
        h2 = int(m.group(4)) if m else 24
        return raw, [w1, h1, w2, h2]

    def _prepare_icon_markup(
        self, raw_svg: str, viewbox_wh: list[int], label: str
    ) -> str:
        """Return wrapped 56x56 SVG markup with enlarged glyph and updated label."""
        # Extract inner SVG content (strip outer <svg>), normalize colors to currentColor
        inner_match = re.search(r"<svg[^>]*>([\s\S]*?)</svg>", raw_svg)
        inner = inner_match.group(1) if inner_match else raw_svg

        # Replace any <text> contents with dynamic label; keep existing fill/stroke (black)
        try:

            def _replace_text_label(svg_str: str, new_label: str) -> str:
                return re.sub(
                    r"(<text\b[^>]*>)(.*?)(</text>)",
                    r"\1" + new_label + r"\3",
                    svg_str,
                    flags=re.DOTALL,
                )

            inner = _replace_text_label(inner, label)
            raw_svg_processed = _replace_text_label(raw_svg, label)
        except Exception:
            raw_svg_processed = raw_svg

        w1, h1, w2, h2 = viewbox_wh
        if w2 == 32 and h2 == 32:
            transform = "translate(-2,-2) scale(0.85)"
        elif w2 == 24 and h2 == 24:
            transform = "translate(-5,-5) scale(1.4)"
        elif h1 == 17:
            transform = "translate(-5,12)"
        else:
            transform = "translate(-5,-12)"
        if h2 == 7:
            svg = f"""<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" style="overflow:visible">
  <g transform="{transform}" fill="currentColor" stroke="currentColor">{raw_svg_processed}</g>
</svg>"""
        elif label == "RZ-":
            svg = f"""<svg viewBox="0 0 24 24" style="transform: scaleX(1);" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
    <g transform="{transform}" fill="currentColor" stroke="currentColor">{inner}</g>
    </svg>"""
        elif label == "Y-":
            svg = f"""<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet" x="0" y="0" style="overflow:visible">
    <g transform="translate(-2,-5) scale(1.4)" fill="currentColor" stroke="currentColor">{inner}</g>
    </svg>"""
        else:
            svg = f"""<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
    <g transform="{transform}" fill="currentColor" stroke="currentColor">{inner}</g>
    </svg>"""
        # Minify whitespace for data URI
        return re.sub(r"\s+", " ", svg).strip()

    async def _on_slot_press(self, slot_id: str, is_pressed: bool) -> None:
        """Event bridge: map fixed slot to current axis string and call set_axis_pressed."""
        meta = self._cart_slot_meta.get(slot_id) or {}
        assign_key = meta.get("assign_key", "ud1")
        sign = meta.get("sign", "+")
        rotation = bool(meta.get("rotation", False))
        axis_str = self._axis_string_for(assign_key, sign, rotation)
        await self.set_axis_pressed(axis_str, bool(is_pressed))

    def set_axis_orientation(self, ud1: str, lr: str, ud2: str) -> None:
        """Update axis assignment for fixed slots and refresh labels/colors/actions."""
        self._cart_assignment["ud1"] = (ud1 or "X").upper()
        self._cart_assignment["lr"] = (lr or "Y").upper()
        self._cart_assignment["ud2"] = (ud2 or "Z").upper()
        self._refresh_cartesian_icons()

    def _refresh_cartesian_icons(self) -> None:
        """Rebuild icon SVGs and color classes for all slots; update axis->element mapping."""
        # Reset axis mapping for pressed visuals
        self._cart_axis_imgs.clear()
        # Known classes to remove
        remove_classes = "tcp-x tcp-y tcp-z tcp-rx tcp-ry tcp-rz"
        for slot_id, elem in self._cart_slot_elems.items():
            meta = self._cart_slot_meta.get(slot_id) or {}
            assign_key = meta.get("assign_key", "ud1")
            sign = meta.get("sign", "+")
            rotation = bool(meta.get("rotation", False))
            raw = meta.get("raw", "")
            vb = meta.get("viewbox", (24, 24))
            axis_str = self._axis_string_for(assign_key, sign, rotation)
            label = axis_str
            markup = self._prepare_icon_markup(raw, vb, label)
            elem.props["name"] = f"img:data:image/svg+xml;charset=utf8,{markup}"
            # Update color classes
            elem.classes(remove=remove_classes)
            letter = self._cart_assignment.get(assign_key, "X").upper()
            elem.classes(add=self._axis_color_class_for(letter, rotation=rotation))
            # Update axis->element map for pressed visuals
            self._cart_axis_imgs[axis_str] = elem

    # ---- Joint jog methods ----

    async def set_joint_pressed(self, j: int, direction: str, is_pressed: bool) -> None:
        """Hybrid click/hold: quick click => single step, press-and-hold => stream until release."""
        # Check if movement is allowed (simulator mode OR connected)
        if not robot_state.simulator_active and not robot_state.connected:
            if is_pressed:
                ui.notify(
                    "Robot mode requires a hardware connection. Connect robot or switch to Simulator mode.",
                    color="negative",
                    icon="error",
                )
            return

        # Visual feedback target
        target = (
            self._joint_right_btns.get(j)
            if direction == "pos"
            else self._joint_left_btns.get(j)
        )
        self._apply_pressed_style(target, bool(is_pressed))

        key = (j, direction)

        def _start_streaming():
            # Mark pressed and turn on jog timer if needed
            if direction == "pos":
                if (
                    isinstance(self._jog_pressed_pos, list)
                    and len(self._jog_pressed_pos) == 6
                ):
                    self._jog_pressed_pos[j] = True
            else:
                if (
                    isinstance(self._jog_pressed_neg, list)
                    and len(self._jog_pressed_neg) == 6
                ):
                    self._jog_pressed_neg[j] = True

            self._holding_active.add(key)
            t = ui_state.joint_jog_timer
            if t:
                if not t.active:
                    self._tick_stats = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
                t.active = True

            tm = self._hold_timers.pop(key, None)
            if tm:
                tm.active = False

        if is_pressed:
            tm_prev = self._hold_timers.pop(key, None)
            if tm_prev:
                tm_prev.active = False
            self._hold_timers[key] = ui.timer(
                self.CLICK_HOLD_THRESHOLD_S, _start_streaming, once=True
            )
            return

        # Release path
        tm = self._hold_timers.pop(key, None)
        was_holding = key in self._holding_active
        if tm and tm.active:
            tm.active = False
            # CLICK => one incremental step
            speed = max(1, min(100, int(ui_state.jog_speed)))
            step = abs(float(ui_state.joint_step_deg))
            index = j if direction == "pos" else (j + 6)
            try:
                await self.client.jog_joint(
                    index, speed_percentage=speed, duration=None, distance_deg=step
                )
            except Exception as e:
                logging.error("Incremental jog failed: %s", e)
            if (
                direction == "pos"
                and isinstance(self._jog_pressed_pos, list)
                and len(self._jog_pressed_pos) == 6
            ):
                self._jog_pressed_pos[j] = False
            if (
                direction == "neg"
                and isinstance(self._jog_pressed_neg, list)
                and len(self._jog_pressed_neg) == 6
            ):
                self._jog_pressed_neg[j] = False
            self._holding_active.discard(key)
            t = ui_state.joint_jog_timer
            if t:
                any_pressed = any(self._jog_pressed_pos) or any(self._jog_pressed_neg)
                t.active = bool(any_pressed)
            return

        if was_holding:
            if (
                direction == "pos"
                and isinstance(self._jog_pressed_pos, list)
                and len(self._jog_pressed_pos) == 6
            ):
                self._jog_pressed_pos[j] = False
            if (
                direction == "neg"
                and isinstance(self._jog_pressed_neg, list)
                and len(self._jog_pressed_neg) == 6
            ):
                self._jog_pressed_neg[j] = False
            self._holding_active.discard(key)
            t = ui_state.joint_jog_timer
            if t:
                any_pressed = any(self._jog_pressed_pos) or any(self._jog_pressed_neg)
                if any_pressed and not t.active:
                    self._tick_stats = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
                t.active = bool(any_pressed)

    async def jog_tick(self) -> None:
        """Timer callback: send/update joint streaming jog if any button is pressed."""
        # Check if movement is allowed
        if not robot_state.simulator_active and not robot_state.connected:
            return

        speed = max(1, min(100, int(ui_state.jog_speed)))
        intent = self._get_first_pressed_joint()
        if intent is not None:
            j, d = intent
            idx = j if d == "pos" else (j + 6)
            await self.client.jog_joint(
                idx, speed_percentage=speed, duration=self.STREAM_TIMEOUT_S
            )
        self._cadence_tick(time.time(), self._tick_stats, "joint")

    # ---- Cartesian jog methods ----

    async def set_axis_pressed(self, axis: str, is_pressed: bool) -> None:
        """Hybrid click/hold for cartesian axes: click => single step, hold => stream."""
        # Check if movement is allowed (simulator mode OR connected)
        if not robot_state.simulator_active and not robot_state.connected:
            if is_pressed:
                ui.notify(
                    "Robot mode requires a hardware connection. Connect robot or switch to Simulator mode.",
                    color="negative",
                    icon="error",
                )
            return

        self._apply_pressed_style(self._cart_axis_imgs.get(axis), bool(is_pressed))
        axes = self._cart_pressed_axes
        if not (isinstance(axes, dict) and isinstance(axis, str)):
            return

        key = axis

        def _start_streaming():
            axes[key] = True
            self._holding_active_cart.add(key)
            t = ui_state.cart_jog_timer
            if t:
                if not t.active:
                    self._tick_stats_cart = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
                t.active = True
            tm = self._hold_timers_cart.pop(key, None)
            if tm:
                tm.active = False

        if is_pressed:
            tm_prev = self._hold_timers_cart.pop(key, None)
            if tm_prev:
                tm_prev.active = False
            # Ensure we have a client context when creating UI elements
            if ui_state.client:
                with ui_state.client:
                    self._hold_timers_cart[key] = ui.timer(
                        self.CLICK_HOLD_THRESHOLD_S, _start_streaming, once=True
                    )
            else:
                self._hold_timers_cart[key] = ui.timer(
                    self.CLICK_HOLD_THRESHOLD_S, _start_streaming, once=True
                )
            return

        tm = self._hold_timers_cart.pop(key, None)
        was_holding = key in self._holding_active_cart
        if tm and tm.active:
            tm.active = False
            # CLICK => one incremental step
            speed = max(1, min(100, int(ui_state.jog_speed)))
            step = max(0.1, min(100.0, float(ui_state.joint_step_deg)))
            duration = max(0.02, min(0.5, step / 50.0))
            frame = cast(Frame, ui_state.frame)
            try:
                await self.client.jog_cartesian(frame, cast(Axis, key), speed, duration)
            except Exception as e:
                logging.error("Incremental cart jog failed: %s", e)
            if isinstance(self._cart_pressed_axes, dict):
                self._cart_pressed_axes[key] = False
            self._holding_active_cart.discard(key)
            t = ui_state.cart_jog_timer
            if t:
                any_pressed = any(bool(v) for v in self._cart_pressed_axes.values())
                t.active = bool(any_pressed)
            return

        if was_holding:
            if isinstance(self._cart_pressed_axes, dict):
                self._cart_pressed_axes[key] = False
            self._holding_active_cart.discard(key)
            t = ui_state.cart_jog_timer
            if t:
                any_pressed = any(bool(v) for v in self._cart_pressed_axes.values())
                if any_pressed and not t.active:
                    self._tick_stats_cart = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
                t.active = bool(any_pressed)

    async def cart_jog_tick(self) -> None:
        """Timer callback: send/update cartesian streaming jog if any axis is pressed."""
        # Check if movement is allowed
        if not robot_state.simulator_active and not robot_state.connected:
            return

        speed = max(1, min(100, int(ui_state.jog_speed)))
        frame = cast(Frame, ui_state.frame)
        axis = self._get_first_pressed_axis()
        if axis is not None:
            await self.client.jog_cartesian(
                frame, cast(Axis, axis), speed, self.STREAM_TIMEOUT_S
            )
        self._cadence_tick(time.time(), self._tick_stats_cart, "cart")

    async def move_joint_to_angle(self, joint_index: int, target_deg: float) -> None:
        """Move a single joint to the specified angle (deg) while holding others."""
        # Check if movement is allowed
        if not robot_state.simulator_active and not robot_state.connected:
            ui.notify(
                "Robot mode requires a hardware connection. Connect robot or switch to Simulator mode.",
                color="negative",
                icon="error",
            )
            return

        try:
            angles = list(robot_state.angles)
            lo, hi = self._get_joint_limits(joint_index)
            tgt = max(lo, min(hi, float(target_deg)))
            pose = angles[:6]
            pose[joint_index] = tgt
            spd = max(1, min(100, int(ui_state.jog_speed)))

            await self.client.move_joints(pose, speed_percentage=spd)
            ui.notify(f"Joint J{joint_index + 1} \u2192 {tgt:.2f}°", color="primary")
        except Exception as e:
            logging.error("Go to joint angle failed: %s", e)
            ui.notify(f"Failed joint move: {e}", color="negative")

    async def go_to_joint_limit(self, joint_index: int, which: str) -> None:
        """Move to min or max joint limit for a specific joint while holding others."""
        # Check if movement is allowed
        if not robot_state.simulator_active and not robot_state.connected:
            ui.notify(
                "Robot mode requires a hardware connection. Connect robot or switch to Simulator mode.",
                color="negative",
                icon="error",
            )
            return

        try:
            angles = list(robot_state.angles)
            limits = JOINT_LIMITS_DEG[joint_index]
            lo, hi = float(limits[0]), float(limits[1])

            target = angles[:6]
            target[joint_index] = float(lo if which == "min" else hi)
            spd = max(1, min(100, int(ui_state.jog_speed)))

            await self.client.move_joints(target, speed_percentage=spd)
            ui.notify(
                f"Joint J{joint_index + 1} \u2192 {'min' if which == 'min' else 'max'}",
                color="primary",
            )
        except Exception as e:
            logging.error("Go to joint limit failed: %s", e)
            ui.notify(f"Failed joint move: {e}", color="negative")

    # ---- Gizmo control methods ----

    def sync_gizmo_to_urdf(self) -> None:
        """Sync gizmo state to URDF scene after it's initialized (called once after scene is ready)."""
        if ui_state.urdf_scene:
            # Apply current gizmo visibility
            ui_state.urdf_scene.set_gizmo_visible(ui_state.gizmo_visible)
            # Apply current gizmo mode (default is Move/TRANSLATE)
            ui_state.urdf_scene.set_gizmo_display_mode("TRANSLATE")
            # Apply current frame
            ui_state.urdf_scene.set_control_frame(ui_state.frame)

    def on_frame_changed(self, new_frame: str) -> None:
        """Visually switch gizmo parenting between WRF and TRF."""
        if ui_state.urdf_scene:
            ui_state.urdf_scene.set_control_frame(new_frame)
            # Refresh cartesian icons so labels/markup stay in sync with frame changes
            self._refresh_cartesian_icons()
        else:
            logging.warning("Cannot switch gizmo frame: URDF scene not initialized")

    def on_gizmo_mode_changed(self, mode: str) -> None:
        """Switch gizmo display mode between Move (translation) and Rotate."""
        if ui_state.urdf_scene is None:
            logging.warning("Cannot change gizmo mode: URDF scene not initialized")
            return
        # Map UI values to internal mode values
        internal_mode = "TRANSLATE" if mode == "Move" else "ROTATE"
        ui_state.urdf_scene.set_gizmo_display_mode(internal_mode)

    async def on_gizmo_toggle(self, visible: bool) -> None:
        """Toggle gizmo visibility."""
        ui_state.gizmo_visible = bool(visible)
        if ui_state.urdf_scene is None:
            logging.warning("Cannot toggle gizmo: URDF scene not initialized")
            return
        ui_state.urdf_scene.set_gizmo_visible(bool(visible))

    # ---- E-STOP dialog methods ----

    def show_estop_dialog(self, is_physical: bool) -> None:
        """Show E-STOP dialog with Lottie animation.

        Args:
            is_physical: True for physical E-STOP (persistent until released),
                        False for digital E-STOP (with Resume button)
        """
        # Close existing dialog if open
        if self._estop_dialog:
            self._estop_dialog.close()
            self._estop_dialog = None

        # Create new dialog
        self._estop_dialog = ui.dialog()
        self._estop_dialog_is_physical = is_physical

        # Make dialog persistent for physical E-STOP
        if is_physical:
            self._estop_dialog.props("persistent")

        with self._estop_dialog, ui.card().classes("gap-4 items-center"):
            # Ensure lottie-player web component is loaded
            ui.html(
                """<lottie-player src="https://lottie.host/b9d2fa51-2204-454e-a882-7647c6712b03/d7w0e81TRh.json" autoplay loop />""",
                sanitize=False,
            ).classes("w-96")

            if is_physical:
                # Physical E-STOP: persistent message
                ui.label("Physical E-STOP Active").classes(
                    "text-xl font-bold text-negative text-center"
                )
                ui.label("The physical E-STOP button was pressed.").classes(
                    "text-center"
                )
                ui.label("To continue, unset the E-STOP button.").classes("text-center")
            else:
                # Digital E-STOP: with Resume button
                ui.label("Digital E-STOP Active").classes(
                    "text-xl font-bold text-warning text-center"
                )
                ui.label("Robot motion has been stopped.").classes("text-center")

                async def resume():
                    try:
                        await self.client.start()
                        ui.notify("Robot enabled - E-STOP cleared", color="positive")
                        if self._estop_dialog:
                            self._estop_dialog.close()
                            self._estop_dialog = None
                    except Exception as e:
                        ui.notify(f"Resume failed: {e}", color="negative")
                        logging.error("Resume after digital E-STOP failed: %s", e)

                with ui.row().classes("gap-2 justify-center w-full mt-4"):
                    ui.button("Resume", on_click=resume).props("color=positive size=lg")

        self._estop_dialog.open()

    def close_estop_dialog(self) -> None:
        """Close the E-STOP dialog if open."""
        if self._estop_dialog:
            self._estop_dialog.close()
            self._estop_dialog = None
            self._estop_dialog_is_physical = False

    def check_estop_state_change(self) -> None:
        """Monitor robot_state.io_estop and show/hide dialog as needed.

        Should be called regularly from main.py's update_ui_from_status().
        """
        current_estop = robot_state.io_estop

        # Detect transition from OK (1) to TRIGGERED (0)
        if self._last_estop_state == 1 and current_estop == 0:
            # Physical E-STOP was just pressed
            self.show_estop_dialog(is_physical=True)

        # Detect transition from TRIGGERED (0) to OK (1)
        elif self._last_estop_state == 0 and current_estop == 1:
            # Physical E-STOP was just released
            if self._estop_dialog and self._estop_dialog_is_physical:
                self.close_estop_dialog()

        # Update last state
        self._last_estop_state = current_estop

    # ---- Robot action methods ----

    async def send_enable(self) -> None:
        try:
            _ = await self.client.enable()
            ui.notify("Sent ENABLE", color="positive")
            logging.info("ENABLE sent")
        except Exception as e:
            logging.error("ENABLE failed: %s", e)

    async def send_disable(self) -> None:
        try:
            _ = await self.client.disable()
            ui.notify("Sent DISABLE", color="warning")
            logging.warning("DISABLE sent")
        except Exception as e:
            logging.error("DISABLE failed: %s", e)

    async def send_home(self) -> None:
        # Check if movement is allowed
        if not robot_state.simulator_active and not robot_state.connected:
            ui.notify(
                "Robot mode requires a hardware connection. Connect robot or switch to Simulator mode.",
                color="negative",
                icon="error",
            )
            return

        try:
            _ = await self.client.home()
            ui.notify("Sent HOME", color="primary")
            logging.info("HOME sent")
        except Exception as e:
            logging.error("HOME failed: %s", e)

    # ---- Rendering methods ----

    def _show_sim_banner(self) -> None:
        """Show persistent simulator mode banner using Quasar notify with dismiss handle."""
        if ui_state.client:
            with ui_state.client:
                ui.run_javascript(
                    """
                    setTimeout(() => {
                        try {
                            window._simBannerDismiss?.();
                            window._simBannerDismiss = Quasar.Notify.create({
                                message: 'Simulator mode - No hardware connection required',
                                position: 'top',
                                timeout: 0,
                                color: 'warning',
                                icon: 'precision_manufacturing'
                            });
                        } catch (e) {
                            console.warn('Simulator banner failed:', e);
                        }
                    }, 0);
                """
                )

    def _hide_sim_banner(self) -> None:
        """Hide simulator mode banner by calling stored dismiss handle."""
        if ui_state.client:
            with ui_state.client:
                ui.run_javascript(
                    """
                    setTimeout(() => {
                        try {
                            window._simBannerDismiss?.();
                            window._simBannerDismiss = null;
                        } catch (e) {
                            console.warn('Banner dismiss failed:', e);
                        }
                    }, 0);
                """
                )

    async def on_toggle_sim(self) -> None:
        """Toggle between robot and simulator modes; show/hide persistent banner."""
        try:
            # Toggle simulator mode and enable
            if not getattr(robot_state, "simulator_active", False):
                await self.client.simulator_on()
                robot_state.simulator_active = True
                # Show persistent simulator banner
                self._show_sim_banner()
                # Enable after switching to simulator
                with contextlib.suppress(Exception):
                    await asyncio.sleep(0.05)  # Brief delay for transport swap
                    await self.client.enable()
            else:
                await self.client.simulator_off()
                robot_state.simulator_active = False
                # Hide simulator banner
                self._hide_sim_banner()
                # Enable after switching back to robot mode
                with contextlib.suppress(Exception):
                    await asyncio.sleep(0.05)  # Brief delay for transport swap
                    await self.client.enable()

            # Update any visual toggle state if present
            if callable(getattr(self, "_update_robot_btn_visual", None)):
                self._update_robot_btn_visual()
        except Exception as ex:
            ui.notify(f"Simulator toggle failed: {ex}", color="negative")

    async def on_estop_click(self) -> None:
        """Trigger digital E-STOP (STOP command) and show dialog."""
        try:
            # Stop robot immediately
            await self.client.stop()
            ui.notify("Digital E-STOP activated - robot disabled", color="warning")
            logging.warning("Digital E-STOP triggered")

            # Show E-STOP dialog with Resume button
            self.show_estop_dialog(is_physical=False)
        except Exception as e:
            logging.error("E-STOP failed: %s", e)
            ui.notify(f"E-STOP failed: {e}", color="negative")

    def render_jog_content(self) -> None:
        """Render jog controls (tabs + grids)."""
        with ui.tabs().props("dense") as jog_mode_tabs:
            joint_tab = ui.tab("Joint jog")
            cart_tab = ui.tab("Cartesian jog")
        jog_mode_tabs.value = joint_tab

        with (
            ui.tab_panels(jog_mode_tabs, value=joint_tab)
            .classes("cp-jog-panels")
            .style("width: 400px; height: 225px")
        ):
            # Joint jog panel
            with ui.tab_panel(joint_tab).classes("gap-1"):
                joint_names = [
                    "Base",
                    "Shoulder",
                    "Elbow",
                    "Wrist 1",
                    "Wrist 2",
                    "Wrist 3",
                ]

                def make_joint_row(idx: int, name: str):
                    with ui.grid(rows="auto", columns="60px auto 80px").classes(
                        "items-center gap-3 w-full"
                    ):
                        ui.label(name).classes("text-right")
                        with ui.row().classes("w-full relative-position"):
                            lo, hi = self._get_joint_limits(idx)
                            bar = (
                                ui.linear_progress(value=0, show_value=False)
                                .props("rounded instant-feedback")
                                .classes("w-full joint-bar")
                            )

                            def _bar_backward(a: Any, i=idx, lo=lo, hi=hi) -> float:
                                if hi <= lo:
                                    return 0.0
                                if (
                                    isinstance(a, list)
                                    and len(a) > i
                                    and isinstance(a[i], (int, float))
                                    and math.isfinite(float(a[i]))
                                ):
                                    return max(
                                        0.0, min(1.0, (float(a[i]) - lo) / (hi - lo))
                                    )
                                return 0.0

                            bar.bind_value_from(
                                robot_state,
                                "angles",
                                backward=_bar_backward,
                            )

                            # Centered numeric input to allow exact setpoint
                            num = (
                                ui.number(
                                    value=0.0,
                                    min=lo,
                                    max=hi,
                                    step=0.1,
                                    format="%.1f",
                                    suffix="°",
                                )
                                .props(
                                    'dense borderless input-style="text-align:right"'
                                )
                                .style(
                                    "position:absolute; left:50%; top:50%; transform:translate(-50%,-50%); width:50px;"
                                )
                            )

                            def _num_backward(a: Any, i=idx) -> float | None:
                                if (
                                    isinstance(a, list)
                                    and len(a) > i
                                    and isinstance(a[i], (int, float))
                                    and math.isfinite(float(a[i]))
                                ):
                                    return float(a[i])
                                return None

                            num.bind_value_from(
                                robot_state,
                                "angles",
                                backward=_num_backward,
                            )

                            def _submit_exact(e=None, i=idx, n=num):
                                try:
                                    val = (
                                        float(n.value) if n.value is not None else None
                                    )
                                except Exception:
                                    val = None
                                if val is not None:
                                    asyncio.create_task(
                                        self.move_joint_to_angle(i, val)
                                    )

                            num.on("blur", _submit_exact)
                            num.on("keydown.enter", _submit_exact)

                            # Left minus pill
                            left_btn = (
                                ui.button(icon="remove")
                                .props("round flat dense no-caps text-color=white")
                                .classes("absolute left-1 joint-cap")
                            )
                            left_btn.mark(f"btn-j{idx + 1}-minus")

                            def check_lower_limit(a, i=idx):
                                if not (
                                    isinstance(a, list)
                                    and len(a) > i
                                    and isinstance(a[i], (int, float))
                                ):
                                    return False
                                step = float(ui_state.joint_step_deg)
                                lo, _hi = self._get_joint_limits(i)
                                return float(a[i]) - step >= lo

                            left_btn.bind_enabled_from(
                                robot_state, "angles", backward=check_lower_limit
                            )
                            left_btn.on(
                                "mousedown",
                                partial(self.set_joint_pressed, idx, "neg", True),
                            )
                            left_btn.on(
                                "mouseup",
                                partial(self.set_joint_pressed, idx, "neg", False),
                            )
                            left_btn.on(
                                "mouseleave",
                                partial(self.set_joint_pressed, idx, "neg", False),
                            )

                            # Right plus pill
                            right_btn = (
                                ui.button(icon="add")
                                .props("round flat dense no-caps text-color=white")
                                .classes("absolute right-1 joint-cap")
                            )
                            right_btn.mark(f"btn-j{idx + 1}-plus")

                            def check_upper_limit(a, i=idx):
                                if not (
                                    isinstance(a, list)
                                    and len(a) > i
                                    and isinstance(a[i], (int, float))
                                ):
                                    return False
                                step = float(ui_state.joint_step_deg)
                                _lo, hi = self._get_joint_limits(i)
                                return float(a[i]) + step <= hi

                            right_btn.bind_enabled_from(
                                robot_state, "angles", backward=check_upper_limit
                            )
                            right_btn.on(
                                "mousedown",
                                partial(self.set_joint_pressed, idx, "pos", True),
                            )
                            right_btn.on(
                                "mouseup",
                                partial(self.set_joint_pressed, idx, "pos", False),
                            )
                            right_btn.on(
                                "mouseleave",
                                partial(self.set_joint_pressed, idx, "pos", False),
                            )

                            self._joint_left_btns[idx] = left_btn
                            self._joint_right_btns[idx] = right_btn
                        with ui.row().classes("justify-end gap-1"):
                            ui.button(
                                icon="first_page",
                                on_click=lambda e, i=idx: asyncio.create_task(
                                    self.go_to_joint_limit(i, "min")
                                ),
                            ).props("round dense").tooltip(
                                "Move to minimum joint limit"
                            )
                            ui.button(
                                icon="last_page",
                                on_click=lambda e, i=idx: asyncio.create_task(
                                    self.go_to_joint_limit(i, "max")
                                ),
                            ).props("round dense").tooltip(
                                "Move to maximum joint limit"
                            )

                for i, n in enumerate(joint_names):
                    make_joint_row(i, n)

            # Cartesian jog panel
            with ui.tab_panel(cart_tab).classes("flex items-center justify-center"):
                # Icons inline with dynamic labels/colors/actions; layout remains fixed
                def _add_slot(
                    slot_id: str,
                    svg_filename: str,
                    assign_key: str,
                    sign: str,
                    rotation: bool,
                ) -> None:
                    raw, vb = self._read_icon_svg(svg_filename)
                    axis_str = self._axis_string_for(assign_key, sign, rotation)
                    label = axis_str
                    markup = self._prepare_icon_markup(raw, vb, label)
                    cont = ui.html(
                        f"""
                        <svg viewBox="0 0 24 24" width="100" height="72"
                            style="cursor:pointer;">
                        <g style="pointer-events:visiblePainted;" fill="currentColor" stroke="currentColor">
                            {markup}
                        </g>
                        </svg>
                        """,
                        sanitize=False,
                    )
                    letter = self._cart_assignment.get(assign_key, "X").upper()
                    cont.classes(self._axis_color_class_for(letter, rotation=rotation))
                    marker = f"axis-{axis_str.replace('+','plus').replace('-','minus').lower()}"
                    cont.mark(marker)
                    # Events: press/hold streaming behavior (don't change)
                    cont.on("mousedown", partial(self._on_slot_press, slot_id, True))
                    cont.on("mouseup", partial(self._on_slot_press, slot_id, False))
                    cont.on("mouseleave", partial(self._on_slot_press, slot_id, False))
                    # Store for refresh
                    self._cart_slot_elems[slot_id] = cont
                    self._cart_slot_meta[slot_id] = {
                        "assign_key": assign_key,
                        "sign": sign,
                        "rotation": rotation,
                        "svg_filename": svg_filename,
                        "raw": raw,
                        "viewbox": vb,
                    }

                # Translation grid (original shape): XY cross with Z column on the right
                with (
                    ui.grid(
                        rows="72px 30px 72px",
                        columns="90px 30px 72px 42px 72px 30px 72px",
                    )
                    .classes("gap-0")
                    .style("place-items: center")
                ):
                    # Row 1:    [UD2+, UD1+, empty, RUD2+, empty, RUD1+, empty]
                    _add_slot("ud2_up", "arrow-small-up-cropped.svg", "ud2", "+", False)
                    _add_slot("ud1_up", "arrow-small-up.svg", "ud1", "+", False)
                    ui.element("div").style("width:30px;height:30px")  # empty
                    _add_slot("r_ud2_plus", "curved-arrow-down.svg", "ud2", "+", True)
                    ui.element("div").style("width:30px;height:30px")  # empty
                    _add_slot("r_ud1_plus", "curved-arrow-down.svg", "ud1", "+", True)
                    ui.element("div").style("width:30px;height:30px")  # empty

                    # Row 2:    [LR-, empty, LR+, empty, empty, RLR+, empty, RLR-]
                    _add_slot("lr_neg", "arrow-small-left.svg", "lr", "-", False)
                    ui.element("div").style("width:30px;height:30px")  # center empty
                    _add_slot("lr_pos", "arrow-small-right.svg", "lr", "+", False)
                    ui.element("div").style("width:30px;height:30px")  # empty
                    _add_slot("r_lr_plus", "curved-arrow-right.svg", "lr", "+", True)
                    ui.element("div").style("width:30px;height:30px")  # empty
                    _add_slot("r_lr_minus", "curved-arrow-left.svg", "lr", "-", True)

                    # Row 3:    [empty, UD1-, UD2-, empty, RUD2-, empty RUD1-, empty]
                    _add_slot(
                        "ud2_down", "arrow-small-down-cropped.svg", "ud2", "-", False
                    )
                    _add_slot("ud1_down", "arrow-small-down.svg", "ud1", "-", False)
                    ui.element("div").style("width:30px;height:30px")  # empty
                    _add_slot("r_ud2_minus", "curved-arrow-up.svg", "ud2", "-", True)
                    ui.element("div").style("width:30px;height:30px")  # empty
                    _add_slot("r_ud1_minus", "curved-arrow-up.svg", "ud1", "-", True)
                    ui.element("div").style("width:30px;height:30px")  # empty

                # Initialize axis->element mapping and ensure visuals reflect current assignment
                self._refresh_cartesian_icons()

    def build(self, anchor: str = "bl") -> None:
        """Render the bottom-left control panel (overlay-bl).

        Args:
            anchor: Position anchor for the panel (e.g., "bl" for bottom-left)
        """
        with ui.card().classes(f"overlay-panel overlay-card overlay-{anchor} gap-1"):
            # Two-column layout: left column with two rows, right column with E-STOP spanning both rows
            with ui.row().classes("gap-2 w-full items-center"):
                # Left column: Speed/Step row + Controls row
                with ui.column().classes("gap-1 flex-grow"):
                    # Speed + Step (single compact row)
                    with ui.row().classes("items-center"):
                        ui.icon("speed", size="md")
                        v_init = max(1, min(10, round(int(ui_state.jog_speed) / 10)))
                        v_rating = ui.rating(max=10, icon="circle", value=v_init)
                        v_rating.on(
                            "update:model-value",
                            lambda e, r=v_rating: setattr(
                                ui_state,
                                "jog_speed",
                                int(max(1, min(10, int(getattr(r, "value", 1) or 1))))
                                * 10,
                            ),
                        )
                        ui.label("Step")
                        ui.number(
                            value=ui_state.joint_step_deg,
                            min=1,
                            max=100.0,
                            step=1,
                            format="%.1f",
                            suffix="°",
                        ).props(
                            'dense borderless hide-bottom-space input-style="text-align:right"'
                        ).bind_value(ui_state, "joint_step_deg")

                    # Home, Robot/Simulator toggle, gizmo controls, and camera reset
                    with ui.row().classes("gap-2 w-full items-center"):
                        ui.button(icon="home", on_click=self.send_home).props(
                            "dense round unelevated"
                        ).tooltip("Return robot to home position").mark("btn-home")

                        # Single-button Robot/Simulator toggle (precision_manufacturing)
                        robot_btn = (
                            ui.button(
                                icon="precision_manufacturing",
                                on_click=self.on_toggle_sim,
                            )
                            .props("round unelevated dense")
                            .tooltip("Toggle between real robot and simulator mode")
                        )
                        robot_btn.mark("btn-robot-toggle")

                        def _update_robot_btn_visual():
                            sim = bool(getattr(robot_state, "simulator_active", False))
                            if sim:
                                robot_btn.props("color=grey-7")
                                robot_btn.classes("glass-btn")
                            else:
                                robot_btn.props("color=primary")
                                robot_btn.classes("glass-btn glass-primary")

                        self._update_robot_btn_visual = (
                            _update_robot_btn_visual  # store for reuse
                        )
                        _update_robot_btn_visual()

                        # Frame switch (WRF/TRF)
                        frame_buttons: dict[str, ui.button] = {}

                        def _update_frame_visual(state: str):
                            for m, btn in frame_buttons.items():
                                btn.props(
                                    "color=primary" if m == state else "color=grey-7"
                                )

                        def set_frame(mode: str):
                            mode = "WRF" if str(mode).upper() == "WRF" else "TRF"
                            ui_state.frame = mode
                            self.on_frame_changed(mode)
                            _update_frame_visual(mode)

                        with ui.button_group().props("rounded"):
                            frame_buttons["WRF"] = (
                                ui.button("WRF", on_click=lambda e: set_frame("WRF"))
                                .props("round unelevated dense")
                                .tooltip("World Reference Frame")
                            )
                            frame_buttons["TRF"] = (
                                ui.button("TRF", on_click=lambda e: set_frame("TRF"))
                                .props("round unelevated dense")
                                .tooltip("Tool Reference Frame")
                            )
                        _update_frame_visual(ui_state.frame)

                        # Gizmo mode button group (rounded icons, single-select)
                        selected = {"value": "Move"}
                        buttons: dict[str, ui.button] = {}

                        def set_gizmo_mode(mode: str):
                            if mode == "Hidden":
                                asyncio.create_task(self.on_gizmo_toggle(False))
                            else:
                                asyncio.create_task(self.on_gizmo_toggle(True))
                                self.on_gizmo_mode_changed(mode)
                            selected["value"] = mode
                            # Update visual state
                            for m, btn in buttons.items():
                                if m == mode:
                                    btn.props("color=primary")
                                else:
                                    btn.props("color=grey-7")

                        with ui.button_group().props("rounded"):
                            buttons["Move"] = (
                                ui.button(
                                    icon="open_with",
                                    on_click=lambda e, m="Move": set_gizmo_mode(m),
                                )
                                .props("round unelevated dense")
                                .tooltip("Translate gizmo mode")
                            )
                            buttons["Rotate"] = (
                                ui.button(
                                    icon="sync",
                                    on_click=lambda e, m="Rotate": set_gizmo_mode(m),
                                )
                                .props("round unelevated dense")
                                .tooltip("Rotate gizmo mode")
                            )
                            buttons["Hidden"] = (
                                ui.button(
                                    icon="visibility_off",
                                    on_click=lambda e, m="Hidden": set_gizmo_mode(m),
                                )
                                .props("round unelevated dense")
                                .tooltip("Hide gizmo")
                            )
                            # Set visual state only - defer actual gizmo calls until URDF scene is ready
                            selected["value"] = "Move"
                            buttons["Move"].props("color=primary")
                            buttons["Rotate"].props("color=grey-7")
                            buttons["Hidden"].props("color=grey-7")

                        # Reset camera button
                        def _reset_cam():
                            try:
                                if ui_state.urdf_scene and ui_state.urdf_scene.scene:
                                    ui_state.urdf_scene.scene.move_camera(
                                        x=0.3,
                                        y=0.3,
                                        z=0.22,
                                        look_at_z=0.22,
                                        duration=0.0,
                                    )
                            except Exception as e:
                                logging.error("Reset camera failed: %s", e)

                        ui.button(icon="view_in_ar", on_click=_reset_cam).props(
                            "round unelevated dense"
                        ).tooltip("Reset camera to default view")

                # Right column: Large E-STOP spanning both rows
                ui.button(
                    icon="dangerous", color="negative", on_click=self.on_estop_click
                ).props("round unelevated").classes(
                    "glass-btn glass-negative text-2xl"
                ).tooltip("Emergency stop - immediately halt all robot motion").mark(
                    "btn-estop"
                )

            # Jog controls (tabs + grids)
            self.render_jog_content()
