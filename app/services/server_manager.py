from __future__ import annotations

from parol6 import ServerManager

# Enable log normalization for web GUI to avoid duplicate timestamp/level/module info
server_manager = ServerManager(normalize_logs=True)
