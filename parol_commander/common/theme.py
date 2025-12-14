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

  /* Simulator mode amber - used for arm ghosting and toggle button */
  --sim-amber: #c77d28;
  --sim-amber: oklch(0.62 0.15 65);

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

/* Strong disabled utility for controls */
.cp-disabled-strong {
  opacity: 0.15 !important;
  filter: grayscale(1) contrast(0.6) brightness(0.8);
  pointer-events: none !important;
  cursor: not-allowed !important;
  box-shadow: none !important;
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


# Panel resize configuration (passed to JS module)
PANEL_RESIZE_CONFIG = {
    "storageKey": "parol_panel_sizes",
    "selectors": {
        "wrap": ".left-wrap",
        "topContainer": ".left-panels",
        "bottomContainer": ".bottom-panels",
    },
    "constraints": {
        "viewportMarginX": 80,
        "viewportMarginY": 100,
        "containerPadding": 20,
        "bottomOffset": 12,
        "totalMargin": 36,
    },
    "stateClasses": {
        "bottomOpen": "bottom-open",
        "bottomOpenNonProgram": "bottom-open-non-program",
        "panelOpen": "is-open",
    },
    "panels": {
        "program": {
            "selector": ".left-panels .program-panel",
            "minWidth": 400,
            "minHeight": 300,
            "group": "top",
            "pushTarget": "response",
        },
        "response": {
            "selector": ".bottom-panels .response-panel",
            "minWidth": 300,
            "minHeight": 100,
            "group": "bottom",
            "pushTarget": "program",
        },
    },
}


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

/* Resizable panel areas - scoped tab panel styling */
/* .left-panels and .bottom-panels ARE the q-tab-panels containers */
.left-panels,
.bottom-panels {
  background: transparent !important;
  overflow: hidden !important;
  scrollbar-width: none !important; /* Firefox */
  -ms-overflow-style: none !important; /* IE/Edge */
}

.left-panels::-webkit-scrollbar,
.bottom-panels::-webkit-scrollbar {
  display: none !important; /* Chrome, Safari, Opera */
}

/* Prevent scrollbar flash during tab transitions - scoped to resizable areas */
.left-panels > .q-tab-panel--inactive,
.bottom-panels > .q-tab-panel--inactive {
  overflow: hidden !important;
}

/* Hide scrollbars on resizable tab panels during transitions */
.left-panels > .q-tab-panel,
.bottom-panels > .q-tab-panel {
  scrollbar-width: none !important; /* Firefox */
  -ms-overflow-style: none !important; /* IE/Edge */
}

.left-panels > .q-tab-panel::-webkit-scrollbar,
.bottom-panels > .q-tab-panel::-webkit-scrollbar {
  display: none !important; /* Chrome, Safari, Opera */
}

/* Prevent full-page scrollbar flash globally */
html, body {
  overflow: hidden !important;
  height: 100%;
  width: 100%;
}

.q-page {
  overflow: hidden !important;
}

/* Main app container should also clip */
.q-layout, .q-page-container {
  overflow: hidden !important;
}

/* Control panel jog tabs: zero padding only here */
.cp-jog-panels .q-tab-panels,
.cp-jog-panels .q-tab-panel {
  padding: 0 !important;
  overflow: hidden;
}

/* IO and Gripper tab content - fixed max-height to prevent shrinking on small screens */
.io-gripper-content {
  max-height: min(calc(100vh - 120px), 600px);
  min-height: 200px;
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

 /* Log line coloring for response log */
 .nicegui-log .log-trace   { color: var(--sem-info);    opacity: 0.8; }
 .nicegui-log .log-debug   { color: var(--ctk-muted);   opacity: 0.9; }
 .nicegui-log .log-info    { color: var(--ctk-text); }
 .nicegui-log .log-warning { color: var(--sem-warning); }
 .nicegui-log .log-error   { color: var(--sem-danger); }
 .nicegui-log .log-critical {
   color: var(--on-danger);
   background: var(--sem-danger);
   padding: 0 4px;
   border-radius: 3px;
 }

/* Program editor panel - full width, can expand to push right side cards */
.left-panels {
  /* Use CSS variable for width persistence, with max constraint */
  max-width: calc(100vw - 60px) !important;
  transition: max-width 0.2s ease;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Ensure tab panels fill and constrain height properly */
.left-panels .q-tab-panels {
  flex: 1 1 auto;
  min-height: 0;
  max-height: 100%;
  overflow: hidden;
}

/* Default tab panel styling - small fixed size for non-program tabs */
.left-panels .q-tab-panel {
  height: auto;
  max-height: calc(100vh - 100px);
  overflow: auto;
  display: flex;
  flex-direction: column;
  width: auto;
  min-width: 300px;
}

/* Program tab panel - large resizable, can expand to push readouts */
/* Uses CSS variables set by JS for persistence across page refresh */
.left-panels .q-tab-panel[name="program"],
.left-panels .program-panel {
  width: var(--panel-width-program, 500px);
  max-width: calc(100vw - 80px);
  min-width: 350px;
  /* Use 100% height to fill parent - parent shrinks when log opens */
  height: 100%;
  min-height: 250px;
}

/* Program panel - shrinks to fit container, with JS resize handle */
.program-panel {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 300px;
  height: 100%;
  max-height: 100%;
  min-width: 400px;
  max-width: calc(100vw - 80px);
  position: relative;
  flex: 1 1 auto;
}

/* Right edge resize handle - actual drag target */
.program-panel .resize-handle-right {
  position: absolute;
  right: -4px;
  top: 0;
  bottom: 12px;
  width: 12px;
  cursor: ew-resize;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Visual indicator inside the right handle */
.program-panel .resize-handle-right::after {
  content: '';
  width: 4px;
  height: 50px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 2px;
  transition: background 0.15s ease, height 0.15s ease;
}

.program-panel .resize-handle-right:hover::after {
  background: rgba(255, 255, 255, 0.45);
  height: 70px;
}

.program-panel .resize-handle-right.dragging::after {
  background: var(--ctk-primary);
  height: 90px;
}

/* Bottom edge resize handle */
.program-panel .resize-handle-bottom {
  position: absolute;
  bottom: -4px;
  left: 0;
  right: 12px;
  height: 12px;
  cursor: ns-resize;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Visual indicator inside the bottom handle */
.program-panel .resize-handle-bottom::after {
  content: '';
  height: 4px;
  width: 50px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 2px;
  transition: background 0.15s ease, width 0.15s ease;
}

.program-panel .resize-handle-bottom:hover::after {
  background: rgba(255, 255, 255, 0.45);
  width: 70px;
}

.program-panel .resize-handle-bottom.dragging::after {
  background: var(--ctk-primary);
  width: 90px;
}

/* Corner resize handle (bottom-right) */
.program-panel .resize-handle-corner {
  position: absolute;
  right: -4px;
  bottom: -4px;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  z-index: 101;
  display: flex;
  align-items: center;
  justify-content: center;
}

.program-panel .resize-handle-corner::after {
  content: '';
  width: 8px;
  height: 8px;
  background: rgba(255, 255, 255, 0.25);
  border-radius: 2px;
  transition: background 0.15s ease;
}

.program-panel .resize-handle-corner:hover::after {
  background: rgba(255, 255, 255, 0.5);
}

.program-panel .resize-handle-corner.dragging::after {
  background: var(--ctk-primary);
}

/* Light mode handle styling */
body.body--light .program-panel .resize-handle-right::after,
body.body--light .program-panel .resize-handle-bottom::after {
  background: rgba(0, 0, 0, 0.15);
}

body.body--light .program-panel .resize-handle-right:hover::after,
body.body--light .program-panel .resize-handle-bottom:hover::after {
  background: rgba(0, 0, 0, 0.35);
}

body.body--light .program-panel .resize-handle-right.dragging::after,
body.body--light .program-panel .resize-handle-bottom.dragging::after {
  background: var(--ctk-primary);
}

body.body--light .program-panel .resize-handle-corner::after {
  background: rgba(0, 0, 0, 0.15);
}

body.body--light .program-panel .resize-handle-corner:hover::after {
  background: rgba(0, 0, 0, 0.35);
}

/* During resize, prevent text selection, scrollbars and transitions */
body.resizing-panel {
  cursor: ew-resize !important;
  user-select: none !important;
  overflow: hidden !important;
}

body.resizing-panel * {
  cursor: ew-resize !important;
  user-select: none !important;
  transition: none !important;
}

body.resizing-panel .left-panels,
body.resizing-panel .program-panel,
body.resizing-panel .overlay-tr {
  transition: none !important;
}

/* Prevent scrollbar flash during viewport resize */
body.viewport-resizing {
  overflow: hidden !important;
}

body.viewport-resizing .left-panels,
body.viewport-resizing .program-panel,
body.viewport-resizing .overlay-tr {
  transition: none !important;
}

/* Make the editor splitter fill its container and be flexible */
.program-panel .editor-splitter {
  flex: 1 1 auto;
  min-height: 0;
  height: auto !important;
  overflow: hidden;
}

/* CodeMirror editor needs to fill available space */
.program-panel .cm-editor {
  height: 100% !important;
  min-height: 50px;
  border-radius: 12px 12px 0 0;
  overflow: hidden;
}

/* Round the top left corner of the gutter */
.program-panel .cm-editor .cm-gutters {
  border-top-left-radius: 12px;
}

/* Log area rounded bottom corners */
.editor-splitter .q-splitter__after .nicegui-scroll-area {
  border-radius: 0 0 12px 12px;
}

.editor-splitter .q-splitter__after .nicegui-log {
  border-radius: 0 0 12px 12px;
}

/* Style CodeMirror's internal scrollbar */
.cm-scroller::-webkit-scrollbar {
  width: 12px;
  height: 12px;
}

.cm-scroller::-webkit-scrollbar-track {
  background: transparent;
}

.cm-scroller::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2);
  border-radius: 4px;
}

.cm-scroller::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.35);
}

body.body--light .cm-scroller::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.2);
}

