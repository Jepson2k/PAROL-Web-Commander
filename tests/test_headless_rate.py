from __future__ import annotations

import json
import socket
import time

import pytest


def _request_loop_stats(
    host: str = "127.0.0.1", port: int = 5001, timeout: float = 1.0
) -> dict | None:
    """
    Fire a GET_LOOP_STATS query and parse LOOP_STATS|{json} response.
    Returns parsed dict or None on failure.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(b"GET_LOOP_STATS", (host, port))
            data, _ = s.recvfrom(4096)
        msg = data.decode("utf-8", errors="ignore").strip()
        if not msg.startswith("LOOP_STATS|"):
            return None
        return json.loads(msg.split("|", 1)[1])
    except Exception:
        return None


def _send_udp(host: str, port: int, payload: str) -> None:
    data = payload.encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(data, (host, port))


@pytest.mark.integration
def test_headless_movejoint_loop_frequency(headless_server):
    """
    Launch the real headless server and verify effective loop Hz via MULTIJOG with duration=2.0s.
    We compute Hz = Δ(loop_count) / Δt using GET_LOOP_STATS (no ACK dependency).
    Assert Hz >= 95 to allow scheduler jitter.
    """
    host, port = "127.0.0.1", 5001

    stats0 = _request_loop_stats(host, port, timeout=2.0)
    assert stats0 is not None, "GET_LOOP_STATS failed initially"
    t0 = time.monotonic()

    # MULTIJOG|joints_csv|speeds_csv|duration ; jog J1+ at 10% for 2.0s (no IK required)
    _send_udp(host, port, "MULTIJOG|0|10|2.0")

    # Guard for execution + scheduling jitter
    time.sleep(2.2)

    stats1 = _request_loop_stats(host, port, timeout=2.0)
    assert stats1 is not None, "GET_LOOP_STATS failed after command"
    t1 = time.monotonic()

    dt = t1 - t0
    assert dt > 0, f"Non-positive Δt={dt:.6f}s"
    delta_loops = int(stats1.get("loop_count", 0)) - int(stats0.get("loop_count", 0))
    hz = delta_loops / dt if dt > 0 else 0.0
    assert (
        hz >= 95.0
    ), f"Effective loop rate too low: {hz:.2f} Hz (dt={dt:.3f}s, Δloops={delta_loops})"


@pytest.mark.integration
def test_headless_cartjog_loop_frequency(headless_server):
    """
    Launch the real headless server with fake-serial enabled and verify effective loop Hz
    while running an IK-driven command (CARTJOG) for 2.0s.
    We compute Hz = Δ(loop_count) / Δt using GET_LOOP_STATS. Assert Hz >= 95.
    """
    host, port = "127.0.0.1", 5001

    stats0 = _request_loop_stats(host, port, timeout=2.0)
    assert stats0 is not None, "GET_LOOP_STATS failed initially"
    t0 = time.monotonic()

    # CARTJOG|frame|axis|speed|duration ; TRF X+ at 50% for 2.0s (invokes IK each 10ms)
    _send_udp(host, port, "CARTJOG|TRF|X+|50|2.0")

    # Guard for execution + scheduling jitter
    time.sleep(2.2)

    stats1 = _request_loop_stats(host, port, timeout=2.0)
    assert stats1 is not None, "GET_LOOP_STATS failed after command"
    t1 = time.monotonic()

    dt = t1 - t0
    assert dt > 0, f"Non-positive Δt={dt:.6f}s"
    delta_loops = int(stats1.get("loop_count", 0)) - int(stats0.get("loop_count", 0))
    hz = delta_loops / dt if dt > 0 else 0.0
    assert (
        hz >= 95.0
    ), f"IK (CARTJOG) effective loop rate too low: {hz:.2f} Hz (dt={dt:.3f}s, Δloops={delta_loops})"
