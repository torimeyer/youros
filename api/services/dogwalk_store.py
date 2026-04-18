"""Storage path for the dog walking service leads store.

Leads submitted via the contact form are persisted here. This file lives
outside the repo so a git pull never clobbers user data.
"""

from pathlib import Path

DOGWALK_LEADS_PATH: Path = Path.home() / ".myos" / "dogwalk_leads.json"