body.body--light .cm-scroller::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.35);
}

/* Ensure splitter panels are flexible */
.editor-splitter .q-splitter__panel {
  overflow: auto;
}

/* Editor splitter styling with visible separator */
.editor-splitter {
  width: 100%;
  pointer-events: auto !important;
  overflow: visible !important;
  min-height: 0;
}

/* Ensure splitter content can scroll but separator stays visible */
.editor-splitter > .q-splitter__panel {
  overflow: auto;
}

.editor-splitter .q-splitter__before {
  overflow: auto;
  flex-shrink: 1;  /* Allow CodeMirror panel to shrink */
  min-height: 0;   /* Allow shrinking below content height */
}

.editor-splitter .q-splitter__after {
  overflow: auto;
  flex-shrink: 1;
  min-height: 0;
}

/* Make splitter separator hold the playbar as handle */
.editor-splitter .q-splitter__separator {
  background: transparent !important;
  height: auto !important;
  min-height: 48px !important;
  width: 100% !important;
  cursor: row-resize !important;
  pointer-events: auto !important;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: -16px 0;
  flex-shrink: 0;  /* Never shrink - playbar always visible */
  z-index: 10;
}

/* Remove any default Quasar backgrounds on separator children */
.editor-splitter .q-splitter__separator > * {
  background: transparent !important;
}

