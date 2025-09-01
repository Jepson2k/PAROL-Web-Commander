from __future__ import annotations

import asyncio
import time

import pytest


class RecorderClient:
    """Records jog send timestamps while acting as the transport for UI-only acceptance tests."""

    def __init__(self) -> None:
        self.joint_ts: list[float] = []
        self.cart_ts: list[float] = []

    async def jog_joint(
        self,
        joint_index: int,
        speed_percentage: int,
        duration: float | None = None,
        distance_deg: float | None = None,
    ) -> str:
        self.joint_ts.append(time.monotonic())
        return "OK"

    async def jog_cartesian(
        self, frame: str, axis: str, speed_percentage: int, duration: float
    ) -> str:
        self.cart_ts.append(time.monotonic())
        return "OK"

    # No-op implementations to satisfy MovePage hooks if called
    async def enable(self) -> str:
        return "OK"

    async def disable(self) -> str:
        return "OK"

    async def home(self) -> str:
        return "OK"

    async def clear_error(self) -> str:
        return "OK"

    async def stop(self) -> str:
        return "OK"

    async def _request(self, message: str, bufsize: int = 2048) -> str | None:
        return None


@pytest.mark.unit
async def test_webapp_rate_joint_100hz(user, monkeypatch):
    """Drive the real page, press-and-hold J1+ with user fixture, assert ~100 Hz emission."""
    import app.main as app_main

    # Prevent controller auto-start so tests remain hardware-free
    async def _noop_start_controller(port: str | None) -> None:
        return None

    monkeypatch.setattr(app_main, "start_controller", _noop_start_controller, raising=True)

    # Replace UDP client used by MovePage with a recorder
    import app.pages.move as move_mod

    recorder = RecorderClient()
    monkeypatch.setattr(move_mod, "client", recorder, raising=True)

    # Open the real page
    await user.open("/")
    # Ensure page content is rendered
    await user.should_see("Joint jog")

    # Tag J1-right arrow image for selection
    img = app_main.move_page_instance._joint_right_imgs.get(0)
    assert img is not None, "J1 right arrow image not found"
    img.mark("j1-right")

    # Press and hold for ~2 seconds
    user.find("j1-right").trigger("mousedown")
    await asyncio.sleep(0.2) # GUI updates don't happen automatically
    start = time.monotonic()
    await asyncio.sleep(1.0)
    user.find("j1-right").trigger("mouseup")
    await asyncio.sleep(0.2) # GUI updates don't happen automatically
    duration = time.monotonic() - start

    count = len(recorder.joint_ts)
    hz = count / max(1e-9, duration)
    assert (
        hz >= 95.0
    ), f"Joint jog emission too low: {hz:.2f} Hz (count={count}, duration={duration:.3f}s)"


@pytest.mark.unit
async def test_webapp_rate_cart_100hz(user, monkeypatch):
    """Drive the real page, press-and-hold X+ with user fixture, assert ~100 Hz emission."""
    import app.main as app_main

    async def _noop_start_controller(port: str | None) -> None:
        return None

    monkeypatch.setattr(app_main, "start_controller", _noop_start_controller, raising=True)

    import app.pages.move as move_mod

    recorder = RecorderClient()
    monkeypatch.setattr(move_mod, "client", recorder, raising=True)

    await user.open("/")
    await user.should_see("Cartesian jog", retries=5)
    user.find("Cartesian jog").click()
    await asyncio.sleep(0.1)

    axis_img = app_main.move_page_instance._cart_axis_imgs.get("X+")
    assert axis_img is not None, "Cartesian X+ image not found"
    # Mark inside the user client context to ensure the marker is visible to the simulated user
    with user:
        axis_img.mark("axis-xplus")

    user.find("axis-xplus").trigger("mousedown")
    await asyncio.sleep(0.2) # GUI updates don't happen automatically
    start = time.monotonic()
    await asyncio.sleep(1.0)
    user.find("axis-xplus").trigger("mouseup")
    await asyncio.sleep(0.2) # GUI updates don't happen automatically
    duration = time.monotonic() - start

    count = len(recorder.cart_ts)
    hz = count / max(1e-9, duration)
    assert (
        hz >= 95.0
    ), f"Cartesian jog emission too low: {hz:.2f} Hz (count={count}, duration={duration:.3f}s)"
