"""Tests for the FCP session lifecycle and registry."""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.fcp_export import FCPExporter, _SERVER_REGISTRY, detect_transport
from services.fcp_session import (
    FCPSession,
    FCPSessionRegistry,
    SESSION_TTL_SECONDS,
    VersionSnapshot,
)


# ---------------------------------------------------------------------------
# FCPExporter unit tests
# ---------------------------------------------------------------------------


class TestFCPExporter:
    """Tests for the one-shot FCPExporter."""

    def test_init_known_server(self):
        """Creating an exporter for a known server should succeed."""
        exporter = FCPExporter("drawio")
        assert exporter._server_name == "drawio"
        assert exporter._started is False

    def test_init_unknown_server(self):
        """Creating an exporter for an unknown server should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown FCP server"):
            FCPExporter("nonexistent-server")

    def test_detect_transport(self):
        """detect_transport should return the right type for known servers."""
        assert detect_transport("drawio") == "npx"
        assert detect_transport("sheets") == "npx"

    def test_detect_transport_unknown(self):
        """Unknown plain names default to uvx; no error raised."""
        assert detect_transport("unknown") == "uvx"

    @pytest.mark.asyncio
    async def test_send_before_start_raises(self):
        """Calling send() before start() should raise RuntimeError."""
        exporter = FCPExporter("drawio")
        with pytest.raises(RuntimeError, match="not started"):
            await exporter.send("set_page", {"title": "Test"})

    @pytest.mark.asyncio
    async def test_start_and_send(self):
        """With a mocked subprocess, start+send should work."""
        exporter = FCPExporter("drawio")

        mock_proc = AsyncMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.is_closing = MagicMock(return_value=False)
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(
            return_value=b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
        )
        mock_proc.stderr = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await exporter.start()
            result = await exporter.send("set_page", {"title": "Test"})

        assert result == {"ok": True}
        assert exporter._started is True

    @pytest.mark.asyncio
    async def test_send_broken_pipe(self):
        """A broken pipe should raise RuntimeError with a helpful message."""
        exporter = FCPExporter("drawio")

        mock_proc = AsyncMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock(side_effect=BrokenPipeError)
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.is_closing = MagicMock(return_value=False)
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stderr = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await exporter.start()
            with pytest.raises(RuntimeError, match="pipe is broken"):
                await exporter.send("add", {"id": "test"})

    @pytest.mark.asyncio
    async def test_send_server_error(self):
        """An FCP server error response should raise RuntimeError."""
        exporter = FCPExporter("drawio")

        mock_proc = AsyncMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.is_closing = MagicMock(return_value=False)
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(
            return_value=b'{"jsonrpc":"2.0","id":1,"error":{"message":"bad shape type"}}\n'
        )
        mock_proc.stderr = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await exporter.start()
            with pytest.raises(RuntimeError, match="bad shape type"):
                await exporter.send("add", {"type": "bad"})

    @pytest.mark.asyncio
    async def test_close(self):
        """close() should terminate the subprocess."""
        exporter = FCPExporter("drawio")

        mock_proc = AsyncMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.is_closing = MagicMock(return_value=False)
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stderr = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await exporter.start()
            await exporter.close()

        assert exporter._started is False
        assert exporter._process is None

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """The async context manager should start and close."""
        mock_proc = AsyncMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.is_closing = MagicMock(return_value=False)
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stderr = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            async with FCPExporter("drawio") as exporter:
                assert exporter._started is True
            assert exporter._started is False

    @pytest.mark.asyncio
    async def test_save_creates_parent_dirs(self, tmp_path):
        """save() should create parent directories for the output file."""
        exporter = FCPExporter("drawio")

        mock_proc = AsyncMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.is_closing = MagicMock(return_value=False)
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(
            return_value=b'{"jsonrpc":"2.0","id":1,"result":{}}\n'
        )
        mock_proc.stderr = AsyncMock()
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()

        nested_path = tmp_path / "deep" / "nested" / "output.drawio"

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await exporter.start()
            result = await exporter.save(str(nested_path))

        assert result == nested_path
        assert nested_path.parent.exists()

    def test_server_registry_has_drawio(self):
        """The server registry should include drawio."""
        assert "drawio" in _SERVER_REGISTRY
        assert _SERVER_REGISTRY["drawio"]["transport"] == "npx"

    def test_server_registry_has_sheets(self):
        """The server registry should include sheets."""
        assert "sheets" in _SERVER_REGISTRY

    def test_server_registry_has_slides(self):
        """The server registry should include slides."""
        assert "slides" in _SERVER_REGISTRY


# ---------------------------------------------------------------------------
# FCPSession lifecycle
# ---------------------------------------------------------------------------


def _make_mock_popen(responses=None):
    """Create a mock subprocess.Popen returning JSON-RPC responses (text=True)."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    if responses:
        mock_proc.stdout.readline.side_effect = responses
    else:
        mock_proc.stdout.readline.return_value = '{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
    return mock_proc


