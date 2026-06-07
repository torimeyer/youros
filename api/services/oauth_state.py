"""Shared in-memory CSRF state for OAuth round-trips.

Multiple OAuth integrations (Google, Slack, Atlassian, GitHub) need to mint a
random `state` value on the redirect-out and validate it on the callback.
Keeping a single dict here means we don't end up with one CSRF store per
provider, and adding a new provider is one import.

In-memory is fine: state lives only for the duration of the OAuth round-trip
(~30s of clock time). No need for a database row.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from services.atomic_io import atomic_write_text

# Maps state-token -> True. Presence is the only signal; we delete on use.
oauth_states: dict[str, bool] = {}

# Drive/Calendar/Gmail OAuth states — richer payload with return_to and expiry.
# Persisted to disk so a server restart mid-flow doesn't invalidate in-flight auth.
_DRIVE_STATES_PATH = Path.home() / ".youros" / "oauth_states.json"
_STATE_TTL_SECONDS = 600  # 10 minutes

drive_oauth_states: dict[str, dict] = {}


def load_drive_oauth_states() -> dict:
    """Read persisted drive OAuth states, dropping any that have expired."""
    try:
        if _DRIVE_STATES_PATH.exists():
            data = json.loads(_DRIVE_STATES_PATH.read_text())
            now = time.time()
            return {k: v for k, v in data.items() if v.get("expires", 0) > now}
    except Exception:
        pass
    return {}


def save_drive_oauth_states(states: dict) -> None:
    """Persist drive OAuth states to disk atomically."""
    try:
        _DRIVE_STATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(_DRIVE_STATES_PATH, json.dumps(states))
    except Exception:
        pass


# Seed from disk on import so in-flight states survive a server restart.
drive_oauth_states.update(load_drive_oauth_states())
