"""Top-left readout panel component for robot pose and IO status display."""

from nicegui import ui

from parol_commander.common.theme import StatusColors
from parol_commander.state import robot_state, controller_state


class ReadoutPanel:
    """Top-left readout panel displaying cartesian pose, rotational pose, and IO status."""

    def __init__(self) -> None:
        """Initialize readout panel with UI element references."""
        # UI element references (for future extensions if needed)
        self.cartesian_labels: dict[str, ui.label] = {}  # X, Y, Z
        self.rotation_labels: dict[str, ui.label] = {}  # Rx, Ry, Rz
        # All colored elements by axis for frame-based color updates
        self.axis_elements: dict[str, list[ui.element]] = {}
        # Vertical task list container
        self.task_list_container: ui.element | None = None

    def update_frame_colors(self, frame: str) -> None:
        """Update readout colors to match the current reference frame mapping.

        In WRF mode: standard colors (X=red, Y=green, Z=blue)
        In TRF mode: colors match button layout (Y gets Z color, Z gets Y color)
        """
        if frame == "TRF":
            # TRF: Z becomes primary vertical (green), Y becomes secondary (blue)
            color_map = {
                "X": "tcp-x",
                "Y": "tcp-z",
                "Z": "tcp-y",
                "Rx": "tcp-rx",
                "Ry": "tcp-rz",
                "Rz": "tcp-ry",
            }
        else:  # WRF
            color_map = {
                "X": "tcp-x",
                "Y": "tcp-y",
                "Z": "tcp-z",
                "Rx": "tcp-rx",
                "Ry": "tcp-ry",
                "Rz": "tcp-rz",
            }

        for axis, elements in self.axis_elements.items():
            new_class = color_map.get(axis, "tcp-x")
            for el in elements:
                el.classes(remove="tcp-x tcp-y tcp-z tcp-rx tcp-ry tcp-rz")
                el.classes(add=new_class)

    def build(self, anchor: str = "tl") -> None:
        """Render the top-left readout panel as an overlay card."""
        with ui.card().classes(f"overlay-panel overlay-card overlay-{anchor}"):
            # Colored Cartesian and Rotational pose readouts with units
            with ui.column().classes("gap-1"):
                # X/Y/Z row - larger text with mm units
                with ui.row().classes("items-center justify-between w-full no-wrap"):
                    with ui.row().classes("items-center gap-1 no-wrap"):
                        x_prefix = ui.label("X:").classes("text-sm tcp-x")
                        x_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "x", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-3xl tcp-x")
                            .style("min-width: 5rem; text-align: right;")
                            .mark("readout-x")
                        )
                        x_unit = ui.label("mm").classes("text-xs tcp-x")
                        self.cartesian_labels["X"] = x_label
                        self.axis_elements["X"] = [x_prefix, x_label, x_unit]

                    with ui.row().classes("items-center gap-1 no-wrap"):
                        y_prefix = ui.label("Y:").classes("text-sm tcp-y")
                        y_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "y", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-3xl tcp-y")
                            .style("min-width: 5rem; text-align: right;")
                            .mark("readout-y")
                        )
                        y_unit = ui.label("mm").classes("text-xs tcp-y")
                        self.cartesian_labels["Y"] = y_label
                        self.axis_elements["Y"] = [y_prefix, y_label, y_unit]

                    with ui.row().classes("items-center gap-1 no-wrap"):
                        z_prefix = ui.label("Z:").classes("text-sm tcp-z")
                        z_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "z", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-3xl tcp-z")
                            .style("min-width: 5rem; text-align: right;")
                            .mark("readout-z")
                        )
                        z_unit = ui.label("mm").classes("text-xs tcp-z")
                        self.cartesian_labels["Z"] = z_label
                        self.axis_elements["Z"] = [z_prefix, z_label, z_unit]

                # Rx/Ry/Rz row - half size with deg units
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-1"):
                        rx_prefix = ui.label("Rx:").classes("text-xs tcp-rx")
                        rx_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "rx", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-base tcp-rx")
                            .style("min-width: 3.5rem; text-align: right;")
                            .mark("readout-rx")
                        )
                        rx_unit = ui.label("°").classes("text-xs tcp-rx")
                        self.rotation_labels["Rx"] = rx_label
                        self.axis_elements["Rx"] = [rx_prefix, rx_label, rx_unit]

                    with ui.row().classes("items-center gap-1"):
                        ry_prefix = ui.label("Ry:").classes("text-xs tcp-ry")
                        ry_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "ry", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-base tcp-ry")
                            .style("min-width: 3.5rem; text-align: right;")
                            .mark("readout-ry")
                        )
                        ry_unit = ui.label("°").classes("text-xs tcp-ry")
                        self.rotation_labels["Ry"] = ry_label
                        self.axis_elements["Ry"] = [ry_prefix, ry_label, ry_unit]

                    with ui.row().classes("items-center gap-1"):
                        rz_prefix = ui.label("Rz:").classes("text-xs tcp-rz")
                        rz_label = (
                            ui.label("-")
                            .bind_text_from(
                                robot_state, "rz", backward=lambda v: f"{v:.1f}"
                            )
                            .classes("text-base tcp-rz")
                            .style("min-width: 3.5rem; text-align: right;")
                            .mark("readout-rz")
                        )
                        rz_unit = ui.label("°").classes("text-xs tcp-rz")
                        self.rotation_labels["Rz"] = rz_label
                        self.axis_elements["Rz"] = [rz_prefix, rz_label, rz_unit]

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
                            f"{chip_pad}background:{StatusColors.POSITIVE};color:white;"
                            if on
                            else f"{chip_pad}background:rgba(255,255,255,0.12);color:{StatusColors.MUTED};"
                        )

                    def _update_conn_io():
                        # CTRL color
                        ctrl.style(
                            f"color: {StatusColors.POSITIVE}"
                            if controller_state.running
                            else f"color: {StatusColors.NEGATIVE}"
                        )
                        # ROBOT color - grey out in simulator mode
                        if robot_state.simulator_active:
                            robot.style(f"color: {StatusColors.MUTED}")
                        else:
                            robot.style(
                                f"color: {StatusColors.POSITIVE}"
                                if robot_state.connected
                                else f"color: {StatusColors.NEGATIVE}"
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
