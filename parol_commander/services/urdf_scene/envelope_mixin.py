"""
Envelope Mixin for UrdfScene.

Provides workspace envelope visualization:
- Convex hull mesh rendering (cached as STL)
- Proximity clipping planes
- Tool offset adjustments
- Workspace envelope calculation with caching
"""

import hashlib
import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from nicegui import app, ui, run
from nicegui.events import SceneClipPlane

from parol_commander.common.theme import SceneColors
from parol_commander.state import simulation_state, robot_state


logger = logging.getLogger(__name__)


# Default sample count for envelope generation (used to find max reach)
# 500k samples → ~9 samples per joint (9^6 = 531441 grid points)
DEFAULT_ENVELOPE_SAMPLES = 500000

# Envelope proximity clipping configuration
ENVELOPE_CAP_DEPTH = 0.08  # 80mm visible cap depth on the sphere
ENVELOPE_PROXIMITY_THRESHOLD = 0.10  # 100mm from boundary triggers display

# Cache directory and file paths
CACHE_DIR = Path.home() / ".parol-commander"
HULL_STL_FILENAME = "workspace_hull.stl"
HULL_STL_PATH = CACHE_DIR / HULL_STL_FILENAME

# Storage key for hull cache metadata
STORAGE_KEY = "workspace_hull_cache"


# -----------------------------------------------------------------------------
# WorkspaceEnvelope class (generates convex hull with caching)
# -----------------------------------------------------------------------------


def _compute_cache_key(
    tool_offset_z: float = 0.0,
    joint_limits_rad: np.ndarray | None = None,
) -> str:
    """Compute cache key from joint limits and tool offset.

    Args:
        tool_offset_z: Tool TCP Z offset in meters
        joint_limits_rad: Joint limits in radians, shape (num_joints, 2).
            If None, reads from ui_state.robot.
    """
    try:
        if joint_limits_rad is None:
            from parol_commander.state import ui_state

            joint_limits_rad = ui_state.active_robot.joints.limits.position.rad
        data = joint_limits_rad.tobytes() + np.array([tool_offset_z]).tobytes()
        return hashlib.md5(data).hexdigest()[:12]
    except Exception:
        return ""


def _save_hull_as_stl(vertices: np.ndarray, faces: np.ndarray, path: Path) -> bool:
    """Save convex hull as STL file.

    Args:
        vertices: Hull vertex positions, shape (N, 3)
        faces: Triangle face indices, shape (F, 3)
        path: Output STL file path

    Returns:
        True if successful
    """
    try:
        from stl import mesh as stl_mesh

        # Ensure cache directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Create mesh from hull data using vectorized indexing
        hull_mesh = stl_mesh.Mesh(np.zeros(len(faces), dtype=stl_mesh.Mesh.dtype))
        hull_mesh.vectors = vertices[faces]  # Advanced indexing: (F, 3, 3)

        hull_mesh.save(str(path))
        logger.info("Saved workspace hull STL to %s", path)
        return True
    except Exception as e:
        logger.error("Failed to save hull STL: %s", e)
        return False


