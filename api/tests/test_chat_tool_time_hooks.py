"""Phase 3 tests — chat tool calls publish to Time primitive.

Tests verify:
1. A successful simulated tool call writes a time_runs row with
   op_kind="chat_tool_<name>" and status="completed".
2. A failing tool call writes status="failed".
3. A Time primitive failure does NOT break the chat response
   (try/except wrapping holds in both the start and finish paths).

All tests use tmp_path SQLite — never touches ~/.myos/primitives.db.
The helper under test is ``chat_providers._execute_tool_timed()``,
a module-level async function extracted from ``_exec_and_notify`` so it
is directly unit-testable without a WebSocket.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# DB + primitive fixtures (same pattern as test_agent_time_hooks.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "primitives_test.db"


@pytest.fixture()
def pdb_mod(db_path, monkeypatch):
    """Point primitives_db at a temp file and clear the thread-local cache."""
    import services.primitives_db as pdb
    monkeypatch.setattr(pdb, "PRIMITIVES_DB_PATH", db_path)
    pdb._local.__dict__.clear()
    return pdb


@pytest.fixture()
def tp(pdb_mod, monkeypatch):
    """Return time_primitive wired to the temp DB."""
    import services.time_primitive as tp_mod
    monkeypatch.setattr(tp_mod, "_get_db", lambda: pdb_mod.get_db())
    return tp_mod


# ---------------------------------------------------------------------------
# Helper: read a raw time_runs row by op_id
# ---------------------------------------------------------------------------


def _read_time_row(pdb_mod, op_id: str):
    conn = pdb_mod.get_db()
    row = conn.execute(
        "SELECT * FROM time_runs WHERE op_id = ?", (op_id,)
    ).fetchone()
    return dict(row) if row else None


def _read_all_rows(pdb_mod):
    conn = pdb_mod.get_db()
    rows = conn.execute("SELECT * FROM time_runs").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 1. Successful tool call writes a completed time_runs row
# ---------------------------------------------------------------------------


class TestSuccessfulToolCallWritesCompletedRow:
    @pytest.mark.asyncio
    async def test_completed_row_written(self, tp, pdb_mod, monkeypatch):
        """A successful execute_tool() call must write op_kind=chat_tool_<name>,
        status=completed in time_runs."""
        from services import chat_providers as cp

        # Wire time_primitive to the temp DB
        monkeypatch.setattr("services.time_primitive._get_db", lambda: pdb_mod.get_db())

        # Mock execute_tool to return a known value without doing real work
        mock_execute = AsyncMock(return_value="tool output value")
        monkeypatch.setattr(cp, "execute_tool", mock_execute)

        op_id = "test-chat-tool-success-001"
        result = await cp._execute_tool_timed(
            tool_name="read_file",
            tool_args={"path": "/tmp/test.txt"},
            op_id=op_id,
            op_kind="chat_tool_read_file",
        )

        assert result == "tool output value", (
            f"Expected 'tool output value', got {result!r}"
        )

        row = _read_time_row(pdb_mod, op_id)
        assert row is not None, (
            "Expected a time_runs row after a successful tool call, got None. "
            "_execute_tool_timed() is not calling time_primitive.start() yet."
        )
        assert row["op_kind"] == "chat_tool_read_file", (
            f"op_kind should be 'chat_tool_read_file', got {row['op_kind']!r}"
        )
        assert row["status"] == "completed", (
            f"Expected status='completed' after successful tool, got {row['status']!r}. "
            "_execute_tool_timed() is not calling time_primitive.finish() yet."
        )
        assert row["finished_at"] is not None, (
            "finished_at must be set for a completed row"
        )

    @pytest.mark.asyncio
    async def test_op_kind_uses_tool_name(self, tp, pdb_mod, monkeypatch):
        """op_kind in the time_runs row must be 'chat_tool_<tool_name>'."""
        from services import chat_providers as cp

        monkeypatch.setattr("services.time_primitive._get_db", lambda: pdb_mod.get_db())
        mock_execute = AsyncMock(return_value="ok")
        monkeypatch.setattr(cp, "execute_tool", mock_execute)

        await cp._execute_tool_timed(
            tool_name="write_file",
            tool_args={},
            op_id="test-wf-002",
            op_kind="chat_tool_write_file",
        )

        row = _read_time_row(pdb_mod, "test-wf-002")
        assert row is not None
        assert row["op_kind"] == "chat_tool_write_file"

    @pytest.mark.asyncio
    async def test_execute_tool_called_with_correct_args(self, tp, pdb_mod, monkeypatch):
        """_execute_tool_timed must pass tool_name and tool_args through to execute_tool."""
        from services import chat_providers as cp

        monkeypatch.setattr("services.time_primitive._get_db", lambda: pdb_mod.get_db())
        mock_execute = AsyncMock(return_value="result")
        monkeypatch.setattr(cp, "execute_tool", mock_execute)

        tool_args = {"path": "/some/file.py", "content": "hello"}
        await cp._execute_tool_timed(
            tool_name="write_file",
            tool_args=tool_args,
            op_id="test-args-003",
            op_kind="chat_tool_write_file",
        )

        mock_execute.assert_awaited_once_with("write_file", tool_args)


# ---------------------------------------------------------------------------
# 2. Failing tool call writes status="failed"
# ---------------------------------------------------------------------------


class TestFailingToolCallWritesFailedRow:
    @pytest.mark.asyncio
    async def test_failed_row_written_on_execute_tool_exception(self, tp, pdb_mod, monkeypatch):
        """When execute_tool() raises, time_runs must get status='failed'."""
        from services import chat_providers as cp

        monkeypatch.setattr("services.time_primitive._get_db", lambda: pdb_mod.get_db())

        # Mock execute_tool to raise
        mock_execute = AsyncMock(side_effect=RuntimeError("tool exploded"))
        monkeypatch.setattr(cp, "execute_tool", mock_execute)

        op_id = "test-chat-tool-fail-001"
        with pytest.raises(RuntimeError, match="tool exploded"):
            await cp._execute_tool_timed(
                tool_name="bash",
                tool_args={"command": "boom"},
                op_id=op_id,
                op_kind="chat_tool_bash",
            )

        row = _read_time_row(pdb_mod, op_id)
        assert row is not None, (
            "Expected a time_runs row even after a failed tool call, got None."
        )
        assert row["status"] == "failed", (
            f"Expected status='failed' after execute_tool exception, got {row['status']!r}. "
            "_execute_tool_timed() is not calling time_primitive.finish('failed') on error."
        )
        assert row["finished_at"] is not None, (
            "finished_at must be set even for failed rows"
        )

    @pytest.mark.asyncio
    async def test_exception_propagates_to_caller(self, tp, pdb_mod, monkeypatch):
        """The original exception must re-raise after time tracking so gather()
        captures it correctly."""
        from services import chat_providers as cp

        monkeypatch.setattr("services.time_primitive._get_db", lambda: pdb_mod.get_db())
        mock_execute = AsyncMock(side_effect=ValueError("bad input"))
        monkeypatch.setattr(cp, "execute_tool", mock_execute)

        with pytest.raises(ValueError, match="bad input"):
            await cp._execute_tool_timed(
                tool_name="read_file",
                tool_args={},
                op_id="test-reraise-002",
                op_kind="chat_tool_read_file",
            )


# ---------------------------------------------------------------------------
# 3. Time primitive failure must not break the chat response
# ---------------------------------------------------------------------------


class TestTimePrimitiveFailureDoesNotBreakChat:
    @pytest.mark.asyncio
    async def test_start_failure_still_returns_tool_result(self, tp, pdb_mod, monkeypatch):
        """If time_primitive.start() raises, _execute_tool_timed must still return the
        tool result — the chat must not be broken."""
        from services import chat_providers as cp

        # Make start() raise
        monkeypatch.setattr(
            "services.time_primitive.start",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("DB locked")),
        )
        # finish() should be fine (won't be called if start raises and we swallow it)
        monkeypatch.setattr("services.time_primitive.finish", lambda *a, **kw: None)

        mock_execute = AsyncMock(return_value="data from tool")
        monkeypatch.setattr(cp, "execute_tool", mock_execute)

        # Must NOT raise — start() failure is swallowed
        result = await cp._execute_tool_timed(
            tool_name="read_file",
            tool_args={"path": "/x"},
            op_id="time-fail-start-001",
            op_kind="chat_tool_read_file",
        )
        assert result == "data from tool", (
            "Tool result must be returned even when time_primitive.start() raises. "
            "The try/except around start() is missing or too narrow."
        )

    @pytest.mark.asyncio
    async def test_finish_failure_still_returns_tool_result(self, tp, pdb_mod, monkeypatch):
        """If time_primitive.finish() raises, _execute_tool_timed must still return the
        tool result — the chat must not be broken."""
        from services import chat_providers as cp

        monkeypatch.setattr("services.time_primitive.start", lambda *a, **kw: None)
        # Make finish() raise
        monkeypatch.setattr(
            "services.time_primitive.finish",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("write failed")),
        )

        mock_execute = AsyncMock(return_value="some result")
        monkeypatch.setattr(cp, "execute_tool", mock_execute)

        result = await cp._execute_tool_timed(
            tool_name="bash",
            tool_args={"command": "echo hi"},
            op_id="time-fail-finish-002",
            op_kind="chat_tool_bash",
        )
        assert result == "some result", (
            "Tool result must be returned even when time_primitive.finish() raises. "
            "The try/except around finish() is missing or too narrow."
        )

    @pytest.mark.asyncio
    async def test_finish_failure_on_error_still_reraises_tool_exception(
        self, tp, pdb_mod, monkeypatch
    ):
        """If time_primitive.finish() raises AND execute_tool() raised, the original
        tool exception must still propagate (finish failure must not swallow it)."""
        from services import chat_providers as cp

        monkeypatch.setattr("services.time_primitive.start", lambda *a, **kw: None)
        monkeypatch.setattr(
            "services.time_primitive.finish",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("write failed")),
        )

        mock_execute = AsyncMock(side_effect=RuntimeError("tool error"))
        monkeypatch.setattr(cp, "execute_tool", mock_execute)

        with pytest.raises(RuntimeError, match="tool error"):
            await cp._execute_tool_timed(
                tool_name="bash",
                tool_args={},
                op_id="time-fail-both-003",
                op_kind="chat_tool_bash",
            )
