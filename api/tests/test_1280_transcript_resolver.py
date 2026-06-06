"""Tests for services.transcript_resolver (→1280).

Verifies that resolve_transcript finds the right JSONL when the agent's
worktree_path is absent from the in-memory map, and returns None when nothing
matches.
"""

import time
from pathlib import Path

import pytest

from services.transcript_resolver import resolve_transcript, clear_cache, _PROJECTS_DIR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project_tree(tmp_path: Path, agent_name: str, jsonl_sizes: list[int]) -> list[Path]:
    """Create a fake ~/.claude/projects/ layout for *agent_name*.

    Builds:
      tmp_path/-Users-tori-claude-torios--claude-worktrees-agent-<name>/
          <uuid-N>.jsonl   (one file per entry in jsonl_sizes, filled with
                            that many bytes of 'x')

    Returns the list of created jsonl paths in order.
    """
    dir_name = f"-Users-tori-claude-torios--claude-worktrees-agent-{agent_name}"
    proj_dir = tmp_path / dir_name
    proj_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for i, size in enumerate(jsonl_sizes):
        p = proj_dir / f"session-{i:04d}.jsonl"
        p.write_bytes(b"x" * size)
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolveTranscript:
    def setup_method(self):
        clear_cache()

    def test_finds_largest_jsonl(self, tmp_path, monkeypatch):
        """Returns the largest .jsonl file in the matching project dir."""
        monkeypatch.setattr("services.transcript_resolver._PROJECTS_DIR", tmp_path)

        agent = "my-agent-abc123"
        paths = _make_project_tree(tmp_path, agent, [100, 500, 200])

        result = resolve_transcript(agent)
        assert result is not None
        assert result.stat().st_size == 500

    def test_no_matching_dir_returns_none(self, tmp_path, monkeypatch):
        """Returns None when no project dir name contains agent-<name>."""
        monkeypatch.setattr("services.transcript_resolver._PROJECTS_DIR", tmp_path)

        # Create a dir for a *different* agent
        _make_project_tree(tmp_path, "other-agent-xyz", [300])

        result = resolve_transcript("missing-agent-999")
        assert result is None

    def test_all_zero_byte_files_returns_none(self, tmp_path, monkeypatch):
        """Returns None when matching dir exists but all .jsonl files are empty."""
        monkeypatch.setattr("services.transcript_resolver._PROJECTS_DIR", tmp_path)

        _make_project_tree(tmp_path, "zero-agent", [0, 0])

        result = resolve_transcript("zero-agent")
        assert result is None

    def test_projects_dir_missing_returns_none(self, tmp_path, monkeypatch):
        """Returns None when ~/.claude/projects/ does not exist at all."""
        monkeypatch.setattr(
            "services.transcript_resolver._PROJECTS_DIR",
            tmp_path / "nonexistent",
        )

        result = resolve_transcript("any-agent")
        assert result is None

    def test_result_is_cached(self, tmp_path, monkeypatch):
        """Second call for same name returns cached result without re-scanning."""
        monkeypatch.setattr("services.transcript_resolver._PROJECTS_DIR", tmp_path)

        agent = "cached-agent-001"
        paths = _make_project_tree(tmp_path, agent, [400])

        first = resolve_transcript(agent)
        assert first is not None

        # Remove the file; cache should still return the old result
        paths[0].unlink()

        second = resolve_transcript(agent)
        assert second == first  # same cached Path object

    def test_cache_expires_and_rescans(self, tmp_path, monkeypatch):
        """After TTL expires, a fresh scan picks up new files."""
        import services.transcript_resolver as mod
        monkeypatch.setattr(mod, "_PROJECTS_DIR", tmp_path)
        monkeypatch.setattr(mod, "_CACHE_TTL", 0.01)  # 10 ms

        agent = "expire-agent-002"
        _make_project_tree(tmp_path, agent, [100])

        first = resolve_transcript(agent)
        assert first is not None

        # Wait for TTL to expire
        time.sleep(0.02)

        # Add a larger file — should be picked up after cache expiry
        dir_name = f"-Users-tori-claude-torios--claude-worktrees-agent-{agent}"
        new_file = tmp_path / dir_name / "session-bigger.jsonl"
        new_file.write_bytes(b"y" * 999)

        second = resolve_transcript(agent)
        assert second is not None
        assert second.stat().st_size == 999

    def test_multiple_matching_dirs_picks_largest_across_all(self, tmp_path, monkeypatch):
        """If two project dirs both match the agent name, largest file wins."""
        monkeypatch.setattr("services.transcript_resolver._PROJECTS_DIR", tmp_path)

        agent = "multi-dir-agent"
        # Dir A: smaller file
        dir_a = tmp_path / f"-path-A--claude-worktrees-agent-{agent}"
        dir_a.mkdir(exist_ok=True)
        (dir_a / "a.jsonl").write_bytes(b"a" * 200)

        # Dir B: larger file
        dir_b = tmp_path / f"-path-B--claude-worktrees-agent-{agent}"
        dir_b.mkdir(exist_ok=True)
        big = dir_b / "b.jsonl"
        big.write_bytes(b"b" * 800)

        result = resolve_transcript(agent)
        assert result is not None
        assert result.stat().st_size == 800

    def test_clear_cache_forces_rescan(self, tmp_path, monkeypatch):
        """clear_cache() drops all entries so next call re-scans."""
        monkeypatch.setattr("services.transcript_resolver._PROJECTS_DIR", tmp_path)

        agent = "clear-agent-003"
        paths = _make_project_tree(tmp_path, agent, [150])

        resolve_transcript(agent)  # warm cache

        # Remove file and clear cache
        paths[0].unlink()
        clear_cache()

        result = resolve_transcript(agent)
        assert result is None  # re-scan sees no files