class WorkspaceEnvelope:
    """Calculates workspace envelope using convex hull with STL caching."""

    def __init__(self):
        self.max_reach: float = 0.0  # Maximum reach radius in meters
        self.stl_url: str = ""  # URL to serve the STL file
        self._generated = False
        self._generating = False
        self._current_tool_offset_z: float = 0.0
        self._static_files_registered = False

    def _get_hull_params(self) -> tuple[str | None, list | None]:
        """Get URDF path and joint limits from the active robot."""
        from parol_commander.state import ui_state

        if ui_state.robot is None:
            return None, None
        urdf_path = ui_state.active_robot.urdf_path
        joint_limits_rad = ui_state.active_robot.joints.limits.position.rad.tolist()
        return urdf_path, joint_limits_rad

    def _ensure_static_files_registered(self) -> None:
        """Register static files directory for serving STL.

        Note: We always attempt registration because NiceGUI test framework
        resets the app between tests, clearing routes but not our flag.
        """
        if not CACHE_DIR.exists():
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            app.add_static_files("/parol-commander-cache", str(CACHE_DIR))
            self._static_files_registered = True
            logger.debug(
                "Registered static files: /parol-commander-cache -> %s", CACHE_DIR
            )
        except ValueError:
            # Route already registered (NiceGUI raises ValueError for duplicates)
            self._static_files_registered = True

    def _get_stl_url(self) -> str:
        """Get URL for the cached STL file."""
        return f"/parol-commander-cache/{HULL_STL_FILENAME}"

    def _load_from_cache(self, tool_offset_z: float) -> bool:
        """Try to load hull from cache.

        Args:
            tool_offset_z: Current tool Z offset in meters

        Returns:
            True if cache was valid and loaded
        """
        try:
            cache = app.storage.general.get(STORAGE_KEY)
            if not cache:
                logger.debug("No hull cache found")
                return False

            # Check cache key matches current config
            current_key = _compute_cache_key(tool_offset_z)
            if cache.get("cache_key") != current_key:
                logger.info(
                    "Hull cache key mismatch (cached=%s, current=%s), will regenerate",
                    cache.get("cache_key"),
                    current_key,
                )
                return False

            # Check STL file exists
            if not HULL_STL_PATH.exists():
                logger.info("Hull STL file missing, will regenerate")
                return False

            # Load cached values
            self.max_reach = cache.get("max_reach", 0.0)
            self._current_tool_offset_z = tool_offset_z
            self._ensure_static_files_registered()
            self.stl_url = self._get_stl_url()
            self._generated = True

            logger.info(
                "Loaded workspace hull from cache: max_reach=%.4fm", self.max_reach
            )
            return True
        except Exception as e:
            logger.warning("Failed to load hull from cache: %s", e)
            return False

    def _save_to_cache(self, max_reach: float, tool_offset_z: float) -> None:
        """Save hull metadata to cache."""
        try:
            cache_key = _compute_cache_key(tool_offset_z)
            app.storage.general[STORAGE_KEY] = {
                "cache_key": cache_key,
                "max_reach": max_reach,
                "tool_offset_z": tool_offset_z,
            }
            logger.debug("Saved hull cache metadata: key=%s", cache_key)
        except Exception as e:
            logger.warning("Failed to save hull cache: %s", e)

    def generate(
        self,
        samples: int = DEFAULT_ENVELOPE_SAMPLES,
        tool_offset_z: float = 0.0,
    ) -> bool:
        """Start workspace generation in background (non-blocking).

        Checks cache first, only regenerates if needed.

        Args:
            samples: Number of random configurations to sample
            tool_offset_z: Tool TCP Z offset in meters

        Returns:
            True if generation started or already complete
        """
        # Try loading from cache first
        if self._load_from_cache(tool_offset_z):
            return True

        if self._generated:
            return True

        if self._generating:
            logger.info("Workspace generation already in progress")
            return True

        # Skip actual generation in tests (PAROL_SKIP_ENVELOPE=1)
        # Check after _generated/_generating so tests can still verify those code paths
        if os.environ.get("PAROL_SKIP_ENVELOPE"):
            logger.debug("Skipping workspace hull generation (PAROL_SKIP_ENVELOPE)")
            return False

        self._generating = True
        self._current_tool_offset_z = tool_offset_z
        logger.info(
            "Starting background workspace hull generation with %d samples...", samples
        )

        urdf_path, joint_limits_rad = self._get_hull_params()
        if urdf_path is None or joint_limits_rad is None:
            logger.warning("Cannot generate hull: no profile loaded")
            self._generating = False
            return False

        async def start_background_generation():
            result = None
            try:
                result = await run.cpu_bound(
                    _generate_hull_cpu_bound,
                    samples,
                    tool_offset_z,
                    urdf_path,
                    joint_limits_rad,
                )
            except Exception as e:
                # Fallback to sync in-process generation when subprocess fails
                # (common in test environments where process pool is unavailable)
                logger.warning("Subprocess hull generation failed (%s), using sync", e)
                try:
                    result = _generate_hull_cpu_bound(
                        samples,
                        tool_offset_z,
                        urdf_path,
                        joint_limits_rad,
                    )
                except Exception as e2:
                    logger.error("Sync hull generation also failed: %s", e2)

            try:
                if result is not None:
                    self.max_reach = result["max_reach"]
                    vertices = np.array(result["vertices"])
                    faces = np.array(result["faces"])

                    # Save STL file
                    if _save_hull_as_stl(vertices, faces, HULL_STL_PATH):
                        self._ensure_static_files_registered()
                        self.stl_url = self._get_stl_url()
                        self._save_to_cache(self.max_reach, tool_offset_z)
                        self._generated = True
                        logger.info(
                            "Workspace hull generation complete: "
                            "max_reach=%.4fm, %d triangles",
                            self.max_reach,
                            len(faces),
                        )
                        await simulation_state.notify_changed()
                    else:
                        logger.warning("Failed to save hull STL")
                else:
                    logger.warning("Workspace generation returned no data")
            finally:
                self._generating = False

        ui.timer(0.0, start_background_generation, once=True)
        return True

    def generate_sync(
        self,
        samples: int = DEFAULT_ENVELOPE_SAMPLES,
        tool_offset_z: float = 0.0,
    ) -> bool:
        """Generate workspace data synchronously (blocking).

        Use this for testing or when background execution isn't needed.

        Args:
            samples: Number of random configurations to sample
            tool_offset_z: Tool TCP Z offset in meters

        Returns:
            True if generation successful
        """
        # Try loading from cache first
        if self._load_from_cache(tool_offset_z):
            return True

        if self._generated:
            return True

        if self._generating:
            logger.info("Workspace generation already in progress")
            return False

        self._generating = True
        self._current_tool_offset_z = tool_offset_z
        logger.info(
            "Generating workspace hull synchronously with %d samples...", samples
        )

        urdf_path, joint_limits_rad = self._get_hull_params()
        if urdf_path is None or joint_limits_rad is None:
            logger.warning("Cannot generate hull: no profile loaded")
            self._generating = False
            return False

        try:
            result = _generate_hull_cpu_bound(
                samples,
                tool_offset_z,
                urdf_path,
                joint_limits_rad,
            )
            if result is not None:
                self.max_reach = result["max_reach"]
                vertices = np.array(result["vertices"])
                faces = np.array(result["faces"])

                if _save_hull_as_stl(vertices, faces, HULL_STL_PATH):
                    self._ensure_static_files_registered()
                    self.stl_url = self._get_stl_url()
                    self._save_to_cache(self.max_reach, tool_offset_z)
                    self._generated = True
                    logger.info(
                        "Workspace hull generation complete: max_reach=%.4fm",
                        self.max_reach,
                    )
                    return True
                else:
                    logger.warning("Failed to save hull STL")
                    return False
            else:
                logger.warning("Workspace generation returned no data")
                return False
        except Exception as e:
            logger.error("Workspace generation failed: %s", e)
            return False
        finally:
            self._generating = False

    def reset(self) -> None:
        """Reset envelope data to allow regeneration."""
        self.max_reach = 0.0
        self.stl_url = ""
        self._generated = False
        self._generating = False

    def invalidate_cache(self) -> None:
        """Invalidate cache and delete STL file."""
        try:
            if STORAGE_KEY in app.storage.general:
                del app.storage.general[STORAGE_KEY]
            if HULL_STL_PATH.exists():
                HULL_STL_PATH.unlink()
            logger.info("Invalidated workspace hull cache")
        except Exception as e:
            logger.warning("Failed to invalidate cache: %s", e)
        self.reset()

    def get_radius_with_tool_offset(self, tool_offset_z: float) -> float:
        """Get effective workspace radius including tool offset.

        Args:
            tool_offset_z: Tool Z offset in meters (can be negative)

        Returns:
            Effective radius = max_reach + abs(tool_offset_z)
        """
        return self.max_reach + abs(tool_offset_z)

    def needs_regeneration(self, tool_offset_z: float) -> bool:
        """Check if hull needs regeneration due to tool change.

        Args:
            tool_offset_z: New tool Z offset in meters

        Returns:
            True if regeneration needed
        """
        # If generation is in progress, don't trigger another one
        if self._generating:
            return False
        if not self._generated:
            return True
        current_key = _compute_cache_key(tool_offset_z)
        cache = app.storage.general.get(STORAGE_KEY, {})
        return cache.get("cache_key") != current_key


