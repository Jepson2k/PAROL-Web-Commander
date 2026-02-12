"""
Stepping client wrapper for GUI-controlled script execution.

Provides a wrapper around RobotClient that:
1. Emits events for each motion command (start/complete)
2. Optionally pauses after each command for stepping through scripts
3. Communicates with GUI via file-based IPC

Cross-platform compatible (Windows, macOS, Linux).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


# Motion methods that should trigger wait_until_stopped and stepping behavior
MOTION_METHODS = frozenset(
    {
        "home",
        "moveJ",
        "moveL",
        "moveC",
        "moveS",
        "moveP",
        "jogJ",
        "jogL",
        "servoJ",
        "servoL",
        "control_pneumatic_gripper",
        "control_electric_gripper",
        "delay",
    }
)


class StepIO:
    """
    File-based IPC for stepping control between script subprocess and GUI.

    Uses two files:
    - Control file (GUI -> Script): Contains paused flag and step signals
    - Event file (Script -> GUI): Contains command start/complete events
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._temp_dir = Path(tempfile.gettempdir())
        self._control_file = self._temp_dir / f".parol_control_{session_id}"
        self._event_file = self._temp_dir / f".parol_events_{session_id}"
        self._step_count = 0
        self._last_step_acked = 0

    @classmethod
    def from_env(cls) -> "StepIO | None":
        """
        Create StepIO from environment variables.
        Returns None if PAROL_STEP_SESSION is not set.
        """
        session_id = os.environ.get("PAROL_STEP_SESSION")
        if not session_id:
            return None
        return cls(session_id)

    def _atomic_write(self, path: Path, data: dict) -> None:
        """Write data to file atomically using temp file + move."""
        temp_path = path.with_suffix(".tmp")
        try:
            temp_path.write_text(json.dumps(data, indent=2))
            shutil.move(str(temp_path), str(path))
        except Exception:
            # Clean up temp file if move failed
            if temp_path.exists():
                temp_path.unlink()
            raise

    def _read_control(self) -> dict:
        """Read control file, return defaults if not exists or parse error."""
        try:
            if self._control_file.exists():
                return json.loads(self._control_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
        return {"paused": True, "step_signal": 0, "step_acked": 0}

    def _read_events(self) -> list[dict]:
        """Read events from event file."""
        try:
            if self._event_file.exists():
                data = json.loads(self._event_file.read_text())
                return data.get("events", [])
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def emit_event(self, event_type: str, method: str, **extra: Any) -> None:
        """
        Emit an event to the event file.

        Args:
            event_type: "start" or "complete"
            method: Name of the motion method
            **extra: Additional event data
        """
        events = self._read_events()
        events.append(
            {
                "event": event_type,
                "method": method,
                "step": self._step_count,
                "ts": time.time(),
                **extra,
            }
        )
        self._atomic_write(self._event_file, {"events": events})

    def check_should_pause(self) -> bool:
        """Check if the script should pause (paused flag is true)."""
        control = self._read_control()
        return control.get("paused", True)

    def wait_for_step_or_play(
        self, timeout: float = 300.0, poll_interval: float = 0.05
    ) -> bool:
        """
        Wait until either:
        - step_signal > step_acked (step forward requested)
        - paused becomes False (play mode activated)

        Returns True if should continue, False on timeout.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            control = self._read_control()

            # If paused is False, we're in play mode - continue immediately
            if not control.get("paused", True):
                return True

            # Check if a step signal was sent
            step_signal = control.get("step_signal", 0)
            step_acked = control.get("step_acked", 0)

            if step_signal > step_acked:
                # Step requested - acknowledge it
                self._ack_step(control, step_signal)
                return True

            time.sleep(poll_interval)

        return False  # Timeout

    def _ack_step(self, control: dict, step_signal: int) -> None:
        """Acknowledge a step by incrementing step_acked."""
        control["step_acked"] = step_signal
        self._atomic_write(self._control_file, control)

    def increment_step_count(self) -> None:
        """Increment the internal step counter."""
        self._step_count += 1


class SteppingClientWrapper:
    """
    Wrapper around RobotClient that adds stepping behavior.

    - Intercepts motion methods
    - Emits start/complete events for GUI visualization
    - Calls wait_until_stopped() after each motion command
    - Optionally pauses for stepping based on control file
    """

    def __init__(self, wrapped_client: Any, step_io: StepIO) -> None:
        """
        Initialize the wrapper.

        Args:
            wrapped_client: The RobotClient instance to wrap
            step_io: StepIO instance for IPC
        """
        self._wrapped = wrapped_client
        self._step_io = step_io

    def __getattr__(self, name: str) -> Any:
        """
        Delegate attribute access to wrapped client.
        Intercept motion methods to add stepping behavior.
        """
        attr = getattr(self._wrapped, name)

        if name in MOTION_METHODS and callable(attr):
            return self._wrap_motion_method(name, attr)

        return attr

    def _wrap_motion_method(self, name: str, method: Callable) -> Callable:
        """Create a wrapper function for a motion method."""

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Emit start event
            self._step_io.emit_event("start", name)

            # Call the actual method
            result = method(*args, **kwargs)

            # Wait for motion to complete
            self._wrapped.wait_motion_complete()

            # Emit complete event
            self._step_io.emit_event("complete", name)

            # Increment step counter for next command
            self._step_io.increment_step_count()

            # Check if we should pause for stepping
            if self._step_io.check_should_pause():
                self._step_io.wait_for_step_or_play()

            return result

        return wrapper


class GUIStepController:
    """
    GUI-side controller for stepping.
    Used by the GUI to control script execution via IPC files.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._temp_dir = Path(tempfile.gettempdir())
        self._control_file = self._temp_dir / f".parol_control_{session_id}"
        self._event_file = self._temp_dir / f".parol_events_{session_id}"
        self._last_event_count = 0

    def _atomic_write(self, path: Path, data: dict) -> None:
        """Write data to file atomically using temp file + move."""
        temp_path = path.with_suffix(".tmp")
        try:
            temp_path.write_text(json.dumps(data, indent=2))
            shutil.move(str(temp_path), str(path))
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

    def _read_control(self) -> dict:
        """Read current control state."""
        try:
            if self._control_file.exists():
                return json.loads(self._control_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
        return {"paused": True, "step_signal": 0, "step_acked": 0}

    def initialize(self) -> None:
        """Initialize control file with default state (paused=True)."""
        self._atomic_write(
            self._control_file,
            {
                "paused": True,
                "step_signal": 0,
                "step_acked": 0,
            },
        )
        # Clear any existing events
        self._atomic_write(self._event_file, {"events": []})

    def signal_step(self) -> None:
        """Signal the script to execute one command then pause."""
        control = self._read_control()
        control["paused"] = True
        control["step_signal"] = control.get("step_signal", 0) + 1
        self._atomic_write(self._control_file, control)

    def signal_play(self) -> None:
        """Signal the script to continue without pausing (play mode)."""
        control = self._read_control()
        control["paused"] = False
        self._atomic_write(self._control_file, control)

    def signal_pause(self) -> None:
        """Signal the script to pause after the current command."""
        control = self._read_control()
        control["paused"] = True
        self._atomic_write(self._control_file, control)

    def poll_events(self) -> list[dict]:
        """
        Poll for new events from the script.
        Returns list of new events since last poll.
        """
        try:
            if self._event_file.exists():
                data = json.loads(self._event_file.read_text())
                events = data.get("events", [])
                # Return only new events
                new_events = events[self._last_event_count :]
                self._last_event_count = len(events)
                return new_events
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def get_step_count(self) -> int:
        """Get the current step count from events."""
        try:
            if self._event_file.exists():
                data = json.loads(self._event_file.read_text())
                events = data.get("events", [])
                # Count complete events
                return sum(1 for e in events if e.get("event") == "complete")
        except (json.JSONDecodeError, OSError):
            pass
        return 0

    def cleanup(self) -> None:
        """Remove IPC files."""
        try:
            if self._control_file.exists():
                self._control_file.unlink()
        except OSError:
            pass
        try:
            if self._event_file.exists():
                self._event_file.unlink()
        except OSError:
            pass
