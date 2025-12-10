"""Top-left readout panel component for robot pose and IO status display."""

from nicegui import ui

from parol_commander.state import robot_state, controller_state


class ReadoutPanel:
    """Top-left readout panel displaying cartesian pose, rotational pose, and IO status."""

    def __init__(self) -> None:
        """Initialize readout panel with UI element references."""
        # UI element references (for future extensions if needed)
        self.cartesian_labels: dict[str, ui.label] = {}  # X, Y, Z
        self.rotation_labels: dict[str, ui.label] = {}  # Rx, Ry, Rz
        # Vertical task list container
        self.task_list_container: ui.element | None = None

    def build(self, anchor: str = "tl") -> None:
        """Render the top-left readout panel as an overlay card."""
        with ui.card().classes(f"overlay-panel overlay-card overlay-{anchor}"):
            # Colored Cartesian and Rotational pose readouts with units
            with ui.column().classes("gap-1"):
                # X/Y/Z row - larger text with mm units
                with ui.row().classes("items-center justify-between w-full no-wrap"):
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.label("X:").classes("text-sm tcp-x")
                        x_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "x", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-3xl tcp-x")
                            .style("min-width: 5rem; text-align: right;")
                            .mark("readout-x")
                        )
                        ui.label("mm").classes("text-xs tcp-x")
                        self.cartesian_labels["X"] = x_label

                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.label("Y:").classes("text-sm tcp-y")
                        y_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "y", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-3xl tcp-y")
                            .style("min-width: 5rem; text-align: right;")
                            .mark("readout-y")
                        )
                        ui.label("mm").classes("text-xs tcp-y")
                        self.cartesian_labels["Y"] = y_label

                    with ui.row().classes("items-center gap-1 no-wrap"):
                        ui.label("Z:").classes("text-sm tcp-z")
                        z_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "z", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-3xl tcp-z")
                            .style("min-width: 5rem; text-align: right;")
                            .mark("readout-z")
                        )
                        ui.label("mm").classes("text-xs tcp-z")
                        self.cartesian_labels["Z"] = z_label

                # Rx/Ry/Rz row - half size with deg units
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-1"):
                        ui.label("Rx:").classes("text-xs tcp-rx")
                        rx_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "rx", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-base tcp-rx")
                            .style("min-width: 3.5rem; text-align: right;")
                            .mark("readout-rx")
                        )
                        ui.label("°").classes("text-xs tcp-rx")
                        self.rotation_labels["Rx"] = rx_label

                    with ui.row().classes("items-center gap-1"):
                        ui.label("Ry:").classes("text-xs tcp-ry")
                        ry_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "ry", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-base tcp-ry")
                            .style("min-width: 3.5rem; text-align: right;")
                            .mark("readout-ry")
                        )
                        ui.label("°").classes("text-xs tcp-ry")
                        self.rotation_labels["Ry"] = ry_label

                    with ui.row().classes("items-center gap-1"):
                        ui.label("Rz:").classes("text-xs tcp-rz")
                        rz_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "rz", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-base tcp-rz")
                            .style("min-width: 3.5rem; text-align: right;")
                            .mark("readout-rz")
                        )
                        ui.label("°").classes("text-xs tcp-rz")
                        self.rotation_labels["Rz"] = rz_label

                # Connectivity + IO row (CTRL / ROBOT + IO chips)
                with ui.row().classes("items-center gap-3 w-full"):
                    ctrl = ui.label("CTRL")
                    ui.label("|").classes("text-xs text-[var(--ctk-muted)]")
                    robot = ui.label("ROBOT")
                    ui.label("|").classes("text-xs text-[var(--ctk-muted)]")
                    # IO chips
                    chip_pad = "border-radius:9999px;padding:0 6px;"
                    io_in1 = ui.label("IN1").classes("text-xs").style(chip_pad)
                    io_in2 = ui.label("IN2").classes("text-xs").style(chip_pad)
                    io_out1 = ui.label("OUT1").classes("text-xs").style(chip_pad)
                    io_out2 = ui.label("OUT2").classes("text-xs").style(chip_pad)

                    def _style_chip(el: ui.label, on: bool) -> None:
                        el.style(
                            f"{chip_pad}background:#21BA45;color:white;"
                            if on
                            else f"{chip_pad}background:rgba(255,255,255,0.12);color:#bbb;"
                        )

                    def _update_conn_io():
                        # CTRL color
                        ctrl.style(
                            "color: #21BA45"
                            if controller_state.running
                            else "color: #DB2828"
                        )
                        # ROBOT color - grey out in simulator mode
                        if robot_state.simulator_active:
                            robot.style("color: #888888")
                        else:
                            robot.style(
                                "color: #21BA45"
                                if robot_state.connected
                                else "color: #DB2828"
                            )
                        # IO chip colors
                        _style_chip(io_in1, bool(robot_state.io_in1))
                        _style_chip(io_in2, bool(robot_state.io_in2))
                        _style_chip(io_out1, bool(robot_state.io_out1))
                        _style_chip(io_out2, bool(robot_state.io_out2))

                    _update_conn_io()
                    ui.timer(0.5, _update_conn_io)

                # Action tracking - simple container that shows only when executing
                action_container = ui.row().classes("items-center gap-2 w-full hidden")
                with action_container:
                    ui.spinner(size="sm")
                    (
                        ui.label()
                        .bind_text_from(
                            robot_state,
                            "action_current",
                            backward=lambda a: a if a else "",
                        )
                        .classes("text-sm font-bold flex-1")
                    )

                def _update_action_visibility():
                    state = robot_state.action_state.upper()
                    # Show container only when executing
                    if state == "EXECUTING":
                        action_container.classes(remove="hidden")
                    else:
                        action_container.classes(add="hidden")

                ui.timer(0.2, _update_action_visibility)

                def _tick():
                    _update_conn_io()

                _tick()
                ui.timer(0.5, _tick)
