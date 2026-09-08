"""Declared held geometry through programs, preview, and the shape controls."""

import asyncio

import numpy as np
import pytest
import waldoctl
from nicegui import run
from nicegui.testing import User
from parol6.client.dry_run_client import DryRunRobotClient
from waldoctl import Sphere

from tests.helpers.wait import (
    enable_sim,
    ensure_robot_ready_for_motion,
    wait_for_app_ready,
    wait_for_urdf_ready,
)
from waldo_commander.services.path_visualizer import _run_simulation_isolated
from waldo_commander.skills import attach_object, detach_object


@pytest.mark.integration
async def test_preview_seeds_held_world_and_confirms_explicit_detach(user: User):
    await user.open("/")
    await wait_for_app_ready()
    await enable_sim(user)
    await ensure_robot_ready_for_motion()
    client = waldoctl.commander.client
    world = await client.shapes()
    joints = await client.angles()
    assert world is not None and joints is not None
    part = Sphere(name="part", radius=0.01).attach(
        flange_pose=(0, 0, 0.25, 0, 0, 0), epoch=world.attachment_epoch
    )
    source = (
        "from parol6 import RobotClient\n"
        "from waldo_commander.skills import detach_object, attach_object\n"
        "with RobotClient() as rbt:\n"
        "    world = rbt.shapes()\n"
        "    assert world.program and world.attachments_valid, 'missing preview attachment context'\n"
        "    assert world.program[0].name == 'part'\n"
        "    detach_object(rbt, name='part', world_pose=(1, 1, 1, 0, 0, 0))\n"
        "    assert rbt.shapes().program[0].attachment is None\n"
        "    attach_object(rbt, name='part', flange_pose=(0, 0, .25, 0, 0, 0))\n"
        "    assert rbt.shapes().attachments_valid\n"
    )
    preview = await run.cpu_bound(
        _run_simulation_isolated,
        source,
        np.radians(joints),
        dry_run_client_cls=DryRunRobotClient,
        shapes_wire=[part.to_wire()],
        initial_tool=("NONE", ""),
        attachment_epoch=world.attachment_epoch,
    )
    assert preview["error"] is None, preview["error"]
    assert (await client.shapes()).program == world.program
    stale = await run.cpu_bound(
        _run_simulation_isolated,
        source,
        np.radians(joints),
        dry_run_client_cls=DryRunRobotClient,
        shapes_wire=[part.to_wire()],
        attachment_epoch=world.attachment_epoch + 1,
    )
    assert "reconcile the scene" in stale["error"]


@pytest.mark.integration
async def test_attachment_controls_confirm_model_and_require_reconciliation(
    user: User, caplog
):
    from waldo_commander.state import ui_state

    await user.open("/")
    await wait_for_app_ready()
    await wait_for_urdf_ready()
    await enable_sim(user)
    await ensure_robot_ready_for_motion()
    client = waldoctl.commander.client
    handle = waldoctl.commander.scene
    scene = ui_state.urdf_scene
    assert handle is not None and scene is not None
    marker = Sphere(name="fixture", radius=0.01, pose=(1, 1, 1, 0, 0, 0))
    part = Sphere(name="part", radius=0.01, pose=(1, 1, 1.2, 0, 0, 0))

    def element(marker):
        return next(iter(user.find(marker=marker).elements))

    try:
        assert await client.set_shapes([marker, part]) == 1
        await handle.refresh_from_backend()
        with scene.scene:
            scene._show_attachment_dialog("part")
        for axis, value in zip(("x", "y", "z"), (0, 0, 250), strict=True):
            element(f"attachment-pos-{axis}").set_value(value)
        element("attachment-contacts").set_value("shape:typo")
        user.find(marker="attachment-apply").click()
        await user.should_see("unknown contact")
        expected = [
            r
            for r in caplog.records
            if r.name == "parol6.commands.base"
            and "unknown contact partners: ['shape:typo']" in r.getMessage()
        ]
        assert len(expected) == 1
        caplog.records.remove(expected[0])
        assert (await client.shapes()).program[-1].attachment is None
        element("attachment-contacts").set_value("shape:fixture")
        user.find(marker="attachment-apply").click()
        await user.should_see("Attachment confirmed: part")
        applied = await client.shapes()
        assert applied is not None and applied.program[0] == marker
        assert applied.attachments_valid and applied.program[-1].attachment is not None
        assert handle.attachments_valid
        assert scene._shape_objects["shape:part"].parent is scene.last_actuated_group

        assert await client.estop() == 1
        async with asyncio.timeout(5):
            while (await client.shapes()).attachments_valid:
                await asyncio.sleep(0)
        async with asyncio.timeout(5):
            while handle.attachments_valid:
                await handle.refresh_from_backend()
                await asyncio.sleep(0)
        assert await client.reset() == 1
        await attach_object.async_call(
            client,
            name="part",
            flange_pose=(0, 0, 0.25, 0, 0, 0),
            allowed_contacts=("shape:fixture",),
        )
        async with asyncio.timeout(5):
            while not handle.attachments_valid:
                await handle.refresh_from_backend()
                await asyncio.sleep(0)
        with scene.scene:
            scene._show_attachment_dialog("part", detach=True)
        for axis in ("x", "y", "z"):
            element(f"attachment-pos-{axis}").set_value(1000)
        user.find(marker="attachment-apply").click()
        await user.should_see("Detached: part", retries=50)
        world = await client.shapes()
        assert world is not None and world.program[0] == marker
        assert world.program[-1].attachment is None
        with pytest.raises(ValueError, match="not attached"):
            await detach_object.async_call(
                client, name="part", world_pose=(1, 1, 1, 0, 0, 0)
            )
    finally:
        await client.stop()
        await client.set_shapes([])
