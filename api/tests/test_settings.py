import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def settings_file(tmp_path):
    """Create a temporary settings file with default values and patch SETTINGS_PATH."""
    sf = tmp_path / "settings.json"
    defaults = {
        "os_name": "ToriOS",
        "dark_mode": True,
        "accent_color": "blue",
        "default_model": "@claude",
        "features": {
            "chat": True, "tasks": True,
            "agents": True, "projects": True, "specs": True,
            "transcripts": False,
        },
        "notifications": {
            "agent_complete": True, "agent_needs_input": True,
            "agent_failed": True, "approval_needed": True,
        },
        "quiet_hours_enabled": True,
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
    }
    sf.write_text(json.dumps(defaults, indent=2))
    return sf


@pytest.mark.asyncio
async def test_get_settings(client, settings_file):
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")

    assert resp.status_code == 200
    data = resp.json()
    assert data["os_name"] == "ToriOS"
    assert data["dark_mode"] is True
    assert data["accent_color"] == "blue"
    assert data["default_model"] == "@claude"


@pytest.mark.asyncio
async def test_settings_defaults_plans_specs_and_imessage_routing(client, settings_file):
    """The two new behavior settings backfill from schema defaults.

    plans_become_specs defaults on (matches the spec-first workflow);
    inbound_imessage_routing_enabled defaults off (acting on incoming
    texts is side-effectful and must be opted into).
    """
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")

    assert resp.status_code == 200
    data = resp.json()
    assert data["plans_become_specs"] is True
    assert data["inbound_imessage_routing_enabled"] is False


@pytest.mark.asyncio
async def test_inbound_imessage_poller_gated_by_setting(monkeypatch, settings_file):
    """schedule_inbound_imessage_routing starts the poller only when the
    setting is on, and never when off (the default)."""
    import main

    calls = {"start": 0}

    class _FakePoller:
        def __init__(self, *args, **kwargs):
            self._cursor = 0

        def start(self):
            calls["start"] += 1
    class _FakeRouter:
        async def handle_inbound_message(self, text):
            pass

    monkeypatch.setattr(
        "services.channel_intent_parser.InboundPoller", _FakePoller, raising=False
    )
    monkeypatch.setattr(
        "routers.channel_routing.build_default_router", lambda: _FakeRouter(), raising=False
    )

    # Default (disabled): no poller is started.
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await main.schedule_inbound_imessage_routing()
    assert calls["start"] == 0

    # Enabled: the poller is constructed and started exactly once.
    data = json.loads(settings_file.read_text())
    data["inbound_imessage_routing_enabled"] = True
    settings_file.write_text(json.dumps(data))
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await main.schedule_inbound_imessage_routing()
    assert calls["start"] == 1


@pytest.mark.asyncio
async def test_put_settings(client, settings_file):
    new_settings = {
        "os_name": "CustomOS",
        "dark_mode": False,
        "accent_color": "red",
    }
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.put("/api/settings", json=new_settings)

    assert resp.status_code == 200
    assert resp.json()["result"] == "saved"

    # Verify persisted to file
    saved = json.loads(settings_file.read_text())
    assert saved["os_name"] == "CustomOS"
    assert saved["dark_mode"] is False
    assert saved["accent_color"] == "red"


@pytest.mark.asyncio
async def test_patch_settings(client, settings_file):
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"accent_color": "green"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "updated"

    # The other fields should remain unchanged
    saved = json.loads(settings_file.read_text())
    assert saved["accent_color"] == "green"
    assert saved["os_name"] == "ToriOS"
    assert saved["dark_mode"] is True


@pytest.mark.asyncio
async def test_default_settings_values(settings_file):
    data = json.loads(settings_file.read_text())
    assert data["os_name"] == "ToriOS"
    assert data["dark_mode"] is True
    assert data["accent_color"] == "blue"
    assert data["default_model"] == "@claude"
    assert data["features"]["chat"] is True
    assert data["features"]["transcripts"] is False
    assert data["notifications"]["agent_complete"] is True
    assert data["quiet_hours_enabled"] is True
    assert data["quiet_hours_start"] == "22:00"
    assert data["quiet_hours_end"] == "07:00"


@pytest.mark.asyncio
async def test_patch_preserves_other_fields(client, settings_file):
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"dark_mode": False})
        resp = await client.get("/api/settings")

    data = resp.json()
    assert data["dark_mode"] is False
    assert data["os_name"] == "ToriOS"
    assert data["quiet_hours_enabled"] is True


# --- Feature key normalization regression tests ---
# These prevent the bug where backend stored lowercase feature keys ("tasks")
# but the frontend looked up TitleCase keys ("Tasks"), causing features to
# appear invisible to the UI.


