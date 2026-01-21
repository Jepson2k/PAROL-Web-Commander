"""Bottom-left control panel component for jog speed, step size, and robot control buttons."""

import asyncio
import contextlib
import logging
import time
import os
import re
import math
from functools import partial
from typing import Any, List, cast
import importlib.resources as pkg_resources

from nicegui import ui, app
from parol6 import AsyncRobotClient

from parol_commander.constants import (
    JOINT_LIMITS_DEG,
    config,
)
from parol_commander.state import (
    readiness_state,
    robot_state,
    ui_state,
    global_phase_timer,
)
from parol_commander.services.motion_recorder import motion_recorder
from parol_commander.components.settings import SettingsContent
from parol6.protocol.types import Axis, Frame
from parol6.config import HOME_ANGLES_DEG

# Module-level constants (avoid recreation every frame)
_AXIS_ORDER = (
    "X+",
    "X-",
    "Y+",
    "Y-",
    "Z+",
    "Z-",
    "RX+",
    "RX-",
    "RY+",
    "RY-",
    "RZ+",
    "RZ-",
)
_AXIS_MAP = {"X": 0, "Y": 1, "Z": 2, "RX": 3, "RY": 4, "RZ": 5}


class ControlPanel:
    """Bottom-left control panel for jog settings and robot control."""

    def __init__(self, client: AsyncRobotClient) -> None:
        """Initialize control panel with jog state and required robot client."""
        self.client = client
        self._ui_client: Any = None  # NiceGUI client for background task UI ops

        # Jog UI references
        self._joint_left_btns: dict[int, ui.button] = {}
        self._joint_right_btns: dict[int, ui.button] = {}
        self._joint_limit_btns: dict[
            tuple[int, str], ui.button
        ] = {}  # (joint_idx, "min"/"max") -> button
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
        self.JOG_TICK_S: float = config.webapp_control_interval_s
        self.CADENCE_WARN_WINDOW: int = max(1, int(config.webapp_control_rate_hz))
        self.CADENCE_TOLERANCE: float = 0.015  # 15mm
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
        self._digital_estop_active: bool = False  # Track if digital estop was active

        # TCP TransformControls drag state
        self._tcp_latest_pose: list[float] | None = None
        self._tcp_last_sent_pose: list[float] | None = (
            None  # Track last sent to avoid duplicates
        )
        self._tcp_drag_active: bool = False

        # Step input widget reference for dynamic suffix/tooltip
        self._step_input: ui.number | None = None
        self._step_input_tooltip: ui.tooltip | None = None
        self._jog_mode_tabs: Any = None

        # Dirty checking caches for button enablement (avoid redundant CSS updates)
        self._last_joint_en_tuple: tuple[int, ...] | None = None
        self._last_cart_en_tuple: tuple[int, ...] | None = None
        self._last_editing_mode: bool | None = None

        # Pending jog end wait task (to prevent spawning multiple concurrent wait tasks)
        self._jog_end_wait_task: asyncio.Task | None = None

        # Preallocated buffer for cartesian jog target to avoid GC pressure
        self._cart_target_buffer: list[float] = [0.0] * 6

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
            if self._jog_pressed_pos[j]:
                return (j, "pos")
            if self._jog_pressed_neg[j]:
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
        self, raw_svg: str, viewbox_wh: list[int], label: str, slot_id: str = ""
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
        elif slot_id == "r_ud2_minus":
            svg = f"""<svg viewBox="0 0 24 24" style="transform: scaleX(1);" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
    <g transform="{transform}" fill="currentColor" stroke="currentColor">{inner}</g>
    </svg>"""
        elif slot_id == "lr_neg":
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
            markup = self._prepare_icon_markup(raw, vb, label, slot_id)
            # Update HTML element content (ui.html elements, not ui.icon)
            new_html = f"""
            <svg viewBox="0 0 24 24" width="100" height="72" style="cursor:pointer;">
            <g style="pointer-events:visiblePainted;" fill="currentColor" stroke="currentColor">
                {markup}
            </g>
            </svg>
            """
            # For ui.html elements, update the content property and trigger update
            if hasattr(elem, "_props") and "content" in elem._props:
                elem._props["content"] = new_html
                elem.update()
            elif hasattr(elem, "content"):
                elem.content = new_html  # type: ignore[attr-defined]
            # Update color classes
            elem.classes(remove=remove_classes)
            letter = self._cart_assignment.get(assign_key, "X").upper()
            elem.classes(add=self._axis_color_class_for(letter, rotation=rotation))
            # Update axis->element map for pressed visuals
            self._cart_axis_imgs[axis_str] = elem

    # ---- Enablement and visuals ----

    def _set_strong_disabled(self, elem: ui.element | None, disabled: bool) -> None:
        if not elem:
            return
        if disabled:
            elem.classes(add="cp-disabled-strong")
        else:
            elem.classes(remove="cp-disabled-strong")

    def refresh_joint_enablement(self) -> None:
        """Apply stronger disabled visuals to joint +/- buttons using robot_state.joint_en."""
        # Get current state for dirty checking
        editing_mode = robot_state.editing_mode
        en = robot_state.joint_en
        current_tuple = tuple(en) if len(en) == 12 else None

        # Skip if state unchanged (18x faster when idle)
        if (
            editing_mode == self._last_editing_mode
            and current_tuple == self._last_joint_en_tuple
        ):
            return

        # If in editing mode, disable all buttons regardless of normal enablement
        if editing_mode:
            for btn in self._joint_left_btns.values():
                self._set_strong_disabled(btn, True)
            for btn in self._joint_right_btns.values():
                self._set_strong_disabled(btn, True)
            self._last_editing_mode = editing_mode
            self._last_joint_en_tuple = current_tuple
            return

        if current_tuple is None:
            return

        for j in range(6):
            plus_allowed = bool(en[2 * j])
            minus_allowed = bool(en[2 * j + 1])
            self._set_strong_disabled(self._joint_right_btns.get(j), not plus_allowed)
            self._set_strong_disabled(self._joint_left_btns.get(j), not minus_allowed)

        self._last_editing_mode = editing_mode
        self._last_joint_en_tuple = current_tuple

    def sync_cartesian_button_states(self) -> None:
        """Apply stronger disabled visuals to axis icons using CART_EN for current frame and mirror to 3D gizmo."""
        # Get current state for dirty checking
        editing_mode = robot_state.editing_mode
        frame = str(ui_state.frame).upper() if ui_state.frame else "WRF"
        en = robot_state.cart_en_trf if frame == "TRF" else robot_state.cart_en_wrf
        current_tuple = tuple(en) if len(en) == 12 else None

        # Skip if state unchanged
        if (
            editing_mode == self._last_editing_mode
            and current_tuple == self._last_cart_en_tuple
        ):
            return

        # If in editing mode, disable all cartesian buttons regardless of normal enablement
        if editing_mode:
            for ax in _AXIS_ORDER:
                elem = self._cart_axis_imgs.get(ax)
                self._set_strong_disabled(elem, True)
            for elem in self._cart_slot_elems.values():
                self._set_strong_disabled(elem, True)
            self._last_editing_mode = editing_mode
            self._last_cart_en_tuple = current_tuple
            return

        if current_tuple is None:
            return

        # 2D icons
        for i, ax in enumerate(_AXIS_ORDER):
            elem = self._cart_axis_imgs.get(ax)
            self._set_strong_disabled(elem, not bool(en[i]))

        self._last_editing_mode = editing_mode
        self._last_cart_en_tuple = current_tuple

    def refresh_editing_mode_enablement(self) -> None:
        """Disable jog buttons when in editing mode (E-STOP stays enabled)."""
        is_editing = robot_state.editing_mode
        # Disable joint buttons
        for btn in self._joint_left_btns.values():
            self._set_strong_disabled(btn, is_editing)
        for btn in self._joint_right_btns.values():
            self._set_strong_disabled(btn, is_editing)
        # Disable joint limit buttons (go-to-limit)
        for btn in self._joint_limit_btns.values():
            self._set_strong_disabled(btn, is_editing)
        # Disable cartesian slot elements
        for elem in self._cart_slot_elems.values():
            self._set_strong_disabled(elem, is_editing)
        # Note: E-STOP button is NOT in these collections, so remains enabled

    # ---- Joint jog methods ----

    async def set_joint_pressed(self, j: int, direction: str, is_pressed: bool) -> None:
        """Hybrid click/hold: quick click => single step, press-and-hold => stream until release."""
        # Skip if in editing mode (target editor controls robot)
        if robot_state.editing_mode:
            return

        # Check if movement is allowed (simulator mode OR connected)
        if not robot_state.simulator_active and not robot_state.connected:
            if is_pressed:
                ui.notify(
                    "Robot mode requires a hardware connection. Connect robot or switch to Simulator mode.",
                    color="negative",
                    icon="error",
                )
            return

        # Notify motion recorder of jog start/end
        sign = "+" if direction == "pos" else "-"
        axis_info = f"J{j + 1}{sign}"
        if is_pressed:
            motion_recorder.on_jog_start("joint", axis_info)
        else:
            self._schedule_jog_end_wait()

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
                self._jog_pressed_pos[j] = True
            else:
                self._jog_pressed_neg[j] = True

            self._holding_active.add(key)
            if not ui_state.joint_jog_timer.active:
                self._tick_stats = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
            ui_state.joint_jog_timer.active = True

            tm = self._hold_timers.pop(key, None)
            if tm:
                tm.active = False

        if is_pressed:
            tm_prev = self._hold_timers.pop(key, None)
            if tm_prev:
                tm_prev.active = False

            # Enforce mutual exclusivity: clear opposite direction for this joint
            other_dir = "neg" if direction == "pos" else "pos"
            other_key = (j, other_dir)
            # Cancel any pending hold timer for the opposite direction
            tm_other = self._hold_timers.pop(other_key, None)
            if tm_other:
                tm_other.active = False
            # Clear streaming/click-hold state for the opposite direction
            self._holding_active.discard(other_key)
            if other_dir == "pos":
                self._jog_pressed_pos[j] = False
                # Remove pressed visual from opposite button
                other_btn = self._joint_right_btns.get(j)
                self._apply_pressed_style(other_btn, False)
            else:
                self._jog_pressed_neg[j] = False
                other_btn = self._joint_left_btns.get(j)
                self._apply_pressed_style(other_btn, False)

            self._hold_timers[key] = ui.timer(
                self.CLICK_HOLD_THRESHOLD_S, _start_streaming, once=True
            )
            return

        # Release path
        tm = self._hold_timers.pop(key, None)
        was_holding = key in self._holding_active
        if tm and tm.active:
            tm.active = False
            # CLICK => one incremental step using move_joints for precision
            speed = max(1, min(100, int(ui_state.jog_speed)))
            step = abs(float(ui_state.joint_step_deg))
            try:
                # Get current angles and calculate target
                angles = list(robot_state.angles.deg)
                if len(angles) >= 6:
                    target_angles = angles[:6]
                    lo, hi = self._get_joint_limits(j)
                    if direction == "pos":
                        target_angles[j] = min(hi, target_angles[j] + step)
                    else:
                        target_angles[j] = max(lo, target_angles[j] - step)
                    await self.client.move_joints(target_angles, speed=speed)
            except Exception as e:
                logging.error("Incremental joint move failed: %s", e)
            if direction == "pos":
                self._jog_pressed_pos[j] = False
            else:
                self._jog_pressed_neg[j] = False
            self._holding_active.discard(key)
            any_pressed = any(self._jog_pressed_pos) or any(self._jog_pressed_neg)
            ui_state.joint_jog_timer.active = bool(any_pressed)
            return

        if was_holding:
            if direction == "pos":
                self._jog_pressed_pos[j] = False
            else:
                self._jog_pressed_neg[j] = False
            self._holding_active.discard(key)
            any_pressed = any(self._jog_pressed_pos) or any(self._jog_pressed_neg)
            if any_pressed and not ui_state.joint_jog_timer.active:
                self._tick_stats = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
            ui_state.joint_jog_timer.active = bool(any_pressed)

    async def jog_tick(self) -> None:
        """Timer callback: send/update joint streaming jog if any button is pressed."""
        with global_phase_timer.phase("jog"):
            # Check if movement is allowed
            if not robot_state.simulator_active and not robot_state.connected:
                return

            speed = max(1, min(100, int(ui_state.jog_speed)))
            intent = self._get_first_pressed_joint()
            if intent is not None:
                j, d = intent
                idx = j if d == "pos" else (j + 6)
                await self.client.jog_joint(
                    idx, speed=speed, duration=self.STREAM_TIMEOUT_S
                )
            self._cadence_tick(time.time(), self._tick_stats, "joint")

    # ---- Cartesian jog methods ----

    async def set_axis_pressed(self, axis: str, is_pressed: bool) -> None:
        """Hybrid click/hold for cartesian axes: click => single step, hold => stream."""
        # Skip if in editing mode (target editor controls robot)
        if robot_state.editing_mode:
            return

        # Check if movement is allowed (simulator mode OR connected)
        if not robot_state.simulator_active and not robot_state.connected:
            if is_pressed:
                ui.notify(
                    "Robot mode requires a hardware connection. Connect robot or switch to Simulator mode.",
                    color="negative",
                    icon="error",
                )
            return

        # Notify motion recorder of jog start/end
        if is_pressed:
            motion_recorder.on_jog_start("cartesian", axis)
        else:
            self._schedule_jog_end_wait()

        # Check backend-reported enablement for this axis in current frame
        en_list = (
            robot_state.cart_en_trf
            if str(getattr(ui_state, "frame", "WRF")).upper() == "TRF"
            else robot_state.cart_en_wrf
        )
        allowed = True
        if len(en_list) == 12 and axis in _AXIS_ORDER:
            idx = _AXIS_ORDER.index(axis)
            allowed = bool(int(en_list[idx]))
        # Apply strong disabled visual if not allowed and ignore press
        self._set_strong_disabled(self._cart_axis_imgs.get(axis), not allowed)
        if is_pressed and not allowed:
            return

        self._apply_pressed_style(self._cart_axis_imgs.get(axis), bool(is_pressed))

        key = axis

        def _start_streaming():
            self._cart_pressed_axes[key] = True
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
            if self._ui_client:
                with self._ui_client:
                    self._hold_timers_cart[key] = ui.timer(
                        self.CLICK_HOLD_THRESHOLD_S, _start_streaming, once=True
                    )
            return

        tm = self._hold_timers_cart.pop(key, None)
        was_holding = key in self._holding_active_cart
        if tm and tm.active:
            tm.active = False
            # CLICK => one incremental step using move_cartesian for precision
            speed = max(1, min(100, int(ui_state.jog_speed)))
            step = max(0.1, min(100.0, float(ui_state.joint_step_deg)))
            try:
                # Get current position and calculate target
                # Parse axis string: "X+", "X-", "RX+", "RZ-", etc.
                axis_letter = key.rstrip("+-")  # "X", "Y", "Z", "RX", "RY", "RZ"
                direction = 1.0 if key.endswith("+") else -1.0

                # Fill preallocated target buffer from current state
                self._cart_target_buffer[0] = float(robot_state.x)
                self._cart_target_buffer[1] = float(robot_state.y)
                self._cart_target_buffer[2] = float(robot_state.z)
                self._cart_target_buffer[3] = float(robot_state.rx)
                self._cart_target_buffer[4] = float(robot_state.ry)
                self._cart_target_buffer[5] = float(robot_state.rz)

                # Apply step to the appropriate axis
                if axis_letter in _AXIS_MAP:
                    idx = _AXIS_MAP[axis_letter]
                    self._cart_target_buffer[idx] += direction * step
                    await self.client.move_cartesian(
                        self._cart_target_buffer,
                        speed=float(speed),
                        accel=float(ui_state.jog_accel),
                    )
            except Exception as e:
                logging.error("Incremental cart move failed: %s", e)
            self._cart_pressed_axes[key] = False
            self._holding_active_cart.discard(key)
            t = ui_state.cart_jog_timer
            if t:
                any_pressed = any(bool(v) for v in self._cart_pressed_axes.values())
                t.active = bool(any_pressed)
            return

        if was_holding:
            self._cart_pressed_axes[key] = False
            self._holding_active_cart.discard(key)
            t = ui_state.cart_jog_timer
            if t:
                any_pressed = any(bool(v) for v in self._cart_pressed_axes.values())
                if any_pressed and not t.active:
                    self._tick_stats_cart = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
                t.active = bool(any_pressed)

    async def cart_jog_tick(self) -> None:
        """Timer callback: unified movement timer for TransformControls drag or cartesian jog."""
        with global_phase_timer.phase("jog"):
            # Check if movement is allowed
            if not robot_state.simulator_active and not robot_state.connected:
                return

            speed = max(1, min(100, int(ui_state.jog_speed)))

            # Priority 1: TransformControls drag actively providing absolute poses
            if self._tcp_drag_active and self._tcp_latest_pose:
                # Only send if pose has changed (avoid flooding with duplicates)
                if self._tcp_last_sent_pose is not None:
                    # Compare with small epsilon for floating-point tolerance
                    epsilon = 0.01  # 0.01mm position / 0.01deg rotation tolerance
                    pose_changed = False
                    for i in range(6):
                        if (
                            abs(self._tcp_latest_pose[i] - self._tcp_last_sent_pose[i])
                            > epsilon
                        ):
                            pose_changed = True
                            break
                    if not pose_changed:
                        # Pose hasn't changed, skip sending
                        self._cadence_tick(time.time(), self._tick_stats_cart, "cart")
                        return
                else:
                    logging.debug("TCP Drag: First move (no last sent pose)")

                try:
                    # Use speed for stream blending. The server enforces a
                    # minimum 200ms duration to keep commands alive long enough for
                    # subsequent updates to blend in, creating a "mouse trail" effect.
                    await self.client.move_cartesian(
                        list(self._tcp_latest_pose[:6]),
                        speed=float(speed),
                        accel=float(ui_state.jog_accel),
                    )
                    # Track what we sent to avoid duplicates
                    self._tcp_last_sent_pose = list(self._tcp_latest_pose[:6])
                except Exception as e:
                    logging.debug("TCP Cartesian move (timer) failed: %s", e)
                self._cadence_tick(time.time(), self._tick_stats_cart, "cart")
                return

            # Priority 2: legacy cart jog buttons (streamed)
            # Use WRF for translation (X,Y,Z) and TRF for rotation (Rx,Ry,Rz)
            axis = self._get_first_pressed_axis()
            if axis is not None:
                axis_str = str(axis).upper()
                frame: Frame = "TRF" if axis_str.startswith("R") else "WRF"
                await self.client.jog_cartesian(
                    frame, cast(Axis, axis), speed, self.STREAM_TIMEOUT_S
                )
            self._cadence_tick(time.time(), self._tick_stats_cart, "cart")

    def _handle_tcp_cartesian_move_start(self) -> None:
        """Handle start of a TCP TransformControls drag.

        Ensures drag state is reset so that even small initial movements are registered.
        """
        logging.debug("TCP Drag: START event received")
        if not robot_state.simulator_active and not robot_state.connected:
            return

        # Force a fresh start for the drag session
        self._tcp_last_sent_pose = None

        # Start drag session and recorder if not already active
        if not self._tcp_drag_active:
            self._tcp_drag_active = True
            motion_recorder.on_jog_start("cartesian", "TCP")

        # Ensure movement timer is active
        t = ui_state.cart_jog_timer
        if t and not t.active:
            self._tick_stats_cart = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
            t.active = True

    def _handle_tcp_cartesian_move(self, pose: List[float]) -> None:
        """Handle TCP Cartesian move events from TransformControls drag operations.

        This sets the latest target pose and ensures the movement timer sends it.
        Recording starts on first drag event and ends on drag-end.
        Used for WRF (World Reference Frame) mode.
        """
        # Check if movement is allowed
        if not robot_state.simulator_active and not robot_state.connected:
            return

        if len(pose) < 6:
            logging.warning("Invalid pose length for Cartesian move: %d", len(pose))
            return

        # Cache latest target pose (x,y,z in mm, rx,ry,rz in deg)
        self._tcp_latest_pose = list(pose[:6])

        # Start drag session (once) and recorder
        if not self._tcp_drag_active:
            logging.debug("TCP Drag: Move received while inactive (implicit start)")
            self._tcp_drag_active = True
            # Implicit start: force reset last sent pose to ensure first move is sent
            self._tcp_last_sent_pose = None
            motion_recorder.on_jog_start("cartesian", "TCP")

        # Ensure movement timer is active
        t = ui_state.cart_jog_timer
        if t and not t.active:
            self._tick_stats_cart = {"last_ts": 0.0, "accum": 0.0, "count": 0.0}
            t.active = True

    def _handle_tcp_cartesian_move_end(self) -> None:
        """End of a TCP TransformControls drag: wait for motion to stop, then record."""
        logging.debug("TCP Drag: END event received")
        if self._tcp_drag_active:
            self._tcp_drag_active = False
            self._schedule_jog_end_wait()
        # Clear last sent pose so next drag starts fresh
        self._tcp_last_sent_pose = None
        # If no cart axis buttons are pressed, allow timer to stop
        t = ui_state.cart_jog_timer
        if t:
            any_pressed = any(bool(v) for v in self._cart_pressed_axes.values())
            t.active = bool(any_pressed)

    def _schedule_jog_end_wait(self) -> None:
        """Schedule a jog end wait task if one isn't already running."""
        # Skip if a wait task is already pending
        if self._jog_end_wait_task is not None and not self._jog_end_wait_task.done():
            return
        self._jog_end_wait_task = asyncio.create_task(self._wait_and_record_jog_end())

    async def _wait_and_record_jog_end(self) -> None:
        """Wait for robot motion to stop, then record the jog end position."""
        try:
            await self.client.wait_motion_complete(timeout=5.0, settle_window=0.2)
            logging.debug("Jog: Motion stopped, recording position")
        except Exception as e:
            logging.warning("Jog: wait_motion_complete failed: %s", e)
        finally:
            self._jog_end_wait_task = None
        motion_recorder.on_jog_end()

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
            angles = list(robot_state.angles.deg)
            lo, hi = self._get_joint_limits(joint_index)
            tgt = max(lo, min(hi, float(target_deg)))
            pose = angles[:6]
            pose[joint_index] = tgt
            spd = max(1, min(100, int(ui_state.jog_speed)))

            await self.client.move_joints(pose, speed=spd)
            ui.notify(f"Joint J{joint_index + 1} \u2192 {tgt:.2f}°", color="primary")
        except Exception as e:
            logging.error("Go to joint angle failed: %s", e)
            ui.notify(f"Failed joint move: {e}", color="negative")

    async def go_to_joint_limit(self, joint_index: int, which: str) -> None:
        """Move to min or max joint limit for a specific joint while holding others."""
        # Skip if in editing mode (target editor controls robot)
        if robot_state.editing_mode:
            return

        # Check if movement is allowed
        if not robot_state.simulator_active and not robot_state.connected:
            ui.notify(
                "Robot mode requires a hardware connection. Connect robot or switch to Simulator mode.",
                color="negative",
                icon="error",
            )
            return

        try:
            angles = list(robot_state.angles.deg)
            limits = JOINT_LIMITS_DEG[joint_index]
            lo, hi = float(limits[0]), float(limits[1])

            target = angles[:6]
            target[joint_index] = float(lo if which == "min" else hi)
            spd = max(1, min(100, int(ui_state.jog_speed)))

            await self.client.move_joints(target, speed=spd)
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
            # Fixed WRF orientation for cartesian UI layout
            # WRF: X (red) horizontal (lr), Y (green) vertical (ud1), Z (blue) vertical (ud2)
            self.set_axis_orientation("Y", "X", "Z")
            # Apply enablement visuals
            self.sync_cartesian_button_states()

            # Register Cartesian move callback for direct TCP position moves
            ui_state.urdf_scene.on_tcp_cartesian_move(self._handle_tcp_cartesian_move)
            # Register drag start/end to manage state
            ui_state.urdf_scene.on_tcp_cartesian_move_start(
                self._handle_tcp_cartesian_move_start
            )
            ui_state.urdf_scene.on_tcp_cartesian_move_end(
                self._handle_tcp_cartesian_move_end
            )
            # Note: set_gizmo_visible() already enables TransformControls when visible,
            # so no need for an additional enable_tcp_transform_controls() call here.

    def on_gizmo_mode_changed(self, mode: str) -> None:
        """Switch gizmo display mode between Move (translation) and Rotate."""
        if ui_state.urdf_scene is None:
            logging.warning("Cannot change gizmo mode: URDF scene not initialized")
            return
        # Map UI values to internal mode values
        internal_mode = "TRANSLATE" if mode == "Move" else "ROTATE"
        ui_state.urdf_scene.set_gizmo_display_mode(internal_mode)
        # Update TCP TransformControls mode (translate or rotate)
        tcp_mode = "translate" if mode == "Move" else "rotate"
        ui_state.urdf_scene.set_tcp_transform_mode(tcp_mode)

    async def on_gizmo_toggle(self, visible: bool) -> None:
        """Toggle gizmo visibility and TCP TransformControls."""
        ui_state.gizmo_visible = bool(visible)
        if ui_state.urdf_scene is None:
            logging.warning("Cannot toggle gizmo: URDF scene not initialized")
            return
        ui_state.urdf_scene.set_gizmo_visible(bool(visible))
        # Enable/disable TCP TransformControls based on visibility
        if visible:
            # Re-enable with current mode
            # Determine mode from current transform mode (lowercase: "translate" or "rotate")
            mode = ui_state.urdf_scene._tcp_transform_mode or "translate"
            ui_state.urdf_scene.enable_tcp_transform_controls(mode)
        else:
            ui_state.urdf_scene.disable_tcp_transform_controls()

    # ---- E-STOP dialog methods ----

    def show_estop_dialog(self, is_physical: bool) -> None:
        """Show E-STOP dialog with Lottie animation.

        Args:
            is_physical: True for physical E-STOP (persistent until released),
                        False for digital E-STOP (with Resume button)
        """
        if not self._ui_client:
            return

        with self._ui_client:
            # If replacing a digital estop dialog with physical, remember digital is pending
            if (
                is_physical
                and self._estop_dialog
                and not self._estop_dialog_is_physical
            ):
                self._digital_estop_active = True

            # Close existing dialog if open
            if self._estop_dialog:
                self._estop_dialog.close()
                self._estop_dialog = None

            # Create new dialog
            self._estop_dialog = ui.dialog()
            self._estop_dialog_is_physical = is_physical

            # Both dialog types are persistent - physical requires button release, digital requires Resume
            self._estop_dialog.props("persistent")

            with (
                self._estop_dialog,
                ui.card()
                .classes("overlay-card gap-4 items-center")
                .mark("estop-dialog"),
            ):
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
                    ui.label("To continue, unset the E-STOP button.").classes(
                        "text-center"
                    )
                else:
                    # Digital E-STOP: with Resume button
                    ui.label("Digital E-STOP Active").classes(
                        "text-xl font-bold text-warning text-center"
                    )
                    ui.label("Robot motion has been stopped.").classes("text-center")

                    async def resume():
                        try:
                            await self.client.start()
                            self._digital_estop_active = (
                                False  # Clear digital estop state
                            )
                            if self._estop_dialog:
                                self._estop_dialog.close()
                                self._estop_dialog = None
                        except Exception as e:
                            logging.error("Resume after digital E-STOP failed: %s", e)

                    with ui.row().classes("gap-2 justify-center w-full mt-4"):
                        ui.button("Resume", on_click=resume).props(
                            "color=positive size=lg"
                        ).mark("btn-estop-resume")

            self._estop_dialog.open()

    def close_estop_dialog(self) -> None:
        """Close the E-STOP dialog if open."""
        if not self._ui_client:
            return

        with self._ui_client:
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
            logging.warning("Physical E-STOP detected (io_estop 1->0)")
            # Physical E-STOP was just pressed
            self.show_estop_dialog(is_physical=True)

        # Detect transition from TRIGGERED (0) to OK (1)
        elif self._last_estop_state == 0 and current_estop == 1:
            logging.info("Physical E-STOP released (io_estop 0->1)")
            # Physical E-STOP was just released
            if self._estop_dialog and self._estop_dialog_is_physical:
                self.close_estop_dialog()
                # If digital estop was active before physical, restore digital dialog
                if self._digital_estop_active:
                    self.show_estop_dialog(is_physical=False)

        # Update last state
        self._last_estop_state = current_estop

    # ---- Robot action methods ----

    async def send_home(self) -> None:
        # In editing mode, move the editing robot to home position
        if robot_state.editing_mode:
            if ui_state.urdf_scene:
                # Home position from PAROL6 config (degrees -> radians)
                home_angles_rad = [math.radians(deg) for deg in HOME_ANGLES_DEG]
                ui_state.urdf_scene.set_editing_angles(home_angles_rad)
                # Sync robot_state with new editing values
                if hasattr(ui_state.urdf_scene, "_sync_robot_state_from_editing"):
                    ui_state.urdf_scene._sync_robot_state_from_editing()
                # Update edit bar values if present
                if (
                    hasattr(ui_state.urdf_scene, "_current_editing_type")
                    and ui_state.urdf_scene._current_editing_type
                ):
                    if hasattr(ui_state.urdf_scene, "_update_edit_bar_values"):
                        ui_state.urdf_scene._update_edit_bar_values(
                            ui_state.urdf_scene._current_editing_type
                        )
                logging.info("HOME sent to editing robot")
            return

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
            logging.info("HOME sent")

            # Record the home action if recording is active
            motion_recorder.record_action("home")
        except Exception as e:
            logging.error("HOME failed: %s", e)

    def _is_urdf_scene_valid(self) -> bool:
        """Check if urdf_scene exists and its client is still valid."""
        if not ui_state.urdf_scene:
            return False
        scene = getattr(ui_state.urdf_scene, "scene", None)
        if not scene:
            return False
        try:
            scene_client = scene._client()
            if scene_client is None or scene_client._deleted:
                return False
        except (RuntimeError, AttributeError):
            return False
        return True

    async def on_toggle_sim(self) -> None:
        """Toggle between robot and simulator modes and update URDF appearance."""
        try:
            # Reset simulator_ready event before toggle so tests can wait for it
            readiness_state.reset_simulator_ready()

            # Stop any running user script before mode switch (safety)
            editor_panel = getattr(ui_state, "editor_panel", None)
            if editor_panel and getattr(editor_panel, "script_running", False):
                logging.info("Stopping running script before mode switch")
                with contextlib.suppress(Exception):
                    await editor_panel._stop_script_process()

            # Toggle simulator mode and enable
            if not robot_state.simulator_active:
                await self.client.simulator_on()
                robot_state.simulator_active = True
                # Apply simulator visual appearance to URDF scene (amber ghosting)
                if self._is_urdf_scene_valid() and ui_state.urdf_scene:
                    ui_state.urdf_scene.set_simulator_appearance(True)
                # Enable after switching to simulator
                # (no delay needed - controller waits for first frame before responding OK)
                try:
                    await self.client.enable()
                except Exception as e:
                    logging.warning("Enable after simulator on failed: %s", e)
            else:
                await self.client.simulator_off()
                robot_state.simulator_active = False
                # Restore default URDF appearance (remove simulator ghosting)
                if self._is_urdf_scene_valid() and ui_state.urdf_scene:
                    ui_state.urdf_scene.set_simulator_appearance(False)
                # Enable after switching back to robot mode
                try:
                    await self.client.enable()
                except Exception as e:
                    logging.warning("Enable after simulator off failed: %s", e)

            # Update any visual toggle state if present
            if callable(getattr(self, "_update_robot_btn_visual", None)):
                self._update_robot_btn_visual()
        except Exception as ex:
            ui.notify(f"Simulator toggle failed: {ex}", color="negative")
            logging.error("Simulator toggle failed: %s", ex)

    async def on_estop_click(self) -> None:
        """Trigger digital E-STOP (STOP command) and show dialog."""
        # Don't allow digital estop while physical estop is active
        if robot_state.io_estop == 0:
            ui.notify("Physical E-STOP is active - release it first", color="warning")
            return

        # Stop robot immediately
        await self.client.stop()
        self._digital_estop_active = True

        # Show E-STOP dialog with Resume button (needs UI context)
        if self._ui_client:
            with self._ui_client:
                self.show_estop_dialog(is_physical=False)

    def render_jog_content(self) -> None:
        """Render jog controls (tabs + grids) and settings."""
        with ui.tabs().props("dense") as jog_mode_tabs:
            joint_tab = ui.tab("Joint Jog").mark("tab-joint")
            cart_tab = ui.tab("Cartesian Jog").mark("tab-cartesian")
            settings_tab = ui.tab("Settings").mark("tab-settings")
        jog_mode_tabs.value = joint_tab
        self._jog_mode_tabs = jog_mode_tabs

        # Tab change handler to update step input suffix/tooltip
        def _on_tab_change(e):
            if self._step_input is None:
                return
            tab_value = e.value if hasattr(e, "value") else e.args
            # Get tab name from the tab object or string
            tab_name = getattr(tab_value, "label", str(tab_value)) if tab_value else ""
            if "Joint" in tab_name:
                self._step_input.props('suffix="°"')
                self._step_input._props["suffix"] = "°"
                if self._step_input_tooltip:
                    self._step_input_tooltip.text = "Step size in degrees"
            elif "Cartesian" in tab_name:
                self._step_input.props('suffix="mm"')
                self._step_input._props["suffix"] = "mm"
                if self._step_input_tooltip:
                    self._step_input_tooltip.text = "Step size in mm"
            self._step_input.update()

        jog_mode_tabs.on("update:model-value", _on_tab_change)

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

                            def _bar_backward(a, i=idx, lo=lo, hi=hi) -> float:
                                if hi <= lo or len(a) <= i or not math.isfinite(a[i]):
                                    return 0.0
                                return max(0.0, min(1.0, (a[i] - lo) / (hi - lo)))

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

                            def _num_backward(a, i=idx) -> float | None:
                                if len(a) <= i or not math.isfinite(a[i]):
                                    return None
                                return float(a[i])

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

                            def check_lower_limit(a, i=idx, lo=lo):
                                if len(a) <= i:
                                    return False
                                step = ui_state.joint_step_deg
                                return a[i] - step >= lo

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

                            def check_upper_limit(a, i=idx, hi=hi):
                                if len(a) <= i:
                                    return False
                                step = ui_state.joint_step_deg
                                return a[i] + step <= hi

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
                            min_btn = (
                                ui.button(
                                    icon="first_page",
                                    on_click=lambda e, i=idx: asyncio.create_task(
                                        self.go_to_joint_limit(i, "min")
                                    ),
                                )
                                .props("round dense unelevated")
                                .tooltip("Move to minimum joint limit")
                                .mark(f"btn-j{idx + 1}-min-limit")
                            )
                            max_btn = (
                                ui.button(
                                    icon="last_page",
                                    on_click=lambda e, i=idx: asyncio.create_task(
                                        self.go_to_joint_limit(i, "max")
                                    ),
                                )
                                .props("round dense unelevated")
                                .tooltip("Move to maximum joint limit")
                                .mark(f"btn-j{idx + 1}-max-limit")
                            )
                            self._joint_limit_btns[(idx, "min")] = min_btn
                            self._joint_limit_btns[(idx, "max")] = max_btn

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
                    markup = self._prepare_icon_markup(raw, vb, label, slot_id)
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
                    marker = f"axis-{axis_str.replace('+', 'plus').replace('-', 'minus').lower()}"
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

            # Settings panel
            with ui.tab_panel(settings_tab).classes("gap-0 p-0"):
                with ui.scroll_area().classes("w-full h-full p-0"):
                    settings_content = SettingsContent(self.client)
                    settings_content.build_embedded()

    def build(self, anchor: str = "bl") -> None:
        """Render the bottom-left control panel (overlay-bl).

        Args:
            anchor: Position anchor for the panel (e.g., "bl" for bottom-left)
        """
        # Capture UI client for background task operations
        self._ui_client = ui.context.client

        with ui.card().classes(f"overlay-panel overlay-card overlay-{anchor} gap-1"):
            # Two-column layout: left column with two rows, right column with E-STOP spanning both rows
            with ui.column().classes("gap-2 w-full"):
                with ui.row().classes("items-center w-full"):
                    # Left column: Speed/Step row + Controls row
                    with ui.column().classes("gap-1 flex-grow"):
                        # Speed + Step (single compact row)
                        with ui.row().classes("items-center gap-2 w-full"):
                            speed_icon = ui.icon(
                                "speed", size="md", color="amber-6"
                            ).tooltip("Jog speed")
                            # Map 10 rating steps across the configured max jog speed
                            # unit = percentage per rating step
                            unit = max(1, int(100 / 10))
                            # Load persisted value or use default
                            stored_speed = app.storage.general.get(
                                "jog_speed", ui_state.jog_speed
                            )
                            ui_state.jog_speed = stored_speed
                            v_init = max(
                                1, min(10, round(int(ui_state.jog_speed) / unit))
                            )
                            # Yellow→red gradient for 10 speed levels (continuously darkening)
                            speed_colors_list = [
                                "yellow-4",
                                "yellow-6",
                                "amber-5",
                                "amber-7",
                                "orange-6",
                                "orange-8",
                                "deep-orange-6",
                                "deep-orange-8",
                                "red-7",
                                "red-9",
                            ]

                            def update_speed(e):
                                val = int(e.args) if e.args else 1
                                val = max(1, min(10, val))
                                setattr(ui_state, "jog_speed", int(val * unit))
                                # Persist to storage
                                app.storage.general["jog_speed"] = int(val * unit)
                                # Update icon color
                                color = speed_colors_list[val - 1]
                                speed_icon.props(f"color={color}")

                            v_rating = ui.rating(
                                max=10, icon="circle", value=v_init
                            ).props(f':color="{speed_colors_list}"')
                            v_rating.on("update:model-value", update_speed)

                            # Initialize icon color
                            if v_init > 0:
                                speed_icon.props(
                                    f"color={speed_colors_list[v_init - 1]}"
                                )

                        # Acceleration rating row
                        with ui.row().classes("items-center gap-2 w-full"):
                            accel_icon = ui.icon(
                                "bolt", size="md", color="cyan-6"
                            ).tooltip("Jog acceleration")
                            # Map 10 rating steps across 100% acceleration
                            accel_unit = max(1, int(100 / 10))
                            # Load persisted value or use default
                            stored_accel = app.storage.general.get(
                                "jog_accel", ui_state.jog_accel
                            )
                            ui_state.jog_accel = stored_accel
                            accel_init = max(
                                1, min(10, round(int(ui_state.jog_accel) / accel_unit))
                            )
                            # Electric Green gradient for 10 acceleration levels (neon/energy feel)
                            accel_colors_list = [
                                "lime-3",
                                "lime-4",
                                "light-green-4",
                                "light-green-5",
                                "green-6",
                                "green-7",
                                "teal-7",
                                "teal-8",
                                "cyan-8",
                                "cyan-9",
                            ]

                            def update_accel(e):
                                val = int(e.args) if e.args else 1
                                val = max(1, min(10, val))
                                setattr(ui_state, "jog_accel", int(val * accel_unit))
                                # Persist to storage
                                app.storage.general["jog_accel"] = int(val * accel_unit)
                                # Update icon color
                                color = accel_colors_list[val - 1]
                                accel_icon.props(f"color={color}")

                            accel_rating = ui.rating(
                                max=10, icon="circle", value=accel_init
                            ).props(f':color="{accel_colors_list}"')
                            accel_rating.on("update:model-value", update_accel)

                            # Initialize icon color
                            if accel_init > 0:
                                accel_icon.props(
                                    f"color={accel_colors_list[accel_init - 1]}"
                                )

                    # Right column: Large E-STOP spanning both rows
                    ui.button(
                        icon="dangerous", color="negative", on_click=self.on_estop_click
                    ).props("round unelevated").classes(
                        "ml-auto glass-btn text-2xl"
                    ).tooltip("E-Stop (Esc)").mark("btn-estop")
                # Home, Robot/Simulator toggle, gizmo controls, and camera reset
                with ui.row().classes("gap-2 items-center"):
                    ui.button(icon="home", on_click=self.send_home).props(
                        "dense round unelevated color=teal-6"
                    ).tooltip("Home (H)").mark("btn-home")

                    # Single-button Robot/Simulator toggle (precision_manufacturing)
                    robot_btn = (
                        ui.button(
                            icon="precision_manufacturing",
                            on_click=self.on_toggle_sim,
                        )
                        .props("round unelevated dense")
                        .tooltip("Robot/Simulator")
                    )
                    robot_btn.mark("btn-robot-toggle")

                    def _update_robot_btn_visual():
                        sim = bool(getattr(robot_state, "simulator_active", False))
                        if sim:
                            # Simulator active: amber (see theme.py SceneColors.SIM_AMBER)
                            robot_btn.props("color=amber-8")
                            robot_btn.classes("glass-btn glass-amber")
                        else:
                            # Robot mode: muted appearance
                            robot_btn.props("color=grey-7")
                            robot_btn.classes("glass-btn")

                    self._update_robot_btn_visual = (
                        _update_robot_btn_visual  # store for reuse
                    )
                    _update_robot_btn_visual()

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

                    with ui.button_group().props("rounded unelevated dense"):
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
                        "round unelevated dense color=light-blue-6"
                    ).tooltip("Reset camera")
                    ui.space()
                    with ui.row(align_items="center").classes("gap-1"):
                        ui.label("Step:").classes("text-white")
                        self._step_input = (
                            ui.number(
                                value=ui_state.joint_step_deg,
                                min=1,
                                max=100.0,
                                step=1,
                                format="%.1f",
                                suffix="°",
                            )
                            .props(
                                'dense borderless hide-bottom-space input-style="text-align:right"'
                            )
                            .bind_value(ui_state, "joint_step_deg")
                        )
                        with self._step_input:
                            self._step_input_tooltip = ui.tooltip(
                                "Step size in degrees"
                            )

            # Jog controls (tabs + grids)
            self.render_jog_content()
