"""Tests for TCP TransformControls jogging functionality."""

import math
from unittest.mock import MagicMock, patch

from parol_commander.services.urdf_scene import UrdfScene, UrdfSceneConfig


class TestTcpTransformControls:
    """Tests for TCP TransformControls state and delta processing."""

    def test_tcp_jog_callback_registration(self, tmp_path):
        """Test that on_tcp_jog_event registers callback correctly."""
        # Create minimal URDF
        urdf_content = """<?xml version="1.0"?>
        <robot name="test">
            <link name="base_link"/>
        </robot>
        """
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)
        meshes_dir = tmp_path / "meshes"
        meshes_dir.mkdir()

        config = UrdfSceneConfig(meshes_dir=meshes_dir, mount_static=False)
        scene = UrdfScene(urdf_file, config)

        # Initially no callback
        assert scene._tcp_cartesian_move_callback is None

        # Register callback
        mock_callback = MagicMock()
        scene.on_tcp_cartesian_move(mock_callback)

        assert scene._tcp_cartesian_move_callback is mock_callback

    def test_handle_tcp_transform_translate_delta(self, tmp_path):
        """Test that translation position is captured and callback is invoked."""
        # Create minimal URDF
        urdf_content = """<?xml version="1.0"?>
        <robot name="test">
            <link name="base_link"/>
        </robot>
        """
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)
        meshes_dir = tmp_path / "meshes"
        meshes_dir.mkdir()

        config = UrdfSceneConfig(meshes_dir=meshes_dir, mount_static=False)
        scene = UrdfScene(urdf_file, config)

        # Set up state
        scene._tcp_transform_enabled = True
        scene._tcp_transform_mode = "translate"
        scene._control_frame = "WRF"

        # Register Cartesian move callback (new API - receives pose list)
        move_calls = []

        def mock_cartesian_callback(pose):
            move_calls.append(pose)

        scene.on_tcp_cartesian_move(mock_cartesian_callback)

        # Mock robot_state for orientation values
        with patch(
            "parol_commander.services.urdf_scene.tcp_controls_mixin.robot_state"
        ) as mock_robot_state:
            mock_robot_state.rx = 10.0
            mock_robot_state.ry = 20.0
            mock_robot_state.rz = 30.0

            # Create mock event - first position (callback is called immediately - no baseline needed)
            event1 = MagicMock()
            event1.object_name = "tcp:jog_ball"
            event1.wx = 0.0
            event1.wy = 0.0
            event1.wz = 0.0
            event1.x = 0.0
            event1.y = 0.0
            event1.z = 0.0

            scene._handle_tcp_transform_for_jog(event1)
            # First event calls callback with position (0, 0, 0)
            assert len(move_calls) == 1

            # Create second event with X+ movement (5mm = 0.005m - above 1mm threshold)
            event2 = MagicMock()
            event2.object_name = "tcp:jog_ball"
            event2.wx = 0.005
            event2.wy = 0.001
            event2.wz = 0.002
            event2.x = 0.005
            event2.y = 0.001
            event2.z = 0.002

            scene._handle_tcp_transform_for_jog(event2)

            # Should have two move calls now (first position + second position)
            assert len(move_calls) == 2
            pose = move_calls[1]  # Check the second call
            # Position: 0.005m * 1000 = 5mm, 0.001m * 1000 = 1mm, 0.002m * 1000 = 2mm
            assert abs(pose[0] - 5.0) < 0.1  # x in mm
            assert abs(pose[1] - 1.0) < 0.1  # y in mm
            assert abs(pose[2] - 2.0) < 0.1  # z in mm
            # Orientation kept from robot_state
            assert pose[3] == 10.0  # rx
            assert pose[4] == 20.0  # ry
            assert pose[5] == 30.0  # rz

    def test_handle_tcp_transform_rotate_delta(self, tmp_path):
        """Test that rotation values are captured and callback is invoked."""
        # Create minimal URDF
        urdf_content = """<?xml version="1.0"?>
        <robot name="test">
            <link name="base_link"/>
        </robot>
        """
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)
        meshes_dir = tmp_path / "meshes"
        meshes_dir.mkdir()

        config = UrdfSceneConfig(meshes_dir=meshes_dir, mount_static=False)
        scene = UrdfScene(urdf_file, config)

        # Set up state for rotation mode
        scene._tcp_transform_enabled = True
        scene._tcp_transform_mode = "rotate"
        scene._control_frame = "TRF"

        # Register Cartesian move callback (new API - receives pose list)
        move_calls = []

        def mock_cartesian_callback(pose):
            move_calls.append(pose)

        scene.on_tcp_cartesian_move(mock_cartesian_callback)

        # Mock robot_state for position values
        with patch(
            "parol_commander.services.urdf_scene.tcp_controls_mixin.robot_state"
        ) as mock_robot_state:
            mock_robot_state.x = 100.0  # mm
            mock_robot_state.y = 200.0
            mock_robot_state.z = 300.0

            # Create mock event - first rotation (callback is called immediately - no baseline needed)
            event1 = MagicMock()
            event1.object_name = "tcp:jog_ball"
            event1.rx = 0.0
            event1.ry = 0.0
            event1.rz = 0.0

            scene._handle_tcp_transform_for_jog(event1)
            # First event calls callback with rotation (0, 0, 0)
            assert len(move_calls) == 1

            # Create second event with rotation (10, 20, 30 degrees - above 1 degree threshold)
            event2 = MagicMock()
            event2.object_name = "tcp:jog_ball"
            event2.rx = math.radians(10.0)
            event2.ry = math.radians(20.0)
            event2.rz = math.radians(30.0)

            scene._handle_tcp_transform_for_jog(event2)

            # Should have two move calls now (first rotation + second rotation)
            assert len(move_calls) == 2
            pose = move_calls[1]  # Check the second call
            # Position kept from robot_state
            assert pose[0] == 100.0  # x in mm
            assert pose[1] == 200.0  # y in mm
            assert pose[2] == 300.0  # z in mm
            # Rotation from transform event (converted from radians to degrees)
            assert abs(pose[3] - 10.0) < 0.1  # rx in degrees
            assert abs(pose[4] - 20.0) < 0.1  # ry in degrees
            assert abs(pose[5] - 30.0) < 0.1  # rz in degrees

    def test_handle_tcp_transform_ignores_wrong_object(self, tmp_path):
        """Test that transforms on non-TCP objects are ignored."""
        # Create minimal URDF
        urdf_content = """<?xml version="1.0"?>
        <robot name="test">
            <link name="base_link"/>
        </robot>
        """
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)
        meshes_dir = tmp_path / "meshes"
        meshes_dir.mkdir()

        config = UrdfSceneConfig(meshes_dir=meshes_dir, mount_static=False)
        scene = UrdfScene(urdf_file, config)

        # Set up state
        scene._tcp_transform_enabled = True
        scene._tcp_transform_mode = "translate"

        # Register callback
        jog_calls = []

        def mock_callback(frame, axis, magnitude):
            jog_calls.append((frame, axis, magnitude))

        scene.on_tcp_cartesian_move(mock_callback)

        # Create mock event for wrong object
        event = MagicMock()
        event.object_name = "target:some_target"
        event.wx = 0.1
        event.wy = 0.1
        event.wz = 0.1

        scene._handle_tcp_transform_for_jog(event)

        # Should not trigger any jog calls
        assert len(jog_calls) == 0

    def test_handle_tcp_transform_disabled_ignores(self, tmp_path):
        """Test that transforms are ignored when TCP transform is disabled."""
        # Create minimal URDF
        urdf_content = """<?xml version="1.0"?>
        <robot name="test">
            <link name="base_link"/>
        </robot>
        """
        urdf_file = tmp_path / "test.urdf"
        urdf_file.write_text(urdf_content)
        meshes_dir = tmp_path / "meshes"
        meshes_dir.mkdir()

        config = UrdfSceneConfig(meshes_dir=meshes_dir, mount_static=False)
        scene = UrdfScene(urdf_file, config)

        # TCP transform disabled by default
        assert scene._tcp_transform_enabled is False

        # Register callback
        jog_calls = []

        def mock_callback(frame, axis, magnitude):
            jog_calls.append((frame, axis, magnitude))

        scene.on_tcp_cartesian_move(mock_callback)

        # Create mock event
        event = MagicMock()
        event.object_name = "tcp:offset"
        event.wx = 0.1
        event.wy = 0.1
        event.wz = 0.1

        scene._handle_tcp_transform_for_jog(event)

        # Should not trigger any jog calls
        assert len(jog_calls) == 0
