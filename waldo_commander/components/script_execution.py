"""Script execution controller: subprocess lifecycle + GUI stepping."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from pathlib import Path
from typing import Any, Callable

from nicegui import Client, context, ui

from waldo_commander.constants import REPO_ROOT
from waldo_commander.state import simulation_state
from waldo_commander.services.script_runner import (
    ScriptProcessHandle,
    create_default_config,
    run_script,
    stop_script,
)
from waldo_commander.services.stepping_client import GUIStepController

logger = logging.getLogger(__name__)


class ScriptExecutionController:
    """Owns the script subprocess lifecycle and GUI stepping controller.

    Notifies the playback bar via callbacks; the playback bar calls public
    methods (start, stop, pause, play, step) when the user clicks buttons.
    """

    def __init__(
        self,
        *,
        on_script_start: Callable[[], None],
        on_script_stop: Callable[[Client], None],
        on_script_step_start: Callable[[int, Client], None],
        on_script_step_complete: Callable[[int, Client], None],
        stop_sim_playback: Callable[[], None],
        update_play_button: Callable[[], None],
        get_textarea_value: Callable[[], str],
        get_filename: Callable[[], str],
        get_program_log: Callable[[], ui.log | None],
        expand_log: Callable[[], None],
        clear_highlight: Callable[[], None],
        program_dir: Path,
    ) -> None:
        self._on_script_start = on_script_start
        self._on_script_stop = on_script_stop
        self._on_script_step_start = on_script_step_start
        self._on_script_step_complete = on_script_step_complete
        self._stop_sim_playback = stop_sim_playback
        self._update_play_button = update_play_button
        self._get_textarea_value = get_textarea_value
        self._get_filename = get_filename
        self._get_program_log = get_program_log
        self._expand_log = expand_log
        self._clear_highlight = clear_highlight
        self._program_dir = program_dir

        self.script_handle: ScriptProcessHandle | None = None
        self._step_session_id: str | None = None
        self._step_controller: GUIStepController | None = None
        self._event_watcher_task: asyncio.Task | None = None

    # ---- Public API (called by PlaybackController) ----

    async def start(self) -> None:
        """Start the current editor content as a Python subprocess.

        Writes to a scratch file under ``PROGRAM_DIR/.runtime/`` so the user's
        named file is only modified by explicit save.
        """
        self._stop_sim_playback()

        if simulation_state.script_running:
            ui.notify("Script already running", color="warning")
            return

        try:
            filename = self._get_filename() or "program.py"
            if not filename.endswith(".py"):
                filename += ".py"

            content = self._get_textarea_value()
            # Write to scratch location — do not overwrite the user's saved file
            runtime_dir = self._program_dir / ".runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            script_path = runtime_dir / filename
            script_path.write_text(content, encoding="utf-8")

            program_log = self._get_program_log()
            if program_log:
                program_log.clear()

            script_config = create_default_config(str(script_path), str(REPO_ROOT))

            ui_client = context.client

            def on_stdout(line: str):
                with ui_client:
                    log = self._get_program_log()
                    if log:
                        log.push(line)

            def on_stderr(line: str):
                with ui_client:
                    log = self._get_program_log()
                    if log:
                        log.push(f"[ERR] {line}")

            self._step_session_id = uuid.uuid4().hex[:8]
            self._step_controller = GUIStepController(self._step_session_id)
            self._step_controller.initialize()

            self.script_handle = await run_script(
                script_config,
                on_stdout,
                on_stderr,
                session_id=self._step_session_id,
            )
            simulation_state.script_running = True

            simulation_state.is_playing = True
            self._step_controller.signal_play()
            self._on_script_start()

            self._expand_log()

            self._event_watcher_task = asyncio.create_task(
                self._watch_script_events(ui_client)
            )

            h = self.script_handle
            asyncio.create_task(self._monitor_script_completion(h, filename, ui_client))

            ui.notify(f"Started script: {filename}", color="positive")
            logger.info("Started script: %s", filename)

        except Exception as e:
            ui.notify(f"Failed to start script: {e}", color="negative")
            logger.error("Failed to start script: %s", e)
            simulation_state.script_running = False
            self._step_session_id = None
            if self._step_controller:
                self._step_controller.cleanup()
                self._step_controller = None
            simulation_state.is_playing = False
            self._update_play_button()

    async def stop(self) -> None:
        """Stop the running script process."""
        if not simulation_state.script_running or not self.script_handle:
            ui.notify("No script running", color="warning")
            return

        try:
            handle = self.script_handle
            self.script_handle = None
            simulation_state.script_running = False
            simulation_state.is_playing = False
            self._update_play_button()

            self.cleanup_stepping()

            if handle:
                await stop_script(handle)

            ui.notify("Script stopped", color="warning")
            logger.info("Script stopped by user")

        except Exception as e:
            ui.notify(f"Error stopping script: {e}", color="negative")
            logger.error("Error stopping script: %s", e)

    def pause(self) -> None:
        if self._step_controller:
            self._step_controller.signal_pause()

    def play(self) -> None:
        if self._step_controller:
            self._step_controller.signal_play()

    def step(self) -> None:
        if self._step_controller:
            self._step_controller.signal_step()

    def has_stepper(self) -> bool:
        return self._step_controller is not None

    def cleanup_stepping(self) -> None:
        """Clean up stepping controller and event watcher."""
        if self._event_watcher_task and not self._event_watcher_task.done():
            self._event_watcher_task.cancel()
        self._event_watcher_task = None

        if self._step_controller:
            self._step_controller.cleanup()
            self._step_controller = None
        self._step_session_id = None

        self._clear_highlight()

    # ---- Internal ----

    async def _watch_script_events(self, ui_client: Any) -> None:
        """Poll for script events and update visualization."""
        try:
            while simulation_state.script_running and self._step_controller:
                events = self._step_controller.poll_events()

                for event in events:
                    event_type = event.get("event")
                    method = event.get("method", "")
                    step = event.get("step", 0)

                    if event_type == "start":
                        self._on_script_step_start(step, ui_client)

                    elif event_type == "complete":
                        self._on_script_step_complete(step, ui_client)
                        logger.debug(
                            "Script event: %s completed (step %d)", method, step
                        )

                await asyncio.sleep(0.05)

        except asyncio.CancelledError:
            logger.debug("Event watcher task cancelled")
        except Exception as e:
            logger.error("Error in event watcher: %s", e)

    async def _monitor_script_completion(
        self, handle: ScriptProcessHandle, filename: str, ui_client: Any
    ) -> None:
        """Monitor script subprocess completion and reset state when it finishes."""
        try:
            rc = await handle["proc"].wait()
            for t in (handle["stdout_task"], handle["stderr_task"]):
                with contextlib.suppress(Exception):
                    await t
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
        simulation_state.script_running = False
        simulation_state.is_playing = False
        simulation_state.sim_pose_override = False
        self._on_script_stop(ui_client)
        self.cleanup_stepping()
