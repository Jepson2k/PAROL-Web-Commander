"""Functional tests for simulation services.

These tests verify actual behavior rather than just checking if buttons exist.
"""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np

from parol_commander.state import (
    simulation_state,
    recording_state,
    robot_state,
    ui_state,
)
from parol_commander.services.dry_run_client import DryRunRobotClient
from parol_commander.services.motion_recorder import MotionRecorder
from parol_commander.services.path_visualizer import PathVisualizer
from parol_commander.services.urdf_scene.envelope_mixin import WorkspaceEnvelope


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

        with patch(
            "parol_commander.services.dry_run_client.PAROL6_ROBOT", mock_robot_module
        ):
            with patch(
                "parol_commander.services.dry_run_client.check_limits",
                return_value=True,
            ):
                segments: list[dict] = []
                targets: list[dict] = []
                client = DryRunRobotClient(
                    segment_collector=segments, target_collector=targets
                )

                # Use angles away from singularities (J5 != 0 avoids gimbal lock)
                client.move_joints([85, -85, 135, 10, 45, 170])

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

        with patch(
            "parol_commander.services.dry_run_client.PAROL6_ROBOT", mock_robot_module
        ):
            with patch(
                "parol_commander.services.dry_run_client.solve_ik", mock_solve_ik
            ):
                segments: list[dict] = []
                targets: list[dict] = []
                client = DryRunRobotClient(
                    segment_collector=segments, target_collector=targets
                )

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

        with patch(
            "parol_commander.services.dry_run_client.PAROL6_ROBOT", mock_robot_module
        ):
            segments: list[dict] = []
            targets: list[dict] = []
            client = DryRunRobotClient(
                segment_collector=segments, target_collector=targets
            )

            # Use angles away from singularities (J5 != 0 avoids gimbal lock)
            client.move_joints([85, -85, 135, 10, 45, 170])

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

        with patch(
            "parol_commander.services.dry_run_client.PAROL6_ROBOT", mock_robot_module
        ):
            with patch(
                "parol_commander.services.dry_run_client.solve_ik", mock_solve_ik
            ):
                segments: list[dict] = []
                targets: list[dict] = []
                client = DryRunRobotClient(
                    segment_collector=segments, target_collector=targets
                )

                client.move_cartesian([9999, 9999, 9999, 0, 0, 0])

                assert len(segments) == 1
                segment = segments[0]
                assert segment["is_valid"] is False


# ============================================================================
# Motion Recorder Tests
# ============================================================================


