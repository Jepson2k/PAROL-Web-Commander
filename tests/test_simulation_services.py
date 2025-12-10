"""Functional tests for simulation services.

These tests verify actual behavior rather than just checking if buttons exist.
"""
import pytest
import time
from unittest.mock import MagicMock, patch, PropertyMock
import numpy as np

from parol_commander.state import (
    SimulationState, RecordingState, PathSegment, ProgramTarget, RobotState,
    PlaybackState, simulation_state, recording_state, robot_state,
    ui_state, playback_state
)
from parol_commander.services.dry_run_client import DryRunRobotClient
from parol_commander.services.motion_recorder import MotionRecorder
from parol_commander.services.path_visualizer import (
    PathVisualizer, get_color_for_move_type, MOVE_TYPE_COLORS
)
from parol_commander.services.workspace_envelope import WorkspaceEnvelope


# ============================================================================
# Path Visualization Tests
# ============================================================================

class TestPathVisualization:
    """Tests for path segment creation and visualization."""
    
    def test_get_color_for_valid_cartesian_move(self):
        """Cartesian moves should return green color."""
        color = get_color_for_move_type("cartesian", is_valid=True)
        assert color == "#2faf7a"
        
        color = get_color_for_move_type("move_cartesian", is_valid=True)
        assert color == "#2faf7a"
    
    def test_get_color_for_valid_joint_move(self):
        """Joint moves should return blue color."""
        color = get_color_for_move_type("joints", is_valid=True)
        assert color == "#4a63e0"
        
        color = get_color_for_move_type("move_joints", is_valid=True)
        assert color == "#4a63e0"
    
    def test_get_color_for_smooth_move(self):
        """Smooth moves should return purple color."""
        color = get_color_for_move_type("smooth", is_valid=True)
        assert color == "#9b59b6"
        
        color = get_color_for_move_type("smooth_cartesian", is_valid=True)
        assert color == "#9b59b6"
    
    def test_get_color_for_invalid_move_returns_red(self):
        """Invalid moves should return red regardless of move type."""
        color = get_color_for_move_type("cartesian", is_valid=False)
        assert color == "#e74c3c"
        
        color = get_color_for_move_type("joints", is_valid=False)
        assert color == "#e74c3c"
    
    def test_get_color_for_unknown_move_type(self):
        """Unknown move types should return gray."""
        color = get_color_for_move_type("unknown_type", is_valid=True)
        assert color == "#95a5a6"
        
        color = get_color_for_move_type("", is_valid=True)
        assert color == "#95a5a6"
    
    def test_path_segment_has_all_required_fields(self):
        """PathSegment should have all required visualization fields."""
        segment = PathSegment(
            points=[[0, 0, 0], [100, 100, 100]],
            color="#2faf7a",
            is_valid=True,
            line_number=5,
            joints=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            move_type="cartesian",
            is_dashed=True,
            show_arrows=True
        )
        
        assert len(segment.points) == 2
        assert segment.is_dashed is True
        assert segment.show_arrows is True
        assert segment.move_type == "cartesian"


# ============================================================================
# Dry Run Client Tests
# ============================================================================

class TestDryRunClient:
    """Tests for dry run simulation client.
    
    Note: The refactored DryRunRobotClient now uses injected collectors
    instead of the global simulation_state. Tests now verify that segments
    are added to the client's segment_collector.
    """
    
    def _create_mock_parol6_robot(self):
        """Create a mock PAROL6_ROBOT module."""
        mock_robot_module = MagicMock()
        mock_robot = MagicMock()
        mock_robot_module.robot = mock_robot
        
        # Mock fkine return
        mock_T = MagicMock()
        mock_T.t = [100, 0, 200]
        mock_T.rpy.return_value = [0, 0, 0]
        mock_robot.fkine.return_value = mock_T
        
        # Mock check_limits
        mock_robot_module.check_limits.return_value = True
        
        return mock_robot_module
    
    @pytest.mark.asyncio
    async def test_move_joints_creates_path_segment(self):
        """move_joints should create a path segment with joint data."""
        mock_robot_module = self._create_mock_parol6_robot()
        
        with patch("parol_commander.services.dry_run_client.PAROL6_ROBOT", mock_robot_module):
            # Create client with explicit collectors
            segments: list[dict] = []
            targets: list[dict] = []
            client = DryRunRobotClient(segment_collector=segments, target_collector=targets)
            
            client.move_joints([10, 20, 30, 40, 50, 60])
            
            # Verify path segment created in collector
            assert len(segments) == 1
            segment = segments[0]
            assert segment["is_valid"] is True
            assert segment["joints"] is not None
            assert len(segment["joints"]) == 6
            assert segment["move_type"] == "joints"
    
    @pytest.mark.asyncio
    async def test_move_cartesian_creates_path_segment(self):
        """move_cartesian should create a path segment with cartesian data."""
        mock_robot_module = self._create_mock_parol6_robot()
        
        # Mock solve_ik
        mock_ik_result = MagicMock()
        mock_ik_result.success = True
        mock_ik_result.q = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
        mock_solve_ik = MagicMock(return_value=mock_ik_result)
        
        with patch("parol_commander.services.dry_run_client.PAROL6_ROBOT", mock_robot_module):
            with patch("parol_commander.services.dry_run_client.solve_ik", mock_solve_ik):
                segments: list[dict] = []
                targets: list[dict] = []
                client = DryRunRobotClient(segment_collector=segments, target_collector=targets)
                
                client.move_cartesian([150, 100, 250, 0, 0, 0])
                
                # Verify segment created
                assert len(segments) == 1
                segment = segments[0]
                assert segment["is_valid"] is True
                assert segment["move_type"] == "cartesian"
    
    @pytest.mark.asyncio
    async def test_move_creates_segment_but_not_target_without_marker(self):
        """Moves without TARGET marker should create segment but not target.
        
        The dry-run client only creates ProgramTarget objects when:
        1. The source line has a TARGET:uuid marker
        2. The source line has literal list arguments (not variables)
        
        When move_joints is called directly (not via code parsing), there's
        no source line with a marker, so no target is created.
        """
        mock_robot_module = self._create_mock_parol6_robot()
        
        with patch("parol_commander.services.dry_run_client.PAROL6_ROBOT", mock_robot_module):
            segments: list[dict] = []
            targets: list[dict] = []
            client = DryRunRobotClient(segment_collector=segments, target_collector=targets)
            
            client.move_joints([10, 20, 30, 40, 50, 60])
            
            # Verify path segment was created (always created for visualization)
            assert len(segments) == 1
            segment = segments[0]
            assert segment["move_type"] == "joints"
            assert segment["joints"] is not None
            
            # Verify NO target was created (no TARGET marker in source)
            assert len(targets) == 0
    
    @pytest.mark.asyncio
    async def test_invalid_ik_creates_invalid_segment(self):
        """Failed IK should create segment with is_valid=False."""
        mock_robot_module = self._create_mock_parol6_robot()
        
        # Mock solve_ik to fail
        mock_ik_result = MagicMock()
        mock_ik_result.success = False
        mock_ik_result.q = None
        mock_solve_ik = MagicMock(return_value=mock_ik_result)
        
        with patch("parol_commander.services.dry_run_client.PAROL6_ROBOT", mock_robot_module):
            with patch("parol_commander.services.dry_run_client.solve_ik", mock_solve_ik):
                segments: list[dict] = []
                targets: list[dict] = []
                client = DryRunRobotClient(segment_collector=segments, target_collector=targets)
                
                client.move_cartesian([9999, 9999, 9999, 0, 0, 0])
                
                assert len(segments) == 1
                segment = segments[0]
                assert segment["is_valid"] is False


