"""FastAPI dependency for extracting the current user from session cookies.

Returns None in solo mode (no enterprise active) so all existing
endpoints keep working without any login or cookies.

Returns a user dict in enterprise mode when a valid session exists.

Raises 401 if enterprise mode is active but no valid session is present.
"""
from __future__ import annotations

from fastapi import Request, HTTPException
from services.session import verify_session, SESSION_COOKIE_NAME
from services import enterprise_store


def get_current_user(request: Request) -> dict | None:
    """Extract current user from session cookie.

    Returns None in solo mode (no enterprise active).
    Returns user dict in enterprise mode.
    Raises 401 if enterprise mode is active but no valid session.
    """
    if not enterprise_store.is_enterprise():
        return None

    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated. Log in to continue.")

    claims = verify_session(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

    return {
        "email": claims["sub"],
        "role": claims.get("role", "member"),
        "member_id": claims.get("member_id", ""),
        "org_id": claims.get("org_id", ""),
    }