class TestFCPSession:
    """Tests for FCPSession lifecycle."""

    def test_session_init(self, tmp_path):
        """FCPSession should store document_id, server_name, file_path."""
        fp = tmp_path / "doc.drawio"
        session = FCPSession("doc-1", "drawio", fp)
        assert session.document_id == "doc-1"
        assert session.server_name == "drawio"
        assert session.file_path == fp
        assert session._process is None

    def test_is_alive_without_process(self, tmp_path):
        """is_alive should be False before start()."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        assert session.is_alive is False

    def test_is_expired_fresh(self, tmp_path):
        """A freshly created session should not be expired."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        assert session.is_expired is False

    def test_is_expired_after_ttl(self, tmp_path):
        """A session should be expired after SESSION_TTL_SECONDS of inactivity."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        session.last_activity = time.monotonic() - SESSION_TTL_SECONDS - 1
        assert session.is_expired is True

    def test_start_launches_subprocess(self, tmp_path):
        """start() should launch a subprocess and send initialize + session_start."""
        fp = tmp_path / "doc.drawio"
        session = FCPSession("doc-1", "drawio", fp)
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session.start()

        assert session._process is mock_proc
        assert session.is_alive is True
        assert mock_proc.stdin.write.call_count == 2

    def test_start_file_not_found_raises(self, tmp_path):
        """start() should raise FCPSessionError if the server binary is missing."""
        from services.fcp_session import FCPSessionError
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")

        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            with pytest.raises(FCPSessionError, match="Could not start"):
                session.start()

    def test_mutate_tracks_undo_stack(self, tmp_path):
        """mutate() should push the batch onto the undo stack."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session.start()

        session.mutate([{"verb": "add", "args": {"id": "box1"}}])
        session.mutate([{"verb": "add", "args": {"id": "box2"}}])

        assert len(session._undo_stack) == 2
        assert len(session._redo_stack) == 0

    def test_mutate_clears_redo_stack(self, tmp_path):
        """A new mutate after undo should clear the redo stack."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session.start()

        session.mutate([{"verb": "add", "args": {"id": "a"}}])
        session.mutate([{"verb": "add", "args": {"id": "b"}}])
        session.undo()
        assert len(session._redo_stack) == 1

        session.mutate([{"verb": "add", "args": {"id": "c"}}])
        assert len(session._redo_stack) == 0

    def test_mutate_without_process_raises(self, tmp_path):
        """mutate() before start() should raise FCPSessionError."""
        from services.fcp_session import FCPSessionError
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")

        with pytest.raises(FCPSessionError, match="not running"):
            session.mutate([{"verb": "add", "args": {}}])

    def test_query_does_not_track_undo(self, tmp_path):
        """query() should not add to the undo stack."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session.start()

        session.query()
        assert len(session._undo_stack) == 0

    def test_undo_empty_returns_nothing_to_undo(self, tmp_path):
        """undo() on an empty stack should return nothing_to_undo status."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session.start()

        result = session.undo()
        assert result == {"status": "nothing_to_undo"}

    def test_redo_empty_returns_nothing_to_redo(self, tmp_path):
        """redo() on an empty stack should return nothing_to_redo status."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session.start()

        result = session.redo()
        assert result == {"status": "nothing_to_redo"}

    def test_undo_moves_batch_to_redo(self, tmp_path):
        """undo() should move the last batch from undo stack to redo stack."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session.start()

        batch = [{"verb": "add", "args": {"id": "a"}}]
        session.mutate(batch)
        session.undo()

        assert len(session._undo_stack) == 0
        assert len(session._redo_stack) == 1
        assert session._redo_stack[0] == batch

    def test_close_kills_process(self, tmp_path):
        """close() should terminate the subprocess."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session.start()

        session.close()
        assert session._process is None

    def test_last_activity_updates_on_mutate(self, tmp_path):
        """mutate() should update last_activity."""
        session = FCPSession("doc-1", "drawio", tmp_path / "doc.drawio")
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session.start()

        old_activity = session.last_activity
        time.sleep(0.01)
        session.mutate([{"verb": "add", "args": {"id": "x"}}])
        assert session.last_activity > old_activity