@pytest.mark.asyncio
async def test_get_settings_normalizes_lowercase_feature_keys(client, settings_file):
    """Lowercase feature keys from old settings files must be normalized to
    TitleCase labels matching the frontend store."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")

    features = resp.json()["features"]
    # The fixture uses lowercase keys. After normalization the API should
    # return TitleCase labels that the frontend expects.
    assert "Tasks" in features
    assert "Chat" in features
    assert "Agents" in features
    assert "Projects" in features
    assert "Specs" in features
    assert "Transcripts" in features
    # Old lowercase keys should NOT appear in the response
    assert "tasks" not in features
    assert "chat" not in features


@pytest.mark.asyncio
async def test_patch_features_normalizes_lowercase_keys(client, settings_file):
    """Patching features with lowercase keys should normalize them to TitleCase."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={
            "features": {"tasks": False, "chat": True}
        })
        resp = await client.get("/api/settings")

    features = resp.json()["features"]
    assert features["Tasks"] is False
    assert features["Chat"] is True
    assert "tasks" not in features
    assert "chat" not in features


@pytest.mark.asyncio
async def test_patch_features_preserves_titlecase_keys(client, settings_file):
    """Features saved with TitleCase keys (from the UI) should be preserved."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={
            "features": {"Tasks": False, "Chat": True, "Transcripts": True}
        })
        resp = await client.get("/api/settings")

    features = resp.json()["features"]
    assert features["Tasks"] is False
    assert features["Chat"] is True
    assert features["Transcripts"] is True


@pytest.mark.asyncio
async def test_feature_values_survive_roundtrip(client, settings_file):
    """A feature disabled via the API must still be disabled when read back.
    This is the core regression test: before the fix, the frontend would
    look up 'Tasks' but the backend stored 'tasks', so a disabled feature
    appeared enabled."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        # Simulate the frontend disabling Tasks
        await client.patch("/api/settings", json={
            "features": {"Tasks": False}
        })
        resp = await client.get("/api/settings")

    features = resp.json()["features"]
    assert features["Tasks"] is False

    # Now simulate the old-style API writing lowercase keys
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        saved = json.loads(settings_file.read_text())
        saved["features"] = {"tasks": False, "chat": True, "transcripts": False}
        settings_file.write_text(json.dumps(saved))
        resp = await client.get("/api/settings")

    features = resp.json()["features"]
    assert features["Tasks"] is False
    assert features["Chat"] is True
    assert features["Transcripts"] is False


@pytest.mark.asyncio
async def test_default_settings_schema_uses_titlecase_feature_keys():
    """The Settings schema defaults must use TitleCase keys matching the UI."""
    from models.schemas import Settings
    defaults = Settings()
    assert "Tasks" in defaults.features
    assert "Chat" in defaults.features
    assert "tasks" not in defaults.features
    assert "chat" not in defaults.features


# --- Default LLM selection tests ---


@pytest.mark.asyncio
async def test_default_model_can_be_changed_to_gemini(client, settings_file):
    """Users can set their default chat AI to Gemini via PATCH."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"default_model": "@gemini"})
        assert resp.status_code == 200

        resp = await client.get("/api/settings")
        assert resp.json()["default_model"] == "@gemini"


@pytest.mark.asyncio
async def test_default_model_can_be_changed_to_claude(client, settings_file):
    """Users can set their default chat AI to Claude via PATCH."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        # First change to gemini
        await client.patch("/api/settings", json={"default_model": "@gemini"})
        # Then change back to claude
        resp = await client.patch("/api/settings", json={"default_model": "@claude"})
        assert resp.status_code == 200

        resp = await client.get("/api/settings")
        assert resp.json()["default_model"] == "@claude"


@pytest.mark.asyncio
async def test_default_model_persists_alongside_other_settings(client, settings_file):
    """Changing default_model should not affect other settings."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"default_model": "@gemini"})
        resp = await client.get("/api/settings")

    data = resp.json()
    assert data["default_model"] == "@gemini"
    assert data["os_name"] == "ToriOS"
    assert data["dark_mode"] is True


# --- Server side user state persistence tests ---
# These lock in the fix where the server, not localStorage, is the
# source of truth for onboarding and other user preferences. If a user
# finishes onboarding, they should never be asked to do it again.


@pytest.mark.asyncio
async def test_patch_persists_onboarded_true(client, settings_file):
    """PATCH /api/settings with onboarded=true should persist the field."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"onboarded": True})
        assert resp.status_code == 200

        resp = await client.get("/api/settings")

    data = resp.json()
    assert data["onboarded"] is True
    # Verify on disk too
    saved = json.loads(settings_file.read_text())
    assert saved["onboarded"] is True


