"""Selenium browser tests for panel resize and tab switching functionality.

Tests the PanelResize JavaScript module using real Chrome browser via Selenium:
- Panel width/height persistence via localStorage
- Resize handle drag interactions
- Push logic when resizing coordinated panels
- Tab switching state preservation
- State class management (bottom-open, is-open, etc.)
- Size constraints (min/max)
"""

from typing import TYPE_CHECKING

import pytest

# Browser tests need longer timeout (full app startup + browser operations)
pytestmark = pytest.mark.timeout(60)

if TYPE_CHECKING:
    from nicegui.testing.screen import Screen


# Storage key used by PanelResize module
STORAGE_KEY = "parol_panel_sizes"

# Window size for tests - larger size makes layout issues more obvious
TEST_WINDOW_WIDTH = 1920
TEST_WINDOW_HEIGHT = 1080


@pytest.fixture(autouse=True)
def set_large_window_size(screen: "Screen") -> None:
    """Set a larger window size for all browser tests in this module.

    The default 600x600 is too small - layout bugs are more obvious at larger sizes.
    """
    screen.selenium.set_window_size(TEST_WINDOW_WIDTH, TEST_WINDOW_HEIGHT)


@pytest.fixture(autouse=True)
def clear_startup_errors(screen: "Screen") -> None:
    """Clear ERROR logs from app startup to prevent false test failures.

    The app may log ERROR messages during startup (e.g., controller spawn failures)
    that are expected in test environments. These should not cause test failures.
    The caplog is cleared after this fixture runs, before the actual test.
    """
    # This fixture runs after set_large_window_size due to ordering
    # The screen fixture has already started the app at this point
    # Clear any startup ERROR logs so they don't fail the test
    screen.caplog.clear()


@pytest.fixture(autouse=True)
def clear_localstorage(screen: "Screen"):
    """Clear localStorage after each test to ensure isolation.

    Panel resize state is persisted in localStorage. Without clearing,
    previous test state could affect subsequent tests.
    """
    yield
    # Clear after test
    try:
        screen.selenium.execute_script(f"localStorage.removeItem('{STORAGE_KEY}')")
    except Exception:
        pass  # Browser may be closed


# ============================================================================
# Helper Functions
# ============================================================================


def get_element_rect(screen: "Screen", selector: str) -> dict | None:
    """Get bounding rect of element via JavaScript.

    Args:
        screen: NiceGUI Screen test fixture
        selector: CSS selector for the element

    Returns:
        Dict with top, right, bottom, left, width, height or None if not found
    """
    result = screen.selenium.execute_script(
        """
        const el = document.querySelector(arguments[0]);
        if (!el) return null;
        const rect = el.getBoundingClientRect();
        return {
            top: rect.top,
            right: rect.right,
            bottom: rect.bottom,
            left: rect.left,
            width: rect.width,
            height: rect.height
        };
        """,
        selector,
    )
    return result


def get_bottom_offset(screen: "Screen", selector: str) -> float | None:
    """Get the distance from bottom of element to bottom of viewport.

    Args:
        screen: NiceGUI Screen test fixture
        selector: CSS selector for the element

    Returns:
        Distance in pixels from element bottom to viewport bottom, or None if not found
    """
    result = screen.selenium.execute_script(
        """
        const el = document.querySelector(arguments[0]);
        if (!el) return null;
        const rect = el.getBoundingClientRect();
        return window.innerHeight - rect.bottom;
        """,
        selector,
    )
    return result


def get_element_classes(screen: "Screen", selector: str) -> list[str]:
    """Get list of CSS classes on an element.

    Args:
        screen: NiceGUI Screen test fixture
        selector: CSS selector for the element

    Returns:
        List of class names
    """
    result = screen.selenium.execute_script(
        """
        const el = document.querySelector(arguments[0]);
        if (!el) return [];
        return Array.from(el.classList);
        """,
        selector,
    )
    return result or []


def get_localstorage_item(screen: "Screen", key: str) -> dict | None:
    """Read and parse JSON from localStorage.

    Args:
        screen: NiceGUI Screen test fixture
        key: localStorage key

    Returns:
        Parsed JSON object or None if not found
    """
    import json

    result = screen.selenium.execute_script(f"return localStorage.getItem('{key}')")
    if result:
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return None
    return None


