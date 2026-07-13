"""→2887: the Activity page Events tab was always empty.

Two root causes, both covered here:

1. The socket mapping sent ``os history`` to the daemon's ``session_history``
   tool, which replays a per-agent session log and answers
   "No previous session found for agent 'unknown'" — never project history.
   History must not have a socket mapping; only the CLI subprocess returns
   the project feed.

2. The socket-availability latch: any command WITHOUT a socket mapping used
   to flip ``_socket_available`` to False forever, pushing every later call
   (including the 30-second clock refresher) onto the deprecated ``ostk os``
   CLI alias. Each alias call appends a ``cli.deprecated`` row to the
   history log, so the "last N" window filled with hidden noise and the
   Events feed filtered down to nothing.

The activity router now also over-fetches the raw history window and trims
to ``last`` after filtering, so the residual noise already in the log
cannot blank the feed.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ostk import OstkService


def _completed(returncode: int = 0, stdout: str = "sub-ok", stderr: str = ""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# History transport: no socket mapping
# ---------------------------------------------------------------------------


class TestHistoryTransport:
    def test_history_has_no_socket_mapping(self):
        """The daemon's session_history tool is a per-agent replay, not the
        project history feed — mapping ``os history`` to it made the Events
        tab permanently empty whenever the socket was healthy."""
        svc = OstkService(cwd="/tmp")
        assert svc._resolve_socket_tool(("os", "history")) is None
        assert svc._resolve_socket_tool(("os", "history", "--last", "50")) is None

    def test_clock_and_status_keep_socket_mappings(self):
        svc = OstkService(cwd="/tmp")
        assert svc._resolve_socket_tool(("os", "clock")) == ("clock", {})
        assert svc._resolve_socket_tool(("os", "status")) == ("status", {})


# ---------------------------------------------------------------------------
# Socket availability latch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSocketLatch:
    async def test_unmapped_command_does_not_poison_latch(self):
        """``os metrics`` has no socket mapping. Falling through to the
        subprocess for it is normal and must not mark the socket transport
        unavailable for every other command."""
        svc = OstkService(cwd="/tmp")
        svc._socket_available = True
        with patch("subprocess.run", return_value=_completed()) as sub:
            out = await svc._run("os", "metrics")
        assert out == "sub-ok"
        assert sub.called
        assert svc._socket_available is True

    async def test_socket_failure_is_retried_after_cooldown(self):
        """A socket failure is transient (daemon restart, dropped
        connection). After the cooldown the socket must be probed again
        instead of shelling out to the deprecated alias forever."""
        svc = OstkService(cwd="/tmp")
        svc._socket_available = False
        svc._socket_failed_at = time.monotonic() - 10_000
        svc._run_socket = AsyncMock(return_value="socket-ok")
        with patch("subprocess.run", return_value=_completed()):
            out = await svc._run("os", "clock")
        assert out == "socket-ok"
        assert svc._socket_available is True

    async def test_socket_failure_not_retried_within_cooldown(self):
        svc = OstkService(cwd="/tmp")
        svc._socket_available = False
        svc._socket_failed_at = time.monotonic()
        svc._run_socket = AsyncMock(
            side_effect=AssertionError("socket must not be probed inside the cooldown")
        )
        with patch("subprocess.run", return_value=_completed()):
            out = await svc._run("os", "clock")
        assert out == "sub-ok"


# ---------------------------------------------------------------------------
# Activity feed: noise resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestActivityFeedNoiseResilience:
    async def test_history_window_is_overfetched(self, client):
        """The router must ask for a larger raw window than ``last`` so
        hidden internal events don't consume the whole window."""
        with patch("routers.activity.ostk") as mock_ostk:
            mock_ostk.get_history = AsyncMock(return_value=[])
            mock_ostk.list_tasks = AsyncMock(return_value=[])
            resp = await client.get("/api/activity?last=100")
        assert resp.status_code == 200
        assert mock_ostk.get_history.call_args.kwargs["last"] >= 1000

    async def test_feed_not_emptied_by_hidden_noise_flood(self, client):
        """96 cli.deprecated rows + 4 real events in the window: the feed
        must still show the 4 real events, not an empty state."""
        noise = [
            {
                "timestamp": f"2026-07-13T10:{i // 60:02d}:{i % 60:02d}Z",
                "event": "cli.deprecated",
                "detail": 'from="os"',
            }
            for i in range(96)
        ]
        real = [
            {
                "timestamp": f"2026-07-13T09:00:0{i}Z",
                "event": "task.added",
                "detail": f"→10{i} Real task {i}",
            }
            for i in range(4)
        ]
        with patch("routers.activity.ostk") as mock_ostk:
            mock_ostk.get_history = AsyncMock(return_value=noise + real)
            mock_ostk.list_tasks = AsyncMock(return_value=[])
            resp = await client.get("/api/activity?last=50")
        data = resp.json()
        assert data["count"] == 4
        assert [e["event"] for e in data["events"]] == ["task.added"] * 4

    async def test_visible_events_trimmed_to_last(self, client):
        """Over-fetching must not inflate the response beyond ``last``."""
        real = [
            {
                "timestamp": f"2026-07-13T{8 + i // 60:02d}:{i % 60:02d}:00Z",
                "event": "task.added",
                "detail": f"→{i} Task {i}",
            }
            for i in range(80)
        ]
        with patch("routers.activity.ostk") as mock_ostk:
            mock_ostk.get_history = AsyncMock(return_value=real)
            mock_ostk.list_tasks = AsyncMock(return_value=[])
            resp = await client.get("/api/activity?last=50")
        data = resp.json()
        assert data["count"] == 50
        # Newest events are the ones kept.
        assert data["events"][0]["timestamp"] > data["events"][-1]["timestamp"]
