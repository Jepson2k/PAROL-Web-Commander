"""Global keybindings manager for PAROL Web Commander.

Provides centralized keyboard shortcut handling with:
- Automatic disabling when editor/input is focused
- Click vs hold behavior for jog keys (matching button behavior)
- Dynamic tooltip suffix generation
- Keybinding registry for help menu display
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Any

from nicegui import ui


# Click vs hold threshold (matches control.py)
CLICK_HOLD_THRESHOLD_S: float = 0.25


@dataclass
class Keybinding:
    """Definition of a keyboard shortcut."""

    key: str  # Key identifier (e.g., "h", " ", "Escape")
    display: str  # Display string for UI (e.g., "H", "Space", "Esc")
    description: str  # Human-readable description
    action: Callable  # Function to call when triggered
    category: str  # Category for help menu grouping
    requires_shift: bool = False
    requires_ctrl: bool = False
    requires_alt: bool = False
    holdable: bool = False  # If True, supports click vs hold behavior
    on_release: Callable | None = None  # Called on keyup for holdable keys
    enabled_check: Callable[[], bool] | None = None  # Dynamic enable check


class KeybindingsManager:
    """Manages global keyboard shortcuts."""

    def __init__(self) -> None:
        self._bindings: dict[str, Keybinding] = {}
        self._enabled: bool = True
        self._editor_focused: bool = False

        # Hold state tracking for holdable keys
        self._hold_start_times: dict[str, float] = {}
        self._hold_timers: dict[str, ui.timer] = {}
        self._holding_active: set[str] = set()
        self._keys_down: set[str] = set()  # Track currently pressed keys

    def register(self, binding: Keybinding) -> None:
        """Register a keybinding."""
        key_id = self._make_key_id(
            binding.key,
            binding.requires_shift,
            binding.requires_ctrl,
            binding.requires_alt,
        )
        self._bindings[key_id] = binding
        logging.debug("Registered keybinding: %s -> %s", key_id, binding.description)

    def unregister(
        self, key: str, shift: bool = False, ctrl: bool = False, alt: bool = False
    ) -> None:
        """Unregister a keybinding."""
        key_id = self._make_key_id(key, shift, ctrl, alt)
        self._bindings.pop(key_id, None)

    def _make_key_id(self, key: str, shift: bool, ctrl: bool, alt: bool) -> str:
        """Create unique identifier for key combination."""
        parts = []
        if ctrl:
            parts.append("Ctrl")
        if alt:
            parts.append("Alt")
        if shift:
            parts.append("Shift")
        parts.append(key.lower())
        return "+".join(parts)

    def _normalize_key(self, key: str) -> str:
        """Normalize key name for consistent matching."""
        # NiceGUI keyboard events use specific key names
        key = key.lower()
        # Space is reported as " " in some cases
        if key == " ":
            return " "
        return key

    def handle_key(self, e: Any) -> None:
        """Handle keyboard event from ui.keyboard."""
        if not self._enabled:
            return

        # Check if editor/input is focused
        if self._editor_focused:
            return

        key = self._normalize_key(e.key.name)
        is_keydown = e.action.keydown
        is_keyup = e.action.keyup

        # Build key ID with modifiers
        key_id = self._make_key_id(
            key,
            e.modifiers.shift,
            e.modifiers.ctrl,
            e.modifiers.alt,
        )

        binding = self._bindings.get(key_id)
        if not binding:
            return

        # Check dynamic enable condition
        if binding.enabled_check and not binding.enabled_check():
            return

        if binding.holdable:
            self._handle_holdable_key(key_id, binding, is_keydown, is_keyup)
        elif is_keydown:
            # Prevent repeat triggers for held keys
            if key_id in self._keys_down:
                return
            self._keys_down.add(key_id)
            self._execute_action(binding.action)
        elif is_keyup:
            self._keys_down.discard(key_id)

    def _handle_holdable_key(
        self, key_id: str, binding: Keybinding, is_keydown: bool, is_keyup: bool
    ) -> None:
        """Handle click vs hold behavior for holdable keys."""
        if is_keydown:
            # Ignore repeat keydown events
            if key_id in self._keys_down:
                return
            self._keys_down.add(key_id)

            # Cancel any existing timer
            old_timer = self._hold_timers.pop(key_id, None)
            if old_timer:
                old_timer.active = False

            # Record start time
            self._hold_start_times[key_id] = time.time()

            # Start timer for hold detection
            def start_hold():
                self._holding_active.add(key_id)
                self._hold_timers.pop(key_id, None)
                # Execute action for continuous jog start
                self._execute_action(binding.action, is_press=True)

            try:
                with ui.context.client:
                    self._hold_timers[key_id] = ui.timer(
                        CLICK_HOLD_THRESHOLD_S, start_hold, once=True
                    )
            except Exception:
                # If no client context, fall back to simple execution
                self._execute_action(binding.action, is_press=True)

        elif is_keyup:
            self._keys_down.discard(key_id)

            # Cancel timer if still running
            timer = self._hold_timers.pop(key_id, None)
            was_holding = key_id in self._holding_active
            self._holding_active.discard(key_id)
            self._hold_start_times.pop(key_id, None)

            if timer and timer.active:
                timer.active = False
                # Was a click (quick press) - execute single step action
                self._execute_action(binding.action, is_press=False, is_click=True)
            elif was_holding and binding.on_release:
                # Was a hold - execute release action
                self._execute_action(binding.on_release)

    def _execute_action(
        self, action: Callable, is_press: bool = True, is_click: bool = False
    ) -> None:
        """Execute a keybinding action, handling async if needed."""
        try:
            # Some actions accept is_press/is_click parameters
            import inspect

            sig = inspect.signature(action)
            params = sig.parameters

            kwargs = {}
            if "is_press" in params:
                kwargs["is_press"] = is_press
            if "is_click" in params:
                kwargs["is_click"] = is_click

            if kwargs:
                result = action(**kwargs)
            else:
                result = action()

            # Handle async actions
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
        except Exception as ex:
            logging.error("Keybinding action failed: %s", ex)

    def set_editor_focused(self, focused: bool) -> None:
        """Called from JS when editor/input focus changes."""
        self._editor_focused = focused
        logging.debug("Editor focused: %s", focused)

    def get_all_bindings(self) -> dict[str, list[Keybinding]]:
        """Get all bindings grouped by category for help menu."""
        categories: dict[str, list[Keybinding]] = {}
        for binding in self._bindings.values():
            if binding.category not in categories:
                categories[binding.category] = []
            categories[binding.category].append(binding)
        return categories

    def get_tooltip_suffix(self, key: str, shift: bool = False) -> str:
        """Get tooltip suffix for a keybinding (e.g., ' (H)' for home)."""
        key_id = self._make_key_id(key, shift, False, False)
        binding = self._bindings.get(key_id)
        if binding:
            display = binding.display
            if shift:
                display = f"Shift+{display}"
            return f" ({display})"
        return ""

    def get_display_for_key(
        self, key: str, shift: bool = False, ctrl: bool = False, alt: bool = False
    ) -> str | None:
        """Get display string for a registered keybinding."""
        key_id = self._make_key_id(key, shift, ctrl, alt)
        binding = self._bindings.get(key_id)
        if binding:
            parts = []
            if ctrl:
                parts.append("Ctrl")
            if alt:
                parts.append("Alt")
            if shift:
                parts.append("Shift")
            parts.append(binding.display)
            return "+".join(parts)
        return None


# Singleton
keybindings_manager = KeybindingsManager()
