"""SSO (Single Sign-On) integration for enterprise mode.

Supports OIDC-compatible identity providers (Okta, Azure AD, Google
Workspace, Auth0). The flow:

1. Admin configures IdP in enterprise settings (issuer URL, client ID, client secret)
2. Users click "Sign in with SSO" and are redirected to the IdP
3. IdP redirects back with an auth code
4. We exchange the code for tokens and extract identity claims
5. A HUMANFILE is auto-created from the claims (name, email, role)

All IdP config lives in ~/.youros/enterprise.json under the "sso" key.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx

from services.atomic_io import atomic_write_text

from services.enterprise_store import _load, _save, MYOS_DIR

SSO_STATES_PATH = MYOS_DIR / "sso_states.json"


def get_sso_config() -> Optional[dict]:
    """Return the SSO configuration or None if not configured."""
    data = _load()
    return data.get("sso")


def configure_sso(
    provider: str,
    issuer_url: str,
    client_id: str,
    client_secret: str,
    allowed_domains: Optional[list] = None,
) -> dict:
    """Save SSO configuration to enterprise settings."""
    data = _load()
    sso = {
        "provider": provider,
        "issuer_url": issuer_url.rstrip("/"),
        "client_id": client_id,
        "client_secret": client_secret,
        "allowed_domains": allowed_domains or [],
        "configured_at": datetime.now(timezone.utc).isoformat(),
    }
    data["sso"] = sso
    _save(data)
    return sso


def remove_sso() -> None:
    """Remove SSO configuration."""
    data = _load()
    data.pop("sso", None)
    _save(data)


def get_auth_url(redirect_uri: str) -> Optional[str]:
    """Build the OIDC authorization URL for the configured IdP."""
    sso = get_sso_config()
    if not sso:
        return None

    state = secrets.token_urlsafe(32)
    _save_state(state, redirect_uri)

    params = {
        "client_id": sso["client_id"],
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": redirect_uri,
        "state": state,
    }

    auth_endpoint = f"{sso['issuer_url']}/v1/authorize"
    return f"{auth_endpoint}?{urlencode(params)}"


async def exchange_code(code: str, redirect_uri: str) -> Optional[dict]:
    """Exchange an auth code for tokens and return user claims.

    Returns a dict with: email, name, groups (if available).
    """
    sso = get_sso_config()
    if not sso:
        return None

    token_endpoint = f"{sso['issuer_url']}/v1/token"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": sso["client_id"],
                "client_secret": sso["client_secret"],
            },
        )

        if resp.status_code != 200:
            return None

        tokens = resp.json()
        id_token = tokens.get("id_token", "")

        # Decode the ID token (we trust the IdP, so we skip signature verification
        # for the MVP. Production would verify the JWT signature.)
        claims = _decode_jwt_claims(id_token)
        if not claims:
            # Fall back to userinfo endpoint
            access_token = tokens.get("access_token", "")
            if access_token:
                userinfo_resp = await client.get(
                    f"{sso['issuer_url']}/v1/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if userinfo_resp.status_code == 200:
                    claims = userinfo_resp.json()

        if not claims:
            return None

        # Check allowed domains
        email = claims.get("email", "")
        if sso.get("allowed_domains"):
            domain = email.split("@")[-1] if "@" in email else ""
            if domain not in sso["allowed_domains"]:
                return None

        return {
            "email": email,
            "name": claims.get("name", ""),
            "given_name": claims.get("given_name", ""),
            "groups": claims.get("groups", []),
            "sub": claims.get("sub", ""),
        }


def _decode_jwt_claims(token: str) -> Optional[dict]:
    """Decode JWT payload without verification (MVP). Returns claims dict."""
    import base64

    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1]
        # Add padding
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def _save_state(state: str, redirect_uri: str) -> None:
    """Save OAuth state for CSRF protection."""
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    states = {}
    if SSO_STATES_PATH.exists():
        try:
            states = json.loads(SSO_STATES_PATH.read_text())
        except Exception:
            pass
    states[state] = {"redirect_uri": redirect_uri, "created_at": datetime.now(timezone.utc).isoformat()}
    atomic_write_text(SSO_STATES_PATH, json.dumps(states))


def validate_state(state: str) -> Optional[str]:
    """Validate and consume an OAuth state. Returns redirect_uri or None."""
    if not SSO_STATES_PATH.exists():
        return None
    try:
        states = json.loads(SSO_STATES_PATH.read_text())
    except Exception:
        return None
    entry = states.pop(state, None)
    atomic_write_text(SSO_STATES_PATH, json.dumps(states))
    return entry.get("redirect_uri") if entry else None