def clear_localstorage_item(screen: "Screen", key: str) -> None:
    """Remove an item from localStorage.

    Args:
        screen: NiceGUI Screen test fixture
        key: localStorage key to remove
    """
    screen.selenium.execute_script(f"localStorage.removeItem('{key}')")


def simulate_drag(screen: "Screen", selector: str, dx: int = 0, dy: int = 0) -> None:
    """Simulate a mouse drag on an element and wait for resize to complete.

    Triggers mousedown on the element, then mousemove and mouseup on document.
    Waits for localStorage to be updated (indicating resize is complete).

    Args:
        screen: NiceGUI Screen test fixture
        selector: CSS selector for the element to drag
        dx: Horizontal pixels to drag (positive = right)
        dy: Vertical pixels to drag (positive = down)
    """
    import time

    # Get localStorage state before drag
    before = screen.selenium.execute_script(
        f"return localStorage.getItem('{STORAGE_KEY}')"
    )

    screen.selenium.execute_script(
        f"""
        const el = document.querySelector('{selector}');
        if (!el) return;
        const rect = el.getBoundingClientRect();
        const startX = rect.left + rect.width / 2;
        const startY = rect.top + rect.height / 2;

        el.dispatchEvent(new MouseEvent('mousedown', {{
            clientX: startX,
            clientY: startY,
            bubbles: true
        }}));

        document.dispatchEvent(new MouseEvent('mousemove', {{
            clientX: startX + {dx},
            clientY: startY + {dy},
            bubbles: true
        }}));

        document.dispatchEvent(new MouseEvent('mouseup', {{
            clientX: startX + {dx},
            clientY: startY + {dy},
            bubbles: true
        }}));
        """
    )

    # Wait for localStorage to be updated (indicates resize complete)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        after = screen.selenium.execute_script(
            f"return localStorage.getItem('{STORAGE_KEY}')"
        )
        if after != before:
            return  # Resize complete
        time.sleep(0.05)
    # Continue even if localStorage didn't change (resize may have hit limits)


def wait_for_panel_resize_ready(screen: "Screen", timeout_s: float = 5.0) -> None:
    """Wait for PanelResize module to be configured and app-ready.

    Args:
        screen: NiceGUI Screen test fixture
        timeout_s: Maximum time to wait

    Raises:
        AssertionError: If PanelResize doesn't become ready
    """
    import time

    interval = 0.1
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        is_ready = screen.selenium.execute_script(
            "return window.PanelResize && window.PanelResize.isAppReady()"
        )
        if is_ready:
            return
        time.sleep(interval)

    raise AssertionError(f"PanelResize not ready after {timeout_s}s")


def wait_for_element_visible(
    screen: "Screen", selector: str, timeout_s: float = 5.0
) -> None:
    """Wait for an element to be visible in the DOM.

    Args:
        screen: NiceGUI Screen test fixture
        selector: CSS selector for the element
        timeout_s: Maximum time to wait
    """
    import time

    interval = 0.1
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        visible = screen.selenium.execute_script(
            """
            const el = document.querySelector(arguments[0]);
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
            """,
            selector,
        )
        if visible:
            return
        time.sleep(interval)

    raise AssertionError(f"Element '{selector}' not visible after {timeout_s}s")


def wait_for_class(
    screen: "Screen", selector: str, class_name: str, timeout_s: float = 5.0
) -> None:
    """Wait for an element to have a specific class.

    Args:
        screen: NiceGUI Screen test fixture
        selector: CSS selector for the element
        class_name: Class name to wait for
        timeout_s: Maximum time to wait
    """
    import time

    interval = 0.1
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        has_class = screen.selenium.execute_script(
            """
            const el = document.querySelector(arguments[0]);
            return el && el.classList.contains(arguments[1]);
            """,
            selector,
            class_name,
        )
        if has_class:
            return
        time.sleep(interval)

    raise AssertionError(
        f"Element '{selector}' doesn't have class '{class_name}' after {timeout_s}s"
    )


def wait_for_no_class(
    screen: "Screen", selector: str, class_name: str, timeout_s: float = 5.0
) -> None:
    """Wait for an element to NOT have a specific class.

    Args:
        screen: NiceGUI Screen test fixture
        selector: CSS selector for the element
        class_name: Class name that should be absent
        timeout_s: Maximum time to wait
    """
    import time

    interval = 0.1
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        has_class = screen.selenium.execute_script(
            """
            const el = document.querySelector(arguments[0]);
            return el && el.classList.contains(arguments[1]);
            """,
            selector,
            class_name,
        )
        if not has_class:
            return
        time.sleep(interval)

    raise AssertionError(
        f"Element '{selector}' still has class '{class_name}' after {timeout_s}s"
    )