.editor-splitter .q-splitter__separator-area {
  background: transparent !important;
}

/* Responsive: Adjust left panels on medium/large screens */
@media (max-width: 1100px) {
  .left-panels {
    max-width: calc(100vw - 300px) !important;
  }
  .left-panels .q-tab-panel[name="program"] {
    width: calc(100vw - 350px);
    min-width: 450px;
  }
}

/* Medium screens */
@media (max-width: 900px) {
  .left-panels {
    max-width: calc(100vw - 250px) !important;
  }
  .left-panels .q-tab-panel[name="program"] {
    width: calc(100vw - 300px);
    min-width: 500px;
  }
}

/* Playback overlay at bottom-center */
.playback-overlay {
  pointer-events: auto;
}

.playback-overlay .overlay-card {
  min-width: 400px;
  max-width: 600px;
}

.playback-overlay .q-slider {
  min-width: 150px;
}

/* ========== Multi-Tab Editor Styles ========== */

/* Editor tabs container */
.editor-tabs {
  background: transparent !important;
}

.editor-tabs .q-tab {
  padding: 4px 8px !important;
  min-height: 36px !important;
  text-transform: none !important;
}

/* Individual editor tab styling */
.editor-tab {
  background: rgba(255, 255, 255, 0.08);
  border-radius: 6px 6px 0 0;
  margin-right: 2px;
  transition: background 0.15s ease;
}

.editor-tab:hover {
  background: rgba(255, 255, 255, 0.12);
}

.editor-tab.q-tab--active {
  background: rgba(255, 255, 255, 0.18);
}

body.body--light .editor-tab {
  background: rgba(0, 0, 0, 0.06);
}

body.body--light .editor-tab:hover {
  background: rgba(0, 0, 0, 0.10);
}

