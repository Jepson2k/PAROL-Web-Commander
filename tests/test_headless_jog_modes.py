from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.utils.udp_ack import make_cmd_id, wait_for_ack_statuses


def _spawn_server(env_overrides: dict[str, str] | None = None) -> subprocess.Popen:
    """
    Spawn the headless server in a subprocess with:
      - cwd = PAROL6-python-API
      - env: PAROL6_NOAUTOHOME=1, PAROL_LOG_LEVEL=WARNING, PAROL6_FAKE_SERIAL=1
      - plus any env_overrides provided (e.g., PAROL6_JOG_MODE)
    """
    repo_root = Path(__file__).resolve().parent.parent
    server_script = repo_root / "PAROL6-python-API" / "headless_commander.py"
    assert server_script.exists(), f"Missing headless server at {server_script}"

    env = os.environ.copy()
    env["PAROL6_NOAUTOHOME"] = "1"
    env["PAROL_LOG_LEVEL"] = "WARNING"
    env["PAROL6_FAKE_SERIAL"] = "1"
    if env_overrides:
        env.update(env_overrides)

    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        cwd=str(server_script.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1,
    )
    # Allow time to bind sockets
    time.sleep(1.5)
    return proc


def _udp_request(host: str, port: int, payload: str, expect_prefix: str, timeout: float = 1.0) -> str:
    """
    Send a UDP payload and wait for a single response that starts with expect_prefix.
    Returns the full decoded response string.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        # Let OS pick an ephemeral port; recv on same socket
        s.sendto(payload.encode("utf-8"), (host, port))
        while True:
            data, _ = s.recvfrom(8192)
            if not data:
                continue
            msg = data.decode("utf-8", errors="ignore").strip()
            if msg.startswith(expect_prefix):
                return msg


@pytest.mark.integration
def test_cartjog_stream_exec_and_timeout_zero_speed(ack_listener):
    """
    Verify CARTJOG_STREAM 'latest-wins' behavior and watchdog timeout decay to zero speed.
    - Send CARTJOG_STREAM TRF X+ at 50% with timeout 0.15s
    - Confirm we receive EXECUTING ACK
    - Immediately GET_SPEEDS and expect non-zero speeds
    - After >timeout, GET_SPEEDS should be all zeros (decayed/disabled)
    """
    proc = _spawn_server()
    try:
        stop_ack, q = ack_listener

        # Enable streaming mode first
        stream_on_id = make_cmd_id()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(f"{stream_on_id}|STREAM|ON".encode(), ("127.0.0.1", 5001))
        stream_wanted = {"COMPLETED"}
        stream_got = wait_for_ack_statuses(q, stream_on_id, wanted=stream_wanted, timeout=5.0)
        assert not (stream_wanted - set(stream_got.keys())), f"Failed to enable streaming: got={list(stream_got.keys())}"

        # Preposition: move all joints away from zero using streaming JOG, let each timeout
        for j in range(6):
            pre_id = make_cmd_id()
            # Alternate directions per joint for variety: even -> positive, odd -> negative
            idx = j if (j % 2 == 0) else (j + 6)
            pre_payload = f"{pre_id}|JOG|{idx}|30|0.10|NONE"
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(pre_payload.encode("utf-8"), ("127.0.0.1", 5001))
            pre_wanted = {"EXECUTING"}
            pre_got = wait_for_ack_statuses(q, pre_id, wanted=pre_wanted, timeout=5.0)
            pre_missing = pre_wanted - set(pre_got.keys())
            assert not pre_missing, f"Missing ACK statuses for preposition j{j+1}: {pre_missing}; got={list(pre_got.keys())}"
            # Allow motion and watchdog expiration before next joint
            time.sleep(0.12)

        cmd_id = make_cmd_id()
        # Test Jacobian jog after preposition using streaming CARTJOG
        payload = f"{cmd_id}|CARTJOG|TRF|X+|50|0.15"
        # Send stream update directly (no helper to ensure we keep socket for GET_SPEEDS separate)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(payload.encode("utf-8"), ("127.0.0.1", 5001))

        wanted = {"EXECUTING"}
        got = wait_for_ack_statuses(q, cmd_id, wanted=wanted, timeout=5.0)
        missing = wanted - set(got.keys())
        assert not missing, f"Missing ACK statuses: {missing}; got={list(got.keys())}"

        # Give a tick for application
        time.sleep(0.05)

        # Query speeds and expect some non-zero
        speeds_msg = _udp_request("127.0.0.1", 5001, "GET_SPEEDS", "SPEEDS|", timeout=2.0)
        _, csv = speeds_msg.split("|", 1)
        speeds = [int(x) for x in csv.split(",")]
        assert any(abs(v) > 0 for v in speeds), f"Expected non-zero speeds after stream set; got {speeds}"

        # Let the watchdog timeout elapse and decay to zero
        time.sleep(0.25)
        speeds_msg2 = _udp_request("127.0.0.1", 5001, "GET_SPEEDS", "SPEEDS|", timeout=2.0)
        _, csv2 = speeds_msg2.split("|", 1)
        speeds2 = [int(x) for x in csv2.split(",")]
        assert all(v == 0 for v in speeds2), f"Expected zero speeds after timeout; got {speeds2}"

    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass


@pytest.mark.integration
def test_jacobian_cartjog_stream_rotational_nonzero_speed(ack_listener):
    """
    Launch headless server with PAROL6_JOG_MODE=jacobian and stream a TRF rotational jog.
    Validate we receive EXECUTING ACK and observe non-zero feedback speeds.
    """
    proc = _spawn_server(env_overrides={"PAROL6_JOG_MODE": "jacobian"})
    try:
        stop_ack, q = ack_listener

        # Enable streaming mode first
        stream_on_id = make_cmd_id()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(f"{stream_on_id}|STREAM|ON".encode(), ("127.0.0.1", 5001))
        stream_wanted = {"COMPLETED"}
        stream_got = wait_for_ack_statuses(q, stream_on_id, wanted=stream_wanted, timeout=5.0)
        assert not (stream_wanted - set(stream_got.keys())), f"Failed to enable streaming: got={list(stream_got.keys())}"

        cmd_id = make_cmd_id()
        # Stream a rotational jog around tool Z (RZ+) using streaming CARTJOG
        payload = f"{cmd_id}|CARTJOG|TRF|RZ+|30|0.2"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(payload.encode("utf-8"), ("127.0.0.1", 5001))

        wanted = {"EXECUTING"}
        got = wait_for_ack_statuses(q, cmd_id, wanted=wanted, timeout=5.0)
        missing = wanted - set(got.keys())
        assert not missing, f"Missing ACK statuses: {missing}; got={list(got.keys())}"

        # Give a couple of ticks
        time.sleep(0.05)

        # Query speeds and expect some non-zero
        speeds_msg = _udp_request("127.0.0.1", 5001, "GET_SPEEDS", "SPEEDS|", timeout=2.0)
        _, csv = speeds_msg.split("|", 1)
        speeds = [int(x) for x in csv.split(",")]
        assert any(abs(v) > 0 for v in speeds), f"Expected non-zero speeds in jacobian mode; got {speeds}"

    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass
