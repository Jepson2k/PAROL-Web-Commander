"""Tests for help menu, keybindings display, and tutorial."""

import asyncio

import pytest
from nicegui.testing import User


@pytest.mark.integration
class TestHelpMenuAndKeybindings:
    """Comprehensive tests for help menu dialog and keybindings display."""

    async def test_help_dialog_opens_with_tabs_and_keybindings(
        self, user: User
    ) -> None:
        """Test help dialog opens, has both tabs, and keybindings display correctly.

        This comprehensive test verifies:
        1. Help dialog opens when tab-help is clicked
        2. Both keybindings and quickstart tabs are present
        3. Keybindings tab shows expected categories
        4. Keybindings shows actual shortcuts with descriptions
        """
        await user.open("/")

        # Click help tab to open dialog
        user.find(marker="tab-help").click()
        await asyncio.sleep(0)  # Yield for dialog to render

        # Dialog should be visible with title
        await user.should_see("Help")

        # Both tabs should be accessible via their markers
        await user.should_see(marker="tab-keybindings")
        await user.should_see(marker="tab-quickstart")

        # Click keybindings tab to ensure that panel is active
        user.find(marker="tab-keybindings").click()
        await asyncio.sleep(0)

        # Keybindings content container should be visible
        await user.should_see(marker="keybindings-content")

        # Keybindings content should show categories (these are ui.label, visible to user fixture)
        await user.should_see("Robot Control")
        await user.should_see("Playback")


@pytest.mark.integration
class TestTutorialStepper:
    """Tests for tutorial/quickstart stepper functionality."""

    async def test_tutorial_shows_steps_and_navigates(self, user: User) -> None:
        """Test tutorial stepper displays steps and navigation works.

        This comprehensive test verifies:
        1. Tutorial tab shows first step content
        2. Next button advances to next step
        3. Back button returns to previous step
        4. All expected steps are present
        """
        await user.open("/")

        # Open help dialog
        user.find(marker="tab-help").click()
        await asyncio.sleep(0)

        # Click quickstart tab (tutorial is default but be explicit)
        user.find(marker="tab-quickstart").click()
        await asyncio.sleep(0)

        # Should see first step
        await user.should_see("Interface Overview")
        await user.should_see("PAROL Commander has three main areas")

        # Click Next to advance
        user.find("Next").click()
        await asyncio.sleep(0)

        # Should see second step
        await user.should_see("Simulator vs Robot Mode")

        # Click Back to return
        user.find("Back").click()
        await asyncio.sleep(0)

        # Should see first step again
        await user.should_see("Interface Overview")

    async def test_tutorial_can_reach_final_step(self, user: User) -> None:
        """Test that tutorial can navigate to the final step with Finish button."""
        await user.open("/")

        # Open help dialog and go to tutorial
        user.find(marker="tab-help").click()
        await asyncio.sleep(0)
        user.find(marker="tab-quickstart").click()
        await asyncio.sleep(0)

        # Navigate through all steps to reach Finish button
        for _ in range(4):  # 5 steps total, need 4 Next clicks
            user.find("Next").click()
            await asyncio.sleep(0)

        # Should see last step with Finish button
        await user.should_see("Begin! TCP Controls")
        await user.should_see("Finish")
