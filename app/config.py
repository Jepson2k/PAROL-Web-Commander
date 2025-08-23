from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    """Runtime configuration for the NiceGUI app and controller connection."""
    HOST: str = "127.0.0.1"
    PORT: int = 5001
    AUTO_START: bool = False
    DEFAULT_COM_PORT: Optional[str] = None
    UI_PORT: int = 8080  # NiceGUI server port

    @classmethod
    def from_env(cls) -> "Config":
        host = os.getenv("PAROL6_SERVER_HOST", "127.0.0.1")
        port = int(os.getenv("PAROL6_SERVER_PORT", "5001"))
        auto_start = os.getenv("PAROL6_AUTO_START", "0") in ("1", "true", "True", "yes", "YES")
        default_com = os.getenv("PAROL6_COM_PORT") or None
        ui_port = int(os.getenv("PAROL6_UI_PORT", "8080"))
        return cls(
            HOST=host,
            PORT=port,
            AUTO_START=auto_start,
            DEFAULT_COM_PORT=default_com,
            UI_PORT=ui_port,
        )


# Export a default instance for convenience
Config = Config.from_env()