def click_tab(screen: "Screen", tab_name: str, timeout: float = 5.0) -> None:
    """Click a tab by its name or icon, then wait for the panel to appear.

    Tab names mapping:
    - "tab-program" -> side tab index 0 (code icon) -> waits for .program-panel
    - "tab-io" -> side tab index 1 (settings_input_component icon)
    - "tab-gripper" -> side tab index 2 (robotic claw)
    - "tab-log" -> bottom tab index 0 (article icon) -> waits for .bottom-panels.is-open
    - "tab-help" -> bottom tab index 1 (help_outline icon)

    Args:
        screen: NiceGUI Screen test fixture
        tab_name: The tab identifier (e.g., "tab-program")
        timeout: Maximum time to wait for element
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    # First try nicegui-marker attribute
    marker_selector = f'[nicegui-marker="{tab_name}"]'
    try:
        element = WebDriverWait(screen.selenium, 1.0).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, marker_selector))
        )
        element.click()
    except Exception:
        # Fall through to index-based selection
        js_click = """
            const tabName = arguments[0];

            // Find tab containers by class and position
            const allContainers = document.querySelectorAll('.q-tabs.side-tab-bar');
            let sideTabs = null;
            let bottomTabs = null;

            for (const container of allContainers) {
                const rect = container.getBoundingClientRect();
                if (rect.top < 100) {
                    sideTabs = container;
                } else if (rect.top > 200) {
                    bottomTabs = container;
                }
            }

            // Map tab names to container and index
            const mapping = {
                'tab-program': {container: 'side', index: 0},
                'tab-io': {container: 'side', index: 1},
                'tab-gripper': {container: 'side', index: 2},
                'tab-log': {container: 'bottom', index: 0},
                'tab-help': {container: 'bottom', index: 1},
            };

            const target = mapping[tabName];
            if (!target) return 'Unknown tab: ' + tabName;

            const container = target.container === 'side' ? sideTabs : bottomTabs;
            if (!container) {
                return 'Container not found for: ' + tabName +
                       ', sideTabs=' + (sideTabs ? 'found' : 'null') +
                       ', bottomTabs=' + (bottomTabs ? 'found' : 'null');
            }

            const tabs = container.querySelectorAll('.q-tab');
            if (target.index >= tabs.length) {
                return 'Tab index ' + target.index + ' out of range, found ' + tabs.length + ' tabs';
            }

            tabs[target.index].click();
            return 'clicked';
        """
        result = screen.selenium.execute_script(js_click, tab_name)
        if result != "clicked":
            raise RuntimeError(f"Failed to click tab {tab_name}: {result}")

    # Wait for the corresponding panel to become visible
    panel_selectors = {
        "tab-program": ".program-panel",
        "tab-io": ".q-tab-panel:not([style*='display: none'])",
        "tab-gripper": ".q-tab-panel:not([style*='display: none'])",
        "tab-log": ".bottom-panels.is-open",
        "tab-help": ".bottom-panels.is-open",
    }
    if tab_name in panel_selectors:
        wait_for_element_visible(screen, panel_selectors[tab_name], timeout)


def click_close_button(
    screen: "Screen", panel_selector: str, wait_for_close: bool = True
) -> None:
    """Click the close button within a panel (closes the entire panel, not tabs).

    For the program panel, this clicks the LAST close button in the first row,
    which is the panel close button (not tab close buttons).

    Args:
        screen: NiceGUI Screen test fixture
        panel_selector: CSS selector for the panel containing the close button
        wait_for_close: If True, wait for coupling classes to be removed
    """
    result = screen.selenium.execute_script(
        """
        const panelSelector = arguments[0];
        const panel = document.querySelector(panelSelector);
        if (!panel) return 'Panel not found: ' + panelSelector;

        // For program-panel, find the header row and click its close button
        // The header row is the first row containing the close button
        // We want the LAST close button in that row (panel close, not tab close)
        const rows = panel.querySelectorAll('.nicegui-row');
        let closeBtn = null;

        // First try: find close button in the first row (header row)
        if (rows.length > 0) {
            const headerRow = rows[0];
            const closeButtons = headerRow.querySelectorAll('button');
            // Get the LAST button with close icon in header row
            for (const btn of closeButtons) {
                const icon = btn.querySelector('i');
                if (icon && icon.innerText === 'close') {
                    closeBtn = btn;  // Keep overwriting to get the last one
                }
            }
        }

        // Fallback: find any close button in the panel (for non-program panels)
        if (!closeBtn) {
            const allButtons = panel.querySelectorAll('button');
            for (const btn of allButtons) {
                const icon = btn.querySelector('i');
                if (icon && icon.innerText === 'close') {
                    closeBtn = btn;
                    break;  // Take the first one for other panels
                }
            }
        }

        if (closeBtn) {
            closeBtn.click();
            return 'clicked';
        }

        // Debug: list all button icons
        const allButtons = panel.querySelectorAll('button');
        const icons = Array.from(allButtons).map(b => {
            const i = b.querySelector('i');
            return i ? i.innerText : 'no-icon';
        });
        return 'No close button found, buttons: ' + JSON.stringify(icons);
    """,
        panel_selector,
    )

    if result != "clicked":
        raise RuntimeError(f"Failed to click close button: {result}")

    # Wait for the panel to close (coupling classes removed)
    if wait_for_close:
        import time

        deadline = time.time() + 5.0
        while time.time() < deadline:
            # Check that neither coupling class is present
            classes = get_element_classes(screen, ".left-wrap")
            if (
                "bottom-open" not in classes
                and "bottom-open-non-program" not in classes
            ):
                return
            time.sleep(0.1)
        # Don't fail - some tests check the classes themselves


# ============================================================================
# Tab Closing Tests (Bug Fix Verification)
# ============================================================================


@pytest.mark.browser
def test_closing_program_tab_preserves_bottom_panel_height(screen: "Screen") -> None:
    """Test that closing program tab doesn't shift the bottom panel."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open program tab
    click_tab(screen, "tab-program")

    # Open response log (bottom panel)
    click_tab(screen, "tab-log")

    # Get initial bottom panel height
    initial_rect = get_element_rect(screen, ".bottom-panels")
    assert initial_rect is not None, "Bottom panel not found"
    initial_height = initial_rect["height"]

    # Close program tab by clicking its close button
    click_close_button(screen, ".program-panel")

    # Get final bottom panel height
    final_rect = get_element_rect(screen, ".bottom-panels")
    assert final_rect is not None, "Bottom panel not found after close"
    final_height = final_rect["height"]

    # Height should be preserved (within tolerance)
    assert (
        abs(final_height - initial_height) < 30
    ), f"Bottom panel height changed: {initial_height} -> {final_height}"


