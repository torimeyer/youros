"""Regression test: no hardcoded frontend ports in backend routers.

After a bug where OAuth redirects went to localhost:5173 (Vite default)
instead of localhost:3010 (our actual frontend port), this test scans all
router modules for hardcoded localhost URLs with any port. Every frontend
URL must go through FRONTEND_URL / os.environ so it works in any env.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROUTERS_DIR = Path(__file__).resolve().parent.parent / "routers"


def _find_hardcoded_localhost_urls(source: str) -> list[str]:
    """Return all hardcoded http://localhost:<port> strings in source.

    Ignores comments and REDIRECT_URI (the OAuth callback goes to the
    backend, which is legitimately on a fixed port during local dev).
    """
    hits = []
    for i, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Skip the OAuth redirect_uri (backend port, not frontend)
        if "REDIRECT_URI" in line:
            continue
        # Skip lines that read from env (these are configurable defaults)
        if "os.environ.get" in line or "environ.get" in line:
            continue
        matches = re.findall(r'["\']http://localhost:\d+/\S*["\']', line)
        for m in matches:
            hits.append(f"line {i}: {m}")
    return hits


@pytest.mark.parametrize("router_file", sorted(ROUTERS_DIR.glob("*.py")))
def test_no_hardcoded_frontend_urls(router_file: Path):
    """Backend routers must not hardcode frontend URLs with a port number.

    Use os.environ.get('FRONTEND_URL', 'http://localhost:3010') instead,
    so the port is configurable and consistent across routers.
    """
    source = router_file.read_text()
    hits = _find_hardcoded_localhost_urls(source)
    assert not hits, (
        f"{router_file.name} has hardcoded localhost URLs:\n"
        + "\n".join(f"  {h}" for h in hits)
        + "\n\nUse os.environ.get('FRONTEND_URL', 'http://localhost:3010') instead."
    )
