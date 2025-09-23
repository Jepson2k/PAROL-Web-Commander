# PAROL-Web-Commander

A modern web interface for controlling the PAROL6 desktop robotic arm. This app provides a NiceGUI-based UI for jogging, monitoring I/O, running calibrations, and controlling grippers over the UDP-based PAROL6 headless controller.

This project integrates:
- Hardware: PAROL6 robot and control board
- Server: Headless controller from the PAROL6 Python API (UDP on port 5001 by default)
- Client: Async UDP client embedded in this web app
- UI: NiceGUI web frontend with live status and controls

Upstream projects and docs:
- PAROL6 hardware: https://github.com/PCrnjak/PAROL6-Desktop-robot-arm
- Commander software (GUI + docs): https://github.com/PCrnjak/PAROL-commander-software
- Python API (headless + UDP client): https://github.com/PCrnjak/PAROL6-python-API
- Official docs: https://source-robotics.github.io/PAROL-docs/

Note: To run a real robot, you must have a PAROL6 control board: https://source-robotics.com/products/parol6-control-board

![PAROL Web Commander Interface](images/readme_screenshot.png)

## Features

- Live robot telemetry (joint angles, pose, I/O, gripper data)
- Joint and Cartesian jogging
- Calibration utilities
- Digital I/O control and status
- SSG-48 electric gripper control
- Built-in program editor and runner (Python) with streamed console output
- Auto status polling and E-stop status display
- URDF viewer with live joint visualization
- Uses UDP client/server from PAROL6 Python API for low-latency control

## How it fits together

- Headless controller (server)
  - Runs on the machine connected to the robot over USB/Serial
  - Listens for UDP commands on port 5001 (default)
  - Provided by the PAROL6 Python API (e.g., `controller.py`)
- Web app (client + UI)
  - This repository runs a NiceGUI server
  - Renders control pages and sends commands via `parol6.AsyncRobotClient`
  - Manages the controller process via `parol6.ServerManager` using `ensure_server(...)`
- Networking
  - Default controller target is `127.0.0.1:5001`
  - Can be reconfigured to a remote controller via environment variables (see Configuration)

## Requirements

- Minimum/validated baseline: Raspberry Pi 5. This app and the headless controller are designed and frequently tested to sustain 100 Hz+ control loops on an RPi 5.
- Other supported platforms: x86_64 Linux, macOS, and Windows are supported for both the UI and the headless controller as long as timing targets and serial/USB requirements are met (performance depends on your CPU and OS).
- OS:
  - Controller: 64-bit Linux recommended (e.g., Raspberry Pi OS 64-bit Bookworm). Other OSes can work if they meet timing targets.
  - UI: Any modern OS with Python 3.11.
- Python: 3.11+
- Hardware: PAROL6 robot with PAROL6 control board connected via USB
- Notes:
  - Ensure your user has permission to access the serial device (e.g., add to the `dialout` group or set udev rules on Linux).
  - If running on lower-performance hardware, consider reducing polling rates and/or running the UI and controller on the same machine.

## Quick start (local controller + UI on the same PC)

1) Clone with submodules (required for external assets and references):
```bash
git clone --recurse-submodules https://github.com/Jepson2k/PAROL-Web-Commander.git
cd PAROL-Web-Commander
```
If you cloned without `--recurse-submodules` then in the repo root run:
```bash
git submodule update --init --recursive
```

2) Create and activate a virtual environment (examples):

macOS/Linux:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Windows (PowerShell):
```powershell
py -3.11 -m venv .venv
. .\.venv\Scripts\Activate.ps1
```

3) Install dependencies for the web UI:
```bash
pip install -r app/requirements.txt
```

4) Connect the robot via USB. Identify the port (examples):
- Windows: `COM5`
- Linux: `/dev/ttyACM0`
- macOS: `/dev/tty.usbmodemXXXX`

5) Run the web UI:
```bash
python -m app.main
```
Open the printed URL in your browser.

6) In the footer, set the serial port and click “Set Port”. On launch the app will, by default, auto-start the headless controller (configurable via `PAROL_AUTO_START` or `--disable-auto-start`). The Set Port action stores the value in local storage and sends it to the controller.

### CLI Options

The web UI accepts several command-line arguments:

```bash
python -m app.main [options]
```