# ---------------------------------------------------------------------------
# FCPSessionRegistry
# ---------------------------------------------------------------------------


class TestFCPSessionRegistry:
    """Tests for the session registry."""

    def test_create_and_get_session(self, tmp_path):
        """create() should start and store a session; get() should return it."""
        registry = FCPSessionRegistry()
        fp = tmp_path / "doc.drawio"
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session = registry.create("doc-1", "drawio", fp)

        found = registry.get("doc-1")
        assert found is session

    def test_get_nonexistent_returns_none(self):
        """get() for a missing document_id should return None."""
        registry = FCPSessionRegistry()
        assert registry.get("no-such-doc") is None

    def test_get_expired_returns_none(self, tmp_path):
        """get() should return None for expired sessions."""
        registry = FCPSessionRegistry()
        fp = tmp_path / "doc.drawio"
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            session = registry.create("doc-1", "drawio", fp)

        session.last_activity = time.monotonic() - SESSION_TTL_SECONDS - 1
        assert registry.get("doc-1") is None

    def test_close_removes_session(self, tmp_path):
        """close() should remove the session from the registry."""
        registry = FCPSessionRegistry()
        fp = tmp_path / "doc.drawio"
        mock_proc = _make_mock_popen()

        with patch("subprocess.Popen", return_value=mock_proc):
            registry.create("doc-1", "drawio", fp)

        registry.close("doc-1")
        assert registry.get("doc-1") is None

    def test_close_nonexistent_is_noop(self):
        """close() on a missing document_id should not raise."""
        registry = FCPSessionRegistry()
        registry.close("no-such-doc")  # must not raise

    def test_cleanup_expired_removes_stale_sessions(self, tmp_path):
        """cleanup_expired() should close expired sessions and return count."""
        registry = FCPSessionRegistry()
        mock_proc1 = _make_mock_popen()
        mock_proc2 = _make_mock_popen()

        with patch("subprocess.Popen", side_effect=[mock_proc1, mock_proc2]):
            registry.create("doc-fresh", "drawio", tmp_path / "fresh.drawio")
            s2 = registry.create("doc-old", "drawio", tmp_path / "old.drawio")

        s2.last_activity = time.monotonic() - SESSION_TTL_SECONDS - 1

        cleaned = registry.cleanup_expired()

        assert cleaned == 1
        assert registry.get("doc-fresh") is not None
        assert registry.get("doc-old") is None

    def test_list_sessions_returns_document_ids(self, tmp_path):
        """list_sessions() should return metadata for all active sessions."""
        registry = FCPSessionRegistry()
        mock_proc1 = _make_mock_popen()
        mock_proc2 = _make_mock_popen()

        with patch("subprocess.Popen", side_effect=[mock_proc1, mock_proc2]):
            registry.create("doc-a", "drawio", tmp_path / "a.drawio")
            registry.create("doc-b", "drawio", tmp_path / "b.drawio")

        sessions = registry.list_sessions()
        assert len(sessions) == 2
        doc_ids = {s["document_id"] for s in sessions}
        assert doc_ids == {"doc-a", "doc-b"}
        for s in sessions:
            assert "server_name" in s
            assert "file_path" in s
            assert "is_alive" in s

    def test_create_replaces_existing_session(self, tmp_path):
        """create() for an existing document_id should close the old session."""
        registry = FCPSessionRegistry()
        mock_proc1 = _make_mock_popen()
        mock_proc2 = _make_mock_popen()

        with patch("subprocess.Popen", side_effect=[mock_proc1, mock_proc2]):
            s1 = registry.create("doc-1", "drawio", tmp_path / "doc.drawio")
            s2 = registry.create("doc-1", "drawio", tmp_path / "doc.drawio")

        assert s1 is not s2
        assert registry.get("doc-1") is s2
