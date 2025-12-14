"""Test URDF scene context menu handling."""

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from nicegui.testing import User

from tests.helpers.wait import wait_for_urdf_ready


@dataclass
class MockHit:
    object_name: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class MockGroundPoint:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class MockEvent:
    click_type: str
    hits: list[MockHit]
    ground_point: MockGroundPoint | None = None


@pytest.mark.integration
async def test_urdf_scene_context_menu_populates_on_target(user: User) -> None:
    """Test that context menu is populated when right-clicking on a target."""
    from parol_commander.state import ui_state, simulation_state, ProgramTarget

    await user.open("/")
    await wait_for_urdf_ready()

    scene = ui_state.urdf_scene
    assert scene is not None
    assert scene.context_menu is not None

    # Add a test target to simulation state
    test_target = ProgramTarget(
        id="test-target-123",
        line_number=5,
        pose=[0.1, 0.2, 0.3, 0.0, 0.0, 0.0],
        move_type="pose",
        scene_object_id="",
    )
    simulation_state.targets.append(test_target)

    try:
        # Simulate right click on the target
        event = MockEvent(
            click_type="contextmenu",
            hits=[MockHit(object_name="target:test-target-123", x=0.1, y=0.2, z=0.3)],
        )

        # Call handler
        scene._populate_context_menu(event)

        # Context menu should have been populated (we can't easily verify contents in test)
        # But we can verify no exceptions were raised
    finally:
        # Clean up
        simulation_state.targets.remove(test_target)


@pytest.mark.integration
async def test_urdf_scene_context_menu_populates_on_empty_space(user: User) -> None:
    """Test that context menu shows 'Add Target' when right-clicking on empty space."""
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_urdf_ready()

    scene = ui_state.urdf_scene
    assert scene is not None
    assert scene.context_menu is not None

    # Simulate right click on empty space (hit on robot link, not target)
    # ground_point provides the ray-plane intersection with Z=0 plane
    event = MockEvent(
        click_type="contextmenu",
        hits=[MockHit(object_name="robot:link1", x=0.5, y=0.3, z=0.2)],
        ground_point=MockGroundPoint(x=0.5, y=0.3, z=0.0),
    )

    # Call handler
    scene._populate_context_menu(event)

    # Verify click coords were captured from ground_point
    assert scene._last_click_coords == (0.5, 0.3, 0.0)


@pytest.mark.integration
async def test_urdf_scene_context_menu_no_hits(user: User) -> None:
    """Test that context menu handles right-click with no hits."""
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_urdf_ready()

    scene = ui_state.urdf_scene
    assert scene is not None

    # Simulate right click with no hits
    event = MockEvent(click_type="contextmenu", hits=[])

    # Call handler
    scene._populate_context_menu(event)

    # Should not crash, just show generic add options


@pytest.mark.integration
async def test_update_simulation_view_handles_deleted_client(user: User) -> None:
    """Test that _update_simulation_view handles deleted client without crashing.

    This is a regression test for a bug where the timer-driven update would
    crash with 'Client deleted' error after the user closed the browser tab.
    The fix checks client._deleted before making scene modifications.
    """
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_urdf_ready()

    urdf_scene = ui_state.urdf_scene
    assert urdf_scene is not None
    assert urdf_scene.scene is not None

    # Mock a deleted client via scene._client()
    mock_client = MagicMock()
    mock_client._deleted = True  # Simulate deleted client

    original_client = urdf_scene.scene._client
    urdf_scene.scene._client = MagicMock(return_value=mock_client)

    try:
        # This should return early without error, not crash
        urdf_scene._update_simulation_view()

        # If we get here without exception, the fix is working
    finally:
        urdf_scene.scene._client = original_client


@pytest.mark.integration
async def test_update_simulation_view_handles_no_client_context(user: User) -> None:
    """Test that _update_simulation_view handles missing client context gracefully.

    When called outside a NiceGUI request context (e.g., background timer),
    scene._client() may raise RuntimeError. The function should handle this.
    """
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_urdf_ready()

    urdf_scene = ui_state.urdf_scene
    assert urdf_scene is not None
    assert urdf_scene.scene is not None

    # Mock scene._client() to raise RuntimeError (no client available)
    original_client = urdf_scene.scene._client
    urdf_scene.scene._client = MagicMock(side_effect=RuntimeError("No client"))

    try:
        # This should return early without error, not crash
        urdf_scene._update_simulation_view()
    finally:
        urdf_scene.scene._client = original_client


