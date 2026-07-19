"""Regression tests for →2967: the texting-from-phone feature is deleted.

The bridge modules must be gone, their HTTP endpoints must return 404, the
settings schema must no longer carry the opt-in flag, and a user
settings.json that still contains old text_bridge keys must keep loading
cleanly (user data is never migrated or wiped by this removal).
"""
import importlib
import json

import pytest


BRIDGE_MODULES = [
    "services.text_bridge",
    "services.telegram_channel",
    "services.channel_intent_parser",
    "routers.channel_routing",
    "routers.text_bridge",
]


@pytest.mark.parametrize("module_name", BRIDGE_MODULES)
def test_bridge_module_is_gone(module_name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_no_bridge_routes_in_route_table():
    """No /api/channel/* or /api/text-bridge* route is registered at all."""
    from main import app

    bridge_paths = [
        getattr(r, "path", "")
        for r in app.routes
        if getattr(r, "path", "").startswith(("/api/channel", "/api/text-bridge"))
    ]
    assert bridge_paths == []


@pytest.mark.asyncio
async def test_bridge_endpoints_are_gone(client):
    """The old bridge URLs no longer reach any handler.

    GET falls through to a plain 404. POST to a removed path is answered by
    the GET/HEAD-only SPA static mount at "/" with 405; either way there is
    no bridge handler behind these URLs any more (the route-table test above
    pins that)."""
    resp = await client.post("/api/channel/route", json={"action": "status"})
    assert resp.status_code in (404, 405)
    resp = await client.post(
        "/api/channel/inbound", json={"text": "hi", "sender": "+15550001111"}
    )
    assert resp.status_code in (404, 405)
    resp = await client.get("/api/text-bridge/status")
    assert resp.status_code == 404


def test_settings_schema_has_no_bridge_flag():
    from models.schemas import Settings

    assert "inbound_imessage_routing_enabled" not in Settings.model_fields


def test_settings_load_cleanly_with_leftover_bridge_keys(tmp_path, monkeypatch):
    """A settings.json still carrying the old text_bridge block and the old
    inbound_imessage_routing_enabled flag loads without error, keeps the
    leftover keys untouched, and still accepts normal updates."""
    import services.settings_store as ss_mod
    from models.schemas import Settings

    leftover = Settings().model_dump()
    leftover["text_bridge"] = {
        "enabled": False,
        "trusted_contacts": ["+15551234567"],
        "confirm_commands": "always",
    }
    leftover["inbound_imessage_routing_enabled"] = False

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(leftover))
    monkeypatch.setattr(ss_mod, "SETTINGS_PATH", settings_path)

    store = ss_mod.SettingsStore()
    data = store.load()

    assert data["text_bridge"]["trusted_contacts"] == ["+15551234567"]
    assert data["inbound_imessage_routing_enabled"] is False

    # A normal settings write round-trips without disturbing the leftovers.
    store.update({"dark_mode": False})
    data2 = store.load()
    assert data2["dark_mode"] is False
    assert data2["text_bridge"]["trusted_contacts"] == ["+15551234567"]


def test_channel_message_received_constant_stays():
    """The event type name is reserved even though its publisher is gone."""
    from services import event_bus

    assert event_bus.CHANNEL_MESSAGE_RECEIVED == "channel.message_received"
