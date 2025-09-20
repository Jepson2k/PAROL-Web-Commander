from __future__ import annotations

import logging
from typing import Literal, cast, get_args

from nicegui import app, ui

ThemeMode = Literal["light", "dark", "system"]


def get_palette(mode: ThemeMode) -> dict[str, str]:
    """Return CTk-mapped palette tokens for the given mode."""
    if mode == "dark":
        return {
            "primary": "#1F538D",  # acceptable alternative "#1F6AA5"
            "primary_hover": "#14375E",  # acceptable alternative "#144870"
            "background": "#1A1A1A",
            "surface": "#212121",
            "surface_top": "#292929",
            "text": "#D6D6D6",  # acceptable "#DCE4EE"
            "muted": "#949A9F",
            "seg_unselected": "#4A4A4A",
            "slider_track": "#4A4D50",
            "slider_progress": "#AAB0B5",
            "on_primary": "#DCE4EE",
            # Accent/hard-coded semantic colors
            "accent": "#22D3EE",
            "positive": "#21BA45",
            "negative": "#DB2828",
            "info": "#31CCEC",
            "warning": "#F2C037",
        }
    # light
    return {
        "primary": "#3B8ED0",
        "primary_hover": "#36719F",
        "background": "#EBEBEB",
        "surface": "#DBDBDB",
        "surface_top": "#CFCFCF",
        "text": "#1A1A1A",
        "muted": "#A6A6A6",
        "seg_unselected": "#979DA2",
        "slider_track": "#939BA2",
        "slider_progress": "#AAB0B5",
        "on_primary": "#DCE4EE",
        # Accent/hard-coded semantic colors
        "accent": "#22D3EE",
        "positive": "#21BA45",
        "negative": "#DB2828",
        "info": "#31CCEC",
        "warning": "#F2C037",
    }


def _inject_css_vars(p: dict[str, str]) -> None:
    """Inject global CSS variables and basic background/text mappings."""
    ui.add_css(
        f"""
:root {{
  --ctk-primary: {p["primary"]};
  --ctk-primary-hover: {p["primary_hover"]};
  --ctk-bg: {p["background"]};
  --ctk-surface: {p["surface"]};
  --ctk-surface-top: {p["surface_top"]};
  --ctk-text: {p["text"]};
  --ctk-muted: {p["muted"]};
  --ctk-on-primary: {p["on_primary"]};
  --ctk-seg-unselected: {p["seg_unselected"]};
  --ctk-slider-track: {p["slider_track"]};
  --ctk-slider-progress: {p["slider_progress"]};
}}

body, .q-page {{ background: var(--ctk-bg); color: var(--ctk-text); }}
"""
    )


def _inject_component_overrides() -> None:
    """Inject component-specific overrides to mimic CustomTkinter visual behavior."""
    ui.add_css(
        """
/* Containers and surfaces */
.q-header, .q-footer { background: var(--ctk-surface); color: var(--ctk-text); }
.q-card, .q-field, .q-toolbar, .q-item { background: var(--ctk-surface); color: var(--ctk-text); }

/* Buttons */
.q-btn:not(.q-btn--round) { border-radius: 6px; padding-top: 3px !important; padding-left: 6px !important; padding-bottom: 3px !important; padding-right: 6px !important; min-height: 32px !important; min-width: 32px !important; }
.q-btn.bg-primary:hover { background: var(--ctk-primary-hover) !important; }
.q-btn--flat, .q-btn--outline { color: var(--ctk-text); }
.q-slider__thumb { width: 30px !important; height: 30px !important; }
.q-slider__track { height: 8px !important; }

/* Inputs */
.q-input .q-field__native, .q-textarea .q-field__native { color: var(--ctk-text); padding-top: 12px !important; padding-bottom: 4px !important; }
.q-field__control { border-radius: 6px; }

/* Segmented toggle */
.q-btn-toggle .q-btn { border-radius: 6px; }
.q-btn-toggle .q-btn.q-btn--active { background: var(--ctk-primary); color: var(--ctk-on-primary); }
.q-btn-toggle .q-btn:not(.q-btn--active) { background: var(--ctk-seg-unselected); color: var(--ctk-on-primary); }

/* Misc */
.q-separator { background: var(--ctk-muted); }

/* Drag-and-drop visuals */
.drop-spacer { height: 4px; margin: 6px 0; border: 2px dashed transparent; border-radius: 6px; opacity: 0.6; transition: border-color .08s, background .08s; }
.drop-spacer.active { height: 28px; border-color: var(--q-accent); background: rgba(34, 211, 238, 0.18); }
.draggable-card { cursor: move; user-select: none; }
"""
    )


def apply_theme(mode: ThemeMode) -> None:
    """
    Apply the selected theme:
    - Set NiceGUI/Quasar colors and dark mode.
    - Inject CTk CSS variables and component overrides.
    """
    choice = mode
    if mode == "system":
        choice = "dark" if ui.dark_mode().client.page.dark else "light"
        logging.debug(f"System theme: {choice}")

    pal = get_palette(choice)

    # Quasar color tokens (primary/secondary/accent) and feedback colors
    ui.colors(
        primary=pal["primary"],
        secondary=pal["primary_hover"],
        accent=pal["accent"],
        positive=pal["positive"],
        negative=pal["negative"],
        info=pal["info"],
        warning=pal["warning"],
    )

    # Toggle Quasar dark mode
    if choice == "dark":
        ui.dark_mode().enable()
    else:
        ui.dark_mode().disable()

    # Inject variables and overrides
    _inject_css_vars(pal)
    _inject_component_overrides()


def set_theme(mode: ThemeMode) -> ThemeMode:
    """Persist, set and apply theme mode."""
    # persist selection
    app.storage.general["theme_mode"] = mode
    apply_theme(mode)
    return mode


