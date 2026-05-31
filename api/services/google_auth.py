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

from services.atomic_io import atomic_write_text

MYOS_DIR = Path.home() / ".myos"
TOKEN_PATH = MYOS_DIR / "google_token.json"
CREDENTIALS_PATH = MYOS_DIR / "google_credentials.json"
DRIVE_CACHE_DIR = MYOS_DIR / "drive_cache"


def _read_tokens() -> dict:
    """Load the saved Google token, tolerating a corrupt trailing byte.

    google_token.json has been seen with a stray byte after the valid JSON
    object (legacy non-atomic write / truncated concurrent write). Plain
    json.loads rejects that with 'Extra data: ... char N', which used to
    500 every Calendar and Gmail request that needed a token. raw_decode
    parses just the first complete JSON object and ignores trailing bytes;
    on recovery we rewrite the clean object atomically so the file self-heals
    and the error cannot recur. Writes already go through atomic_write_text.
    """
    text = TOKEN_PATH.read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        obj, _idx = json.JSONDecoder().raw_decode(text.lstrip())
        if isinstance(obj, dict):
            try:
                atomic_write_text(TOKEN_PATH, json.dumps(obj))
            except OSError:
                pass
            return obj
        raise

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/presentations.readonly",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


def _ensure_dirs() -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    DRIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def credentials_file_exists() -> bool:
    """Return True if the user has placed credentials at the expected path."""
    return CREDENTIALS_PATH.exists()


def can_start_oauth() -> bool:
    """Return True if we have enough config to start an OAuth flow.

    Accepts either the credentials file OR env vars so machines without
    the manually-placed JSON file can still authenticate via env vars.
    """
    return CREDENTIALS_PATH.exists() or bool(
        os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")
    )


def _load_client_config() -> dict:
    """Read client_id/client_secret from the credentials file or env vars.

    Falls back to GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET env vars when
    the credentials file is missing. This lets the app work on machines
    where the user has set env vars but hasn't manually placed the JSON file.
    """
    if CREDENTIALS_PATH.exists():
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
        return data
    # Fallback: build config from env vars (no credentials file on this machine).
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            f"No credentials file at {CREDENTIALS_PATH} and "
            "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET env vars are not set."
        )
    return {"client_id": client_id, "client_secret": client_secret}


def build_redirect_uri(request) -> str:
    """Return the Google OAuth redirect URI.

    Checks GOOGLE_REDIRECT_URI env var first so work setups with a reverse
    proxy or HTTPS terminator can pin a stable value that matches what is
    registered in Google Cloud Console. Falls back to request.base_url.
    """
    override = os.environ.get("GOOGLE_REDIRECT_URI", "")
    if override:
        return override
    return str(request.base_url).rstrip("/") + "/api/auth/google/callback"


def get_auth_url(state: str, redirect_uri: str) -> str:
    """Build the Google OAuth consent-screen URL."""
    cfg = _load_client_config()
    client_id = cfg["client_id"]
    scope_str = " ".join(SCOPES)
    import urllib.parse

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope_str,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        params
    )


def exchange_code(code: str, redirect_uri: str) -> None:
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
            "redirect_uri": redirect_uri,
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
    # Atomic write so a crash mid-save never leaves a half-written
    # token file that forces Tori to re-auth.
    atomic_write_text(TOKEN_PATH, json.dumps(tokens))
    # Connection state changed: drop every cached status payload so the
    # next poll picks up the new email, scopes, and authed=True state.
    _invalidate_google_status_cache()


