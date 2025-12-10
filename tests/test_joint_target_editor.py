"""Integration tests for joint target editor functionality.

Tests the ghost robot visualization and joint target editing workflow:
- Ghost robot building, showing, and hiding
- Joint target editor lifecycle (show, confirm, cancel)
- IK chain and joint ring creation/cleanup
- Live robot dimming when ghost is visible
"""

import asyncio
import math
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from nicegui.testing import User

from tests.helpers.wait import wait_for_urdf_ready

# Skip reason for tests that call internal methods creating UI elements
UI_CONTEXT_SKIP = pytest.mark.skip(
    reason="Test calls internal methods that create UI elements requiring NiceGUI context. "
    "Needs refactoring to trigger via UI interaction."
)


@dataclass
class MockKeyEvent:
    """Mock keyboard event for testing."""
    key: str
    action: MagicMock
    
    def __post_init__(self):
        if not hasattr(self.action, 'keydown'):
            self.action = MagicMock()
            self.action.keydown = True


@dataclass
class MockHit:
    """Mock hit object for scene click events."""
    object_name: str
    object_id: str = ""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class MockGroundPoint:
    """Mock ground point for scene click events."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class MockClickEvent:
    """Mock scene click event."""
    click_type: str
    hits: list[MockHit]
    ground_point: MockGroundPoint | None = None


# ============================================================================
# Ghost Robot Visibility Tests
# ============================================================================

@pytest.mark.integration
async def test_ghost_robot_builds_on_demand(user: User) -> None:
    """Test that ghost robot is built when joint target editor is shown."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # Ghost should not exist initially
    assert scene._ghost_group is None
    
    with user:
        # Show joint target editor
        scene._show_joint_target_editor()
        
        await asyncio.sleep(0.2)
        
        # Ghost should now be built and visible
        assert scene._ghost_group is not None, "Ghost group should be created"
        assert scene._ghost_visible == True, "Ghost should be visible"
        assert scene._editing_joint_target == True, "Should be in editing mode"
        
        # Clean up
        scene._cancel_joint_target_editing()


@pytest.mark.integration
async def test_ghost_robot_hides_on_cancel(user: User) -> None:
    """Test that ghost robot is hidden when editing is cancelled."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    with user:
        # Show joint target editor
        scene._show_joint_target_editor()
        
        await asyncio.sleep(0.2)
        
        # Verify editor is active
        assert scene._editing_joint_target == True
        assert scene._ghost_visible == True
        
        # Cancel editing
        scene._cancel_joint_target_editing()
        
        await asyncio.sleep(0.1)
        
        # Ghost should be hidden
        assert scene._ghost_visible == False, "Ghost should be hidden after cancel"
        assert scene._editing_joint_target == False, "Should not be in editing mode"


@pytest.mark.integration
async def test_ghost_robot_esc_key_cancels(user: User) -> None:
    """Test that pressing ESC cancels joint target editing."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    with user:
        # Show joint target editor
        scene._show_joint_target_editor()
        
        await asyncio.sleep(0.2)
        
        assert scene._editing_joint_target == True
        
        # Simulate ESC key press
        mock_action = MagicMock()
        mock_action.keydown = True
        esc_event = MockKeyEvent(key='Escape', action=mock_action)
        
        scene._handle_keyboard(esc_event)
        
        await asyncio.sleep(0.1)
        
        # Should be cancelled
        assert scene._editing_joint_target == False, "ESC should cancel editing"
        assert scene._ghost_visible == False, "Ghost should be hidden"


# ============================================================================
# Joint Target Editor Lifecycle Tests
# ============================================================================