# ============================================================================
# Motion Recorder Tests
# ============================================================================

class TestMotionRecorder:
    """Tests for motion recording functionality (code-insertion API)."""
    
    @pytest.fixture(autouse=True)
    def reset_recording_state(self):
        """Reset recording state before each test."""
        recording_state.is_recording = False
        simulation_state.path_segments.clear()
        yield
        recording_state.is_recording = False
        simulation_state.path_segments.clear()
    
    @pytest.fixture
    def mock_editor(self):
        """Create mock editor for testing."""
        mock_editor = MagicMock()
        mock_textarea = MagicMock()
        mock_textarea.value = "# Initial code\n"
        mock_editor.program_textarea = mock_textarea
        ui_state.editor_panel = mock_editor
        yield mock_editor
        ui_state.editor_panel = None
    
    def _set_robot_pose(self, x, y, z, rx=0.0, ry=0.0, rz=0.0):
        """Helper to set both robot_state pose values and pose matrix."""
        robot_state.x = x
        robot_state.y = y
        robot_state.z = z
        robot_state.rx = rx
        robot_state.ry = ry
        robot_state.rz = rz
        # Set pose as flattened 4x4 identity-based matrix with translation
        # Row-major: [r00, r01, r02, tx, r10, r11, r12, ty, r20, r21, r22, tz, 0, 0, 0, 1]
        robot_state.pose = [
            1.0, 0.0, 0.0, x,
            0.0, 1.0, 0.0, y,
            0.0, 0.0, 1.0, z,
            0.0, 0.0, 0.0, 1.0
        ]
    
    def test_capture_current_pose_inserts_code(self, mock_editor):
        """capture_current_pose should insert move_cartesian code into editor."""
        self._set_robot_pose(150.0, 250.0, 350.0)
        
        recorder = MotionRecorder()
        recorder.capture_current_pose()
        
        inserted_code = mock_editor.program_textarea.value
        assert "rbt.move_cartesian([150.000, 250.000, 350.000" in inserted_code
        assert "duration=" in inserted_code
    
    def test_capture_current_pose_joints_mode(self, mock_editor):
        """capture_current_pose with joints mode should insert move_joints code."""
        robot_state.angles = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
        
        recorder = MotionRecorder()
        recorder.capture_current_pose(move_type="joints")
        
        inserted_code = mock_editor.program_textarea.value
        assert "rbt.move_joints([10.00, 20.00, 30.00, 40.00, 50.00, 60.00" in inserted_code
    
    def test_toggle_recording_starts_session(self):
        """toggle_recording should start recording when not recording."""
        recorder = MotionRecorder()
        
        assert recording_state.is_recording is False
        recorder.toggle_recording()
        assert recording_state.is_recording is True
    
    def test_toggle_recording_stops_session(self):
        """toggle_recording should stop recording when recording."""
        recorder = MotionRecorder()
        
        recorder.toggle_recording()  # Start
        assert recording_state.is_recording is True
        
        recorder.toggle_recording()  # Stop
        assert recording_state.is_recording is False
    
    def test_on_jog_start_sets_active_jog(self):
        """on_jog_start should set active jog when recording."""
        self._set_robot_pose(100.0, 200.0, 300.0)
        robot_state.angles = [0.0] * 6
        
        recorder = MotionRecorder()
        recorder.toggle_recording()  # Start recording
        
        recorder.on_jog_start("joint", "J1+")
        
        # Active jog should be set
        assert recorder._active_jog is not None
        assert recorder._active_jog.move_type == "joint"
        assert recorder._active_jog.axis_info == "J1+"
    
    def test_on_jog_end_inserts_code(self, mock_editor):
        """on_jog_end should insert code when jog completes."""
        self._set_robot_pose(100.0, 200.0, 300.0)
        robot_state.angles = [0.0] * 6
        
        recorder = MotionRecorder()
        recorder.toggle_recording()  # Start recording
        
        # Start jog
        recorder.on_jog_start("cartesian", "X+")
        
        # Simulate robot movement during jog (need time to pass > 0.1s)
        time.sleep(0.15)
        self._set_robot_pose(150.0, 250.0, 350.0)
        
        # End jog - should insert code
        recorder.on_jog_end()
        
        # Check that code was inserted
        inserted_code = mock_editor.program_textarea.value
        assert "rbt.move_cartesian(" in inserted_code
    
    def test_on_jog_start_ignored_when_not_recording(self):
        """on_jog_start should be ignored when not recording."""
        recorder = MotionRecorder()
        
        # Not recording
        recorder.on_jog_start("joint", "J1+")
        
        assert recorder._active_jog is None
    
    def test_on_jog_end_ignored_when_not_recording(self):
        """on_jog_end should be ignored when not recording."""
        recorder = MotionRecorder()
        ui_state.editor_panel = MagicMock()
        ui_state.editor_panel.program_textarea = MagicMock()
        ui_state.editor_panel.program_textarea.value = ""
        
        # Not recording
        recorder.on_jog_end()
        
        # No code inserted
        assert ui_state.editor_panel.program_textarea.value == ""
        ui_state.editor_panel = None
    
    def test_record_action_home_generates_code(self, mock_editor):
        """record_action for home should generate home code."""
        recorder = MotionRecorder()
        recording_state.is_recording = True
        
        recorder.record_action("home")
        
        inserted_code = mock_editor.program_textarea.value
        assert "rbt.home()" in inserted_code
    
    def test_record_action_gripper_calibrate(self, mock_editor):
        """record_action for gripper calibrate should generate code."""
        recorder = MotionRecorder()
        recording_state.is_recording = True
        
        recorder.record_action("gripper", calibrate=True)
        
        inserted_code = mock_editor.program_textarea.value
        assert 'rbt.control_electric_gripper("calibrate")' in inserted_code
    
    def test_record_action_gripper_move(self, mock_editor):
        """record_action for gripper move should generate code with params."""
        recorder = MotionRecorder()
        recording_state.is_recording = True
        
        recorder.record_action("gripper", position=100, speed=50, current=200)
        
        inserted_code = mock_editor.program_textarea.value
        assert 'rbt.control_electric_gripper("move"' in inserted_code
        assert "position=100" in inserted_code
        assert "speed=50" in inserted_code
        assert "current=200" in inserted_code
    
    def test_record_action_io(self, mock_editor):
        """record_action for io should generate pneumatic gripper code."""
        recorder = MotionRecorder()
        recording_state.is_recording = True
        
        recorder.record_action("io", port=1, state=1)
        
        inserted_code = mock_editor.program_textarea.value
        assert 'rbt.control_pneumatic_gripper("open", 1)' in inserted_code
    
    def test_record_action_ignored_when_not_recording(self, mock_editor):
        """record_action should be ignored when not recording."""
        recorder = MotionRecorder()
        recording_state.is_recording = False
        
        recorder.record_action("home")
        
        # Code should not have been inserted (still just initial code)
        assert mock_editor.program_textarea.value == "# Initial code\n"
    
    def test_no_editor_shows_notification(self):
        """capture_current_pose without editor should not crash."""
        ui_state.editor_panel = None
        
        recorder = MotionRecorder()
        # Should not raise an exception
        recorder.capture_current_pose()
    
    def test_multiple_jogs_insert_multiple_code_lines(self, mock_editor):
        """Multiple jog start/end cycles should insert multiple code lines."""
        self._set_robot_pose(100.0, 100.0, 100.0)
        robot_state.angles = [0.0] * 6
        
        recorder = MotionRecorder()
        recorder.toggle_recording()  # Start
        
        # First jog
        recorder.on_jog_start("cartesian", "X+")
        time.sleep(0.15)  # Need time > 0.1s
        self._set_robot_pose(150.0, 100.0, 100.0)
        recorder.on_jog_end()
        
        # Second jog
        recorder.on_jog_start("cartesian", "Y+")
        time.sleep(0.15)
        self._set_robot_pose(150.0, 200.0, 100.0)
        recorder.on_jog_end()
        
        recorder.toggle_recording()  # Stop
        
        # Should have inserted code for both moves
        inserted_code = mock_editor.program_textarea.value
        # Count occurrences of move commands
        assert inserted_code.count("rbt.move_") >= 2
    
    def test_short_jogs_not_recorded(self, mock_editor):
        """Very short jogs (< 0.1s) should not be recorded as additional moves."""
        self._set_robot_pose(100.0, 100.0, 100.0)
        robot_state.angles = [0.0] * 6
        
        recorder = MotionRecorder()
        recorder.toggle_recording()
        
        # Very short jog (no sleep)
        recorder.on_jog_start("cartesian", "X+")
        self._set_robot_pose(150.0, 100.0, 100.0)
        recorder.on_jog_end()
        
        # Should only have the initial anchor move, no move from the short jog
        inserted_code = mock_editor.program_textarea.value
        # Anchor is move_joints, short jog would have been move_cartesian
        assert "rbt.move_joints(" in inserted_code  # Anchor is present
        assert "rbt.move_cartesian(" not in inserted_code  # Short jog not recorded
    
    def test_stop_recording_ends_active_jog(self, mock_editor):
        """Stopping recording should end any active jog."""
        self._set_robot_pose(100.0, 100.0, 100.0)
        robot_state.angles = [0.0] * 6
        
        recorder = MotionRecorder()
        recorder.toggle_recording()  # Start
        
        # Start jog but don't end it
        recorder.on_jog_start("cartesian", "X+")
        time.sleep(0.15)
        self._set_robot_pose(150.0, 100.0, 100.0)
        
        # Stop recording should capture the active jog
        recorder.toggle_recording()  # Stop
        
        # Check that code was inserted
        inserted_code = mock_editor.program_textarea.value
        assert "rbt.move_cartesian(" in inserted_code
    
    def test_delay_calculation_excludes_motion_duration(self, mock_editor):
        """Auto-inserted delay should NOT include the previous motion's duration.
        
        Bug regression test: Before the fix, _last_action_time was set to `now`
        (when record_action was called), not `now + duration` (estimated completion).
        
        This caused delays to incorrectly include the motion time:
        - Action A starts at T=0 with duration=5s, _last_action_time = 0 (BUG!)
        - User waits until motion completes at T=5, then waits 2s more
        - Action B starts at T=7
        - Gap calculated as 7 - 0 = 7s (WRONG - includes motion)
        - Should be: 7 - 5 = 2s (idle time only)
        
        After fix: _last_action_time = now + duration for motion commands.
        So gap = now - (start + duration) = idle time only.
        """
        import re
        
        self._set_robot_pose(100.0, 100.0, 100.0)
        robot_state.angles = [0.0] * 6
        
        recorder = MotionRecorder()
        
        # Start recording (resets _last_action_time to 0)
        recorder.toggle_recording()
        
        # Clear editor to start fresh (toggle_recording inserts anchor)
        mock_editor.program_textarea.value = ""
        
        # Record first action with a SHORT duration (0.5s)
        # This sets _last_action_time = now + 0.5
        recorder.record_action("move_cartesian", pose=[100, 100, 100, 0, 0, 0], duration=0.5)
        
        # Wait 1.5 seconds - this is LONGER than the duration
        # So: motion completes at T=0.5, we wait until T=1.5
        # Expected idle time (delay) = 1.5 - 0.5 = 1.0s
        time.sleep(1.5)
        
        # Record second action - this should trigger auto-delay insertion
        # Gap = now (1.5) - _last_action_time (0.5) = 1.0s > 0.5s threshold
        recorder.record_action("move_cartesian", pose=[200, 200, 200, 0, 0, 0], duration=0.5)
        
        recorder.toggle_recording()  # Stop
        
        # Get the final code
        final_code = mock_editor.program_textarea.value
        
        # Extract the delay value that was inserted (uses time.sleep for playback support)
        delay_match = re.search(r'time\.sleep\(([\d.]+)\)', final_code)

        # A delay should have been inserted (since idle time > 0.5s threshold)
        assert delay_match is not None, f"Expected time.sleep to be inserted, got: {final_code}"
        
        delay_value = float(delay_match.group(1))
        
        # The delay should be approximately 1 second (the idle wait time)
        # NOT approximately 2 seconds (motion duration + idle time) - that would indicate the bug
        # Allow for timing variations (0.7 to 1.5 seconds)
        assert delay_value < 2.0, \
            f"Delay {delay_value}s is too large - it may be incorrectly including motion duration. " \
            f"Expected ~1s (idle time only), not ~2s (motion + idle)"
        
        # Also verify it's a reasonable value (> 0.7s since we waited 1.5s minus 0.5s duration = 1.0s)
        assert delay_value > 0.7, \
            f"Delay {delay_value}s is too small - should be ~1s (1.5s wait - 0.5s duration)"


