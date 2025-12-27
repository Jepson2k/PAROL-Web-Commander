"""
Envelope Mixin for UrdfScene.

Provides workspace envelope visualization:
- Envelope sphere rendering
- Proximity clipping planes
- Tool offset adjustments
- Workspace envelope calculation
"""

import logging
import math
from typing import Any, List, Optional, Tuple, cast

import numpy as np
from nicegui import ui, run
from nicegui.events import SceneClipPlane

from parol_commander.common.theme import SceneColors
from parol_commander.state import simulation_state, robot_state


logger = logging.getLogger(__name__)


# Default sample count for envelope generation (used to find max reach)
DEFAULT_ENVELOPE_SAMPLES = 50000

# Envelope proximity clipping configuration
ENVELOPE_CAP_DEPTH = 0.08  # 80mm visible cap depth on the sphere
ENVELOPE_PROXIMITY_THRESHOLD = 0.10  # 100mm from boundary triggers display


# -----------------------------------------------------------------------------
# WorkspaceEnvelope class (generates max reach radius)
# -----------------------------------------------------------------------------


class WorkspaceEnvelope:
    """Calculates workspace envelope radius using grid sampling."""

    def __init__(self):
        self.max_reach: float = 0.0  # Maximum reach radius in meters
        self._generated = False
        self._generating = False

    def _get_robot_model(self) -> Optional[Any]:
        """Safely get the robot model, returning None if not available."""
        try:
            import parol6.PAROL6_ROBOT as PAROL6_ROBOT

            robot = getattr(PAROL6_ROBOT, "robot", None)
            if robot is None:
                logger.warning("Robot model attribute is None")
                return None
            return robot
        except ImportError as e:
            logger.error(f"Failed to import PAROL6_ROBOT: {e}")
            return None
        except AttributeError as e:
            logger.error(f"PAROL6_ROBOT module missing expected attributes: {e}")
            return None

    def _get_joint_limits(self) -> Optional[np.ndarray]:
        """Safely get joint limits, returning None if not available."""
        try:
            import parol6.PAROL6_ROBOT as PAROL6_ROBOT

            limits = getattr(PAROL6_ROBOT, "_joint_limits_radian", None)
            if limits is None:
                logger.warning("Joint limits not found in PAROL6_ROBOT")
                return None
            return np.array(limits)
        except ImportError:
            return None
        except AttributeError:
            return None

    def generate(self, samples: int = DEFAULT_ENVELOPE_SAMPLES) -> bool:
        """Start workspace generation in background (non-blocking).

        Args:
            samples: Number of random configurations to sample

        Returns:
            True if generation started or already complete, False if failed immediately
        """
        if self._generated:
            return True

        if self._generating:
            logger.info("Workspace generation already in progress")
            return True

        self._generating = True
        logger.info(
            f"Starting background workspace envelope generation with {samples} samples..."
        )

        async def start_background_generation():
            try:
                result = await run.cpu_bound(_generate_envelope_cpu_bound, samples)
                if result is not None:
                    self.max_reach = result
                    self._generated = True
                    logger.info(
                        f"Workspace generation complete: max reach = {self.max_reach:.4f}m"
                    )
                    simulation_state.notify_changed()
                else:
                    logger.warning("Workspace generation returned no data")
            except Exception as e:
                logger.error(f"Background workspace generation failed: {e}")
            finally:
                self._generating = False

        ui.timer(0.0, start_background_generation, once=True)
        return True

    def generate_sync(self, samples: int = DEFAULT_ENVELOPE_SAMPLES) -> bool:
        """Generate workspace data synchronously (blocking).

        Use this for testing or when background execution isn't needed.

        Args:
            samples: Number of random configurations to sample

        Returns:
            True if generation successful, False otherwise
        """
        if self._generated:
            return True

        if self._generating:
            logger.info("Workspace generation already in progress")
            return False

        self._generating = True
        logger.info(
            f"Generating workspace envelope synchronously with {samples} samples..."
        )

        try:
            result = _generate_envelope_cpu_bound(samples)
            if result is not None:
                self.max_reach = result
                self._generated = True
                logger.info(
                    f"Workspace generation complete: max reach = {self.max_reach:.4f}m"
                )
                return True
            else:
                logger.warning("Workspace generation returned no data")
                return False
        except Exception as e:
            logger.error(f"Workspace generation failed: {e}")
            return False
        finally:
            self._generating = False

    def reset(self) -> None:
        """Reset envelope data to allow regeneration."""
        self.max_reach = 0.0
        self._generated = False
        self._generating = False

    def get_radius_with_tool_offset(self, tool_offset_z: float = 0.0) -> float:
        """Get effective workspace radius including tool Z offset.

        Args:
            tool_offset_z: Tool TCP offset along Z axis in meters

        Returns:
            Adjusted max reach radius in meters
        """
        return self.max_reach + abs(tool_offset_z)


