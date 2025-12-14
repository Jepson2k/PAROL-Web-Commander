"""Floating playback overlay component with scrub bar and speed slider."""

import logging
from nicegui import ui

from parol_commander.state import playback_state, simulation_state, robot_state

logger = logging.getLogger(__name__)


class PlaybackOverlay:
    """Floating scrub bar overlay at bottom-center of 3D scene."""

    def __init__(self):
        self.scrub_slider: ui.slider | None = None
        self.speed_slider: ui.slider | None = None
        self.speed_label: ui.label | None = None
        self.step_label: ui.label | None = None
        self.container: ui.element | None = None
        self._play_timer: ui.timer | None = None
        self._speed_row: ui.row | None = None
        # Accumulator for fractional step advancement
        self._tick_accumulator: float = 0.0

    def build(self) -> None:
        """Build floating overlay anchored at bottom-center."""
        with ui.element("div").classes("playback-overlay").style(
            "position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); "
            "z-index: 100; pointer-events: auto; display: none;"
        ) as self.container:
            with (
                ui.card()
                .classes("overlay-card gap-2 items-center")
                .style("padding: 12px 20px; min-width: 480px;")
            ):
                with ui.row().classes("w-full items-center gap-3"):
                    # Step backward
                    ui.button(icon="skip_previous", on_click=self._step_backward).props(
                        "round unelevated dense color=grey-7"
                    ).tooltip("Previous step")

                    # Play/Pause button
                    self._play_btn = ui.button(
                        icon="play_arrow", on_click=self._toggle_play
                    ).props("round unelevated dense color=primary")
                    self._play_btn.tooltip("Play/Pause")

                    # Step forward
                    ui.button(icon="skip_next", on_click=self._step_forward).props(
                        "round unelevated dense color=grey-7"
                    ).tooltip("Next step")

                    # Scrub slider
                    with ui.element("div").classes("flex-1"):
                        self.scrub_slider = (
                            ui.slider(min=0, max=100, step=1)
                            .props("label-always")
                            .bind_value(simulation_state, "current_step_index")
                        )
                        self.scrub_slider.on_value_change(self._on_scrub_change)

                    # Step label
                    self.step_label = ui.label("0 / 0").classes("text-sm min-w-[60px]")

                    # Speed slider (only in sim mode)
                    with ui.row().classes("items-center gap-1") as self._speed_row:
                        ui.icon("speed", size="xs").classes("text-amber-500")
                        self.speed_slider = (
                            ui.slider(min=0.25, max=4.0, step=0.25, value=1.0)
                            .props("dense")
                            .classes("w-20")
                        )
                        self.speed_slider.on_value_change(self._on_speed_change)
                        self.speed_label = ui.label("1.0x").classes(
                            "text-xs min-w-[35px] font-mono"
                        )
                        self._speed_row.tooltip(
                            "Playback speed multiplier. Note: Cannot exceed robot's "
                            "physical limits - scripts at max speed can't go faster, "
                            "scripts at min speed can't go slower."
                        )

                    # Close button
                    ui.button(icon="close", on_click=self.hide).props(
                        "round flat dense color=grey-6"
                    ).tooltip("Close playback bar")

        # Timer for playback tick (base interval 0.1s)
        self._play_timer = ui.timer(0.1, self._playback_tick, active=False)

    def show(self) -> None:
        """Show overlay during playback."""
        if self.container:
            self.container.style(add="display: flex;", remove="display: none;")
        playback_state.is_playing = False  # Start paused
        self._tick_accumulator = 0.0  # Reset accumulator
        self._update_play_button()

    def hide(self) -> None:
        """Hide overlay when not playing."""
        if self.container:
            self.container.style(add="display: none;", remove="display: flex;")
        playback_state.is_playing = False
        if self._play_timer:
            self._play_timer.active = False

    def set_interactive(self, enabled: bool) -> None:
        """Enable/disable scrubbing (disabled in robot mode)."""
        playback_state.scrub_interactive = enabled
        if self.scrub_slider:
            if enabled:
                self.scrub_slider.enable()
            else:
                self.scrub_slider.disable()

        # Hide speed slider in robot mode (can't change physical speed)
        if self._speed_row:
            if enabled and robot_state.simulator_active:
                self._speed_row.set_visibility(True)
            else:
                self._speed_row.set_visibility(False)

    def update_progress(self, current: int, total: int) -> None:
        """Update scrub bar position and label."""
        playback_state.current_step = current
        playback_state.total_steps = total
        simulation_state.current_step_index = current
        simulation_state.total_steps = total

        if self.scrub_slider:
            self.scrub_slider.props(f"max={max(1, total - 1)}")

        if self.step_label:
            self.step_label.text = f"{current} / {total}"

    def _toggle_play(self) -> None:
        """Toggle play/pause state."""
        playback_state.is_playing = not playback_state.is_playing
        simulation_state.is_playing = playback_state.is_playing
        self._update_play_button()

        if playback_state.is_playing:
            self._tick_accumulator = 0.0  # Reset on play

        if self._play_timer:
            self._play_timer.active = playback_state.is_playing

    def _update_play_button(self) -> None:
        """Update play button icon."""
        if hasattr(self, "_play_btn"):
            icon = "pause" if playback_state.is_playing else "play_arrow"
            self._play_btn.props(f"icon={icon}")

    def _step_forward(self) -> None:
        """Step forward one frame."""
        if simulation_state.current_step_index < simulation_state.total_steps - 1:
            simulation_state.current_step_index += 1
            self._update_robot_pose()

    def _step_backward(self) -> None:
        """Step backward one frame."""
        if simulation_state.current_step_index > 0:
            simulation_state.current_step_index -= 1
            self._update_robot_pose()

    def _on_scrub_change(self) -> None:
        """Handle scrub bar value change."""
        if playback_state.scrub_interactive:
            self._update_robot_pose()

    def _on_speed_change(self) -> None:
        """Handle speed slider change."""
        if self.speed_slider:
            speed = self.speed_slider.value
            playback_state.playback_speed = speed
            simulation_state.playback_speed = speed
            if self.speed_label:
                self.speed_label.text = f"{speed:.2g}x"

    def _playback_tick(self) -> None:
        """Timer callback for automatic playback.

        Uses an accumulator to handle fractional speed multipliers.
        Base tick is 0.1s, speed multiplier adjusts how many steps per tick.
        """
        if not playback_state.is_playing:
            return

        # Update step label
        if self.step_label:
            self.step_label.text = f"{simulation_state.current_step_index} / {simulation_state.total_steps}"

        # Sync slider max
        if self.scrub_slider:
            max_val = max(1, simulation_state.total_steps - 1)
            if self.scrub_slider.props.get("max") != max_val:
                self.scrub_slider.props(f"max={max_val}")

        # Accumulate progress based on speed
        # At 1x: advance 1 step per tick (10 steps/sec)
        # At 2x: advance 2 steps per tick (20 steps/sec)
        # At 0.5x: advance 0.5 steps per tick (5 steps/sec, advance every other tick)
        speed = playback_state.playback_speed
        self._tick_accumulator += speed

        # Advance by whole steps
        steps_to_advance = int(self._tick_accumulator)
        self._tick_accumulator -= steps_to_advance

        if steps_to_advance > 0:
            new_index = simulation_state.current_step_index + steps_to_advance
            max_index = simulation_state.total_steps - 1

            if new_index >= max_index:
                # Reached end
                simulation_state.current_step_index = max_index
                self._update_robot_pose()
                # Stop at end
                playback_state.is_playing = False
                simulation_state.is_playing = False
                self._update_play_button()
                if self._play_timer:
                    self._play_timer.active = False
            else:
                simulation_state.current_step_index = new_index
                self._update_robot_pose()

    def _update_robot_pose(self) -> None:
        """Update URDF scene robot pose based on current step."""
        from parol_commander.state import ui_state

        if not ui_state.urdf_scene:
            return

        idx = simulation_state.current_step_index
        if 0 <= idx < len(simulation_state.path_segments):
            segment = simulation_state.path_segments[idx]
            if segment.joints:
                ui_state.urdf_scene.set_axis_values(segment.joints)


# Singleton
playback_overlay = PlaybackOverlay()