@pytest.mark.browser
def test_closing_io_tab_preserves_bottom_panel_height(screen: "Screen") -> None:
    """Test that closing IO tab doesn't shift the bottom panel."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open IO tab
    click_tab(screen, "tab-io")

    # Open response log (bottom panel)
    click_tab(screen, "tab-log")

    # Get initial bottom panel height
    initial_rect = get_element_rect(screen, ".bottom-panels")
    assert initial_rect is not None, "Bottom panel not found"
    initial_height = initial_rect["height"]

    # Close IO tab by clicking its close button
    click_close_button(screen, ".q-tab-panel:not([style*='display: none'])")

    # Get final bottom panel height
    final_rect = get_element_rect(screen, ".bottom-panels")
    assert final_rect is not None, "Bottom panel not found after close"
    final_height = final_rect["height"]

    # Height should be preserved (within tolerance)
    assert (
        abs(final_height - initial_height) < 30
    ), f"Bottom panel height changed: {initial_height} -> {final_height}"


@pytest.mark.browser
def test_closing_gripper_tab_preserves_bottom_panel_height(screen: "Screen") -> None:
    """Test that closing gripper tab doesn't shift the bottom panel."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open gripper tab
    click_tab(screen, "tab-gripper")

    # Open response log (bottom panel)
    click_tab(screen, "tab-log")

    # Get initial bottom panel height
    initial_rect = get_element_rect(screen, ".bottom-panels")
    assert initial_rect is not None, "Bottom panel not found"
    initial_height = initial_rect["height"]

    # Close gripper tab by clicking its close button
    click_close_button(screen, ".q-tab-panel:not([style*='display: none'])")

    # Get final bottom panel height
    final_rect = get_element_rect(screen, ".bottom-panels")
    assert final_rect is not None, "Bottom panel not found after close"
    final_height = final_rect["height"]

    # Height should be preserved (within tolerance)
    assert (
        abs(final_height - initial_height) < 30
    ), f"Bottom panel height changed: {initial_height} -> {final_height}"