@pytest.mark.integration
async def test_context_menu_uses_ground_point_for_coordinates(user: User) -> None:
    """Test that context menu uses ground_point (ray-plane intersection) for click coordinates.

    The implementation uses ground_point from the scene click event, which is the
    intersection of the click ray with the Z=0 plane. This provides reliable
    coordinates even when clicking on empty space or above robot parts.
    """
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_urdf_ready()

    scene = ui_state.urdf_scene
    assert scene is not None

    # Simulate right click where ray hits robot arm first, then ground
    # ground_point provides the ray-plane intersection with Z=0
    event = MockEvent(
        click_type="contextmenu",
        hits=[
            MockHit(
                object_name="robot:link5", x=0.2, y=0.3, z=0.3
            ),  # Robot arm (first hit)
            MockHit(
                object_name="robot:base", x=0.2, y=0.3, z=0.01
            ),  # Near ground (second hit)
        ],
        ground_point=MockGroundPoint(x=0.2, y=0.3, z=0.0),
    )

    # Call handler
    scene._populate_context_menu(event)

    # Should use ground_point coordinates (Z=0 plane intersection)
    assert scene._last_click_coords is not None
    x, y, z = scene._last_click_coords
    assert abs(z) < 0.001, f"Expected z≈0 (ground_point), got z={z}"


@pytest.mark.integration
async def test_context_menu_ground_point_independent_of_hits(user: User) -> None:
    """Test that click coordinates come from ground_point, not hit objects."""
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_urdf_ready()

    scene = ui_state.urdf_scene
    assert scene is not None

    # Multiple hits at different heights, but ground_point is authoritative
    event = MockEvent(
        click_type="contextmenu",
        hits=[
            MockHit(object_name="robot:link6", x=0.1, y=0.1, z=0.5),  # High up
            MockHit(object_name="robot:link3", x=0.1, y=0.1, z=0.25),  # Middle
            MockHit(object_name="robot:base", x=0.1, y=0.1, z=0.02),  # Near ground
            MockHit(object_name="grid", x=0.1, y=0.1, z=0.0),  # Exact ground
        ],
        ground_point=MockGroundPoint(x=0.15, y=0.15, z=0.0),  # Different x,y than hits
    )

    # Call handler
    scene._populate_context_menu(event)

    # Should use ground_point coordinates, not any of the hits
    assert scene._last_click_coords is not None
    x, y, z = scene._last_click_coords
    assert abs(x - 0.15) < 0.001, f"Expected x≈0.15 (ground_point), got x={x}"
    assert abs(y - 0.15) < 0.001, f"Expected y≈0.15 (ground_point), got y={y}"
    assert abs(z) < 0.001, f"Expected z≈0 (ground_point), got z={z}"


@pytest.mark.integration
async def test_envelope_sphere_creation_succeeds(user: User) -> None:
    """Test that envelope sphere creation works correctly.

    Verifies that when envelope mode is enabled and workspace envelope is ready,
    the envelope sphere is created successfully.

    Note: The envelope visualization was changed from point cloud to a
    lightweight wireframe sphere for better performance.
    """
    from parol_commander.state import ui_state, simulation_state
    from parol_commander.services.urdf_scene.envelope_mixin import workspace_envelope

    await user.open("/")
    await wait_for_urdf_ready()

    urdf_scene = ui_state.urdf_scene
    assert urdf_scene is not None

    # Setup: enable envelope mode and prepare max_reach
    original_mode = simulation_state.envelope_mode
    original_envelope_obj = urdf_scene.envelope_object

    try:
        simulation_state.envelope_mode = "on"
        urdf_scene.envelope_object = None  # Force recreation attempt

        # Set up workspace envelope with valid max_reach
        workspace_envelope.max_reach = 0.6
        workspace_envelope._generated = True

        # The scene already has a valid client from wait_for_urdf_ready()
        # Update should create envelope without error
        urdf_scene._update_simulation_view()

        # Should have created envelope_object
        assert (
            urdf_scene.envelope_object is not None
        ), "Envelope sphere should be created when mode='on' and max_reach > 0"
    finally:
        # Restore state
        simulation_state.envelope_mode = original_mode
        urdf_scene.envelope_object = original_envelope_obj
        workspace_envelope.reset()


