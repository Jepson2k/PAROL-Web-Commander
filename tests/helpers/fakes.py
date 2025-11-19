"""Fake implementations for testing."""
import time
from typing import Any, Callable

from .types import RecordedCall


class RecordingAsyncClient:
    """Fake AsyncRobotClient that records all method calls for test assertions.

    This is a no-op client that implements the subset of parol6.AsyncRobotClient
    methods used by the app, recording each call as a RecordedCall for later inspection.
    """

    def __init__(self, on_call: Callable[[RecordedCall], None] | None = None) -> None:
        """Initialize the recording client.

        Args:
            on_call: Optional callback invoked on each method call with the RecordedCall.
        """
        self.calls: list[RecordedCall] = []
        self.on_call = on_call

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        """Record a method call."""
        call: RecordedCall = {
            "name": name,
            "args": args,
            "kwargs": kwargs,
            "timestamp": time.time(),
        }
        self.calls.append(call)
        if self.on_call:
            self.on_call(call)

    # Jog and motion commands
    async def jog_joint(
        self,
        index: int,
        *,
        speed_percentage: int,
        duration: float | None = None,
        distance_deg: float | None = None,
    ) -> None:
        """Record a joint jog command."""
        self._record(
            "jog_joint",
            index,
            speed_percentage=speed_percentage,
            duration=duration,
            distance_deg=distance_deg,
        )

    async def jog_cartesian(
        self, frame: Any, axis: Any, speed_percentage: int, duration: float
    ) -> None:
        """Record a cartesian jog command."""
        self._record(
            "jog_cartesian",
            frame,
            axis,
            speed_percentage=speed_percentage,
            duration=duration,
        )

    async def move_joints(
        self, target: list[float], *, speed_percentage: int = 50, **kwargs: Any
    ) -> None:
        """Record a move_joints command."""
        self._record("move_joints", target, speed_percentage=speed_percentage, **kwargs)

    # System commands
    async def home(self) -> None:
        """Record a home command."""
        self._record("home")

    async def stop(self) -> None:
        """Record a stop command."""
        self._record("stop")

    async def enable(self) -> None:
        """Record an enable command."""
        self._record("enable")

    async def disable(self) -> None:
        """Record a disable command."""
        self._record("disable")

    async def start(self) -> None:
        """Record a start command (for E-STOP resume)."""
        self._record("start")

    # Simulator control
    async def simulator_on(self) -> None:
        """Record a simulator_on command."""
        self._record("simulator_on")

    async def simulator_off(self) -> None:
        """Record a simulator_off command."""
        self._record("simulator_off")

    # IO and gripper
    async def control_pneumatic_gripper(self, action: str, port: int) -> None:
        """Record a pneumatic gripper control command."""
        self._record("control_pneumatic_gripper", action, port)

    async def control_electric_gripper(self, action: str, **kwargs: Any) -> None:
        """Record an electric gripper control command."""
        self._record("control_electric_gripper", action, **kwargs)

    # Configuration
    async def set_serial_port(self, port: str) -> None:
        """Record a set_serial_port command."""
        self._record("set_serial_port", port)

    async def set_tool(self, tool: str) -> None:
        """Record a set_tool command."""
        self._record("set_tool", tool)

    # Query methods (may return defaults for tests)
    async def ping(self) -> dict[str, Any]:
        """Record a ping and return a fake response."""
        self._record("ping")
        return {"payload": {"serial": 1}}  # Fake connected response

    async def get_status(self) -> dict[str, Any]:
        """Record a get_status and return a fake response."""
        self._record("get_status")
        return {}

    async def get_tool(self) -> dict[str, Any]:
        """Record a get_tool and return a fake response."""
        self._record("get_tool")
        return {"tool": "NONE"}

    async def wait_for_server_ready(self, timeout: float = 5.0) -> None:
        """Record a wait_for_server_ready (no-op)."""
        self._record("wait_for_server_ready", timeout=timeout)

    async def stream_on(self) -> None:
        """Record a stream_on command."""
        self._record("stream_on")

    async def stream_off(self) -> None:
        """Record a stream_off command."""
        self._record("stream_off")

    async def close(self) -> None:
        """Record a close command."""
        self._record("close")

    def status_stream(self) -> Any:
        """Return a fake async iterator for status stream."""
        # For tests that don't need real streaming, return an empty async generator

        async def _empty_stream():
            if False:
                yield {}

        return _empty_stream()
