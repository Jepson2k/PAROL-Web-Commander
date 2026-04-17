"""Editor decorations: line highlights, flash animations, diagnostics, metadata."""

from __future__ import annotations

import logging
import re

from nicegui import Client, ui

from waldo_commander.state import simulation_state, ui_state

logger = logging.getLogger(__name__)

_ERROR_LINE_RE = re.compile(
    r'(?:File "simulation_script\.py", line (\d+))|(?:^Line (\d+):)',
    re.MULTILINE,
)


class EditorDecorations:
    """Manages CodeMirror decorations for the active editor tab.

    EditorPanel calls `set_textarea()` on tab switch to point at the
    active tab's CodeMirror widget. All decoration methods operate on
    that reference.
    """

    def __init__(self) -> None:
        self._ui_client: Client | None = None
        self._textarea: ui.codemirror | None = None

        # Python-side mirror of CM6 StateField target positions.
        # Updated via target-positions events emitted by JS on document changes.
        # Maps target index → current 1-indexed line number.
        self._target_positions: dict[str, int] = {}

    def set_textarea(self, textarea: ui.codemirror | None) -> None:
        """Point decorations at the active tab's CodeMirror widget."""
        self._textarea = textarea

    def set_ui_client(self, client: Client | None) -> None:
        """Store the NiceGUI client for JS execution from background tasks."""
        self._ui_client = client

    # ---- Line highlighting ----

    def highlight_executing_line(self, step_index: int) -> None:
        """Highlight the source line corresponding to the current step.

        Uses path_segments line_number to look up which line to highlight.
        """
        if not self._textarea:
            return

        if simulation_state.path_segments and 0 <= step_index < len(
            simulation_state.path_segments
        ):
            segment = simulation_state.path_segments[step_index]
            line_number = segment.line_number
            if line_number > 0:
                self._textarea.run_method(
                    "setDecorations",
                    {
                        "executing": [
                            {
                                "kind": "line",
                                "line": line_number,
                                "class": "cm-highlighted",
                            }
                        ]
                    },
                )
                self._textarea.run_method("revealLine", line_number)
                return

        self._textarea.run_method("setDecorations", {"executing": []})

    def clear_executing_line_highlight(self) -> None:
        """Clear the executing line highlight decoration."""
        if self._textarea:
            self._textarea.run_method("setDecorations", {"executing": []})

    # ---- Flash animations ----

    def flash_editor_lines(self, line_numbers: list[int]) -> None:
        """Flash specific lines in the CodeMirror editor to highlight newly added content.

        Args:
            line_numbers: List of 1-indexed line numbers to flash
        """
        if not self._textarea or not line_numbers:
            return

        if self._is_editor_panel_visible():
            self._textarea.highlight_lines(
                line_numbers,
                css_class="cm-line-flash",
                duration_ms=1500,
            )
        else:
            self._flash_editor_tab()

    def _flash_editor_tab(self) -> None:
        """Flash the editor tab to indicate new content when panel is collapsed."""
        js_code = """
        (function() {
            const tabs = document.querySelectorAll('.q-tab');
            for (const tab of tabs) {
                const icon = tab.querySelector('i');
                if (icon && icon.innerText === 'code') {
                    tab.classList.add('tab-flash');
                    setTimeout(() => tab.classList.remove('tab-flash'), 2000);
                    break;
                }
            }
        })();
        """
        try:
            ui.run_javascript(js_code)
        except RuntimeError:
            if self._ui_client:
                self._ui_client.run_javascript(js_code)
            else:
                logger.debug("Cannot flash editor tab: no client available")

    @staticmethod
    def _is_editor_panel_visible() -> bool:
        """Check if the editor panel is currently visible (not collapsed)."""
        return ui_state.program_panel_visible

    # ---- Diagnostics & metadata ----

    def apply_diagnostics(self, error: str | None = None) -> None:
        """Apply CM6 lint diagnostics for simulation errors and timing warnings."""
        if not self._textarea:
            return

        diagnostics: list[dict] = []

        if error:
            error_lines: set[int] = set()
            for m in _ERROR_LINE_RE.finditer(error):
                line_no = int(m.group(1) or m.group(2))
                error_lines.add(line_no)
            error_msg = error.strip().split("\n")[-1] if error.strip() else error
            for ln in sorted(error_lines):
                diagnostics.append(
                    {
                        "line": ln,
                        "severity": "error",
                        "message": error_msg,
                        "source": "simulation",
                    }
                )

        warned_lines: set[int] = set()
        for seg in simulation_state.path_segments:
            if seg.timing_feasible or seg.line_number <= 0:
                continue
            if seg.line_number in warned_lines:
                continue
            warned_lines.add(seg.line_number)
            if seg.estimated_duration is not None:
                diagnostics.append(
                    {
                        "line": seg.line_number,
                        "severity": "warning",
                        "message": f"Duration too short — minimum: {seg.estimated_duration:.2f}s",
                        "source": "timing",
                    }
                )

        self._textarea.set_diagnostics(diagnostics)

    def push_line_metadata(self) -> None:
        """Push per-line metadata to CM6 for hover tooltips."""
        if not self._textarea:
            return
        metadata: dict[int, dict] = {}
        for seg in simulation_state.path_segments:
            if seg.line_number <= 0 or not seg.points:
                continue
            end = seg.points[-1]
            pos_str = f"x: {end[0] * 1000:.1f}, y: {end[1] * 1000:.1f}, z: {end[2] * 1000:.1f} mm"
            dur_str = f"{seg.estimated_duration:.2f}s" if seg.estimated_duration else ""
            warnings = []
            if not seg.is_valid:
                warnings.append("Unreachable position")
            if not seg.timing_feasible and seg.estimated_duration is not None:
                warnings.append(
                    f"Duration too short (min: {seg.estimated_duration:.2f}s)"
                )

            entry: dict = {"position": pos_str}
            if dur_str:
                entry["duration"] = dur_str
            if warnings:
                entry["warnings"] = warnings
            metadata[seg.line_number] = entry

        self._textarea.set_line_tooltips(metadata, set_name="simulation")

    def push_target_positions(self) -> None:
        """Push current target positions to CM6 line anchors for edit tracking."""
        if not self._textarea:
            return
        anchors = [
            {"id": t.id, "line": t.line_number}
            for t in simulation_state.targets
            if t.line_number > 0
        ]
        self._textarea.set_line_anchors(anchors, set_name="targets")
        self._target_positions = {str(a["id"]): int(a["line"]) for a in anchors}
