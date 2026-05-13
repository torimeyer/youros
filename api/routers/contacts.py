"""Contacts endpoints — reads macOS Contacts via Swift/CNContactFetchRequest."""

from __future__ import annotations

import platform

from fastapi import APIRouter, HTTPException, Query

from services import imessage_contacts as contacts_service

router = APIRouter(tags=["contacts"])


def _require_macos():
    if platform.system().lower() != "darwin":
        raise HTTPException(
            status_code=503,
            detail="Contacts are only available on macOS.",
        )


@router.get("/contacts")
async def get_contacts(q: str | None = Query(default=None, description="Filter contacts by name, phone, or email")):
    """Return all contacts from macOS Contacts.app.

    Loads contacts via Swift/CNContactFetchRequest (the native Contacts framework).
    Results are cached for 5 minutes. First call may take ~1 second to compile
    and run the Swift script; subsequent calls within the cache window are instant.

    Contacts stay on this machine — they are never sent to any external service.
    """
    _require_macos()

    try:
        contacts = contacts_service.list_contacts()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not load contacts: {exc}",
        ) from exc

    if q:
        q_lower = q.lower()
        contacts = [
            c for c in contacts
            if q_lower in (c.get("name") or "").lower()
            or any(q_lower in p.lower() for p in c.get("phone_numbers", []))
            or any(q_lower in e.lower() for e in c.get("emails", []))
        ]

    return {"contacts": contacts, "count": len(contacts)}
