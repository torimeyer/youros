"""Demo-gated build rule appended to spawned-agent prompts.

When the live demo flag is on, every agent ToriOS spawns should be told to
deliver a feature that actually works in the running app, not just one that
passes tests. Off by default so normal builds are untouched.
"""
import importlib


def _agents_mod():
    return importlib.import_module("routers.agents")


def test_demo_mode_active_via_env(monkeypatch):
    m = _agents_mod()
    monkeypatch.setenv("MYOS_DEMO_MODE", "1")
    assert m._demo_mode_active() is True


def test_demo_mode_inactive_by_default(monkeypatch):
    m = _agents_mod()
    monkeypatch.delenv("MYOS_DEMO_MODE", raising=False)
    # No flag file, no env: must be off.
    monkeypatch.setattr(m.os.path, "exists", lambda p: False)
    assert m._demo_mode_active() is False


def test_demo_mode_active_via_flag_file(monkeypatch):
    m = _agents_mod()
    monkeypatch.delenv("MYOS_DEMO_MODE", raising=False)
    flag = m.os.path.expanduser("~/.myos/.demo_mode")
    monkeypatch.setattr(m.os.path, "exists", lambda p: p == flag)
    assert m._demo_mode_active() is True


def test_demo_build_rule_content():
    m = _agents_mod()
    rule = m._DEMO_BUILD_RULE
    low = rule.lower()
    assert "running app" in low
    assert "verify" in low
    # No new dependency guidance and merge-to-served-branch guidance present.
    assert "dependenc" in low
    assert "merge" in low
    # House style: no em-dashes.
    assert "—" not in rule
