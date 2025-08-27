from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Repository root and controller path
REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_PATH = (REPO_ROOT / "PAROL6-python-API" / "headless_commander.py").as_posix()

# Official PAROL6 documentation URL
PAROL6_OFFICIAL_DOC_URL = "https://github.com/PCrnjak/PAROL-commander-software"

# Ensure PAROL6-python-API on path
sys.path.append((REPO_ROOT / "PAROL6-python-API").as_posix())

try:
    # Official constant from PAROL6
    from PAROL6_ROBOT import Joint_limits_degree  # pyright: ignore[reportMissingImports] # noqa: I001
except Exception as e:
    logging.critical("Failed to import PAROL6_ROBOT.Joint_limits_degree: %s", e)
    # Fail fast: the app should not start without valid joint limits
    sys.exit(1)

JOINT_LIMITS_DEG = Joint_limits_degree
HOST: str = os.getenv("PAROL6_SERVER_HOST", "127.0.0.1")
PORT: int = int(os.getenv("PAROL6_SERVER_PORT", "5001"))
AUTO_START: bool = os.getenv("PAROL6_AUTO_START", "0") in ("1", "true", "True", "yes", "YES")
DEFAULT_COM_PORT: str | None = os.getenv("PAROL6_COM_PORT") or None
UI_PORT: int = int(os.getenv("PAROL6_UI_PORT", "8080"))
