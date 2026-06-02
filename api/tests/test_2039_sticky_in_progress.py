"""
→2039: needle must NOT stick at in_progress after its agent dies without closing it.

Root cause: set_task_in_progress() writes status=in_progress to issues.jsonl at
spawn time. When the agent goes terminal without calling close_task, that status
stays forever. The fix: _set_agent_status fires release_needle on terminal so
issues.jsonl gets reset to open — but ONLY for in_progress needles (closed/
shelved are left alone), and ONLY when no other live agent still holds the needle.
"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_needle(issues_path: Path, needle_id: str, status: str) -> None:
    issues_path.parent.mkdir(parents=True, exist_ok=True)
    issues_path.write_text(
        json.dumps({"id": needle_id, "status": status, "title": "test needle"}) + "\n"
    )


def _read_status(issues_path: Path, needle_id: str) -> str | None:
    for line in issues_path.read_text().strip().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if str(entry.get("id", "")) == needle_id:
            return entry.get("status")
    return None


# ---------------------------------------------------------------------------
# OstkService.release_needle tests
# ---------------------------------------------------------------------------

class TestReleaseNeedle:
    @pytest.fixture
    def tmp_ostk(self, tmp_path):
        issues_path = tmp_path / ".ostk" / "needles" / "issues.jsonl"
        return issues_path, tmp_path

    @pytest.mark.asyncio
    async def test_in_progress_resets_to_open(self, tmp_ostk):
        """in_progress needle is reset to open — the bug case."""
        issues_path, tmp = tmp_ostk
        _write_needle(issues_path, "→2039", "in_progress")

        from services.ostk import OstkService
        svc = OstkService(cwd=str(tmp))
        result = await svc.release_needle("→2039")

        assert result is True
        assert _read_status(issues_path, "→2039") == "open"

    @pytest.mark.asyncio
    async def test_in_progress_clears_in_progress_at_field(self, tmp_ostk):
        """in_progress_at timestamp is removed when needle is released."""
        issues_path, tmp = tmp_ostk
        issues_path.parent.mkdir(parents=True, exist_ok=True)
        issues_path.write_text(
            json.dumps({"id": "→2039", "status": "in_progress",
                        "in_progress_at": "2026-01-01T00:00:00Z",
                        "title": "test"}) + "\n"
        )

        from services.ostk import OstkService
        svc = OstkService(cwd=str(tmp))
        await svc.release_needle("→2039")

        lines = issues_path.read_text().strip().splitlines()
        entry = json.loads(lines[0])
        assert entry.get("status") == "open"
        assert "in_progress_at" not in entry

    @pytest.mark.asyncio
    async def test_closed_needle_is_untouched(self, tmp_ostk):
        """Closed needle (agent properly closed it) must NOT be reopened."""
        issues_path, tmp = tmp_ostk
        _write_needle(issues_path, "→2039", "closed")

        from services.ostk import OstkService
        svc = OstkService(cwd=str(tmp))
        result = await svc.release_needle("→2039")

        assert result is False
        assert _read_status(issues_path, "→2039") == "closed"

    @pytest.mark.asyncio
    async def test_shelved_needle_is_untouched(self, tmp_ostk):
        """Shelved needle must NOT be reopened."""
        issues_path, tmp = tmp_ostk
        _write_needle(issues_path, "→2039", "shelved")

        from services.ostk import OstkService
        svc = OstkService(cwd=str(tmp))
        result = await svc.release_needle("→2039")

        assert result is False
        assert _read_status(issues_path, "→2039") == "shelved"

    @pytest.mark.asyncio
    async def test_open_needle_is_noop(self, tmp_ostk):
        """Already-open needle: no change, returns False."""
        issues_path, tmp = tmp_ostk
        _write_needle(issues_path, "→2039", "open")

        from services.ostk import OstkService
        svc = OstkService(cwd=str(tmp))
        result = await svc.release_needle("→2039")

        assert result is False
        assert _read_status(issues_path, "→2039") == "open"

    @pytest.mark.asyncio
    async def test_bare_id_matches_arrow_prefixed_needle(self, tmp_ostk):
        """Bare '2039' matches needle stored as '→2039' (normalization)."""
        issues_path, tmp = tmp_ostk
        _write_needle(issues_path, "→2039", "in_progress")

        from services.ostk import OstkService
        svc = OstkService(cwd=str(tmp))
        result = await svc.release_needle("2039")

        assert result is True
        assert _read_status(issues_path, "→2039") == "open"

    @pytest.mark.asyncio
    async def test_missing_issues_file_returns_false(self, tmp_ostk):
        """No-op when issues.jsonl doesn't exist."""
        _issues_path, tmp = tmp_ostk
        from services.ostk import OstkService
        svc = OstkService(cwd=str(tmp))
        result = await svc.release_needle("→2039")
        assert result is False


