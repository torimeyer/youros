"""Text yourOS bridge router: manage inbound channel configuration.

Exposes status, configuration, and testing endpoints for the iMessage/Telegram
bridge, as part of task →1874.
"""

from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.settings_store import settings_store
from services.text_bridge import is_trusted_sender, classify_and_dispatch

router = APIRouter(tags=["channels"])


class TextBridgeConfig(BaseModel):
    enabled: Optional[bool] = None
    trusted_contacts: Optional[list[str]] = None
    confirm_commands: Optional[str] = None  # null, "always", "never"


class TestMessage(BaseModel):
    text: str
    sender: str


@router.get("/text-bridge/status")
async def get_status():
    """Return the current bridge status and configuration."""
    config = settings_store.get("text_bridge", {})
    return {
        "enabled": config.get("enabled", False),
        "trusted_contacts": config.get("trusted_contacts", []),
        "confirm_commands": config.get("confirm_commands"),
    }


@router.patch("/text-bridge/config")
async def update_config(body: TextBridgeConfig):
    """Update bridge configuration in SettingsStore."""
    config = settings_store.get("text_bridge", {})
    
    if body.enabled is not None:
        config["enabled"] = body.enabled
    if body.trusted_contacts is not None:
        config["trusted_contacts"] = body.trusted_contacts
    if body.confirm_commands is not None:
        if body.confirm_commands not in (None, "always", "never"):
            raise HTTPException(status_code=400, detail="Invalid confirm_commands value")
        config["confirm_commands"] = body.confirm_commands
        
    settings_store.update({"text_bridge": config})
    return {"ok": True, "config": config}


@router.post("/text-bridge/test")
async def test_message(body: TestMessage):
    """Dry-run classification for a test message."""
    trusted = is_trusted_sender(body.sender)
    if not trusted:
        return {
            "trusted": False,
            "action": "dropped",
            "reply": None
        }
        
    reply = await classify_and_dispatch(body.text, body.sender)
    return {
        "trusted": True,
        "action": "classified",
        "reply": reply
    }