class TestMotionRecorder:
    """Tests for motion recording functionality (code-insertion API)."""

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
            1.0,
            0.0,
            0.0,
            x,
            0.0,
            1.0,
            0.0,
            y,
            0.0,
            0.0,
            1.0,
            z,
            0.0,
            0.0,
            0.0,
            1.0,
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
        robot_state.angles.set_deg(np.array([10.0, 20.0, 30.0, 40.0, 50.0, 60.0]))

        recorder = MotionRecorder()
        recorder.capture_current_pose(move_type="joints")

        inserted_code = mock_editor.program_textarea.value
        assert (
            "rbt.move_joints([10.00, 20.00, 30.00, 40.00, 50.00, 60.00" in inserted_code
        )

    def test_toggle_recording_lifecycle(self, mock_editor):
        """toggle_recording should toggle recording state on/off."""
        recorder = MotionRecorder()

        # Initially not recording
        assert recording_state.is_recording is False

        # First toggle starts recording
        recorder.toggle_recording()
        assert recording_state.is_recording is True

        # Second toggle stops recording
        recorder.toggle_recording()
        assert recording_state.is_recording is False

    def test_jog_recording_lifecycle(self, mock_editor):
        """Test complete jog recording cycle: start sets state, end inserts code."""
        self._set_robot_pose(100.0, 200.0, 300.0)
        robot_state.angles.set_deg(np.zeros(6))

        recorder = MotionRecorder()
        recorder.toggle_recording()  # Start recording

        # --- Part 1: on_jog_start should set active jog ---
        recorder.on_jog_start("cartesian", "X+")

        assert recorder._active_jog is not None
        assert recorder._active_jog.move_type == "cartesian"
        assert recorder._active_jog.axis_info == "X+"

        # --- Part 2: on_jog_end should insert code ---
        # Simulate robot movement during jog (need time to pass > 0.1s)
        time.sleep(0.15)
        self._set_robot_pose(150.0, 250.0, 350.0)

        recorder.on_jog_end()

        # Check that code was inserted
        inserted_code = mock_editor.program_textarea.value
        assert "rbt.move_cartesian(" in inserted_code

    def test_jog_events_ignored_when_not_recording(self):
        """Jog start and end events should be ignored when not recording."""
        recorder = MotionRecorder()
        ui_state.editor_panel = MagicMock()
        ui_state.editor_panel.program_textarea = MagicMock()
        ui_state.editor_panel.program_textarea.value = ""

        # Not recording - jog start should be ignored
        recorder.on_jog_start("joint", "J1+")
        assert recorder._active_jog is None

        # Not recording - jog end should also be ignored
        recorder.on_jog_end()
        assert ui_state.editor_panel.program_textarea.value == ""

        ui_state.editor_panel = None

    def test_record_action_home_generates_code(self, mock_editor):
        """record_action for home should generate home code."""
        recorder = MotionRecorder()
        recording_state.is_recording = True

        recorder.record_action("home")

        inserted_code = mock_editor.program_textarea.value
        assert "rbt.home()" in inserted_code

    def test_record_action_gripper_commands(self, mock_editor):
        """record_action for gripper should generate calibrate and move code."""
        recorder = MotionRecorder()
        recording_state.is_recording = True

        # Part 1: Calibrate command
        recorder.record_action("gripper", calibrate=True)
        inserted_code = mock_editor.program_textarea.value
        assert 'rbt.control_electric_gripper("calibrate")' in inserted_code

        # Part 2: Move command with params
        mock_editor.program_textarea.value = ""  # Reset
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

    def test_multiple_jogs_insert_multiple_code_lines(self, mock_editor):
        """Multiple jog start/end cycles should insert multiple code lines."""
        self._set_robot_pose(100.0, 100.0, 100.0)
        robot_state.angles.set_deg(np.zeros(6))

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
        robot_state.angles.set_deg(np.zeros(6))

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
        robot_state.angles.set_deg(np.zeros(6))

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
        robot_state.angles.set_deg(np.zeros(6))

        recorder = MotionRecorder()

        # Start recording (resets _last_action_time to 0)
        recorder.toggle_recording()

        # Clear editor to start fresh (toggle_recording inserts anchor)
        mock_editor.program_textarea.value = ""

        # Record first action with a SHORT duration (0.5s)
        # This sets _last_action_time = now + 0.5
        recorder.record_action(
            "move_cartesian", pose=[100, 100, 100, 0, 0, 0], duration=0.5
        )

        # Wait 1.5 seconds - this is LONGER than the duration
        # So: motion completes at T=0.5, we wait until T=1.5
        # Expected idle time (delay) = 1.5 - 0.5 = 1.0s
        time.sleep(1.5)

        # Record second action - this should trigger auto-delay insertion
        # Gap = now (1.5) - _last_action_time (0.5) = 1.0s > 0.5s threshold
        recorder.record_action(
            "move_cartesian", pose=[200, 200, 200, 0, 0, 0], duration=0.5
        )

        recorder.toggle_recording()  # Stop

        # Get the final code
        final_code = mock_editor.program_textarea.value

        # Extract the delay value that was inserted (uses time.sleep for playback support)
        delay_match = re.search(r"time\.sleep\(([\d.]+)\)", final_code)

        # A delay should have been inserted (since idle time > 0.5s threshold)
        assert delay_match is not None, (
            f"Expected time.sleep to be inserted, got: {final_code}"
        )

        delay_value = float(delay_match.group(1))

        # The delay should be approximately 1 second (the idle wait time)
        # NOT approximately 2 seconds (motion duration + idle time) - that would indicate the bug
        # Allow for timing variations (0.7 to 1.5 seconds)
        assert delay_value < 2.0, (
            f"Delay {delay_value}s is too large - it may be incorrectly including motion duration. "
            f"Expected ~1s (idle time only), not ~2s (motion + idle)"
        )

        # Also verify it's a reasonable value (> 0.7s since we waited 1.5s minus 0.5s duration = 1.0s)
        assert delay_value > 0.7, (
            f"Delay {delay_value}s is too small - should be ~1s (1.5s wait - 0.5s duration)"
        )


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
        _result = envelope.generate_sync(samples=64)

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

    @pytest.mark.parametrize(
        "offset,expected",
        [
            (0.05, 0.65),  # Positive offset extends reach
            (-0.05, 0.65),  # Negative offset uses abs()
            (0.0, 0.6),  # Zero offset returns base reach
        ],
    )
    def test_get_radius_with_tool_offset(self, envelope, offset, expected):
        """get_radius_with_tool_offset should add abs(offset) to max_reach."""
        envelope.max_reach = 0.6  # 600mm base reach

        effective_radius = envelope.get_radius_with_tool_offset(offset)

        assert effective_radius == expected, (
            f"With offset={offset}, expected {expected}, got {effective_radius}"
        )


