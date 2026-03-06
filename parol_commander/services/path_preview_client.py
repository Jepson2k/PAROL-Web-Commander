"""
Path preview client for offline simulation and visualization.

Wraps a backend's DryRunRobotClient with visualization
metadata collection (path segments, TARGET markers, colors).
"""

import inspect
import linecache
import logging
import re
from typing import Any

import numpy as np

from waldoctl import DryRunResult

from parol_commander.common.theme import get_color_for_move_type
from parol_commander.state import ToolAction

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for performance
_TARGET_MARKER_RE = re.compile(r"#\s*TARGET:(\w+)")
_LITERAL_LIST_RE = re.compile(
    r"(?:moveJ|moveL|moveC|moveS|moveP)\s*\(\s*\["
    r"\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
    r"(?:\s*,\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)*\s*\]"
)
_DURATION_RE = re.compile(r"duration\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")

# Methods that produce trajectory segments for visualization.
MOTION_METHODS: dict[str, str] = {
    "moveJ": "joints",
    "moveL": "cartesian",
    "moveC": "smooth_arc",
    "moveS": "smooth_spline",
    "moveP": "cartesian",
    "jogJ": "jog",
    "jogL": "jog",
    "servoJ": "jog",
    "servoL": "jog",
}

# Valid method names on the real RobotClient. Anything not in this set
# raises AttributeError so typos in user scripts fail during dry-run
# the same way they would on real hardware.


class _ToolCollectionProxy:
    """Wraps DryRunRobotClient.tool with collection + visualization metadata.

    Intercepts tool action method calls, delegates to the dry-run tool
    (which dispatches through the planner), collects the DryRunResult,
    and augments with tool visualization metadata for 3D arrow rendering.
    """

    def __init__(self, preview_client: "PathPreviewClient"):
        self._preview = preview_client

    def __getattr__(self, name: str) -> Any:
        dry_run_tool = self._preview._client.tool

        def interceptor(*args: Any, **kwargs: Any) -> Any:
            method = getattr(dry_run_tool, name)
            result = method(*args, **kwargs)
            self._preview._record_tool_action(name, args, kwargs, result)
            return result

        return interceptor