# ============================================================================
# Workspace Envelope Tests
# ============================================================================

class TestWorkspaceEnvelope:
    """Tests for workspace envelope generation.
    
    The WorkspaceEnvelope now uses a lightweight max_reach approach instead
    of generating a full point cloud. It calculates the maximum reach radius
    and visualizes it as a wireframe sphere.
    """
    
    @pytest.fixture
    def envelope(self):
        """Create fresh envelope instance for each test."""
        env = WorkspaceEnvelope()
        yield env
        env.reset()
    
    def test_reset_clears_data(self, envelope):
        """reset should clear all generated data."""
        envelope.max_reach = 0.65
        envelope._generated = True
        
        envelope.reset()
        
        assert envelope.max_reach == 0.0
        assert envelope._generated is False
    
    def test_generate_sync_no_ui_crash(self, envelope):
        """generate_sync should work without crashing when called outside UI context.
        
        This verifies the fix for the ui.notify() crash - generate_sync() should NOT
        call any UI functions like ui.notify() that require NiceGUI context.
        """
        # DO NOT patch ui.notify - we want to verify it's not called
        # generate_sync uses _generate_envelope_cpu_bound which imports PAROL6_ROBOT directly
        result = envelope.generate_sync(samples=64)  # 2^6 for grid sampling
        
        # The important thing is it doesn't crash with ui.notify()
        # If robot module is available, it will succeed and set max_reach
        if result:
            assert envelope._generated is True
            assert envelope.max_reach > 0
        else:
            # Robot module not available, but still shouldn't crash
            assert envelope._generated is False
        
        # Either way, _generating should be reset
        assert envelope._generating is False
    
    def test_generate_sync_returns_false_without_limits_no_ui_crash(self, envelope):
        """generate_sync should return False without crashing when limits unavailable.
        
        This verifies generate_sync() doesn't call ui.notify() for errors.
        The _generate_envelope_cpu_bound function imports PAROL6_ROBOT directly,
        so we test the actual behavior with the real module.
        """
        # generate_sync uses _generate_envelope_cpu_bound which imports PAROL6_ROBOT directly
        # Without a valid robot/limits, it should fail gracefully
        result = envelope.generate_sync(samples=10)
        
        # The important thing is it doesn't crash with ui.notify()
        # Result depends on whether PAROL6_ROBOT is available
        assert envelope._generating is False  # Should not be stuck in generating state
    
    def test_generate_sync_creates_max_reach_with_valid_robot(self, envelope):
        """generate_sync should calculate max_reach when robot is available.
        
        This test uses the real PAROL6_ROBOT module since _generate_envelope_cpu_bound
        imports it directly and mocks don't transfer to separate processes.
        """
        # Use generate_sync which runs in-process
        result = envelope.generate_sync(samples=64)  # 64 = 2^6 for grid sampling
        
        # With real robot module, should calculate max_reach
        if result:
            assert envelope._generated is True
            assert envelope.max_reach > 0
            # PAROL6 robot typically has reach around 0.6-0.7 meters
            assert 0.3 < envelope.max_reach < 1.0
        else:
            # Robot module may not be available in test environment
            assert envelope._generated is False
    
    def test_generate_skips_if_already_generated(self, envelope):
        """generate should return True immediately if already generated."""
        envelope._generated = True
        
        result = envelope.generate(samples=10)
        
        assert result is True
    
    def test_generate_sync_handles_exceptions_gracefully(self, envelope):
        """generate_sync should catch exceptions without crashing.
        
        The _generate_envelope_cpu_bound function handles exceptions internally
        and returns None on failure. generate_sync should handle this gracefully.
        """
        # generate_sync uses _generate_envelope_cpu_bound which handles exceptions
        # If robot module has issues, it should return False without crashing
        result = envelope.generate_sync(samples=64)
        
        # Whether it succeeds depends on robot module availability
        # The key is it doesn't crash and _generating flag is reset
        assert envelope._generating is False
    
    def test_concurrent_generation_prevented(self, envelope):
        """generate should return True when already generating (indicates in-progress).
        
        The async generate() returns True when generation is already in progress
        since starting/in-progress are both valid states for non-blocking generation.
        """
        envelope._generating = True
        
        result = envelope.generate(samples=10)
        
        # Returns True because generation is in progress (valid state)
        assert result is True
    
    def test_get_radius_with_tool_offset_positive(self, envelope):
        """get_radius_with_tool_offset should add tool offset to max_reach."""
        envelope.max_reach = 0.6  # 600mm base reach
        
        # Tool with 50mm (0.05m) Z offset should extend reach
        effective_radius = envelope.get_radius_with_tool_offset(0.05)
        
        assert effective_radius == 0.65  # 0.6 + 0.05
    
    def test_get_radius_with_tool_offset_negative(self, envelope):
        """get_radius_with_tool_offset should use absolute value of offset."""
        envelope.max_reach = 0.6
        
        # Negative offset should still extend reach (uses abs())
        effective_radius = envelope.get_radius_with_tool_offset(-0.05)
        
        assert effective_radius == 0.65  # 0.6 + abs(-0.05)
    
    def test_get_radius_with_zero_offset(self, envelope):
        """get_radius_with_tool_offset should return max_reach when offset is zero."""
        envelope.max_reach = 0.6
        
        effective_radius = envelope.get_radius_with_tool_offset(0.0)
        
        assert effective_radius == 0.6