# ============================================================================
# Editor Auto-Simulation Tests
# ============================================================================


class TestEditorAutoSimulation:
    """Tests for editor auto-simulation on code change."""

    @pytest.fixture
    def mock_client(self):
        """Create mock AsyncRobotClient."""
        return MagicMock()

    def test_debounce_defaults(self, mock_client):
        """EditorPanel should have correct debounce defaults."""
        from parol_commander.components.editor import EditorPanel

        panel = EditorPanel(mock_client)

        # Default delay is 375ms
        assert panel._debounce_delay == 0.375
        # Timer starts as None
        assert panel._simulation_debounce_timer is None

    def test_schedule_debounced_simulation_creates_timer(self, mock_client):
        """schedule_debounced_simulation should create a timer."""
        from parol_commander.components.editor import EditorPanel
        from parol_commander.state import editor_tabs_state

        with patch("parol_commander.components.editor.ui") as mock_ui:
            mock_timer = MagicMock()
            mock_ui.timer.return_value = mock_timer

            # Set up active tab so scheduling doesn't return early
            editor_tabs_state.active_tab_id = "test-tab"

            panel = EditorPanel(mock_client)
            panel._schedule_debounced_simulation()

            # Verify timer was created with correct parameters
            mock_ui.timer.assert_called_once()
            call_args = mock_ui.timer.call_args
            assert call_args[0][0] == 0.375  # debounce delay
            assert call_args[1]["once"] is True

    def test_schedule_debounced_simulation_cancels_previous_timer(self, mock_client):
        """Calling schedule_debounced_simulation again should cancel previous timer."""
        from parol_commander.components.editor import EditorPanel
        from parol_commander.state import editor_tabs_state

        with patch("parol_commander.components.editor.ui") as mock_ui:
            mock_timer1 = MagicMock()
            mock_timer2 = MagicMock()
            mock_ui.timer.side_effect = [mock_timer1, mock_timer2]

            # Set up active tab so scheduling doesn't return early
            editor_tabs_state.active_tab_id = "test-tab"

            panel = EditorPanel(mock_client)

            # First call creates timer1
            panel._schedule_debounced_simulation()
            assert panel._simulation_debounce_timer == mock_timer1

            # Second call should cancel timer1 and create timer2
            panel._schedule_debounced_simulation()
            mock_timer1.cancel.assert_called_once()
            assert panel._simulation_debounce_timer == mock_timer2

    @pytest.mark.asyncio
    async def test_run_simulation_notify_modes(self, mock_client):
        """_run_simulation notify parameter controls ui.notify behavior."""
        from parol_commander.components.editor import EditorPanel

        with patch("parol_commander.components.editor.ui") as mock_ui:
            with patch(
                "parol_commander.components.editor.path_visualizer"
            ) as mock_visualizer:

                async def mock_update(content, tab_id=None):
                    pass

                mock_visualizer.update_path_visualization = mock_update

                panel = EditorPanel(mock_client)
                panel.program_textarea = MagicMock()
                panel.program_textarea.value = "# some code"

                # Part 1: Silent mode - no notifications
                await panel._run_simulation(notify=False)
                mock_ui.notify.assert_not_called()

                # Part 2: Verbose mode - shows notifications
                await panel._run_simulation(notify=True)
                assert mock_ui.notify.call_count >= 1

    @pytest.mark.asyncio
    async def test_run_simulation_calls_path_visualizer(self, mock_client):
        """_run_simulation should call path_visualizer.update_path_visualization."""
        from parol_commander.components.editor import EditorPanel

        with patch("parol_commander.components.editor.ui"):
            with patch(
                "parol_commander.components.editor.path_visualizer"
            ) as mock_visualizer:
                # Track if update was called
                update_called = False
                update_content = None

                async def mock_update(content, tab_id=None):
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

        with patch("parol_commander.components.editor.ui"):
            with patch(
                "parol_commander.components.editor.path_visualizer"
            ) as mock_visualizer:
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