def get_theme() -> ThemeMode:
    """Return current requested mode ('light'/'dark'/'system')."""
    mode = app.storage.general.get("theme_mode", "system")
    if isinstance(mode, str) and mode in get_args(ThemeMode):
        return cast("ThemeMode", mode)
    return cast("ThemeMode", "system")


def toggle_theme() -> ThemeMode:
    """Cycle through modes: system -> light -> dark -> system."""
    order: list[ThemeMode] = ["system", "light", "dark"]
    current = get_theme()
    try:
        idx = order.index(current)
    except ValueError:
        idx = 0
    next_mode: ThemeMode = order[(idx + 1) % len(order)]
    set_theme(next_mode)
    return next_mode


def inject_layout_css() -> None:
    """Injects the app's layout and component CSS previously embedded in main.py."""
    ui.add_css(
        """
/* Drag handle styles */
.drag-handle-btn {
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 8px;
  padding: 4px;
  transition: background .15s ease, border-color .15s ease, box-shadow .15s ease;
}
body.body--light .drag-handle-btn {
  background: rgba(0,0,0,0.06);
  border-color: rgba(0,0,0,0.12);
}
.drag-handle-btn:hover {
  background: rgba(255,255,255,0.16);
  border-color: rgba(255,255,255,0.24);
  box-shadow: 0 1px 2px rgba(0,0,0,0.3);
}
body.body--light .drag-handle-btn:hover {
  background: rgba(0,0,0,0.12);
  border-color: rgba(0,0,0,0.20);
}
.drag-handle-btn:active {
  cursor: grabbing;
}

/* Main layout responsive grid */
.move-layout-container {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  width: 100%;
}

/* Mobile breakpoint - stack to single column */
@media (max-width: 1060px) {
  .move-layout-container {
    grid-template-columns: 1fr;
    gap: 1rem;
  }
}

/* Joint jog responsive progress bars */
.joint-progress-container {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  max-width: 100%;
}

.joint-progress-bar {
  flex: 1;
  min-width: 480px;
  max-width: 1000px;
}

/* Responsive adjustments for joint progress bars */
@media (max-width: 768px) and (min-width: 481px) {
  .joint-progress-bar {
    min-width: 400px;
    max-width: 800px;
  }
}

@media (max-width: 480px) {
  .joint-progress-bar {
    min-width: 360px;
  }
}

/* Cartesian jog responsive grid */
.cart-jog-grid-3 {
  display: grid;
  grid-template-columns: repeat(3, minmax(60px, 72px));
  gap: 8px;
  justify-content: center;
}

.cart-jog-grid-6 {
  display: grid;
  grid-template-columns: repeat(3, minmax(50px, 60px));
  gap: 8px;
  justify-content: center;
}

/* Readouts panel responsive columns */
.readouts-row {
  display: flex;
  gap: 2rem;
  align-items: flex-start;
  width: 100%;
  flex-wrap: wrap;
}

.readouts-col {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 12rem;
  flex: 0 0 auto;
}

.readouts-controls {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  flex: 1;
}

/* Program editor responsive layout */
.editor-layout {
  display: flex;
  gap: 1rem;
  width: 100%;
}

.editor-main {
  flex: 1;
  min-width: 300px;
  max-width: none;
}

.editor-palette {
  flex: 0 0 auto;
  width: 10vw;
  min-width: 175px;
}

/* Editor palette header - prevent drag handle wrapping */
.editor-palette-header {
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  flex-wrap: nowrap !important;
  gap: 0.5rem !important;
  width: 100% !important;
  min-height: 2.5rem;
}

.editor-palette-header .q-toggle {
  flex-shrink: 1;
  min-width: 0;
  white-space: nowrap;
}

.editor-palette-header .drag-handle-btn {
  flex-shrink: 0;
  min-width: 28px;
}

/* Compact input field styling */
.q-field .q-field__control {
  max-height: 3em !important;
}

.q-field .q-field__native {
   padding: 0 !important;
}

.q-field__label {
    top: 12px !important;
}

/* Small screen adjustments for editor palette header */
@media (max-width: 768px) {
  .editor-palette {
    width: 16vw;
    min-width: 180px;
  }

  .editor-palette-header .drag-handle-btn {
    min-width: 24px;
    padding: 2px;
  }
}

@media (max-width: 640px) {
  .editor-palette {
    width: 18vw;
    min-width: 160px;
  }

  .editor-palette-header .q-toggle__label {
    font-size: 0.85rem;
  }
}

/* Mobile adjustments */
@media (max-width: 600px) {
  .readouts-row {
    flex-direction: column;
    gap: 1rem;
  }

  .readouts-col, .readouts-controls {
    width: 100%;
    min-width: unset;
  }

  .editor-layout {
    flex-direction: column;
  }

  .editor-main, .editor-palette {
    width: 100%;
    max-width: none;
    min-width: unset;
  }

  .joint-progress-bar {
    min-width: 150px;
    max-width: none;
  }
}

/* Tablet adjustments */
@media (max-width: 1200px) and (min-width: 769px) {
  .readouts-row {
    gap: 1rem;
  }

  .editor-palette {
    width: 16vw;
  }
}

/* Command palette table responsive styling */
.q-table {
  width: 100%;
  max-width: 100%;
}

.q-table .q-table__container {
  overflow-x: auto;
  max-width: 100%;
}

.q-table .q-td, .q-table .q-th {
  word-wrap: break-word;
  word-break: break-word;
  white-space: normal;
}

/* Pressed visual feedback for jog controls */
.is-pressed {
  transform: scale(0.96);
  filter: brightness(1.2);
  outline: 1px solid var(--q-accent);
  transition: transform 40ms linear, filter 40ms linear, outline-color 40ms linear;
}
"""
    )
