"""Top-left readout panel component for robot pose and IO status display."""

from nicegui import ui

from parol_commander.common.theme import StatusColors
from parol_commander.state import robot_state, controller_state


def _fmt_1f(v: float) -> str:
    """Format float with 1 decimal place."""
    return f"{v:.1f}"


class ReadoutPanel:
    """Top-left readout panel displaying cartesian pose, rotational pose, and IO status."""

    _CHIP_PAD = "border-radius:9999px;padding:0 6px;"

    def __init__(self) -> None:
        """Initialize readout panel with UI element references."""
        # Connection/IO status elements
        self._ctrl_label: ui.label | None = None
        self._robot_label: ui.label | None = None
        self._io_chips: dict[str, ui.label] = {}
        self._action_container: ui.element | None = None

        # Dirty checking state for update_conn_io
        self._last_ctrl_running: bool | None = None
        self._last_simulator_active: bool | None = None
        self._last_robot_connected: bool | None = None
        self._last_io_in1: bool | None = None
        self._last_io_in2: bool | None = None
        self._last_io_out1: bool | None = None
        self._last_io_out2: bool | None = None
        # Dirty checking for update_action_visibility
        self._last_action_state: str | None = None

    def _style_chip(self, el: ui.label, on: bool) -> None:
        """Style an IO chip based on on/off state."""
        el.style(
            f"{self._CHIP_PAD}background:{StatusColors.POSITIVE};color:white;"
            if on
            else f"{self._CHIP_PAD}background:rgba(255,255,255,0.12);color:{StatusColors.MUTED};"
        )

    def update_conn_io(self) -> None:
        """Update connection and IO status styling. Called from status consumer."""
        if not self._ctrl_label:
            return

        # CTRL color (only update if changed)
        ctrl_running = controller_state.running
        if ctrl_running != self._last_ctrl_running:
            self._last_ctrl_running = ctrl_running
            self._ctrl_label.style(
                f"color: {StatusColors.POSITIVE}"
                if ctrl_running
                else f"color: {StatusColors.NEGATIVE}"
            )

        # ROBOT color - grey out in simulator mode (only update if changed)
        if self._robot_label:
            sim_active = robot_state.simulator_active
            connected = robot_state.connected
            if (
                sim_active != self._last_simulator_active
                or connected != self._last_robot_connected
            ):
                self._last_simulator_active = sim_active
                self._last_robot_connected = connected
                if sim_active:
                    self._robot_label.style(f"color: {StatusColors.MUTED}")
                else:
                    self._robot_label.style(
                        f"color: {StatusColors.POSITIVE}"
                        if connected
                        else f"color: {StatusColors.NEGATIVE}"
                    )

        # IO chip colors (only update if changed)
        if self._io_chips:
            io_in1 = bool(robot_state.io_in1)
            if io_in1 != self._last_io_in1:
                self._last_io_in1 = io_in1
                self._style_chip(self._io_chips["in1"], io_in1)

            io_in2 = bool(robot_state.io_in2)
            if io_in2 != self._last_io_in2:
                self._last_io_in2 = io_in2
                self._style_chip(self._io_chips["in2"], io_in2)

            io_out1 = bool(robot_state.io_out1)
            if io_out1 != self._last_io_out1:
                self._last_io_out1 = io_out1
                self._style_chip(self._io_chips["out1"], io_out1)

            io_out2 = bool(robot_state.io_out2)
            if io_out2 != self._last_io_out2:
                self._last_io_out2 = io_out2
                self._style_chip(self._io_chips["out2"], io_out2)

    def update_action_visibility(self) -> None:
        """Update action container visibility. Called from status consumer."""
        if not self._action_container:
            return
        state = robot_state.action_state
        # Only update if state changed
        if state == self._last_action_state:
            return
        self._last_action_state = state
        # Show container only when executing
        if state == "EXECUTING":
            self._action_container.classes(remove="hidden")
        else:
            self._action_container.classes(add="hidden")

    def build(self, anchor: str = "tl") -> None:
        """Render the top-left readout panel as an overlay card."""
        with ui.card().classes(f"overlay-panel overlay-card overlay-{anchor}"):
            # Colored Cartesian and Rotational pose readouts with units
            with ui.column().classes("gap-1"):
                # X/Y/Z row - larger text with mm units
                with ui.row().classes("items-center justify-between w-full no-wrap"):
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.label("X:").classes("text-sm tcp-x")
                        (
                            ui.label("-")
                            .bind_text_from(robot_state, "x", backward=_fmt_1f)
                            .classes("text-3xl tcp-x")
                            .style("min-width: 5rem; text-align: right;")
                            .mark("readout-x")
                        )
                        ui.label("mm").classes("text-xs tcp-x")

                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.label("Y:").classes("text-sm tcp-y")
                        (
                            ui.label("-")
                            .bind_text_from(robot_state, "y", backward=_fmt_1f)
                            .classes("text-3xl tcp-y")
                            .style("min-width: 5rem; text-align: right;")
                            .mark("readout-y")
                        )
                        ui.label("mm").classes("text-xs tcp-y")

                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.label("Z:").classes("text-sm tcp-z")
                        (
                            ui.label("-")
                            .bind_text_from(robot_state, "z", backward=_fmt_1f)
                            .classes("text-3xl tcp-z")
                            .style("min-width: 5rem; text-align: right;")
                            .mark("readout-z")
                        )
                        ui.label("mm").classes("text-xs tcp-z")

                # Rx/Ry/Rz row - half size with deg units
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-1"):
                        ui.label("Rx:").classes("text-xs tcp-rx")
                        (
                            ui.label("-")
                            .bind_text_from(robot_state, "rx", backward=_fmt_1f)
                            .classes("text-base tcp-rx")
                            .style("min-width: 3.5rem; text-align: right;")
                            .mark("readout-rx")
                        )
                        ui.label("°").classes("text-xs tcp-rx")

                    with ui.row().classes("items-center gap-1"):
                        ui.label("Ry:").classes("text-xs tcp-ry")
                        (
                            ui.label("-")
                            .bind_text_from(robot_state, "ry", backward=_fmt_1f)
                            .classes("text-base tcp-ry")
                            .style("min-width: 3.5rem; text-align: right;")
                            .mark("readout-ry")
                        )
                        ui.label("°").classes("text-xs tcp-ry")

                    with ui.row().classes("items-center gap-1"):
                        ui.label("Rz:").classes("text-xs tcp-rz")
                        (
                            ui.label("-")
                            .bind_text_from(robot_state, "rz", backward=_fmt_1f)
                            .classes("text-base tcp-rz")
                            .style("min-width: 3.5rem; text-align: right;")
                            .mark("readout-rz")
                        )
                        ui.label("°").classes("text-xs tcp-rz")

                # Connectivity + IO row (CTRL / ROBOT + IO chips)
                with ui.row().classes("items-center gap-3 w-full"):
                    self._ctrl_label = ui.label("CTRL")
                    ui.label("|").classes("text-xs text-[var(--ctk-muted)]")
                    self._robot_label = ui.label("ROBOT")
                    ui.label("|").classes("text-xs text-[var(--ctk-muted)]")
                    # IO chips
                    self._io_chips["in1"] = (
                        ui.label("IN1").classes("text-xs").style(self._CHIP_PAD)
                    )
                    self._io_chips["in2"] = (
                        ui.label("IN2").classes("text-xs").style(self._CHIP_PAD)
                    )
                    self._io_chips["out1"] = (
                        ui.label("OUT1").classes("text-xs").style(self._CHIP_PAD)
                    )
                    self._io_chips["out2"] = (
                        ui.label("OUT2").classes("text-xs").style(self._CHIP_PAD)
                    )

                # Action tracking - simple container that shows only when executing
                self._action_container = ui.row().classes(
                    "items-center gap-2 w-full hidden"
                )
                with self._action_container:
                    ui.spinner(size="sm")
                    (
                        ui.label()
                        .bind_text_from(
                            robot_state,
                            "action_current",
                            backward=lambda a: a or "",
                        )
                        .classes("text-sm font-bold flex-1")
                    )

                # Initial state update (subsequent updates driven by status consumer)
                self.update_conn_io()
                self.update_action_visibility()
