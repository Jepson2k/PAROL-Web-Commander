"""Help menu component with keybindings and quick start tutorial."""

from nicegui import ui

from parol_commander.services.keybindings import keybindings_manager


class HelpMenu:
    """Help dialog with vertical tabs for keybindings and quick start tutorial."""

    FIRST_VISIT_KEY = "parol_first_visit_shown"

    def __init__(self) -> None:
        self._dialog: ui.dialog | None = None
        self._stepper: ui.stepper | None = None
        self._keybindings_container: ui.element | None = None

    def show_help_dialog(self) -> None:
        """Show the main help dialog with vertical tabs."""
        if self._dialog:
            self._dialog.delete()
            self._dialog = None

        self._dialog = ui.dialog().classes("help-dialog").mark("help-dialog")

        with self._dialog:
            with ui.card().classes("overlay-card help-dialog-card p-0 overflow-hidden"):
                with ui.row().classes("gap-0"):
                    # Left side: vertical tabs
                    with ui.column().classes("help-tabs-column shrink-0"):
                        with ui.tabs().props("vertical dense").classes(
                            "help-vertical-tabs"
                        ) as tabs:
                            keybindings_tab = (
                                ui.tab(name="keybindings", label="", icon="keyboard")
                                .classes("help-tab")
                                .tooltip("Keybindings")
                                .mark("tab-keybindings")
                            )
                            quickstart_tab = (
                                ui.tab(name="quickstart", label="", icon="school")
                                .classes("help-tab")
                                .tooltip("Quick Start")
                                .mark("tab-quickstart")
                            )

                    # Right side: content
                    with ui.column().classes("flex-1 gap-0 overflow-hidden"):
                        # Header with close button
                        with ui.row().classes(
                            "w-full items-center px-4 py-2 shrink-0"
                        ).style("border-bottom: 1px solid rgba(255,255,255,0.1);"):
                            ui.label("Help").classes("text-lg font-medium")
                            ui.space()
                            ui.button(icon="close", on_click=self._dialog.close).props(
                                "flat round dense color=white"
                            )

                        # Tab panels with vertical animation (matching vertical tabs)
                        with ui.tab_panels(tabs, value=quickstart_tab).classes(
                            "w-full overflow-hidden"
                        ).props(
                            "animated transition-prev=slide-up transition-next=slide-down"
                        ):
                            with ui.tab_panel(keybindings_tab).classes("p-0"):
                                self._build_keybindings_content()

                            with ui.tab_panel(quickstart_tab).classes("p-0").style(
                                "width: 720px; height: 700px; max-height: 85vh;"
                            ):
                                self._build_quickstart_stepper()

        self._dialog.open()

    def _build_keybindings_content(self) -> None:
        """Build the keybindings table content."""
        categories = keybindings_manager.get_all_bindings()

        with ui.column().classes("w-full p-4 gap-4").mark("keybindings-content"):
            if not categories:
                ui.label("No keybindings registered").classes("text-gray-500")
                return

            # Sort categories for consistent display
            category_order = [
                "Robot Control",
                "Playback",
                "Recording",
                "Cartesian Jog",
                "Speed Control",
            ]
            sorted_categories = sorted(
                categories.items(),
                key=lambda x: (
                    category_order.index(x[0]) if x[0] in category_order else 999,
                    x[0],
                ),
            )

            for category, bindings in sorted_categories:
                with ui.column().classes("w-full gap-1"):
                    ui.label(category).classes("text-sm font-medium text-gray-400")

                    # Build rows with key parts as list for template rendering
                    rows = []
                    for i, binding in enumerate(bindings):
                        key_parts = []
                        if binding.requires_ctrl:
                            key_parts.append("Ctrl")
                        if binding.requires_alt:
                            key_parts.append("Alt")
                        if binding.requires_shift:
                            key_parts.append("Shift")
                        key_parts.append(binding.display)

                        rows.append(
                            {
                                "id": f"{category}-{i}",
                                "keys": key_parts,
                                "description": binding.description,
                            }
                        )

                    columns = [
                        {
                            "name": "keys",
                            "label": "Key",
                            "field": "keys",
                            "align": "left",
                        },
                        {
                            "name": "description",
                            "label": "Description",
                            "field": "description",
                            "align": "left",
                        },
                    ]

                    table = (
                        ui.table(columns=columns, rows=rows, row_key="id")
                        .props("flat dense hide-header hide-pagination")
                        .classes("keybindings-table")
                    )

                    # Custom slot to render keys as keyboard icons
                    table.add_slot(
                        "body-cell-keys",
                        """
                        <q-td :props="props" class="keys-cell">
                            <span class="kbd-group">
                                <template v-for="(key, idx) in props.value" :key="idx">
                                    <span class="kbd-key">{{ key }}</span>
                                    <span v-if="idx < props.value.length - 1" class="kbd-plus">+</span>
                                </template>
                            </span>
                        </q-td>
                    """,
                    )

    def _build_quickstart_stepper(self) -> None:
        """Build quick start stepper with GIF placeholders."""
        steps = [
            {
                "title": "Interface Overview",
                "description": "PAROL Commander has three main areas: the 3D view (center), control panel (bottom-right), and program editor (left). The 3D view shows your robot and lets you interact with it directly.",
                "gif": "step-1-overview.gif",
            },
            {
                "title": "Simulator vs Robot Mode",
                "description": "Toggle between simulator mode (amber robot) and real hardware control using the robot/controller button. In simulator mode, you can test programs safely without moving the real robot.",
                "gif": "step-2-simulator.gif",
            },
            {
                "title": "Program Tab",
                "description": "Write Python programs to control your robot. Why Python? It gives you full programming power - loops, conditionals, math, and access to the complete robot API. Programs run step-by-step so you can pause and inspect.",
                "gif": "step-3-program.gif",
            },
            {
                "title": "Set Serial Port",
                "description": "To connect to real hardware, open Settings and select your serial port. The robot status indicator will turn green when connected.",
                "gif": "step-4-serial.gif",
            },
            {
                "title": "Begin! TCP Controls",
                "description": "Click the TCP (Tool Center Point) in the 3D view to show movement gizmos. Drag the arrows to jog in cartesian space, or use the rotation rings for orientation. You can also use WASD+QE keys for quick jogging!",
                "gif": "step-5-tcp.gif",
            },
        ]

        with ui.scroll_area().classes("w-full h-full tutorial-scroll"):
            with ui.stepper().props("vertical header-nav flat").classes("p-0").style(
                "width: 700px;"
            ) as self._stepper:
                for i, step in enumerate(steps):
                    with ui.step(step["title"]).classes("gap-2"):
                        # GIF placeholder
                        with ui.card().classes("gif-placeholder").style(
                            "width: 100%; aspect-ratio: 16/9; background: rgba(128,128,128,0.15);"
                        ):
                            with ui.column().classes(
                                "w-full h-full items-center justify-center"
                            ):
                                ui.icon("movie", size="2rem").classes("text-gray-500")
                                ui.label(f"Tutorial GIF: {step['title']}").classes(
                                    "text-center text-gray-500 text-xs"
                                )

                        ui.label(step["description"]).classes("text-sm")

                        with ui.stepper_navigation():
                            if i < len(steps) - 1:
                                ui.button("Next", on_click=self._stepper.next).props(
                                    "color=primary"
                                )
                            else:
                                ui.button("Finish", on_click=self._on_finish).props(
                                    "color=primary"
                                )
                            if i > 0:
                                ui.button(
                                    "Back", on_click=self._stepper.previous
                                ).props("flat")

    def _on_finish(self) -> None:
        """Handle finish button click - mark tutorial complete and close dialog."""
        self._mark_tutorial_complete()
        if self._dialog:
            self._dialog.close()

    def _mark_tutorial_complete(self) -> None:
        """Mark tutorial as complete in localStorage."""
        ui.run_javascript(f'localStorage.setItem("{self.FIRST_VISIT_KEY}", "true");')

    def check_first_visit(self) -> None:
        """Check if this is the first visit and show tutorial dialog if so."""
        ui.run_javascript(
            f"""
            (function() {{
                const seen = localStorage.getItem("{self.FIRST_VISIT_KEY}");
                if (!seen) {{
                    emitEvent('show_first_time_tutorial', {{}});
                }}
            }})();
            """
        )

    def show_dialog(self) -> None:
        """Show the first-time tutorial dialog (alias for backwards compatibility)."""
        self.create_first_time_dialog().open()

    def create_first_time_dialog(self) -> ui.dialog:
        """Create and return the first-time tutorial dialog."""
        self._dialog = ui.dialog()

        with self._dialog:
            with ui.card().classes("overlay-card tutorial-dialog-card"):
                with ui.column().classes("w-full h-full gap-0"):
                    # Header
                    ui.label("Welcome to PAROL Commander!").classes("text-xl font-bold")

                    ui.label(
                        "Let's get you started with a quick tour of the interface."
                    ).classes("text-sm text-gray-400 mb-3 shrink-0")

                    # Quick start stepper
                    self._build_quickstart_stepper()

                    # Footer with checkbox
                    with ui.row().classes("w-full items-center pt-3 shrink-0").style(
                        "border-top: 1px solid rgba(255,255,255,0.1);"
                    ):
                        dont_show = ui.checkbox("Don't show this again")
                        dont_show.on(
                            "update:model-value",
                            lambda e: self._save_dont_show_pref(e.args),
                        )
                        ui.space()
                        ui.button("Skip Tour", on_click=self._dialog.close).props(
                            "flat"
                        )

        return self._dialog

    def _save_dont_show_pref(self, value: bool) -> None:
        """Save don't show again preference to localStorage."""
        if value:
            self._mark_tutorial_complete()


# Singleton
help_menu = HelpMenu()