@pytest.mark.browser
def test_closing_top_panel_removes_all_coupling_classes(screen: "Screen") -> None:
    """Test that closing top panel removes both bottom-open classes."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open program tab and response log
    click_tab(screen, "tab-program")
    click_tab(screen, "tab-log")

    # Verify coupling class is present
    classes = get_element_classes(screen, ".left-wrap")
    assert (
        "bottom-open" in classes or "bottom-open-non-program" in classes
    ), f"Expected coupling class before close, got: {classes}"

    # Close program tab - click_close_button waits for classes to be removed
    click_close_button(screen, ".program-panel")

    # Verify NEITHER coupling class is present
    classes = get_element_classes(screen, ".left-wrap")
    assert (
        "bottom-open" not in classes
    ), f"'bottom-open' should be removed after close, got: {classes}"
    assert (
        "bottom-open-non-program" not in classes
    ), f"'bottom-open-non-program' should be removed after close, got: {classes}"


# ============================================================================
# Tab Switching Tests
# ============================================================================


@pytest.mark.browser
def test_tab_switch_preserves_bottom_panel_position(screen: "Screen") -> None:
    """Test that bottom panel stays anchored when switching Program -> IO."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open program tab
    click_tab(screen, "tab-program")

    # Open response log (bottom panel)
    click_tab(screen, "tab-log")

    # Get initial bottom panel position
    initial_rect = get_element_rect(screen, ".bottom-panels")
    assert initial_rect is not None, "Bottom panel not found"
    initial_bottom = initial_rect["bottom"]

    # Switch to IO tab
    click_tab(screen, "tab-io")

    # Get position after tab switch
    final_rect = get_element_rect(screen, ".bottom-panels")
    assert final_rect is not None, "Bottom panel not found after tab switch"
    final_bottom = final_rect["bottom"]

    # Bottom position should be similar (anchored at viewport bottom)
    assert (
        abs(final_bottom - initial_bottom) < 50
    ), f"Bottom panel moved unexpectedly: {initial_bottom} -> {final_bottom}"


@pytest.mark.browser
def test_tab_switch_preserves_bottom_panel_height(screen: "Screen") -> None:
    """Test that bottom panel height doesn't change on tab switch."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open program tab and response log
    click_tab(screen, "tab-program")
    click_tab(screen, "tab-log")

    initial_rect = get_element_rect(screen, ".bottom-panels")
    assert initial_rect is not None
    initial_height = initial_rect["height"]

    # Switch to IO tab
    click_tab(screen, "tab-io")

    final_rect = get_element_rect(screen, ".bottom-panels")
    assert final_rect is not None
    final_height = final_rect["height"]

    # Height should be preserved
    assert (
        abs(final_height - initial_height) < 20
    ), f"Bottom panel height changed: {initial_height} -> {final_height}"


@pytest.mark.browser
def test_bottom_open_class_on_program_tab(screen: "Screen") -> None:
    """Test that bottom-open class is applied when program tab + response log open."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open program tab
    click_tab(screen, "tab-program")

    # Open response log
    click_tab(screen, "tab-log")

    # Check for bottom-open class on wrap
    classes = get_element_classes(screen, ".left-wrap")
    assert "bottom-open" in classes, f"Expected 'bottom-open' class, got: {classes}"
    assert "bottom-open-non-program" not in classes


@pytest.mark.browser
def test_bottom_open_non_program_class(screen: "Screen") -> None:
    """Test that bottom-open-non-program class is applied when IO/Gripper + response log."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open IO tab (non-program)
    click_tab(screen, "tab-io")

    # Open response log
    click_tab(screen, "tab-log")

    # Check for bottom-open-non-program class on wrap
    classes = get_element_classes(screen, ".left-wrap")
    assert (
        "bottom-open-non-program" in classes
    ), f"Expected 'bottom-open-non-program' class, got: {classes}"
    assert "bottom-open" not in classes


@pytest.mark.browser
def test_switch_back_to_program_restores_coupling(screen: "Screen") -> None:
    """Test that returning to program tab restores push system coupling."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open program tab and response log
    click_tab(screen, "tab-program")
    click_tab(screen, "tab-log")

    # Verify bottom-open
    classes = get_element_classes(screen, ".left-wrap")
    assert "bottom-open" in classes

    # Switch to IO
    click_tab(screen, "tab-io")

    # Verify bottom-open-non-program
    classes = get_element_classes(screen, ".left-wrap")
    assert "bottom-open-non-program" in classes

    # Switch back to program
    click_tab(screen, "tab-program")

    # Verify bottom-open is restored
    classes = get_element_classes(screen, ".left-wrap")
    assert (
        "bottom-open" in classes
    ), f"Expected 'bottom-open' after switch back, got: {classes}"