class TestSimulationCaching:
    """Tests for per-tab simulation caching and optimization.

    These tests verify:
    - Default script optimization skips simulation and uses cached home position
    - Non-default scripts trigger actual simulation
    - Results are stored in the originating tab, not the active tab
    - Anchor check uses cached final_joints_rad (instant, no blocking)
    """

    def test_default_script_detected(self):
        """_is_default_script returns True for default content, skipping simulation."""
        from parol_commander.components.editor import EditorPanel

        mock_client = MagicMock()
        panel = EditorPanel(mock_client)

        default_content = panel._default_python_snippet()
        assert panel._is_default_script(default_content) is True

        # Whitespace variations should still match
        assert panel._is_default_script(default_content + "\n\n  \n") is True

        # Non-default content should not match
        assert panel._is_default_script("rbt.move_joints([0,0,0,0,0,0])") is False

    def test_anchor_check_uses_cached_final_joints(self):
        """Anchor check reads from tab.final_joints_rad without blocking."""
        from parol_commander.state import editor_tabs_state, robot_state, EditorTab

        test_tab = EditorTab(
            id="test_tab",
            filename="test.py",
            file_path=None,
            content="print('test')",
            saved_content="print('test')",
        )
        editor_tabs_state.tabs.append(test_tab)
        editor_tabs_state.active_tab_id = "test_tab"

        recorder = MotionRecorder()

        # Robot at home position (degrees)
        robot_state.angles.set_deg(np.array([90.0, -90.0, 180.0, 0.0, 0.0, 180.0]))

        # Set cached final joints (matching position in radians)
        test_tab.final_joints_rad = [1.5708, -1.5708, 3.1416, 0.0, 0.0, 3.1416]

        # Should NOT insert anchor (positions match within tolerance)
        result = recorder._should_insert_anchor()
        assert not result, "Anchor should be skipped when robot matches cached position"

        # No cached result -> should insert anchor
        test_tab.final_joints_rad = None
        result = recorder._should_insert_anchor()
        assert result, "Anchor should be inserted when no cached position"

    @pytest.mark.asyncio
    async def test_results_stored_in_originating_tab(self):
        """Simulation results go to tab_id, not active tab (for tab switch during sim)."""
        from parol_commander.state import editor_tabs_state, simulation_state, EditorTab
        from parol_commander.services.path_visualizer import PathVisualizer

        # Create two tabs
        tab1 = EditorTab(
            id="tab1", filename="a.py", file_path=None, content="", saved_content=""
        )
        tab2 = EditorTab(
            id="tab2", filename="b.py", file_path=None, content="", saved_content=""
        )
        editor_tabs_state.tabs = [tab1, tab2]
        editor_tabs_state.active_tab_id = "tab2"  # Active is tab2

        # Mock run.cpu_bound to return test data and notify_changed to avoid slot stack error
        with (
            patch("parol_commander.services.path_visualizer.run") as mock_run,
            patch.object(simulation_state, "notify_changed"),
        ):
            mock_run.setup = MagicMock()
            mock_run.cpu_bound = AsyncMock(
                return_value={
                    "segments": [],
                    "targets": [],
                    "truncated": False,
                    "error": None,
                    "total_steps": 0,
                    "final_joints_rad": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
                }
            )

            visualizer = PathVisualizer()
            # Run simulation for tab1 (not active)
            await visualizer.update_path_visualization("print('hi')", tab_id="tab1")

            # Results should be in tab1, not tab2
            assert tab1.final_joints_rad == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
            assert tab2.final_joints_rad is None

    def test_simulation_returns_final_joints_rad(self):
        """Simulation result includes final_joints_rad for caching."""
        from parol_commander.services.path_visualizer import _run_simulation_isolated

        program = """
from parol6 import RobotClient
rbt = RobotClient()
rbt.home()
"""
        result = _run_simulation_isolated(program)

        assert "final_joints_rad" in result
        if result["final_joints_rad"] is not None:
            assert len(result["final_joints_rad"]) == 6


