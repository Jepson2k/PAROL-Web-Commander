# CLAUDE.md - Project Guidelines for Claude Code

## Project Overview

Waldo Commander is a NiceGUI-based web interface for controlling PAROL6 robotic arms. It provides real-time robot control, script editing, motion recording, and 3D visualization.

## Testing Guidelines

### Browser Tests vs Simulated Tests

- **Use `user` fixture** (simulated) for tests that don't need real browser/JavaScript behavior - much faster
- **Use `screen` fixture** (real browser) only when testing actual browser behavior (JS execution, CSS rendering, etc.)

### Prefer Explicit Waits Over Fixed Sleeps

**Bad:**
```python
await asyncio.sleep(0.5)  # Wastes time if element appears sooner, fails if it takes longer
```

**Good:**
```python
# Wait for specific condition
await user.should_see(marker="some-element")

# Or with Selenium
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
WebDriverWait(driver, timeout=5).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".my-element")))
```

Fixed sleeps should only be used when:
- Waiting for animations (though animations are disabled in tests)
- Debouncing user input simulation
- No other condition can be checked

**For `user` fixture tests:** Sometimes a yield is needed between a click and an assertion. Start with `await asyncio.sleep(0)` first—this yields control without actually waiting. Only increase the sleep duration if the test still fails.

### Running Browser Tests

```bash
# Run all tests (headless by default)
pytest tests/

# Run with visible browser for debugging
HEADED=1 pytest tests/test_specific.py

# Run specific test file
pytest tests/test_editor_integration.py -v
```

### Test Environment Configuration

The following are **already configured in `conftest.py`** - do not set them manually:
- Headless browser mode is the default (only set `HEADED=1` to see the browser)
- `PAROL6_STATUS_RATE_HZ=20` (vs 50Hz default) - reduces CI load
- `PAROL6_FAKE_SERIAL=1` - uses simulator instead of hardware

**IMPORTANT: Do NOT prefix `pytest` commands with environment variables like `PAROL6_FAKE_SERIAL=1 pytest ...`. Everything is already set in conftest.py. Just run `pytest` directly.**

### CI Red Herring Errors

When debugging CI failures, this error is a **secondary symptom** that occurs after a primary failure:

- **`'AppConfig' object has no attribute 'binding_refresh_interval'`** - NiceGUI's binding loop tries to run after app teardown has started. Look at the logs **before** this error to find the root cause.

### Test Markers

- `@pytest.mark.integration` - Integration tests requiring full app setup
- `@pytest.mark.browser` - Tests requiring real browser (Selenium)
- `@pytest.mark.slow` - Long-running tests

### Testing Philosophy

**When CI tests fail, fix them.** Don't waste time analyzing whether failures are "related to your changes" — just fix all failing tests. The goal is a green CI, not attribution.

Prefer fewer, comprehensive integration tests that mimic manual testing over a large number of unit tests. We have no code coverage requirements—the goal is working features, not metrics.

**Test type selection:**

| Type | Value | Maintenance | When to use |
|------|-------|-------------|-------------|
| Integration (`user` fixture) | High | Low | Default choice for most feature testing |
| Browser (`screen` fixture) | High | High | Only when JS behavior must be tested |
| Unit tests | Low | Low | Isolating and testing backend logic |

**Guidelines:**

- **`user` fixture** is always preferred over `screen` for speed and simplicity
- **`screen` fixture** tests are brittle and hard to get right, but they're the closest thing to real testing—use only when necessary (JS-dependent features)
- **Unit tests** are sometimes necessary to isolate backend logic, but rarely preferred over integration tests
- Avoid testing "bloat"—more test code means more maintenance burden without proportional value
- A single comprehensive test that exercises a complete workflow is better than many shallow tests
- **Merge into one function** - When tests are variations of the same thing (e.g., positive/negative jog), combine into one test with multiple assertions
- **Class-level fixture sharing** - When tests are logically separate but don't need isolation, group them in a class with class-scoped fixtures to avoid per-test startup/teardown (especially important for expensive browser tests)
- **Test results are in `test-results.xml` — ALWAYS read this file after running tests.** Pytest writes JUnit XML to `test-results.xml` automatically. It contains test names, durations, failure messages, and full tracebacks. **Do NOT re-run tests** just to capture output you missed — the XML file already has everything. Do NOT grep/tail pytest console output; read the XML file instead.
- **NEVER run parol6 and web commander test suites in parallel** — no proper isolation, they share resources and have timing issues when resource-constrained. Always run sequentially.
- **NEVER allow subagents to run tests.** Many tests are timing-sensitive and the system doesn't have enough resources for agents and tests to run simultaneously. Only the main conversation should run tests, and only after all agents have completed.

## Code Patterns

### State Management

Global state is managed through dataclasses in `waldo_commander/state.py`:
- `robot_state` - Robot joint angles, position, I/O status
- `simulation_state` - Path visualization, targets, playback
- `ui_state` - UI component references
- `recording_state` - Motion recording mode

### NiceGUI Components

Custom components are in `waldo_commander/components/`:
- `editor.py` - Code editor with tabs, script execution
- `control.py` - Jogging controls, robot mode switching
- `readout.py` - Joint/position readouts

### Services

Background services in `waldo_commander/services/`:
- `script_runner.py` - Python script subprocess management
- `path_visualizer.py` - Motion path simulation
- `motion_recorder.py` - Recording robot movements to code

## Common Tasks

### Adding a New UI Element

1. Add to appropriate component in `waldo_commander/components/`
2. Add marker with `.mark("descriptive-marker-name")` for testing
3. Add test in `tests/test_*_integration.py`

### Modifying Robot Communication

Robot communication goes through a `waldoctl.RobotClient` ABC. Each backend (e.g. `parol6`) provides async and sync client implementations. The client is created via `robot.create_async_client()` in `main.py` and passed to components that need it.

## Code Style

- **Comments**: Describe the final implementation, not what changed. Avoid "changed X to Y" or "added this because..." comments.
- **Git commits/PRs**: No emoji, no "Generated by..." footers, no co-author boilerplate.
- **Tests**: Use deterministic waits (polling for conditions) rather than blind sleeps. Exception: very small sleeps (~0.1s) for debouncing are acceptable.
- **Exception handling**: Never use `except Exception: pass`. Either catch specific exceptions with `pass`, or if catching broad exceptions, log or handle the error meaningfully.

### UI Component Preferences
- **Prefer NiceGUI native elements** (ui.chip, ui.image, ui.icon, ui.label, ui.row, etc.) over raw HTML (`ui.html`). Only use `ui.html` when NiceGUI doesn't provide an equivalent.
- **Use `ui.icon`** for Material Icons. Use `ui.icon("img:path")` for custom SVGs. Don't inline SVG content in Python strings.
- **Keep layouts compact** — avoid unnecessary gaps. Don't add `gap-*` classes unless spacing is actually needed.
- **Color preference order**: Tailwind or Quasar color classes first, then `oklab()`, then raw hex as last resort.