@pytest.mark.asyncio
async def test_get_settings_returns_new_user_state_fields_after_patch(client, settings_file):
    """GET /api/settings returns the new user state fields once they
    have been written via PATCH. This is the normal flow the frontend
    uses: hydrate -> patch missing fields -> hydrate again."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={
            "onboarded": True,
            "accent_color": "purple",
            "default_model": "@gemini",
            "use_ostk_terms": True,
        })
        resp = await client.get("/api/settings")

    data = resp.json()
    assert data["onboarded"] is True
    assert data["accent_color"] == "purple"
    assert data["default_model"] == "@gemini"
    assert data["use_ostk_terms"] is True


@pytest.mark.asyncio
async def test_fresh_settings_file_includes_onboarded(tmp_path):
    """A freshly created settings.json should include onboarded=false
    so the Settings pydantic defaults reach disk."""
    fresh_path = tmp_path / "settings.json"
    # Reimport to get a new SettingsStore pointed at the temp path
    with patch("services.settings_store.SETTINGS_PATH", fresh_path):
        from services.settings_store import SettingsStore
        SettingsStore()  # triggers _ensure_exists
        data = json.loads(fresh_path.read_text())

    assert data["onboarded"] is False
    assert data["use_ostk_terms"] is False
    assert data["accent_color"] == "blue"
    assert data["default_model"] == "@claude"


@pytest.mark.asyncio
async def test_default_settings_include_onboarded_false():
    """The Settings schema must default onboarded to False so new users
    see the onboarding wizard the first time."""
    from models.schemas import Settings
    defaults = Settings()
    assert defaults.onboarded is False
    assert defaults.use_ostk_terms is False
    assert defaults.accent_color == "blue"
    assert defaults.default_model == "@claude"


@pytest.mark.asyncio
async def test_patch_onboarded_then_accent_preserves_both(client, settings_file):
    """Patching onboarded and then accent_color should preserve both values.
    This catches the bug where one setter would wipe out another."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"onboarded": True})
        await client.patch("/api/settings", json={"accent_color": "purple"})
        resp = await client.get("/api/settings")

    data = resp.json()
    assert data["onboarded"] is True
    assert data["accent_color"] == "purple"


@pytest.mark.asyncio
async def test_patch_use_ostk_terms_persists(client, settings_file):
    """Toggling ostk terms should persist on the server."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"use_ostk_terms": True})
        resp = await client.get("/api/settings")

    assert resp.json()["use_ostk_terms"] is True


# --- Tour, What's New, and custom agent templates persistence ---
# These prevent the regression where finishing the tour, dismissing the
# What's New panel, or building custom agent templates would silently
# get wiped if the user cleared their browser cache or signed in on a
# new device.


@pytest.mark.asyncio
async def test_patch_tour_complete_persists(client, settings_file):
    """Finishing or skipping the guided tour writes to the server."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"tour_complete": True})
        resp = await client.get("/api/settings")

    assert resp.json()["tour_complete"] is True
    saved = json.loads(settings_file.read_text())
    assert saved["tour_complete"] is True


@pytest.mark.asyncio
async def test_patch_whats_new_last_seen_persists(client, settings_file):
    """The last release notes date the user saw is server backed."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"whats_new_last_seen": "2026-04-05"})
        resp = await client.get("/api/settings")

    assert resp.json()["whats_new_last_seen"] == "2026-04-05"


@pytest.mark.asyncio
async def test_patch_custom_agent_templates_persists(client, settings_file):
    """Custom agent templates are saved server side, not in localStorage."""
    templates = [
        {
            "name": "Researcher",
            "description": "Searches the web",
            "icon": "search",
            "model": "sonnet",
            "budget": 2.0,
        }
    ]
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"custom_agent_templates": templates})
        resp = await client.get("/api/settings")

    assert resp.json()["custom_agent_templates"] == templates


@pytest.mark.asyncio
async def test_new_user_state_fields_survive_store_restart(tmp_path):
    """All the new server backed fields must be readable after a fresh
    SettingsStore is constructed against the same file. This is the
    closest thing we have to "the API process restarted but the data
    is still there"."""
    sf = tmp_path / "settings.json"
    with patch("services.settings_store.SETTINGS_PATH", sf):
        from services.settings_store import SettingsStore
        store_a = SettingsStore()
        store_a.update(
            {
                "tour_complete": True,
                "whats_new_last_seen": "2026-04-05",
                "custom_agent_templates": [
                    {
                        "name": "Helper",
                        "description": "Does things",
                        "icon": "star",
                        "model": "sonnet",
                        "budget": 1.0,
                    }
                ],
                "onboarded": True,
                "os_name": "Tori",
            }
        )

        # Simulate a restart by constructing a new store against the same file.
        store_b = SettingsStore()
        data = store_b.load()

    assert data["tour_complete"] is True
    assert data["whats_new_last_seen"] == "2026-04-05"
    assert data["custom_agent_templates"][0]["name"] == "Helper"
    assert data["onboarded"] is True
    assert data["os_name"] == "Tori"


