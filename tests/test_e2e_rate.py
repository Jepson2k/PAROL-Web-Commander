from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

import app.pages.move as move_mod
from app import main
from app.services.robot_client import client as real_client
from app.constants import WEBAPP_CONTROL_RATE_HZ

if TYPE_CHECKING:
    from nicegui.testing import User
    from pytest import MonkeyPatch

    from app.services.robot_client import AsyncRobotClient
    from parol6.protocol.types import Frame, Axis


class ForwardingRecorderClient:
    """
    Wraps the real UDP RobotClient to forward actual datagrams to the headless server,
    while recording timestamps of jog sends for rate measurement.
    """

    def __init__(self, real_client: AsyncRobotClient) -> None:
        self.real = real_client
        self.joint_ts: list[float] = []
        self.cart_ts: list[float] = []

    async def jog_joint(
        self,
        joint_index: int,
        speed_percentage: int,
        duration: float | None = None,
        distance_deg: float | None = None,
    ) -> str | dict:
        self.joint_ts.append(time.monotonic())
        return await self.real.jog_joint(
            joint_index, speed_percentage, duration, distance_deg
        )

    async def jog_cartesian(
        self, frame: Frame, axis: Axis, speed_percentage: int, duration: float
    ) -> str | dict:
        self.cart_ts.append(time.monotonic())
        return await self.real.jog_cartesian(frame, axis, speed_percentage, duration)

    async def stream_on(self) -> str | dict:
        return await self.real.stream_on()

    async def stream_off(self) -> str | dict:
        return await self.real.stream_off()

    # Pass-through for any other calls if triggered
    async def __getattr__(self, name):
        return getattr(self.real, name)


@pytest.mark.integration
@pytest.mark.module_under_test(main)
async def test_e2e_rate_joint(user: User, headless_server, monkeypatch: MonkeyPatch):
    """
    E2E acceptance: Drive real UI with user fixture, forward UDP to real headless server,
    measure emission cadence on the client side; assert ~100 Hz.
    """

    # Prevent controller auto-start; we already run the headless server via fixture
    async def _noop_start_controller(port: str | None) -> None:
        return None

    monkeypatch.setattr(main, "start_controller", _noop_start_controller, raising=True)
    # Ensure webapp does not attempt its own simulator toggling in e2e
    monkeypatch.setenv("PAROL_WEBAPP_AUTO_SIMULATOR", "0")

    # Forwarder records sends and forwards to server
    fwd = ForwardingRecorderClient(real_client)
    monkeypatch.setattr(move_mod, "client", fwd, raising=True)

    await user.open("/")

    img = main.move_page_instance._joint_right_imgs.get(0)
    assert img is not None, "J1 right arrow image not found"
    img.mark("e2e-j1-right")

    await asyncio.sleep(2)  # Backend might still be starting up
    user.find("e2e-j1-right").trigger("mousedown")
    start = time.monotonic()
    await asyncio.sleep(2)
    user.find("e2e-j1-right").trigger("mouseup")
    duration = time.monotonic() - start

    count = len(fwd.joint_ts)
    hz = count / max(1e-9, duration)
    assert (
        hz >= 0.9 * WEBAPP_CONTROL_RATE_HZ
    ), f"E2E joint emission too low: {hz:.2f} Hz (count={count}, duration={duration:.3f}s)"


@pytest.mark.integration
@pytest.mark.module_under_test(main)
async def test_e2e_rate_cart(user: User, headless_server, monkeypatch: MonkeyPatch):
    """
    E2E acceptance: Drive real UI for cartesian jog with user fixture, forward UDP to server,
    and assert ~100 Hz emission cadence.
    """

    async def _noop_start_controller(port: str | None) -> None:
        return None

    monkeypatch.setattr(main, "start_controller", _noop_start_controller, raising=True)
    # Ensure webapp does not attempt its own simulator toggling in e2e
    monkeypatch.setenv("PAROL_WEBAPP_AUTO_SIMULATOR", "0")

    fwd = ForwardingRecorderClient(real_client)
    monkeypatch.setattr(move_mod, "client", fwd, raising=True)

    await user.open("/")

    axis_img = main.move_page_instance._cart_axis_imgs.get("X+")
    assert axis_img is not None, "Cartesian X+ image not found"
    axis_img.mark("e2e-axis-xplus")

    await asyncio.sleep(2)  # Backend might still be starting up
    user.find("e2e-axis-xplus").trigger("mousedown")
    start = time.monotonic()
    await asyncio.sleep(2)
    user.find("e2e-axis-xplus").trigger("mouseup")
    duration = time.monotonic() - start

    count = len(fwd.cart_ts)
    hz = count / max(1e-9, duration)
    assert (
        hz >= 0.9 * WEBAPP_CONTROL_RATE_HZ
    ), f"E2E cart emission too low: {hz:.2f} Hz (count={count}, duration={duration:.3f}s)"
