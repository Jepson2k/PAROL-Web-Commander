"""Program editor component with script execution and command palette."""

import asyncio
import contextlib
import inspect
import logging
from typing import Any

from nicegui import ui, context

from parol_commander.common.theme import get_theme
from parol_commander.constants import REPO_ROOT, CONTROLLER_HOST, CONTROLLER_PORT
from parol_commander.state import robot_state
from parol_commander.services.robot_client import client
from parol_commander.services.script_runner import (
    ScriptProcessHandle,
    run_script,
    create_default_config,
    stop_script,
)
from parol6.client.async_client import AsyncRobotClient


# ---- Command Discovery Functions ----


def categorize_command(name: str, doc: str) -> str:
    """Smart categorization based on method name patterns."""
    name_lower = name.lower()

    if "smooth_" in name_lower:
        return "Smooth Motion"
    elif any(x in name_lower for x in ["move", "jog"]):
        return "Motion"
    elif any(x in name_lower for x in ["get_", "ping", "is_", "wait_"]):
        return "Query"
    elif "gripper" in name_lower:
        return "Gripper"
    elif "gcode" in name_lower:
        return "GCODE"
    elif any(
        x in name_lower
        for x in ["enable", "disable", "home", "stop", "clear", "stream", "simulator"]
    ):
        return "Control & System"
    elif any(x in name_lower for x in ["io", "set_"]):
        return "IO"
    else:
        return "Other"


def discover_robot_commands() -> dict:
    """Introspect AsyncRobotClient to find all available commands."""
    commands = {}

    for name in dir(AsyncRobotClient):
        if name.startswith("_"):
            continue
        attr = getattr(AsyncRobotClient, name)
        if not callable(attr):
            continue

        sig = inspect.signature(attr)
        doc = (attr.__doc__ or "").strip().split("\n")[0]  # First line only
        category = categorize_command(name, doc)

        commands[name] = {
            "title": f"rbt.{name}(...)",
            "category": category,
            "signature": str(sig),
            "docstring": doc or "No description available",
        }

    return commands