@pytest.mark.asyncio
async def test_default_settings_include_new_user_state_fields():
    """The Settings schema defaults must include the new server backed
    fields so a brand new settings.json has them present."""
    from models.schemas import Settings
    defaults = Settings()
    assert defaults.tour_complete is False
    assert defaults.whats_new_last_seen == ""
    assert defaults.custom_agent_templates == []


@pytest.mark.asyncio
async def test_get_settings_backfills_missing_new_fields_for_old_files(tmp_path):
    """An existing settings.json from before today's new fields were
    added must still surface those fields on GET so the frontend never
    sees ``undefined``. We simulate that by writing a settings file that
    only has the old fields, then assert the backfilled defaults come
    back on load.
    """
    sf = tmp_path / "settings.json"
    # Deliberately minimal, no tour_complete, no auto_label_tasks, etc.
    sf.write_text(json.dumps({
        "os_name": "myOS",
        "onboarded": True,
        "accent_color": "blue",
    }))

    with patch("services.settings_store.SETTINGS_PATH", sf):
        from services.settings_store import SettingsStore
        data = SettingsStore().load()

    # Every new server backed field should now be present with its
    # schema default even though the file on disk did not have it.
    assert data["tour_complete"] is False
    assert data["whats_new_last_seen"] == ""
    assert data["custom_agent_templates"] == []
    assert data["auto_label_tasks"] is True
    assert data["auto_template_matching"] is True
    assert data["chat_backend_preference"] == "auto"
    # Appearance settings should be backfilled with defaults.
    assert data["sidebar_position"] == "left"
    assert data["compact_mode"] is False
    assert data["font_size"] == "medium"
    assert data["icon_style"] == "filled"
    assert data["card_style"] == "glass"
    assert data["dashboard_layout"] == "full"
    assert data["status_dot_style"] == "dots"
    assert data["greeting_style"] == "time"
    # Pre-existing fields are untouched.
    assert data["os_name"] == "myOS"
    assert data["onboarded"] is True


# --- Appearance settings tests ---
# These verify the 8 new appearance fields round-trip through the API
# and persist correctly to disk. Needle 340.


@pytest.mark.asyncio
async def test_appearance_settings_schema_defaults():
    """The Settings schema must include the 8 appearance fields with correct defaults."""
    from models.schemas import Settings
    defaults = Settings()
    assert defaults.sidebar_position == "left"
    assert defaults.compact_mode is False
    assert defaults.font_size == "medium"
    assert defaults.icon_style == "filled"
    assert defaults.card_style == "glass"
    assert defaults.dashboard_layout == "full"
    assert defaults.status_dot_style == "dots"
    assert defaults.greeting_style == "time"


@pytest.mark.asyncio
async def test_patch_sidebar_position(client, settings_file):
    """Patching sidebar_position should persist and be readable."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"sidebar_position": "right"})
        assert resp.status_code == 200
        resp = await client.get("/api/settings")
    assert resp.json()["sidebar_position"] == "right"


@pytest.mark.asyncio
async def test_patch_compact_mode(client, settings_file):
    """Patching compact_mode should persist and be readable."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"compact_mode": True})
        assert resp.status_code == 200
        resp = await client.get("/api/settings")
    assert resp.json()["compact_mode"] is True


@pytest.mark.asyncio
async def test_patch_font_size(client, settings_file):
    """Patching font_size should persist and be readable."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"font_size": "large"})
        assert resp.status_code == 200
        resp = await client.get("/api/settings")
    assert resp.json()["font_size"] == "large"


@pytest.mark.asyncio
async def test_patch_icon_style(client, settings_file):
    """Patching icon_style should persist and be readable."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"icon_style": "outlined"})
        assert resp.status_code == 200
        resp = await client.get("/api/settings")
    assert resp.json()["icon_style"] == "outlined"


@pytest.mark.asyncio
async def test_patch_card_style(client, settings_file):
    """Patching card_style should persist and be readable."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"card_style": "solid"})
        assert resp.status_code == 200
        resp = await client.get("/api/settings")
    assert resp.json()["card_style"] == "solid"


@pytest.mark.asyncio
async def test_patch_dashboard_layout(client, settings_file):
    """Patching dashboard_layout should persist and be readable."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"dashboard_layout": "focus"})
        assert resp.status_code == 200
        resp = await client.get("/api/settings")
    assert resp.json()["dashboard_layout"] == "focus"