body.body--light .editor-tab.q-tab--active {
  background: rgba(0, 0, 0, 0.14);
}

/* Compact filename input in tabs */
.editor-tab .q-field {
  min-height: 24px !important;
}

.editor-tab .q-field__control {
  height: 24px !important;
  min-height: 24px !important;
}

.editor-tab .q-field__native {
  padding: 0 4px !important;
  min-height: 20px !important;
  font-size: 0.85rem;
}

/* Compact save FAB in tabs */
.editor-tab .save-fab {
  min-width: 24px !important;
  min-height: 24px !important;
  width: 24px !important;
  height: 24px !important;
}

.editor-tab .save-fab .q-icon {
  font-size: 14px !important;
}

/* Editor tab panel */
.editor-tab-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 0 !important;
  position: relative;
}

/* Fade CodeMirror content at bottom using mask - fades to transparent */
.editor-tab-panel {
  -webkit-mask-image: linear-gradient(to bottom, black 0%, black calc(100% - 16px), transparent 100%);
  mask-image: linear-gradient(to bottom, black 0%, black calc(100% - 16px), transparent 100%);
}

/* Fade log content at top using mask - fades in from transparent */
.editor-splitter .q-splitter__after {
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, black 16px, black 100%);
  mask-image: linear-gradient(to bottom, transparent 0%, black 16px, black 100%);
}

/* Editor header scroll area - no padding */
.program-panel .nicegui-scroll-area .q-scrollarea__content {
  padding: 0 !important;
  gap: 0 !important;
}

/* Bottom playback bar */
.bottom-playback-bar {
  background-color: var(--overlay-bg-1) !important;
  backdrop-filter: blur(16px) saturate(var(--overlay-saturation));
  -webkit-backdrop-filter: blur(16px) saturate(var(--overlay-saturation));
  flex-shrink: 0;
  border-radius: 9999px;
  border: 0 !important;
  box-shadow:
    inset -0.3px -1px 4px 0 color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 12%), transparent),
    inset -1.5px 2.5px 0 -2px color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 18%), transparent),
    inset 0 3px 4px -2px color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 18%), transparent),
    0 6px 16px 0 color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 8%), transparent);
  isolation: isolate;
  padding: 0 12px;
  position: relative;
  z-index: 10;
}

.bottom-playback-bar .q-slider {
  min-width: 50px;
}

/* Ensure playbar buttons remain clickable inside splitter separator */
.editor-splitter .bottom-playback-bar {
  pointer-events: auto;
  cursor: default;
}

.editor-splitter .bottom-playback-bar .q-btn,
.editor-splitter .bottom-playback-bar .q-slider,
.editor-splitter .bottom-playback-bar .q-fab {
  pointer-events: auto;
  cursor: pointer;
}

/* Ensure readouts come back into view when tab is closed */
.overlay-tr {
  transition: transform 0.3s ease;
}

/* Phone screens - hide left tabs, center right panels */
@media (max-width: 640px) {
  /* Hide left tab bar and panels completely */
  .side-tab-bar {
    display: none !important;
    visibility: hidden !important;
    opacity: 0 !important;
    width: 0 !important;
    height: 0 !important;
    overflow: hidden !important;
  }
  .left-panels { display: none !important; }

  /* Center panels horizontally using transform */
  .overlay-tr {
    right: auto !important;
    left: 50% !important;
    transform: translateX(-50%) !important;
    /* Variable top margin that goes to 0 on small screens */
    top: max(0px, calc((100vw - 360px) * 0.0375)) !important;
    /* Prevent text wrapping, scale down instead */
    white-space: nowrap !important;
    font-size: clamp(0.65rem, 2.8vw, 1rem) !important;
  }

  .overlay-br {
    right: auto !important;
    left: 50% !important;
    transform: translateX(-50%) !important;
    /* Variable bottom margin that goes to 0 on small screens */
    bottom: max(0px, calc((100vw - 360px) * 0.0375)) !important;
  }
}

/* Small phone screens - scale control panel to fit */
/* Using stepped breakpoints since CSS can't compute unitless scale from viewport units */
@media (max-width: 414px) {
  .overlay-br {
    transform: translateX(-50%) scale(0.95) !important;
    transform-origin: center bottom !important;
  }
  .overlay-tr {
    transform: translateX(-50%) scale(0.95) !important;
    transform-origin: center bottom !important;
  }
}

