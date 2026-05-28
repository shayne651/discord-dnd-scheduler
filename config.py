"""
config.py — Load and validate environment variables.
All other modules import from here instead of reading os.environ directly.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"[ERROR] Missing required environment variable: {name}")
        print("        Copy .env.example to .env and fill in your values.")
        sys.exit(1)
    return value

def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default)

# ── Required ────────────────────────────────────────────────────────────────
BOT_TOKEN: str = _require("DISCORD_BOT_TOKEN")
GUILD_ID: int  = int(_require("GUILD_ID"))

# ── Set by /init wizard — do not need to be in .env ─────────────────────────
POLL_CHANNEL_ID: int      = int(_optional("POLL_CHANNEL_ID", "0"))
DM_NOTIFY_CHANNEL_ID: int = int(_optional("DM_NOTIFY_CHANNEL_ID", "0"))

# ── Extra text channels created inside the D&D category by /init ─────────────
# Edit this list (or set EXTRA_CHANNELS=general,maps,notes,pictures in .env)
_extra_raw = _optional("EXTRA_CHANNELS", "general,maps,notes,pictures")
EXTRA_CHANNELS: list[str] = [c.strip() for c in _extra_raw.split(",") if c.strip()]

# ── Optional (have sensible defaults) ───────────────────────────────────────
SESSION_DAY: str    = _optional("SESSION_DAY", "Saturday")
PLAYER_ROLE_NAME: str = _optional("PLAYER_ROLE_NAME", "DnD Player")
DM_ROLE_NAME: str   = _optional("DM_ROLE_NAME", "Dungeon Master")

# ── Day-name → weekday int mapping (Python: Mon=0 … Sun=6) ─────────────────
DAY_NAME_TO_INT: dict[str, int] = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}

SESSION_DAY_INT: int = DAY_NAME_TO_INT.get(SESSION_DAY, 5)  # default Saturday
