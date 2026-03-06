"""Top-left readout panel component for robot pose and IO status display."""

import html as html_mod
from enum import Enum
from pathlib import Path

from nicegui import ui

from parol_commander.common.theme import IO_COLOR_OFF, IO_COLOR_ON
from parol_commander.state import ActionStatus, action_log, robot_state


class RobotFace(Enum):
    """Robot face states for the connection status indicator."""

    HAPPY = "happy"
    NEUTRAL = "neutral"
    SAD = "sad"


# Load robot face SVGs at module level for inline rendering (CSS hover needs DOM access)
_ICONS_DIR = Path(__file__).parent.parent / "static" / "icons"
_FACE_SVGS = {
    RobotFace.HAPPY: (_ICONS_DIR / "robot_happy.svg").read_text(),
    RobotFace.NEUTRAL: (_ICONS_DIR / "robot_neutral.svg").read_text(),
    RobotFace.SAD: (_ICONS_DIR / "robot_sad.svg").read_text(),
}
_FACE_TOOLTIPS = {
    RobotFace.HAPPY: "Connected",
    RobotFace.NEUTRAL: "Simulator",
    RobotFace.SAD: "Disconnected",
}


def _fmt_1f(v: float) -> str:
    """Format float with 1 decimal place."""
    return f"{v:.1f}"


# ---------------------------------------------------------------------------
# Action log HTML rendering
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    ActionStatus.EXECUTING: (
        '<span style="color:var(--color-sky-500);font-size:11px" '
        'class="material-icons q-spinner-mat">sync</span>'
    ),
    ActionStatus.COMPLETED: (
        '<span style="color:var(--color-emerald-500);font-size:13px">\u2713</span>'
    ),
    ActionStatus.FAILED: (
        '<span style="color:var(--color-red-500);font-size:13px">\u2717</span>'
    ),
}


def _build_log_entries_html() -> str:
    """Build HTML for all action log entries."""
    parts: list[str] = []
    for entry in action_log.entries:
        icon = _STATUS_ICONS.get(entry.status, "")
        count = (
            f" <span style='color:var(--ctk-muted)'>\u00d7{entry.count}</span>"
            if entry.count > 1
            else ""
        )
        params = ""
        if entry.params:
            params = (
                f' <span style="color:var(--ctk-muted)">'
                f"{html_mod.escape(entry.params)}</span>"
            )
        name = html_mod.escape(entry.command_name)
        parts.append(
            f'<div class="action-log-entry" style="font-size:12px;line-height:1.5">'
            f"{icon} <b>{name}</b>{count}{params}</div>"
        )
    return "\n".join(parts)