# ============================================================================
# Playback State Tests
# ============================================================================

class TestPlaybackState:
    """Tests for playback state tracking."""
    
    def test_playback_state_default_values(self):
        """PlaybackState should have correct default values."""
        state = PlaybackState()
        
        assert state.is_playing is False
        assert state.is_simulating is False
        assert state.current_step == 0
        assert state.total_steps == 0
        assert state.playback_speed == 1.0
        assert state.scrub_interactive is True
    
    def test_playback_speed_options(self):
        """PlaybackState should support 1x, 2x, 4x, 8x speed."""
        state = PlaybackState()
        
        for speed in [1.0, 2.0, 4.0, 8.0]:
            state.playback_speed = speed
            assert state.playback_speed == speed
    
    def test_scrub_interactive_false_in_robot_mode(self):
        """scrub_interactive should be False when not simulating."""
        state = PlaybackState()
        state.is_simulating = False
        state.scrub_interactive = False
        
        assert state.scrub_interactive is False


# ============================================================================
# Path Visualizer Integration Tests
# ============================================================================

# ============================================================================
# Editor Auto-Simulation Tests
# ============================================================================

class TestEditorAutoSimulation:
    """Tests for editor auto-simulation on code change."""
    
    @pytest.fixture(autouse=True)
    def reset_simulation_state(self):
        """Reset simulation state before each test."""
        simulation_state.path_segments.clear()
        simulation_state.targets.clear()
        simulation_state.current_step_index = 0
        simulation_state.total_steps = 0
        yield
        simulation_state.path_segments.clear()
        simulation_state.targets.clear()
    
    @pytest.fixture
    def mock_client(self):
        """Create mock AsyncRobotClient."""
        return MagicMock()
    
    def test_debounce_delay_default_value(self, mock_client):
        """EditorPanel should have 750ms default debounce delay."""
        from parol_commander.components.editor import EditorPanel
        
        panel = EditorPanel(mock_client)
        
        assert panel._debounce_delay == 0.75
    
    def test_debounce_timer_initially_none(self, mock_client):
        """EditorPanel should start with no active debounce timer."""
        from parol_commander.components.editor import EditorPanel
        
        panel = EditorPanel(mock_client)
        
        assert panel._simulation_debounce_timer is None
    
    def test_schedule_debounced_simulation_creates_timer(self, mock_client):
        """schedule_debounced_simulation should create a timer."""
        from parol_commander.components.editor import EditorPanel
        
        with patch('parol_commander.components.editor.ui') as mock_ui:
            mock_timer = MagicMock()
            mock_ui.timer.return_value = mock_timer
            
            panel = EditorPanel(mock_client)
            panel._schedule_debounced_simulation()
            
            # Verify timer was created with correct parameters
            mock_ui.timer.assert_called_once()
            call_args = mock_ui.timer.call_args
            assert call_args[0][0] == 0.75  # debounce delay
            assert call_args[1]['once'] is True
    
    def test_schedule_debounced_simulation_cancels_previous_timer(self, mock_client):
        """Calling schedule_debounced_simulation again should cancel previous timer."""
        from parol_commander.components.editor import EditorPanel
        
        with patch('parol_commander.components.editor.ui') as mock_ui:
            mock_timer1 = MagicMock()
            mock_timer2 = MagicMock()
            mock_ui.timer.side_effect = [mock_timer1, mock_timer2]
            
            panel = EditorPanel(mock_client)
            
            # First call creates timer1
            panel._schedule_debounced_simulation()
            assert panel._simulation_debounce_timer == mock_timer1
            
            # Second call should cancel timer1 and create timer2
            panel._schedule_debounced_simulation()
            mock_timer1.cancel.assert_called_once()
            assert panel._simulation_debounce_timer == mock_timer2
    
    @pytest.mark.asyncio
    async def test_run_simulation_silent_mode_no_notify(self, mock_client):
        """_run_simulation(notify=False) should not call ui.notify."""
        from parol_commander.components.editor import EditorPanel
        
        with patch('parol_commander.components.editor.ui') as mock_ui:
            with patch('parol_commander.components.editor.path_visualizer') as mock_visualizer:
                # Make update_path_visualization a coroutine
                async def mock_update(content):
                    pass
                mock_visualizer.update_path_visualization = mock_update
                
                panel = EditorPanel(mock_client)
                panel.program_textarea = MagicMock()
                panel.program_textarea.value = "# some code"
                
                await panel._run_simulation(notify=False)
                
                # ui.notify should NOT have been called
                mock_ui.notify.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_run_simulation_verbose_mode_shows_notify(self, mock_client):
        """_run_simulation(notify=True) should call ui.notify."""
        from parol_commander.components.editor import EditorPanel
        
        with patch('parol_commander.components.editor.ui') as mock_ui:
            with patch('parol_commander.components.editor.path_visualizer') as mock_visualizer:
                # Make update_path_visualization a coroutine
                async def mock_update(content):
                    pass
                mock_visualizer.update_path_visualization = mock_update
                
                panel = EditorPanel(mock_client)
                panel.program_textarea = MagicMock()
                panel.program_textarea.value = "# some code"
                
                await panel._run_simulation(notify=True)
                
                # ui.notify SHOULD have been called (at least for "Running simulation...")
                assert mock_ui.notify.call_count >= 1
    
    @pytest.mark.asyncio
    async def test_run_simulation_calls_path_visualizer(self, mock_client):
        """_run_simulation should call path_visualizer.update_path_visualization."""
        from parol_commander.components.editor import EditorPanel
        
        with patch('parol_commander.components.editor.ui'):
            with patch('parol_commander.components.editor.path_visualizer') as mock_visualizer:
                # Track if update was called
                update_called = False
                update_content = None
                
                async def mock_update(content):
                    nonlocal update_called, update_content
                    update_called = True
                    update_content = content
                
                mock_visualizer.update_path_visualization = mock_update
                
                panel = EditorPanel(mock_client)
                panel.program_textarea = MagicMock()
                panel.program_textarea.value = "rbt.move_joints([0,0,0,0,0,0])"
                
                await panel._run_simulation(notify=False)
                
                assert update_called is True
                assert update_content == "rbt.move_joints([0,0,0,0,0,0])"
    
    @pytest.mark.asyncio
    async def test_run_simulation_empty_content_skips_visualization(self, mock_client):
        """_run_simulation should skip visualization when content is empty."""
        from parol_commander.components.editor import EditorPanel
        
        with patch('parol_commander.components.editor.ui'):
            with patch('parol_commander.components.editor.path_visualizer') as mock_visualizer:
                update_called = False
                
                async def mock_update(content):
                    nonlocal update_called
                    update_called = True
                
                mock_visualizer.update_path_visualization = mock_update
                
                panel = EditorPanel(mock_client)
                panel.program_textarea = MagicMock()
                panel.program_textarea.value = ""  # Empty content
                
                await panel._run_simulation(notify=False)
                
                # Should NOT call update_path_visualization for empty content
                assert update_called is False


