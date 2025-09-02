from __future__ import annotations

import pytest

from tests.utils.udp_ack import make_cmd_id, send_udp_cmd, wait_for_ack_statuses


@pytest.mark.integration
def test_headless_movejoint_loop_frequency(headless_server, ack_listener):
    """
    Launch the real headless server and verify effective loop Hz via MULTIJOG with duration=2.0s.
    We derive N = int(2.0 / 0.01) steps and compute Hz = N / (t_completed - t_executing).
    Assert Hz >= 100 (with small tolerance for scheduler jitter).
    """
    stop, q = ack_listener  # fixture provides (stop_fn, queue)

    cmd_id = make_cmd_id()
    # MULTIJOG|joints_csv|speeds_csv|duration ; jog J1+ at 10% for 2.0s (no IK required)
    payload = f"{cmd_id}|MULTIJOG|0|10|2.0"
    send_udp_cmd("127.0.0.1", 5001, payload)

    wanted = {"EXECUTING", "COMPLETED"}
    got = wait_for_ack_statuses(q, cmd_id, wanted=wanted, timeout=15.0)
    missing = wanted - set(got.keys())
    assert not missing, f"Missing ACK statuses: {missing}; got={list(got.keys())}"

    dt = got["COMPLETED"].t - got["EXECUTING"].t
    assert dt > 0, f"Non-positive execution time Δt={dt:.6f}s"

    steps = int(2.0 / 0.01)  # INTERVAL_S=0.01
    hz = steps / dt
    assert hz >= 95.0, f"Effective loop rate too low: {hz:.2f} Hz (dt={dt:.3f}s, steps={steps})"

@pytest.mark.integration
def test_headless_cartjog_loop_frequency(headless_server, ack_listener):
    """
    Launch the real headless server with fake-serial enabled and verify effective loop Hz
    while running an IK-driven command (CARTJOG) for 2.0s. We derive N = int(2.0 / 0.01) steps
    and compute Hz = N / (t_completed - t_executing). Assert Hz >= 95 to allow scheduler jitter.
    """
    stop, q = ack_listener  # fixture provides (stop_fn, queue)

    cmd_id = make_cmd_id()
    # CARTJOG|frame|axis|speed|duration ; TRF X+ at 50% for 2.0s (invokes IK each 10ms)
    payload = f"{cmd_id}|CARTJOG|TRF|X+|50|2.0"
    send_udp_cmd("127.0.0.1", 5001, payload)

    wanted = {"EXECUTING", "COMPLETED"}
    got = wait_for_ack_statuses(q, cmd_id, wanted=wanted, timeout=20.0)
    missing = wanted - set(got.keys())
    assert not missing, f"Missing ACK statuses: {missing}; got={list(got.keys())}"

    dt = got["COMPLETED"].t - got["EXECUTING"].t
    assert dt > 0, f"Non-positive execution time Δt={dt:.6f}s"

    steps = int(2.0 / 0.01)  # INTERVAL_S=0.01
    hz = steps / dt
    assert hz >= 95.0, f"IK (CARTJOG) effective loop rate too low: {hz:.2f} Hz (dt={dt:.3f}s, steps={steps})"