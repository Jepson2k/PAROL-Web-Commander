"""Program editor component with script execution and command palette."""

import asyncio
import contextlib
import inspect
import logging
import re
import time
import uuid
from typing import Any, Callable

import numpy as np
from nicegui import ui, context, Client
from waldo_commander.common.theme import get_theme
from waldo_commander.constants import REPO_ROOT, config
from waldo_commander.state import (
    robot_state,
    simulation_state,
    ui_state,
    EditorTab,
    editor_tabs_state,
    recording_state,
)
from waldo_commander.services.script_runner import (
    ScriptProcessHandle,
    run_script,
    create_default_config,
    stop_script,
)
from waldo_commander.services.path_visualizer import path_visualizer
from waldo_commander.services.motion_recorder import motion_recorder
from waldo_commander.services.stepping_client import GUIStepController
from waldo_commander.components.playback import PlaybackController
from waldo_commander.components.file_operations import FileOperationsMixin

logger = logging.getLogger(__name__)

# Marker prefix for editable target lines (e.g. "# TARGET:a1b2c3d4")
TARGET_MARKER = "# TARGET:"


def _get_home_joints_rad() -> list[float]:
    """Get home position in radians from the active robot."""
    return ui_state.active_robot.joints.home.rad.tolist()


# Cached robot commands (populated lazily, never invalidated — backend
# switching requires an app restart).
_robot_commands_cache: dict | None = None


# ---- Command Discovery Functions ----


_CATEGORY_RE = re.compile(r"^\s*Category:\s*(.+)", re.MULTILINE)
_EXAMPLE_RE = re.compile(r"^\s*Examples?:\s*$", re.MULTILINE)


def _parse_docstring_category(doc: str) -> str | None:
    """Extract ``Category: Foo`` from a Google-style docstring."""
    m = _CATEGORY_RE.search(doc)
    return m.group(1).strip() if m else None


def _parse_docstring_example(doc: str) -> str | None:
    """Extract the first indented line after an ``Example:`` section."""
    m = _EXAMPLE_RE.search(doc)
    if not m:
        return None
    rest = doc[m.end() :]
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def discover_robot_commands() -> dict:
    """Introspect the active backend's AsyncRobotClient to find all available commands (cached).

    Only methods whose docstrings contain both ``Category:`` and ``Example:``
    sections are included.  Methods without these sections are silently excluded.
    """
    global _robot_commands_cache
    if _robot_commands_cache is not None:
        return _robot_commands_cache

    try:
        client_cls = ui_state.active_robot.async_client_class
    except (AttributeError, RuntimeError, AssertionError):
        logger.warning("Could not get async_client_class for command discovery")
        _robot_commands_cache = {}
        return _robot_commands_cache

    commands = {}

    for name in dir(client_cls):
        if name.startswith("_"):
            continue
        attr = getattr(client_cls, name)
        if not callable(attr):
            continue

        doc = (attr.__doc__ or "").strip()
        category = _parse_docstring_category(doc)
        snippet = _parse_docstring_example(doc)

        if category is None or snippet is None:
            continue

        sig = inspect.signature(attr)
        first_line = doc.splitlines()[0] if doc else ""

        commands[name] = {
            "title": f"rbt.{name}(...)",
            "category": category,
            "snippet": snippet,
            "signature": str(sig),
            "docstring": first_line or "No description available",
        }

    _robot_commands_cache = commands
    return commands


def generate_completions_from_commands() -> list[dict]:
    """Generate CodeMirror completion items from discovered robot commands."""
    all_commands = discover_robot_commands()
    completions = []

    for name, cmd in all_commands.items():
        # Parse signature to create a useful apply text
        sig = cmd["signature"]
        # Remove 'self' from signature if present
        sig_clean = sig.replace("(self, ", "(").replace("(self)", "()")

        # Create the completion item
        completion = {
            "label": f"rbt.{name}",
            "detail": sig_clean,
            "info": cmd["docstring"],
            "apply": f"rbt.{name}",  # Just insert the method name, user will add args
            "type": "function",
        }
        completions.append(completion)

    return completions


class EditorPanel(FileOperationsMixin):
    """Program editor panel with script execution and command palette."""

    def __init__(self) -> None:
        """Initialize editor panel with state and UI references."""
        self._ui_client: Client | None = None  # NiceGUI client for JS execution
        # Program directory
        self.PROGRAM_DIR = (
            REPO_ROOT / "PAROL-commander-software" / "GUI" / "files" / "Programs"
        )
        if not self.PROGRAM_DIR.exists():
            self.PROGRAM_DIR = REPO_ROOT / "programs"
            self.PROGRAM_DIR.mkdir(parents=True, exist_ok=True)

        # Multi-tab management
        self.tabs_container: ui.tabs | None = None
        self.tab_panels_container: ui.tab_panels | None = None
        self._tab_widgets: dict[
            str, dict
        ] = {}  # tab_id -> {textarea, log, splitter, filename_input, ...}

        # Active tab's widgets (updated on tab switch for backward compatibility)
        self.program_filename_input: ui.input | None = None
        self.program_textarea: ui.codemirror | None = None
        self.program_log: ui.log | None = None
        self.run_btn: ui.button | None = None
        self.log_toggle_btn: ui.button | None = None
        self.record_btn: ui.button | None = None
        self._capture_btn: ui.button | None = None

        # Playback controller (owns bottom bar UI and playback logic)
        self.playback = PlaybackController(self)

        # Shared log area (below play bar)
        self._log_expanded: bool = False
        self.editor_splitter: ui.splitter | None = None
        self._splitter_value_when_expanded: float = (
            70.0  # Remember user's preferred split
        )

        # Script execution via subprocess
        self.script_handle: ScriptProcessHandle | None = None
        self.script_running: bool = False

        # Stepping control for GUI-controlled script execution
        self._step_session_id: str | None = None
        self._step_controller: GUIStepController | None = None
        self._event_watcher_task: asyncio.Task | None = None

        # Per-tab simulation tracking (for cancellation on tab close/switch)
        self._pending_simulations: dict[str, asyncio.Task] = {}

        # Drawer element reference
        self.drawer: ui.element | None = None

        # Debounce timer for auto-simulation on code change.
        # The timer reference is kept alive while the callback runs so that
        # cancel(with_current_invocation=True) can abort a running simulation.
        self._simulation_debounce_timer: ui.timer | None = None
        self._debounce_delay: float = 1.0  # seconds of idle before running simulation

        # Debounce for tab-switch path rendering
        self._tab_switch_render_task: asyncio.Task | None = None

        # Recording notification
        self._recording_notification: ui.notification | None = None

        # Tooltip references (to update text without recreating)
        self._record_btn_tooltip: ui.tooltip | None = None
        self._log_toggle_btn_tooltip: ui.tooltip | None = None

    def _default_python_snippet(self) -> str:
        """Generate the initial pre-filled Python code with inlined controller host/port."""
        backend = ui_state.active_robot.backend_package
        return f"""import time
from {backend} import RobotClient

rbt = RobotClient(host={config.controller_host!r}, port={config.controller_port})

print("Moving to home position...")
rbt.home()

status = rbt.status()
print(f"Robot status: {{status}}")
"""

    def _is_default_script(self, content: str) -> bool:
        """Check if content matches the default script template.

        Used to skip simulation for the default script since it just homes
        the robot (final position = home position).
        """
        if not content:
            return False
        default = self._default_python_snippet()

        # Normalize both for comparison (strip whitespace)
        def normalize(s: str) -> str:
            return "".join(s.split())

        return normalize(content) == normalize(default)

    def _insert_python_snippet(self, key: str) -> str:
        """Get Python code snippet for the given key."""
        # Non-robot utility snippets
        utility_snippets = {
            "delay": "time.sleep(1.0)",
            "comment": "# Add your robot commands here",
        }
        if key in utility_snippets:
            return utility_snippets[key]

        # Look up auto-discovered snippet from backend docstrings
        all_commands = discover_robot_commands()
        if key in all_commands:
            return all_commands[key]["snippet"]

        return f"rbt.{key}(...)"

    def _generate_snippet(self, method_name: str, use_current_position: bool) -> str:
        """Generate Python snippet with optional current position pre-fill."""
        speed = max(0.01, min(1.0, ui_state.jog_speed / 100.0))
        accel = max(0.01, min(1.0, ui_state.jog_accel / 100.0))

        # Motion commands that can use current position
        if use_current_position:
            if method_name == "move_j":
                angles = list(robot_state.angles.deg)
                return f"rbt.move_j({angles}, speed={speed}, accel={accel})"
            elif method_name == "move_l":
                x, y, z = robot_state.x, robot_state.y, robot_state.z
                rx, ry, rz = robot_state.rx, robot_state.ry, robot_state.rz
                return f"rbt.move_l([{x:.3f}, {y:.3f}, {z:.3f}, {rx:.3f}, {ry:.3f}, {rz:.3f}], speed={speed}, accel={accel})"

        # Generic snippets - delegate to existing method
        return self._insert_python_snippet(method_name)

    def _insert_command(self, method_name: str, use_current_position: bool) -> None:
        """Generate and insert command snippet into editor."""
        if self.program_textarea:
            snippet = self._generate_snippet(method_name, use_current_position)
            val = self.program_textarea.value
            if val and not val.endswith("\n"):
                val += "\n"
            self.program_textarea.value = val + snippet + "\n"
            logger.info("Added Python snippet: %s", snippet)

    def sync_code_from_target(
        self,
        target_id: str,
        pose: list[float],
        *,
        move_type: str | None = None,
        joint_angles_deg: list[float] | None = None,
    ) -> None:
        """Update the program code with the new pose for a specific target.

        Uses marker-based lookup (# TARGET:uuid) instead of line numbers for stability.

        Note: pose is in scene units (meters for position, degrees for rotation).
        Code uses user units (mm for position, degrees for rotation).

        If move_type is provided (e.g. "joints"), the move command is also
        converted (move_l→move_j or vice versa). joint_angles_deg must be
        provided when converting to move_j.
        """
        if not self.program_textarea:
            return

        # Check if codemirror is properly initialized
        try:
            current_value = self.program_textarea.value
            if current_value is None:
                logger.debug("Sync skipped: codemirror value is None")
                return
        except (AttributeError, RuntimeError) as e:
            logger.debug("Sync skipped: codemirror not ready - %s", e)
            return

        content = current_value
        lines = content.splitlines()

        # Find line with TARGET:target_id marker (marker-based lookup)
        target_marker = f"{TARGET_MARKER}{target_id}"
        found_line_idx = None

        for i, line in enumerate(lines):
            if target_marker in line:
                found_line_idx = i
                break

        if found_line_idx is None:
            logger.warning("Sync failed: Target marker %s not found in code", target_id)
            return

        line = lines[found_line_idx]

        # Replace the coordinate list in the line
        # Match a list of numbers: [...]
        match = re.search(r"(\[[\d\.\,\-\s]+\])", line)

        if match:
            # Convert move type if requested (e.g. move_l → move_j)
            if move_type == "joints" and joint_angles_deg is not None:
                new_values_str = (
                    "[" + ", ".join(f"{v:.3f}" for v in joint_angles_deg) + "]"
                )
                new_line = line[: match.start()] + new_values_str + line[match.end() :]
                new_line = new_line.replace("rbt.move_l(", "rbt.move_j(")
                new_line = new_line.replace("rbt.move_c(", "rbt.move_j(")
            else:
                # Convert from scene units (meters) to user units (mm) for position
                pose_mm = [
                    pose[0] * 1000.0 if len(pose) > 0 else 0.0,
                    pose[1] * 1000.0 if len(pose) > 1 else 0.0,
                    pose[2] * 1000.0 if len(pose) > 2 else 0.0,
                    pose[3] if len(pose) > 3 else 0.0,
                    pose[4] if len(pose) > 4 else 0.0,
                    pose[5] if len(pose) > 5 else 0.0,
                ]
                new_values_str = "[" + ", ".join(f"{v:.3f}" for v in pose_mm) + "]"
                new_line = line[: match.start()] + new_values_str + line[match.end() :]

            lines[found_line_idx] = new_line
            self.program_textarea.value = "\n".join(lines)
            logger.info("Synced code for target %s: %s", target_id, new_values_str)
        else:
            logger.warning(
                "Sync failed: Could not find coordinate list in line: %s", line
            )

    def delete_target_code(self, target_id: str) -> None:
        """Delete the code line corresponding to the target and re-simulate.

        Uses marker-based lookup (# TARGET:uuid) to find and remove the line.
        """
        if not self.program_textarea:
            return

        content = self.program_textarea.value or ""
        lines = content.splitlines()

        # Find and remove line with TARGET:target_id marker
        target_marker = f"{TARGET_MARKER}{target_id}"
        new_lines = [line for line in lines if target_marker not in line]

        if len(new_lines) < len(lines):
            # Line was removed - update editor
            self.program_textarea.value = "\n".join(new_lines)
            logger.info("Deleted target %s from code", target_id)
            # Re-simulation will trigger automatically via debounced on_change
        else:
            logger.warning("Target %s marker not found for deletion", target_id)

    def add_target_code(self, pose: list[float], move_type: str) -> str | None:
        """Add target code to the editor and return the marker ID.

        Args:
            pose: [x, y, z, rx, ry, rz] position and orientation
            move_type: Type of movement ("pose", "cartesian", "joints")

        Returns:
            Marker ID (uuid string) for the created target, or None on failure
        """
        if not self.program_textarea:
            return None

        # Generate unique marker ID
        marker_id = uuid.uuid4().hex[:8]

        speed = max(0.01, min(1.0, ui_state.jog_speed / 100.0))
        accel = max(0.01, min(1.0, ui_state.jog_accel / 100.0))

        pose_str = "[" + ", ".join(f"{v:.3f}" for v in pose) + "]"

        if move_type == "joints":
            code_line = f"rbt.move_j({pose_str}, speed={speed}, accel={accel})  {TARGET_MARKER}{marker_id}"
        else:
            code_line = f"rbt.move_l({pose_str}, speed={speed}, accel={accel})  {TARGET_MARKER}{marker_id}"

        content = self.program_textarea.value or ""

        # Count lines before adding
        lines_before = len(content.splitlines()) if content else 0

        # Ensure content ends with newline
        if content and not content.endswith("\n"):
            content += "\n"

        # Append new code (will trigger debounced simulation)
        new_content = content + code_line + "\n"
        self.program_textarea.value = new_content

        # Flash the newly added line
        new_line_number = lines_before + 1
        self.flash_editor_lines([new_line_number])

        logger.info("Added target code with marker %s: %s", marker_id, code_line)
        return marker_id

    def add_joint_target_code(self, joint_angles: list[float]) -> str | None:
        """Add joint target code to the editor and return the marker ID.

        Args:
            joint_angles: [j1, j2, j3, j4, j5, j6] joint angles in degrees

        Returns:
            Marker ID (uuid string) for the created target, or None on failure
        """
        return self.add_target_code(joint_angles, move_type="joints")

    def flash_editor_lines(self, line_numbers: list[int]) -> None:
        """Flash specific lines in the CodeMirror editor to highlight newly added content.

        Args:
            line_numbers: List of 1-indexed line numbers to flash
        """
        if not self.program_textarea or not line_numbers:
            return

        # Check if editor panel is visible (not collapsed)
        if self._is_editor_panel_visible():
            # Use NiceGUI CodeMirror's highlight_lines method
            # Auto-removal is handled by the decorations system
            self.program_textarea.highlight_lines(
                line_numbers,
                css_class="cm-line-flash",
                duration_ms=1500,
            )
        else:
            # Flash the editor tab instead
            self._flash_editor_tab()

    def _flash_editor_tab(self) -> None:
        """Flash the editor tab to indicate new content when panel is collapsed."""
        # Find the editor tab element and add flash class
        js_code = """
        (function() {
            // Find the program tab - look for tab with the code icon
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
            # No client context (called from background task) - use stored client
            if self._ui_client:
                self._ui_client.run_javascript(js_code)
            else:
                logger.debug("Cannot flash editor tab: no client available")

    def _is_editor_panel_visible(self) -> bool:
        """Check if the editor panel is currently visible (not collapsed)."""
        return ui_state.program_panel_visible

    def _build_command_menu(self) -> None:
        """Build command palette as a dropdown menu with nested submenus."""
        # Discover all commands dynamically
        all_commands = discover_robot_commands()

        # Group by category
        categories: dict[str, list[dict[str, Any]]] = {}
        for key, cmd in all_commands.items():
            cat = cmd["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append({"key": key, **cmd})

        # Build menu structure with nested submenus (following NiceGUI docs pattern)
        with ui.menu():
            for category_name, commands in sorted(categories.items()):
                # Category as submenu parent - must disable auto_close to keep open while navigating
                with ui.menu_item(category_name, auto_close=False).classes(
                    "text-sm font-medium"
                ):
                    # Arrow indicator on the right side
                    with ui.item_section().props("side"):
                        ui.icon("keyboard_arrow_right")
                    # Nested submenu with auto-close
                    with (
                        ui.menu()
                        .props('anchor="top end" self="top start" auto-close')
                        .classes("max-h-80 overflow-y-auto")
                    ):
                        for cmd in sorted(commands, key=lambda c: c["title"]):
                            # Command menu item
                            item = ui.menu_item(
                                cmd["title"],
                                on_click=lambda e, k=cmd["key"]: self._insert_command(
                                    k, True
                                ),
                            ).classes("text-sm")

                            # Add tooltip
                            with item:
                                tooltip_text = f"{cmd['signature']}"
                                if cmd["docstring"]:
                                    tooltip_text += f"\n\n{cmd['docstring']}"
                                ui.tooltip(tooltip_text).classes("text-xs").style(
                                    "max-width: 300px; white-space: pre-wrap;"
                                )

    async def _toggle_run_script(self) -> None:
        """Toggle start/stop script."""
        if self.script_running:
            await self._stop_script_process()
        else:
            await self._start_script_process()

    async def _start_script_process(self) -> None:
        """Save current editor content and start it as a Python subprocess."""
        self.playback.stop_playback()

        if self.script_running:
            ui.notify("Script already running", color="warning")
            return

        try:
            # Get filename, default to program.py if empty
            filename = (
                self.program_filename_input.value.strip()
                if self.program_filename_input
                else ""
            ) or "program.py"

            # Ensure .py extension
            if not filename.endswith(".py"):
                filename += ".py"

            # Save script content to file
            content = self.program_textarea.value if self.program_textarea else ""
            script_path = self.PROGRAM_DIR / filename
            script_path.write_text(content, encoding="utf-8")

            # Update filename input
            if self.program_filename_input:
                self.program_filename_input.value = filename

            # Clear program log
            if self.program_log:
                self.program_log.clear()

            script_config = create_default_config(str(script_path), str(REPO_ROOT))

            # Capture UI client context for the callbacks
            ui_client = context.client

            # Start the script process with log callbacks directed to program_log
            def on_stdout(line: str):
                with ui_client:
                    if self.program_log:
                        self.program_log.push(line)

            def on_stderr(line: str):
                with ui_client:
                    if self.program_log:
                        self.program_log.push(f"[ERR] {line}")

            # Initialize stepping controller with unique session ID
            self._step_session_id = uuid.uuid4().hex[:8]
            self._step_controller = GUIStepController(self._step_session_id)
            self._step_controller.initialize()

            self.script_handle = await run_script(
                script_config, on_stdout, on_stderr, session_id=self._step_session_id
            )
            self.script_running = True

            # Start in playing mode (not paused) so user doesn't need to press play twice
            simulation_state.is_playing = True
            self._step_controller.signal_play()
            self.playback.on_script_start()

            # Auto-expand log
            self._expand_log()

            # Capture UI client context BEFORE creating background task
            ui_client = context.client

            # Launch event watcher task to update visualization as commands complete
            self._event_watcher_task = asyncio.create_task(
                self._watch_script_events(ui_client)
            )

            # Launch monitor task to reset state when script finishes
            h = self.script_handle  # capture
            asyncio.create_task(self._monitor_script_completion(h, filename, ui_client))

            ui.notify(f"Started script: {filename}", color="positive")
            logger.info("Started script: %s", filename)

        except Exception as e:
            ui.notify(f"Failed to start script: {e}", color="negative")
            logger.error("Failed to start script: %s", e)
            self.script_running = False
            self._step_session_id = None
            if self._step_controller:
                self._step_controller.cleanup()
                self._step_controller = None
            simulation_state.is_playing = False
            self.playback._update_play_button()

    async def _watch_script_events(self, ui_client: Any) -> None:
        """Poll for script events and update visualization.

        Args:
            ui_client: The NiceGUI client context for UI updates
        """
        try:
            while self.script_running and self._step_controller:
                # Poll for new events
                events = self._step_controller.poll_events()

                for event in events:
                    event_type = event.get("event")
                    method = event.get("method", "")
                    step = event.get("step", 0)

                    if event_type == "start":
                        self.playback.on_script_step_start(step, ui_client)

                    elif event_type == "complete":
                        self.playback.on_script_step_complete(step, ui_client)
                        logger.debug(
                            "Script event: %s completed (step %d)", method, step
                        )

                # Poll interval - 50ms for responsive updates
                await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            logger.debug("Event watcher task cancelled")
        except Exception as e:
            logger.error("Error in event watcher: %s", e)

    async def _monitor_script_completion(
        self, handle: ScriptProcessHandle, filename: str, ui_client: Any
    ) -> None:
        """Monitor script subprocess completion and reset state when it finishes.

        Args:
            handle: The script process handle to monitor
            filename: Name of the script file for logging
            ui_client: The NiceGUI client context (must be captured before task creation)
        """

        try:
            rc = await handle["proc"].wait()
            # Let stream reader tasks finish
            for t in (handle["stdout_task"], handle["stderr_task"]):
                with contextlib.suppress(Exception):
                    await t
            # Only reset state if this handle is still the active one
            if self.script_handle is handle:
                with ui_client:
                    self._reset_script_state(handle, ui_client)
                    logger.info("Script %s finished with code %s", filename, rc)
        except Exception as e:
            logger.error("Error monitoring script process: %s", e)
            with ui_client:
                if self.script_handle is handle:
                    self._reset_script_state(handle, ui_client)

    def _reset_script_state(
        self, handle: ScriptProcessHandle, ui_client: Client
    ) -> None:
        """Reset all script-related state after a script finishes or errors."""
        self.script_handle = None
        self.script_running = False
        simulation_state.is_playing = False
        simulation_state.sim_pose_override = False
        self.playback.on_script_stop(ui_client)
        self._cleanup_stepping()

    def cleanup(self) -> None:
        """Remove listeners registered by this panel."""
        self.playback.cleanup()

    def _cleanup_stepping(self) -> None:
        """Clean up stepping controller and event watcher."""
        # Cancel event watcher task
        if self._event_watcher_task and not self._event_watcher_task.done():
            self._event_watcher_task.cancel()
        self._event_watcher_task = None

        # Clean up IPC files
        if self._step_controller:
            self._step_controller.cleanup()
            self._step_controller = None
        self._step_session_id = None

        # Clear executing line highlight
        self.clear_executing_line_highlight()

    async def _stop_script_process(self) -> None:
        """Stop the running script process."""
        if not self.script_running or not self.script_handle:
            ui.notify("No script running", color="warning")
            return

        try:
            handle = self.script_handle  # capture
            # Clear UI state up-front; monitor will see this and stay silent
            self.script_handle = None
            self.script_running = False
            simulation_state.is_playing = False
            self.playback._update_play_button()

            # Clean up stepping controller
            self._cleanup_stepping()

            if handle:
                await stop_script(handle)

            ui.notify("Script stopped", color="warning")
            logger.info("Script stopped by user")

        except Exception as e:
            ui.notify(f"Error stopping script: {e}", color="negative")
            logger.error("Error stopping script: %s", e)
            # State already cleared above

    async def _run_simulation(self, tab_id: str | None = None) -> str | None:
        """Run the simulation for the current script.

        Args:
            tab_id: Optional tab ID to run simulation for. If None, uses active tab.

        Returns:
            Error message if simulation failed, None otherwise.
        """
        # Get tab_id if not provided
        if tab_id is None:
            tab_id = editor_tabs_state.active_tab_id

        content = self.program_textarea.value if self.program_textarea else ""
        if not content:
            return None

        # Show loading indicator during simulation
        loading = self.playback.sim_loading_progress
        if loading:
            loading.visible = True
        try:
            error = await path_visualizer.update_path_visualization(
                content, tab_id=tab_id
            )
        finally:
            if loading:
                loading.visible = False

        # Invalidate timeline so it gets rebuilt from new segments
        self.playback.invalidate_timeline()
        simulation_state.sim_playback_time = 0.0

        # Snapshot robot position used for this simulation (per-tab)
        tab = editor_tabs_state.find_tab_by_id(tab_id) if tab_id else None
        if tab and error is None:
            n = ui_state.active_robot.joints.count
            tab.last_sim_joints_deg = robot_state.angles.deg[:n].copy()

        # Update scrub bar segments to match the new paths
        self.playback.update_scrub_segments()

        # Apply timing warning decorations for infeasible durations
        self._apply_timing_decorations()

        # Show error in program log if any
        if error and self.program_log:
            self.program_log.push(f"[SIM ERROR] {error}")

        # Annotate any lines that generated targets but don't have UUID markers
        self._annotate_unmarked_targets()

        return error

    def _annotate_unmarked_targets(self) -> None:
        """Add UUID markers to lines that generated targets but don't have them.

        After simulation, targets may have auto-generated IDs (auto_{line_no})
        for lines with literal move commands but no # TARGET:uuid marker.
        This method adds proper UUID markers to enable interactive editing.
        """
        if not self.program_textarea:
            return

        content = self.program_textarea.value
        if not content:
            return

        # Find targets with auto-generated IDs
        auto_targets = [t for t in simulation_state.targets if t.id.startswith("auto_")]

        if not auto_targets:
            return

        lines = content.splitlines()
        modified = False

        for target in auto_targets:
            line_idx = target.line_number - 1  # Convert to 0-indexed
            if 0 <= line_idx < len(lines):
                line = lines[line_idx]

                # Skip if line already has a TARGET marker
                if TARGET_MARKER in line:
                    continue

                # Generate new UUID
                new_id = uuid.uuid4().hex[:8]

                # Add marker to end of line
                lines[line_idx] = f"{line}  {TARGET_MARKER}{new_id}"

                # Update target ID in simulation_state
                target.id = new_id

                # Update target ID in tab state too
                tab = editor_tabs_state.get_active_tab()
                if tab:
                    for tab_target in tab.targets:
                        if tab_target.line_number == target.line_number:
                            tab_target.id = new_id
                            break

                modified = True
                logger.info(
                    "Added TARGET marker %s to line %d", new_id, target.line_number
                )

        if modified:
            # Update editor content (will trigger debounced simulation,
            # but next simulation will find the markers and not generate auto_ IDs)
            self.program_textarea.value = "\n".join(lines)

    def schedule_debounced_simulation(self, tab_id: str | None = None) -> None:
        """Schedule a debounced simulation run when code changes.

        Cancels any pending *or running* simulation and schedules a new one after
        the debounce delay.  ``cancel(with_current_invocation=True)`` aborts both the
        debounce sleep and an in-progress simulation subprocess, so edits never
        pile up stale simulations behind the simulation lock.

        Args:
            tab_id: The tab to run simulation for. If None, uses active tab.
        """
        # Use active tab if not specified
        if tab_id is None:
            tab_id = editor_tabs_state.active_tab_id
        if not tab_id:
            return

        # Cancel any pending debounce wait *and* any running simulation callback
        if self._simulation_debounce_timer is not None:
            logger.debug("DEBOUNCE: Cancelling pending/running simulation")
            self._simulation_debounce_timer.cancel(with_current_invocation=True)
            self._simulation_debounce_timer = None

        # Check for default script optimization
        tab = editor_tabs_state.find_tab_by_id(tab_id)
        if tab and self._is_default_script(tab.content):
            # Default script ends at home position - skip simulation
            tab.final_joints_rad = list(_get_home_joints_rad())
            tab.path_segments = []
            tab.targets = []
            tab.tool_actions = []
            # Update global state if active
            if tab_id == editor_tabs_state.active_tab_id:
                simulation_state.path_segments = []
                simulation_state.targets = []
                simulation_state.tool_actions = []
                simulation_state.total_steps = 0
                try:
                    ui_client = self._ui_client or context.client
                    with ui_client:
                        simulation_state.notify_changed()
                except RuntimeError:
                    simulation_state.notify_changed()
                self.playback.update_scrub_segments()
            return

        async def run_simulation_quietly():
            """Run simulation without notifications (silent auto-update)."""
            try:
                logger.debug("DEBOUNCE: Starting simulation...")
                await self._run_simulation(tab_id=tab_id)
                logger.debug("DEBOUNCE: Simulation completed successfully")
            except asyncio.CancelledError:
                logger.debug("DEBOUNCE: Simulation cancelled by newer edit")
            except Exception as e:
                logger.error("Auto-simulation failed: %s", e, exc_info=True)
                ui.notify(f"Simulation error: {e}", color="negative", timeout=3000)
            finally:
                # Only clear if we are still the active timer — a newer
                # scheduling may have already replaced the reference.
                if self._simulation_debounce_timer is my_timer:
                    self._simulation_debounce_timer = None

        # Schedule new simulation after debounce delay
        logger.debug(
            "DEBOUNCE: Scheduling new timer with delay=%.3fs", self._debounce_delay
        )
        my_timer = ui.timer(self._debounce_delay, run_simulation_quietly, once=True)
        self._simulation_debounce_timer = my_timer

    def _check_position_changed(self) -> None:
        """Periodically check if robot position changed and re-run path preview."""
        # Skip if script running, editing, scrubbing/playing, or sim already pending
        if (
            self.script_running
            or robot_state.editing_mode
            or self._simulation_debounce_timer is not None
            or simulation_state.sim_pose_override
            or simulation_state.sim_playback_active
        ):
            return

        # Read from active tab's per-tab snapshot
        active_tab = editor_tabs_state.get_active_tab()
        if not active_tab or active_tab.last_sim_joints_deg is None:
            return

        # Skip if no active script content
        if not self.program_textarea or not self.program_textarea.value:
            return

        current_deg = robot_state.angles.deg[: ui_state.active_robot.joints.count]
        if np.max(np.abs(current_deg - active_tab.last_sim_joints_deg)) > 0.5:
            self.schedule_debounced_simulation()

    def _toggle_recording(self) -> None:
        """Toggle motion recording on/off."""
        motion_recorder.toggle_recording()
        # Update button visual
        if recording_state.is_recording:
            if self.record_btn:
                self.record_btn.props("color=warning")
            if self._record_btn_tooltip:
                self._record_btn_tooltip.text = "Stop Recording"
            # Disable playback controls during recording
            self.playback.set_enabled(False)
            # Show recording notification at top of screen
            try:
                ui_client = self._ui_client or context.client
                with ui_client:
                    self._recording_notification = ui.notification(
                        message="Recording",
                        type="negative",
                        icon="fiber_manual_record",
                        position="top",
                        timeout=0,  # Persistent until dismissed
                        close_button=False,
                        classes="recording-notification",
                    )
            except RuntimeError:
                pass  # No client context available
        else:
            if self.record_btn:
                self.record_btn.props("color=negative")
            if self._record_btn_tooltip:
                self._record_btn_tooltip.text = "Start Recording"
            # Re-enable playback controls
            self.playback.set_enabled(True)
            # Dismiss recording notification
            if self._recording_notification is not None:
                try:
                    client = self._ui_client or context.client
                    with client:
                        self._recording_notification.dismiss()
                except RuntimeError:
                    pass  # No client context available
                self._recording_notification = None

    def _toggle_log(self) -> None:
        """Toggle shared log panel visibility via splitter position."""
        if self._log_expanded:
            self._collapse_log()
        else:
            self._expand_log()

    def _expand_log(self) -> None:
        """Expand the shared log panel by adjusting splitter."""
        self._log_expanded = True
        if self.editor_splitter:
            self.editor_splitter.set_value(self._splitter_value_when_expanded)
        if self.log_toggle_btn:
            self.log_toggle_btn.props("icon=expand_less")
            if self._log_toggle_btn_tooltip:
                self._log_toggle_btn_tooltip.text = "Hide Output"

    def _collapse_log(self) -> None:
        """Collapse the shared log panel by adjusting splitter."""
        self._log_expanded = False
        if self.editor_splitter:
            self.editor_splitter.set_value(94)  # 94% to editor (collapsed)
        if self.log_toggle_btn:
            self.log_toggle_btn.props("icon=expand_more")
            if self._log_toggle_btn_tooltip:
                self._log_toggle_btn_tooltip.text = "Show Output"

    def _on_splitter_change(self, e) -> None:
        """Handle splitter drag changes to update log expanded state."""
        value = e.value
        if value is None:
            return

        # If user drags to near-bottom (>90%), treat as collapsed
        if value > 90:
            self._log_expanded = False
            if self.log_toggle_btn:
                self.log_toggle_btn.props("icon=expand_more")
                if self._log_toggle_btn_tooltip:
                    self._log_toggle_btn_tooltip.text = "Show Output"
        else:
            self._log_expanded = True
            self._splitter_value_when_expanded = value  # Remember user's preference
            if self.log_toggle_btn:
                self.log_toggle_btn.props("icon=expand_less")
                if self._log_toggle_btn_tooltip:
                    self._log_toggle_btn_tooltip.text = "Hide Output"

    # ---- Tab Management Methods ----

    def _new_tab(
        self, filename: str = "untitled.py", content: str | None = None
    ) -> EditorTab:
        """Create a new tab and switch to it."""
        tab = EditorTab(
            id=uuid.uuid4().hex[:8],
            filename=filename,
            file_path=None,
            content=content if content is not None else self._default_python_snippet(),
            saved_content=content
            if content is not None
            else self._default_python_snippet(),
            output_log=[],
            path_segments=[],
            targets=[],
            created_at=time.time(),
        )

        editor_tabs_state.add_tab(tab)
        self._create_tab_widget(tab)
        self._create_tab_panel(tab)
        self._switch_to_tab(tab.id)

        # Trigger simulation at tab creation (with default script optimization)
        if self._is_default_script(tab.content):
            # Default script ends at home position - set directly, skip simulation
            tab.final_joints_rad = list(_get_home_joints_rad())
            tab.path_segments = []
            tab.targets = []
        elif tab.content.strip():
            self.schedule_debounced_simulation(tab_id=tab.id)

        return tab

    def _close_tab(self, tab: EditorTab) -> None:
        """Close a tab, prompting to save if dirty.

        Uses deferred execution via ui.timer to avoid modifying UI
        during NiceGUI's event listener iteration.
        """

        def do_close():
            if tab.is_dirty:
                self._show_save_confirmation(tab)
            else:
                self._do_close_tab(tab)

        # Defer to avoid "dictionary changed size during iteration" in tests
        ui.timer(0, do_close, once=True)

    def _show_save_confirmation(self, tab: EditorTab) -> None:
        """Show save confirmation dialog for dirty tab."""
        dlg = ui.dialog().classes("save-dialog")

        def dont_save():
            dlg.close()
            self._do_close_tab(tab)

        with dlg, ui.card().classes("overlay-card w-80"):
            ui.label(f"Save changes to {tab.filename}?").classes(
                "text-lg font-medium mb-2"
            )
            ui.label("Your changes will be lost if you don't save.").classes(
                "text-sm text-gray-500 mb-4"
            )
            with ui.row().classes("gap-2 justify-end w-full"):
                ui.button(
                    "Don't Save",
                    on_click=dont_save,
                ).props("flat color=negative")
                ui.button("Cancel", on_click=dlg.close).props("flat")
                ui.button(
                    "Save", on_click=lambda: self._save_tab_and_close(tab, dlg)
                ).props("color=primary")
        dlg.open()

    def _do_close_tab(self, tab: EditorTab) -> None:
        """Actually close the tab and clean up UI."""
        tab_id = tab.id

        # Cancel any pending simulation for this tab
        if tab_id in self._pending_simulations:
            self._pending_simulations[tab_id].cancel()
            del self._pending_simulations[tab_id]

        # Determine which tab to switch to BEFORE removing
        tabs = editor_tabs_state.tabs
        closed_idx = next((i for i, t in enumerate(tabs) if t.id == tab_id), -1)
        new_active_id = None

        if len(tabs) > 1:
            if closed_idx > 0:
                new_active_id = tabs[closed_idx - 1].id  # Previous tab
            else:
                new_active_id = tabs[1].id  # Next tab if closing first

        # Remove tab widget from tabs container
        if tab_id in self._tab_widgets:
            widgets = self._tab_widgets[tab_id]
            # Delete the tab widget element
            if "tab_element" in widgets and widgets["tab_element"]:
                widgets["tab_element"].delete()
            # Delete the panel element
            if "panel" in widgets and widgets["panel"]:
                widgets["panel"].delete()
            del self._tab_widgets[tab_id]

        # Remove from state
        editor_tabs_state.remove_tab(tab_id)

        # Create new tab if all tabs closed
        if not editor_tabs_state.tabs:
            self._new_tab()
        elif new_active_id:
            editor_tabs_state.active_tab_id = new_active_id
            self._switch_to_tab(new_active_id)

    def _switch_to_tab(self, tab_id: str) -> None:
        """Switch to a specific tab (blocked during recording/playback)."""

        # Block tab switching during recording or playback
        if recording_state.is_recording:
            ui.notify("Cannot switch tabs while recording", color="warning")
            # Reset UI to current active tab since the click already changed it visually
            if self.tabs_container and editor_tabs_state.active_tab_id:
                self.tabs_container.set_value(editor_tabs_state.active_tab_id)
            return
        if self.script_running and simulation_state.is_playing:
            ui.notify("Cannot switch tabs during script playback", color="warning")
            if self.tabs_container and editor_tabs_state.active_tab_id:
                self.tabs_container.set_value(editor_tabs_state.active_tab_id)
            return

        # Stop simulation playback on tab switch (non-blocking)
        self.playback.stop_playback()
        self.playback.invalidate_timeline()

        tab = editor_tabs_state.find_tab_by_id(tab_id)
        if not tab:
            return

        # Save current tab's simulation context and log content
        current_tab = editor_tabs_state.get_active_tab()
        if current_tab and current_tab.id != tab_id:
            self._save_simulation_context(current_tab)
            # Save current log content to tab
            # (log content is stored in tab.output_log by script runner callbacks)

        # Update active tab
        editor_tabs_state.active_tab_id = tab_id
        simulation_state.active_cursor_line = 0

        # Update tab panels value
        if self.tab_panels_container:
            self.tab_panels_container.set_value(tab_id)

        # Update tabs container value
        if self.tabs_container:
            self.tabs_container.set_value(tab_id)

        # Load this tab's simulation context
        self._load_simulation_context(tab)

        # Swap log content: load new tab's log entries into shared log
        if self.program_log:
            self.program_log.clear()
            for entry in tab.output_log:
                self.program_log.push(entry)

        # Update references for backward compatibility
        widgets = self._tab_widgets.get(tab_id, {})
        self.program_textarea = widgets.get("textarea")
        self.program_filename_input = widgets.get("filename_input")

    def _save_simulation_context(self, tab: EditorTab) -> None:
        """Save current simulation state to tab."""
        tab.path_segments = list(simulation_state.path_segments)
        tab.targets = list(simulation_state.targets)
        tab.tool_actions = list(simulation_state.tool_actions)

    def _load_simulation_context(self, tab: EditorTab) -> None:
        """Load tab's simulation state into global simulation_state.

        Updates simulation_state synchronously so _save_simulation_context on
        the *next* tab switch reads consistent data. Only defers the expensive
        path invalidation and re-render to an async task.
        """
        # Cancel previous tab-switch render if still pending
        if self._tab_switch_render_task is not None:
            self._tab_switch_render_task.cancel()

        # Update global state synchronously to avoid races with _save
        simulation_state.path_segments = list(tab.path_segments)
        simulation_state.targets = list(tab.targets)
        simulation_state.tool_actions = list(tab.tool_actions)
        simulation_state.current_step_index = 0
        simulation_state.total_steps = len(tab.path_segments)

        # Capture client context before creating task (asyncio.create_task
        # doesn't propagate NiceGUI context)
        try:
            client = context.client
        except RuntimeError:
            client = None

        async def _apply():
            try:
                await asyncio.sleep(0)  # yield so UI updates first
                if ui_state.urdf_scene:
                    ui_state.urdf_scene.invalidate_paths()
                if client is not None:
                    with client:
                        self.playback.update_scrub_segments()
                simulation_state.notify_changed()
            finally:
                if self._tab_switch_render_task is task:
                    self._tab_switch_render_task = None

        task = asyncio.create_task(_apply())
        self._tab_switch_render_task = task

    def _create_tab_widget(self, tab: EditorTab) -> ui.tab | None:
        """Create a single tab widget with filename input, save button, close button."""
        if not self.tabs_container:
            return None

        with self.tabs_container:
            tab_element = ui.tab(name=tab.id, label="").classes("editor-tab")
            tab_element.mark(f"editor-tab-{tab.id}")
            with tab_element:
                with ui.row().classes("items-center gap-1 no-wrap"):
                    # Dirty indicator (orange dot)
                    dirty_dot = (
                        ui.icon("fiber_manual_record", size="xs")
                        .classes("text-amber-500")
                        .style("font-size: 8px;")
                    )
                    # Bind visibility to dirty state - update on content change
                    dirty_dot.bind_visibility_from(tab, "is_dirty", lambda d: d)

                    # Filename input (compact)
                    filename_input = (
                        ui.input(value=tab.filename)
                        .props("dense borderless")
                        .classes("text-sm w-28")
                        .on("change", lambda e, t=tab: setattr(t, "filename", e.args))
                    )
                    filename_input.mark(f"editor-tab-filename-{tab.id}")

                    # Close button
                    close_btn = (
                        ui.button(
                            icon="close", on_click=lambda _e, t=tab: self._close_tab(t)
                        )
                        .props("flat round dense size=xs")
                        .classes("text-white")
                        .tooltip("Close tab")
                    )
                    close_btn.mark(f"editor-tab-close-{tab.id}")

            # Store tab element reference
            if tab.id not in self._tab_widgets:
                self._tab_widgets[tab.id] = {}
            self._tab_widgets[tab.id]["tab_element"] = tab_element
            self._tab_widgets[tab.id]["filename_input"] = filename_input
            self._tab_widgets[tab.id]["dirty_dot"] = dirty_dot

        return tab_element

    def _create_tab_panel(self, tab: EditorTab) -> ui.tab_panel | None:
        """Create content panel for a tab (CodeMirror only, log is shared)."""
        if not self.tab_panels_container:
            return None

        with self.tab_panels_container:
            panel = (
                ui.tab_panel(name=tab.id)
                .classes("editor-tab-panel")
                .style("padding: 0; width: 100%; height: 100%;")
            )
            with panel:
                # Generate completions
                completions = generate_completions_from_commands()

                # CodeMirror editor - fill entire panel (uses its own internal scrolling)
                textarea = (
                    ui.codemirror(
                        value=tab.content,
                        language="Python",
                        line_wrapping=True,
                        on_change=lambda e, t=tab: self._on_tab_content_change(
                            t, e.value
                        ),
                        on_cursor_line=lambda e, t=tab: self._on_cursor_line(t, e),
                        custom_completions=completions,
                    )
                    .classes("w-full h-full")
                    .style("min-height: 100%;")
                )

                # Initialize theme
                try:
                    mode = get_theme()
                    effective = "light" if mode == "light" else "dark"
                    textarea.theme = "basicLight" if effective == "light" else "oneDark"
                except (KeyError, ValueError):
                    textarea.theme = "oneDark"

            # Store references
            self._tab_widgets[tab.id]["panel"] = panel
            self._tab_widgets[tab.id]["textarea"] = textarea

        return panel

    def _on_cursor_line(self, tab: EditorTab, e) -> None:
        """Handle cursor line change from CodeMirror."""
        if tab.id != editor_tabs_state.active_tab_id:
            return
        simulation_state.active_cursor_line = e.args.get("line", 0)
        if ui_state.urdf_scene and simulation_state.paths_visible:
            ui_state.urdf_scene.update_cursor_line_highlight()

    def _on_tab_content_change(self, tab: EditorTab, new_value: str) -> None:
        """Handle content change for a tab."""
        tab.content = new_value

        self._update_dirty_dot(tab)

        # Only run simulation for active tab
        if tab.id == editor_tabs_state.active_tab_id:
            self.schedule_debounced_simulation()

    # ---- Line highlighting  ----

    def highlight_executing_line(self, step_index: int) -> None:
        """Highlight the source line corresponding to the current step.

        Uses path_segments line_number to look up which line to highlight.
        Uses persistent decorations (not flash animations) that update with each step.

        Args:
            step_index: The current step index (0-indexed)
        """
        if not self.program_textarea:
            return

        # Look up line number from path_segments if available
        if simulation_state.path_segments and 0 <= step_index < len(
            simulation_state.path_segments
        ):
            segment = simulation_state.path_segments[step_index]
            line_number = segment.line_number
            if line_number > 0:
                # Use set_decorations for persistent highlight
                self.program_textarea.set_decorations(
                    [{"kind": "line", "line": line_number, "class": "cm-highlighted"}],
                    set_name="executing",
                )
                return

        # Clear highlight if no valid line found
        self.program_textarea.clear_decorations("executing")

    def clear_executing_line_highlight(self) -> None:
        """Clear the executing line highlight decoration."""
        if self.program_textarea:
            self.program_textarea.clear_decorations("executing")

    _DURATION_PARAM_RE = re.compile(r"duration\s*=\s*[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

    def _apply_timing_decorations(self) -> None:
        """Highlight the duration=XXX span and show min time for infeasible timing."""
        if not self.program_textarea:
            return
        source = self.program_textarea.value or ""
        lines = source.split("\n")
        # Pre-compute line start offsets (0-indexed char positions)
        line_starts: list[int] = []
        offset = 0
        for ln in lines:
            line_starts.append(offset)
            offset += len(ln) + 1  # +1 for '\n'

        decorations: list[dict] = []
        marked_lines: set[int] = set()
        for seg in simulation_state.path_segments:
            if seg.timing_feasible or seg.line_number <= 0:
                continue
            line_idx = seg.line_number - 1  # 0-indexed
            if line_idx >= len(lines):
                continue

            # Mark decoration on the "duration=XXX" span (once per line)
            if seg.line_number not in marked_lines:
                marked_lines.add(seg.line_number)
                line_text = lines[line_idx]
                line_start = line_starts[line_idx]
                m = self._DURATION_PARAM_RE.search(line_text)
                if m:
                    decorations.append(
                        {
                            "kind": "mark",
                            "from": line_start + m.start(),
                            "to": line_start + m.end(),
                            "class": "cm-timing-warning-mark",
                        }
                    )

            # Line decoration for the ::after annotation text
            if seg.estimated_duration is not None:
                decorations.append(
                    {
                        "kind": "line",
                        "line": seg.line_number,
                        "class": "cm-timing-warning",
                        "attributes": {
                            "data-timing": f"min: {seg.estimated_duration:.2f}s",
                        },
                    }
                )
        self.program_textarea.set_decorations(decorations, set_name="timing")

    def build(self, close_callback: Callable | None = None) -> None:
        """Build the program editor content with multi-tab support."""
        # Store NiceGUI client reference for JS execution from background tasks
        try:
            self._ui_client = ui.context.client
        except RuntimeError:
            pass  # No client context during build (shouldn't happen)

        # Periodic check: re-run path preview when robot position changes
        ui.timer(1.0, self._check_position_changed)

        # Main editor container
        with (
            ui.column()
            .classes("w-full h-full gap-0")
            .style("height: 100%; min-height: 0; padding-bottom: 16px;")
        ):
            # ---- Header Row (title + tabs + cmd + X) ----
            with (
                ui.row()
                .classes("w-full items-center gap-2 px-2")
                .style("height: 42px;")
            ):
                # Title
                ui.label("Program").classes("text-lg font-medium whitespace-nowrap")

                # Tabs area (horizontal scroll)
                with (
                    ui.scroll_area()
                    .classes("flex-1 no-wrap items-start editor-tabs-scroll")
                    .style("height: 42px;")
                ):
                    with ui.row().classes("items-center gap-0 flex-nowrap"):
                        # Tabs container
                        self.tabs_container = (
                            ui.tabs()
                            .props("dense inline-label")
                            .classes("editor-tabs")
                            .on(
                                "update:model-value",
                                lambda e: self._switch_to_tab(e.args),
                            )
                        )

                        # New tab button (last element in scrollable area)
                        new_tab_btn = (
                            ui.button(icon="add", on_click=lambda: self._new_tab())
                            .props("flat dense color=white")
                            .classes("ml-2")
                            .tooltip("New Tab")
                        )
                        new_tab_btn.mark("editor-new-tab-btn")

                # Open button
                open_btn = (
                    ui.button(icon="folder", on_click=self._show_open_dialog)
                    .props("flat dense color=white")
                    .tooltip("Open")
                )
                open_btn.mark("editor-open-btn")

                # Save button
                save_btn = (
                    ui.button(icon="save", on_click=self._show_save_dialog)
                    .props("flat dense color=white")
                    .tooltip("Save")
                )
                save_btn.mark("editor-save-btn")

                # Command palette menu
                commands_btn = (
                    ui.button(icon="library_add")
                    .props("flat dense color=white")
                    .tooltip("Insert Command")
                )
                commands_btn.mark("editor-commands-btn")
                with commands_btn:
                    self._build_command_menu()

                # X close button
                if close_callback:
                    ui.button(icon="close", on_click=close_callback).props(
                        "flat round dense color=white"
                    )

            # ---- Splitter: Editor (before) | Playbar (separator) | Log (after) ----
            # horizontal=True means vertical stacking (column layout)
            with (
                ui.splitter(
                    horizontal=True,
                    value=94,  # Start collapsed (94% to editor, leaves room for playbar)
                    limits=(50, 94),
                    on_change=self._on_splitter_change,
                )
                .classes("w-full flex-1 editor-splitter")
                .style("overflow: hidden;") as splitter
            ):
                self.editor_splitter = splitter

                # ---- Tab Panels Area (CodeMirror) in splitter.before ----
                with splitter.before:
                    self.tab_panels_container = (
                        ui.tab_panels(self.tabs_container)
                        .classes("w-full h-full")
                        .props("animated")
                        .style("padding: 0; overflow: hidden;")
                    )

                # ---- Playbar in splitter.separator (acts as handle) ----
                with splitter.separator:
                    self.playback.build_bar()
                    self.run_btn = self.playback._play_btn

                # ---- Shared Log Area in splitter.after ----
                with splitter.after:
                    self.program_log = (
                        ui.log(max_lines=1000)
                        .classes("w-full h-full whitespace-pre-wrap break-words")
                        .style("min-height: 0;")
                    )

        # Set up playback timers and listeners
        self.playback.setup_timers()

        # Restore tabs from existing state (page refresh) or create initial tab
        if editor_tabs_state.tabs:
            # Clear stale UI references from previous page load
            self._tab_widgets.clear()
            self._pending_simulations.clear()

            # Rebuild UI for each existing tab
            for tab in editor_tabs_state.tabs:
                self._create_tab_widget(tab)
                self._create_tab_panel(tab)

            # Activate the previously active tab (or first tab if none active).
            # Set references directly instead of calling _switch_to_tab() which
            # blocks during recording/playback — those guards are for user-initiated
            # switches, not page-load restoration.
            active_id = editor_tabs_state.active_tab_id or editor_tabs_state.tabs[0].id
            editor_tabs_state.active_tab_id = active_id
            if self.tab_panels_container:
                self.tab_panels_container.set_value(active_id)
            if self.tabs_container:
                self.tabs_container.set_value(active_id)
            widgets = self._tab_widgets.get(active_id, {})
            self.program_textarea = widgets.get("textarea")
            self.program_filename_input = widgets.get("filename_input")

            # Restore simulation state from active tab
            active_tab = editor_tabs_state.get_active_tab()
            if active_tab:
                self._load_simulation_context(active_tab)
        else:
            # No existing tabs - create initial tab
            self._new_tab()