@media (max-width: 380px) {
  .overlay-br {
    transform: translateX(-50%) scale(0.88) !important;
    transform-origin: center bottom !important;
  }
  .overlay-tr {
    transform: translateX(-50%) scale(0.88) !important;
    transform-origin: center bottom !important;
  }
}

@media (max-width: 340px) {
  .overlay-br {
    transform: translateX(-50%) scale(0.8) !important;
    transform-origin: center bottom !important;
  }
  .overlay-tr {
    transform: translateX(-50%) scale(0.8) !important;
    transform-origin: center bottom !important;
  }
}

/* Transition for overlay panels on resize */
@media (min-width: 641px) {
  .overlay-tr, .overlay-br {
    transition: transform 0.3s ease, left 0.3s ease, right 0.3s ease, width 0.3s ease;
  }
}

/* Bottom panels (response log) - resizable styling */
.bottom-panels {
  position: absolute;
  bottom: 12px;
  left: 58px;
  height: var(--panel-height-response, 50vh);
  width: var(--panel-width-response, calc(50vw - 58px));
  max-width: calc(100vw - 80px);
  pointer-events: none;
  transition: height 0.2s ease;
}

.bottom-panels .q-tab-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* Top resize handle for response panel (log) */
.response-panel .resize-handle-top {
  position: absolute;
  top: -6px;
  left: 0;
  right: 0;
  height: 14px;
  cursor: ns-resize;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: auto;
}

.response-panel .resize-handle-top::after {
  content: '';
  height: 4px;
  width: 50px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 2px;
  transition: background 0.15s ease, width 0.15s ease;
}

.response-panel .resize-handle-top:hover::after {
  background: rgba(255, 255, 255, 0.45);
  width: 70px;
}

.response-panel .resize-handle-top.dragging::after {
  background: var(--ctk-primary);
  width: 90px;
}

body.body--light .response-panel .resize-handle-top::after {
  background: rgba(0, 0, 0, 0.15);
}

body.body--light .response-panel .resize-handle-top:hover::after {
  background: rgba(0, 0, 0, 0.35);
}

/* Right edge resize handle for response panel */
.response-panel .resize-handle-right {
  position: absolute;
  right: -4px;
  top: 0;
  bottom: 0;
  width: 12px;
  cursor: ew-resize;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: auto;
}

.response-panel .resize-handle-right::after {
  content: '';
  width: 4px;
  height: 50px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 2px;
  transition: background 0.15s ease, height 0.15s ease;
}

.response-panel .resize-handle-right:hover::after {
  background: rgba(255, 255, 255, 0.45);
  height: 70px;
}

.response-panel .resize-handle-right.dragging::after {
  background: var(--ctk-primary);
  height: 90px;
}

body.body--light .response-panel .resize-handle-right::after {
  background: rgba(0, 0, 0, 0.15);
}

body.body--light .response-panel .resize-handle-right:hover::after {
  background: rgba(0, 0, 0, 0.35);
}

/* Corner resize handle for response panel (top-right) */
.response-panel .resize-handle-corner {
  position: absolute;
  right: -4px;
  top: -4px;
  width: 16px;
  height: 16px;
  cursor: nesw-resize;
  z-index: 101;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: auto;
}

.response-panel .resize-handle-corner::after {
  content: '';
  width: 8px;
  height: 8px;
  background: rgba(255, 255, 255, 0.25);
  border-radius: 2px;
  transition: background 0.15s ease;
}

.response-panel .resize-handle-corner:hover::after {
  background: rgba(255, 255, 255, 0.5);
}

.response-panel .resize-handle-corner.dragging::after {
  background: var(--ctk-primary);
}

body.body--light .response-panel .resize-handle-corner::after {
  background: rgba(0, 0, 0, 0.15);
}

body.body--light .response-panel .resize-handle-corner:hover::after {
  background: rgba(0, 0, 0, 0.35);
}

/* Response panel needs position relative for absolute handles */
.response-panel {
  position: relative;
  overflow: hidden !important;
}

.response-panel .q-scrollarea__absolute > * {
  max-width: 100%;
  box-sizing: border-box;
  overflow-x: hidden;
  white-space: normal;
}

/* Bottom panel open/close state classes */
.bottom-panels.is-open {
  pointer-events: auto !important;
}

/* When program tab is active and response log is open, couple their heights */
.left-wrap.bottom-open {
  height: calc(100% - 50vh - 12px) !important;
}

