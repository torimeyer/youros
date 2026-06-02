"""Regression test: FRONTEND_URL_DEFAULT must be the unified port 8000.

Since v4.1.0, backend serves the static frontend build on port 8000.
Port 3010 (old Vite dev server) no longer listens on fresh installs.
Any flow falling back to the default (e.g. Gmail OAuth callback with no
FRONTEND_URL env var set) must redirect to :8000, not :3010.
"""

from config import FRONTEND_URL_DEFAULT


def test_frontend_url_default_is_port_8000():
    """FRONTEND_URL_DEFAULT must point to the unified port 8000 (not stale 3010).

    Since v4.1.0, frontend+backend are served together on :8000.
    The old Vite dev server on :3010 is gone. Fresh installs without FRONTEND_URL
    set in ~/.myos/.env would get ERR_CONNECTION_REFUSED if the default is :3010.
    """
    assert FRONTEND_URL_DEFAULT == "https://localhost:8000", (
        f"FRONTEND_URL_DEFAULT is '{FRONTEND_URL_DEFAULT}' but must be "
        "'https://localhost:8000' — port 3010 (old Vite dev server) no longer "
        "listens on fresh installs since v4.1.0 unified frontend+backend onto :8000"
    )