class TestPathVisualizerIntegration:
    """Integration tests for PathVisualizer with dry run client.

    These tests run in a subprocess via NiceGUI's cpu_bound(), so mocking
    PAROL6_ROBOT doesn't work (mocks don't transfer across process boundaries).
    The tests use the real robot kinematics module which should be available.
    """

    @pytest.fixture(autouse=True)
    def setup_test_tab(self):
        """Create a test tab so path visualizer can store results.

        State reset is handled by conftest.reset_state fixture.
        This fixture only sets up the test tab needed for these tests.
        """
        from parol_commander.state import editor_tabs_state, EditorTab

        # Clear change listeners to prevent UI rendering attempts without context
        simulation_state._change_listeners.clear()

        # Create a test tab so path visualizer can store results
        test_tab = EditorTab(
            id="test-tab",
            filename="test.py",
            file_path=None,
            content="",
            saved_content="",
        )
        editor_tabs_state.tabs = [test_tab]
        editor_tabs_state.active_tab_id = "test-tab"

        yield

        simulation_state._change_listeners.clear()

    @pytest.mark.asyncio
    async def test_visualizer_executes_simple_program(self):
        """PathVisualizer should execute program and create path segments.

        Uses real PAROL6_ROBOT module in subprocess - no mocking needed.
        """
        visualizer = PathVisualizer()

        # Simple program - uses the DryRunRobotClient shim injected by PathVisualizer
        program = """
import parol6

async def main():
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_joints([0, 0, 0, 0, 0, 0])
"""

        await visualizer.update_path_visualization(program)

        # Should have created at least one segment
        assert len(simulation_state.path_segments) >= 1, (
            f"Expected at least 1 segment, got {len(simulation_state.path_segments)}"
        )

    @pytest.mark.asyncio
    async def test_visualizer_updates_total_steps(self):
        """PathVisualizer should update total_steps after simulation."""
        visualizer = PathVisualizer()

        program = """
import parol6

async def main():
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_joints([10, 0, 0, 0, 0, 0])
        await rbt.move_joints([20, 0, 0, 0, 0, 0])
"""

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
        program = """
import parol6

async def main():
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_cartesian([150, 100, 250, 0, 0, 0])
"""

        await visualizer.update_path_visualization(program)

        # Should have created a segment
        assert len(simulation_state.path_segments) >= 1, (
            f"Expected at least 1 segment, got {len(simulation_state.path_segments)}"
        )

        # Check that end point is in meters (not mm)
        segment = simulation_state.path_segments[-1]
        end_point = segment.points[1]  # [x, y, z]

        # Values should be < 1 (meters), not > 100 (mm)
        assert abs(end_point[0]) < 1.0, (
            f"X coordinate {end_point[0]} appears to be in mm, expected meters"
        )
        assert abs(end_point[1]) < 1.0, (
            f"Y coordinate {end_point[1]} appears to be in mm, expected meters"
        )
        assert abs(end_point[2]) < 1.0, (
            f"Z coordinate {end_point[2]} appears to be in mm, expected meters"
        )

        # Check expected converted values (0.15, 0.1, 0.25)
        assert abs(end_point[0] - 0.15) < 0.01, (
            f"Expected X ~0.15m, got {end_point[0]}m"
        )
        assert abs(end_point[1] - 0.1) < 0.01, f"Expected Y ~0.1m, got {end_point[1]}m"
        assert abs(end_point[2] - 0.25) < 0.01, (
            f"Expected Z ~0.25m, got {end_point[2]}m"
        )

    @pytest.mark.asyncio
    async def test_target_markers_create_targets(self):
        """Programs with TARGET markers should create ProgramTarget objects."""
        visualizer = PathVisualizer()

        # Program with TARGET markers in comments
        # These should cause ProgramTarget objects to be created
        program = """
import parol6

async def main():
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_cartesian([100, 200, 300, 0, 0, 0])  # TARGET:abc12345
        await rbt.move_cartesian([150, 250, 350, 0, 0, 0])  # TARGET:def67890
"""

        await visualizer.update_path_visualization(program)

        # Should have created 2 path segments
        assert len(simulation_state.path_segments) >= 2, (
            f"Expected at least 2 segments, got {len(simulation_state.path_segments)}"
        )

        # Should have created 2 targets (one for each TARGET marker)
        assert len(simulation_state.targets) == 2, (
            f"Expected 2 targets (one per TARGET marker), got {len(simulation_state.targets)}. "
            f"Bug: compile() may not be using 'simulation_script.py' filename for frame inspection."
        )

        # Verify target IDs match the markers in the code
        target_ids = [t.id for t in simulation_state.targets]
        assert "abc12345" in target_ids, (
            f"Expected target 'abc12345' not found in {target_ids}"
        )
        assert "def67890" in target_ids, (
            f"Expected target 'def67890' not found in {target_ids}"
        )

    @pytest.mark.asyncio
    async def test_move_with_literals_auto_generates_targets(self):
        """Moves with literal values auto-generate targets for 3D editing.

        Even without explicit TARGET:uuid markers, moves with literal coordinates
        get auto-generated targets so users can edit positions in the 3D scene.
        """
        visualizer = PathVisualizer()

        # Program WITHOUT explicit TARGET markers, but with literal coordinates
        program = """
import parol6

async def main():
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_cartesian([100, 200, 300, 0, 0, 0])
        await rbt.move_joints([0, 0, 0, 0, 0, 0])
"""

        await visualizer.update_path_visualization(program)

        # Should have created path segments (for visualization)
        assert len(simulation_state.path_segments) >= 2, (
            f"Expected at least 2 segments, got {len(simulation_state.path_segments)}"
        )

        # Should have auto-generated targets for moves with literal values
        assert len(simulation_state.targets) >= 2, (
            f"Expected at least 2 auto-generated targets, got {len(simulation_state.targets)}"
        )

        # Auto-generated target IDs should be based on line numbers
        target_ids = [t.id for t in simulation_state.targets]
        assert any(tid.startswith("auto_") for tid in target_ids), (
            f"Expected auto-generated target IDs, got {target_ids}"
        )

    @pytest.mark.asyncio
    async def test_move_with_variables_no_target_created(self):
        """Moves with variable arguments should visualize but NOT create targets.

        When move commands use variables instead of literal values, the path
        should still be visualized, but no ProgramTarget is created since
        the coordinates aren't statically determinable.
        """
        visualizer = PathVisualizer()

        # Program with moves using variables (not literals)
        program = """
import parol6

async def main():
    position = [100, 200, 300, 0, 0, 0]
    joints = [0, 0, 0, 0, 0, 0]
    async with parol6.AsyncRobotClient() as rbt:
        await rbt.move_cartesian(position)
        await rbt.move_joints(joints)
"""

        await visualizer.update_path_visualization(program)

        # Should have created path segments (visualization still works)
        assert len(simulation_state.path_segments) >= 2, (
            f"Expected at least 2 segments, got {len(simulation_state.path_segments)}"
        )

        # Should NOT have created any targets (variables not inspectable)
        assert len(simulation_state.targets) == 0, (
            f"Expected 0 targets (moves use variables), got {len(simulation_state.targets)}"
        )