@pytest.mark.asyncio
async def test_patch_status_dot_style(client, settings_file):
    """Patching status_dot_style should persist and be readable."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"status_dot_style": "badges"})
        assert resp.status_code == 200
        resp = await client.get("/api/settings")
    assert resp.json()["status_dot_style"] == "badges"


@pytest.mark.asyncio
async def test_patch_greeting_style(client, settings_file):
    """Patching greeting_style should persist and be readable."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"greeting_style": "quote"})
        assert resp.status_code == 200
        resp = await client.get("/api/settings")
    assert resp.json()["greeting_style"] == "quote"


@pytest.mark.asyncio
async def test_patch_appearance_preserves_other_settings(client, settings_file):
    """Patching appearance settings should not affect other settings."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={
            "sidebar_position": "right",
            "compact_mode": True,
            "font_size": "small",
            "icon_style": "outlined",
            "card_style": "solid",
            "dashboard_layout": "focus",
            "status_dot_style": "badges",
            "greeting_style": "none",
        })
        resp = await client.get("/api/settings")
    data = resp.json()
    # Appearance settings updated
    assert data["sidebar_position"] == "right"
    assert data["compact_mode"] is True
    assert data["font_size"] == "small"
    assert data["icon_style"] == "outlined"
    assert data["card_style"] == "solid"
    assert data["dashboard_layout"] == "focus"
    assert data["status_dot_style"] == "badges"
    assert data["greeting_style"] == "none"
    # Original settings still intact
    assert data["os_name"] == "ToriOS"
    assert data["dark_mode"] is True
    assert data["accent_color"] == "blue"


@pytest.mark.asyncio
async def test_appearance_settings_survive_store_restart(tmp_path):
    """Appearance settings should persist across SettingsStore restarts."""
    sf = tmp_path / "settings.json"
    with patch("services.settings_store.SETTINGS_PATH", sf):
        from services.settings_store import SettingsStore
        store_a = SettingsStore()
        store_a.update({
            "sidebar_position": "right",
            "compact_mode": True,
            "font_size": "large",
            "icon_style": "outlined",
            "card_style": "solid",
            "dashboard_layout": "focus",
            "status_dot_style": "badges",
            "greeting_style": "quote",
        })
        store_b = SettingsStore()
        data = store_b.load()
    assert data["sidebar_position"] == "right"
    assert data["compact_mode"] is True
    assert data["font_size"] == "large"
    assert data["icon_style"] == "outlined"
    assert data["card_style"] == "solid"
    assert data["dashboard_layout"] == "focus"
    assert data["status_dot_style"] == "badges"
    assert data["greeting_style"] == "quote"


# --- System feature defaults tests ---
# Needle: every non-debug system feature must default to True so new users
# see the full feature set without any manual setup.

# Keys that are intentionally excluded from the "must be True" check:
# none at this time. All features in the schema are user-visible and should
# default on.
_DEBUG_FEATURE_KEYS: set[str] = set()


@pytest.mark.asyncio
async def test_all_non_debug_feature_defaults_are_true():
    """Every system feature in the Settings schema must default to True.

    This prevents a regression where a new feature is added with a False
    default and new users never see it turned on. Debug/staging-only keys
    listed in _DEBUG_FEATURE_KEYS are exempt.
    """
    from models.schemas import Settings
    defaults = Settings()
    for key, value in defaults.features.items():
        if key in _DEBUG_FEATURE_KEYS:
            continue
        assert value is True, (
            f"Feature '{key}' defaults to False. "
            "All user-visible features must default to True. "
            "If this is a debug/staging-only flag, add it to _DEBUG_FEATURE_KEYS."
        )


@pytest.mark.asyncio
async def test_user_set_feature_false_preserved_on_load(client, tmp_path):
    """A user who intentionally turned a feature off after the all-features-on
    migration keeps it off after load.

    Once ``features_default_on_migrated`` is set to True, subsequent loads
    must never flip user-disabled features back on.
    """
    sf = tmp_path / "settings.json"
    # Write a settings file where the user has explicitly disabled Automations
    # AFTER the one-shot migration has already run.
    sf.write_text(json.dumps({
        "os_name": "myOS",
        "onboarded": True,
        "features_default_on_migrated": True,
        "features": {
            "Chat": True,
            "Tasks": True,
            "Automations": False,
        },
    }))

    with patch("services.settings_store.SETTINGS_PATH", sf):
        resp = await client.get("/api/settings")

    assert resp.status_code == 200
    features = resp.json()["features"]
    # Automations was explicitly set False by the user after migration; it must stay False.
    assert features["Automations"] is False
    # Other features that were absent from the file should be backfilled as True.
    assert features["Chat"] is True
    assert features["Tasks"] is True


@pytest.mark.asyncio
async def test_feature_defaults_all_true():
    """Every feature in the Settings schema must default to True so new
    users see every system feature enabled out of the box."""
    from models.schemas import Settings
    defaults = Settings()
    assert defaults.features, "Settings.features defaults must not be empty"
    for key, value in defaults.features.items():
        assert value is True, (
            f"Feature '{key}' defaults to {value}. All system features "
            "must default to True."
        )


@pytest.mark.asyncio
async def test_settings_load_migrates_missing_or_false_defaults_to_true_first_time(
    client, tmp_path
):
    """First load after the all-features-on rule must promote any
    historically default-False feature (Automations) to True and backfill
    missing feature keys with True. The migration runs exactly once and
    records a flag so future user toggles are preserved.
    """
    sf = tmp_path / "settings.json"
    # Pre-migration settings file: no migration flag, Automations is False
    # because the old frontend default wrote it that way, and most feature
    # keys are missing entirely.
    sf.write_text(json.dumps({
        "os_name": "myOS",
        "onboarded": True,
        "features": {
            "Chat": True,
            "Automations": False,
        },
    }))

    with patch("services.settings_store.SETTINGS_PATH", sf):
        resp = await client.get("/api/settings")
        features = resp.json()["features"]

        # Historically default-False Automations is promoted to True on first load.
        assert features["Automations"] is True
        # Every schema feature is now present and True for this user.
        for key in ("Chat", "Tasks", "Agents", "Projects", "Specs", "Cost Tracking"):
            assert features[key] is True, f"{key} should be True after migration"

        # The migration flag is recorded on disk so this never runs again.
        saved = json.loads(sf.read_text())
        assert saved.get("features_default_on_migrated") is True
        assert saved["features"]["Automations"] is True

        # Second load: a user toggle that sets Automations False AFTER the
        # migration must be preserved, not flipped back to True.
        await client.patch(
            "/api/settings", json={"features": {"Automations": False}}
        )
        resp2 = await client.get("/api/settings")
        features2 = resp2.json()["features"]
        assert features2["Automations"] is False


# --- Standing instructions ---


@pytest.mark.asyncio
async def test_settings_stores_standing_instructions(client, settings_file):
    """PATCH /api/settings accepts standing_instructions and GET returns it.

    The Usage page links "Save standing instructions to raise this" to
    ``/settings#standing-instructions``. The Settings page stores that
    free-form block on the generic settings store so every chat and
    agent spawn can pick it up. An empty default keeps new users clean.
    """
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        # Empty default for users who have not written anything.
        assert resp.json().get("standing_instructions", "") == ""

        resp = await client.patch(
            "/api/settings",
            json={"standing_instructions": "Always reply in plain language."},
        )
        assert resp.status_code == 200

        resp = await client.get("/api/settings")
        assert resp.json()["standing_instructions"] == "Always reply in plain language."


# --- instance_name tests ---
# The instance_name field names this specific installation of the product.
# The product is yourOS. A user who never changes it sees "yourOS". A user who
# renames their copy sees whatever they pick. Tori's copy is "toriOS".


@pytest.mark.asyncio
async def test_instance_name_default_is_youros(tmp_path):
    """A fresh settings file reports instance_name as the product default."""
    sf = tmp_path / "settings.json"
    with patch("services.settings_store.SETTINGS_PATH", sf):
        from services.settings_store import SettingsStore
        store = SettingsStore()
        data = store.load()
        assert data["instance_name"] == "yourOS"


@pytest.mark.asyncio
async def test_get_settings_returns_instance_name(client, settings_file):
    """GET /api/settings includes instance_name in the response."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    # Default from the Pydantic schema since the fixture does not set it.
    assert data["instance_name"] == "yourOS"