# ---------------------------------------------------------------------------
# _set_agent_status fires needle release on terminal
# ---------------------------------------------------------------------------

class TestTerminalTriggersNeedleRelease:
    """_set_agent_status must schedule _fire_release_needle_if_orphaned on terminal."""

    def _setup_agent(self, name: str, needle_id: str, status: str = "running"):
        from routers.agents import agent_metadata
        agent_metadata[name] = {"status": status, "needle_id": needle_id}
        return name

    def _cleanup_agent(self, name: str):
        from routers.agents import agent_metadata
        agent_metadata.pop(name, None)

    def test_terminal_fires_release_for_needle_id(self):
        """terminated_stale → _fire_release_needle_if_orphaned called with needle_id."""
        name = "_test_2039_terminal_a"
        self._setup_agent(name, "→2039")
        try:
            with patch("routers.agents._fire_release_needle_if_orphaned") as mock_rel:
                from routers.agents import _set_agent_status
                _set_agent_status(name, "terminated_stale", terminated_at="2026-01-01T00:00:00Z")
                mock_rel.assert_called_once_with("→2039")
        finally:
            self._cleanup_agent(name)

    def test_all_terminal_statuses_fire_release(self):
        """Every terminal status fires the release."""
        from routers.agents import _TERMINAL_STATUSES, _set_agent_status
        for status in _TERMINAL_STATUSES:
            name = f"_test_2039_ts_{status}"
            self._setup_agent(name, "→2039")
            try:
                with patch("routers.agents._fire_release_needle_if_orphaned") as mock_rel:
                    _set_agent_status(name, status)
                    mock_rel.assert_called_once_with("→2039"), \
                        f"expected release for terminal status '{status}'"
            finally:
                self._cleanup_agent(name)

    def test_non_terminal_status_does_not_fire_release(self):
        """running/spawned transitions do NOT trigger release."""
        name = "_test_2039_non_terminal"
        self._setup_agent(name, "→2039", status="spawned")
        try:
            with patch("routers.agents._fire_release_needle_if_orphaned") as mock_rel:
                from routers.agents import _set_agent_status
                _set_agent_status(name, "running")
                mock_rel.assert_not_called()
        finally:
            self._cleanup_agent(name)

    def test_agent_without_needle_id_does_not_crash(self):
        """Agent with no needle_id goes terminal without error."""
        name = "_test_2039_no_needle"
        from routers.agents import agent_metadata
        agent_metadata[name] = {"status": "running"}
        try:
            with patch("routers.agents._fire_release_needle_if_orphaned") as mock_rel:
                from routers.agents import _set_agent_status
                _set_agent_status(name, "completed")
                mock_rel.assert_not_called()
        finally:
            agent_metadata.pop(name, None)

    def test_needle_ids_list_also_fires_release(self):
        """needle_ids list (multi-needle agents) also triggers release for each."""
        name = "_test_2039_multi_needle"
        from routers.agents import agent_metadata
        agent_metadata[name] = {
            "status": "running",
            "needle_id": "→2039",
            "needle_ids": ["→2040", "→2041"],
        }
        try:
            with patch("routers.agents._fire_release_needle_if_orphaned") as mock_rel:
                from routers.agents import _set_agent_status
                _set_agent_status(name, "failed")
                calls = [c.args[0] for c in mock_rel.call_args_list]
                assert "→2039" in calls
                assert "→2040" in calls
                assert "→2041" in calls
        finally:
            agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# _release_needle_if_orphaned_async skips when another agent is live
# ---------------------------------------------------------------------------

class TestOrphanCheck:
    @pytest.mark.asyncio
    async def test_skips_release_when_another_live_agent_holds_needle(self):
        """If another live agent still holds needle_id, do NOT release it."""
        from routers.agents import _release_needle_if_orphaned_async

        with patch("routers.agents.get_running_needle_ids", return_value={"→2039", "2039"}):
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.release_needle = AsyncMock(return_value=True)
                await _release_needle_if_orphaned_async("→2039")
                mock_ostk.release_needle.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_release_when_needle_is_truly_orphaned(self):
        """No live agent holds needle_id → ostk.release_needle is called."""
        from routers.agents import _release_needle_if_orphaned_async

        with patch("routers.agents.get_running_needle_ids", return_value=set()):
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.release_needle = AsyncMock(return_value=True)
                await _release_needle_if_orphaned_async("→2039")
                mock_ostk.release_needle.assert_called_once_with("→2039")
