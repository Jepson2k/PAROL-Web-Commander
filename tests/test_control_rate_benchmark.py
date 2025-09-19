from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("PAROL6_RUN_BENCHMARKS", "0").strip() not in {"1", "true", "yes", "on"},
    reason="Set PAROL6_RUN_BENCHMARKS=1 to run the control loop benchmark.",
)


def _find_controller_script() -> Path:
    """
    Locate the headless controller entrypoint (controller.py).
    Mirrors logic used in tests/conftest.py to be resilient to layout.
    """
    repo_root = Path(__file__).resolve().parent.parent
    candidates = [
        repo_root
        / "external"
        / "PAROL6-python-API"
        / "parol6"
        / "server"
        / "controller.py",
        repo_root
        / "external"
        / "PAROL6-python-API"
        / "parol6"
        / "server"
        / "headless_commander.py",
        repo_root / "PAROL6-python-API" / "parol6" / "server" / "controller.py",
        repo_root / "PAROL6-python-API" / "parol6" / "server" / "headless_commander.py",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "Missing headless server. Checked paths:\n"
        + "\n".join(str(p) for p in candidates)
    )


async def _load_tasks(client, duration_s: float) -> None:
    """
    Generate async UDP load against the controller while motion runs.
    - Mix of queries to exercise server under non-trivial traffic.
    """
    t_end = time.time() + duration_s
    # Cadences (seconds)
    cadence_ping = 0.01
    cadence_angles = 0.02
    cadence_speeds = 0.02
    cadence_status = 0.05

    async def _every(period_s: float, coro_factory):
        next_t = time.time()
        while time.time() < t_end:
            try:
                await coro_factory()
            except Exception:
                # Best-effort: ignore transient errors/timeouts
                pass
            next_t += period_s
            sleep_for = max(0.0, next_t - time.time())
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

    async def _ping():
        await client.ping()

    async def _angles():
        await client.get_angles()

    async def _speeds():
        await client.get_speeds()

    async def _status():
        await client.get_status()

    await asyncio.gather(
        _every(cadence_ping, _ping),
        _every(cadence_angles, _angles),
        _every(cadence_speeds, _speeds),
        _every(cadence_status, _status),
    )


def _request_loop_stats(
    host: str = "127.0.0.1", port: int = 5001, timeout: float = 1.0
) -> dict | None:
    """
    Fire a GET_LOOP_STATS query and parse LOOP_STATS|{json} response.
    Returns parsed dict or None on failure.
    """
    import socket  # local import to keep test module self-contained

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
    """Fire-and-forget UDP datagram with given payload string."""
    import socket

    data = payload.encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(data, (host, port))


def _run_bench_for_rate(rate_hz: int, duration_s: float = 2.0) -> Dict:
    """
    Launch server in simulation mode at specified control Hz, run motion under async load,
    measure effective loop Hz using GET_LOOP_STATS delta (no ACK dependency).
    """
    server_script = _find_controller_script()
    env = os.environ.copy()
    env["PAROL6_NOAUTOHOME"] = "1"
    env["PAROL_LOG_LEVEL"] = "WARNING"
    env["PAROL6_FAKE_SERIAL"] = "1"
    env["PAROL6_CONTROL_RATE_HZ"] = str(rate_hz)

    # Spawn server process
    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        cwd=str(server_script.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1,
    )
    # Give the server time to bind sockets
    time.sleep(3)

    try:
        # Start async load in background thread
        from parol6.client.async_client import AsyncRobotClient

        def _async_worker():
            try:
                asyncio.run(
                    _load_tasks(
                        AsyncRobotClient(host="127.0.0.1", port=5001), duration_s
                    )
                )
            except Exception:
                # Ignore; this is best-effort load
                pass

        t_load = threading.Thread(target=_async_worker, name="bench-load", daemon=True)
        t_load.start()

        # Loop stats before
        stats0 = _request_loop_stats("127.0.0.1", 5001, timeout=2.0)
        if not stats0:
            return {"rate_hz": rate_hz, "error": "GET_LOOP_STATS failed initially"}

        t0 = time.monotonic()

        # Build and send MULTIJOG (jog J1+ at 10% for duration_s)
        message = f"MULTIJOG|0|10|{duration_s:.3f}"
        _send_udp("127.0.0.1", 5001, message)

        # Guard for execution + scheduling jitter
        time.sleep(duration_s + 0.25)

        stats1 = _request_loop_stats("127.0.0.1", 5001, timeout=2.0)
        if not stats1:
            return {"rate_hz": rate_hz, "error": "GET_LOOP_STATS failed after command"}

        t1 = time.monotonic()

        dt = t1 - t0
        if dt <= 0:
            return {
                "rate_hz": rate_hz,
                "error": f"Non-positive timing window dt={dt:.6f}s",
            }

        delta_loops = int(stats1.get("loop_count", 0)) - int(
            stats0.get("loop_count", 0)
        )
        effective_hz = delta_loops / dt if dt > 0 else 0.0

        return {
            "rate_hz": rate_hz,
            "effective_hz": effective_hz,
            "dt_window_s": dt,
            "delta_loops": delta_loops,
            "duration_cmd_s": duration_s,
        }

    finally:
        # Stop server process
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
def test_control_loop_benchmark_under_load(tmp_path: Path) -> None:
    """
    Benchmark the control-loop under load using the simulator and AsyncRobotClient traffic.
    Prints a summary and optionally writes results to tests/bench_results/*.json if PAROL6_BENCH_WRITE=1.
    This test does not assert strict thresholds to avoid nondeterministic failures; it reports results.
    """
    rates = [100, 200, 300, 400, 500, 1000]

    results: List[Dict] = []
    for hz in rates:
        res = _run_bench_for_rate(hz, duration_s=2.0)
        results.append(res)

    # Print human-readable summary
    print("\nControl Loop Benchmark (under load):")
    for r in results:
        if "error" in r and r["error"]:
            print(f"  {r['rate_hz']:>4} Hz -> ERROR: {r['error']}")
        else:
            print(
                f"  {r['rate_hz']:>4} Hz -> effective {r['effective_hz']:.2f} Hz "
                f"(dt={r['dt_window_s']:.3f}s, Δloops={r['delta_loops']})"
            )

    # Optionally persist results
    if os.getenv("PAROL6_BENCH_WRITE", "0").strip() in {"1", "true", "yes", "on"}:
        bench_dir = Path(__file__).resolve().parent / "bench_results"
        bench_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = bench_dir / f"control_loop_bench_{ts}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump({"results": results}, f, indent=2)
        print(f"Wrote benchmark results to {out_path}")