/* When non-program tabs (IO/Gripper) are active with response log open,
   let them stay their natural small size - no height coupling */
.left-wrap.bottom-open-non-program {
  height: auto !important;
  max-height: calc(100% - 50vh - 24px) !important;
}

/* Reduce vertical tab padding to match right side panel margins (12px) */
.q-tabs--vertical .q-tab {
  padding: 8px 12px !important;
  min-height: 44px !important;
}

/* Make left tab column narrower */
.q-tabs--vertical {
  width: 52px !important;
}

/* Side tab bar with frosted glass effect - unified bar appearance */
.side-tab-bar {
  background: linear-gradient(135deg, var(--overlay-bg-1), var(--overlay-bg-2)) !important;
  backdrop-filter: blur(var(--overlay-blur)) saturate(var(--overlay-saturation));
  -webkit-backdrop-filter: blur(var(--overlay-blur)) saturate(var(--overlay-saturation));
  border: 0 !important;
  border-radius: 10px;
  box-shadow:
    inset -0.3px -1px 4px 0 color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 12%), transparent),
    inset -1.5px 2.5px 0 -2px color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 18%), transparent),
    inset 0 3px 4px -2px color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 18%), transparent),
    0 6px 16px 0 color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 8%), transparent);
  isolation: isolate;
  margin: 12px;
  padding: 4px 0;
  pointer-events: auto;
  height: auto !important;
  min-height: 0 !important;
}

/* Ensure tabs inside the bar have proper sizing */
.side-tab-bar .q-tab {
  min-height: 44px !important;
  padding: 8px 12px !important;
}

/* Left panels positioned to slide out from underneath the tab bar */
.left-panels {
  margin-left: 0 !important;
  padding-left: 0 !important;
  position: relative;
}

/* Tab panels appear to come from underneath with left edge shadow */
.left-panels .q-tab-panel.overlay-card {
  border-top-left-radius: 0 !important;
  border-bottom-left-radius: 12px !important;
  box-shadow:
    inset 4px 0 8px -4px color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 25%), transparent),
    inset -0.3px -1px 4px 0 color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 12%), transparent),
    inset -1.5px 2.5px 0 -2px color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 18%), transparent),
    inset 0 3px 4px -2px color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 18%), transparent),
    0 6px 16px 0 color-mix(in srgb, var(--overlay-stroke-dark) calc(var(--overlay-reflex-dark) * 8%), transparent);
}

/* Bottom panels appear to come from underneath the tab bar */
.bottom-panels .q-tab-panel.overlay-card {
  border-bottom-left-radius: 0 !important;
}

/* Custom slide animations for left panels - override Quasar transitions */
/* These ensure panels always slide in from left and out to left */
@keyframes left-panel-enter {
  from {
    transform: translateX(-100%);
    opacity: 0;
  }
  to {
    transform: translateX(0);
    opacity: 1;
  }
}

@keyframes left-panel-leave {
  from {
    transform: translateX(0);
    opacity: 1;
  }
  to {
    transform: translateX(-100%);
    opacity: 0;
  }
}

/* Apply custom animations to left panel transitions - target .q-panel.scroll */
.left-panels .q-panel.scroll[class*="q-transition--slide"] {
  animation-duration: 0.3s !important;
  animation-timing-function: ease-out !important;
}

/* Entering panel - slide in from left */
.left-panels .q-panel.scroll.q-transition--slide-right-enter-active,
.left-panels .q-panel.scroll.q-transition--slide-left-enter-active {
  animation-name: left-panel-enter !important;
}

/* Leaving panel - slide out to left */
.left-panels .q-panel.scroll.q-transition--slide-right-leave-active,
.left-panels .q-panel.scroll.q-transition--slide-left-leave-active {
  animation-name: left-panel-leave !important;
}

/* Also handle vertical transitions (slide-up/slide-down) that Quasar uses for first tab open */
.left-panels .q-panel.scroll.q-transition--slide-up-enter-active,
.left-panels .q-panel.scroll.q-transition--slide-down-enter-active {
  animation-name: left-panel-enter !important;
}

.left-panels .q-panel.scroll.q-transition--slide-up-leave-active,
.left-panels .q-panel.scroll.q-transition--slide-down-leave-active {
  animation-name: left-panel-leave !important;
}
"""
    )

    # Load external JavaScript module for panel resize functionality
    ui.add_head_html('<script src="/static/js/panel-resize.js" defer></script>')