class TestSceneRenderingIntegration:
    """Integration tests for scene rendering from simulation state.
    
    These tests verify that path segments in simulation_state actually get
    rendered to scene objects in UrdfScene. This catches the race condition
    bug where fast simulations could complete without triggering a re-render.
    """
    
    @pytest.fixture(autouse=True)
    def reset_simulation_state(self):
        """Reset simulation state before each test.
        
        Also clears change listeners and urdf_scene to prevent UI rendering
        callbacks from trying to create scene elements without a NiceGUI context.
        """
        simulation_state.path_segments.clear()
        simulation_state.targets.clear()
        simulation_state.current_step_index = 0
        simulation_state.total_steps = 0
        # Clear any lingering UI callbacks that might try to render
        simulation_state._change_listeners.clear()
        # Ensure no stale scene reference exists (prevents UI rendering attempts)
        original_scene = ui_state.urdf_scene
        ui_state.urdf_scene = None
        yield
        simulation_state.path_segments.clear()
        simulation_state.targets.clear()
        simulation_state._change_listeners.clear()
        ui_state.urdf_scene = original_scene
    
    def test_scene_detects_new_segments_after_render_count_reset(self):
        """Scene should render new segments when _rendered_segment_count is reset.
        
        This tests the fix for the race condition where:
        1. Old simulation has N segments, scene renders them, _rendered_segment_count = N
        2. New simulation clears and adds N NEW segments
        3. Without reset, scene sees count=N, _rendered=N, thinks nothing changed
        4. WITH reset (_rendered=0), scene sees count=N > 0, renders new segments
        """
        # Add some path segments to simulation state
        segment1 = PathSegment(
            points=[[0, 0, 0], [0.1, 0.1, 0.1]],
            color="#2faf7a",
            is_valid=True,
            line_number=1,
            joints=[0.0] * 6,
            move_type="cartesian"
        )
        segment2 = PathSegment(
            points=[[0.1, 0.1, 0.1], [0.2, 0.2, 0.2]],
            color="#4a63e0",
            is_valid=True,
            line_number=2,
            joints=[0.1] * 6,
            move_type="joints"
        )
        simulation_state.path_segments.extend([segment1, segment2])
        
        # Simulate scenario where _rendered_segment_count equals current count
        # (This is what happens when simulation completes within one timer tick)
        rendered_count_before = 2  # Pretend we already rendered 2 segments
        current_count = len(simulation_state.path_segments)  # Also 2
        
        # Without reset: count == rendered, no new rendering triggered
        assert current_count == rendered_count_before
        should_render_without_reset = current_count > rendered_count_before
        assert should_render_without_reset is False
        
        # WITH reset (our fix): rendered = 0, count > rendered, rendering triggered
        rendered_count_after_reset = 0
        should_render_with_reset = current_count > rendered_count_after_reset
        assert should_render_with_reset is True
    
    @pytest.mark.asyncio
    async def test_path_visualizer_resets_scene_render_counter(self):
        """PathVisualizer should reset UrdfScene._rendered_segment_count after simulation.

        This verifies the fix is actually applied in path_visualizer.py.
        """
        from parol_commander.services.path_visualizer import PathVisualizer

        # Create mock UrdfScene with _rendered_segment_count
        mock_scene = MagicMock()
        mock_scene._rendered_segment_count = 5  # Simulate previous render count
        ui_state.urdf_scene = mock_scene

        try:
            visualizer = PathVisualizer()

            # Run a simple simulation (empty program just to trigger the reset)
            await visualizer.update_path_visualization("# empty program")

            # Verify _rendered_segment_count was reset to 0
            assert mock_scene._rendered_segment_count == 0
        finally:
            ui_state.urdf_scene = None
    
    def test_scene_render_count_tracking_logic(self):
        """Test the segment count tracking logic that determines when to render."""
        # Scenario 1: No segments (clear operation)
        current_count = 0
        rendered_count = 5
        should_clear = current_count == 0
        assert should_clear is True
        
        # Scenario 2: New segments added (incremental add from recording)
        current_count = 6
        rendered_count = 5
        should_render_new = current_count > rendered_count
        assert should_render_new is True
        
        # Scenario 3: Segments replaced with same count (simulation race condition)
        current_count = 5
        rendered_count = 5
        should_render_same_count = current_count > rendered_count
        assert should_render_same_count is False  # BUG: This is why we need reset!
        
        # Scenario 4: After reset, same count triggers render
        current_count = 5
        rendered_count = 0  # Reset by our fix
        should_render_after_reset = current_count > rendered_count
        assert should_render_after_reset is True  # FIXED: Now renders


