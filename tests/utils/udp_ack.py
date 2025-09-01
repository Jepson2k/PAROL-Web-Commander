from __future__ import annotations

import contextlib
import queue
import secrets
import socket
import threading
import time
from typing import TYPE_CHECKING

from .types import AckEvent

if TYPE_CHECKING:
    from collections.abc import Iterable


def make_cmd_id() -> str:
    """Return an 8-char lowercase hex command id."""
    return secrets.token_hex(4)


def send_udp_cmd(host: str, port: int, payload: str) -> None:
    """Send a UDP datagram to host:port with the given payload string."""
    data = payload.encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(data, (host, port))


def parse_ack(msg: str) -> tuple[str, str, str] | None:
    """
    Parse ack message in format: 'ACK|<cmd_id>|<status>|<details>'.
    Returns (cmd_id, status, details) or None if not an ACK frame.
    """
    if not msg:
        return None
    parts = msg.split("|", 3)
    if len(parts) < 4:
        return None
    if parts[0] != "ACK":
        return None
    cmd_id, status, details = parts[1], parts[2], parts[3]
    return (cmd_id, status, details)


def ack_listener_start(bind_host: str = "127.0.0.1", bind_port: int = 5002):
    """
    Start a background thread that listens for ACK datagrams and enqueues AckEvent objects.
    Returns (stop_fn, q) where stop_fn() stops the listener and q is a Queue[AckEvent].
    """
    q: queue.Queue[AckEvent] = queue.Queue()
    stop_flag = threading.Event()

    def _run():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((bind_host, bind_port))
            sock.settimeout(0.25)
            while not stop_flag.is_set():
                try:
                    data, _ = sock.recvfrom(4096)
                except TimeoutError:
                    continue
                except OSError:
                    break
                t = time.monotonic()
                try:
                    msg = data.decode("utf-8", errors="ignore").strip()
                except Exception:
                    continue
                parsed = parse_ack(msg)
                if not parsed:
                    continue
                cmd_id, status, details = parsed
                try:  # noqa: SIM105
                    q.put_nowait(AckEvent(cmd_id=cmd_id, status=status, details=details, t=t))
                except Exception:
                    # Best-effort; drop if queue is full or invalid
                    pass
        finally:
            with contextlib.suppress(Exception):
                sock.close()

    th = threading.Thread(target=_run, name="ack-listener", daemon=True)
    th.start()

    def stop():
        stop_flag.set()
        # Send a dummy datagram to unblock the thread promptly
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(b"", (bind_host, bind_port))
        except Exception:
            pass
        th.join(timeout=2.0)

    return stop, q


def wait_for_ack_statuses(
    q: queue.Queue[AckEvent],
    cmd_id: str,
    wanted: Iterable[str],
    timeout: float,
) -> dict[str, AckEvent]:
    """
    Drain the queue until all wanted statuses for cmd_id are seen or timeout.
    Returns mapping status -> AckEvent (only for those seen).
    """
    deadline = time.monotonic() + timeout
    wanted_set = {str(w) for w in wanted}
    seen: dict[str, AckEvent] = {}
    while time.monotonic() < deadline and wanted_set - set(seen.keys()):
        remaining = max(0.0, deadline - time.monotonic())
        try:
            ev = q.get(timeout=min(0.25, remaining))
        except queue.Empty:
            continue
        if ev.cmd_id == cmd_id and ev.status in wanted_set and ev.status not in seen:
            seen[ev.status] = ev
    return seen