def _invalidate_google_status_cache() -> None:
    """Drop cached status payloads for gmail, calendar, and drive.

    Imported lazily so this module has no hard dependency on the router
    cache keys at import time. Safe in all Python import orders.
    """
    try:
        from services import connections_cache

        for key in ("gmail_auth_status", "calendar_auth_status", "drive_auth_status"):
            connections_cache.invalidate(key)
    except Exception:
        # Cache invalidation is best effort. A miss just means the UI
        # picks up the new state a few seconds later when the TTL expires.
        pass


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
        import urllib.error

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

        # Successful refresh clears any previous revoked marker.
        new_tokens.pop("revoked", None)
        # Merge: keep the refresh token (Google doesn't re-issue it every time).
        new_tokens.setdefault("refresh_token", refresh_token)
        # Preserve scope from the original token. Google's refresh response
        # omits scope when it hasn't changed; without this setdefault,
        # has_gmail_scope() falls back to False and shows a spurious
        # "Gmail access needs to be updated" reconnect prompt.
        new_tokens.setdefault("scope", tokens.get("scope", ""))
        expires_in = new_tokens.get("expires_in", 3600)
        new_tokens["expires_at"] = time.time() + int(expires_in)
        atomic_write_text(TOKEN_PATH, json.dumps(new_tokens))
        return new_tokens
    except urllib.error.HTTPError as exc:
        # Read the error body to check for invalid_grant, which means the
        # refresh token itself was revoked by Google. Persist a flag so the
        # status endpoint can report needs_reauth without a network call.
        is_invalid_grant = False
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
            is_invalid_grant = body.get("error") == "invalid_grant"
        except Exception:
            pass
        if is_invalid_grant:
            try:
                revoked = {**tokens, "revoked": True}
                atomic_write_text(TOKEN_PATH, json.dumps(revoked))
                _invalidate_google_status_cache()
            except Exception:
                pass
            raise RuntimeError(
                "invalid_grant: Token has been expired or revoked."
            ) from exc
        return tokens
    except Exception:
        return tokens


def get_credentials() -> dict:
    """Return current tokens (refreshed if needed).

    Raises RuntimeError if the user has not authenticated.
    """
    if not TOKEN_PATH.exists():
        raise RuntimeError("Not authenticated. Connect your Google account first.")
    tokens = _read_tokens()
    return _refresh_if_needed(tokens)


def save_token(tokens: dict) -> None:
    """Persist a token dict to TOKEN_PATH and invalidate connection caches."""
    _ensure_dirs()
    atomic_write_text(TOKEN_PATH, json.dumps(tokens))
    _invalidate_google_status_cache()


def is_authenticated() -> bool:
    """Return True if we have a saved token."""
    return TOKEN_PATH.exists()


def get_email() -> str | None:
    """Return the email address stored in the token, if available."""
    if not TOKEN_PATH.exists():
        return None
    try:
        tokens = _read_tokens()
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


def has_calendar_scope() -> bool:
    """Return True if the stored token includes a Calendar scope."""
    if not TOKEN_PATH.exists():
        return False
    try:
        tokens = _read_tokens()
        scope_str = tokens.get("scope", "")
        return "calendar" in scope_str
    except Exception:
        return False


def has_gmail_scope() -> bool:
    """Return True if the stored token includes a Gmail scope."""
    if not TOKEN_PATH.exists():
        return False
    try:
        tokens = _read_tokens()
        scope_str = tokens.get("scope", "")
        return "gmail" in scope_str
    except Exception:
        return False


def has_write_scope() -> bool:
    """Return True if the stored token includes a Drive write scope.

    Accepts either ``drive.file`` (app-created files only) or the full
    ``drive`` scope (all files). The full scope is required to trash
    files that were not created by this app (e.g. shared documents).

    The token must contain a 'scope' field (Google includes this in the
    token response).  If the field is missing we assume the old token does
    not have write access and the user needs to reconnect.
    """
    if not TOKEN_PATH.exists():
        return False
    try:
        tokens = _read_tokens()
        scope_str = tokens.get("scope", "")
        # Full drive scope grants everything; drive.file is app-only.
        return (
            "https://www.googleapis.com/auth/drive " in scope_str + " "
            or "drive.file" in scope_str
        )
    except Exception:
        return False


def has_full_drive_scope() -> bool:
    """Return True if the token has the full drive scope (not just drive.file).

    The full scope is needed to trash files the app did not create.
    """
    if not TOKEN_PATH.exists():
        return False
    try:
        tokens = _read_tokens()
        scope_str = tokens.get("scope", "")
        # Use word-boundary check: auth/drive must not be followed by .
        # because auth/drive.file and auth/drive.readonly are substrings.
        import re
        return bool(re.search(r"auth/drive(?![.\w])", scope_str))
    except Exception:
        return False


def revoke() -> None:
    """Revoke the stored token and delete the token file."""
    if TOKEN_PATH.exists():
        try:
            tokens = _read_tokens()
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
    # Connection state changed: drop cached status payloads so the next
    # poll sees authed=False right away rather than waiting for TTL.
    _invalidate_google_status_cache()
