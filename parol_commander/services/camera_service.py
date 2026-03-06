"""Camera feed service for gripper panel live view.

Captures frames from a video device and streams them as MJPEG via a
``multipart/x-mixed-replace`` HTTP endpoint.  The browser renders the
stream natively in an ``<img>`` tag — no JavaScript polling required.

On Linux the service tries ``linuxpy`` first for zero-copy MJPEG
passthrough (raw JPEG frames straight from v4l2, no decode+re-encode).
Falls back to OpenCV on other platforms or when ``linuxpy`` is
unavailable.

Typical workflow for AI annotations:
  physical webcam → user's overlay/analysis script → pyvirtualcam output
  → Web Commander reads the virtual camera device
"""

from __future__ import annotations

import asyncio
import base64
import logging
import sys
from typing import Protocol

from fastapi import Response
from starlette.responses import StreamingResponse

from nicegui import app as ng_app, run

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Placeholder
# ---------------------------------------------------------------------------

_BLACK_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAAXNSR0IArs4c6Q"
    "AAAA1JREFUGFdjYGBg+A8AAQQBAHAgZQsAAAAASUVORK5CYII="
)
_PLACEHOLDER = Response(content=_BLACK_1PX, media_type="image/png")

_STREAM_FPS = 30
_STREAM_BOUNDARY = b"frame"


# ---------------------------------------------------------------------------
# Capture backends
# ---------------------------------------------------------------------------


class CaptureBackend(Protocol):
    """Minimal interface for a camera capture backend."""

    def open(self, device: int | str, width: int, height: int) -> bool: ...
    def read_frame(self) -> bytes | None: ...
    def close(self) -> None: ...


class LinuxpyBackend:
    """Zero-copy MJPEG capture via linuxpy (v4l2).

    Frames come straight from the kernel as JPEG — no decode or
    re-encode step.  Only available on Linux.
    """

    def __init__(self) -> None:
        self._capture = None  # linuxpy.video.device.VideoCapture

    def open(self, device: int | str, width: int, height: int) -> bool:
        try:
            from linuxpy.video.device import Device, VideoCapture
        except ImportError:
            log.debug("linuxpy not installed — skipping v4l2 backend")
            return False

        if not isinstance(device, int):
            log.debug("linuxpy requires integer device index, got %s", type(device))
            return False

        try:
            cap = VideoCapture(Device(f"/dev/video{device}"))
            cap.open()
            cap.set_format(width, height, "MJPG")
            self._capture = cap
            log.info(
                "linuxpy v4l2 backend opened /dev/video%d (%dx%d MJPG)",
                device,
                width,
                height,
            )
            return True
        except Exception:
            log.debug("linuxpy failed to open device %s", device, exc_info=True)
            if self._capture is not None:
                try:
                    self._capture.close()
                except Exception:
                    pass
                self._capture = None
            return False

    def read_frame(self) -> bytes | None:
        """Read one MJPEG frame (blocking)."""
        if self._capture is None:
            return None
        try:
            for frame in self._capture:
                return bytes(frame.data)
        except Exception:
            log.debug("linuxpy read_frame error", exc_info=True)
        return None

    def close(self) -> None:
        if self._capture is not None:
            try:
                self._capture.close()
            except Exception:
                pass
            self._capture = None


