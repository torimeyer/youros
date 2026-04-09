"""Google Drive OAuth2 authentication service.

Tokens are stored in ~/.myos/google_token.json (outside the repo).
The user must supply credentials at ~/.myos/google_credentials.json
(downloaded from Google Cloud Console).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

MYOS_DIR = Path.home() / ".myos"
TOKEN_PATH = MYOS_DIR / "google_token.json"
CREDENTIALS_PATH = MYOS_DIR / "google_credentials.json"
DRIVE_CACHE_DIR = MYOS_DIR / "drive_cache"

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# Redirect URI used during the OAuth flow.  The backend serves the callback.
REDIRECT_URI = "http://localhost:8000/api/drive/auth/callback"


def _ensure_dirs() -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    DRIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def credentials_file_exists() -> bool:
    """Return True if the user has placed credentials at the expected path."""
    return CREDENTIALS_PATH.exists()


def _load_client_config() -> dict:
    """Read client_id/client_secret from the user-supplied credentials file."""
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not read {CREDENTIALS_PATH}: {exc}"
        ) from exc
    # Support both 'web' and 'installed' app types from Google Cloud Console.
    for key in ("web", "installed"):
        if key in data:
            return data[key]
    # Flat format (rare).
    return data


def get_auth_url(state: str) -> str:
    """Build the Google OAuth consent-screen URL."""
    cfg = _load_client_config()
    client_id = cfg["client_id"]
    scope_str = " ".join(SCOPES)
    import urllib.parse

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": scope_str,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        params
    )


def exchange_code(code: str) -> None:
    """Exchange an authorization code for tokens and persist them."""
    import urllib.request
    import urllib.parse

    _ensure_dirs()
    cfg = _load_client_config()
    payload = urllib.parse.urlencode(
        {
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        }
    ).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        tokens = json.loads(resp.read())

    # Store raw tokens (includes refresh_token on first auth).
    TOKEN_PATH.write_text(json.dumps(tokens))


def _refresh_if_needed(tokens: dict) -> dict:
    """Refresh the access token if it is expired or nearly expired."""
    expires_at = tokens.get("expires_at", 0)
    if expires_at and time.time() < expires_at - 60:
        return tokens

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return tokens

    try:
        import urllib.request
        import urllib.parse

        cfg = _load_client_config()
        payload = urllib.parse.urlencode(
            {
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=payload,
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            new_tokens = json.loads(resp.read())

        # Merge: keep the refresh token (Google doesn't re-issue it every time).
        new_tokens.setdefault("refresh_token", refresh_token)
        expires_in = new_tokens.get("expires_in", 3600)
        new_tokens["expires_at"] = time.time() + int(expires_in)
        TOKEN_PATH.write_text(json.dumps(new_tokens))
        return new_tokens
    except Exception:
        return tokens


def get_credentials() -> dict:
    """Return current tokens (refreshed if needed).

    Raises RuntimeError if the user has not authenticated.
    """
    if not TOKEN_PATH.exists():
        raise RuntimeError("Not authenticated. Connect your Google account first.")
    tokens = json.loads(TOKEN_PATH.read_text())
    return _refresh_if_needed(tokens)


def is_authenticated() -> bool:
    """Return True if we have a saved token."""
    return TOKEN_PATH.exists()


def get_email() -> str | None:
    """Return the email address stored in the token, if available."""
    if not TOKEN_PATH.exists():
        return None
    try:
        tokens = json.loads(TOKEN_PATH.read_text())
        # Try id_token claims first.
        id_token = tokens.get("id_token")
        if id_token:
            import base64

            # JWT payload is the second segment.
            parts = id_token.split(".")
            if len(parts) >= 2:
                payload_b64 = parts[1] + "=="
                try:
                    claims = json.loads(base64.urlsafe_b64decode(payload_b64))
                    email = claims.get("email")
                    if email:
                        return email
                except Exception:
                    pass
        return tokens.get("email")
    except Exception:
        return None


def has_write_scope() -> bool:
    """Return True if the stored token includes the drive.file write scope.

    The token must contain a 'scope' field (Google includes this in the
    token response).  If the field is missing we assume the old token does
    not have write access and the user needs to reconnect.
    """
    if not TOKEN_PATH.exists():
        return False
    try:
        tokens = json.loads(TOKEN_PATH.read_text())
        scope_str = tokens.get("scope", "")
        return "drive.file" in scope_str
    except Exception:
        return False


def revoke() -> None:
    """Revoke the stored token and delete the token file."""
    if TOKEN_PATH.exists():
        try:
            tokens = json.loads(TOKEN_PATH.read_text())
            token = tokens.get("access_token") or tokens.get("refresh_token")
            if token:
                import urllib.request
                import urllib.parse

                url = (
                    "https://oauth2.googleapis.com/revoke?"
                    + urllib.parse.urlencode({"token": token})
                )
                req = urllib.request.Request(url, method="POST")
                try:
                    urllib.request.urlopen(req)
                except Exception:
                    pass
        except Exception:
            pass
        TOKEN_PATH.unlink(missing_ok=True)
