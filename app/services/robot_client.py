from __future__ import annotations

from parol6 import AsyncRobotClient
from app.constants import CONTROLLER_HOST, CONTROLLER_PORT

# Module-level singleton instance
client = AsyncRobotClient(
    host=CONTROLLER_HOST, port=CONTROLLER_PORT, timeout=0.30, retries=1
)
