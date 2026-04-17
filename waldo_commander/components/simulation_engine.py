"""Simulation engine: debounced path preview + diagnostics update."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

import numpy as np
from nicegui import Client, context, ui

from waldo_commander.state import (
    editor_tabs_state,
    robot_state,
    simulation_state,
    ui_state,
)
from waldo_commander.services.path_visualizer import path_visualizer
from waldo_commander.components.editor_decorations import EditorDecorations
from waldo_commander.components.playback import PlaybackController

logger = logging.getLogger(__name__)


class SimulationEngine:
    """Owns path-preview simulation scheduling + debouncing.

    Called from:
    - EditorPanel._on_tab_content_change → schedule_debounced_simulation
    - EditorPanel build() → ui.timer(check_position_changed)
    - settings.py → editor_panel.simulation.schedule_debounced_simulation
    - EditorPanel._do_close_tab → cancel_tab_simulation
    """

    def __init__(
        self,
        *,
        playback: PlaybackController,
        decorations: EditorDecorations,
        get_textarea_value: Callable[[], str],
        get_program_log: Callable[[], ui.log | None],
        get_ui_client: Callable[[], Client | None],
        is_default_script: Callable[[str], bool],
    ) -> None:
        self._playback = playback
        self._decorations = decorations
        self._get_textarea_value = get_textarea_value
        self._get_program_log = get_program_log
        self._get_ui_client = get_ui_client
        self._is_default_script = is_default_script

        self._simulation_debounce_timer: ui.timer | None = None
        self._debounce_delay: float = 1.0
        self._pending_simulations: dict[str, asyncio.Task] = {}

    async def run_simulation(self, tab_id: str | None = None) -> str | None:
        """Run the simulation for the current script.

        Args:
            tab_id: Optional tab ID to run simulation for. If None, uses active tab.

        Returns:
            Error message if simulation failed, None otherwise.
        """
        if tab_id is None:
            tab_id = editor_tabs_state.active_tab_id

        content = self._get_textarea_value()
        if not content:
            return None

        loading = self._playback.sim_loading_progress
        if loading:
            loading.visible = True
        try:
            error = await path_visualizer.update_path_visualization(
                content, tab_id=tab_id
            )
        finally:
            if loading:
                loading.visible = False

        from waldo_commander.services.path_visualizer import _UNCHANGED

        tab = editor_tabs_state.find_tab_by_id(tab_id) if tab_id else None
        if tab and (error is None or error == _UNCHANGED):
            n = ui_state.active_robot.joints.count
            tab.last_sim_joints_deg = robot_state.angles.deg[:n].copy()

        if error == _UNCHANGED:
            return None

        self._playback.invalidate_timeline()
        simulation_state.sim_playback_time = 0.0

        self._playback.update_scrub_segments()

        # Apply initial tool selection from script to scene and controller
        if simulation_state.tool_selections and ui_state.urdf_scene:
            first_sel = simulation_state.tool_selections[0]
            if first_sel.segment_index < 0:
                tool_key = first_sel.tool_key
                variant_key = first_sel.variant_key or None
                ui_state.active_robot.set_active_tool(
                    tool_key,
                    variant_key=variant_key,
                )
                ui_state.urdf_scene.apply_tool(
                    tool_key,
                    variant_key=variant_key,
                )
                ui_state.urdf_scene._update_tcp_ball_position()
                if ui_state.control_panel and ui_state.control_panel.client:
                    try:
                        await ui_state.control_panel.client.select_tool(
                            tool_key,
                            variant_key=variant_key or "",
                        )
                    except Exception as e:
                        logger.debug("select_tool sync failed: %s", e)

        program_log = self._get_program_log()
        if error and program_log:
            program_log.push(f"[SIM ERROR] {error}")

        self._decorations.apply_diagnostics(error)
        self._decorations.push_line_metadata()
        self._decorations.push_target_positions()

        return error

    def schedule_debounced_simulation(self, tab_id: str | None = None) -> None:
        """Schedule a debounced simulation run when code changes.

        Cancels any pending *or running* simulation and schedules a new one after
        the debounce delay.
        """
        if tab_id is None:
            tab_id = editor_tabs_state.active_tab_id
        if not tab_id:
            return

        if self._simulation_debounce_timer is not None:
            logger.debug("DEBOUNCE: Cancelling pending/running simulation")
            self._simulation_debounce_timer.cancel(with_current_invocation=True)
            self._simulation_debounce_timer = None

        # Default script optimization — skip simulation, update state directly
        tab = editor_tabs_state.find_tab_by_id(tab_id)
        if tab and self._is_default_script(tab.content):
            tab.final_joints_rad = ui_state.active_robot.joints.home.rad.tolist()
            tab.path_segments = []
            tab.targets = []
            tab.tool_actions = []
            if tab_id == editor_tabs_state.active_tab_id:
                simulation_state.path_segments = []
                simulation_state.targets = []
                simulation_state.tool_actions = []
                simulation_state.total_steps = 0
                try:
                    ui_client = self._get_ui_client() or context.client
                    with ui_client:
                        simulation_state.notify_changed()
                except RuntimeError:
                    simulation_state.notify_changed()
                self._playback.update_scrub_segments()
            return

        async def run_simulation_quietly():
            try:
                logger.debug("DEBOUNCE: Starting simulation...")
                await self.run_simulation(tab_id=tab_id)
                logger.debug("DEBOUNCE: Simulation completed successfully")
            except asyncio.CancelledError:
                logger.debug("DEBOUNCE: Simulation cancelled by newer edit")
            except Exception as e:
                logger.error("Auto-simulation failed: %s", e, exc_info=True)
                ui.notify(f"Simulation error: {e}", color="negative", timeout=3000)
            finally:
                if self._simulation_debounce_timer is my_timer:
                    self._simulation_debounce_timer = None

        logger.debug(
            "DEBOUNCE: Scheduling new timer with delay=%.3fs", self._debounce_delay
        )
        my_timer = ui.timer(self._debounce_delay, run_simulation_quietly, once=True)
        self._simulation_debounce_timer = my_timer

    def check_position_changed(self) -> None:
        """Periodically check if robot position changed and re-run path preview."""
        if (
            simulation_state.script_running
            or robot_state.editing_mode
            or self._simulation_debounce_timer is not None
            or simulation_state.sim_pose_override
            or simulation_state.sim_playback_active
        ):
            return

        active_tab = editor_tabs_state.get_active_tab()
        if not active_tab or active_tab.last_sim_joints_deg is None:
            return

        if not self._get_textarea_value():
            return

        current_deg = robot_state.angles.deg[: ui_state.active_robot.joints.count]
        if np.max(np.abs(current_deg - active_tab.last_sim_joints_deg)) > 0.5:
            self.schedule_debounced_simulation()

    def cancel_tab_simulation(self, tab_id: str) -> None:
        """Cancel any pending simulation for a tab (called from _do_close_tab)."""
        if tab_id in self._pending_simulations:
            self._pending_simulations[tab_id].cancel()
            del self._pending_simulations[tab_id]
