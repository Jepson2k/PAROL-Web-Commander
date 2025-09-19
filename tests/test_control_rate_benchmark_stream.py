from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("PAROL6_RUN_BENCHMARKS", "0").strip() not in {"1", "true", "yes", "on"},
    reason="Set PAROL6_RUN_BENCHMARKS=1 to run the control loop stream benchmark.",
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
    Mix of queries to exercise server under non-trivial traffic.
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


def _wait_for_ping(host: str, port: int, timeout: float = 5.0) -> bool:
    """Wait until a UDP PING -> PONG succeeds or timeout."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.5)
                s.sendto(b"PING", (host, port))
                data, _ = s.recvfrom(256)
                if data.decode("utf-8", errors="ignore").strip().startswith("PONG"):
                    return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def _run_stream_bench_for_rate(
    rate_hz: int,
    duration_s: float = 2.0,
    stream_rate_hz: int = 200,
) -> Dict:
    """
    Launch server in simulation at specified control Hz, enable STREAM mode, stream JOG commands
    at stream_rate_hz while also generating background async UDP query load.
    Measure loop health by counting 'overrun' warnings emitted by the controller during the run.
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

    # Collect stdout in background
    lines: List[str] = []
    _stop_read = threading.Event()

    def _reader():
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line.rstrip("\n"))
                if _stop_read.is_set():
                    break
        except Exception:
            pass

    t_reader = threading.Thread(target=_reader, name="server-log-reader", daemon=True)
    t_reader.start()

    # Wait for server to respond to PING instead of fixed sleep
    if not _wait_for_ping("127.0.0.1", 5001, timeout=5.0):
        return {"rate_hz": rate_hz, "error": "server not ready"}

    try:
        # Background async load
        from parol6.client.async_client import AsyncRobotClient

        def _async_worker():
            try:
                asyncio.run(
                    _load_tasks(
                        AsyncRobotClient(host="127.0.0.1", port=5001), duration_s
                    )
                )
            except Exception:
                pass

        t_load = threading.Thread(
            target=_async_worker, name="bench-udp-load", daemon=True
        )
        t_load.start()

        # Enable stream mode (fire-and-forget)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(b"STREAM|ON", ("127.0.0.1", 5001))
        except Exception:
            # Continue even if STREAM toggle failed; this benchmark tolerates best-effort
            pass

        # Stream JOG updates at stream_rate_hz for duration_s
        def _stream_worker():
            end_t = time.time() + duration_s
            period = 1.0 / float(stream_rate_hz)
            next_t = time.time()
            joint = 0
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    while time.time() < end_t:
                        # Cycle joints 0..5, 50% speed, short duration jog (replaced continuously)
                        msg = f"JOG|{joint}|50|0.10|NONE"
                        try:
                            s.sendto(msg.encode("utf-8"), ("127.0.0.1", 5001))
                        except Exception:
                            pass
                        joint = (joint + 1) % 6
                        next_t += period
                        sleep_for = next_t - time.time()
                        if sleep_for > 0:
                            time.sleep(sleep_for)
            except Exception:
                pass

        t_stream = threading.Thread(
            target=_stream_worker, name="bench-stream-jog", daemon=True
        )
        t_stream.start()

        # Wait for streaming to finish
        t_stream.join(timeout=duration_s + 2.0)
        t_load.join(timeout=2.0)

        # Small grace period for final logs to flush
        time.sleep(0.3)

        # Count loop overrun warnings
        overrun_count = 0
        for ln in lines:
            # Controller logs: "Control loop overrun by X.XXXXs (target: Y.YYYYs)"
            if "Control loop overrun by" in ln:
                overrun_count += 1

        return {
            "rate_hz": rate_hz,
            "stream_rate_hz": stream_rate_hz,
            "duration_s": duration_s,
            "overrun_count": overrun_count,
            "log_lines": len(lines),
        }

    finally:
        try:
            _stop_read.set()
            # Stop server process
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass


@pytest.mark.integration
def test_control_loop_stream_benchmark_under_load(tmp_path: Path) -> None:
    """
    Benchmark the control-loop under stream-mode jogging plus async UDP load.
    Reports per-rate overrun counts as a proxy for loop stability under command churn.
    """
    rates = [100, 200, 300, 400, 500, 1000]
    stream_rate_hz = int(os.getenv("PAROL6_STREAM_RATE_HZ", "200"))

    results: List[Dict] = []
    for hz in rates:
        res = _run_stream_bench_for_rate(
            hz, duration_s=2.0, stream_rate_hz=stream_rate_hz
        )
        results.append(res)

    # Print human-readable summary
    print("\nControl Loop Stream Benchmark (under load):")
    for r in results:
        print(
            f"  {r['rate_hz']:>4} Hz -> stream {r['stream_rate_hz']:>3} Hz,"
            f" overruns={r['overrun_count']}, logs={r['log_lines']}"
        )

    # Optionally persist results
    if os.getenv("PAROL6_BENCH_WRITE", "0").strip() in {"1", "true", "yes", "on"}:
        bench_dir = Path(__file__).resolve().parent / "bench_results"
        bench_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = bench_dir / f"control_loop_bench_stream_{ts}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump({"results": results}, f, indent=2)
        print(f"Wrote benchmark results to {out_path}")
