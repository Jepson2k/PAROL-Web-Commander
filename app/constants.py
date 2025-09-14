from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from parol6.PAROL6_ROBOT import Joint_limits_degree

# Repository root and controller path
REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_PATH = (REPO_ROOT / "PAROL6-python-API" / "controller.py").as_posix()
PAROL6_URDF_PATH = (
    REPO_ROOT
    / "external"
    / "PAROL6-Desktop-robot-arm"
    / "PAROL6_URDF"
    / "PAROL6"
    / "urdf"
    / "PAROL6.urdf"
)

# Official PAROL6 documentation URL
PAROL6_OFFICIAL_DOC_URL = "https://github.com/PCrnjak/PAROL-commander-software"

# Ensure PAROL6-python-API on path
sys.path.append((REPO_ROOT / "PAROL6-python-API").as_posix())

# Ensure urdf_scene_nicegui on path
sys.path.append((REPO_ROOT / "urdf_scene_nicegui" / "src").as_posix())


JOINT_LIMITS_DEG = Joint_limits_degree
HOST: str = os.getenv("PAROL6_SERVER_HOST", "127.0.0.1")
PORT: int = int(os.getenv("PAROL6_SERVER_PORT", "5001"))
AUTO_START: bool = os.getenv("PAROL6_AUTO_START", "0") in (
    "1",
    "true",
    "True",
    "yes",
    "YES",
)
DEFAULT_COM_PORT: str | None = os.getenv("PAROL6_COM_PORT") or None
UI_PORT: int = int(os.getenv("PAROL6_UI_PORT", "8080"))


def _resolve_log_level() -> int:
    s = os.getenv("PAROL_LOG_LEVEL")
    if s:
        name = s.strip().upper()
        mapping = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        return mapping.get(name, logging.WARNING)
    else:
        return logging.WARNING


LOG_LEVEL: int = _resolve_log_level()
