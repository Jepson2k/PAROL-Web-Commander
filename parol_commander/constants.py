import logging
import os
from pathlib import Path
from parol6.PAROL6_ROBOT import joint

# Repository root
REPO_ROOT = Path(__file__).resolve().parent.parent

# Official PAROL6 documentation URL
PAROL6_OFFICIAL_DOC_URL = "https://github.com/PCrnjak/PAROL-commander-software"

# Expose as plain Python lists for UI/serialization friendliness
JOINT_LIMITS_DEG = joint.limits.deg.tolist()
# Controller target (what the UI connects to)
CONTROLLER_HOST: str = os.getenv("PAROL_CONTROLLER_IP", "127.0.0.1")
CONTROLLER_PORT: int = int(os.getenv("PAROL_CONTROLLER_PORT", "5001"))
EXCLUSIVE_START: bool = os.getenv("PAROL_EXCLUSIVE_START", "1") in (
    "1",
    "true",
    "True",
    "yes",
    "YES",
)
# Webserver bind (NiceGUI host/port)
SERVER_HOST: str = os.getenv("PAROL_SERVER_IP", "0.0.0.0")
SERVER_PORT: int = int(os.getenv("PAROL_SERVER_PORT", "8080"))


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


# Webapp control emission cadence (client -> controller)
# We want it as low as possible while being higher than the jog duration rate and enough to feel responsive
WEBAPP_CONTROL_RATE_HZ: float = float(os.getenv("PAROL_WEBAPP_CONTROL_RATE_HZ", "20"))
WEBAPP_CONTROL_INTERVAL_S: float = 1.0 / max(WEBAPP_CONTROL_RATE_HZ, 1.0)
