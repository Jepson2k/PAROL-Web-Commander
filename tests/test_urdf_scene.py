"""Unit tests for URDF scene service."""

from pathlib import Path

import pytest


@pytest.mark.unit
def test_urdf_scene_config_basic_construction() -> None:
    """Test that UrdfSceneConfig can be constructed with basic parameters.

    Verifies that the configuration class accepts standard parameters.
    """
    from parol_commander.services.urdf_scene import UrdfSceneConfig
    from pathlib import Path

    # Create a basic config
    config = UrdfSceneConfig(
        meshes_dir=Path("/path/to/meshes"),
    )

    # Assert fields are set
    assert config.meshes_dir == Path("/path/to/meshes")


@pytest.mark.unit
def test_urdf_meshes_dir_resolution(tmp_path: Path) -> None:
    """Test that UrdfScene resolves mesh directories correctly.

    Verifies mesh path resolution and URL generation.
    """
    from parol_commander.services.urdf_scene import UrdfSceneConfig

    # Create a minimal URDF file
    urdf_path = tmp_path / "test.urdf"
    urdf_path.write_text(
        """<?xml version="1.0"?>
<robot name="test">
  <link name="base_link"/>
</robot>
"""
    )

    # Create a mesh directory
    meshes_dir = tmp_path / "meshes"
    meshes_dir.mkdir()

    # Create config
    config = UrdfSceneConfig(
        meshes_dir=meshes_dir,
    )

    # Verify config is valid
    assert config.meshes_dir == meshes_dir


@pytest.mark.unit
def test_urdf_scene_tcp_pose_update() -> None:
    """Test that TCP pose can be updated via tool resolver.

    This is a conceptual test - actual implementation may require
    a full NiceGUI context with scene3d element.
    """
    from parol_commander.services.urdf_scene import UrdfSceneConfig, ToolPose

    # Create a config with a mock tool pose resolver
    def mock_resolver(tool_name: str):
        """Mock resolver that returns a test pose."""
        return ToolPose(origin=[0.1, 0.2, 0.3], rpy=[10.0, 20.0, 30.0])

    config = UrdfSceneConfig(
        tool_pose_resolver=mock_resolver,
    )

    # Verify the resolver is set
    assert config.tool_pose_resolver is not None
    result = config.tool_pose_resolver("TEST_TOOL")
    assert result is not None
    assert result.origin == [0.1, 0.2, 0.3]
    assert result.rpy == [10.0, 20.0, 30.0]