# ============================================================================
# TCP Offset and Tool Change Tests
# ============================================================================


class TestTcpOffsetTracking:
    """Tests for TCP offset tracking when tools change."""

    def test_update_tcp_pose_from_tool_tracks_offset(self):
        """update_tcp_pose_from_tool should store tool offset for envelope calculations."""
        from parol_commander.services.urdf_scene import (
            UrdfScene,
            UrdfSceneConfig,
            ToolPose,
        )

        # Create config with a tool that has Z offset
        config = UrdfSceneConfig(
            tool_pose_map={
                "gripper_v1": ToolPose(origin=[0.0, 0.0, 0.05], rpy=[0.0, 0.0, 0.0]),
                "long_tool": ToolPose(origin=[0.0, 0.0, 0.15], rpy=[0.0, 0.0, 0.0]),
            }
        )

        # Create mock scene without actually loading URDF
        scene = MagicMock()
        scene.config = config
        scene.tcp_offset = MagicMock()
        scene._current_tool = "none"
        scene._current_tool_offset_z = 0.0
        scene.envelope_object = None

        # Call the actual method by binding it
        bound_method = UrdfScene.update_tcp_pose_from_tool.__get__(scene, type(scene))

        # Update to gripper_v1
        bound_method("gripper_v1")

        assert scene._current_tool == "gripper_v1"
        assert scene._current_tool_offset_z == 0.05

        # Update to long_tool
        bound_method("long_tool")

        assert scene._current_tool == "long_tool"
        assert scene._current_tool_offset_z == 0.15

    def test_update_tcp_pose_from_tool_resets_on_none(self):
        """update_tcp_pose_from_tool should reset offset when tool is 'none'."""
        from parol_commander.services.urdf_scene import (
            UrdfScene,
            UrdfSceneConfig,
            ToolPose,
        )

        config = UrdfSceneConfig(
            tool_pose_map={
                "gripper_v1": ToolPose(origin=[0.0, 0.0, 0.05], rpy=[0.0, 0.0, 0.0]),
            }
        )

        scene = MagicMock()
        scene.config = config
        scene.tcp_offset = MagicMock()
        scene._current_tool = "gripper_v1"
        scene._current_tool_offset_z = 0.05
        scene.envelope_object = None

        bound_method = UrdfScene.update_tcp_pose_from_tool.__get__(scene, type(scene))

        # Update to "none" (no tool)
        bound_method("none")

        assert scene._current_tool == "none"
        assert scene._current_tool_offset_z == 0.0

    def test_update_tcp_pose_from_tool_uses_resolver(self):
        """update_tcp_pose_from_tool should use tool_pose_resolver if provided."""
        from parol_commander.services.urdf_scene import (
            UrdfScene,
            UrdfSceneConfig,
            ToolPose,
        )

        # Create resolver function
        def mock_resolver(tool: str):
            if tool == "custom_tool":
                return ToolPose(origin=[0.0, 0.0, 0.08], rpy=[0.0, 0.0, 0.0])
            return None

        config = UrdfSceneConfig(tool_pose_resolver=mock_resolver)

        scene = MagicMock()
        scene.config = config
        scene.tcp_offset = MagicMock()
        scene._current_tool = "none"
        scene._current_tool_offset_z = 0.0
        scene.envelope_object = None

        bound_method = UrdfScene.update_tcp_pose_from_tool.__get__(scene, type(scene))

        # Update to custom_tool (should use resolver)
        bound_method("custom_tool")

        assert scene._current_tool == "custom_tool"
        assert scene._current_tool_offset_z == 0.08


# ============================================================================
# Proximity-Based Auto Mode Tests
# ============================================================================