@pytest.mark.asyncio
async def test_patch_instance_name_persists(client, settings_file):
    """PATCH /api/settings with instance_name writes through and survives GET."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch(
            "/api/settings", json={"instance_name": "toriOS"}
        )
        assert resp.status_code == 200
        resp = await client.get("/api/settings")
    assert resp.json()["instance_name"] == "toriOS"
    # Other fields remain intact.
    saved = json.loads(settings_file.read_text())
    assert saved["instance_name"] == "toriOS"
    assert saved["os_name"] == "ToriOS"


@pytest.mark.asyncio
async def test_patch_instance_name_back_to_default(client, settings_file):
    """The user can reset instance_name back to the product name."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"instance_name": "scottOS"})
        await client.patch("/api/settings", json={"instance_name": "myOS"})
        resp = await client.get("/api/settings")
    assert resp.json()["instance_name"] == "myOS"


# --- onboarding_step persistence tests ---
# The wizard saves stepIndex here before any OAuth redirect so the browser
# can land back on the right step. Cleared immediately after consume.


@pytest.mark.asyncio
async def test_patch_onboarding_step_round_trips(client, settings_file):
    """PATCH /api/settings with onboarding_step=8 should be readable via GET."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch("/api/settings", json={"onboarding_step": 8})
        assert resp.status_code == 200

        resp = await client.get("/api/settings")

    assert resp.json()["onboarding_step"] == 8
    saved = json.loads(settings_file.read_text())
    assert saved["onboarding_step"] == 8


@pytest.mark.asyncio
async def test_patch_onboarding_step_null_clears_value(client, settings_file):
    """PATCHing onboarding_step to null after it was set should clear it."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        await client.patch("/api/settings", json={"onboarding_step": 5})
        resp = await client.patch("/api/settings", json={"onboarding_step": None})
        assert resp.status_code == 200

        resp = await client.get("/api/settings")

    assert resp.json()["onboarding_step"] is None