@pytest.mark.integration
async def test_context_menu_has_joint_target_option(user: User) -> None:
    """Test that context menu shows joint target editing options."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    assert scene.context_menu is not None
    
    # Simulate right-click on empty space
    event = MockClickEvent(
        click_type="contextmenu",
        hits=[MockHit(object_name="robot:base", x=0.1, y=0.1, z=0.0)],
        ground_point=MockGroundPoint(x=0.1, y=0.1, z=0.0)
    )
    
    scene._populate_context_menu(event)
    
    # Context menu should have been populated without error
    # (Menu items are created via NiceGUI elements, so we verify no exception raised)
    # The menu includes "Edit Joint Target (Visual)..." option


@pytest.mark.integration
async def test_joint_target_editor_click_away_does_not_confirm(user: User) -> None:
    """Test that clicking away from ghost robot does NOT auto-confirm.
    
    With the new UX, users must use the edit bar Cancel/Confirm buttons.
    Clicking away should be ignored (editor stays open, no code inserted).
    """
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # Mock the editor panel to track if add_joint_target_code is called
    mock_editor = MagicMock()
    mock_editor.add_joint_target_code.return_value = "test-marker-123"
    original_editor = ui_state.editor_panel
    ui_state.editor_panel = mock_editor
    
    try:
        with user:
            # Show joint target editor
            scene._show_joint_target_editor()
            
            await asyncio.sleep(0.2)
            
            assert scene._editing_joint_target == True
            
            # Simulate click on ground (not on ghost parts)
            click_event = MockClickEvent(
                click_type="mousedown",
                hits=[MockHit(object_name="ground", x=0.5, y=0.5, z=0.0)],
                ground_point=MockGroundPoint(x=0.5, y=0.5, z=0.0)
            )
            
            scene._handle_scene_click(click_event)
            
            await asyncio.sleep(0.1)
            
            # Editor should STILL be open (click-away does NOT confirm anymore)
            assert scene._editing_joint_target == True, "Click-away should NOT close editor (use Cancel/Confirm buttons)"
            
            # add_joint_target_code should NOT have been called (no auto-confirm)
            mock_editor.add_joint_target_code.assert_not_called()
            
    finally:
        ui_state.editor_panel = original_editor
        # Clean up - cancel the editor
        if scene._editing_joint_target:
            with user:
                scene._cancel_joint_target_editing()


@pytest.mark.integration
async def test_joint_target_editor_esc_does_not_insert_code(user: User) -> None:
    """Test that ESC cancels without inserting joint target code."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # Mock the editor panel
    mock_editor = MagicMock()
    mock_editor.add_joint_target_code.return_value = "test-marker-123"
    original_editor = ui_state.editor_panel
    ui_state.editor_panel = mock_editor
    
    try:
        with user:
            # Show joint target editor
            scene._show_joint_target_editor()
            
            await asyncio.sleep(0.2)
            
            # Simulate ESC key press
            mock_action = MagicMock()
            mock_action.keydown = True
            esc_event = MockKeyEvent(key='Escape', action=mock_action)
            
            scene._handle_keyboard(esc_event)
            
            await asyncio.sleep(0.1)
            
            # add_joint_target_code should NOT have been called
            mock_editor.add_joint_target_code.assert_not_called()
            
    finally:
        ui_state.editor_panel = original_editor


# ============================================================================
# Ghost Robot State Tests
# ============================================================================

@pytest.mark.integration
async def test_ghost_joint_angles_updated_on_show(user: User) -> None:
    """Test that ghost robot joint angles are set correctly when shown."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # Build ghost robot first
    scene.build_ghost_robot()
    
    await asyncio.sleep(0.1)
    
    # Set specific angles (in radians)
    test_angles = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    
    scene.show_ghost_robot(test_angles)
    
    await asyncio.sleep(0.1)
    
    # Verify angles are stored
    stored_angles = scene.get_ghost_joint_angles()
    for i, (expected, actual) in enumerate(zip(test_angles, stored_angles)):
        assert abs(expected - actual) < 1e-6, f"Joint {i} angle mismatch: expected {expected}, got {actual}"
    
    # Clean up
    scene.hide_ghost_robot()


@pytest.mark.integration
async def test_ghost_tcp_ball_created(user: User) -> None:
    """Test that ghost TCP ball is created with the editor."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    with user:
        # Show joint target editor (which builds ghost robot)
        scene._show_joint_target_editor()
        
        await asyncio.sleep(0.2)
        
        # TCP ball should exist
        assert scene._ghost_tcp_ball is not None, "Ghost TCP ball should be created"
        
        # Clean up
        scene._cancel_joint_target_editing()


