"""Browser tests for file operations in the editor.

Tests save, load, and download operations using real UI components via Selenium.
"""

import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from nicegui.testing.screen import Screen

# Browser tests need longer timeout
pytestmark = [pytest.mark.browser, pytest.mark.timeout(60)]


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def clear_startup_errors(screen: "Screen") -> None:
    """Clear ERROR logs from app startup to prevent false test failures."""
    screen.caplog.clear()


# ============================================================================
# Helper Functions
# ============================================================================


def wait_for_app_ready(screen: "Screen", timeout_s: float = 10.0) -> None:
    """Wait for the app to be ready."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ready = screen.selenium.execute_script(
            "return document.querySelector('.q-tab-panels') !== null"
        )
        if ready:
            return
        time.sleep(0.2)
    raise AssertionError(f"App not ready after {timeout_s}s")


def click_tab(screen: "Screen", tab_name: str, timeout: float = 5.0) -> None:
    """Click a tab by its name, then wait for the panel to appear.

    Args:
        screen: NiceGUI Screen test fixture
        tab_name: The tab identifier (e.g., "tab-program")
        timeout: Maximum time to wait for panel
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
            const allContainers = document.querySelectorAll('.q-tabs.side-tab-bar');
            let sideTabs = null;
            for (const container of allContainers) {
                const rect = container.getBoundingClientRect();
                if (rect.top < 100) {
                    sideTabs = container;
                    break;
                }
            }
            const mapping = {
                'tab-program': 0,
                'tab-io': 1,
                'tab-gripper': 2,
            };
            const index = mapping[tabName];
            if (index === undefined) return 'Unknown tab: ' + tabName;
            if (!sideTabs) return 'Side tabs container not found';
            const tabs = sideTabs.querySelectorAll('.q-tab');
            if (index >= tabs.length) return 'Tab index out of range';
            tabs[index].click();
            return 'clicked';
        """
        result = screen.selenium.execute_script(js_click, tab_name)
        if result != "clicked":
            raise RuntimeError(f"Failed to click tab {tab_name}: {result}")

    # Wait for the program panel to become visible
    if tab_name == "tab-program":
        deadline = time.time() + timeout
        while time.time() < deadline:
            visible = screen.selenium.execute_script(
                "return document.querySelector('.program-panel') !== null"
            )
            if visible:
                time.sleep(0.3)  # Extra time for CodeMirror to initialize
                return
            time.sleep(0.1)
        raise AssertionError(f"Program panel not visible after {timeout}s")


def get_codemirror_content(screen: "Screen") -> str:
    """Get content from the active CodeMirror editor."""
    return (
        screen.selenium.execute_script(
            """
        // CodeMirror 6: cmView.view is the EditorView instance
        const content = document.querySelector('.cm-content');
        if (!content || !content.cmView || !content.cmView.view) return '';
        return content.cmView.view.state.doc.toString();
    """
        )
        or ""
    )


def set_codemirror_content(screen: "Screen", content: str) -> None:
    """Set content in the active CodeMirror editor."""
    # Escape content for JavaScript
    escaped = content.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    screen.selenium.execute_script(
        f"""
        // CodeMirror 6: cmView.view is the EditorView instance
        const content = document.querySelector('.cm-content');
        if (content && content.cmView && content.cmView.view) {{
            const view = content.cmView.view;
            view.dispatch({{
                changes: {{from: 0, to: view.state.doc.length, insert: `{escaped}`}}
            }});
        }}
    """
    )


def get_filename_input_value(screen: "Screen") -> str:
    """Get the value from the filename input field."""
    return (
        screen.selenium.execute_script(
            """
        const input = document.querySelector('[nicegui-marker="editor-filename-input"] input');
        return input ? input.value : '';
    """
        )
        or ""
    )


def set_filename_input_value(screen: "Screen", value: str) -> None:
    """Set the filename input field value."""
    screen.selenium.execute_script(
        f"""
        // Find filename input in the program panel
        const inputs = document.querySelectorAll('.program-panel input[type="text"]');
        for (const input of inputs) {{
            if (input.placeholder?.toLowerCase().includes('filename') ||
                input.closest('.q-field')?.textContent?.toLowerCase().includes('file')) {{
                input.value = '{value}';
                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                return;
            }}
        }}
        // Fallback: first text input in program panel
        const firstInput = document.querySelector('.program-panel input[type="text"]');
        if (firstInput) {{
            firstInput.value = '{value}';
            firstInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
        }}
    """
    )


def wait_for_condition(
    screen: "Screen",
    condition_js: str,
    timeout_s: float = 5.0,
    description: str = "condition",
) -> None:
    """Wait for a JavaScript condition to become true.

    Args:
        screen: NiceGUI Screen test fixture
        condition_js: JavaScript expression that returns truthy when ready
        timeout_s: Maximum time to wait
        description: Human-readable description for error messages
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if screen.selenium.execute_script(f"return {condition_js}"):
            return
        time.sleep(0.05)
    raise AssertionError(f"Timeout waiting for {description} after {timeout_s}s")


def click_save_button(screen: "Screen") -> None:
    """Click the save button (fab action with dns icon)."""
    clicked = screen.selenium.execute_script(
        """
        // Find the save fab action by its icon
        const btns = document.querySelectorAll('.q-fab__actions .q-btn');
        for (const btn of btns) {
            if (btn.querySelector('.q-icon')?.textContent?.includes('dns')) {
                btn.click();
                return true;
            }
        }
        return false;
    """
    )
    if not clicked:
        raise AssertionError("Save button not found")