# --- os_name default tests (→2004) ---


def test_os_name_schema_default_is_yourOS():
    """Settings schema must default os_name to 'yourOS', not 'myOS'."""
    from models.schemas import Settings
    assert Settings().os_name == "yourOS"


@pytest.mark.asyncio
async def test_os_name_defaults_to_yourOS_when_absent_from_file(client, tmp_path):
    """When os_name is missing from the settings file, GET /api/settings must
    backfill it with 'yourOS' — the canonical product default."""
    sf = tmp_path / "settings.json"
    sf.write_text(json.dumps({"onboarded": True, "dark_mode": False}))

    with patch("services.settings_store.SETTINGS_PATH", sf):
        resp = await client.get("/api/settings")

    assert resp.status_code == 200
    assert resp.json()["os_name"] == "yourOS"


# --- API key masking (→2684) ---
# GET /api/settings used to return the raw settings file, including the
# full Anthropic API key, to the browser. Any page script, extension, or
# captured log could read it. These tests lock in the fix: secret-shaped
# fields are masked in the response (last 4 characters only) and treated
# as write-only on PATCH/PUT so a masked or empty value coming back in
# never overwrites the stored one.

_REAL_KEY = "sk-ant-api03-verylongsecretvalue-Ab12Cd34"


@pytest.mark.asyncio
async def test_get_settings_masks_anthropic_api_key(client, settings_file):
    """The response shows only the last 4 characters, never the full key."""
    data = json.loads(settings_file.read_text())
    data["anthropic_api_key"] = _REAL_KEY
    settings_file.write_text(json.dumps(data))

    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")

    assert resp.status_code == 200
    body = resp.json()
    assert body["anthropic_api_key"] == "****Cd34"
    assert body["has_anthropic_api_key"] is True
    # The full key must not appear anywhere in the response.
    assert _REAL_KEY not in json.dumps(body)


@pytest.mark.asyncio
async def test_get_settings_has_flag_false_when_no_key_saved(client, settings_file):
    """Without a saved key the has-flag is False so the UI can say 'not set'."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")

    assert resp.status_code == 200
    assert resp.json()["has_anthropic_api_key"] is False


@pytest.mark.asyncio
async def test_get_settings_masks_other_secret_shaped_fields(client, settings_file):
    """Every field whose name looks like a key, token, or secret is masked,
    including ones nested inside dicts and lists."""
    data = json.loads(settings_file.read_text())
    data["gemini_api_key"] = "AIzaSyFakeGeminiKeyValue-Wx89"
    data["telegram"] = {"token": "123456:AAFakeTelegramTokenValue", "chat_id": "42"}
    data["mcp_servers"] = [
        {"name": "Slack", "url": "https://mcp.example.com", "auth_token": "tok-secret-value-Zz77"},
    ]
    settings_file.write_text(json.dumps(data))

    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")

    body = resp.json()
    assert body["gemini_api_key"] == "****Wx89"
    assert body["has_gemini_api_key"] is True
    assert body["telegram"]["token"].startswith("****")
    assert "AAFakeTelegramTokenValue" not in json.dumps(body)
    assert body["mcp_servers"][0]["auth_token"] == "****Zz77"
    assert "tok-secret-value-Zz77" not in json.dumps(body)
    # Non-secret fields are untouched.
    assert body["telegram"]["chat_id"] == "42"
    assert body["mcp_servers"][0]["name"] == "Slack"


@pytest.mark.asyncio
async def test_get_settings_empty_secret_stays_empty(client, settings_file):
    """An empty stored secret is returned as-is with a False has-flag."""
    data = json.loads(settings_file.read_text())
    data["gemini_oauth_access_token"] = ""
    settings_file.write_text(json.dumps(data))

    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.get("/api/settings")

    body = resp.json()
    assert body["gemini_oauth_access_token"] == ""
    assert body["has_gemini_oauth_access_token"] is False


@pytest.mark.asyncio
async def test_patch_new_anthropic_key_saves(client, settings_file):
    """A genuinely new full key value saves to disk as before."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch(
            "/api/settings", json={"anthropic_api_key": _REAL_KEY}
        )
        assert resp.status_code == 200

    saved = json.loads(settings_file.read_text())
    assert saved["anthropic_api_key"] == _REAL_KEY


