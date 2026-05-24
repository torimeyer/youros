"""Regression tests for →1192 — O(1) name-index lookup in _find_freshest_matching_jsonl.

Before the fix, resolving 162 agents against 826 candidates ran 1.6 M string
comparisons every 30 s in asyncio.to_thread, holding the GIL and deadlocking
health probes. After the fix, _load_candidates builds an inverted name→path
index so each agent is resolved in O(1) rather than O(candidates).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.agents import (
    _extract_agent_name,
    _find_freshest_matching_jsonl,
    _load_candidates,
    _reset_candidates_cache,
    _candidates_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jsonl(tmp_path: Path, name: str, first_line: str) -> Path:
    """Write a one-line JSONL file and return its path."""
    p = tmp_path / f"agent-{name}.jsonl"
    p.write_text(first_line + "\n", encoding="utf-8")
    return p


def _register_line(name: str) -> str:
    return json.dumps({"name": name, "status": "running"})


# ---------------------------------------------------------------------------
# _extract_agent_name — pattern coverage
# ---------------------------------------------------------------------------

def test_extract_json_name():
    line = json.dumps({"name": "my-agent", "status": "running"}).lower()
    assert _extract_agent_name(line) == "my-agent"


def test_extract_json_name_no_space():
    assert _extract_agent_name('"name":"my-agent"') == "my-agent"


def test_extract_escaped_json_name():
    assert _extract_agent_name('\\"name\\": \\"my-agent\\"') == "my-agent"


def test_extract_endpoint_complete():
    line = 'post /api/agents/my-agent/complete\n{"result": "done"}'
    assert _extract_agent_name(line.lower()) == "my-agent"


def test_extract_endpoint_register():
    line = 'post /agents/my-agent/register'
    assert _extract_agent_name(line.lower()) == "my-agent"


def test_extract_api_agents_path():
    assert _extract_agent_name("/api/agents/my-agent/ http/1.1") == "my-agent"


def test_extract_you_are_double_quote():
    assert _extract_agent_name('you are "my-agent"') == "my-agent"


def test_extract_you_are_single_quote():
    assert _extract_agent_name("you are 'my-agent'") == "my-agent"


def test_extract_you_are_the_agent():
    assert _extract_agent_name("you are the my-agent agent") == "my-agent"


def test_extract_agent_colon():
    assert _extract_agent_name("agent: my-agent") == "my-agent"


def test_extract_returns_none_for_empty():
    assert _extract_agent_name("") is None
    assert _extract_agent_name("no matching pattern here") is None


# ---------------------------------------------------------------------------
# _load_candidates builds the name index
# ---------------------------------------------------------------------------

def test_name_index_populated(tmp_path):
    _reset_candidates_cache()
    names = ["alpha", "beta", "gamma"]
    for n in names:
        _make_jsonl(tmp_path, n, _register_line(n))

    _load_candidates(tmp_path, "agent-*.jsonl")

    # Find the cache entry (root_mtime may vary; walk _candidates_cache values)
    cache_values = list(_candidates_cache.values())
    assert cache_values, "cache should be populated after _load_candidates"
    _expires, _candidates, name_index = cache_values[0]

    for n in names:
        assert n in name_index, f"name '{n}' missing from inverted index"
        assert name_index[n].name == f"agent-{n}.jsonl"


def test_name_index_freshest_wins(tmp_path):
    """When two files share a name, the freshest one wins in the index."""
    _reset_candidates_cache()
    old = tmp_path / "agent-old.jsonl"
    new = tmp_path / "agent-new.jsonl"
    line = _register_line("target")
    old.write_text(line + "\n")
    time.sleep(0.01)
    new.write_text(line + "\n")

    _load_candidates(tmp_path, "agent-*.jsonl")
    _expires, _candidates, name_index = list(_candidates_cache.values())[0]
    # Both would extract "target"; the freshest (newer mtime) should win
    assert name_index["target"] == new


# ---------------------------------------------------------------------------
# _find_freshest_matching_jsonl — correctness
# ---------------------------------------------------------------------------

def test_find_returns_correct_path(tmp_path):
    _reset_candidates_cache()
    _make_jsonl(tmp_path, "alice", _register_line("alice"))
    _make_jsonl(tmp_path, "bob", _register_line("bob"))
    path = _find_freshest_matching_jsonl(tmp_path, "alice", "agent-*.jsonl")
    assert path is not None
    assert path.name == "agent-alice.jsonl"


def test_find_returns_none_for_missing(tmp_path):
    _reset_candidates_cache()
    _make_jsonl(tmp_path, "alice", _register_line("alice"))
    path = _find_freshest_matching_jsonl(tmp_path, "carol", "agent-*.jsonl")
    assert path is None


def test_find_linear_scan_fallback(tmp_path):
    """Fallback linear scan resolves names _extract_agent_name can't parse."""
    _reset_candidates_cache()
    # Unusual intro that none of the regex patterns match but
    # _first_line_matches_needle's intro_patterns will catch via substring.
    # Use a format that skips all extraction regexes but matches
    # the "you are the X agent" substring check in _first_line_matches_needle.
    unusual = 'context: you are the mystery agent, do the thing'
    p = _make_jsonl(tmp_path, "mystery", unusual)

    path = _find_freshest_matching_jsonl(tmp_path, "mystery", "agent-*.jsonl")
    assert path == p


# ---------------------------------------------------------------------------
# Performance: 800 candidates resolves in under 2 s (was ~6 s before fix)
# ---------------------------------------------------------------------------

def test_resolve_800_candidates_fast(tmp_path):
    """With 800 candidate files, resolution must complete under 2 s."""
    _reset_candidates_cache()

    target_name = "needle-agent"
    for i in range(799):
        _make_jsonl(tmp_path, f"agent-{i:04d}", _register_line(f"agent-{i:04d}"))
    _make_jsonl(tmp_path, target_name, _register_line(target_name))

    start = time.monotonic()
    path = _find_freshest_matching_jsonl(tmp_path, target_name, "agent-*.jsonl")
    elapsed = time.monotonic() - start

    assert path is not None, "should find the needle"
    assert elapsed < 2.0, f"resolution took {elapsed:.3f}s — GIL stall likely"
