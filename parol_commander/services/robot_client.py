from parol6 import AsyncRobotClient
from parol_commander.constants import CONTROLLER_HOST, CONTROLLER_PORT

# Module-level singleton instance
client = AsyncRobotClient(
    host=CONTROLLER_HOST, port=CONTROLLER_PORT, timeout=0.30, retries=1
)
