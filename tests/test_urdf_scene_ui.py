"""Integration tests for URDF scene initialization in the main page.

These tests verify that opening the main page creates a URDF scene and
populates basic state on ``ui_state``.
"""
import asyncio

import pytest
from nicegui.testing import User


@pytest.mark.integration
async def test_urdf_scene_initialized_on_main_page(user: User) -> None:
    """Opening ``/`` should initialize the URDF scene on ui_state.

    This asserts that the URDF scene is at least constructed and attached to
    the global ui_state, which is a prerequisite for all 3D visualization.
    """
    from parol_commander.state import ui_state

    await user.open("/")
    # Give the deferred URDF init timer a bit of time to run
    await asyncio.sleep(0.3)

    scene = ui_state.urdf_scene
    assert scene is not None, "Expected ui_state.urdf_scene to be initialized"

    # Joint names should be a non-empty list (typically 6 for PAROL6)
    if ui_state.urdf_joint_names is not None:
        assert len(ui_state.urdf_joint_names) >= 1
