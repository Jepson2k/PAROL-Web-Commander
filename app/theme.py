from __future__ import annotations

from typing import Dict, Literal

from nicegui import ui

ThemeMode = Literal["light", "dark", "system"]

_current_mode: ThemeMode = "system"


def get_palette(mode: ThemeMode) -> Dict[str, str]:
    """Return CTk-mapped palette tokens for the given mode."""
    if mode == "dark":
        return {
            "primary":  "#1F538D",          # acceptable alternative "#1F6AA5"
            "primary_hover": "#14375E",    # acceptable alternative "#144870"
            "background": "#1A1A1A",
            "surface": "#212121",
            "surface_top": "#292929",
            "text": "#D6D6D6",             # acceptable "#DCE4EE"
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


def _inject_css_vars(p: Dict[str, str]) -> None:
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
.drop-spacer.active { border-color: var(--q-accent); background: rgba(34, 211, 238, 0.18); }
.droppable-col.highlight { box-shadow: inset 0 0 0 2px var(--q-accent); background: rgba(34, 211, 238, 0.08); }
.draggable-card { cursor: move; user-select: none; }
"""
    )


def apply_theme(mode: ThemeMode) -> None:
    """
    Apply the selected theme:
    - Set NiceGUI/Quasar colors and dark mode.
    - Inject CTk CSS variables and component overrides.
    Note: 'system' currently resolves on the server side with a dark-biased default.
    """
    global _current_mode
    _current_mode = mode

    # Best-effort system detection fallback: default to dark (refined client-side detection can adjust later if needed)
    effective = "dark" if mode == "system" else mode

    pal = get_palette("dark" if effective == "dark" else "light")

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
    if effective == "dark":
        ui.dark_mode().enable()
    else:
        ui.dark_mode().disable()

    # Inject variables and overrides
    _inject_css_vars(pal)
    _inject_component_overrides()


def set_theme(mode: ThemeMode) -> ThemeMode:
    """Set and apply theme mode."""
    apply_theme(mode)
    return mode


def get_theme() -> ThemeMode:
    """Return current requested mode ('light'/'dark'/'system')."""
    return _current_mode


def toggle_theme() -> ThemeMode:
    """Cycle through modes: system -> light -> dark -> system."""
    order = ["system", "light", "dark"]
    try:
        idx = order.index(_current_mode)
    except ValueError:
        idx = 0
    next_mode: ThemeMode = order[(idx + 1) % len(order)]  # type: ignore[assignment]
    set_theme(next_mode)
    return next_mode