def _generate_hull_cpu_bound(
    samples: int,
    tool_offset_z: float,
    urdf_path: str | None = None,
    joint_limits_rad: list | None = None,
) -> Optional[Dict[str, Any]]:
    """CPU-bound function to calculate workspace convex hull.

    This function runs in a separate process via run.cpu_bound.

    Args:
        samples: Number of random configurations to sample
        tool_offset_z: Tool TCP Z offset in meters
        urdf_path: Path to URDF file for creating pinokin Robot
        joint_limits_rad: Joint limits in radians, shape (num_joints, 2)

    Returns:
        Dict with max_reach, vertices, faces, or None if failed
    """
    # Configure logging in subprocess (each worker needs its own config)
    # basicConfig() won't work if handlers exist, so force-add one
    import logging as subprocess_logging
    import sys

    sub_logger = subprocess_logging.getLogger(__name__)
    if not sub_logger.handlers:
        handler = subprocess_logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            subprocess_logging.Formatter(
                "%(levelname)s %(name)s:%(filename)s:%(lineno)d %(message)s"
            )
        )
        sub_logger.addHandler(handler)
        sub_logger.setLevel(subprocess_logging.DEBUG)

    try:
        from scipy.spatial import ConvexHull
        from pinokin import Robot

        if urdf_path is None or joint_limits_rad is None:
            sub_logger.warning("urdf_path and joint_limits_rad are required")
            return None

        robot = Robot(urdf_path)
        limits_arr = np.array(joint_limits_rad)

        # Generate evenly spaced joint configurations using a grid
        samples_per_joint = max(2, int(round(samples ** (1 / 6))))
        actual_samples = samples_per_joint**6
        sub_logger.info(
            "Using %d samples per joint (%d total grid points)",
            samples_per_joint,
            actual_samples,
        )

        joint_ranges = [
            np.linspace(limits_arr[i, 0], limits_arr[i, 1], samples_per_joint)
            for i in range(6)
        ]

        grids = np.meshgrid(*joint_ranges, indexing="ij")
        q_samples = np.column_stack([g.ravel() for g in grids])

        # Calculate FK for each configuration using batch_fk
        T_list = robot.batch_fk(q_samples)  # list of 4x4 matrices

        # Extract positions (TCP positions)
        pos = np.array([T[:3, 3] for T in T_list])  # (N, 3)

        # Apply tool offset along Z axis of each TCP frame
        if tool_offset_z != 0:
            z_axes = np.array([T[:3, 2] for T in T_list])  # (N, 3)
            pos += z_axes * tool_offset_z

        # Calculate distances from origin (robot base)
        distances = np.linalg.norm(pos, axis=1)
        max_reach = float(distances.max())

        # Compute convex hull
        try:
            hull = ConvexHull(pos)
            vertices = pos.tolist()  # All points (hull uses indices into this)
            faces = hull.simplices.tolist()  # Triangle indices

            sub_logger.info(
                "Convex hull: %d hull vertices, %d faces, max_reach=%.4fm",
                len(hull.vertices),
                len(faces),
                max_reach,
            )

            return {
                "max_reach": max_reach,
                "vertices": vertices,
                "faces": faces,
            }
        except Exception as e:
            sub_logger.error("ConvexHull computation failed: %s", e)
            # Fall back to just max_reach without hull
            return None

    except ImportError as e:
        sub_logger.error("Failed to import in CPU-bound task: %s", e)
        return None
    except Exception as e:
        sub_logger.error("Workspace generation failed in CPU-bound task: %s", e)
        return None