Options:
- `--host HOST`: Webserver bind host (default: `0.0.0.0`)
- `--port PORT`: Webserver bind port (default: `8080`)
- `--controller-host HOST`: Controller host to connect to (default: `127.0.0.1`)
- `--controller-port PORT`: Controller UDP port (default: `5001`)
- `--disable-auto-start`: Disable automatic controller start (overrides `PAROL_AUTO_START`)
- `--log-level LEVEL`: Set log level (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `-v`, `-vv`, `-vvv`: Increase verbosity (INFO / DEBUG / TRACE)
- `-q`: Enable WARNING logging

Examples:
```bash
# Disable automatic controller start (CLI overrides environment)
python -m app.main --disable-auto-start

# Custom ports and more verbose logging
python -m app.main --port 8081 --controller-port 5002 -vv

# Disable auto-start via environment variable
PAROL_AUTO_START=0 python -m app.main
```

## Configuration

Environment variables (read in `app/constants.py` and at runtime):

- `PAROL_CONTROLLER_IP`: Controller host (default: `127.0.0.1`)
- `PAROL_CONTROLLER_PORT`: Controller UDP port (default: `5001`)
- `PAROL_SERVER_IP`: NiceGUI bind host (default: `0.0.0.0`)
- `PAROL_SERVER_PORT`: NiceGUI HTTP port (default: `8080`)
- `PAROL_AUTO_START`: Enable automatic controller start on app launch (default: `1`; set to `0` to disable)
- `PAROL_LOG_LEVEL`: `DEBUG` | `INFO` | `WARNING` | `ERROR` | `CRITICAL` (default: `WARNING`)
- `PAROL_WEBAPP_CONTROL_RATE_HZ`: Jog emission cadence from UI to controller (default: `50`)
- `PAROL_WEBAPP_AUTO_SIMULATOR`: If no serial port is configured, auto-enable simulator on startup (default: `1`)
- `PAROL_WEBAPP_REQUIRE_READY`: Wait for server ready and enable stream_on at startup (default: `1`)
- `PAROL_TRACE`: Enable TRACE-level logging in console/UI logs (`1` / `true` / `yes` / `on`)

Examples (macOS/Linux):
```bash
export PAROL_CONTROLLER_IP=127.0.0.1
export PAROL_CONTROLLER_PORT=5001
export PAROL_SERVER_IP=0.0.0.0
export PAROL_SERVER_PORT=8080
export PAROL_LOG_LEVEL=INFO
export PAROL_AUTO_START=1
```

Notes:
- The UI persists the last-used COM port in its local storage; you can also set it directly from the footer input field.
- The app instantiates `parol6.ServerManager()` to manage the headless controller process and `parol6.AsyncRobotClient()` to send commands/status queries.

## Remote controller scenario (advanced)

If your headless controller runs on a different machine:

1. Start the controller there using the PAROL6 Python API.
2. Ensure UDP port 5001 is open on the controller host.
3. Run this web app on any machine and set:
   - `PAROL_CONTROLLER_IP` to the controller’s IP (e.g. `192.168.1.100`)
   - `PAROL_CONTROLLER_PORT` as needed (default `5001`)

For best results and fewer timing issues, run the web app and controller on the same machine connected to the robot.

## Program editor

The Move tab includes a built-in program editor and runner:
- Default snippet uses `parol6.RobotClient` and is prefilled with the current controller host/port.
- Scripts are saved under `./programs` by default (directory is created if missing).
- “Start” spawns a Python subprocess and streams stdout/stderr into the UI log; `stream_on` is paused during execution and restored when the script completes.
- “Stop” requests a graceful termination and force-kills on timeout if needed.
- “Open” supports uploading text-based programs into the editor.

## Control rates

Jog emission cadence is governed by `PAROL_WEBAPP_CONTROL_RATE_HZ` (default 50 Hz). The UI uses timers to send joint/cartesian jog updates and will log warnings if measured cadence drift exceeds a small tolerance. See:
- `tests/test_webapp_rate.py`
- `tests/test_e2e_rate.py`

## Development and tests

Install dev extras:
```bash
pip install -e .[dev]
```

Run linters and tests:
```bash
ruff check .
mypy .
pytest
```

Test configuration/env notes:
- Unit/acceptance tests avoid hardware by default and prevent simulator/stream waits:
  - `PAROL_WEBAPP_AUTO_SIMULATOR=0`
  - `PAROL_WEBAPP_REQUIRE_READY=0`
- Integration tests may spawn the headless server with fake serial:
  - `PAROL6_FAKE_SERIAL=1`
- Benchmarks are gated to avoid nondeterministic CI noise:
  - Set `PAROL6_RUN_BENCHMARKS=1` to enable the control-loop benchmarks in:
    - `tests/test_control_rate_benchmark.py`
    - `tests/test_control_rate_benchmark_stream.py`

Project config:
- `pyproject.toml` configures `pytest`, `ruff`, and `mypy`.
- Pre-commit hooks are defined in `.pre-commit-config.yaml`.

## Safety and liability

- Always have physical E-Stop accessible. Keep work area clear.
- Software safeguards are not a substitute for safe operating practice. Use at your own risk.

## Credits and licensing

- Hardware: PAROL6 by Source Robotics (see upstream repos)
- Software: This UI builds on the open-source PAROL6 ecosystem:
  - PAROL6-Desktop-robot-arm
  - PAROL-commander-software
  - PAROL6-python-API
- Licensing follows the terms of the upstream components; see LICENSE files in those repositories.