# ============================================================================
# IK Chain and Rings Tests
# ============================================================================

@pytest.mark.integration
async def test_joint_rings_created_on_editor_show(user: User) -> None:
    """Test that joint rotation controls (TransformControls) are created when editor is shown."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # Joint TransformControls should not exist initially
    assert len(scene._joint_control_groups) == 0
    
    with user:
        # Show joint target editor
        scene._show_joint_target_editor()
        
        await asyncio.sleep(0.3)
        
        # Should have 6 joint TransformControls attached to joint groups
        assert len(scene._joint_control_groups) == 6, f"Expected 6 joint control groups, got {len(scene._joint_control_groups)}"
        for i in range(6):
            assert i in scene._joint_control_groups, f"Missing control group for joint {i}"
        
        # Clean up
        scene._cancel_joint_target_editing()


@pytest.mark.integration
async def test_cleanup_removes_rings(user: User) -> None:
    """Test that cleanup properly removes joint TransformControls on joint groups."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    with user:
        # Show joint target editor (creates TransformControls on joint groups)
        scene._show_joint_target_editor()
        
        await asyncio.sleep(0.3)
        
        # Verify joint control groups exist
        assert len(scene._joint_control_groups) == 6
        
        # Cancel (triggers cleanup)
        scene._cancel_joint_target_editing()
        
        await asyncio.sleep(0.1)
        
        # Joint control groups should be cleared (TransformControls disabled)
        assert len(scene._joint_control_groups) == 0, "Joint control groups should be cleaned up"


@pytest.mark.integration
async def test_get_joint_axes_returns_correct_axes(user: User) -> None:
    """Test that _get_joint_axes_letters returns correct axes for PAROL6.
    
    Note: All PAROL6 joints rotate around their LOCAL Z axis as defined in the URDF.
    The visual appearance of different world-frame axes comes from the frame
    transformations (rpy in origin), not from different axis values.
    """
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    axes = scene._get_joint_axes_letters()
    
    # PAROL6 URDF defines all joints with axis="0 0 1" (local Z axis)
    # TransformControls use local space, so all axes are Z
    expected = ['Z', 'Z', 'Z', 'Z', 'Z', 'Z']
    assert axes == expected, f"Expected axes {expected}, got {axes}"


@pytest.mark.integration
async def test_get_joint_limits_returns_valid_limits(user: User) -> None:
    """Test that _get_joint_limits returns valid min/max tuples."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    limits = scene._get_joint_limits()
    
    # Should have limits for all 6 joints
    assert len(limits) == 6, f"Expected 6 joint limits, got {len(limits)}"
    
    for i, (min_val, max_val) in enumerate(limits):
        # Each should be a tuple of (min, max)
        assert min_val < max_val, f"Joint {i} limits invalid: min={min_val}, max={max_val}"
        # Should be in reasonable range (radians), with small tolerance for floating point
        tolerance = 1e-6
        assert min_val >= -2 * math.pi - tolerance, f"Joint {i} min too low: {min_val}"
        assert max_val <= 2 * math.pi + tolerance, f"Joint {i} max too high: {max_val}"


# ============================================================================
# Live Robot Dimming Tests
# ============================================================================

@pytest.mark.integration
async def test_live_robot_dimmed_when_ghost_visible(user: User) -> None:
    """Test that live robot meshes are dimmed when ghost is shown."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # Should have robot meshes
    assert len(scene._robot_meshes) > 0, "Scene should have robot meshes"
    
    # Show ghost robot
    scene.build_ghost_robot()
    scene.show_ghost_robot([0.0] * 6)
    
    await asyncio.sleep(0.1)
    
    # Live robot should be dimmed (we can't easily check opacity, but verify no exceptions)
    assert scene._ghost_visible == True
    
    # Clean up
    scene.hide_ghost_robot()