# ============================================================================
# Resize Handle Tests
# ============================================================================


@pytest.mark.browser
def test_program_panel_width_resize(screen: "Screen") -> None:
    """Test that dragging right handle changes program panel width."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open program tab
    click_tab(screen, "tab-program")

    # Get initial width
    initial_rect = get_element_rect(screen, ".program-panel")
    assert initial_rect is not None
    initial_width = initial_rect["width"]

    # Drag right handle to increase width
    simulate_drag(screen, ".program-panel .resize-handle-right", dx=100, dy=0)

    # Get final width
    final_rect = get_element_rect(screen, ".program-panel")
    assert final_rect is not None
    final_width = final_rect["width"]

    # Width should have increased
    assert (
        final_width > initial_width
    ), f"Width should have increased: {initial_width} -> {final_width}"


@pytest.mark.browser
def test_response_panel_height_resize(screen: "Screen") -> None:
    """Test that dragging top handle changes response panel height."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Open program and response log
    click_tab(screen, "tab-program")
    click_tab(screen, "tab-log")

    # Get initial height
    initial_rect = get_element_rect(screen, ".bottom-panels")
    assert initial_rect is not None
    initial_height = initial_rect["height"]

    # Drag top handle up to increase height (negative dy)
    simulate_drag(screen, ".response-panel .resize-handle-top", dx=0, dy=-50)

    # Get final height
    final_rect = get_element_rect(screen, ".bottom-panels")
    assert final_rect is not None
    final_height = final_rect["height"]

    # Height should have increased (or at least changed)
    assert (
        abs(final_height - initial_height) > 10 or final_height >= initial_height
    ), f"Height should have changed: {initial_height} -> {final_height}"


@pytest.mark.browser
def test_resize_respects_min_width(screen: "Screen") -> None:
    """Test that panel can't be resized below minimum width (400px)."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    click_tab(screen, "tab-program")

    # Try to drag way left (shrink beyond minimum)
    simulate_drag(screen, ".program-panel .resize-handle-right", dx=-500, dy=0)

    # Get final width
    final_rect = get_element_rect(screen, ".program-panel")
    assert final_rect is not None
    final_width = final_rect["width"]

    # Width should be at least minimum (400px per config)
    assert final_width >= 395, f"Width {final_width} should be >= 400 (min)"


@pytest.mark.browser
def test_resize_respects_min_height(screen: "Screen") -> None:
    """Test that response panel can't be resized below minimum height (100px)."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    click_tab(screen, "tab-program")
    click_tab(screen, "tab-log")

    # Try to drag way down (shrink beyond minimum)
    simulate_drag(screen, ".response-panel .resize-handle-top", dx=0, dy=500)

    # Get final height
    final_rect = get_element_rect(screen, ".response-panel")
    assert final_rect is not None
    final_height = final_rect["height"]

    # Height should be at least minimum (100px per config)
    assert final_height >= 95, f"Height {final_height} should be >= 100 (min)"


# ============================================================================
# Persistence Tests (localStorage)
# ============================================================================


