"""Tests for the user-configurable Files location.

Exercises :mod:`services.files_dir` directly (default resolution,
settings-override, cache invalidation) and verifies that a PUT to
``/settings`` wires a new ``files_dir`` into the resolver.
"""
from pathlib import Path

from fastapi.testclient import TestClient


def _fresh_files_dir():
    """Return a freshly imported ``services.files_dir`` module with its
    cache cleared so the test starts from a known state.
    """
    from services import files_dir as mod
    mod.invalidate_files_dir_cache()
    return mod


def test_get_files_dir_default_when_setting_unset(monkeypatch, tmp_path):
    """When ``files_dir`` is not configured, the default ``~/.myos/files``
    is returned."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    # Patch the settings store to report no configured files_dir.
    import services.settings_store as ss_mod

    class _FakeStore:
        def get(self, key, default=None):
            return None

    monkeypatch.setattr(ss_mod, "settings_store", _FakeStore())

    mod = _fresh_files_dir()
    # The default is resolved against the real user home at module
    # import time. When nothing is set we fall back to that default.
    result = mod.get_files_dir()
    assert result == mod.default_files_dir()


def test_get_files_dir_returns_configured_path(monkeypatch, tmp_path):
    """When the user saves a ``files_dir`` via Settings, ``get_files_dir``
    returns that path.
    """
    configured = tmp_path / "custom-files-location"

    import services.settings_store as ss_mod

    class _FakeStore:
        def get(self, key, default=None):
            return str(configured) if key == "files_dir" else None

    monkeypatch.setattr(ss_mod, "settings_store", _FakeStore())

    mod = _fresh_files_dir()
    assert mod.get_files_dir() == configured


def test_invalidate_files_dir_cache_picks_up_changes(monkeypatch, tmp_path):
    """After ``invalidate_files_dir_cache`` is called, the next
    ``get_files_dir`` call re-reads the settings store.
    """
    import services.settings_store as ss_mod

    value = {"files_dir": None}

    class _MutableStore:
        def get(self, key, default=None):
            return value.get(key, default)

    monkeypatch.setattr(ss_mod, "settings_store", _MutableStore())

    mod = _fresh_files_dir()
    first = mod.get_files_dir()
    assert first == mod.default_files_dir()

    # Simulate a user saving a new files_dir.
    value["files_dir"] = str(tmp_path / "new-location")
    # Without invalidation the cached default is returned.
    assert mod.get_files_dir() == first
    mod.invalidate_files_dir_cache()
    assert mod.get_files_dir() == tmp_path / "new-location"


def test_settings_put_invalidates_files_dir_cache(monkeypatch, tmp_path):
    """A PUT to ``/settings`` that changes ``files_dir`` is reflected
    on the next ``get_files_dir`` call, without a server restart.
    """
    settings_path = tmp_path / "settings.json"
    import services.settings_store as ss_mod

    monkeypatch.setattr(ss_mod, "SETTINGS_PATH", settings_path)
    # Rebuild the store so it writes to the tmp path.
    fresh_store = ss_mod.SettingsStore()
    monkeypatch.setattr(ss_mod, "settings_store", fresh_store)

    # Rebind the settings router's imported name too.
    import routers.settings as settings_router

    monkeypatch.setattr(settings_router, "settings_store", fresh_store)

    from main import app

    client = TestClient(app)

    mod = _fresh_files_dir()
    assert mod.get_files_dir() == mod.default_files_dir()

    new_dir = tmp_path / "user-picked"
    # Load existing settings, add files_dir, PUT back.
    current = client.get("/api/settings").json()
    current["files_dir"] = str(new_dir)
    put = client.put("/api/settings", json=current)
    assert put.status_code == 200

    # The PUT handler invalidates the cache, so the next call returns
    # the configured path.
    assert mod.get_files_dir() == new_dir


def test_existing_test_patches_keep_working():
    """Sanity check: the conftest ``_pin_myos_files_dir_to_tmp`` fixture
    still redirects the agents module's files dir away from the user's
    real home. This protects the historical test pattern where
    ``patch.object(agents_module, "MYOS_FILES_DIR", ...)`` is used.
    """
    from routers import agents as agents_mod

    # The autouse fixture points this at a tmp path, so it must differ
    # from the production default.
    assert agents_mod.MYOS_FILES_DIR != agents_mod._DEFAULT_MYOS_FILES_DIR
    # The ``_files_dir()`` helper honors that override.
    assert agents_mod._files_dir() == agents_mod.MYOS_FILES_DIR
