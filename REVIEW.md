# Codebase Review: `parol_commander`

Full review of ~20k lines across 40 files. Findings validated across 4 discovery rounds and 1 validation round (32 Opus agents total).

---

## Structural Themes

Five areas where multiple findings cluster around a deeper design issue. Individual patches are listed under each theme; fixing the structural root would address several at once.

### A. `main.py` — ad-hoc page-scoped state management

Module-level globals (`_connection_notification`, `_page_client`, panel references) hold page-scoped UI elements but are cleaned up inconsistently in `_on_disconnect`. Grouping all page-scoped state into a single object replaced atomically on page rebuild would eliminate this class of bug.

| # | Severity | Finding |
|---|----------|---------|
| 10 | Consider | Status consumer catches all exceptions at DEBUG level — `main.py:1207-1208`. A `TypeError` or `AttributeError` from a code change would be silently swallowed unless DEBUG logging is enabled. Consider rate-limited escalation (first occurrence at WARNING). |
| 17 | Consider | `_build_gripper_content` closure stashed in `ui_state` as `Any` — `main.py:502-554`. Works but the typing could be `Callable[[], None] | None` for static checking. |

### B. `editor.py` — file is overloaded (~1666 lines, 10 findings)

Tab lifecycle, command discovery, snippet generation, simulation scheduling, recording, log management, playback coordination, and file tree logic. Code written at different times for the same concept diverges. Further mixin extraction (tab lifecycle, simulation coordination) would naturally consolidate multiple findings.

| # | Severity | Finding |
|---|----------|---------|
| 35 | **Must fix** | File tree uses bare filename as node ID — `file_operations.py:133-134`. Files in subdirectories can't be opened because `load_program` resolves `PROGRAM_DIR / "demo.py"` instead of `PROGRAM_DIR / "examples/demo.py"`. Also causes ID collisions for same-named files in different directories. **Fix**: Use path relative to `PROGRAM_DIR` as node ID. |
| 38 | **Must fix** | Closing a tab during recording leaves stale `program_textarea` — `editor.py:1161-1200`. `_do_close_tab` deletes the tab's widgets, then `_switch_to_tab` returns early during recording without updating `self.program_textarea`. Motion recorder subsequently accesses a deleted widget. **Fix**: Guard `_do_close_tab` against closing the active tab while recording, or disable close buttons during recording. |
| 34 | Consider | Default-script tab reset logic duplicated — `editor.py:921-927` and `:1109-1113`. The latter is missing `tab.tool_actions = []` (harmless on fresh tabs due to dataclass default, but fragile). |
| 28 | Consider | Log expand/collapse button+tooltip updates repeated 4x — `editor.py:1042-1081`. A `_update_log_toggle_button(expanded: bool)` helper would unify all four. |

### C. `path_preview_client.py` — triple-layer `__getattr__` is hard to reason about

`_ToolCollectionProxy.__getattr__` wraps `PathPreviewClient.__getattr__` wraps dry-run client methods, and `AsyncPathPreviewClient.__getattr__` adds a caching layer on top. Findings #3, #24, #25 are all symptoms of this indirection making subtle bugs easy to introduce. Replacing dynamic dispatch with explicit method definitions for the known method set would prevent this class of bug.

| # | Severity | Finding |
|---|----------|---------|
| 3 | Should fix | `_ToolCollectionProxy.__getattr__` wraps non-callables — `path_preview_client.py:61-70`. Returns a callable closure for every attribute access including properties. The underlying dry-run tool has the same pattern and scripts only call methods (low practical impact), but inconsistent with `_SteppingToolProxy` which checks `callable()`. **Fix**: Add `attr = getattr(dry_run_tool, name); if not callable(attr): return attr`. |
| 24 | Should fix | Failed target rotation not converted to radians — `path_preview_client.py:438-444`. Comment says "Convert mm/deg to m/rad" but only position is converted; rotation stays in degrees. Valid targets get radians from `tcp_poses`. Mixed units in the same data structure. |
| 25 | Should fix | `AsyncPathPreviewClient` caching bypasses `_flush_blend` — `path_preview_client.py:680-689`. `object.__setattr__` caches the wrapper; subsequent calls skip `PathPreviewClient.__getattr__` and its `_flush_blend()` side effect. Low-risk since `set_tool` is typically called once, but a real latent bug. |
| 30 | Consider | Segment dict construction repeated 3x with 14+ identical keys — `path_preview_client.py:364,493,537`. A `_make_segment(...)` factory would reduce drift risk. |
| 49 | Consider | Duplicate `PathPreviewClient` constructor kwargs — `path_visualizer.py:163-185`. `LocalPathPreviewClient` and `LocalAsyncPathPreviewClient` pass the same 6 kwargs. |
| 27 | Consider | `MockTimeModule.time()` / `monotonic()` return constant 0.0 — `path_visualizer.py:215-233`. Scripts with time-based polling loops hang until subprocess timeout. Deliberate tradeoff for simulation speed. |
| 32 | Consider | Backend module monkeypatching duplicated — `path_visualizer.py:188-192` and `stepping_bootstrap.py:71-80`. |
| 43 | Should fix | Service-to-component import inversion — `motion_recorder.py:92` imports `discover_robot_commands` from `editor.py`. Only service→component import in `services/`. The function does pure introspection and should live in a shared location. |

