"""Central configuration for the yourOS API.

All path references derive from PROJECT_ROOT, which is computed from this
file's location so the backend works regardless of where the repo lives.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OSTK_DIR = PROJECT_ROOT / ".ostk"
AGENTS_DIR = PROJECT_ROOT / "agents"

FRONTEND_URL_DEFAULT = "https://localhost:3010"