# Singleton instance
workspace_envelope = WorkspaceEnvelope()


# -----------------------------------------------------------------------------
# EnvelopeMixin class
# -----------------------------------------------------------------------------


class EnvelopeMixin:
    """Mixin providing envelope visualization functionality for UrdfScene."""

    # These attributes are defined in the main UrdfScene class
    scene: Any
    simulation_group: Any

    def _init_envelope_state(self) -> None:
        """Initialize envelope state variables."""
        self.envelope_object: Any | None = None

        # Track envelope visibility state to avoid redundant visible() calls
        self._envelope_visible: bool = False

        # Track current tool offset for envelope calculations
        self._current_tool: str = "none"
        self._current_tool_offset_z: float = 0.0

    @staticmethod
    def _is_near_boundary(x_m: float, y_m: float, z_m: float) -> bool:
        """Check if a point (in meters) is within proximity threshold of the workspace boundary."""
        if not workspace_envelope._generated or workspace_envelope.max_reach <= 0:
            return False
        dist = math.sqrt(x_m * x_m + y_m * y_m + z_m * z_m)
        return dist >= workspace_envelope.max_reach - ENVELOPE_PROXIMITY_THRESHOLD

    def _create_envelope_object(self) -> bool:
        """Create the envelope hull STL object if not already created.

        Returns:
            True if envelope object exists (created or already existed)
        """
        if self.envelope_object:
            return True
        if not workspace_envelope.stl_url:
            return False
        try:
            with self.simulation_group:
                self.envelope_object = ui.scene.stl(
                    workspace_envelope.stl_url, wireframe=True
                ).with_name("envelope:hull")
                self.envelope_object.material(SceneColors.ENVELOPE_HEX, 0.8)
            self._envelope_visible = True
            return True
        except Exception as e:
            logging.error("Failed to create envelope hull: %s", e)
            return False

    def _update_envelope_from_robot_state(self) -> None:
        """Update envelope visibility based on current robot TCP position."""
        if not self.scene or not self.simulation_group:
            return

        # Check if scene is still valid before modifying it
        if self.scene.is_deleted:
            return

        # Only handle auto mode here - on/off modes are handled by simulation_state
        envelope_mode = simulation_state.envelope_mode
        if envelope_mode != "auto":
            return

        # Get robot TCP position (convert mm to m)
        tcp_x = robot_state.x / 1000.0
        tcp_y = robot_state.y / 1000.0
        tcp_z = robot_state.z / 1000.0

        show_envelope = self._is_near_boundary(tcp_x, tcp_y, tcp_z)

        if show_envelope:
            # Create envelope if needed
            if not self.envelope_object:
                self._create_envelope_object()
            elif not self._envelope_visible:
                self.envelope_object.visible(True)
                self._envelope_visible = True

            # Apply proximity clipping
            if self.envelope_object:
                approaching_positions = [(tcp_x, tcp_y, tcp_z)]
                clipping_planes = self._calculate_envelope_clipping_planes(
                    approaching_positions, workspace_envelope.max_reach
                )
                if clipping_planes and self.scene:
                    envelope_id = str(self.envelope_object.id)
                    self.scene.set_clipping_planes(envelope_id, clipping_planes)
        else:
            # Hide envelope
            if self.envelope_object and self._envelope_visible:
                self.envelope_object.visible(False)
                self._envelope_visible = False
                if self.scene:
                    envelope_id = str(self.envelope_object.id)
                    self.scene.clear_clipping_planes(envelope_id)

    def _update_envelope_in_simulation_view(
        self, approaching_positions: List[Tuple[float, float, float]]
    ) -> None:
        """Update envelope visibility based on simulation state.

        Called from _update_simulation_view in the main class.

        Args:
            approaching_positions: List of (x, y, z) positions approaching boundary
        """
        envelope_mode = simulation_state.envelope_mode
        show_envelope = False

        if envelope_mode == "on":
            show_envelope = True
        elif envelope_mode == "auto":
            show_envelope = len(approaching_positions) > 0
        # "off" mode: show_envelope stays False

        if show_envelope:
            # Trigger generation if needed (checks cache first)
            if not workspace_envelope._generated:
                workspace_envelope.generate(tool_offset_z=self._current_tool_offset_z)

            # Create hull mesh if ready
            if not self.envelope_object and workspace_envelope._generated:
                self._create_envelope_object()
            elif self.envelope_object and not self._envelope_visible:
                self.envelope_object.visible(True)
                self._envelope_visible = True

            # Apply proximity clipping in auto mode
            if (
                envelope_mode == "auto"
                and self.envelope_object
                and approaching_positions
            ):
                clipping_planes = self._calculate_envelope_clipping_planes(
                    approaching_positions, workspace_envelope.max_reach
                )
                if clipping_planes and self.scene:
                    envelope_id = str(self.envelope_object.id)
                    self.scene.set_clipping_planes(envelope_id, clipping_planes)
            elif envelope_mode == "on" and self.envelope_object and self.scene:
                # In "on" mode, clear any clipping to show the full hull
                envelope_id = str(self.envelope_object.id)
                self.scene.clear_clipping_planes(envelope_id)
        else:
            # Only call visible(False) if state changed
            if self.envelope_object and self._envelope_visible:
                self.envelope_object.visible(False)
                self._envelope_visible = False
                # Clear clipping planes when hiding
                if self.scene:
                    envelope_id = str(self.envelope_object.id)
                    self.scene.clear_clipping_planes(envelope_id)

    def _calculate_envelope_clipping_planes(
        self,
        approaching_positions: List[Tuple[float, float, float]],
        max_reach: float,
    ) -> List[SceneClipPlane]:
        """Calculate clipping planes to show only nearby portions of the envelope.

        For each approaching object, creates a clipping plane that reveals a
        cap of the envelope near that object.

        Args:
            approaching_positions: List of (x, y, z) positions approaching boundary
            max_reach: Maximum reach radius of the envelope

        Returns:
            List of plane definitions for set_clipping_planes()
        """
        planes: List[SceneClipPlane] = []

        for pos in approaching_positions:
            x, y, z = pos
            dist = math.sqrt(x * x + y * y + z * z)

            if dist < 1e-6:
                # Object at origin - can't determine direction, skip
                continue

            # Normalize to get direction from origin to object
            nx = x / dist
            ny = y / dist
            nz = z / dist

            # Calculate plane distance to show a cap
            # How close to boundary (0 = at boundary, positive = inside)
            distance_to_boundary = max_reach - dist

            # Adaptive cap depth: larger cap when closer to boundary
            if distance_to_boundary < 0:
                # Beyond boundary - show full cap
                cap_depth = ENVELOPE_CAP_DEPTH
            elif distance_to_boundary < ENVELOPE_PROXIMITY_THRESHOLD:
                # Within threshold - scale cap depth proportionally
                proximity_ratio = 1.0 - (
                    distance_to_boundary / ENVELOPE_PROXIMITY_THRESHOLD
                )
                cap_depth = ENVELOPE_CAP_DEPTH * proximity_ratio
            else:
                # Too far from boundary - no cap needed
                continue

            # Plane distance
            plane_d = -(max_reach - cap_depth)

            planes.append(SceneClipPlane(nx=nx, ny=ny, nz=nz, d=plane_d))

        return planes

    def _update_envelope_for_tool_change(self, tool_offset_z: float) -> None:
        """Handle tool offset change - regenerate hull if needed.

        Args:
            tool_offset_z: New tool Z offset in meters
        """
        if workspace_envelope.needs_regeneration(tool_offset_z):
            logger.info(
                "Tool offset changed to %.4fm, regenerating workspace hull",
                tool_offset_z,
            )
            # Delete current envelope object
            if self.envelope_object:
                try:
                    self.envelope_object.delete()
                except Exception:
                    pass
                self.envelope_object = None
                self._envelope_visible = False

            # Reset and regenerate
            workspace_envelope.reset()
            workspace_envelope.generate(tool_offset_z=tool_offset_z)

    def _update_envelope_radius(self) -> None:
        """Update envelope for current tool offset.

        Called when tool selection changes.
        """
        self._update_envelope_for_tool_change(self._current_tool_offset_z)
