"""Explicit entry selection and physical setup review before a fresh run."""

from nicegui import ui
import waldoctl

from waldo_commander.components.script_execution import script_exec
from waldo_commander.services.programs import is_any_program_running
from waldo_commander.services.control_lease import require_browser_control
from waldo_commander.services.run_records import load_record
from waldo_commander.services.supervised_restart import (
    discover_entries,
    fresh_state,
    source_digest,
)
from waldo_commander.state import ui_state


async def show_supervised_restart() -> None:
    if is_any_program_running():
        ui.notify(
            "Stop the current program before selecting a restart entry", color="warning"
        )
        return
    textarea = ui_state.active_textarea
    source = textarea.value if textarea else ""
    digest = source_digest(source)
    try:
        entries = discover_entries(source)
    except (SyntaxError, ValueError) as error:
        ui.notify(str(error), color="warning")
        return
    reference = None
    with ui.dialog() as dialog, ui.card().classes("w-[720px] max-w-full gap-2"):
        ui.label("Supervised restart").classes("text-lg font-semibold")
        previous = script_exec.last_outcome or "No previous run in this session"
        if script_exec.last_run_source_digest is not None:
            previous += (
                " · same source"
                if digest == script_exec.last_run_source_digest
                else " · different source"
            )
        ui.label(f"Previous run: {previous}").mark("restart-previous-run")
        if script_exec.last_record is not None:
            try:
                events = load_record(script_exec.last_record)
                last_action = next(
                    (
                        e
                        for e in reversed(events)
                        if e["event"] in {"command_started", "skill_started"}
                    ),
                    None,
                )
                if last_action:
                    ui.label(
                        f"Last recorded action: {last_action.get('method', '')} (completion is not implied)"
                    ).classes("text-sm")
            except (OSError, ValueError):
                ui.label("Previous run record is unavailable.").classes("text-sm")
        if not entries:
            ui.label(
                "This program has no declared restart entries. Start runs it from the beginning."
            )
            ui.button("Close", on_click=dialog.close)
            dialog.on("hide", dialog.delete)
            dialog.open()
            return
        ui.label(
            "The selected function starts with fresh Python state. Check the arm, tool, held part and work area before continuing."
        ).classes("text-sm")
        choice = (
            ui.select(
                {
                    e.name: f"{e.name} — {e.description}" if e.description else e.name
                    for e in entries
                },
                value=entries[0].name,
                label="Python entry function",
            )
            .classes("w-full")
            .mark("restart-entry-choice")
        )
        state_label = (
            ui.label("Reading controller state…")
            .classes("whitespace-pre-line text-sm")
            .mark("restart-controller-state")
        )
        confirmation = ui.checkbox("I checked the physical setup for this entry").mark(
            "restart-physical-confirmation"
        )

        async def refresh():
            nonlocal reference
            reference = None
            confirmation.value = False
            start_button.disable()
            state_label.text = "Reading controller state…"
            try:
                current = await fresh_state(waldoctl.commander.client)
                current.require_ready()
                reference = current
                state_label.text = (
                    f"Controller ready · session {current.session_id} · publication {current.seq}\n"
                    f"Referenced · enabled · queue empty\n"
                    f"Joints (°): {', '.join(f'{v:.1f}' for v in current.angles_deg)}\n"
                    f"Tool: {current.tool or 'none'} {current.tool_variant} · TCP: {current.tcp}"
                    + (
                        "\nHand-guiding available at rest; keep hands clear for execution."
                        if current.freedrive
                        else ""
                    )
                )
            except Exception as error:
                state_label.text = (
                    f"Restart unavailable: {error or type(error).__name__}"
                )

        async def start():
            nonlocal reference
            if not confirmation.value or reference is None:
                return
            if not require_browser_control(ui_state.active_client_id):
                return
            start_button.disable()
            if await script_exec.start(
                restart_entry=choice.value,
                restart_reference=reference,
                reviewed_source_digest=digest,
            ):
                dialog.close()
            else:
                reference = None
                confirmation.value = False
                state_label.text = (
                    "Restart refused. Refresh state and check the physical setup again."
                )

        with ui.row():
            start_button = ui.button("Start from entry", on_click=start).mark(
                "restart-start"
            )
            start_button.disable()
            ui.button("Refresh state", on_click=refresh).mark("restart-refresh")
            ui.button("Close", on_click=dialog.close)
        confirmation.on_value_change(
            lambda e: start_button.set_enabled(bool(e.value) and reference is not None)
        )
        choice.on_value_change(lambda _: confirmation.set_value(False))
    dialog.on("hide", dialog.delete)
    dialog.open()
    await refresh()
