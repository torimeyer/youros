"""
Regression test for build_redirect_uri 127.0.0.1 -> localhost normalisation.

Root cause: Vite's proxy uses changeOrigin:true, which rewrites the Host header
to the proxy target (127.0.0.1:PORT). Starlette uses that header to build
request.base_url, so build_redirect_uri returned https://127.0.0.1:8000/...
while Google Cloud Console has https://localhost:8000/... registered. Google's
token endpoint does an exact-match check on redirect_uri and returns
redirect_uri_mismatch, causing token_exchange_failed at the callback.
"""
import os
from unittest.mock import MagicMock


def _make_request(base_url: str) -> MagicMock:
    req = MagicMock()
    req.base_url = base_url
    return req


def test_build_redirect_uri_normalises_127_to_localhost():
    """127.0.0.1 in the computed URI is replaced with localhost."""
    from services.google_auth import build_redirect_uri
    uri = build_redirect_uri(_make_request("https://127.0.0.1:8000/"))
    assert "127.0.0.1" not in uri
    assert uri == "https://localhost:8000/api/auth/google/callback"


def test_build_redirect_uri_localhost_host_unchanged():
    """localhost host passes through unchanged."""
    from services.google_auth import build_redirect_uri
    uri = build_redirect_uri(_make_request("https://localhost:8000/"))
    assert uri == "https://localhost:8000/api/auth/google/callback"


def test_build_redirect_uri_external_host_unchanged():
    """Non-loopback hosts are not affected."""
    from services.google_auth import build_redirect_uri
    uri = build_redirect_uri(_make_request("https://youros.example.com/"))
    assert uri == "https://youros.example.com/api/auth/google/callback"


def test_build_redirect_uri_env_override_takes_precedence(monkeypatch):
    """GOOGLE_REDIRECT_URI env var still wins when set."""
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://example.com/oauth/callback")
    from services import google_auth
    # reload to pick up monkeypatched env
    import importlib
    importlib.reload(google_auth)
    uri = google_auth.build_redirect_uri(_make_request("https://127.0.0.1:8000/"))
    assert uri == "https://example.com/oauth/callback"
