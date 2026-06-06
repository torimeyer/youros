"""
Regression tests for →1345: e2e tests must not pollute ~/.myos/settings.json.

The fix: settings_store.py respects the MYOS_HOME environment variable.
When it is set, all reads and writes go to that directory instead of ~/.myos.

These tests prove that:
  1. SETTINGS_PATH is computed from MYOS_HOME when the env var is set.
  2. Writing through SettingsStore with MYOS_HOME set never touches the real
     ~/.myos/settings.json.
  3. Reading through SettingsStore with MYOS_HOME set pulls from the isolated
     directory, not the real one.
  4. The real ~/.myos/settings.json hash is unchanged after a full
     SettingsStore lifecycle (init + update) under MYOS_HOME isolation.
"""

import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    """Return hex digest of path contents, or 'MISSING' if path does not exist."""
    if not path.exists():
        return "MISSING"
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Fixture: isolated MYOS_HOME
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """
    Yield a (fresh_module, isolated_dir) tuple with MYOS_HOME redirected to
    a temporary directory for the duration of the test.

    The module is reloaded so SETTINGS_PATH is recomputed from the new env
    var.  On teardown, MYOS_HOME is removed and the module is reloaded again
    so the global singleton goes back to the real ~/.myos path.
    """
    isolated_dir = tmp_path / "myos-isolated"
    isolated_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("MYOS_HOME", str(isolated_dir))

    import services.settings_store as ss_mod
    importlib.reload(ss_mod)

    yield ss_mod, isolated_dir

    # Teardown: restore the module to the real path.
    monkeypatch.delenv("MYOS_HOME", raising=False)
    importlib.reload(ss_mod)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_settings_path_uses_myos_home(isolated_store):
    """SETTINGS_PATH reflects MYOS_HOME immediately after module reload."""
    ss_mod, isolated_dir = isolated_store
    assert ss_mod.SETTINGS_PATH == isolated_dir / "settings.json", (
        f"Expected {isolated_dir / 'settings.json'}, got {ss_mod.SETTINGS_PATH}"
    )


def test_write_does_not_touch_real_settings(isolated_store):
    """Updating settings under MYOS_HOME isolation never modifies ~/.myos/settings.json."""
    ss_mod, _ = isolated_store

    real_settings = Path.home() / ".myos" / "settings.json"
    before_hash = _sha256(real_settings)

    store = ss_mod.SettingsStore()
    store.update({"os_name": "isolation-test-write"})

    after_hash = _sha256(real_settings)
    assert before_hash == after_hash, (
        f"Real settings.json was modified!\n"
        f"  before={before_hash[:16]}\n"
        f"  after ={after_hash[:16]}\n"
        f"  This is the →1345 regression."
    )


def test_write_goes_to_isolated_dir(isolated_store):
    """Updates land in the isolated directory, not the real home."""
    ss_mod, isolated_dir = isolated_store

    store = ss_mod.SettingsStore()
    store.update({"os_name": "isolation-test-write"})

    isolated_settings = isolated_dir / "settings.json"
    assert isolated_settings.exists(), "settings.json not created in isolated dir"
    data = json.loads(isolated_settings.read_text())
    assert data["os_name"] == "isolation-test-write"


def test_read_comes_from_isolated_dir(isolated_store):
    """Reads pull from the isolated directory, not the real home."""
    ss_mod, isolated_dir = isolated_store

    # Pre-seed the isolated dir with a known value.
    isolated_settings = isolated_dir / "settings.json"
    isolated_settings.write_text(json.dumps({"os_name": "seed-value-only-in-isolated"}))

    store = ss_mod.SettingsStore()
    data = store.load()
    assert data["os_name"] == "seed-value-only-in-isolated"

    # Real settings should have a different (or absent) os_name.
    real_settings = Path.home() / ".myos" / "settings.json"
    if real_settings.exists():
        real_data = json.loads(real_settings.read_text())
        assert real_data.get("os_name") != "seed-value-only-in-isolated", (
            "Real settings.json has the isolated test value — it was not isolated!"
        )


def test_init_does_not_touch_real_settings(isolated_store):
    """SettingsStore() construction (which creates the file) never touches ~/.myos."""
    ss_mod, _ = isolated_store

    real_settings = Path.home() / ".myos" / "settings.json"
    before_hash = _sha256(real_settings)

    # Init creates the isolated settings file if it doesn't exist.
    ss_mod.SettingsStore()

    after_hash = _sha256(real_settings)
    assert before_hash == after_hash, (
        f"Real settings.json changed during SettingsStore init!\n"
        f"  before={before_hash[:16]}\n"
        f"  after ={after_hash[:16]}\n"
        f"  This is the →1345 regression."
    )


def test_default_path_after_teardown(tmp_path, monkeypatch):
    """After removing MYOS_HOME, SETTINGS_PATH goes back to the real ~/.myos path."""
    isolated_dir = tmp_path / "temp-isolated"
    isolated_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("MYOS_HOME", str(isolated_dir))

    import services.settings_store as ss_mod
    importlib.reload(ss_mod)
    assert ss_mod.SETTINGS_PATH == isolated_dir / "settings.json"

    monkeypatch.delenv("MYOS_HOME", raising=False)
    importlib.reload(ss_mod)
    expected = Path.home() / ".myos" / "settings.json"
    assert ss_mod.SETTINGS_PATH == expected, (
        f"After removing MYOS_HOME, expected {expected}, got {ss_mod.SETTINGS_PATH}"
    )
