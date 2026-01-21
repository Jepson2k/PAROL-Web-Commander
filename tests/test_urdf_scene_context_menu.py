"""Test URDF scene context menu and envelope visibility using screen fixture."""

import time

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException

from tests.helpers.wait import screen_wait_for_scene_ready


def _retry_find_elements(finder_func, retries: int = 3, delay: float = 0.2):
    """Retry finding elements to handle StaleElementReferenceException."""
    for i in range(retries):
        try:
            return finder_func()
        except StaleElementReferenceException:
            if i == retries - 1:
                raise
            time.sleep(delay)


@pytest.mark.browser
def test_right_click_shows_context_menu(screen) -> None:
    """Test that right-clicking on the 3D scene shows the context menu."""
    screen.open("/")
    screen_wait_for_scene_ready(screen)

    # Find the 3D scene canvas
    canvas = WebDriverWait(screen.selenium, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "canvas"))
    )

    # Right-click on the canvas
    actions = ActionChains(screen.selenium)
    actions.context_click(canvas).perform()
    time.sleep(0.3)

    # Context menu should appear
    menu = WebDriverWait(screen.selenium, 5).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".q-menu"))
    )
    assert menu.is_displayed(), "Context menu should be visible after right-click"


@pytest.mark.browser
def test_context_menu_has_options(screen) -> None:
    """Test that context menu has menu items when right-clicking."""
    screen.open("/")
    screen_wait_for_scene_ready(screen)

    canvas = WebDriverWait(screen.selenium, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "canvas"))
    )

    actions = ActionChains(screen.selenium)
    actions.context_click(canvas).perform()
    time.sleep(0.3)

    # Find menu items with retry to handle stale element
    def find_menu_items():
        menu = WebDriverWait(screen.selenium, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".q-menu"))
        )
        return menu.find_elements(By.CSS_SELECTOR, ".q-item")

    menu_items = _retry_find_elements(find_menu_items)
    assert len(menu_items) > 0, "Context menu should have options"


@pytest.mark.browser
def test_context_menu_closes_on_click_outside(screen) -> None:
    """Test that context menu closes when clicking outside."""
    screen.open("/")
    screen_wait_for_scene_ready(screen)

    canvas = WebDriverWait(screen.selenium, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "canvas"))
    )

    # Open menu
    actions = ActionChains(screen.selenium)
    actions.context_click(canvas).perform()
    time.sleep(0.3)

    menu = WebDriverWait(screen.selenium, 5).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".q-menu"))
    )
    assert menu.is_displayed(), "Menu should be visible after right-click"

    # Click outside the menu to close it (click on the canvas)
    actions = ActionChains(screen.selenium)
    actions.move_to_element(canvas).click().perform()
    time.sleep(0.3)

    # Menu should be gone or hidden
    menus = screen.selenium.find_elements(By.CSS_SELECTOR, ".q-menu")
    visible_menus = [m for m in menus if m.is_displayed()]
    assert len(visible_menus) == 0, "Context menu should close when clicking outside"


@pytest.mark.browser
def test_envelope_visible_when_mode_on(screen, enable_envelope) -> None:
    """Test that envelope sphere is visible in scene when mode is 'on'.

    Uses the Settings UI to change envelope mode, then verifies the envelope
    object exists in the Three.js scene.
    """
    screen.open("/")
    screen_wait_for_scene_ready(screen)

    # Wait for envelope generation to complete (polling instead of fixed sleep)
    # Hull generation with 500k samples takes ~3-5s plus process pool overhead
    from parol_commander.services.urdf_scene.envelope_mixin import workspace_envelope

    for _ in range(150):  # Up to 15 seconds
        if workspace_envelope._generated and workspace_envelope.stl_url:
            break
        time.sleep(0.1)

    assert workspace_envelope._generated, (
        "Envelope should be generated before testing visibility"
    )

    # Click Settings tab (find by text content)
    settings_tab = WebDriverWait(screen.selenium, 5).until(
        EC.element_to_be_clickable(
            (By.XPATH, "//*[contains(@class, 'q-tab')]//*[text()='Settings']")
        )
    )
    settings_tab.click()
    time.sleep(0.3)

    # Find and click the envelope mode select using JavaScript
    # This is more reliable than complex XPath
    screen.selenium.execute_script(
        """
        const labels = document.querySelectorAll('*');
        for (const label of labels) {
            if (label.textContent.includes('Workspace Envelope') &&
                !label.textContent.includes('Show reachable')) {
                // Find the select in the same row
                const row = label.closest('.row');
                if (row) {
                    const select = row.querySelector('.q-select');
                    if (select) select.click();
                    return;
                }
            }
        }
    """
    )
    time.sleep(0.3)

    # Select "On" from the dropdown
    on_option = WebDriverWait(screen.selenium, 5).until(
        EC.element_to_be_clickable(
            (By.XPATH, "//*[contains(@class, 'q-item')]//*[text()='On']")
        )
    )
    on_option.click()
    time.sleep(1.0)  # Wait for envelope to be added to scene

    # Check for envelope object in the three.js scene
    result = screen.selenium.execute_script(
        """
        const sceneDiv = document.querySelector('.nicegui-scene');
        if (!sceneDiv) return {found: false, objects: []};
        const sceneId = sceneDiv.id;
        const scene = window['scene_' + sceneId];
        if (!scene) return {found: false, objects: []};
        let found = false;
        let objects = [];
        scene.traverse(obj => {
            if (obj.name) objects.push(obj.name);
            if (obj.name && obj.name.toLowerCase().includes('envelope')) found = true;
        });
        return {found: found, objects: objects};
    """
    )

    assert result and result.get("found") is True, (
        f"Envelope sphere should be visible in scene when mode='on'. "
        f"Found objects: {result.get('objects', []) if result else []}"
    )
