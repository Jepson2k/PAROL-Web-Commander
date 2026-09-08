"""Teach and edit static setup snapshots without changing a running program."""

from __future__ import annotations

import asyncio
from typing import ClassVar, cast

from nicegui import ui
from waldoctl import Commander, Panel, PanelSlot
from waldoctl.setup import Frame, Parameter, Pose, PoseValues, SetupSnapshot

from waldo_commander.setup import SetupStore, export_snapshot
from waldo_commander.components.tcp_calibration import TcpCalibrationEditor
from waldo_commander.components.device_signals import DeviceSignalEditor
from waldo_commander.services.python_source import insert_prelude


class NamedSetupPanel(Panel):
    id: ClassVar[str] = "setup"
    display_name: ClassVar[str] = "Setup"
    slot: ClassVar[PanelSlot] = PanelSlot.LEFT_TOP_TAB
    tab_icon: ClassVar[str] = "architecture"
    tab_tooltip: ClassVar[str] = "Named frames, poses and parameters"
    order: ClassVar[int] = 20
    default_width: ClassVar[int] = 440
    default_height: ClassVar[int] = 650
    min_width: ClassVar[int] = 360
    min_height: ClassVar[int] = 420
    resizable: ClassVar[bool] = True

    def build(self, commander: Commander) -> None:
        store = SetupStore()
        snapshot = SetupSnapshot()

        def inform(message: str) -> None:
            status.set_text(message)

        def refresh() -> None:
            frame_options = ["WRF", *snapshot.frames]
            for selector in (frame_parent, pose_frame):
                selector.set_options(
                    frame_options,
                    value=selector.value if selector.value in frame_options else "WRF",
                )
            frame_existing.set_options(list(snapshot.frames), value=None)
            pose_existing.set_options(list(snapshot.poses), value=None)
            parameter_existing.set_options(list(snapshot.parameters), value=None)
            summary.refresh()
            tcp_editor.refresh()
            signal_editor.refresh()

        def set_snapshot(updated: SetupSnapshot) -> None:
            nonlocal snapshot
            snapshot = updated
            refresh()

        def load() -> None:
            nonlocal snapshot
            try:
                loaded = store.load(setup_name.value)
            except (OSError, ValueError) as error:
                inform(str(error))
                return
            snapshot = loaded
            refresh()
            saved.set_value(setup_name.value)
            for selector, entries in (
                (frame_existing, snapshot.frames),
                (pose_existing, snapshot.poses),
                (parameter_existing, snapshot.parameters),
            ):
                if entries:
                    selector.set_value(next(iter(entries)))
            inform(f"Loaded {setup_name.value}")

        def save() -> None:
            try:
                store.save(setup_name.value, snapshot)
            except (OSError, ValueError) as error:
                inform(str(error))
                return
            saved.set_options(store.names(), value=setup_name.value)
            inform(f"Saved {setup_name.value}")

        def insert_load() -> None:
            program = commander.programs.active
            if program is None or program.execution.is_running:
                inform("Open a stopped program before inserting a load call")
                return
            try:
                store.load(setup_name.value)
            except (OSError, ValueError) as error:
                inform(f"Save the setup first: {error}")
                return
            source = f"from waldo_commander.setup import load_setup\nsetup = load_setup({setup_name.value!r})\n\n"
            try:
                new_source = insert_prelude(program.source, source)
            except ValueError as error:
                inform(str(error))
                return
            from waldo_commander.state import ui_state

            textarea = ui_state.textareas_by_tab.get(program.id)
            if textarea is not None:
                textarea.set_value(new_source)
            program.source = new_source
            inform("Inserted setup load at the start of the active program")

        def export() -> None:
            ui.download(export_snapshot(snapshot).encode("utf-8"), "setup_snapshot.py")
            inform("Exported the current fixed snapshot")

        with ui.column().classes("w-full h-full min-h-0 flex-nowrap"):
            with ui.row().classes("w-full items-center"):
                setup_name = (
                    ui.input("Setup name", value="bench")
                    .props("dense")
                    .classes("grow")
                    .mark("setup-name")
                )
                saved = (
                    ui.select(
                        store.names(),
                        label="Saved",
                        on_change=lambda e: setup_name.set_value(e.value)
                        if e.value
                        else None,
                    )
                    .props("dense")
                    .classes("grow")
                    .mark("setup-saved")
                )
            with ui.row():
                ui.button("Load", on_click=load).props("dense flat").mark("setup-load")
                ui.button("Save", on_click=save).props("dense").mark("setup-save")
                ui.button("Insert load call", on_click=insert_load).props(
                    "dense flat"
                ).mark("setup-insert-load")
                ui.button("Export snapshot", on_click=export).props("dense flat").mark(
                    "setup-export"
                )
            status = (
                ui.label(
                    "Edit a setup, then save. Existing program snapshots keep their values."
                )
                .classes("text-caption")
                .mark("setup-status")
            )

            with ui.tabs().classes("w-full shrink-0") as tabs:
                frames_tab = ui.tab("Frames")
                poses_tab = ui.tab("Poses")
                params_tab = ui.tab("Parameters")
                tcp_tab = ui.tab("TCP")
                signals_tab = ui.tab("Signals")

            def coordinates(prefix: str) -> list[ui.number]:
                with ui.grid(columns=3).classes("w-full"):
                    return [
                        ui.number(label, value=0.0, format="%.3f")
                        .props("dense")
                        .classes("w-full")
                        .mark(f"{prefix}-{key}")
                        for key, label in zip(
                            ("x", "y", "z", "rx", "ry", "rz"),
                            (
                                "X (mm)",
                                "Y (mm)",
                                "Z (mm)",
                                "Roll (°)",
                                "Pitch (°)",
                                "Yaw (°)",
                            ),
                        )
                    ]

            def values(inputs: list[ui.number]) -> PoseValues:
                if any(item.value is None for item in inputs):
                    raise ValueError("Fill all six pose coordinates")
                return cast(PoseValues, tuple(float(item.value) for item in inputs))

            async def teach(inputs: list[ui.number], reference: ui.select) -> None:
                try:
                    observed = await asyncio.wait_for(
                        commander.client.pose(), timeout=2.0
                    )
                    if observed is None:
                        raise ValueError("No fresh TCP pose is available")
                    local = snapshot.relative_pose(
                        Pose(cast(PoseValues, tuple(observed))), reference.value
                    )
                    for element, number in zip(inputs, local.values):
                        element.set_value(number)
                    inform("Captured the current TCP; use Set and Save to keep it")
                except (OSError, ValueError, TimeoutError) as error:
                    inform(str(error))

            def set_frame() -> None:
                nonlocal snapshot
                try:
                    snapshot = snapshot.with_frame(
                        frame_name.value,
                        Frame(values(frame_values), frame_parent.value),
                    )
                    refresh()
                    inform(f"Set frame {frame_name.value}; save to persist")
                except ValueError as error:
                    inform(str(error))

            def set_pose() -> None:
                nonlocal snapshot
                try:
                    snapshot = snapshot.with_pose(
                        pose_name.value, Pose(values(pose_values), pose_frame.value)
                    )
                    refresh()
                    inform(f"Set pose {pose_name.value}; save to persist")
                except ValueError as error:
                    inform(str(error))

            def set_parameter() -> None:
                nonlocal snapshot
                try:
                    raw = parameter_value.value
                    if parameter_type.value == "number":
                        value = float(raw)
                    elif parameter_type.value == "integer":
                        value = int(raw)
                    elif parameter_type.value == "boolean":
                        if raw.lower() not in ("true", "false"):
                            raise ValueError("Use true or false for a boolean")
                        value = raw.lower() == "true"
                    else:
                        value = raw
                    snapshot = snapshot.with_parameter(
                        parameter_name.value, Parameter(value, parameter_unit.value)
                    )
                    refresh()
                    inform(f"Set parameter {parameter_name.value}; save to persist")
                except ValueError as error:
                    inform(str(error))

            def remove(kind: str, name: str) -> None:
                nonlocal snapshot
                try:
                    snapshot = snapshot.without(kind, name)
                    refresh()
                    inform(f"Removed {name}; save to persist")
                except (KeyError, ValueError) as error:
                    inform(str(error))

            def select_frame(name: str | None) -> None:
                if name not in snapshot.frames:
                    return
                entry = snapshot.frames[name]
                frame_name.set_value(name)
                frame_parent.set_value(entry.parent)
                for element, number in zip(frame_values, entry.values):
                    element.set_value(number)

            def select_pose(name: str | None) -> None:
                if name not in snapshot.poses:
                    return
                entry = snapshot.poses[name]
                pose_name.set_value(name)
                pose_frame.set_value(entry.frame)
                for element, number in zip(pose_values, entry.values):
                    element.set_value(number)

            def select_parameter(name: str | None) -> None:
                if name not in snapshot.parameters:
                    return
                entry = snapshot.parameters[name]
                parameter_name.set_value(name)
                parameter_type.set_value(
                    {float: "number", int: "integer", bool: "boolean", str: "text"}[
                        type(entry.value)
                    ]
                )
                parameter_value.set_value(
                    str(entry.value).lower()
                    if isinstance(entry.value, bool)
                    else str(entry.value)
                )
                parameter_unit.set_value(entry.unit)

            with ui.tab_panels(tabs, value=frames_tab).classes(
                "w-full flex-1 min-h-0 overflow-y-auto"
            ):
                with ui.tab_panel(frames_tab).classes("p-0"):
                    frame_existing = (
                        ui.select(
                            [],
                            label="Edit frame",
                            on_change=lambda e: select_frame(e.value),
                        )
                        .props("dense")
                        .classes("w-full")
                        .mark("setup-frame-existing")
                    )
                    with ui.row().classes("w-full"):
                        frame_name = (
                            ui.input("Frame name", value="fixture")
                            .props("dense")
                            .classes("flex-1 min-w-0")
                            .mark("setup-frame-name")
                        )
                        frame_parent = (
                            ui.select(["WRF"], value="WRF", label="Parent")
                            .props("dense")
                            .classes("w-36")
                            .mark("setup-frame-parent")
                        )
                    frame_values = coordinates("setup-frame")
                    with ui.row():
                        ui.button(
                            "Use current TCP",
                            on_click=lambda: teach(frame_values, frame_parent),
                        ).props("dense flat").mark("setup-teach-frame")
                        ui.button("Set frame", on_click=set_frame).props("dense").mark(
                            "setup-set-frame"
                        )
                        ui.button(
                            icon="delete",
                            on_click=lambda: remove("frames", frame_name.value),
                        ).props("dense flat").tooltip("Remove frame")
                with ui.tab_panel(poses_tab).classes("p-0"):
                    pose_existing = (
                        ui.select(
                            [],
                            label="Edit pose",
                            on_change=lambda e: select_pose(e.value),
                        )
                        .props("dense")
                        .classes("w-full")
                        .mark("setup-pose-existing")
                    )
                    with ui.row().classes("w-full"):
                        pose_name = (
                            ui.input("Pose name", value="pick")
                            .props("dense")
                            .classes("flex-1 min-w-0")
                            .mark("setup-pose-name")
                        )
                        pose_frame = (
                            ui.select(["WRF"], value="WRF", label="Frame")
                            .props("dense")
                            .classes("w-36")
                            .mark("setup-pose-frame")
                        )
                    pose_values = coordinates("setup-pose")
                    with ui.row():
                        ui.button(
                            "Use current TCP",
                            on_click=lambda: teach(pose_values, pose_frame),
                        ).props("dense flat").mark("setup-teach-pose")
                        ui.button("Set pose", on_click=set_pose).props("dense").mark(
                            "setup-set-pose"
                        )
                        ui.button(
                            icon="delete",
                            on_click=lambda: remove("poses", pose_name.value),
                        ).props("dense flat").tooltip("Remove pose")
                with ui.tab_panel(params_tab).classes("p-0"):
                    parameter_existing = (
                        ui.select(
                            [],
                            label="Edit parameter",
                            on_change=lambda e: select_parameter(e.value),
                        )
                        .props("dense")
                        .classes("w-full")
                    )
                    parameter_name = (
                        ui.input("Parameter name", value="clearance")
                        .props("dense")
                        .mark("setup-parameter-name")
                    )
                    parameter_type = (
                        ui.select(
                            ["number", "integer", "text", "boolean"],
                            value="number",
                            label="Type",
                        )
                        .props("dense")
                        .mark("setup-parameter-type")
                    )
                    with ui.row():
                        parameter_value = (
                            ui.input("Value", value="30")
                            .props("dense")
                            .mark("setup-parameter-value")
                        )
                        parameter_unit = (
                            ui.input("Unit", value="mm")
                            .props("dense")
                            .mark("setup-parameter-unit")
                        )
                    with ui.row():
                        ui.button("Set parameter", on_click=set_parameter).props(
                            "dense"
                        ).mark("setup-set-parameter")
                        ui.button(
                            icon="delete",
                            on_click=lambda: remove("parameters", parameter_name.value),
                        ).props("dense flat").tooltip("Remove parameter")

                with ui.tab_panel(tcp_tab).classes("p-0"):
                    tcp_editor = TcpCalibrationEditor(
                        commander, lambda: snapshot, set_snapshot
                    )
                with ui.tab_panel(signals_tab).classes("p-0"):
                    signal_editor = DeviceSignalEditor(
                        commander, lambda: snapshot, set_snapshot
                    )

            @ui.refreshable
            def summary() -> None:
                rows = []
                for name, pose in snapshot.poses.items():
                    world = snapshot.resolve(pose)
                    rows.append(
                        {
                            "name": name,
                            "frame": pose.frame,
                            "world": ", ".join(f"{v:.2f}" for v in world.values),
                        }
                    )
                if rows:
                    ui.table(
                        columns=[
                            {"name": key, "label": label, "field": key, "align": "left"}
                            for key, label in (
                                ("name", "Pose"),
                                ("frame", "Frame"),
                                ("world", "WRF · mm / degrees"),
                            )
                        ],
                        rows=rows,
                        row_key="name",
                    ).props("dense flat").classes("w-full").mark("setup-resolved-poses")

            summary()