class OpenCVBackend:
    """Fallback backend using OpenCV.  Decodes + re-encodes to JPEG."""

    def __init__(self) -> None:
        self._cap = None  # cv2.VideoCapture

    def open(self, device: int | str, width: int, height: int) -> bool:
        try:
            import cv2
        except ImportError:
            log.warning("opencv-python-headless not installed — camera disabled")
            return False

        cap = cv2.VideoCapture(device)
        if not cap.isOpened():
            log.warning("OpenCV failed to open camera device %s", device)
            cap.release()
            return False

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap = cap
        log.info("OpenCV backend opened device %s (%dx%d)", device, width, height)
        return True

    def read_frame(self) -> bytes | None:
        """Read one frame and return JPEG bytes (blocking)."""
        import cv2

        if self._cap is None or not self._cap.isOpened():
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CameraService:
    """Manages a single camera and caches the latest JPEG frame."""

    def __init__(self) -> None:
        self._backend: CaptureBackend | None = None
        self._latest_jpeg: bytes = _BLACK_1PX
        self._active: bool = False
        self._capture_task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self._active

    def start(self, device: int | str, width: int = 640, height: int = 480) -> None:
        """Open a camera device and begin capturing."""
        self.stop()

        backend: CaptureBackend | None = None

        # Try linuxpy first on Linux
        if sys.platform == "linux" and isinstance(device, int):
            candidate = LinuxpyBackend()
            if candidate.open(device, width, height):
                backend = candidate

        # Fall back to OpenCV
        if backend is None:
            candidate_cv = OpenCVBackend()
            if candidate_cv.open(device, width, height):
                backend = candidate_cv

        if backend is None:
            log.warning("No camera backend could open device %s", device)
            return

        self._backend = backend
        self._active = True
        self._capture_task = asyncio.get_event_loop().create_task(self._capture_loop())
        log.info("Camera started on device %s", device)

    def stop(self) -> None:
        """Release the camera device and stop the capture loop."""
        self._active = False
        if self._capture_task is not None:
            self._capture_task.cancel()
            self._capture_task = None
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        self._latest_jpeg = _BLACK_1PX

    def get_latest_frame(self) -> bytes:
        """Return the most recently captured JPEG (non-blocking)."""
        return self._latest_jpeg

    async def _capture_loop(self) -> None:
        """Background task: read frames from the backend at ~30 fps."""
        interval = 1.0 / _STREAM_FPS
        try:
            while self._active and self._backend is not None:
                frame = await run.io_bound(self._backend.read_frame)
                if frame is not None:
                    self._latest_jpeg = frame
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        except Exception:
            log.error("Capture loop crashed", exc_info=True)
            self._active = False


# Module-level singleton
camera_service = CameraService()


# ---------------------------------------------------------------------------
# Device enumeration (still uses OpenCV — works cross-platform)
# ---------------------------------------------------------------------------


def enumerate_video_devices(max_check: int = 10) -> list[dict[str, int | str]]:
    """Probe video device indices that can be opened by OpenCV.

    Returns a list of ``{"index": int, "label": str}`` dicts.
    """
    devices: list[dict[str, int | str]] = []
    try:
        import cv2
    except ImportError:
        return devices
    for i in range(max_check):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            devices.append({"index": i, "label": f"Camera {i}"})
            cap.release()
    return devices


# ---------------------------------------------------------------------------
# FastAPI endpoints
# ---------------------------------------------------------------------------


async def _mjpeg_generator():
    """Yield MJPEG multipart frames at ~30 fps."""
    interval = 1.0 / _STREAM_FPS
    try:
        while camera_service.active:
            jpeg = camera_service.get_latest_frame()
            yield (
                b"--" + _STREAM_BOUNDARY + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n"
                b"\r\n" + jpeg + b"\r\n"
            )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        return


@ng_app.get("/tool/camera/stream", response_model=None)
async def _tool_camera_stream():
    """Serve the camera feed as an MJPEG multipart stream."""
    if not camera_service.active:
        return _PLACEHOLDER
    return StreamingResponse(
        _mjpeg_generator(),
        media_type=f"multipart/x-mixed-replace; boundary={_STREAM_BOUNDARY.decode()}",
        headers={"Cache-Control": "no-cache"},
    )


@ng_app.get("/tool/camera/frame")
async def _tool_camera_frame() -> Response:
    """Serve a single JPEG snapshot."""
    if not camera_service.active:
        return _PLACEHOLDER
    return Response(content=camera_service.get_latest_frame(), media_type="image/jpeg")