class EditorPanel:
    """Program editor panel with script execution and command palette."""

    def __init__(self) -> None:
        """Initialize editor panel with state and UI references."""
        # Program directory
        self.PROGRAM_DIR = (
            REPO_ROOT / "PAROL-commander-software" / "GUI" / "files" / "Programs"
        )
        if not self.PROGRAM_DIR.exists():
            self.PROGRAM_DIR = REPO_ROOT / "programs"
            self.PROGRAM_DIR.mkdir(parents=True, exist_ok=True)

        # Program editor widgets
        self.program_filename_input: ui.input | None = None
        self.program_textarea: ui.codemirror | None = None
        self.program_log: ui.log | None = None

        # Script execution via subprocess
        self.script_handle: ScriptProcessHandle | None = None
        self.script_running: bool = False

        # Drawer element reference
        self.drawer: ui.element | None = None

    def _default_python_snippet(self) -> str:
        """Generate the initial pre-filled Python code with inlined controller host/port."""
        return f"""from parol6 import RobotClient

rbt = RobotClient(host={CONTROLLER_HOST!r}, port={CONTROLLER_PORT})

print("Moving to home position...")
rbt.home()

status = rbt.get_status()
print(f"Robot status: {{status}}")
"""

    def _insert_python_snippet(self, key: str) -> str:
        """Get Python code snippet for the given key."""
        snippets = {
            "enable": "rbt.enable()",
            "disable": "rbt.disable()",
            "home": "rbt.home()",
            "stop": "rbt.stop()",
            "clear_error": "rbt.clear_error()",
            "delay": "time.sleep(1.0)",
            "get_status": "status = rbt.get_status()\nprint(status)",
            "get_angles": "angles = rbt.get_angles()\nprint(f'Joint angles: {angles}')",
            "move_joints": "rbt.move_joints([0, 0, 0, 0, 0, 0], speed_percentage=50)",
            "jog_joint": "rbt.jog_joint(0, speed_percentage=50, duration=1.0)",
            "comment": "# Add your robot commands here",
        }

        # If not in hardcoded snippets, generate from discovered commands
        if key not in snippets:
            all_commands = discover_robot_commands()
            if key in all_commands:
                # Generate basic template based on method name and signature
                all_commands[key]["signature"]
                doc = all_commands[key]["docstring"]
                return f"rbt.{key}(...)  # {doc}"

        return snippets.get(key, f"rbt.{key}(...)")

    def _generate_snippet(self, method_name: str, use_current_position: bool) -> str:
        """Generate Python snippet with optional current position pre-fill."""
        # Motion commands that can use current position
        if use_current_position:
            if method_name == "move_joints":
                angles = list(robot_state.angles)
                return f"rbt.move_joints({angles}, speed_percentage=50)"
            elif method_name in ("move_pose", "move_cartesian"):
                x, y, z = robot_state.x, robot_state.y, robot_state.z
                rx, ry, rz = robot_state.rx, robot_state.ry, robot_state.rz
                return f"rbt.{method_name}([{x:.3f}, {y:.3f}, {z:.3f}, {rx:.3f}, {ry:.3f}, {rz:.3f}], speed_percentage=50)"

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
            logging.info("Added Python snippet: %s", snippet)

    def build_command_palette_table(self) -> None:
        """Build hierarchical command palette using ui.expansion for categories."""
        # Discover all commands dynamically
        all_commands = discover_robot_commands()

        # Group by category
        categories: dict[str, list[dict[str, Any]]] = {}
        for key, cmd in all_commands.items():
            cat = cmd["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append({"key": key, **cmd})

        # Scrollable container
        with ui.element("div").classes("overflow-y-auto w-full").style("height: 260px"):
            for category_name, commands in sorted(categories.items()):
                # Collapsible category expansion (no icon)
                with ui.expansion(category_name).classes("w-full").props("dense"):
                    for cmd in sorted(commands, key=lambda c: c["title"]):
                        # Clickable command row with tooltip
                        with ui.row().classes(
                            "cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 p-1 w-full items-center"
                        ) as row:
                            label = ui.label(cmd["title"]).classes("text-sm")
                            # Add tooltip showing signature and docstring with proper wrapping
                            with label:
                                tooltip_text = f"{cmd['signature']}"
                                if cmd["docstring"]:
                                    tooltip_text += f"\n\n{cmd['docstring']}"
                                ui.tooltip(tooltip_text).classes("text-xs").style(
                                    "max-width: 300px; white-space: pre-wrap;"
                                )
                            # Click handler with current position support
                            row.on(
                                "click",
                                lambda e, k=cmd["key"]: self._insert_command(k, True),
                            )

    async def load_program(self, filename: str | None = None) -> None:
        """Load a program file into the editor."""
        try:
            name = (
                filename
                or (
                    self.program_filename_input.value
                    if self.program_filename_input
                    else ""
                )
                or ""
            )
            text = (self.PROGRAM_DIR / name).read_text(encoding="utf-8")
            if self.program_textarea:
                self.program_textarea.value = text
            ui.notify(f"Loaded {name}", color="primary")
            logging.info("Loaded program %s", name)
        except Exception as e:
            ui.notify(f"Load failed: {e}", color="negative")
            logging.error("Load failed: %s", e)

    async def save_program(self, as_name: str | None = None) -> None:
        """Save the current program to a file."""
        try:
            name = (
                as_name
                or (
                    self.program_filename_input.value
                    if self.program_filename_input
                    else ""
                )
                or ""
            )
            content = self.program_textarea.value if self.program_textarea else ""
            (self.PROGRAM_DIR / name).write_text(content, encoding="utf-8")
            ui.notify(f"Saved {name}", color="positive")
            logging.info("Saved program %s", name)
            if as_name and self.program_filename_input:
                self.program_filename_input.value = as_name
        except Exception as e:
            ui.notify(f"Save failed: {e}", color="negative")
            logging.error("Save failed: %s", e)

    def open_file_picker(self) -> None:
        """Open a file picker dialog to upload a program file."""
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label("Open Program from disk")

            def _on_upload(e):
                try:
                    data = e.content.read()
                    name = getattr(e, "name", None) or "uploaded_program.txt"
                    (self.PROGRAM_DIR / name).write_bytes(data)
                    if self.program_filename_input:
                        self.program_filename_input.value = name
                    if self.program_textarea:
                        self.program_textarea.value = data.decode(
                            "utf-8", errors="ignore"
                        )
                    ui.notify(f"Loaded {name}", color="primary")
                except Exception as ex:
                    ui.notify(f"Open failed: {ex}", color="negative")
                finally:
                    dlg.close()

            ui.upload(on_upload=_on_upload).props("accept=.txt,.prog,.gcode,*/*")
            with ui.row().classes("gap-2"):
                ui.button("Cancel", on_click=dlg.close)
        dlg.open()

    async def _start_script_process(self) -> None:
        """Save current editor content and start it as a Python subprocess."""
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

            config = create_default_config(str(script_path), str(REPO_ROOT))

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

            await client.stream_off()
            self.script_handle = await run_script(config, on_stdout, on_stderr)
            self.script_running = True

            # Launch monitor task to reset state when script finishes
            h = self.script_handle  # capture
            asyncio.create_task(self._monitor_script_completion(h, filename))

            ui.notify(f"Started script: {filename}", color="positive")
            logging.info("Started script: %s", filename)

        except Exception as e:
            ui.notify(f"Failed to start script: {e}", color="negative")
            logging.error("Failed to start script: %s", e)

    async def _monitor_script_completion(
        self, handle: ScriptProcessHandle, filename: str
    ) -> None:
        """Monitor script subprocess completion and reset state when it finishes."""
        # Capture UI client context at the start
        ui_client = context.client

        try:
            rc = await handle["proc"].wait()
            # Let stream reader tasks finish
            for t in (handle["stdout_task"], handle["stderr_task"]):
                with contextlib.suppress(Exception):
                    await t
            # Only reset state if this handle is still the active one
            if self.script_handle is handle:
                # Re-enter UI client context for notifications and state updates
                with ui_client:
                    self.script_handle = None
                    self.script_running = False
                    ui.notify(
                        f"Script finished: {filename} (exit {rc})",
                        color="positive" if rc == 0 else "warning",
                    )
                    logging.info("Script %s finished with code %s", filename, rc)
                    await client.stream_on()
        except Exception as e:
            logging.error("Error monitoring script process: %s", e)
            # Best-effort reset if still active with UI client context
            with ui_client:
                if self.script_handle is handle:
                    self.script_handle = None
                    self.script_running = False

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

            if handle:
                await stop_script(handle)
            await client.stream_on()

            ui.notify("Script stopped", color="warning")
            logging.info("Script stopped by user")

        except Exception as e:
            ui.notify(f"Error stopping script: {e}", color="negative")
            logging.error("Error stopping script: %s", e)
            # State already cleared above

    def build(self) -> None:
        """Build the program editor content (no wrapper)."""
        # Editor content
        with ui.column():
            with ui.row():
                with ui.column():
                    with ui.row().classes("items-center gap-2 w-full"):
                        self.program_filename_input = ui.input(
                            label="Filename", value=""
                        ).classes("text-sm font-small flex-1")
                        ui.button("Open", on_click=self.open_file_picker).props(
                            "unelevated"
                        )

                    self.program_textarea = (
                        ui.codemirror(
                            value=self._default_python_snippet(),
                            language="Python",
                            line_wrapping=True,
                        )
                        .classes("w-full")
                        .style("height: 420px")
                    )

                    # Initialize CodeMirror theme based on theme/system
                    try:
                        mode = get_theme()
                        effective = "light" if mode == "light" else "dark"
                        self.program_textarea.theme = (
                            "basicLight" if effective == "light" else "oneDark"
                        )
                    except Exception:
                        self.program_textarea.theme = "oneDark"

                    with ui.row().classes("gap-2"):
                        ui.button("Start", on_click=self._start_script_process).props(
                            "unelevated color=positive"
                        )
                        ui.button("Stop", on_click=self._stop_script_process).props(
                            "unelevated color=negative"
                        )
                        ui.button("Save", on_click=self.save_program).props(
                            "unelevated"
                        )

                        def save_as():
                            async def do_save_as():
                                name = save_as_input.value.strip() or "program.txt"
                                await self.save_program(as_name=name)
                                save_as_dialog.close()

                            save_as_dialog = ui.dialog()
                            with save_as_dialog, ui.card():
                                ui.label("Save As")
                                save_as_input = ui.input(
                                    label="New filename",
                                    value=self.program_filename_input.value
                                    if self.program_filename_input
                                    else "",
                                ).classes("w-80")
                                with ui.row().classes("gap-2"):
                                    ui.button("Cancel", on_click=save_as_dialog.close)
                                    ui.button("Save", on_click=do_save_as).props(
                                        "color=positive"
                                    )
                            save_as_dialog.open()

                        ui.button("Save as", on_click=save_as).props("unelevated")

                with ui.column():
                    self.build_command_palette_table()

            # Program log directly beneath editor
            ui.label("Program Log").classes("text-sm text-[var(--ctk-muted)]")
            self.program_log = (
                ui.log(max_lines=1000)
                .classes("w-full whitespace-pre-wrap break-words")
                .style("height: 200px")
            )
