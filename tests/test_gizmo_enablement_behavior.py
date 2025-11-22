import asyncio
import pytest
from nicegui.testing import User


@pytest.mark.integration
async def test_gizmo_disabled_blocks_press_and_enabled_allows_press(
    user: User, monkeypatch
):
    # Open main page and let URDF initialize
    await user.open("/")
    await asyncio.sleep(0.4)

    from parol_commander.state import ui_state

    # Ensure scene initialized
    assert ui_state.urdf_scene is not None

    # Patch UrdfScene._dispatch_gizmo_event to capture dispatched events
    events: list[object] = []

    def _record_dispatch(event):  # type: ignore[no-redef]
        events.append(event)

    monkeypatch.setattr(
        ui_state.urdf_scene, "_dispatch_gizmo_event", _record_dispatch, raising=True
    )  # type: ignore[attr-defined]

    # Helper to synthesize a NiceGUI scene click event targeting a gizmo handle
    class _Hit:
        def __init__(self, name: str):
            self.object_name = name

    class _Event:
        def __init__(self, click_type: str, hits):
            self.click_type = click_type
            self.hits = hits

    # Disable X+ handle and verify no event is dispatched on press
    ui_state.urdf_scene.set_control_handle_enabled("X+", False)  # type: ignore[attr-defined]
    evt_press = _Event("mousedown", [_Hit("gizmo:X+")])
    ui_state.urdf_scene._handle_scene_click(evt_press)  # type: ignore[attr-defined]
    await asyncio.sleep(0.05)
    assert events == [], "Disabled gizmo handle should not dispatch press events"

    # Re-enable X+ and verify an event is dispatched on press
    ui_state.urdf_scene.set_control_handle_enabled("X+", True)  # type: ignore[attr-defined]
    ui_state.urdf_scene._handle_scene_click(evt_press)  # type: ignore[attr-defined]
    await asyncio.sleep(0.05)
    assert (
        events and getattr(events[-1], "handle", "") == "X+"
    ), "Enabled gizmo handle press should dispatch event"
