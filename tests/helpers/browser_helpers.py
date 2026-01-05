"""Shared helpers for browser/Selenium tests.

These helpers are used across multiple browser test files and provide
consistent patterns for interacting with the UI via Selenium.
"""

from typing import TYPE_CHECKING, Any

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

if TYPE_CHECKING:
    from nicegui.testing.screen import Screen


def js(screen: "Screen", script: str, *args) -> Any:
    """Execute JavaScript and return result."""
    return screen.selenium.execute_script(script, *args)


def click_tab(screen: "Screen", tab_name: str, timeout: float = 5.0) -> None:
    """Click a tab by finding it via CSS selector, wait for it to become active.

    Args:
        screen: Selenium screen fixture
        tab_name: One of 'program', 'io', 'gripper', 'log', 'help'
        timeout: Max seconds to wait for tab to become active
    """
    # Map tab names to their icon names
    tab_icons = {
        "program": "code",
        "io": "settings_input_component",
        "log": "article",
        "help": "help",
    }
    icon_name = tab_icons.get(tab_name)
    if not icon_name:
        raise ValueError(f"Unknown tab: {tab_name}. Valid: {list(tab_icons.keys())}")

    # Find tab by looking for the icon within a q-tab
    tabs = screen.selenium.find_elements(By.CSS_SELECTOR, ".q-tab")
    target_tab = None
    for tab in tabs:
        try:
            icon = tab.find_element(By.CSS_SELECTOR, ".q-icon")
            if icon.text == icon_name:
                target_tab = tab
                break
        except Exception:
            continue

    if not target_tab:
        raise AssertionError(f"Tab with icon '{icon_name}' not found")

    target_tab.click()

    # Wait for tab to become active
    WebDriverWait(screen.selenium, timeout).until(
        lambda d: "q-tab--active" in (target_tab.get_attribute("class") or "")
    )


def find_button_by_icon(screen: "Screen", icon_name: str) -> WebElement | None:
    """Find a button containing a Material icon.

    Args:
        screen: Selenium screen fixture
        icon_name: Material icon name (e.g., 'camera_alt', 'code', 'close')

    Returns:
        The button WebElement if found, None otherwise
    """
    buttons = screen.selenium.find_elements(By.TAG_NAME, "button")
    for btn in buttons:
        try:
            icon = btn.find_element(By.TAG_NAME, "i")
            if icon.text == icon_name:
                return btn
        except Exception:
            continue
    return None


def click_button_by_icon(
    screen: "Screen", icon_name: str, timeout: float = 5.0
) -> None:
    """Click a button by its Material icon name.

    Args:
        screen: Selenium screen fixture
        icon_name: Material icon name (e.g., 'camera_alt', 'close')
        timeout: Max seconds to wait for button to be clickable

    Raises:
        AssertionError: If button with icon not found
    """
    btn = find_button_by_icon(screen, icon_name)
    if btn is None:
        raise AssertionError(f"Button with icon '{icon_name}' not found")

    WebDriverWait(screen.selenium, timeout).until(EC.element_to_be_clickable(btn))
    btn.click()


def close_panel(screen: "Screen", panel_class: str) -> None:
    """Close a panel by clicking its close button (the last one in the panel).

    Args:
        screen: Selenium screen fixture
        panel_class: CSS class of the panel (e.g., 'program-panel', 'response-panel')
    """
    panel = screen.selenium.find_element(By.CSS_SELECTOR, f".{panel_class}")
    # Find all close buttons and click the last one (panel close, not tab close)
    close_buttons = panel.find_elements(By.XPATH, ".//button[.//i[text()='close']]")
    if not close_buttons:
        raise AssertionError(f"No close button found in .{panel_class}")
    close_buttons[-1].click()


def dismiss_dialogs(screen: "Screen", timeout: float = 2.0) -> None:
    """Dismiss any open dialogs by clicking the backdrop or pressing Escape.

    Waits for the tutorial dialog (which appears ~1s after page load) to appear,
    dismisses it, then sets localStorage to prevent future dialogs.

    Args:
        screen: Selenium screen fixture
        timeout: Max seconds to wait for dialog operations
    """
    from selenium.common.exceptions import TimeoutException

    def has_visible_dialog() -> bool:
        """Check if any dialog backdrop is currently visible."""
        try:
            backdrops = screen.selenium.find_elements(
                By.CSS_SELECTOR, ".q-dialog__backdrop"
            )
            return any(b.is_displayed() for b in backdrops)
        except Exception:
            return False

    def close_dialogs() -> None:
        """Try to close any open dialogs."""
        # Click the "Skip Tour" button or any close button in the dialog
        js(
            screen,
            """
            // Find and click Skip Tour or close buttons
            const buttons = document.querySelectorAll('.q-dialog button');
            for (const btn of buttons) {
                const text = btn.textContent.toLowerCase();
                if (text.includes('skip') || text.includes('close')) {
                    btn.click();
                    return;
                }
                // Also check for close icon
                const icon = btn.querySelector('i');
                if (icon && icon.textContent === 'close') {
                    btn.click();
                    return;
                }
            }
            // Fallback: click the backdrop
            const backdrop = document.querySelector('.q-dialog__backdrop');
            if (backdrop) backdrop.click();
            """,
        )

    # Wait for tutorial dialog to appear (it has a 1s delay after page load)
    # Use short timeout since dialog may not appear if localStorage already set
    try:
        WebDriverWait(screen.selenium, timeout).until(lambda _: has_visible_dialog())
    except TimeoutException:
        pass  # No dialog appeared, that's fine

    # If a dialog is visible, close it and wait for it to be gone
    if has_visible_dialog():
        close_dialogs()
        WebDriverWait(screen.selenium, timeout).until(
            lambda _: not has_visible_dialog()
        )


def wait_for_codemirror_ready(screen: "Screen", timeout: float = 10.0) -> None:
    """Wait for CodeMirror editor to be fully interactive.

    Args:
        screen: Selenium screen fixture
        timeout: Max seconds to wait

    Raises:
        TimeoutError: If CodeMirror not ready in time
    """
    condition_js = """(() => {
        const cm = document.querySelector('.cm-editor');
        if (!cm) return false;
        const content = cm.querySelector('.cm-content');
        if (!content) return false;
        return content.isContentEditable;
    })()"""

    def check_ready(driver):
        return driver.execute_script(f"return {condition_js}")

    try:
        WebDriverWait(screen.selenium, timeout).until(check_ready)
    except Exception as e:
        raise TimeoutError(f"CodeMirror not ready after {timeout}s") from e