class PathPreviewClient:
    """Wraps DryRunRobotClient with visualization metadata collection.

    Delegates all commands to the backend's DryRunClient (which runs
    through the real command pipeline with ControllerState). After each
    motion, collects path segment dicts for 3D visualization.

    Methods are resolved via __getattr__:
    - Motion methods: dispatch through _client + collect visualization
    - All other methods: delegate to _client (which raises AttributeError
      for unknown names, catching typos in user scripts)
    """

    def __init__(
        self,
        dry_run_client_cls: type,
        segment_collector: list[dict] | None = None,
        target_collector: list[dict] | None = None,
        tool_action_collector: list[ToolAction] | None = None,
        initial_joints: list[float] | np.ndarray | None = None,
        tool_metadata: dict | None = None,
    ):
        self.segment_collector: list[dict] = (
            [] if segment_collector is None else segment_collector
        )
        self.target_collector: list[dict] = (
            [] if target_collector is None else target_collector
        )
        self.tool_action_collector: list[ToolAction] = (
            [] if tool_action_collector is None else tool_action_collector
        )
        self._tool_metadata = tool_metadata

        init_deg: list[float] | None = None
        if initial_joints is not None:
            init_deg = np.degrees(np.asarray(initial_joints, dtype=np.float64)).tolist()

        self._client = dry_run_client_cls(initial_joints_deg=init_deg)
        self._tool_proxy = _ToolCollectionProxy(self)
        self.last_joints_rad: list[float] | None = None
        self._blend_move_type: str = ""

        logger.debug("PathPreviewClient initialized")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._flush_blend()

    def close(self):
        self._flush_blend()

    @property
    def tool(self) -> _ToolCollectionProxy:
        """Return a proxy that delegates to DryRunRobotClient.tool and collects results."""
        return self._tool_proxy

    def _record_tool_action(
        self,
        method_name: str,
        args: tuple,
        kwargs: dict,
        result: DryRunResult | None = None,
    ) -> None:
        """Record a tool action with TCP pose for path preview visualization."""
        if self._tool_metadata is None:
            return

        line_no = self._get_caller_line_number()

        # Determine target positions from the method call
        if method_name == "set_position" and args:
            target_pos = (float(args[0]),)
        elif method_name == "open":
            target_pos = (0.0,)
        elif method_name == "close":
            target_pos = (1.0,)
        else:
            return

        # Get TCP position from the DryRunResult (preferred) or last segment
        tcp_pose = None
        if result is not None and result.tcp_poses.shape[0] > 0:
            tcp_pose = result.tcp_poses[-1, :3].tolist()
        elif self.segment_collector:
            last_seg = self.segment_collector[-1]
            if last_seg.get("points"):
                tcp_pose = last_seg["points"][-1]

        action = ToolAction(
            tcp_pose=tcp_pose,
            motions=self._tool_metadata["motions"],
            target_positions=target_pos,
            activation_type=self._tool_metadata["activation_type"],
            line_number=line_no,
            method=method_name,
        )
        self.tool_action_collector.append(action)

    def _flush_blend(self) -> None:
        """Flush pending blend buffer from the underlying dry-run client."""
        results = self._client.flush()
        for result in results:
            self._collect_from_result(result, self._blend_move_type or "joints")
        self._blend_move_type = ""

    # ---- Source introspection ----

    def _get_caller_line_number(self) -> int:
        try:
            frame = inspect.currentframe()
            while frame:
                if frame.f_code.co_filename == "simulation_script.py":
                    return frame.f_lineno
                frame = frame.f_back
        except (AttributeError, RuntimeError):
            pass
        return 0

    def _get_source_line(self, line_no: int) -> str:
        try:
            line = linecache.getline("simulation_script.py", line_no)
            if line:
                return line.strip()
        except (OSError, ValueError, IndexError):
            pass
        return ""

    def _extract_target_marker(self, line: str) -> str | None:
        match = _TARGET_MARKER_RE.search(line)
        return match.group(1) if match else None

    def _has_literal_list_args(self, line: str) -> bool:
        return bool(_LITERAL_LIST_RE.search(line))

    @staticmethod
    def _extract_requested_duration(line: str) -> float | None:
        """Extract duration=<value> from a source line. Returns None if not found or <= 0."""
        match = _DURATION_RE.search(line)
        if match:
            val = float(match.group(1))
            return val if val > 0 else None
        return None

    # ---- Segment collection ----

    def _collect_from_result(self, result: DryRunResult | None, move_type: str):
        if result is None:
            return

        if result.end_joints_rad.size > 0:
            self.last_joints_rad = result.end_joints_rad.tolist()

        if result.tcp_poses.shape[0] == 0:
            return

        line_no = self._get_caller_line_number()
        source_line = self._get_source_line(line_no)

        valid = result.valid
        has_error = result.error is not None

        end_joints = self.last_joints_rad if self.last_joints_rad else []

        estimated = result.duration if result.duration else None
        requested = self._extract_requested_duration(source_line)
        if estimated is not None and requested is not None:
            timing_feasible = estimated <= requested * 1.05
        else:
            timing_feasible = True

        if valid is not None:
            # Per-pose validity: split into runs of consecutive valid/invalid
            self._collect_validity_segments(
                result,
                valid,
                move_type,
                line_no,
                end_joints,
                estimated,
                requested,
                timing_feasible,
            )
        else:
            # All valid or all invalid (legacy path)
            is_valid = not has_error
            points = result.tcp_poses[:, :3].tolist()
            segment = {
                "points": points,
                "color": get_color_for_move_type(move_type, is_valid),
                "is_valid": is_valid,
                "line_number": line_no,
                "joints": end_joints,
                "move_type": move_type,
                "is_dashed": len(points) <= 2,
                "show_arrows": True,
                "estimated_duration": estimated,
                "requested_duration": requested,
                "timing_feasible": timing_feasible,
            }
            self.segment_collector.append(segment)

        marker_id = self._extract_target_marker(source_line)
        has_literal_args = self._has_literal_list_args(source_line)

        if has_literal_args:
            end_pose_m = result.tcp_poses[-1].copy()

            target_id = marker_id or f"auto_{line_no}"
            target = {
                "id": target_id,
                "line_number": line_no,
                "pose": end_pose_m.tolist(),
                "move_type": move_type,
                "scene_object_id": "",
            }
            self.target_collector.append(target)
            if marker_id:
                logger.debug("Created target %s at line %d", target_id, line_no)
            else:
                logger.debug("Auto-generated target %s at line %d", target_id, line_no)
        elif marker_id:
            logger.debug(
                "Skipped target %s - line has variable args (not editable)", marker_id
            )

    def _collect_validity_segments(
        self,
        result: DryRunResult,
        valid: np.ndarray,
        move_type: str,
        line_no: int,
        end_joints: list[float],
        estimated: float | None,
        requested: float | None,
        timing_feasible: bool,
    ) -> None:
        """Split a result with per-pose validity into green/red segments."""
        poses = result.tcp_poses
        n = len(valid)

        # Find runs of consecutive same-validity poses
        i = 0
        while i < n:
            run_valid = bool(valid[i])
            j = i + 1
            while j < n and bool(valid[j]) == run_valid:
                j += 1

            # Include one overlapping point at boundaries for visual continuity
            end = min(j + 1, n) if j < n else j
            start = max(i - 1, 0) if i > 0 else i
            run_poses = poses[start:end, :3].tolist()

            if len(run_poses) >= 2:
                segment = {
                    "points": run_poses,
                    "color": get_color_for_move_type(move_type, run_valid),
                    "is_valid": run_valid,
                    "line_number": line_no,
                    "joints": end_joints if j >= n else [],
                    "move_type": move_type,
                    "is_dashed": False,
                    "show_arrows": run_valid,
                    "estimated_duration": estimated if j >= n else None,
                    "requested_duration": requested if j >= n else None,
                    "timing_feasible": timing_feasible,
                }
                self.segment_collector.append(segment)

            i = j

    # ---- Explicit: home ----

    def home(self, **kw: Any) -> bool:
        self._flush_blend()
        try:
            result = self._client.home(**kw)
        except Exception as e:
            logger.warning("home failed: %s", e)
            return True
        self._collect_from_result(result, "joints")
        return True

    # ---- Dynamic dispatch ----

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        # Motion methods: dispatch through _client + collect visualization
        move_type = MOTION_METHODS.get(name)
        if move_type is not None:

            def motion_method(*args: Any, **kwargs: Any) -> bool:
                try:
                    method = getattr(self._client, name)
                    result = method(*args, **kwargs)
                    if result is None:
                        # Buffered for blending — track move_type of first buffered cmd
                        if not self._blend_move_type:
                            self._blend_move_type = move_type
                    else:
                        # Result returned (single dispatch or flushed composite)
                        mt = self._blend_move_type or move_type
                        self._blend_move_type = ""
                        self._collect_from_result(result, mt)
                    return True
                except Exception as e:
                    logger.warning("%s failed: %s", name, e)
                    return False

            return motion_method

        # All other methods: flush blend first, then delegate to backend.
        # It raises AttributeError for unknown names, catching typos.
        self._flush_blend()
        return getattr(self._client, name)


class AsyncPathPreviewClient:
    """Async wrapper around PathPreviewClient."""

    def __init__(
        self,
        dry_run_client_cls: type,
        segment_collector: list[dict] | None = None,
        target_collector: list[dict] | None = None,
        tool_action_collector: list[ToolAction] | None = None,
        initial_joints: list[float] | np.ndarray | None = None,
        tool_metadata: dict | None = None,
    ):
        self._sync_client = PathPreviewClient(
            dry_run_client_cls=dry_run_client_cls,
            segment_collector=segment_collector,
            target_collector=target_collector,
            tool_action_collector=tool_action_collector,
            initial_joints=initial_joints,
            tool_metadata=tool_metadata,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._sync_client._flush_blend()

    async def close(self):
        self._sync_client._flush_blend()

    @property
    def segment_collector(self) -> list[dict]:
        return self._sync_client.segment_collector

    @property
    def target_collector(self) -> list[dict]:
        return self._sync_client.target_collector

    @property
    def tool_action_collector(self) -> list[ToolAction]:
        return self._sync_client.tool_action_collector

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._sync_client, name)
        if callable(attr) and name != "close":

            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                return attr(*args, **kwargs)

            object.__setattr__(self, name, wrapper)
            return wrapper
        return attr
