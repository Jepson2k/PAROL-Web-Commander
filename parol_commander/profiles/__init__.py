"""Robot registry — maps robot names to concrete Robot instances.

Each backend provides a ``Robot`` class that structurally satisfies the
web commander's ``Robot`` Protocol.  This module is the only place that
imports backend-specific packages.
"""

from __future__ import annotations

from parol_commander.robot_interface import Robot

DEFAULT_ROBOT = "parol6"


def get_robot(name: str = DEFAULT_ROBOT) -> Robot:
    """Create a Robot instance by name.

    Raises ``ValueError`` for unknown robot names.
    """
    if name == "parol6":
        try:
            from parol6 import Robot as Parol6Robot
        except ImportError:
            raise ImportError(
                "parol6 backend not installed. Install with: "
                "pip install parol-commander[parol6]"
            ) from None
        return Parol6Robot(normalize_logs=True)

    raise ValueError(f"Unknown robot {name!r}. Available: parol6")