### D. `urdf_scene/` — mixin boundaries create duplication

Helpers that should be shared (world-position extraction, angle padding, rotation setup) get duplicated across mixin files. A small `urdf_scene/_utils.py` for shared extraction/transformation helpers would address multiple findings. Dead/trivial wrapper methods accumulate because each mixin's interface was designed independently.

| # | Severity | Finding |
|---|----------|---------|
| 37 | **Must fix** | Angle pipeline sign/offset index mismatch — `angle_pipeline.py:65` stores signs at output index `i`, but `numba_pipelines.py:48` reads at input index `src_idx`. With non-identity `index_mapping`, the kernel applies the wrong sign/offset. Currently masked because `urdf_index_mapping` defaults to identity `[0,1,2,3,4,5]`. **Fix**: Store at `controller_idx` position, or read at `i` position in kernel. |
| 47 | Consider | Duplicate world-position extraction — `tcp_controls_mixin.py:402-412` and `:484-494`. 11 identical lines extracting `wx/wy/wz` with fallback. Extract `_extract_world_pos(e)`. |
| 48 | Consider | Duplicate editing_angles pad/truncate — `urdf_scene.py:1491-1493` and `editing_mixin.py:127-128`. `enter_editing_mode` could delegate to `set_editing_angles`. |
| 53 | Consider | `generate()` / `generate_sync()` share ~90% identical preamble — `envelope_mixin.py:279-304` and `:364-386`. One behavioral difference (`True` vs `False` for `_generating` guard). |
| 54 | Consider | Tool action rendering methods duplicate ~20 lines of per-motion setup — `path_renderer_mixin.py:228-262` and `:309-355`. Rotation extraction, axis transform, travel params all identical. |
| 19 | Consider | Repetitive diff-update pattern — segments/tool-actions/waypoints in `urdf_scene.py`. Tool action and waypoint blocks could share a generic diff-reconciler. |
| 18 | Consider | `get_joint_names()` wraps `joint_groups.keys()` — `urdf_scene.py:1517-1519`. Not dead (called from `main.py:222` and tests), but `self.joint_names` is already a public attribute used everywhere else. |

### E. `stepping_client.py` — two sides of one IPC protocol with duplicated infrastructure

`StepIO` (script subprocess) and `GUIStepController` (NiceGUI server) duplicate path construction, file reading, and event parsing. Simple module-level helpers would eliminate the duplication without over-engineering a shared base class.

| # | Severity | Finding |
|---|----------|---------|
| 29 | Consider | `StepIO.__init__` and `GUIStepController.__init__` both construct identical `_control_file` / `_event_file` paths from `session_id`. Event reading logic is also duplicated. Extract `_ipc_paths(session_id)` and a module-level `_read_events`. |
| 12 | Consider | Unbounded event file — `stepping_client.py:98-109`. `emit_event` re-reads, appends, and re-writes the full JSON list. O(N^2) total I/O for long scripts. |

---

## Standalone Findings

Issues not tied to a structural theme.

### Must fix

| # | Finding |
|---|---------|
| 22 | **`_jog_end_wait_task` finally block race** — `control.py:1284-1304`. When the old task is cancelled, its `finally` block sets `self._jog_end_wait_task = None`, overwriting the reference to the newly created replacement task. Subsequent calls can't cancel the orphaned task, leading to spurious `on_jog_end()` calls. **Fix**: Guard the finally with `if self._jog_end_wait_task is current_task`. |

### Should fix

| # | Finding |
|---|---------|


### Consider

| # | Finding |
|---|---------|
| 6 | `logger.log(5, ...)` in `motion_recorder.py:247` inside `TRACE_ENABLED` guard — intentional performance pattern, not a style violation. |
| 9 | Duplicated chip style string — `readout.py:175-181` and `:254-260`. 5-line style block with only the color varying. |
| 11 | Hardcoded `"WRF"`/`"TRF"` — `settings.py:433-434`, `control.py:1109`. Currently fine (settings locked, only one robot), but fragile. |
| 13 | Near-duplicate `_increase_jog_speed` / `_decrease_jog_speed` — `keybindings.py:473-486`. Differ only in `+10`/`-10` and `min`/`max`. |
| 14 | Duplicate close-button pattern — `ui.button(icon="close",...).props("flat round dense color=white")` appears 8x across `main.py`, `file_operations.py`, `help_menu.py`, `editor.py`, `editing_mixin.py`. |
| 15 | Readout X/Y/Z and Rx/Ry/Rz blocks are near-identical — `readout.py:326-392`. Intentional differences in font size/units, but a parameterized helper could reduce repetition. |
| 16 | `_Config` properties repeat override-check pattern — `constants.py:35-98`. Each property checks `_overrides` then env var. Could use a descriptor, but current code is clear. |
| 31 | Duplicate exception-guarded `client.tool` access — `control.py:219-223` and `gripper.py:62-67`. Same `(RuntimeError, KeyError, NotImplementedError)` tuple. |