def _generate_envelope_cpu_bound(samples: int) -> Optional[float]:
    """CPU-bound function to calculate max workspace reach.

    This function runs in a separate process via run.cpu_bound to avoid blocking the UI.

    Args:
        samples: Number of random configurations to sample

    Returns:
        Maximum reach radius in meters, or None if generation failed
    """
    try:
        import parol6.PAROL6_ROBOT as PAROL6_ROBOT

        robot = getattr(PAROL6_ROBOT, "robot", None)
        if robot is None:
            logger.warning("Robot model attribute is None")
            return None

        limits = getattr(PAROL6_ROBOT, "_joint_limits_radian", None)
        if limits is None:
            logger.warning("Joint limits not found in PAROL6_ROBOT")
            return None

        limits_arr = np.array(limits)

        # Generate evenly spaced joint configurations using a grid
        samples_per_joint = max(2, int(round(samples ** (1 / 6))))
        actual_samples = samples_per_joint**6
        logger.info(
            f"Using {samples_per_joint} samples per joint ({actual_samples} total grid points)"
        )

        joint_ranges = [
            np.linspace(limits_arr[i, 0], limits_arr[i, 1], samples_per_joint)
            for i in range(6)
        ]

        grids = np.meshgrid(*joint_ranges, indexing="ij")
        q_samples = np.column_stack([g.ravel() for g in grids])

        # Calculate FK for each configuration
        robot_any = cast(Any, robot)
        T = robot_any.fkine(q_samples)

        # Extract positions
        pos = T.t  # (samples, 3)

        # Calculate distances from origin (robot base)
        distances = np.linalg.norm(pos, axis=1)

        # Return max reach
        return float(distances.max())

    except ImportError as e:
        logger.error(f"Failed to import PAROL6_ROBOT in CPU-bound task: {e}")
        return None
    except Exception as e:
        logger.error(f"Workspace generation failed in CPU-bound task: {e}")
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

    def _update_envelope_from_robot_state(self) -> None:
        """Update envelope visibility based on current robot TCP position."""
        if not self.scene or not self.simulation_group:
            return

        # Check if THIS scene's client still exists before modifying scene
        try:
            scene_client = self.scene._client()
            if scene_client is None or scene_client._deleted:  # pylint: disable=protected-access
                return
        except (RuntimeError, AttributeError):
            return

        # Only handle auto mode here - on/off modes are handled by simulation_state listener
        envelope_mode = simulation_state.envelope_mode
        if envelope_mode != "auto":
            return

        # Check if robot TCP is near the workspace boundary
        if not workspace_envelope._generated or workspace_envelope.max_reach <= 0:
            return

        max_reach = workspace_envelope.max_reach
        boundary_distance = max_reach - ENVELOPE_PROXIMITY_THRESHOLD

        # Get robot TCP position (convert mm to m)
        tcp_x = robot_state.x / 1000.0
        tcp_y = robot_state.y / 1000.0
        tcp_z = robot_state.z / 1000.0
        tcp_dist = math.sqrt(tcp_x * tcp_x + tcp_y * tcp_y + tcp_z * tcp_z)

        show_envelope = tcp_dist >= boundary_distance

        if show_envelope:
            # Create envelope if needed
            if not self.envelope_object and workspace_envelope.max_reach > 0:
                try:
                    effective_radius = workspace_envelope.get_radius_with_tool_offset(
                        self._current_tool_offset_z
                    )
                    with self.simulation_group:
                        self.envelope_object = ui.scene.sphere(
                            radius=effective_radius,
                            width_segments=32,
                            height_segments=32,
                            wireframe=True,
                        ).with_name("envelope:sphere")
                        self.envelope_object.material(SceneColors.ENVELOPE_HEX, 0.3)
                    self._envelope_visible = True
                except Exception as e:
                    logging.error(f"Failed to create envelope sphere: {e}")
            elif self.envelope_object and not self._envelope_visible:
                self.envelope_object.visible(True)
                self._envelope_visible = True

            # Apply proximity clipping
            if self.envelope_object:
                effective_radius = workspace_envelope.get_radius_with_tool_offset(
                    self._current_tool_offset_z
                )
                approaching_positions = [(tcp_x, tcp_y, tcp_z)]
                clipping_planes = self._calculate_envelope_clipping_planes(
                    approaching_positions, effective_radius
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
            approaching_positions: List of (x, y, z) positions approaching the boundary
        """
        envelope_mode = simulation_state.envelope_mode
        show_envelope = False

        if envelope_mode == "on":
            show_envelope = True
        elif envelope_mode == "auto":
            show_envelope = len(approaching_positions) > 0
        # "off" mode: show_envelope stays False

        if show_envelope:
            if not workspace_envelope._generated:
                workspace_envelope.generate()

            # Create wireframe sphere at max reach radius (adjusted for tool offset)
            if not self.envelope_object and workspace_envelope.max_reach > 0:
                try:
                    # Calculate effective radius with tool offset
                    effective_radius = workspace_envelope.get_radius_with_tool_offset(
                        self._current_tool_offset_z
                    )
                    with self.simulation_group:
                        # Create wireframe sphere showing workspace boundary
                        self.envelope_object = ui.scene.sphere(
                            radius=effective_radius,
                            width_segments=32,
                            height_segments=32,
                            wireframe=True,
                        ).with_name("envelope:sphere")
                        self.envelope_object.material(SceneColors.ENVELOPE_HEX, 0.3)
                    self._envelope_visible = True
                    logging.debug(
                        f"Created envelope sphere with radius {effective_radius:.3f}m "
                        f"(tool offset: {self._current_tool_offset_z:.3f}m)"
                    )
                except Exception as e:
                    logging.error(f"Failed to create envelope sphere: {e}")
            elif self.envelope_object:
                # Only call visible(True) if state changed
                if not self._envelope_visible:
                    self.envelope_object.visible(True)
                    self._envelope_visible = True

            # Apply proximity clipping in auto mode
            if (
                envelope_mode == "auto"
                and self.envelope_object
                and approaching_positions
            ):
                effective_radius = workspace_envelope.get_radius_with_tool_offset(
                    self._current_tool_offset_z
                )
                clipping_planes = self._calculate_envelope_clipping_planes(
                    approaching_positions, effective_radius
                )
                if clipping_planes and self.scene:
                    envelope_id = str(self.envelope_object.id)
                    self.scene.set_clipping_planes(envelope_id, clipping_planes)
            elif envelope_mode == "on" and self.envelope_object and self.scene:
                # In "on" mode, clear any clipping to show the full sphere
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
        """Calculate clipping planes to show only nearby portions of the envelope sphere.

        For each approaching object, creates a clipping plane that reveals a spherical
        cap of the envelope near that object. Objects farther from the boundary get
        smaller visible caps.

        Args:
            approaching_positions: List of (x, y, z) positions approaching the boundary
            max_reach: Maximum reach radius of the envelope sphere

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

            # Calculate plane distance to show a spherical cap
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

    def _update_envelope_radius(self) -> None:
        """Update envelope sphere radius based on current tool offset."""
        if not self.envelope_object:
            return

        # Calculate effective radius with tool offset
        effective_radius = workspace_envelope.get_radius_with_tool_offset(
            self._current_tool_offset_z
        )

        if effective_radius > 0:
            # Delete old sphere and create new one with updated radius
            try:
                self.envelope_object.delete()
                self.envelope_object = None
                self._envelope_visible = False
                # Will be recreated on next _update_simulation_view call
                simulation_state.notify_changed()
            except Exception as e:
                logging.warning(f"Failed to update envelope radius: {e}")