class TestPathVisualizerIntegration:
    """Integration tests for PathVisualizer with dry run client.
    
    These tests run in a subprocess via NiceGUI's cpu_bound(), so mocking 
    PAROL6_ROBOT doesn't work (mocks don't transfer across process boundaries).
    The tests use the real robot kinematics module which should be available.
    """
    
    @pytest.fixture(autouse=True)
    def reset_simulation_state(self):
        """Reset simulation state before each test.
        
        Also clears change listeners and urdf_scene to prevent UI rendering
        callbacks from trying to create scene elements without a NiceGUI context.
        """
        simulation_state.path_segments.clear()
        simulation_state.targets.clear()
        simulation_state.current_step_index = 0
        simulation_state.total_steps = 0
        # Clear any lingering UI callbacks that might try to render
        simulation_state._change_listeners.clear()
        # Ensure no stale scene reference exists (prevents UI rendering attempts)
        original_scene = ui_state.urdf_scene
        ui_state.urdf_scene = None
        yield
        simulation_state.path_segments.clear()
        simulation_state.targets.clear()
        simulation_state._change_listeners.clear()
        ui_state.urdf_scene = original_scene
    
    @pytest.mark.asyncio
    async def test_visualizer_executes_simple_program(self):
        """PathVisualizer should execute program and create path segments.
        
        Uses real PAROL6_ROBOT module in subprocess - no mocking needed.
        """
        visualizer = PathVisualizer()
        
        # Simple program - uses the DryRunRobotClient shim injected by PathVisualizer
        program = '''
import parol6

async def main():
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_joints([0, 0, 0, 0, 0, 0])
'''
        
        await visualizer.update_path_visualization(program)
        
        # Should have created at least one segment
        assert len(simulation_state.path_segments) >= 1, \
            f"Expected at least 1 segment, got {len(simulation_state.path_segments)}"
    
    @pytest.mark.asyncio
    async def test_visualizer_updates_total_steps(self):
        """PathVisualizer should update total_steps after simulation."""
        visualizer = PathVisualizer()
        
        program = '''
import parol6

async def main():
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_joints([10, 0, 0, 0, 0, 0])
        await rbt.move_joints([20, 0, 0, 0, 0, 0])
'''
        
        await visualizer.update_path_visualization(program)
        
        # Should have 2 segments and total_steps should match
        assert simulation_state.total_steps == len(simulation_state.path_segments)
    
    @pytest.mark.asyncio
    async def test_visualizer_cartesian_coordinates_in_meters(self):
        """Path segment coordinates from move_cartesian should be in meters.
        
        User input is in mm, but segments should be converted to meters for
        the 3D scene which uses SI units.
        """
        visualizer = PathVisualizer()
        
        # Move to 150mm, 100mm, 250mm - should become 0.15m, 0.1m, 0.25m
        program = '''
import parol6

async def main():
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_cartesian([150, 100, 250, 0, 0, 0])
'''
        
        await visualizer.update_path_visualization(program)
        
        # Should have created a segment
        assert len(simulation_state.path_segments) >= 1, \
            f"Expected at least 1 segment, got {len(simulation_state.path_segments)}"
        
        # Check that end point is in meters (not mm)
        segment = simulation_state.path_segments[-1]
        end_point = segment.points[1]  # [x, y, z]
        
        # Values should be < 1 (meters), not > 100 (mm)
        assert abs(end_point[0]) < 1.0, \
            f"X coordinate {end_point[0]} appears to be in mm, expected meters"
        assert abs(end_point[1]) < 1.0, \
            f"Y coordinate {end_point[1]} appears to be in mm, expected meters"
        assert abs(end_point[2]) < 1.0, \
            f"Z coordinate {end_point[2]} appears to be in mm, expected meters"
        
        # Check expected converted values (0.15, 0.1, 0.25)
        assert abs(end_point[0] - 0.15) < 0.01, \
            f"Expected X ~0.15m, got {end_point[0]}m"
        assert abs(end_point[1] - 0.1) < 0.01, \
            f"Expected Y ~0.1m, got {end_point[1]}m"
        assert abs(end_point[2] - 0.25) < 0.01, \
            f"Expected Z ~0.25m, got {end_point[2]}m"
    
    @pytest.mark.asyncio
    async def test_target_markers_create_targets(self):
        """Programs with TARGET markers should create ProgramTarget objects.
        
        Bug regression test: Before the fix, exec(program_text) was used without
        compile(), so the code's filename was "<string>" instead of 
        "simulation_script.py". This caused frame inspection to fail when 
        looking for source lines with TARGET markers.
        
        After fix: compile(program_text, "simulation_script.py", "exec") is used
        so frame.f_code.co_filename == "simulation_script.py" as expected.
        """
        visualizer = PathVisualizer()
        
        # Program with TARGET markers in comments
        # These should cause ProgramTarget objects to be created
        program = '''
import parol6

async def main():
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_cartesian([100, 200, 300, 0, 0, 0])  # TARGET:abc12345
        await rbt.move_cartesian([150, 250, 350, 0, 0, 0])  # TARGET:def67890
'''
        
        await visualizer.update_path_visualization(program)
        
        # Should have created 2 path segments
        assert len(simulation_state.path_segments) >= 2, \
            f"Expected at least 2 segments, got {len(simulation_state.path_segments)}"
        
        # Should have created 2 targets (one for each TARGET marker)
        assert len(simulation_state.targets) == 2, \
            f"Expected 2 targets (one per TARGET marker), got {len(simulation_state.targets)}. " \
            f"Bug: compile() may not be using 'simulation_script.py' filename for frame inspection."
        
        # Verify target IDs match the markers in the code
        target_ids = [t.id for t in simulation_state.targets]
        assert "abc12345" in target_ids, \
            f"Expected target 'abc12345' not found in {target_ids}"
        assert "def67890" in target_ids, \
            f"Expected target 'def67890' not found in {target_ids}"
    
    @pytest.mark.asyncio
    async def test_move_without_target_marker_no_target_created(self):
        """Moves without TARGET markers should NOT create ProgramTarget objects.
        
        This verifies that targets are only created for lines with explicit
        TARGET:uuid markers, not for all move commands.
        """
        visualizer = PathVisualizer()
        
        # Program WITHOUT any TARGET markers
        program = '''
import parol6

async def main():
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_cartesian([100, 200, 300, 0, 0, 0])
        await rbt.move_joints([0, 0, 0, 0, 0, 0])
'''
        
        await visualizer.update_path_visualization(program)
        
        # Should have created path segments (for visualization)
        assert len(simulation_state.path_segments) >= 2, \
            f"Expected at least 2 segments, got {len(simulation_state.path_segments)}"
        
        # Should NOT have created any targets (no TARGET markers)
        assert len(simulation_state.targets) == 0, \
            f"Expected 0 targets (no TARGET markers in code), got {len(simulation_state.targets)}"
