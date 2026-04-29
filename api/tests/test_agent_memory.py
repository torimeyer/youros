"""Tests for agent_memory: weighted facts and configurable re-read cadence."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def mem(tmp_path):
    import services.agent_memory as mod
    with patch.object(mod, "AGENT_MEMORY_DIR", tmp_path):
        yield mod


def test_save_and_get_fact(mem):
    mem.save_memory("agent1", "lang", "Python")
    data = mem.get_memory("agent1")
    assert data["facts"]["lang"]["value"] == "Python"
    assert data["facts"]["lang"]["weight"] == 1.0


def test_save_preserves_weight_on_update(mem):
    mem.save_memory("a", "lang", "Python")
    mem.reinforce_memory("a", "lang", 2.0)
    mem.save_memory("a", "lang", "Go")
    data = mem.get_memory("a")
    assert data["facts"]["lang"]["value"] == "Go"
    assert data["facts"]["lang"]["weight"] == 3.0


def test_reinforce_increases_weight(mem):
    mem.save_memory("a", "k", "v")
    new_w = mem.reinforce_memory("a", "k", 1.5)
    assert new_w == 2.5
    assert mem.get_memory("a")["facts"]["k"]["weight"] == 2.5


def test_penalize_decreases_weight(mem):
    mem.save_memory("a", "k", "v")
    new_w = mem.penalize_memory("a", "k", 0.5)
    assert new_w == 0.5


def test_penalize_floors_at_zero(mem):
    mem.save_memory("a", "k", "v")
    new_w = mem.penalize_memory("a", "k", 999.0)
    assert new_w == 0.0


def test_reinforce_missing_key_returns_none(mem):
    assert mem.reinforce_memory("a", "nokey") is None


def test_penalize_missing_key_returns_none(mem):
    assert mem.penalize_memory("a", "nokey") is None


def test_get_context_sorts_by_weight(mem):
    mem.save_memory("a", "low", "low value")
    mem.save_memory("a", "high", "high value")
    mem.reinforce_memory("a", "high", 5.0)
    ctx = mem.get_context("a")
    assert ctx.index("high value") < ctx.index("low value")


def test_cadence_filters_recently_read(mem):
    mem.save_memory("a", "k", "v")
    mem.set_reread_cadence("a", 24.0)
    ctx1 = mem.get_context("a")
    assert "v" in ctx1
    ctx2 = mem.get_context("a")
    assert "v" not in ctx2


def test_cadence_zero_always_includes(mem):
    mem.save_memory("a", "k", "v")
    mem.set_reread_cadence("a", 0.0)
    ctx1 = mem.get_context("a")
    ctx2 = mem.get_context("a")
    assert "v" in ctx1
    assert "v" in ctx2


def test_set_and_get_reread_cadence(mem):
    mem.set_reread_cadence("a", 12.5)
    assert mem.get_reread_cadence("a") == 12.5


def test_cadence_negative_raises(mem):
    with pytest.raises(ValueError):
        mem.set_reread_cadence("a", -1.0)


def test_backward_compat_string_facts(mem, tmp_path):
    agent_file = tmp_path / "legacy.json"
    agent_file.write_text(json.dumps({
        "facts": {"key1": "old string value"},
        "summaries": [],
    }))
    with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
        data = mem.get_memory("legacy")
        assert data["facts"]["key1"]["value"] == "old string value"
        assert data["facts"]["key1"]["weight"] == 1.0


def test_append_summary(mem):
    mem.append_summary("a", "Did some work")
    data = mem.get_memory("a")
    assert len(data["summaries"]) == 1
    assert data["summaries"][0]["text"] == "Did some work"
    assert data["summaries"][0]["weight"] == 1.0


def test_clear_memory(mem):
    mem.save_memory("a", "k", "v")
    mem.clear_memory("a")
    data = mem.get_memory("a")
    assert data["facts"] == {}


def test_read_count_increments(mem):
    mem.save_memory("a", "k", "v")
    mem.get_context("a")
    assert mem.get_memory("a")["facts"]["k"]["read_count"] == 1
    mem.get_context("a")
    assert mem.get_memory("a")["facts"]["k"]["read_count"] == 2


def test_get_context_empty_returns_empty_string(mem):
    ctx = mem.get_context("nobody")
    assert ctx == ""


def test_new_fact_has_read_count_zero(mem):
    mem.save_memory("a", "k", "v")
    data = mem.get_memory("a")
    assert data["facts"]["k"]["read_count"] == 0
    assert data["facts"]["k"]["last_read_at"] is None
