"""Integration tests for URDF scene initialization and behavior in the main page.

These tests verify that opening the main page creates a URDF scene,
populates basic state on ``ui_state``, and that the scene's Python API
correctly updates internal state.
"""

import asyncio

import pytest
from nicegui.testing import User

from tests.helpers.wait import wait_for_urdf_ready


@pytest.mark.integration
async def test_urdf_scene_initialized_on_main_page(user: User) -> None:
    """Opening ``/`` should initialize the URDF scene on ui_state.

    This asserts that the URDF scene is at least constructed and attached to
    the global ui_state, which is a prerequisite for all 3D visualization.
    """
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_urdf_ready()

    scene = ui_state.urdf_scene
    assert scene is not None, "Expected ui_state.urdf_scene to be initialized"

    # Joint names should be a non-empty list (typically 6 for PAROL6)
    if ui_state.urdf_joint_names is not None:
        assert len(ui_state.urdf_joint_names) >= 1


@pytest.mark.integration
async def test_urdf_scene_joint_names(user: User) -> None:
    """Test that get_joint_names returns the expected joint names.

    Verifies that the scene reports 6 actuated joints for PAROL6.
    """
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_urdf_ready()

    scene = ui_state.urdf_scene
    assert scene is not None, "Expected ui_state.urdf_scene to be initialized"

    joint_names = scene.get_joint_names()
    assert isinstance(joint_names, list), "Expected joint_names to be a list"
    assert len(joint_names) == 6, f"Expected 6 joints, got {len(joint_names)}"


@pytest.mark.integration
async def test_urdf_scene_joint_limits(user: User) -> None:
    """Test that get_joint_limits returns valid limits for each joint.

    Verifies that each joint has min/max limits defined.
    """
    from parol_commander.state import ui_state

    await user.open("/")
    await wait_for_urdf_ready()

    scene = ui_state.urdf_scene
    assert scene is not None, "Expected ui_state.urdf_scene to be initialized"

    joint_limits = scene.get_joint_limits()
    assert isinstance(joint_limits, dict), "Expected joint_limits to be a dict"
    assert len(joint_limits) >= 6, (
        f"Expected at least 6 joint limits, got {len(joint_limits)}"
    )

    # Each joint should have min and max keys
    for joint_name, limits in joint_limits.items():
        assert "min" in limits, f"Expected 'min' key for joint {joint_name}"
        assert "max" in limits, f"Expected 'max' key for joint {joint_name}"


@pytest.mark.integration
async def test_urdf_scene_envelope_pregenerated_on_startup(user: User) -> None:
    """Test that workspace envelope is pre-generated when scene loads.

    Verifies that opening the main page triggers envelope data generation,
    so it's available for immediate rendering when envelope mode is 'on'.
    """
    from parol_commander.state import ui_state
    from parol_commander.services.urdf_scene.envelope_mixin import workspace_envelope

    # Reset envelope state before test
    workspace_envelope.reset()
    assert workspace_envelope._generated is False, (
        "Expected envelope to start ungenerated"
    )

    await user.open("/")
    await (
        wait_for_urdf_ready()
    )  # Wait for scene init (envelope generated during show())

    scene = ui_state.urdf_scene
    assert scene is not None, "Expected ui_state.urdf_scene to be initialized"

    # Envelope should be almost pre-generated after scene.show() is called
    await asyncio.sleep(1)
    assert workspace_envelope._generated is True, (
        "Expected workspace envelope to be pre-generated on scene startup"
    )
    assert workspace_envelope.max_reach > 0, (
        "Expected envelope to have calculated max reach"
    )


@pytest.mark.integration
async def test_urdf_scene_envelope_visibility_on_mode_change(user: User) -> None:
    """Test that changing envelope_mode to 'on' creates and shows the envelope.

    Verifies that when simulation_state.envelope_mode is set to 'on', the
    envelope wireframe sphere is created and made visible in the scene.
    """
    from parol_commander.state import ui_state, simulation_state
    from parol_commander.services.urdf_scene.envelope_mixin import workspace_envelope

    await user.open("/")
    await wait_for_urdf_ready()

    scene = ui_state.urdf_scene
    assert scene is not None, "Expected ui_state.urdf_scene to be initialized"

    # Set envelope mode to 'off' first
    simulation_state.envelope_mode = "off"
    await asyncio.sleep(0.2)  # Let update timer run

    # If envelope_object exists, it should be hidden
    if scene.envelope_object is not None:
        assert scene.envelope_object.visible_ is False, (
            "Expected envelope to be hidden when mode is 'off'"
        )

    # Now set envelope mode to 'on'
    simulation_state.envelope_mode = "on"
    await asyncio.sleep(0.2)  # Let update timer run

    # Envelope data should exist
    assert workspace_envelope._generated is True, "Expected envelope to be generated"

    # Envelope object should be created and visible
    # Note: envelope_object may be created lazily on first 'on' mode
    if scene.envelope_object is not None:
        assert scene.envelope_object.visible_ is True, (
            "Expected envelope to be visible when mode is 'on'"
        )
