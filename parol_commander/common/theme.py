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
            "accent": "#2AA8DE",
            "positive": "#2EAD77",
            "negative": "#D6493E",
            "info": "#2AA8DE",
            "warning": "#F4C21E",
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
        "accent": "#2AA8DE",
        "positive": "#2EAD77",
        "negative": "#D6493E",
        "info": "#2AA8DE",
        "warning": "#F4C21E",
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

  /* Axis/TCP colors (fallbacks first, then OKLCH overrides) */
  --axis-x: #d94c3f; --axis-rx: #f1a79f;
  --axis-y: #2faf7a; --axis-ry: #aee5cf;
  --axis-z: #4a63e0; --axis-rz: #aeb9f3;

  --axis-x: oklch(0.51 0.15 28);
  --axis-rx: oklch(0.82 0.09 28);
  --axis-y: oklch(0.56 0.11 161);
  --axis-ry: oklch(0.86 0.08 165);
  --axis-z: oklch(0.62 0.20 265);
  --axis-rz: oklch(0.86 0.07 265);

  /* Glass defaults (dark-mode baseline) */
  --glass-blur: 36px;
  --glass-bg-1: rgba(255,255,255,0.16);
  --glass-bg-2: rgba(255,255,255,0.08);
  --glass-border: rgba(255,255,255,0.18);
  --glass-shadow: rgba(0,0,0,0.35);
  --glass-fg: var(--ctk-text);
  --glass-hover: rgba(255,255,255,0.08);

  /* OKLCH overrides for glass (dark baseline) */
  --glass-bg-1: oklch(0.93 0.01 230 / 0.16);
  --glass-bg-2: oklch(0.93 0.01 230 / 0.08);
  --glass-border: oklch(0.93 0.01 230 / 0.18);

  /* Unified overlay variables (dark baseline) */
  --overlay-bg-1: oklch(0.93 0.01 230 / 0.20);
  --overlay-bg-2: oklch(0.93 0.01 230 / 0.10);
  --overlay-border: var(--glass-border);
  --overlay-shadow: var(--glass-shadow);
  --overlay-blur: var(--glass-blur);
  --overlay-stroke-light: #ffffff;
  --overlay-stroke-dark: #000000;
  --overlay-reflex-light: 1;
  --overlay-reflex-dark: 0.6;
  --overlay-saturation: 150%;

  /* Semantic brand tokens (fallbacks then OKLCH) */
  --sem-danger: #D6493E;
  --sem-warning: #F4C21E;
  --sem-success: #2EAD77;
  --sem-info: #2AA8DE;
  --brand-accent: #2AA8DE;

  --sem-danger: oklch(0.62 0.24 28);
  --sem-warning: oklch(0.88 0.14 95);
  --sem-success: oklch(0.72 0.18 150);
  --sem-info: oklch(0.80 0.15 220);
  --brand-accent: oklch(0.80 0.15 220);

  /* On-color defaults for legibility */
  --on-danger: #ffffff;
  --on-warning: #1a1a1a;
  --on-success: #0b1612;
  --on-info: #0b141a;
  --on-accent: #0b141a;

  /* Joint bar height */
  --joint-bar-h: 33px;
}}

body, .q-page {{ background: var(--ctk-bg); color: var(--ctk-text); }}

/* Flip glass to dark-tinted in light mode */
body.body--light {{
  --glass-bg-1: rgba(0,0,0,0.32);
  --glass-bg-2: rgba(0,0,0,0.18);
  --glass-border: rgba(0,0,0,0.22);
  --glass-shadow: rgba(0,0,0,0.18);
  --glass-fg: #F2F5F7;
  --glass-hover: rgba(255,255,255,0.10);

  --glass-bg-1: oklch(0.28 0.02 260 / 0.32);
  --glass-bg-2: oklch(0.28 0.02 260 / 0.18);
  --glass-border: oklch(0.28 0.02 260 / 0.22);

  /* Unified overlay variables (light overrides) */
  --overlay-bg-1: oklch(0.28 0.02 260 / 0.22);
  --overlay-bg-2: oklch(0.28 0.02 260 / 0.12);
  --overlay-border: oklch(0.28 0.02 260 / 0.18);
  --overlay-reflex-light: 0.6;
  --overlay-reflex-dark: 1.2;
  --overlay-saturation: 160%;
}}

/* Ensure component-scoped dark contexts inherit glass defaults */
.q-dark {{
  --glass-bg-1: oklch(0.93 0.01 230 / 0.16);
  --glass-bg-2: oklch(0.93 0.01 230 / 0.08);
  --glass-border: oklch(0.93 0.01 230 / 0.18);
  --glass-shadow: rgba(0,0,0,0.35);
  --glass-fg: var(--ctk-text);
  --glass-hover: rgba(255,255,255,0.08);
}}
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

/* Frosted glass for overlays and utility */
.glass-surface,
.q-menu,
.q-dialog__inner > .q-card,
.q-dialog .q-card,
.q-drawer.q-drawer--on-top {
  background: linear-gradient(135deg, var(--glass-bg-1), var(--glass-bg-2)) !important;
  color: var(--glass-fg) !important;
  backdrop-filter: saturate(1.05) blur(var(--glass-blur));
  -webkit-backdrop-filter: saturate(1.05) blur(var(--glass-blur));
  border: 1px solid var(--glass-border) !important;
  box-shadow:
    0 8px 24px var(--glass-shadow),
    inset 0 1px 0 rgba(255,255,255,0.12);
}

/* Transparent inners on glass */
.glass-surface .q-field,
.glass-surface .q-item,
.q-menu .q-list,
.q-menu .q-item,
.q-dialog .q-card .q-field,
.q-drawer.q-drawer--on-top .q-item {
  background: transparent !important;
  color: var(--glass-fg) !important;
}

/* Hover feedback on glass items */
.glass-surface .q-item:hover,
.q-menu .q-item:hover {
  background: var(--glass-hover) !important;
}

/* Inputs on glass */
.glass-surface .q-field__control,
.q-dialog .q-card .q-field__control {
  border: 1px solid var(--glass-border);
}

/* Transparent shell and transparent fields */
body.body--dark, .q-dark, .q-dark .q-page { background: transparent !important; }
body.body--light, .q-light, .q-light .q-page { background: transparent !important; }
.q-field { background: transparent !important; }

/* Disable input steppers for numercal input */
input::-webkit-outer-spin-button,
input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
input[type=number] {
  -moz-appearance: textfield;
}
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

/* Joint control bars with integrated pill buttons */
.joint-bar {
  border-radius: 9999px !important;
  height: var(--joint-bar-h);
}

.joint-cap {
  height: calc(var(--joint-bar-h) - 1px);
  width: var(--joint-bar-h);
  min-height: 0;
  padding: 0;
  border-radius: 9999px;
  background: transparent !important;
  color: #fff !important;
  font-size: 19px;
}

.joint-cap:hover {
  opacity: 0.8;
}

.joint-cap.q-btn--disabled {
  color: #aaa !important;
  pointer-events: none;
}


/* Overlay panels with frosted glass effect */
.overlay-panel { position: absolute; z-index: 10; pointer-events: auto; }
.overlay-card {
  padding: 10px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--overlay-bg-1), var(--overlay-bg-2)) !important;
  backdrop-filter: blur(var(--overlay-blur)) saturate(var(--overlay-saturation));
  -webkit-backdrop-filter: blur(var(--overlay-blur)) saturate(var(--overlay-saturation));
  border: 0 !important;
  /* Layered light/dark reflex for liquid-glass feel */
  box-shadow:
    # inset 0 0 0 1px color-mix(in srgb, var(--overlay-stroke-light) calc(var(--overlay-reflex-light) * 10%), transparent),
    # inset 2px 1.5px 0 -1px color-mix(in srgb, var(--overlay-stroke-light) calc(var(--overlay-reflex-light) * 75%), transparent),
    # inset -1.5px -1px 0 -1px color-mix(in srgb, var(--overlay-stroke-light) calc(var(--overlay-reflex-light) * 60%), transparent),
    # inset -2px -6px 1px -5px color-mix(in srgb, var(--overlay-stroke-light) calc(var(--overlay-reflex-light) * 40%), transparent),
    inset -0.3px -1px 4px 0 color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 12%), transparent),
    inset -1.5px 2.5px 0 -2px color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 18%), transparent),
    inset 0 3px 4px -2px color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 18%), transparent),
    0 6px 16px 0 color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 8%), transparent);
  isolation: isolate;
}

/* Overlay anchors */
.overlay-tl { top: 12px; left: 12px; }
.overlay-tr { top: 12px; right: 12px; }
.overlay-bl { bottom: 12px; left: 12px; }
.overlay-br { bottom: 12px; right: 12px; }
.overlay-right {
  position: absolute;
  top: 50%;
  right: 12px;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 12;
}

.q-tab-panels {
  background: transparent !important;
}

/* Control panel jog tabs: zero padding only here */
.cp-jog-panels .q-tab-panels,
.cp-jog-panels .q-tab-panel {
  padding: 0 !important;
  overflow: hidden;
}

/* Axis/TCP colors */
.tcp-x  { color: var(--axis-x); }
.tcp-rx { color: var(--axis-rx); }
.tcp-y  { color: var(--axis-y); }
.tcp-ry { color: var(--axis-ry); }
.tcp-z  { color: var(--axis-z); }
.tcp-rz { color: var(--axis-rz); }

/* smaller expansion header */
.q-expansion-item .q-item {
  min-height: 34px;
  padding: 4px 10px;
}

/* Mobile: overlays flush to edges */
@media (max-width: 420px) {
  .overlay-tl { top: 0; left: 0; }
  .overlay-bl { bottom: 0; left: 0; }

  /* Only remove rounding for the two overlay panels, not all cards */
  .overlay-card.overlay-tl,
  .overlay-card.overlay-bl {
    border-radius: 0 !important;
  }

  /* Reduce overlay card padding on mobile */
  .overlay-card { padding: 5px; }
}
"""
    )