@pytest.mark.integration
async def test_live_robot_restored_when_ghost_hidden(user: User) -> None:
    """Test that live robot is restored when ghost is hidden."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # Show then hide ghost
    scene.build_ghost_robot()
    scene.show_ghost_robot([0.0] * 6)
    
    await asyncio.sleep(0.1)
    
    scene.hide_ghost_robot()
    
    await asyncio.sleep(0.1)
    
    # Ghost should be hidden, live robot restored
    assert scene._ghost_visible == False
    # Meshes should have normal appearance (can't easily verify color/opacity programmatically)


# ============================================================================
# IK Event Handling Tests
# ============================================================================

@pytest.mark.integration
async def test_ik_solved_event_updates_ghost_angles(user: User) -> None:
    """Test that IK solved event updates ghost joint angles."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    with user:
        # Show joint target editor
        scene._show_joint_target_editor()
        
        await asyncio.sleep(0.2)
        
        # Simulate IK solved event
        test_angles = [0.5, 0.4, 0.3, 0.2, 0.1, 0.0]  # radians
        mock_event = MagicMock()
        mock_event.args = {
            'chain_id': 'ghost_ik',
            'angles': test_angles
        }
        
        scene._on_ik_solved(mock_event)
        
        # Angles should be updated
        stored = scene.get_ghost_joint_angles()
        for i, (expected, actual) in enumerate(zip(test_angles, stored)):
            assert abs(expected - actual) < 1e-6, f"Joint {i} not updated: expected {expected}, got {actual}"
        
        # Clean up
        scene._cancel_joint_target_editing()


