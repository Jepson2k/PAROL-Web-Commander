"""Verify that all programs/ scripts simulate without errors.

Runs each program through the path visualizer's dry-run simulation
(the same code path used when viewing scripts in the editor).
This catches IK failures, missing imports, and API misuse before
the user hits them in the UI.
"""

from pathlib import Path

import pytest

from parol6.client.dry_run_client import DryRunRobotClient
from waldo_commander.services.path_visualizer import _run_simulation_isolated

PROGRAMS_DIR = Path(__file__).resolve().parents[1] / "programs"

# Programs that are actual demos (not test scaffolding or empty files)
PROGRAMS = sorted(
    p.name
    for p in PROGRAMS_DIR.glob("*.py")
    if not p.name.startswith("test_")
    and not p.name.startswith("__")
    and p.stat().st_size > 10
)


@pytest.mark.parametrize("script", PROGRAMS)
def test_program_simulates(script):
    """Each program should simulate without errors in the path visualizer."""
    program_text = (PROGRAMS_DIR / script).read_text()
    result = _run_simulation_isolated(
        program_text,
        dry_run_client_cls=DryRunRobotClient,
    )
    assert result["error"] is None, f"{script} simulation failed:\n{result['error']}"
