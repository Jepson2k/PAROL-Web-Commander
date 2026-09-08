"""Review optional execution records and explicitly download diagnostics."""

import json
from datetime import datetime

from nicegui import ui

from waldo_commander.components.script_execution import script_exec
from waldo_commander.services.run_records import (
    debugging_export,
    load_record,
    record_directory,
)


def show_run_records() -> None:
    paths = sorted(
        record_directory().glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:20]
    selected = {p.stem: p for p in paths}
    options = {
        p.stem: f"{datetime.fromtimestamp(p.stat().st_mtime):%b %d %H:%M:%S} · {p.stem[:8]}"
        for p in paths
    }
    with ui.dialog() as dialog, ui.card().classes("w-[850px] max-w-full gap-2"):
        ui.label("Run records").classes("text-lg font-semibold")
        ui.checkbox(
            "Record future program runs",
            value=script_exec.record_runs,
            on_change=lambda e: setattr(script_exec, "record_runs", bool(e.value)),
        ).mark("record-runs-enabled")
        ui.label(
            "Local records include arguments, results, loaded setup snapshots and sampled controller status. Source code and console output are excluded."
        ).classes("text-sm")
        ui.label(
            "Debugging export keeps numeric values and timing; it removes free text, custom names and mapping keys."
        ).classes("text-sm text-gray-400")
        choice = (
            ui.select(
                options, value=paths[0].stem if paths else None, label="Recent run"
            )
            .classes("w-full")
            .mark("run-record-choice")
        )
        summary = ui.label("No recorded runs yet.").mark("run-record-summary")
        search = (
            ui.input("Filter events")
            .props("dense clearable")
            .classes("w-full")
            .mark("run-record-filter")
        )
        table = (
            ui.table(
                columns=[
                    {"name": "time", "label": "Elapsed (s)", "field": "time"},
                    {"name": "event", "label": "Event", "field": "event"},
                    {"name": "method", "label": "Skill / command", "field": "method"},
                    {"name": "step", "label": "Step", "field": "step"},
                ],
                rows=[],
                row_key="row",
                pagination=12,
            )
            .classes("w-full max-h-[45vh] overflow-auto")
            .props("dense")
            .mark("run-record-events")
        )
        table.bind_filter_from(search, "value", backward=lambda value: value or "")

        def refresh():
            if choice.value not in selected:
                return
            try:
                events = load_record(selected[choice.value])
                started = events[0].get("received_ns", 0) if events else 0
                outcome = next(
                    (
                        e.get("outcome")
                        for e in reversed(events)
                        if e["event"] == "run_finished"
                    ),
                    "No terminal event",
                )
                loss = any(
                    e["event"] in {"events_lost", "record_truncated"} for e in events
                )
                summary.text = f"{outcome} · {len(events)} entries" + (
                    " · incomplete capture" if loss else ""
                )
                table.rows = [
                    {
                        "row": i,
                        "time": round(
                            (e.get("received_ns", started) - started) / 1e9, 3
                        ),
                        "event": e["event"],
                        "method": e.get("method", ""),
                        "step": e.get("step", ""),
                    }
                    for i, e in enumerate(events)
                ]
                table.update()
            except (OSError, ValueError) as error:
                summary.text = f"Record unavailable: {error}"

        def details(event):
            if choice.value not in selected:
                return
            row = event.args.get("row", {})
            try:
                record = load_record(selected[choice.value])[row["row"]]
                with (
                    ui.dialog() as detail_dialog,
                    ui.card().classes("w-[800px] max-w-full"),
                ):
                    ui.label("Local event values")
                    ui.code(json.dumps(record, indent=2), language="json").classes(
                        "w-full max-h-[65vh] overflow-auto"
                    )
                    ui.button("Close", on_click=detail_dialog.close)
                detail_dialog.open()
                detail_dialog.on("hide", detail_dialog.delete)
            except (OSError, ValueError, KeyError, IndexError) as error:
                ui.notify(f"Event unavailable: {error}", color="warning")

        def export():
            if choice.value not in selected:
                return
            try:
                ui.download.content(
                    debugging_export(selected[choice.value]),
                    filename="waldo-debug.json",
                    media_type="application/json",
                )
            except (OSError, ValueError) as error:
                ui.notify(f"Export unavailable: {error}", color="warning")

        table.on("rowClick", details, js_handler="(_event, row) => emit({row})")
        choice.on_value_change(lambda _: refresh())
        refresh()
        with ui.row():
            ui.button("Export debugging data", on_click=export).mark(
                "run-record-export"
            )
            ui.button("Refresh", on_click=refresh)
            ui.button("Close", on_click=dialog.close)
    dialog.on("hide", dialog.delete)
    dialog.open()
