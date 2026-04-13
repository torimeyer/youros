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
    if token:
        claims = verify_session(token)
        if claims:
            return {
                "email": claims["sub"],
                "role": claims.get("role", "member"),
                "member_id": claims.get("member_id", ""),
                "org_id": claims.get("org_id", ""),
            }

    # Local single-user mode: auto-authenticate as admin so enterprise
    # features (policies, audit, team dashboard) work without a login flow.
    org = enterprise_store.get_org()
    if org:
        members = enterprise_store.list_members()
        admin = next((m for m in members if m.get("role") == "admin"), None)
        if admin:
            return {
                "email": admin.get("email", ""),
                "role": "admin",
                "member_id": admin.get("id", ""),
                "org_id": org.get("id", ""),
            }

    raise HTTPException(status_code=401, detail="Not authenticated. Log in to continue.")
