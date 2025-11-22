import asyncio
import pytest
from nicegui.testing import User


@pytest.mark.integration
async def test_cartesian_disabled_blocks_jog(user: User, monkeypatch):
    """When CART_EN reports axis disabled, ControlPanel should ignore presses (no jog sent)."""
    # Open app
    await user.open("/")

    # Set TRF array to all zeros to disable all cart axes
    from parol_commander.state import robot_state

    robot_state.cart_en_trf = [0] * 12

    # Patch AsyncRobotClient.jog_cartesian to record calls
    calls = []

    async def _record_jog_cartesian(self, frame, axis, speed_percentage, duration):  # type: ignore[no-redef]
        calls.append((frame, axis, speed_percentage, duration))
        return True

    from parol6.client.async_client import AsyncRobotClient

    monkeypatch.setattr(
        AsyncRobotClient, "jog_cartesian", _record_jog_cartesian, raising=True
    )

    # Try to press X+
    user.find(marker="axis-xplus").trigger("mousedown")
    await asyncio.sleep(0.2)
    user.find(marker="axis-xplus").trigger("mouseup")

    # Give control loop a moment
    await asyncio.sleep(0.3)

    # Expect no jog_cartesian calls due to disabled axis
    assert calls == []