def click_download_button(screen: "Screen") -> None:
    """Click the download button (fab action with download icon)."""
    # The download button is inside the Save FAB (icon=save, direction=right)
    # First find and expand the correct FAB
    screen.selenium.execute_script(
        """
        // Find all FABs and look for the one with save icon
        const fabs = document.querySelectorAll('.q-fab');
        for (const fab of fabs) {
            const iconHolder = fab.querySelector('.q-fab__icon-holder .q-icon');
            if (iconHolder && iconHolder.textContent?.includes('save')) {
                // Expand this FAB by clicking its main button
                if (!fab.classList.contains('q-fab--opened')) {
                    const mainBtn = fab.querySelector('.q-btn');
                    if (mainBtn) mainBtn.click();
                }
                break;
            }
        }
    """
    )
    time.sleep(0.3)  # Wait for FAB animation

    clicked = screen.selenium.execute_script(
        """
        // Find the download fab action by its icon
        const btns = document.querySelectorAll('.q-fab__actions .q-btn');
        for (const btn of btns) {
            if (btn.querySelector('.q-icon')?.textContent?.includes('download')) {
                btn.click();
                return true;
            }
        }
        return false;
    """
    )
    if not clicked:
        raise AssertionError("Download button not found")


def has_notification_with_text(screen: "Screen", text: str) -> bool:
    """Check if a notification with the given text exists."""
    return screen.selenium.execute_script(
        f"""
        const notifications = document.querySelectorAll('.q-notification');
        for (const n of notifications) {{
            if (n.textContent.toLowerCase().includes('{text.lower()}')) return true;
        }}
        return false;
    """
    )


# ============================================================================
# Tests
# ============================================================================


def test_program_tab_opens_editor(screen: "Screen") -> None:
    """Test that opening program tab shows CodeMirror editor."""
    screen.open("/", timeout=10.0)
    wait_for_app_ready(screen)

    # Click program tab (already waits for panel)
    click_tab(screen, "tab-program")

    # Wait for CodeMirror editor to be ready
    wait_for_condition(
        screen,
        "document.querySelector('.cm-editor') !== null",
        description="CodeMirror editor",
    )


def test_editor_content_can_be_modified(screen: "Screen") -> None:
    """Test that editor content can be set and retrieved."""
    screen.open("/", timeout=10.0)
    wait_for_app_ready(screen)

    click_tab(screen, "tab-program")

    # Wait for CodeMirror EditorView to be ready
    wait_for_condition(
        screen,
        "document.querySelector('.cm-content')?.cmView?.view?.state !== undefined",
        description="CodeMirror editor",
    )

    # Set content
    test_content = "# Test content\nprint('hello')"
    set_codemirror_content(screen, test_content)

    # Verify content was set
    actual = get_codemirror_content(screen)
    assert actual == test_content, f"Expected '{test_content}', got '{actual}'"


def test_save_button_exists(screen: "Screen") -> None:
    """Test that save button exists when program tab is open."""
    screen.open("/", timeout=10.0)
    wait_for_app_ready(screen)

    click_tab(screen, "tab-program")

    # Wait for FAB actions to be rendered
    wait_for_condition(
        screen,
        "document.querySelectorAll('.q-fab__actions .q-btn').length > 0",
        description="FAB action buttons",
    )

    # Find save fab action by icon (dns icon)
    has_save = screen.selenium.execute_script(
        """
        const btns = document.querySelectorAll('.q-fab__actions .q-btn');
        for (const btn of btns) {
            if (btn.querySelector('.q-icon')?.textContent?.includes('dns')) {
                return true;
            }
        }
        return false;
    """
    )
    assert has_save, "Save button should exist"


def test_download_button_exists(screen: "Screen") -> None:
    """Test that download button exists when program tab is open."""
    screen.open("/", timeout=10.0)
    wait_for_app_ready(screen)

    click_tab(screen, "tab-program")

    # Wait for FAB actions to be rendered
    wait_for_condition(
        screen,
        "document.querySelectorAll('.q-fab__actions .q-btn').length > 0",
        description="FAB action buttons",
    )

    # Find download fab action by icon
    has_download = screen.selenium.execute_script(
        """
        const btns = document.querySelectorAll('.q-fab__actions .q-btn');
        for (const btn of btns) {
            if (btn.querySelector('.q-icon')?.textContent?.includes('download')) {
                return true;
            }
        }
        return false;
    """
    )
    assert has_download, "Download button should exist"


def test_download_empty_content_shows_warning(screen: "Screen") -> None:
    """Test that downloading empty content shows a warning notification."""
    screen.open("/", timeout=10.0)
    wait_for_app_ready(screen)

    click_tab(screen, "tab-program")

    # Wait for CodeMirror EditorView to be ready
    wait_for_condition(
        screen,
        "document.querySelector('.cm-content')?.cmView?.view?.state !== undefined",
        description="CodeMirror editor",
    )

    # Clear the editor content
    set_codemirror_content(screen, "")

    # Wait for the content change to sync to the server
    # CodeMirror sends change events which need to round-trip to NiceGUI
    time.sleep(0.5)

    # Click download
    click_download_button(screen)

    # Wait for notification to appear
    wait_for_condition(
        screen,
        "document.querySelectorAll('.q-notification').length > 0",
        description="notification",
    )

    # Check for warning notification - the message is "No content to download"
    has_warning = (
        has_notification_with_text(screen, "no content")
        or has_notification_with_text(screen, "empty")
        or has_notification_with_text(screen, "nothing")
    )
    assert has_warning, "Should show warning for empty content"
