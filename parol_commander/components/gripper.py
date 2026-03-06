import logging
from collections.abc import Callable

from nicegui import ui

from waldoctl import (
    ActivationType,
    ElectricGripperTool,
    GripperTool,
    LinearMotion,
    RobotClient,
)

from parol_commander.services.camera_service import camera_service
from parol_commander.services.motion_recorder import motion_recorder
from parol_commander.state import robot_state, ui_state

logger = logging.getLogger(__name__)

# Chart colors
_CLR_POS = "#2dd4bf"  # teal-400
_CLR_FORCE = "#f87171"  # red-400
_CLR_CUR = "#fbbf24"  # amber-400

# Tool state dot colors (by ToolStatus.state int)
_STATE_DOTS: dict[int, tuple[str, str]] = {
    0: ("var(--ctk-muted)", "Off"),
    1: ("var(--color-sky-400)", "Idle"),
    2: ("var(--color-emerald-400)", "Active"),
    3: ("var(--color-red-400)", "Error"),
}


class GripperPage:
    """Gripper tab page — camera, time series, status, and controls."""

    def __init__(self, client: RobotClient) -> None:
        self.client = client
        self._timers: list[ui.timer] = []
        self._last_current_tool_key: str | None = None
        self._current_range_listener: Callable | None = None

    # ---- Helpers ----

    def _get_active_gripper(self) -> GripperTool | None:
        try:
            tool = self.client.tool
        except (RuntimeError, KeyError, NotImplementedError):
            return None
        return tool if isinstance(tool, GripperTool) else None

    def _is_electric(self) -> bool:
        tool = self._get_active_gripper()
        return isinstance(tool, ElectricGripperTool)

    # ---- Actions ----

    async def _grip_set(self, position: float, label: str) -> None:
        try:
            tool = self._get_active_gripper()
            if tool is None:
                ui.notify("No gripper available", color="warning")
                return
            spd_kwargs: dict = {}
            if isinstance(tool, ElectricGripperTool):
                if ui_state.gripper_speed_sync:
                    spd_kwargs["speed"] = ui_state.jog_speed / 100.0
                else:
                    spd_kwargs["speed"] = ui_state.gripper_speed / 100.0
                if self._cur_slider:
                    spd_kwargs["current"] = int(self._cur_slider.value)
            await tool.set_position(position, **spd_kwargs)
            motion_recorder.record_action("gripper", position=position, **spd_kwargs)
        except Exception as e:
            logger.error("Gripper %s failed: %s", label.lower(), e)
            ui.notify(f"{label} failed: {e}", color="negative")

    async def _grip_open(self) -> None:
        await self._grip_set(0.0, "Open")

    async def _grip_close(self) -> None:
        await self._grip_set(1.0, "Close")

    async def _grip_cal(self) -> None:
        try:
            tool = self._get_active_gripper()
            if not isinstance(tool, ElectricGripperTool):
                ui.notify("Only electric grippers support calibration", color="warning")
                return
            await tool.calibrate()
            motion_recorder.record_action("gripper", calibrate=True)
        except Exception as e:
            logger.error("Gripper calibrate failed: %s", e)
            ui.notify(f"Calibrate failed: {e}", color="negative")

    async def _grip_move(self) -> None:
        pos = self._pos_slider.value / 100.0 if self._pos_slider else 0.0
        await self._grip_set(pos, "Move")

    def _get_tool_info_text(self) -> str:
        tool = self._get_active_gripper()
        if tool is None:
            return ""
        parts: list[str] = []
        motions = tool.motions
        linear: LinearMotion | None = None
        for m in motions:
            if isinstance(m, LinearMotion):
                linear = m
                break
        if linear is not None:
            stroke_mm = linear.travel_m * 1000 * (2 if linear.symmetric else 1)
            parts.append(f"Stroke: {stroke_mm:.1f} mm")
            if tool.activation_type == ActivationType.BINARY:
                if linear.estimated_speed_m_s:
                    travel_time_ms = (
                        linear.travel_m / linear.estimated_speed_m_s
                    ) * 1000
                    parts.append(f"Travel: ~{travel_time_ms:.0f} ms")
        return " | ".join(parts)

    # ---- Build ----

    def build(self) -> None:
        self._pos_slider: ui.slider | None = None
        self._cur_slider: ui.slider | None = None
        self._chart: ui.echart | None = None

        with ui.column().classes("w-full gap-2"):
            # Header row
            self._build_header()
            # Camera
            self._build_camera_section()
            # Chart
            self._build_chart()
            # Status + Controls
            self._build_status_and_controls()

    # ---- Header ----

    def _build_header(self) -> None:
        with ui.row().classes("items-center gap-4 w-full"):
            (
                ui.label("Tool: -")
                .bind_text_from(
                    robot_state,
                    "tool_key",
                    backward=lambda v: f"Tool: {v}",
                )
                .classes("text-sm font-medium")
            )
            (
                ui.label("")
                .bind_text_from(
                    robot_state,
                    "tool_key",
                    backward=lambda _: self._get_tool_info_text(),
                )
                .bind_visibility_from(
                    robot_state,
                    "tool_key",
                    backward=lambda k: k != "NONE" and bool(self._get_tool_info_text()),
                )
                .classes("text-xs text-[var(--ctk-muted)]")
            )

    # ---- Camera ----

    def _build_camera_section(self) -> None:
        with ui.column().classes("w-full").mark("gripper-camera-section"):
            if camera_service.active:
                (
                    ui.interactive_image(
                        "/tool/camera/stream",
                        cross=True,
                    )
                    .classes("w-full")
                    .style("max-height: 240px; object-fit: contain;")
                )
            else:
                with (
                    ui.column()
                    .classes("w-full items-center justify-center p-4 rounded-lg")
                    .style(
                        "min-height: 80px; background: color-mix(in srgb, var(--ctk-muted) 10%, transparent);"
                    )
                ):
                    ui.label("No camera selected").classes(
                        "text-sm text-[var(--ctk-muted)]"
                    )
                    ui.label(
                        "Select a camera device in Settings to enable live feed."
                    ).classes("text-xs text-[var(--ctk-muted)]")
                    ui.label(
                        "AI annotations: webcam \u2192 your script \u2192 pyvirtualcam \u2192 select virtual device"
                    ).classes("text-xs text-[var(--ctk-muted)]")
                    ui.label("Linux: sudo apt install v4l2loopback-dkms").classes(
                        "text-xs text-[var(--ctk-muted)] font-mono"
                    )

    # ---- Chart ----

    def _build_chart(self) -> None:
        self._chart = (
            ui.echart(
                {
                    "animation": False,
                    "renderer": "canvas",
                    "grid": {
                        "top": 28,
                        "right": 52,
                        "bottom": 24,
                        "left": 48,
                        "containLabel": False,
                    },
                    "legend": {
                        "data": ["Position %", "Force N", "Current mA"],
                        "top": 0,
                        "textStyle": {"fontSize": 10, "color": "var(--ctk-text)"},
                        "itemWidth": 14,
                        "itemHeight": 8,
                    },
                    "xAxis": {
                        "type": "time",
                        "axisLabel": {"show": False},
                        "splitLine": {"show": False},
                        "axisLine": {"lineStyle": {"color": "var(--ctk-muted)"}},
                    },
                    "yAxis": [
                        {
                            "type": "value",
                            "name": "%/N",
                            "nameTextStyle": {
                                "fontSize": 9,
                                "color": "var(--ctk-muted)",
                            },
                            "axisLabel": {"fontSize": 9, "color": "var(--ctk-muted)"},
                            "splitLine": {
                                "lineStyle": {"color": "rgba(128,128,128,0.15)"}
                            },
                            "min": 0,
                        },
                        {
                            "type": "value",
                            "name": "mA",
                            "nameTextStyle": {
                                "fontSize": 9,
                                "color": "var(--ctk-muted)",
                            },
                            "axisLabel": {"fontSize": 9, "color": "var(--ctk-muted)"},
                            "splitLine": {"show": False},
                            "min": 0,
                        },
                    ],
                    "series": [
                        {
                            "name": "Position %",
                            "type": "line",
                            "yAxisIndex": 0,
                            "showSymbol": False,
                            "lineStyle": {"width": 1.5, "color": _CLR_POS},
                            "itemStyle": {"color": _CLR_POS},
                            "data": [],
                        },
                        {
                            "name": "Force N",
                            "type": "line",
                            "yAxisIndex": 0,
                            "showSymbol": False,
                            "lineStyle": {"width": 1.5, "color": _CLR_FORCE},
                            "itemStyle": {"color": _CLR_FORCE},
                            "data": [],
                        },
                        {
                            "name": "Current mA",
                            "type": "line",
                            "yAxisIndex": 1,
                            "showSymbol": False,
                            "lineStyle": {"width": 1.5, "color": _CLR_CUR},
                            "itemStyle": {"color": _CLR_CUR},
                            "data": [],
                        },
                    ],
                }
            )
            .classes("w-full")
            .style("height: 160px;")
            .mark("gripper-chart")
        )

        t = ui.timer(0.1, self._update_chart)
        self._timers.append(t)

    def _update_chart(self) -> None:
        if self._chart is None:
            return
        timestamps, positions, forces, currents = (
            robot_state.tool_time_series.get_series()
        )
        if not timestamps:
            return
        # Convert timestamps to JS-epoch (ms) for ECharts time axis
        pos_data = [
            [t * 1000, round(p * 100, 1)] for t, p in zip(timestamps, positions)
        ]
        frc_data = [[t * 1000, round(f, 2)] for t, f in zip(timestamps, forces)]
        cur_data = [[t * 1000, round(c, 1)] for t, c in zip(timestamps, currents)]
        self._chart.run_chart_method(
            "setOption",
            {"series": [{"data": pos_data}, {"data": frc_data}, {"data": cur_data}]},
        )

    # ---- Status + Controls ----

    def _build_status_and_controls(self) -> None:
        with ui.row().classes("w-full gap-4 items-start"):
            self._build_status_column()
            self._build_controls_column()

    def _build_status_column(self) -> None:
        with ui.column().classes("gap-1").style("min-width: 90px;"):
            # State dot + label
            with ui.row().classes("items-center gap-1"):
                dot = ui.icon("circle").classes("text-xs").style("font-size: 10px;")

                def _poll_state():
                    s = robot_state.tool_status.state
                    color, label = _STATE_DOTS.get(s, _STATE_DOTS[0])
                    dot.style(f"color: {color}; font-size: 10px;")
                    state_lbl.text = label

                state_lbl = ui.label("Off").classes("text-xs")
                t = ui.timer(0.5, _poll_state)
                self._timers.append(t)

            # Position
            (
                ui.label("0%")
                .bind_text_from(
                    robot_state,
                    "tool_position",
                    backward=lambda v: f"{v * 100:.0f}%",
                )
                .classes("text-sm font-medium")
            )

            # Force (estimated)
            (
                ui.label("~0.0 N")
                .bind_text_from(
                    robot_state,
                    "tool_force",
                    backward=lambda v: f"~{v:.1f} N",
                )
                .classes("text-xs text-[var(--ctk-muted)]")
                .tooltip("Estimated force (derived from current)")
            )

            # Current (primary)
            (
                ui.label("0 mA")
                .bind_text_from(
                    robot_state,
                    "tool_current",
                    backward=lambda v: f"{v:.0f} mA",
                )
                .classes("text-sm")
            )

            # Part detected
            with ui.row().classes("items-center gap-1"):
                part_dot = (
                    ui.icon("circle")
                    .classes("text-xs")
                    .style("font-size: 8px; color: var(--ctk-muted);")
                )

                # Update dot color via timer since icon has no bind_style_from
                def _update_part_dot():
                    color = (
                        "var(--color-emerald-400)"
                        if robot_state.tool_part_detected
                        else "var(--ctk-muted)"
                    )
                    part_dot.style(f"font-size: 8px; color: {color};")

                t = ui.timer(0.5, _update_part_dot)
                self._timers.append(t)
                ui.label("Part").classes("text-xs text-[var(--ctk-muted)]")

    def _build_controls_column(self) -> None:
        with ui.column().classes("flex-1 gap-2"):
            self._build_sliders()
            self._build_action_buttons()
            self._build_speed_section()

    def _build_sliders(self) -> None:
        # Position slider + input
        with ui.row().classes("items-center gap-2 w-full"):
            ui.label("Pos").classes("text-xs text-[var(--ctk-muted)] w-8")
            self._pos_slider = ui.slider(min=0, max=100, value=4, step=1).classes(
                "flex-1"
            )
            pos_input = (
                ui.number(min=0, max=100, step=1, value=4)
                .props("dense borderless")
                .classes("w-14")
            )
            pos_input.bind_value(self._pos_slider, "value")

        # Current slider + input (electric only)
        with (
            ui.row()
            .classes("items-center gap-2 w-full")
            .bind_visibility_from(
                robot_state,
                "tool_key",
                backward=lambda k: k != "NONE" and self._is_electric(),
            )
        ):
            ui.label("mA").classes("text-xs text-[var(--ctk-muted)] w-8")
            self._cur_slider = ui.slider(min=0, max=1000, value=500, step=10).classes(
                "flex-1"
            )
            cur_input = (
                ui.number(min=0, max=1000, step=10, value=500)
                .props("dense borderless")
                .classes("w-14")
            )
            cur_input.bind_value(self._cur_slider, "value")

            # Update current slider range when tool changes
            def _update_current_range() -> None:
                if self._cur_slider is None:
                    return
                if robot_state.tool_key == self._last_current_tool_key:
                    return
                self._last_current_tool_key = robot_state.tool_key
                tool = self._get_active_gripper()
                if isinstance(tool, ElectricGripperTool):
                    lo, hi = tool.current_range
                    self._cur_slider._props["min"] = lo
                    self._cur_slider._props["max"] = hi
                    cur_input._props["min"] = lo
                    cur_input._props["max"] = hi
                    self._cur_slider.value = min(lo + 80, hi)
                    self._cur_slider.update()

            self._current_range_listener = _update_current_range
            robot_state.add_change_listener(_update_current_range)

    def _build_action_buttons(self) -> None:
        with ui.row().classes("items-center gap-1"):
            ui.button(icon="open_in_full", on_click=self._grip_open).props(
                "flat round dense"
            ).tooltip("Open").mark("btn-grip-open")

            ui.button(icon="close_fullscreen", on_click=self._grip_close).props(
                "flat round dense"
            ).tooltip("Close").mark("btn-grip-close")

            # Calibrate (electric only)
            (
                ui.button(icon="build", on_click=self._grip_cal)
                .props("flat round dense")
                .tooltip("Calibrate")
                .bind_visibility_from(
                    robot_state,
                    "tool_key",
                    backward=lambda k: k != "NONE" and self._is_electric(),
                )
                .mark("btn-grip-cal")
            )

            # Move
            ui.button(icon="play_arrow", on_click=self._grip_move).props(
                "flat round dense"
            ).tooltip("Move to position").mark("btn-grip-move")

    def _build_speed_section(self) -> None:
        with ui.row().classes("items-center gap-2 w-full"):
            ui.label("Speed").classes("text-xs text-[var(--ctk-muted)] w-12")
            with ui.row().classes("items-center gap-1"):
                spd_sync = (
                    ui.checkbox("Sync", value=ui_state.gripper_speed_sync)
                    .props("dense")
                    .classes("text-xs")
                )
                spd_sync.bind_value(ui_state, "gripper_speed_sync")

            # Synced: show read-only system speed
            (
                ui.label("")
                .bind_text_from(
                    ui_state,
                    "jog_speed",
                    backward=lambda v: f"{v}%",
                )
                .bind_visibility_from(ui_state, "gripper_speed_sync")
                .classes("text-xs text-[var(--ctk-muted)]")
            )

            # Independent: slider
            (
                ui.slider(min=1, max=100, value=ui_state.gripper_speed, step=1)
                .bind_value(ui_state, "gripper_speed")
                .bind_visibility_from(
                    ui_state,
                    "gripper_speed_sync",
                    backward=lambda v: not v,
                )
                .classes("flex-1")
            )

    # ---- Cleanup ----

    def cleanup(self) -> None:
        """Cancel timers and remove listeners when panel is destroyed."""
        for t in self._timers:
            t.cancel()
        self._timers.clear()
        if self._current_range_listener is not None:
            robot_state.remove_change_listener(self._current_range_listener)
