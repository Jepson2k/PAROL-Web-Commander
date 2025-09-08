from __future__ import annotations

from parol6 import AsyncRobotClient
from app.constants import HOST, PORT

# Module-level singleton instance
client = AsyncRobotClient(host=HOST, port=PORT, timeout=0.30, retries=1)