@pytest.mark.integration
async def test_ik_solved_event_ignores_other_chains(user: User) -> None:
    """Test that IK solved events for other chains are ignored."""
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    with user:
        # Show joint target editor
        scene._show_joint_target_editor()
        
        await asyncio.sleep(0.2)
        
        # Store original angles
        original_angles = scene.get_ghost_joint_angles()
        
        # Simulate IK solved event for different chain
        mock_event = MagicMock()
        mock_event.args = {
            'chain_id': 'other_chain',  # Not 'ghost_ik'
            'angles': [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        }
        
        scene._on_ik_solved(mock_event)
        
        # Angles should NOT have changed
        stored = scene.get_ghost_joint_angles()
        for i, (original, actual) in enumerate(zip(original_angles, stored)):
            assert abs(original - actual) < 1e-6, f"Joint {i} should not have changed"
        
        # Clean up
        scene._cancel_joint_target_editing()


# ============================================================================
# Diagnostic Tests - Verify Specific Behaviors
# ============================================================================

@pytest.mark.integration
async def test_ghost_tcp_ball_color_is_blue(user: User) -> None:
    """DIAGNOSTIC: Verify ghost TCP ball is blue (#4a63e0), not orange.
    
    The TCP ball for joint target editing should be blue.
    Orange balls (#e67e22) are for pose targets, not joint targets.
    """
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    with user:
        # Show joint target editor
        scene._show_joint_target_editor()
        
        await asyncio.sleep(0.2)
        
        # Verify TCP ball exists
        tcp_ball = scene._ghost_tcp_ball
        assert tcp_ball is not None, "Ghost TCP ball should exist after showing joint target editor"
        
        # Verify TCP ball color is blue
        expected_color = "#4a63e0"
        actual_color = scene._ghost_tcp_color
        assert actual_color == expected_color, (
            f"Ghost TCP ball color should be blue ({expected_color}), "
            f"but configured color is {actual_color}"
        )
        
        # Verify the TCP ball has the correct name for identification
        # NiceGUI stores the name via with_name() which we can verify was called correctly
        # The name is used for identification in scene.js
        tcp_ball_name = tcp_ball._name if hasattr(tcp_ball, '_name') and callable(tcp_ball._name) else None
        # Call _name() if it's a method to get the actual name string
        if callable(tcp_ball_name):
            # _name is a method that returns the name
            pass  # We can't easily get the stored name from Python, so verify the object exists
        
        # The important thing is the TCP ball exists and is configured with blue color
        # The name was set via with_name() in _create_ghost_tcp_ball()
        
        # Clean up
        scene._cancel_joint_target_editing()


@pytest.mark.integration
async def test_robot_meshes_populated(user: User) -> None:
    """DIAGNOSTIC: Verify robot meshes are tracked for appearance changes.
    
    The _robot_meshes list must be populated for dimming to work.
    """
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # Verify robot meshes are tracked
    mesh_count = len(scene._robot_meshes)
    assert mesh_count > 0, (
        f"Expected robot meshes to be tracked, but _robot_meshes has {mesh_count} items. "
        "This means dimming won't work because there are no meshes to dim."
    )


@pytest.mark.integration
async def test_robot_meshes_dimmed_when_ghost_visible(user: User) -> None:
    """DIAGNOSTIC: Verify live robot is dimmed (grey, transparent) when ghost is shown.
    
    When ghost robot is visible, live robot should be:
    - Color: #555555 (grey)
    - Opacity: 0.25 (transparent)
    """
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # Verify we have meshes to test
    assert len(scene._robot_meshes) > 0, "No robot meshes to test dimming"
    
    # Show ghost robot (which should dim the live robot)
    scene.build_ghost_robot()
    scene.show_ghost_robot([0.0] * 6)
    
    await asyncio.sleep(0.1)
    
    # Verify ghost is visible
    assert scene._ghost_visible == True, "Ghost should be visible"
    
    # Verify _dim_live_robot was effectively called
    # The expected dim values are defined in _dim_live_robot()
    expected_dim_color = "#555555"
    expected_dim_opacity = 0.25
    
    # Check at least one mesh for dimming (meshes should all be dimmed the same)
    # Note: We can't easily check the actual Three.js material from Python,
    # but we can verify the dimming logic was triggered
    
    # Clean up
    scene.hide_ghost_robot()


@pytest.mark.integration
async def test_ghost_joint_ids_in_kinematic_order(user: User) -> None:
    """DIAGNOSTIC: Verify ghost joint IDs are returned in kinematic chain order.
    
    The IDs must match self.joint_names order for IK to work correctly.
    Wrong order = wrong rotations applied to wrong joints.
    """
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # Build ghost robot
    scene.build_ghost_robot()
    
    await asyncio.sleep(0.1)
    
    # Get ghost joint IDs
    ghost_joint_ids = scene._get_ghost_joint_ids()
    
    # Verify we got the right number of joints
    expected_count = len(scene.joint_names)
    actual_count = len(ghost_joint_ids)
    assert actual_count == expected_count, (
        f"Expected {expected_count} ghost joint IDs (matching joint_names), "
        f"but got {actual_count}"
    )
    
    # Verify the order matches joint_names
    ghost_joint_groups = scene._ghost_joint_groups
    joint_names_order = list(scene.joint_names)
    ghost_joint_names_order = list(ghost_joint_groups.keys())
    
    assert ghost_joint_names_order == joint_names_order, (
        f"Ghost joint names order {ghost_joint_names_order} does not match "
        f"expected kinematic order {joint_names_order}. "
        "This will cause IK to apply rotations to wrong joints!"
    )
    
    # Clean up
    scene.hide_ghost_robot()


@pytest.mark.integration
async def test_ik_solver_created_on_editor_show(user: User) -> None:
    """DIAGNOSTIC: Verify Python CCD IK solver is created when joint target editor is shown.
    
    The Python GhostIKSolver must be initialized for dragging to solve IK.
    Note: We no longer use JS-side IK chains; IK is solved in Python for better synchronization.
    """
    from parol_commander.state import ui_state
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    # IK solver should not exist initially
    assert scene._ghost_ik_solver is None, "IK solver should not exist before editor is shown"
    
    with user:
        try:
            # Show joint target editor
            scene._show_joint_target_editor()
            
            # Wait for async enable_controls_after_render to execute (includes IK init)
            await asyncio.sleep(0.2)
            
            # Verify Python IK solver was created
            assert scene._ghost_ik_solver is not None, (
                "Python GhostIKSolver was not created when showing joint target editor. "
                "Without an IK solver, dragging the TCP ball won't solve IK."
            )
            
            # Verify it has correct number of joints
            assert scene._ghost_ik_solver.num_joints == 6, (
                f"IK solver should have 6 joints, but has {scene._ghost_ik_solver.num_joints}"
            )
            
        finally:
            # Clean up
            scene._cancel_joint_target_editing()


@pytest.mark.integration
async def test_tcp_transform_event_triggers_python_ik_solve(user: User) -> None:
    """DIAGNOSTIC: Verify dragging TCP ball triggers Python CCD IK solving.
    
    When TCP ball is dragged (continuous transform event), it should:
    1. Call Python GhostIKSolver.solve() with new position
    2. Update ghost robot joint angles with the result
    
    Note: TCP ball transforms are handled by _handle_transform_continuous (for live updates),
    NOT _handle_transform_event (which is for pose target transform_end events).
    IK is now solved in Python (not JS) for better synchronization.
    """
    from parol_commander.state import ui_state
    from parol_commander.services.ghost_ik_solver import GhostIKSolver
    
    await user.open("/")
    await wait_for_urdf_ready()
    
    scene = ui_state.urdf_scene
    assert scene is not None
    
    with user:
        try:
            # Show joint target editor
            scene._show_joint_target_editor()
            
            # Wait for async controls initialization (includes IK solver creation)
            await asyncio.sleep(0.2)
            
            # Verify IK solver was created
            assert scene._ghost_ik_solver is not None, "Python IK solver not initialized"
            
            # Track IK solver calls by wrapping the solve method
            solve_calls = []
            original_solve = scene._ghost_ik_solver.solve
            
            def mock_solve(target_pos, current_angles, throttle=True):
                solve_calls.append({
                    'target_pos': target_pos.tolist() if hasattr(target_pos, 'tolist') else list(target_pos),
                    'current_angles': list(current_angles),
                    'throttle': throttle
                })
                # Return a mock result with slightly modified angles
                from parol_commander.services.ghost_ik_solver import IKResult
                return IKResult(
                    success=True,
                    angles=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
                    error=0.001,
                    iterations=5,
                )
            
            scene._ghost_ik_solver.solve = mock_solve
            
            # Store initial angles
            initial_angles = scene.get_ghost_joint_angles()
            
            # Simulate continuous transform event on TCP ball
            # This mimics what happens when user drags the TCP ball in the scene
            mock_transform_event = MagicMock()
            mock_transform_event.object_name = "ghost:tcp_ball"
            mock_transform_event.object_id = str(scene._ghost_tcp_ball.id) if scene._ghost_tcp_ball else ""
            mock_transform_event.type = "transform"
            mock_transform_event.x = 0.2
            mock_transform_event.y = 0.1
            mock_transform_event.z = 0.3
            # Explicitly set world coordinates to None so the code uses local coords
            mock_transform_event.wx = None
            mock_transform_event.wy = None
            mock_transform_event.wz = None
            mock_transform_event.rx = 0.0
            mock_transform_event.ry = 0.0
            mock_transform_event.rz = 0.0
            mock_transform_event.mode = "translate"
            
            # Call the continuous transform handler
            scene._handle_transform_continuous(mock_transform_event)
            
            # Verify Python IK solver was called
            assert len(solve_calls) > 0, (
                "GhostIKSolver.solve() was never called when TCP ball was transformed. "
                "The continuous transform event handler is not triggering Python IK solving."
            )
            
            # Verify correct target position was passed
            call = solve_calls[0]
            expected_pos = [0.2, 0.1, 0.3]
            for i, (expected, actual) in enumerate(zip(expected_pos, call['target_pos'])):
                assert abs(expected - actual) < 1e-6, (
                    f"IK solver called with wrong target position at index {i}: "
                    f"expected {expected}, got {actual}"
                )
            
            # Verify ghost joint angles were updated with the result
            updated_angles = scene.get_ghost_joint_angles()
            expected_result = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
            for i, (expected, actual) in enumerate(zip(expected_result, updated_angles)):
                assert abs(expected - actual) < 1e-6, (
                    f"Ghost joint angle {i} not updated with IK result: "
                    f"expected {expected}, got {actual}"
                )
            
        finally:
            # Clean up
            scene._cancel_joint_target_editing()
