from __future__ import annotations

import logging
import sys
import os
import threading
import weakref

from nicegui import ui

_LEVEL_COLORS = {
    "TRACE": "\033[32m",  # green
    "DEBUG": "\033[36m",  # cyan
    "INFO": "\033[37m",  # light gray
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",  # red
    "CRITICAL": "\033[41m",  # red background
}
_RESET = "\033[0m"
_DIM = "\033[2m"

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

# Add Logger.trace if missing
if not hasattr(logging.Logger, "trace"):

    def _trace(self, msg, *args, **kwargs):
        if self.isEnabledFor(TRACE):
            self._log(TRACE, msg, args, **kwargs)

    logging.Logger.trace = _trace  # type: ignore[attr-defined]
    logging.TRACE = TRACE  # type: ignore[attr-defined]

# Environment guard to make hot-path trace zero-cost unless explicitly enabled
TRACE_ENABLED = str(os.getenv("PAROL_TRACE", "0")).lower() in ("1", "true", "yes", "on")


class AnsiColorFormatter(logging.Formatter):
    """Formatter that adds ANSI colors and a compact timestamp."""

    def __init__(self, colored: bool = True) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"
        )
        self.colored = colored and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        if not self.colored:
            return base
        # Colorize level name and dim the timestamp
        level = record.levelname.upper()
        color = _LEVEL_COLORS.get(level, "")
        # Expect format "HH:MM:SS LEVEL logger: msg"
        try:
            ts, rest = base.split(" ", 1)
            if color:
                rest = rest.replace(level, f"{color}{level}{_RESET}", 1)
            return f"{_DIM}{ts}{_RESET} {rest}"
        except Exception:
            return base


# ---- NiceGUI UI log handler ----

_ui_log_targets: set[weakref.ref] = set()
_ui_lock = threading.Lock()


class NiceGuiLogHandler(logging.Handler):
    """Push log records into one or more NiceGUI ui.log widgets."""

    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        # Basic, non-colored format for UI (timestamp + level + message)
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        if not _ui_log_targets:
            return
        msg = self.format(record)
        stale: list[weakref.ref] = []
        with _ui_lock:
            for ref in list(_ui_log_targets):
                widget = ref()
                if widget is None:
                    stale.append(ref)
                    continue
                try:
                    widget.push(msg)
                except Exception:
                    # If widget is gone or not available, mark as stale
                    stale.append(ref)
            for ref in stale:
                _ui_log_targets.discard(ref)


def attach_ui_log(log_widget) -> None:
    """Register a ui.log widget as a sink for log records."""
    if ui is None:
        return
    try:
        ref = weakref.ref(log_widget)
    except TypeError:
        return
    with _ui_lock:
        _ui_log_targets.add(ref)


def detach_ui_log(log_widget) -> None:
    """Unregister a ui.log widget."""
    try:
        ref = weakref.ref(log_widget)
    except TypeError:
        return
    with _ui_lock:
        _ui_log_targets.discard(ref)


def _have_console_handler(logger: logging.Logger) -> bool:
    return any(isinstance(h, logging.StreamHandler) for h in logger.handlers)


def _have_ui_handler(logger: logging.Logger) -> bool:
    return any(isinstance(h, NiceGuiLogHandler) for h in logger.handlers)


def configure_logging(
    level: int = logging.INFO, use_color: bool = True, add_ui_handler: bool = True
) -> logging.Logger:
    """
    Configure root logger with:
      - ANSI-colored console handler (stderr) with timestamps and levels
      - Optional NiceGUI UI log handler (messages mirrored to web log)
    Idempotent across multiple calls.
    """
    logger = logging.getLogger()
    logger.setLevel(level)

    if not _have_console_handler(logger):
        console = logging.StreamHandler(stream=sys.stderr)
        console.setLevel(level)
        console.setFormatter(AnsiColorFormatter(colored=use_color))
        logger.addHandler(console)

    if add_ui_handler and not _have_ui_handler(logger):
        ui_handler = NiceGuiLogHandler(level=level)
        logger.addHandler(ui_handler)

    return logger