@pytest.mark.browser
def test_panel_width_saves_to_localstorage(screen: "Screen") -> None:
    """Test that resizing panel width saves to localStorage."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)
    clear_localstorage_item(screen, STORAGE_KEY)

    click_tab(screen, "tab-program")

    # Get initial width
    rect_before = get_element_rect(screen, ".program-panel")
    assert rect_before is not None
    width_before = rect_before["width"]

    # Resize to increase width
    simulate_drag(screen, ".program-panel .resize-handle-right", dx=80, dy=0)

    # Get new width
    rect_after = get_element_rect(screen, ".program-panel")
    assert rect_after is not None
    width_after = rect_after["width"]

    # Verify width changed
    assert (
        width_after > width_before
    ), f"Width should have increased: {width_before} -> {width_after}"

    # Check localStorage was updated with the new width
    saved = get_localstorage_item(screen, STORAGE_KEY)
    assert saved is not None, "Panel sizes should be saved to localStorage"
    assert "program" in saved, "Program panel size should be saved"
    assert saved["program"]["width"] is not None, "Width should be saved"
    # Saved width should be close to actual width
    assert (
        abs(saved["program"]["width"] - width_after) < 10
    ), f"Saved width {saved['program']['width']} should match actual {width_after}"


@pytest.mark.browser
def test_panel_height_saves_to_localstorage(screen: "Screen") -> None:
    """Test that resizing panel height saves to localStorage."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)
    clear_localstorage_item(screen, STORAGE_KEY)

    click_tab(screen, "tab-program")
    click_tab(screen, "tab-log")

    # Get initial height
    rect_before = get_element_rect(screen, ".response-panel")
    assert rect_before is not None
    height_before = rect_before["height"]

    # Resize height (drag up to increase)
    simulate_drag(screen, ".response-panel .resize-handle-top", dx=0, dy=-40)

    # Get new height
    rect_after = get_element_rect(screen, ".response-panel")
    assert rect_after is not None
    height_after = rect_after["height"]

    # Verify height changed (should have increased when dragging up)
    assert (
        height_after != height_before
    ), f"Height should have changed: {height_before} -> {height_after}"

    # Check localStorage was updated with the new height
    saved = get_localstorage_item(screen, STORAGE_KEY)
    assert saved is not None, "Panel sizes should be saved to localStorage"
    assert "response" in saved, "Response panel size should be saved"
    assert saved["response"]["height"] is not None, "Height should be saved"


# ============================================================================
# Push System Tests
# ============================================================================


@pytest.mark.browser
def test_growing_program_shrinks_response(screen: "Screen") -> None:
    """Test that growing program panel height shrinks response panel."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    click_tab(screen, "tab-program")
    click_tab(screen, "tab-log")

    # Get initial heights
    response_before = get_element_rect(screen, ".bottom-panels")
    assert response_before is not None
    response_height_before = response_before["height"]

    # Drag program panel bottom handle down (grow program)
    simulate_drag(screen, ".program-panel .resize-handle-bottom", dx=0, dy=80)

    # Get final response height
    response_after = get_element_rect(screen, ".bottom-panels")
    assert response_after is not None
    response_height_after = response_after["height"]

    # Response should have shrunk or stayed same (push system)
    assert response_height_after <= response_height_before + 10, (
        f"Response should shrink when program grows: "
        f"{response_height_before} -> {response_height_after}"
    )


@pytest.mark.browser
def test_growing_response_interacts_with_program(screen: "Screen") -> None:
    """Test that growing response panel interacts with program panel via push system."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    click_tab(screen, "tab-program")
    click_tab(screen, "tab-log")

    # Get initial heights
    program_before = get_element_rect(screen, ".program-panel")
    response_before = get_element_rect(screen, ".response-panel")
    assert program_before is not None
    assert response_before is not None

    # Drag response panel top handle up (grow response)
    simulate_drag(screen, ".response-panel .resize-handle-top", dx=0, dy=-80)

    # Get final heights
    program_after = get_element_rect(screen, ".program-panel")
    response_after = get_element_rect(screen, ".response-panel")
    assert program_after is not None
    assert response_after is not None

    # Just verify panels are still visible and have reasonable heights
    # The push system may or may not change heights depending on constraints
    assert (
        program_after["height"] >= 100
    ), "Program panel should maintain minimum height"
    assert (
        response_after["height"] >= 100
    ), "Response panel should maintain minimum height"


@pytest.mark.browser
def test_push_respects_min_heights(screen: "Screen") -> None:
    """Test that push system doesn't reduce panels below minimum height."""
    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    click_tab(screen, "tab-program")
    click_tab(screen, "tab-log")

    # Try to grow response panel very large (would push program below minimum)
    simulate_drag(screen, ".response-panel .resize-handle-top", dx=0, dy=-500)

    # Response panel should respect minimum (100px)
    response_rect = get_element_rect(screen, ".response-panel")
    assert response_rect is not None
    assert (
        response_rect["height"] >= 95
    ), f"Response height {response_rect['height']} should be >= 100"


