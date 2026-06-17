"""YOUROS_HOME single-root resolver + isolation tests.

The resolver (services.youros_paths) is the one place that decides where
yourOS keeps user data. Setting YOUROS_HOME must redirect the whole profile so
a throwaway test run never touches the real ~/.youros. Resolution is lazy
(reads the env on each call), with back-compat for the older MYOS_* overrides.
"""
from pathlib import Path

import pytest

from services import youros_paths as yp


def test_youros_home_defaults_to_dot_youros(monkeypatch):
    monkeypatch.delenv("YOUROS_HOME", raising=False)
    monkeypatch.delenv("MYOS_DIR", raising=False)
    assert yp.youros_home() == Path.home() / ".youros"


def test_youros_home_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("YOUROS_HOME", str(tmp_path))
    assert yp.youros_home() == tmp_path


def test_youros_home_is_lazy(monkeypatch, tmp_path):
    # Changing the env after import must take effect (no import-time caching).
    monkeypatch.setenv("YOUROS_HOME", str(tmp_path / "a"))
    assert yp.youros_home() == tmp_path / "a"
    monkeypatch.setenv("YOUROS_HOME", str(tmp_path / "b"))
    assert yp.youros_home() == tmp_path / "b"


def test_myos_dir_legacy_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("YOUROS_HOME", raising=False)
    monkeypatch.setenv("MYOS_DIR", str(tmp_path / "legacy"))
    assert yp.youros_home() == tmp_path / "legacy"


def test_youros_home_wins_over_myos_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("YOUROS_HOME", str(tmp_path / "win"))
    monkeypatch.setenv("MYOS_DIR", str(tmp_path / "lose"))
    assert yp.youros_home() == tmp_path / "win"


def test_data_path_joins_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("YOUROS_HOME", str(tmp_path))
    assert yp.data_path("tasks.json") == tmp_path / "tasks.json"
    assert yp.data_path("a", "b") == tmp_path / "a" / "b"


def test_specs_dir_honors_override(monkeypatch, tmp_path):
    monkeypatch.setenv("YOUROS_HOME", str(tmp_path))
    monkeypatch.delenv("MYOS_USER_SPECS_DIR", raising=False)
    monkeypatch.delenv("YOUROS_USER_SPECS_DIR", raising=False)
    assert yp.specs_dir() == tmp_path / "specs"
    monkeypatch.setenv("MYOS_USER_SPECS_DIR", str(tmp_path / "custom-specs"))
    assert yp.specs_dir() == tmp_path / "custom-specs"


def test_drafts_dir_honors_override(monkeypatch, tmp_path):
    monkeypatch.setenv("YOUROS_HOME", str(tmp_path))
    monkeypatch.delenv("MYOS_USER_DRAFTS_DIR", raising=False)
    assert yp.drafts_dir() == tmp_path / "drafts"


def test_migrated_service_redirects_under_youros_home(monkeypatch, tmp_path):
    """A previously-hardcoded service must now resolve its data path under
    YOUROS_HOME (proves the P2 migration actually redirects real writes)."""
    import importlib

    monkeypatch.setenv("YOUROS_HOME", str(tmp_path))
    from services import chat_history_store as chs

    importlib.reload(chs)
    try:
        assert chs.CHAT_HISTORY_PATH == tmp_path / "chat_history.json"
        assert str(chs.CHAT_HISTORY_PATH).startswith(str(tmp_path))
    finally:
        monkeypatch.delenv("YOUROS_HOME", raising=False)
        importlib.reload(chs)
