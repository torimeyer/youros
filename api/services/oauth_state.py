"""Shared in-memory CSRF state for OAuth round-trips.

Multiple OAuth integrations (Google, Slack, Atlassian, GitHub) need to mint a
random `state` value on the redirect-out and validate it on the callback.
Keeping a single dict here means we don't end up with one CSRF store per
provider, and adding a new provider is one import.

In-memory is fine: state lives only for the duration of the OAuth round-trip
(~30s of clock time). No need for a database row.
"""

from __future__ import annotations

# Maps state-token -> True. Presence is the only signal; we delete on use.
oauth_states: dict[str, bool] = {}