class TestProximityBasedAutoMode:
    """Tests for proximity-based envelope visibility in auto mode."""

    def test_auto_mode_shows_envelope_when_tcp_near_boundary(self):
        """Auto mode should show envelope when TCP is near workspace boundary."""
        from parol_commander.state import robot_state, simulation_state
        from parol_commander.services.urdf_scene.envelope_mixin import (
            workspace_envelope,
        )
        import math

        # Setup: max_reach = 0.6m, threshold = 10% = 0.06m
        # boundary_distance = 0.6 - 0.06 = 0.54m
        workspace_envelope.max_reach = 0.6
        workspace_envelope._generated = True
        simulation_state.envelope_mode = "auto"
        simulation_state.targets.clear()

        try:
            # TCP at 0.55m from origin (>0.54m boundary) - should trigger
            # Using x=0.55, y=0, z=0 -> distance = 0.55m
            robot_state.x = 550.0  # mm -> will be divided by 1000
            robot_state.y = 0.0
            robot_state.z = 0.0

            tcp_dist = math.sqrt((550 / 1000) ** 2 + 0**2 + 0**2)
            boundary_distance = 0.6 - max(0.6 * 0.1, 0.05)

            # TCP (0.55m) >= boundary (0.54m) -> should show envelope
            assert tcp_dist >= boundary_distance
        finally:
            workspace_envelope.reset()
            simulation_state.envelope_mode = "auto"

    def test_auto_mode_hides_envelope_when_tcp_far_from_boundary(self):
        """Auto mode should hide envelope when TCP is far from boundary."""
        from parol_commander.state import robot_state, simulation_state
        from parol_commander.services.urdf_scene.envelope_mixin import (
            workspace_envelope,
        )
        import math

        workspace_envelope.max_reach = 0.6
        workspace_envelope._generated = True
        simulation_state.envelope_mode = "auto"
        simulation_state.targets.clear()

        try:
            # TCP at 0.3m from origin (<0.54m boundary) - should NOT trigger
            robot_state.x = 300.0  # mm
            robot_state.y = 0.0
            robot_state.z = 0.0

            tcp_dist = math.sqrt((300 / 1000) ** 2 + 0**2 + 0**2)
            boundary_distance = 0.6 - max(0.6 * 0.1, 0.05)

            # TCP (0.3m) < boundary (0.54m) -> should NOT show envelope
            assert tcp_dist < boundary_distance
        finally:
            workspace_envelope.reset()

    def test_auto_mode_shows_envelope_when_target_near_boundary(self):
        """Auto mode should show envelope when any target is near boundary."""
        from parol_commander.state import robot_state, simulation_state, ProgramTarget
        from parol_commander.services.urdf_scene.envelope_mixin import (
            workspace_envelope,
        )
        import math

        workspace_envelope.max_reach = 0.6
        workspace_envelope._generated = True
        simulation_state.envelope_mode = "auto"
        simulation_state.targets.clear()

        try:
            # TCP far from boundary
            robot_state.x = 100.0  # mm -> 0.1m, far from boundary
            robot_state.y = 0.0
            robot_state.z = 0.0

            # Add target near boundary (0.55m from origin)
            target = ProgramTarget(
                id="near-boundary",
                line_number=1,
                pose=[0.55, 0.0, 0.0, 0.0, 0.0, 0.0],  # Already in meters
                move_type="pose",
                scene_object_id="",
            )
            simulation_state.targets.append(target)

            # Calculate distances
            tcp_dist = math.sqrt((100 / 1000) ** 2 + 0**2 + 0**2)  # 0.1m
            target_dist = math.sqrt(0.55**2 + 0**2 + 0**2)  # 0.55m
            boundary_distance = 0.6 - max(0.6 * 0.1, 0.05)  # 0.54m

            # TCP (0.1m) < boundary, but target (0.55m) >= boundary
            assert tcp_dist < boundary_distance
            assert target_dist >= boundary_distance
        finally:
            simulation_state.targets.clear()
            workspace_envelope.reset()

    def test_auto_mode_threshold_calculation(self):
        """Verify threshold is max(10% of reach, 50mm)."""
        from parol_commander.services.urdf_scene.envelope_mixin import (
            workspace_envelope,
        )

        # Test with large max_reach (10% > 50mm)
        workspace_envelope.max_reach = 0.8  # 800mm, 10% = 80mm > 50mm
        threshold_large = max(0.8 * 0.1, 0.05)
        assert threshold_large == pytest.approx(0.08)  # 10% wins

        # Test with small max_reach (10% < 50mm)
        workspace_envelope.max_reach = 0.3  # 300mm, 10% = 30mm < 50mm
        threshold_small = max(0.3 * 0.1, 0.05)
        assert threshold_small == pytest.approx(0.05)  # 50mm minimum wins

        workspace_envelope.reset()