@pytest.mark.asyncio
async def test_patch_masked_value_leaves_stored_key_untouched(client, settings_file):
    """A masked value echoed back in a PATCH must not overwrite the real key."""
    data = json.loads(settings_file.read_text())
    data["anthropic_api_key"] = _REAL_KEY
    settings_file.write_text(json.dumps(data))

    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch(
            "/api/settings", json={"anthropic_api_key": "****Cd34", "dark_mode": False}
        )
        assert resp.status_code == 200

    saved = json.loads(settings_file.read_text())
    assert saved["anthropic_api_key"] == _REAL_KEY
    # Other fields in the same PATCH still apply.
    assert saved["dark_mode"] is False


@pytest.mark.asyncio
async def test_patch_empty_string_leaves_stored_key_untouched(client, settings_file):
    """An empty string in a PATCH must not wipe the stored key."""
    data = json.loads(settings_file.read_text())
    data["anthropic_api_key"] = _REAL_KEY
    settings_file.write_text(json.dumps(data))

    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch(
            "/api/settings", json={"anthropic_api_key": ""}
        )
        assert resp.status_code == 200

    saved = json.loads(settings_file.read_text())
    assert saved["anthropic_api_key"] == _REAL_KEY


@pytest.mark.asyncio
async def test_put_masked_value_leaves_stored_key_untouched(client, settings_file):
    """PUT gets the same write-only treatment as PATCH."""
    data = json.loads(settings_file.read_text())
    data["anthropic_api_key"] = _REAL_KEY
    settings_file.write_text(json.dumps(data))

    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.put(
            "/api/settings", json={"anthropic_api_key": "****Cd34", "accent_color": "red"}
        )
        assert resp.status_code == 200

    saved = json.loads(settings_file.read_text())
    assert saved["anthropic_api_key"] == _REAL_KEY
    assert saved["accent_color"] == "red"


@pytest.mark.asyncio
async def test_patch_has_flag_is_never_persisted(client, settings_file):
    """A client echoing the whole GET response back must not write the
    response-only has_ flags into the settings file."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch(
            "/api/settings", json={"has_anthropic_api_key": True, "dark_mode": False}
        )
        assert resp.status_code == 200

    saved = json.loads(settings_file.read_text())
    assert "has_anthropic_api_key" not in saved
    assert saved["dark_mode"] is False


@pytest.mark.asyncio
async def test_patch_mcp_servers_with_masked_token_keeps_stored_token(client, settings_file):
    """The MCP installer reads mcp_servers from GET /settings and patches the
    whole list back. A masked auth_token in that round trip must be replaced
    with the stored real token, not saved as the mask."""
    data = json.loads(settings_file.read_text())
    data["mcp_servers"] = [
        {"name": "Slack", "url": "https://mcp.example.com", "auth_token": "tok-secret-value-Zz77", "enabled": True},
    ]
    settings_file.write_text(json.dumps(data))

    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        # Echo the masked entry back with a changed non-secret field, the
        # way the installer's enable/disable toggle does.
        resp = await client.patch(
            "/api/settings",
            json={"mcp_servers": [
                {"name": "Slack", "url": "https://mcp.example.com", "auth_token": "****Zz77", "enabled": False},
            ]},
        )
        assert resp.status_code == 200

    saved = json.loads(settings_file.read_text())
    assert saved["mcp_servers"][0]["auth_token"] == "tok-secret-value-Zz77"
    assert saved["mcp_servers"][0]["enabled"] is False


@pytest.mark.asyncio
async def test_patch_mcp_servers_new_entry_with_real_token_saves(client, settings_file):
    """Adding a brand new server with a real token still saves normally."""
    with patch("services.settings_store.SETTINGS_PATH", settings_file):
        resp = await client.patch(
            "/api/settings",
            json={"mcp_servers": [
                {"name": "Linear", "url": "https://linear.example.com", "auth_token": "tok-new-real-Qq55", "enabled": True},
            ]},
        )
        assert resp.status_code == 200

    saved = json.loads(settings_file.read_text())
    assert saved["mcp_servers"][0]["auth_token"] == "tok-new-real-Qq55"
