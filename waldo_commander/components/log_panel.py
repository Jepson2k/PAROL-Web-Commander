"""Log panel controller: manages shared log area splitter + toggle button."""

from __future__ import annotations

from nicegui import ui


class LogPanelController:
    """Owns the shared log panel state (splitter position, expand/collapse).

    Widget references (editor_splitter, log_toggle_btn, log_toggle_btn_tooltip)
    are set after build(). The splitter ref is set by EditorPanel.build();
    the button refs are owned by PlaybackController and read via properties.
    """

    def __init__(self) -> None:
        self._log_expanded: bool = False
        self._splitter_value_when_expanded: float = 70.0

        # Set by EditorPanel after build() — split lives in the tab layout.
        self.editor_splitter: ui.splitter | None = None

        # The button/tooltip live on PlaybackController; EditorPanel wires
        # them in so we can update icon/tooltip text from here.
        self.log_toggle_btn: ui.button | None = None
        self.log_toggle_btn_tooltip: ui.tooltip | None = None

    def toggle(self) -> None:
        """Toggle shared log panel visibility via splitter position."""
        if self._log_expanded:
            self.collapse()
        else:
            self.expand()

    def expand(self) -> None:
        """Expand the shared log panel by adjusting splitter."""
        self._log_expanded = True
        if self.editor_splitter:
            self.editor_splitter.set_value(self._splitter_value_when_expanded)
        if self.log_toggle_btn:
            self.log_toggle_btn.props("icon=expand_less")
            if self.log_toggle_btn_tooltip:
                self.log_toggle_btn_tooltip.text = "Hide Output"

    def collapse(self) -> None:
        """Collapse the shared log panel by adjusting splitter."""
        self._log_expanded = False
        if self.editor_splitter:
            self.editor_splitter.set_value(94)  # 94% to editor (collapsed)
        if self.log_toggle_btn:
            self.log_toggle_btn.props("icon=expand_more")
            if self.log_toggle_btn_tooltip:
                self.log_toggle_btn_tooltip.text = "Show Output"

    def on_splitter_change(self, e) -> None:
        """Handle splitter drag changes to update log expanded state."""
        value = e.value
        if value is None:
            return

        # If user drags to near-bottom (>90%), treat as collapsed
        if value > 90:
            self._log_expanded = False
            if self.log_toggle_btn:
                self.log_toggle_btn.props("icon=expand_more")
                if self.log_toggle_btn_tooltip:
                    self.log_toggle_btn_tooltip.text = "Show Output"
        else:
            self._log_expanded = True
            self._splitter_value_when_expanded = value
            if self.log_toggle_btn:
                self.log_toggle_btn.props("icon=expand_less")
                if self.log_toggle_btn_tooltip:
                    self.log_toggle_btn_tooltip.text = "Hide Output"
