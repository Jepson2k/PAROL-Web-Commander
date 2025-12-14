"""Browser tests for TCP transform controls visibility.

Tests verify that the TCP ball (gizmo handle sphere) and TransformControls
are properly created and visible in the Three.js scene after page load.
"""

from typing import TYPE_CHECKING

import pytest

# Browser tests need longer timeout (full app startup + browser operations)
pytestmark = pytest.mark.timeout(60)

if TYPE_CHECKING:
    from nicegui.testing.screen import Screen


def wait_for_scene_ready(screen: "Screen", timeout_s: float = 10.0) -> None:
    """Wait for NiceGUI scene to be fully initialized.

    Args:
        screen: NiceGUI Screen test fixture
        timeout_s: Maximum time to wait
    """
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        # Find scene canvas and check if initialized
        is_ready = screen.selenium.execute_script(
            """
            const canvas = document.querySelector('canvas');
            if (!canvas) return false;
            const sceneEl = canvas.closest('[data-initializing]');
            // Once initialized, data-initializing is removed
            return sceneEl === null && canvas.parentElement;
        """
        )
        if is_ready:
            return
        time.sleep(0.1)
    raise AssertionError(f"Scene not ready after {timeout_s}s")


def get_scene_object_by_name(screen: "Screen", name: str) -> dict | None:
    """Find a Three.js object by name in the scene.

    Args:
        screen: NiceGUI Screen test fixture
        name: Name of the object to find (e.g., "tcp:ball")

    Returns:
        Dict with object info, or dict with 'error' key if not found
    """
    result = screen.selenium.execute_script(
        """
        const name = arguments[0];
        // Find the scene element and get the exposed Three.js scene
        const sceneDiv = document.querySelector('.nicegui-scene');
        if (!sceneDiv) return {error: 'Scene div not found'};

        const sceneId = sceneDiv.id;
        const scene = window['scene_' + sceneId];
        if (!scene) return {error: 'Three.js scene not found for id: ' + sceneId};

        // Traverse scene to find object by name
        let found = null;
        scene.traverse(function(obj) {
            if (obj.name === name) {
                found = {
                    name: obj.name,
                    type: obj.type,
                    visible: obj.visible,
                    position: obj.position ? {
                        x: obj.position.x,
                        y: obj.position.y,
                        z: obj.position.z
                    } : null
                };
            }
        });
        return found;
    """,
        name,
    )
    return result


def list_scene_objects(screen: "Screen") -> list[dict]:
    """List all named objects in the Three.js scene for debugging.

    Args:
        screen: NiceGUI Screen test fixture

    Returns:
        List of dicts with object name, type, and visibility
    """
    result = screen.selenium.execute_script(
        """
        const sceneDiv = document.querySelector('.nicegui-scene');
        if (!sceneDiv) return [{error: 'Scene div not found'}];

        const sceneId = sceneDiv.id;
        const scene = window['scene_' + sceneId];
        if (!scene) return [{error: 'Three.js scene not found'}];

        const objects = [];
        scene.traverse(function(obj) {
            if (obj.name) {
                objects.push({
                    name: obj.name,
                    type: obj.type,
                    visible: obj.visible
                });
            }
        });
        return objects;
    """
    )
    return result or []


@pytest.mark.browser
def test_tcp_ball_exists_in_scene(screen: "Screen") -> None:
    """Test that TCP ball sphere exists in the Three.js scene after page load."""
    screen.open("/", timeout=15.0)
    wait_for_scene_ready(screen, timeout_s=10.0)

    # Give extra time for TCP ball to be created and scene to stabilize
    import time

    time.sleep(3.0)

    # List all scene objects for debugging
    objects = list_scene_objects(screen)
    print(f"[DEBUG] Scene objects: {objects}")

    # Look for the TCP ball object
    tcp_ball = get_scene_object_by_name(screen, "tcp:ball")

    assert (
        tcp_ball is not None
    ), f"TCP ball should exist in scene. Objects found: {objects}"
    assert "error" not in tcp_ball, f"Error finding TCP ball: {tcp_ball.get('error')}"
    assert (
        tcp_ball.get("type") == "Mesh"
    ), f"TCP ball should be a Mesh, got: {tcp_ball.get('type')}"
    assert tcp_ball.get("visible") is True, "TCP ball should be visible"


@pytest.mark.browser
def test_tcp_ball_position_not_at_origin(screen: "Screen") -> None:
    """Test that TCP ball is not stuck at origin (0,0,0).

    If the ball is at origin, it's likely hidden inside the robot base.
    """
    screen.open("/", timeout=15.0)
    wait_for_scene_ready(screen, timeout_s=10.0)

    import time

    time.sleep(3.0)

    tcp_ball = get_scene_object_by_name(screen, "tcp:ball")

    assert tcp_ball is not None, "TCP ball should exist"
    assert "error" not in tcp_ball, f"Error: {tcp_ball.get('error')}"

    pos = tcp_ball.get("position")
    assert pos is not None, "TCP ball should have a position"

    # Ball should NOT be exactly at origin (would be hidden inside robot base)
    is_at_origin = (
        abs(pos["x"]) < 0.001 and abs(pos["y"]) < 0.001 and abs(pos["z"]) < 0.001
    )
    assert not is_at_origin, f"TCP ball should not be at origin, position: {pos}"


@pytest.mark.browser
def test_tcp_transform_controls_attached(screen: "Screen") -> None:
    """Test that TransformControls gizmo is attached to TCP ball."""
    screen.open("/", timeout=15.0)
    wait_for_scene_ready(screen, timeout_s=10.0)

    import time

    time.sleep(3.0)

    # Check if TransformControls exist in the scene
    has_gizmo = screen.selenium.execute_script(
        """
        const sceneDiv = document.querySelector('.nicegui-scene');
        if (!sceneDiv) return {error: 'Scene div not found'};

        const sceneId = sceneDiv.id;
        const scene = window['scene_' + sceneId];
        if (!scene) return {error: 'Three.js scene not found'};

        // Look for TransformControls helper objects (they have specific names/types)
        let hasTransformControls = false;
        let gizmoTypes = [];
        scene.traverse(function(obj) {
            // TransformControls adds helper objects with specific patterns
            if (obj.type === 'TransformControlsGizmo' ||
                obj.type === 'TransformControlsPlane' ||
                (obj.name && obj.name.includes('transform'))) {
                hasTransformControls = true;
                gizmoTypes.push(obj.type);
            }
        });
        return {hasTransformControls: hasTransformControls, gizmoTypes: gizmoTypes};
    """
    )

    assert has_gizmo is not None
    assert "error" not in has_gizmo, f"Error: {has_gizmo.get('error')}"
    assert (
        has_gizmo.get("hasTransformControls") is True
    ), f"TransformControls gizmo should exist, found types: {has_gizmo.get('gizmoTypes')}"