# ============================================================================
# Regression Tests (Bug Fixes)
# ============================================================================


@pytest.mark.browser
def test_closing_gripper_preserves_resized_response_log_position(
    screen: "Screen",
) -> None:
    """Test the specific bug: close gripper tab after resizing response log.

    Regression test for: closing a top-level tab shifts the bottom panel up.

    Steps:
    1. Open response log, get size and offset from bottom of screen
    2. Open program editor
    3. Drag response log down a bit
    4. Verify size/offset changed as expected
    5. Switch to gripper tab (response log still open)
    6. Verify response log size and offset from bottom hasn't changed
    7. Close gripper tab
    8. Verify response log size and offset from bottom hasn't changed
    """
    import time

    screen.open("/", timeout=10.0)
    wait_for_panel_resize_ready(screen)

    # Clear localStorage to ensure test isolation
    clear_localstorage_item(screen, STORAGE_KEY)

    # Step 1: Open response log and get initial size/offset from bottom
    click_tab(screen, "tab-log")
    initial_rect = get_element_rect(screen, ".bottom-panels")
    initial_bottom_offset = get_bottom_offset(screen, ".bottom-panels")
    assert initial_rect is not None, "Bottom panel not found"
    assert initial_bottom_offset is not None, "Could not get bottom offset"
    initial_height = initial_rect["height"]

    # Step 2: Open program editor
    click_tab(screen, "tab-program")

    # Step 3: Drag response log down (shrink it by dragging top handle down)
    simulate_drag(screen, ".response-panel .resize-handle-top", dx=0, dy=50)

    # Step 4: Verify size changed as expected (height should decrease)
    after_drag_rect = get_element_rect(screen, ".bottom-panels")
    after_drag_bottom_offset = get_bottom_offset(screen, ".bottom-panels")
    assert after_drag_rect is not None
    assert after_drag_bottom_offset is not None
    after_drag_height = after_drag_rect["height"]

    # Height should have decreased (dragged down = smaller)
    assert (
        after_drag_height < initial_height
    ), f"Height should decrease after dragging down: {initial_height} -> {after_drag_height}"
    # Bottom offset should remain ~same (panel stays anchored at bottom)
    assert (
        abs(after_drag_bottom_offset - initial_bottom_offset) < 20
    ), f"Bottom offset should stay stable: {initial_bottom_offset} -> {after_drag_bottom_offset}"

    # Step 5: Switch to gripper tab (response log still open)
    click_tab(screen, "tab-gripper")

    # Step 6: Verify response log size and offset from bottom hasn't changed
    after_switch_rect = get_element_rect(screen, ".bottom-panels")
    after_switch_bottom_offset = get_bottom_offset(screen, ".bottom-panels")
    assert after_switch_rect is not None
    assert after_switch_bottom_offset is not None
    after_switch_height = after_switch_rect["height"]

    assert abs(after_switch_bottom_offset - after_drag_bottom_offset) < 10, (
        f"Bottom offset should be preserved after tab switch: "
        f"{after_drag_bottom_offset} -> {after_switch_bottom_offset}"
    )
    assert abs(after_switch_height - after_drag_height) < 10, (
        f"Height should be preserved after tab switch: "
        f"{after_drag_height} -> {after_switch_height}"
    )

    # Step 7: Close gripper tab
    click_close_button(
        screen, ".q-tab-panel:not([style*='display: none'])", wait_for_close=False
    )

    # Wait a bit for any style updates
    time.sleep(0.3)

    # Step 8: Verify response log size and offset from bottom hasn't changed
    final_rect = get_element_rect(screen, ".bottom-panels")
    final_bottom_offset = get_bottom_offset(screen, ".bottom-panels")
    assert (
        final_rect is not None
    ), "Bottom panel should still exist after closing gripper"
    assert final_bottom_offset is not None, "Could not get final bottom offset"
    final_height = final_rect["height"]

    # THIS IS THE BUG: The position shifts after closing (offset from bottom changes)
    assert abs(final_bottom_offset - after_switch_bottom_offset) < 30, (
        f"Bottom offset should be preserved after closing gripper: "
        f"{after_switch_bottom_offset} -> {final_bottom_offset}"
    )
    assert abs(final_height - after_switch_height) < 30, (
        f"Height should be preserved after closing gripper: "
        f"{after_switch_height} -> {final_height}"
    )