class ReadoutPanel:
    """Top-left readout panel displaying cartesian pose, rotational pose, and IO status."""

    def __init__(self) -> None:
        """Initialize readout panel with UI element references."""
        # Robot face + IO elements
        self._robot_face_html: ui.html | None = None
        self._robot_face_container: ui.element | None = None
        self._robot_face_tooltip: ui.tooltip | None = None
        self._io_chips: list[ui.chip] = []

        # Action log elements
        self._action_scroll_area: ui.scroll_area | None = None
        self._action_log_html: ui.html | None = None
        self._action_log_expanded: bool = False

        # Dirty checking state
        self._last_face_state: RobotFace | None = None
        self._last_io_inputs: list[int] | None = None
        self._last_io_outputs: list[int] | None = None
        self._last_log_version: int = -1

    def update_conn_io(self) -> None:
        """Update connection face and IO status. Called from status consumer."""
        # Robot face — determine state from connection
        if self._robot_face_html and self._robot_face_container:
            sim_active = robot_state.simulator_active
            connected = robot_state.connected
            if sim_active:
                face = RobotFace.NEUTRAL
            elif connected:
                face = RobotFace.HAPPY
            else:
                face = RobotFace.SAD
            if face != self._last_face_state:
                self._last_face_state = face
                self._robot_face_html.set_content(_FACE_SVGS[face])
                # Swap CSS class for hover animations
                remove = " ".join(
                    f"robot-face-{s.value}" for s in RobotFace if s != face
                )
                self._robot_face_container.classes(
                    add=f"robot-face-{face.value}", remove=remove
                )
                if self._robot_face_tooltip:
                    self._robot_face_tooltip.text = _FACE_TOOLTIPS[face]
                    self._robot_face_tooltip.update()

        # IO chips — update colors when values change
        if self._io_chips:
            inputs = robot_state.io_inputs
            outputs = robot_state.io_outputs
            if inputs != self._last_io_inputs or outputs != self._last_io_outputs:
                self._last_io_inputs = list(inputs)
                self._last_io_outputs = list(outputs)
                all_vals = self._last_io_inputs + self._last_io_outputs
                for i, chip in enumerate(self._io_chips):
                    if i < len(all_vals):
                        color = IO_COLOR_ON if all_vals[i] else IO_COLOR_OFF
                        chip.props(f"color={color}")

    def update_action_log(self) -> None:
        """Update the action log scroll area. Called from status consumer."""
        if not self._action_scroll_area:
            return

        version = action_log.version
        if version == self._last_log_version:
            return
        self._last_log_version = version

        # Rebuild log entries and auto-scroll to bottom
        if self._action_log_html:
            self._action_log_html.set_content(_build_log_entries_html())
            self._action_scroll_area.scroll_to(percent=1.0)

    def _toggle_action_log(self) -> None:
        """Toggle action log between collapsed and expanded."""
        self._action_log_expanded = not self._action_log_expanded
        if self._action_scroll_area:
            if self._action_log_expanded:
                self._action_scroll_area.classes(add="action-log-expanded")
            else:
                self._action_scroll_area.classes(remove="action-log-expanded")

    def build(self, anchor: str = "tl") -> None:
        """Render the top-left readout panel as an overlay card."""
        with ui.card().classes(f"overlay-panel overlay-card overlay-{anchor}"):
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

                    with ui.row().classes("items-center gap-1"):
                        ui.label("v:").classes("text-xs")
                        (
                            ui.label("-")
                            .bind_text_from(
                                robot_state,
                                "tcp_speed",
                                backward=lambda v: f"{v:.0f}",
                            )
                            .classes("text-base")
                            .style("min-width: 2.5rem; text-align: right;")
                            .mark("readout-tcp-speed")
                        )
                        ui.label("mm/s").classes("text-xs")

                # Connectivity + IO row
                with ui.row().classes("items-center w-full no-wrap"):
                    self._robot_face_container = (
                        ui.element("div")
                        .classes(f"robot-face robot-face-{RobotFace.SAD.value}")
                        .style("width: 32px; height: 32px")
                        .mark("readout-robot-face")
                    )
                    with self._robot_face_container:
                        self._robot_face_html = ui.html(
                            _FACE_SVGS[RobotFace.SAD], sanitize=False
                        ).style("width: 32px; height: 32px")
                        self._robot_face_tooltip = ui.tooltip("Disconnected")
                    # IO chips: DI1, DI2 | spacer | DO1, DO2
                    self._io_chips = []
                    for i in range(len(robot_state.io_inputs)):
                        chip = (
                            ui.chip(f"DI{i + 1}", color=IO_COLOR_OFF)
                            .props("dense size=sm")
                            .tooltip(f"Digital Input {i + 1}")
                        )
                        self._io_chips.append(chip)
                    ui.space()
                    for i in range(len(robot_state.io_outputs)):
                        chip = (
                            ui.chip(f"DO{i + 1}", color=IO_COLOR_OFF)
                            .props("dense size=sm")
                            .tooltip(f"Digital Output {i + 1}")
                        )
                        self._io_chips.append(chip)

                # Collapsible action log
                with (
                    ui.row()
                    .classes("items-center w-full no-wrap gap-0")
                    .mark("readout-action-log")
                ):
                    self._action_scroll_area = (
                        ui.scroll_area()
                        .classes("action-log flex-1")
                        .on("click", self._toggle_action_log)
                    )
                    with self._action_scroll_area:
                        self._action_log_html = ui.html("", sanitize=False).classes(
                            "w-full"
                        )

                # Initial state update (subsequent updates driven by status consumer)
                self.update_conn_io()
                self.update_action_log()
