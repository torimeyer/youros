"""Tests for the Roadmap chat notification and chat-command flow.

Covers Part A (notification fires + auto-task suppression for Roadmap
outputs) and Part B (chat command endpoint parses the roadmap and
creates tasks). Also regression-guards the unchanged behaviour for
non-roadmap agents (auto-tasks still fire).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.fixture(autouse=True)
def _isolate_agent_memory(tmp_path):
    """Redirect agent_memory writes to a temp dir.

    Without this, tests that POST /api/agents/{name}/complete write to
    the real ``~/.youros/agent_memory/<name>.json``, and the server's
    retroactive Files-tab sweep on the next cold start picks up those
    entries and writes <name>-<ts>.md into the real ``~/.youros/files/``.
    Pin to a tmp dir so the tests are hermetic.
    """
    import services.agent_memory as _mem

    with patch.object(_mem, "AGENT_MEMORY_DIR", tmp_path / "agent_memory"):
        yield


# ---------------------------------------------------------------------------
# Part A: Roadmap /complete posts chat notification + suppresses auto-tasks
# ---------------------------------------------------------------------------


ROADMAP_SUMMARY = (
    "# Product roadmap\n\n"
    "## Q1 2026\n\n"
    "- Ship onboarding wizard\n"
    "- Launch guided tour\n"
    "- Send welcome email\n\n"
    "## Q2 2026\n\n"
    "- Add billing flow\n"
    "- Ship team invites\n\n"
    "## Next steps\n\n"
    "- Finalize roadmap review with stakeholders\n"
)


def _roadmap_metadata() -> dict:
    return {
        "spawned_at": "2026-04-15T10:00:00+00:00",
        "budget": "3.0",
        "model": "claude-sonnet-4-20250514",
        "source": "claude-code",
        "template": "Roadmap",
        "status": "running",
    }


@pytest.mark.asyncio
async def test_roadmap_completion_emits_notification_not_chat_message(tmp_path):
    """Completing a Roadmap agent must fire a bell-icon notification
    with a link to the roadmap, and must NOT post a chat bubble. The
    chat thread stays clean for the demo.
    """
    from routers import agents as agents_module
    from routers.agents import agent_metadata
    from services.chat_history_store import ChatHistoryStore

    chat_path = tmp_path / "chat_history.json"
    chat_path.write_text(
        json.dumps(
            {
                "tabs": [
                    {"id": "tab-1", "name": "Chat", "messages": []},
                ],
                "active_tab_id": "tab-1",
            }
        )
    )
    fresh_store = ChatHistoryStore(path=chat_path)

    agent_name = "roadmap-notify-agent"
    agent_metadata[agent_name] = _roadmap_metadata()
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)

    notif_calls: list[dict] = []

    class _FakeNotif:
        def add(self, **kwargs):
            notif_calls.append(kwargs)
            return None

    import os as _os
    old_skip = _os.environ.pop("MYOS_SKIP_CHAT_NOTIFICATIONS", None)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(
                agents_module,
                "MYOS_FILES_DIR",
                fake_home / ".youros" / "files",
            ), patch(
                "services.chat_notifications.chat_history_store",
                fresh_store,
            ), patch(
                "services.notifications.notifications_service",
                _FakeNotif(),
            ), patch(
                "routers.agents._save_agent_state"
            ), patch(
                "routers.agents.ostk"
            ) as mock_ostk, patch(
                "config.PROJECT_ROOT", tmp_path
            ):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(
                    return_value={"raw": "", "daemon_running": False, "agents": []}
                )
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk.append_nudge_reply = AsyncMock(
                    return_value={"message": "ok", "timestamp": "2026-04-15T10:01:00+00:00"}
                )
                resp = await client.post(
                    f"/api/agents/{agent_name}/complete",
                    json={"summary": ROADMAP_SUMMARY},
                )
            assert resp.status_code == 200

        # Roadmap_ready notification fired exactly once. The /complete
        # endpoint also fires a generic type="agent" notification for
        # every completion; we ignore that here and only guard the
        # roadmap-specific delivery.
        roadmap_calls = [c for c in notif_calls if c.get("type") == "roadmap_ready"]
        assert len(roadmap_calls) == 1, (
            f"expected 1 roadmap_ready notification, got {len(roadmap_calls)}: "
            f"{roadmap_calls}"
        )
        call = roadmap_calls[0]
        assert "roadmap" in call["title"].lower()
        assert call.get("action_url", "").startswith("/files?path=")

        # Chat history is clean: zero messages posted.
        saved = fresh_store.load()
        messages = saved["tabs"][0]["messages"]
        assert len(messages) == 0, (
            f"expected 0 chat messages, got {len(messages)}: {messages}"
        )
    finally:
        agent_metadata.pop(agent_name, None)
        if old_skip is not None:
            _os.environ["MYOS_SKIP_CHAT_NOTIFICATIONS"] = old_skip


@pytest.mark.asyncio
async def test_retro_save_does_not_emit_notification_or_chat(tmp_path):
    """Calling _save_agent_output_to_files with emit_notification=False
    (the retro-save path) must never fire a bell notification or post
    a chat bubble. Regression guard for the duplicate-roadmap-at-boot bug.
    """
    from routers import agents as agents_module
    from routers.agents import agent_metadata, _save_agent_output_to_files

    agent_name = "roadmap-retro-save-agent"
    agent_metadata[agent_name] = _roadmap_metadata()
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)

    notif_calls: list[dict] = []

    class _FakeNotif:
        def add(self, **kwargs):
            notif_calls.append(kwargs)
            return None

    chat_calls: list[dict] = []

    def _fake_post_system_message(*args, **kwargs):
        chat_calls.append({"args": args, "kwargs": kwargs})
        return {}

    try:
        with patch.object(
            agents_module, "MYOS_FILES_DIR", fake_home / ".youros" / "files"
        ), patch(
            "services.notifications.notifications_service",
            _FakeNotif(),
        ), patch(
            "services.chat_notifications.post_system_message",
            side_effect=_fake_post_system_message,
        ):
            _save_agent_output_to_files(
                agent_name, ROADMAP_SUMMARY, emit_notification=False
            )
        assert notif_calls == [], (
            f"retro-save must not fire notifications, got {notif_calls}"
        )
        assert chat_calls == [], (
            f"retro-save must not post to chat, got {chat_calls}"
        )
    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_roadmap_writes_exactly_one_file(tmp_path):
    """Regression guard: a Roadmap completion must write ONLY the stable
    roadmap.md file, not the timestamped <agentname>-<ts>.md sibling.
    Tori was seeing two files on the Files page for every Roadmap spawn
    ('roadmap.md' and 'Roadmap J0bab1'). The per-agent timestamped write
    has to be skipped for roadmap agents so the Files page stays clean.
    """
    from routers import agents as agents_module
    from routers.agents import agent_metadata, _save_agent_output_to_files

    agent_name = "roadmap-single-file-probe"
    agent_metadata[agent_name] = _roadmap_metadata()
    fake_files = tmp_path / ".youros" / "files"
    fake_files.mkdir(parents=True, exist_ok=True)

    class _FakeNotif:
        def add(self, **_):
            return None

    try:
        with patch.object(
            agents_module, "MYOS_FILES_DIR", fake_files
        ), patch(
            "services.notifications.notifications_service", _FakeNotif()
        ):
            written_paths = _save_agent_output_to_files(
                agent_name, ROADMAP_SUMMARY, skip_auto_tasks=True
            )
        # Only one file should land on disk, and it must be roadmap.md.
        md_files = list(fake_files.glob("*.md"))
        assert len(md_files) == 1, (
            f"Roadmap must write exactly one file, got {len(md_files)}: "
            f"{[p.name for p in md_files]}"
        )
        assert md_files[0].name == "roadmap.md", (
            f"The single file must be roadmap.md, got {md_files[0].name}"
        )
        # The writer's return value should match.
        assert len(written_paths) == 1
        assert written_paths[0].name == "roadmap.md"
    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_non_roadmap_agent_still_writes_timestamped_file(tmp_path):
    """Counter-test: non-roadmap doc-producing agents keep the old
    timestamped-file behavior. Only roadmap agents skip the timestamp copy.
    """
    from routers import agents as agents_module
    from routers.agents import agent_metadata, _save_agent_output_to_files

    agent_name = "ia-review-consolidation-advisor"
    agent_metadata[agent_name] = {
        "status": "completed",
        "template": "IA Review",
        "template_produces_doc": True,
        "source": "api",
    }
    fake_files = tmp_path / ".youros" / "files"
    fake_files.mkdir(parents=True, exist_ok=True)

    class _FakeNotif:
        def add(self, **_):
            return None

    try:
        with patch.object(
            agents_module, "MYOS_FILES_DIR", fake_files
        ), patch(
            "services.notifications.notifications_service", _FakeNotif()
        ):
            written_paths = _save_agent_output_to_files(
                agent_name,
                "Summary long enough to pass the minimum character threshold "
                "so the artifact actually gets written to disk for this test.",
                skip_auto_tasks=True,
            )
        md_files = list(fake_files.glob("*.md"))
        # Non-roadmap writes a timestamped file (not roadmap.md).
        assert len(md_files) >= 1
        assert all(p.name != "roadmap.md" for p in md_files), (
            f"Non-roadmap agents must not write to roadmap.md, got {md_files}"
        )
        assert len(written_paths) >= 1
    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_roadmap_complete_suppresses_auto_task_creation(tmp_path):
    """Roadmap outputs must NOT trigger the background auto_create_tasks
    call. Tori wants a human-in-the-loop opt-in via chat, not automatic
    task creation.
    """
    from routers import agents as agents_module
    from routers.agents import agent_metadata

    agent_name = "roadmap-no-autotasks-agent"
    agent_metadata[agent_name] = _roadmap_metadata()
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)

    captured = {"called": False}

    async def _fake_auto_create(*args, **kwargs):
        captured["called"] = True
        return []

    class _FakeNotif:
        def add(self, **kwargs):
            return None

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(
                agents_module,
                "MYOS_FILES_DIR",
                fake_home / ".youros" / "files",
            ), patch(
                "services.automation_outputs.auto_create_tasks",
                side_effect=_fake_auto_create,
            ), patch(
                "services.chat_notifications.post_system_message"
            ), patch(
                "services.notifications.notifications_service",
                _FakeNotif(),
            ), patch(
                "routers.agents._save_agent_state"
            ), patch(
                "routers.agents.ostk"
            ) as mock_ostk, patch(
                "config.PROJECT_ROOT", tmp_path
            ):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(
                    return_value={"raw": "", "daemon_running": False, "agents": []}
                )
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk.append_nudge_reply = AsyncMock(
                    return_value={"message": "ok", "timestamp": "2026-04-15T10:01:00+00:00"}
                )
                resp = await client.post(
                    f"/api/agents/{agent_name}/complete",
                    json={"summary": ROADMAP_SUMMARY},
                )
            assert resp.status_code == 200

        assert captured["called"] is False, (
            "Roadmap outputs must not trigger auto_create_tasks"
        )
    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_non_roadmap_complete_still_auto_creates_tasks(tmp_path):
    """Regression guard: non-roadmap agents that opt in via
    template_produces_doc still auto-create tasks from next-step bullets.
    Only the Roadmap path changes behaviour.
    """
    from routers import agents as agents_module
    from routers.agents import agent_metadata

    agent_name = "prd-demo-agent"
    agent_metadata[agent_name] = {
        "spawned_at": "2026-04-15T10:00:00+00:00",
        "budget": "2.0",
        "model": "claude-sonnet-4-20250514",
        "source": "claude-code",
        "template": "PRD",
        "template_produces_doc": True,
        "status": "running",
    }
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)

    captured = {"called": False}

    async def _fake_auto_create(*args, **kwargs):
        captured["called"] = True
        return []

    prd_summary = (
        "# PRD for onboarding\n\n"
        "Drafted the new onboarding PRD with goals and metrics.\n\n"
        "## Next steps\n\n"
        "- Review with design\n"
        "- Schedule engineering kickoff\n"
    )

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(
                agents_module,
                "MYOS_FILES_DIR",
                fake_home / ".youros" / "files",
            ), patch(
                "services.automation_outputs.auto_create_tasks",
                side_effect=_fake_auto_create,
            ), patch(
                "routers.agents._save_agent_state"
            ), patch(
                "routers.agents.ostk"
            ) as mock_ostk, patch(
                "config.PROJECT_ROOT", tmp_path
            ):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(
                    return_value={"raw": "", "daemon_running": False, "agents": []}
                )
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk.append_nudge_reply = AsyncMock(
                    return_value={"message": "ok", "timestamp": "2026-04-15T10:01:00+00:00"}
                )
                resp = await client.post(
                    f"/api/agents/{agent_name}/complete",
                    json={"summary": prd_summary},
                )
            assert resp.status_code == 200

        # Give the fire-and-forget task a turn on the loop.
        import asyncio as _asyncio

        await _asyncio.sleep(0.05)

        assert captured["called"] is True, (
            "Non-roadmap produces_doc agents must still auto-create tasks"
        )
    finally:
        agent_metadata.pop(agent_name, None)


# ---------------------------------------------------------------------------
# Part B: Chat command endpoint creates tasks from roadmap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_create_tasks_from_roadmap_parses_and_creates(tmp_path):
    """The endpoint reads the latest roadmap.md, parses Milestones/Phases
    headings plus Next steps, calls auto_create_tasks, and returns a
    short summary reply the chat panel can render.
    """
    from routers import chat as chat_module

    files_dir = tmp_path / "files"
    files_dir.mkdir(exist_ok=True)
    roadmap = files_dir / "roadmap.md"
    roadmap.write_text(
        "---\nkind: roadmap\n---\n\n"
        "# Roadmap\n\n"
        "## Milestones\n\n"
        "- Launch onboarding v1\n"
        "- Ship billing flow\n\n"
        "## Next steps\n\n"
        "- Schedule design review\n"
    )

    captured: dict = {}

    async def _fake_auto_create(items, source_name, automation_kind, priority="P2"):
        captured["items"] = list(items)
        captured["source"] = source_name
        captured["kind"] = automation_kind
        captured["priority"] = priority
        return [{"id": f"t{i}", "title": t} for i, t in enumerate(items, start=1)]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(chat_module, "MYOS_FILES_DIR", files_dir), patch(
            "services.automation_outputs.auto_create_tasks",
            side_effect=_fake_auto_create,
        ):
            resp = await client.post(
                "/api/chat/roadmap/create-tasks",
                json={},
            )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert "Created 3 needles" in body["reply"]
    assert body["roadmap_path"].endswith("roadmap.md")
    assert captured["kind"] == "roadmap_from_chat"
    assert captured["priority"] == "P2"
    assert "Launch onboarding v1" in captured["items"]
    assert "Schedule design review" in captured["items"]
    assert len(body["created"]) == 3


@pytest.mark.asyncio
async def test_chat_create_tasks_from_roadmap_e2e_agent_name_prefixes_source(tmp_path):
    """When an e2e-* agent drives the endpoint, the generated tasks must
    carry an ``e2e-`` prefixed source_name so the cleanup sweep can
    catch them by description. Leak-class regression guard for
    roadmap-e2e-task-leak.
    """
    from routers import chat as chat_module

    files_dir = tmp_path / "files"
    files_dir.mkdir(exist_ok=True)
    # Filename does NOT start with e2e-. Only the agent name does.
    roadmap = files_dir / "plain-roadmap.md"
    roadmap.write_text(
        "---\nkind: roadmap\n---\n\n"
        "# Roadmap\n\n"
        "## Milestones\n\n"
        "- Ship onboarding wizard\n"
    )

    captured: dict = {}

    async def _fake_auto_create(items, source_name, automation_kind, priority="P2"):
        captured["source"] = source_name
        captured["items"] = list(items)
        return [{"id": "t1", "title": items[0]}]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(chat_module, "MYOS_FILES_DIR", files_dir), patch(
            "services.automation_outputs.auto_create_tasks",
            side_effect=_fake_auto_create,
        ):
            resp = await client.post(
                "/api/chat/roadmap/create-tasks",
                json={"agent_name": "e2e-roadmap-probe-123"},
            )
    assert resp.status_code == 200, resp.text
    # Source must be prefixed even though the roadmap filename was not.
    assert captured["source"].startswith("e2e-"), (
        f"Expected source_name to start with 'e2e-', got {captured['source']!r}"
    )


@pytest.mark.asyncio
async def test_chat_create_tasks_from_roadmap_non_e2e_agent_leaves_source_alone(tmp_path):
    """Production agents (no e2e- prefix) must NOT get their source name
    mutated. This is the regression guard that the e2e fix did not
    weaken production behavior.
    """
    from routers import chat as chat_module

    files_dir = tmp_path / "files"
    files_dir.mkdir(exist_ok=True)
    roadmap = files_dir / "quarterly-plan.md"
    roadmap.write_text(
        "---\nkind: roadmap\n---\n\n"
        "# Plan\n\n"
        "## Milestones\n\n"
        "- Ship feature\n"
    )

    captured: dict = {}

    async def _fake_auto_create(items, source_name, automation_kind, priority="P2"):
        captured["source"] = source_name
        return [{"id": "t1", "title": items[0]}]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(chat_module, "MYOS_FILES_DIR", files_dir), patch(
            "services.automation_outputs.auto_create_tasks",
            side_effect=_fake_auto_create,
        ):
            resp = await client.post(
                "/api/chat/roadmap/create-tasks",
                json={"agent_name": "roadmap-notify-agent"},
            )
    assert resp.status_code == 200
    assert captured["source"] == "quarterly-plan", (
        f"Non-e2e agents must not mutate source_name, got {captured['source']!r}"
    )


@pytest.mark.asyncio
async def test_chat_create_tasks_from_roadmap_no_agent_name_backwards_compat(tmp_path):
    """Callers that do not pass agent_name (today's UI path) must keep
    the same source-name behavior as before.
    """
    from routers import chat as chat_module

    files_dir = tmp_path / "files"
    files_dir.mkdir(exist_ok=True)
    roadmap = files_dir / "roadmap.md"
    roadmap.write_text(
        "---\nkind: roadmap\n---\n\n"
        "# Roadmap\n\n"
        "## Milestones\n\n"
        "- Ship feature\n"
    )

    captured: dict = {}

    async def _fake_auto_create(items, source_name, automation_kind, priority="P2"):
        captured["source"] = source_name
        return [{"id": "t1", "title": items[0]}]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(chat_module, "MYOS_FILES_DIR", files_dir), patch(
            "services.automation_outputs.auto_create_tasks",
            side_effect=_fake_auto_create,
        ):
            resp = await client.post(
                "/api/chat/roadmap/create-tasks",
                json={},
            )
    assert resp.status_code == 200
    assert captured["source"] == "roadmap"


@pytest.mark.asyncio
async def test_chat_create_tasks_from_roadmap_no_roadmap_returns_hint(tmp_path):
    """With no roadmap file on disk, the endpoint returns a friendly
    plain-language hint instead of creating any tasks.
    """
    from routers import chat as chat_module

    files_dir = tmp_path / "files"
    files_dir.mkdir(exist_ok=True)  # empty

    call_counter = {"n": 0}

    async def _never_called(*args, **kwargs):
        call_counter["n"] += 1
        return []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(chat_module, "MYOS_FILES_DIR", files_dir), patch(
            "services.automation_outputs.auto_create_tasks",
            side_effect=_never_called,
        ):
            resp = await client.post(
                "/api/chat/roadmap/create-tasks",
                json={},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_roadmap"
    assert "don't see a recent roadmap" in body["reply"].lower()
    assert body["created"] == []
    assert call_counter["n"] == 0


# ---------------------------------------------------------------------------
# Matcher + parse_roadmap_items helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("create tasks from this roadmap", True),
        ("Create Tasks From The Roadmap", True),
        ("make tasks from the roadmap please", True),
        ("turn the roadmap into tasks", True),
        ("turn this roadmap into tasks now", True),
        ("convert the roadmap to tasks", True),
        ("break the roadmap down into tasks", True),
        ("build tasks from my roadmap", True),
        ("generate tasks from that roadmap", True),
        # Non matches
        ("what's on the roadmap?", False),
        ("show me the tasks", False),
        ("hey claude, how are you", False),
        ("", False),
    ],
)
def test_is_roadmap_to_tasks_request(text, expected):
    from routers.chat import is_roadmap_to_tasks_request

    assert is_roadmap_to_tasks_request(text) is expected


def test_parse_roadmap_items_picks_up_milestones_and_next_steps():
    from services.automation_outputs import parse_roadmap_items

    content = (
        "# Roadmap\n\n"
        "## Milestones\n\n"
        "- Launch onboarding v1\n"
        "- Ship billing flow\n\n"
        "## Q2 2026\n\n"
        "- Add team invites\n\n"
        "## Next steps\n\n"
        "- Schedule design review\n"
    )
    items = parse_roadmap_items(content)
    # Next steps gets harvested first by parse_next_steps, then roadmap
    # sections backfill. Order does not matter for assertion, just
    # presence.
    titles = set(items)
    assert "Launch onboarding v1" in titles
    assert "Ship billing flow" in titles
    assert "Add team invites" in titles
    assert "Schedule design review" in titles


def test_parse_roadmap_items_empty_content_returns_empty():
    from services.automation_outputs import parse_roadmap_items

    assert parse_roadmap_items("") == []
    assert parse_roadmap_items("# Roadmap\n\nNothing actionable here.") == []


# ---------------------------------------------------------------------------
# Regression: roadmap output must land in ~/.youros/files/ not ~/Documents/
# → Both must appear in /api/docs/recent. Ticket →1097.
# ---------------------------------------------------------------------------


def test_roadmap_template_writes_to_myos_files_not_documents():
    """The roadmap.agent template must NOT direct the agent to write to
    ~/Documents/ (old behavior that caused outputs to be invisible in the UI).
    The backend saves roadmap output to ~/.youros/files/ automatically via
    _save_agent_output_to_files when the agent calls /complete; the template
    does not need to reference that path directly.
    See also: test_roadmap_completion_md_appears_in_recent_docs.
    """
    template_path = Path(__file__).parents[2] / "agents" / "marketplace" / "roadmap.agent"
    content = template_path.read_text()
    assert "~/Documents/" not in content, (
        "roadmap.agent still references ~/Documents/ — remove it so outputs "
        "appear in Recent Documents on the Files page (backend saves to "
        "~/.youros/files/ automatically via _save_agent_output_to_files)"
    )


@pytest.mark.asyncio
async def test_roadmap_completion_md_appears_in_recent_docs(tmp_path):
    """After a roadmap completion, roadmap.md must appear in /api/docs/recent.

    Simulates the backend writing roadmap.md to MYOS_FILES_DIR (which
    _save_agent_output_to_files does for roadmap agents) and asserts the
    /api/docs/recent endpoint returns it.
    """
    import importlib

    import routers.projects as projects_module

    fake_home = tmp_path / "home"
    fake_ws = tmp_path / "workspace"
    fake_ws.mkdir(parents=True, exist_ok=True)
    myos_files = fake_home / ".youros" / "files"
    myos_files.mkdir(parents=True, exist_ok=True)

    (myos_files / "roadmap.md").write_text(
        "---\nsource: roadmap-agent\nkind: roadmap\n---\n\n# Roadmap\n\n"
        "| Quarter | Theme | Initiatives |\n"
        "| Q1 2026 | Ship onboarding | wizard, tour, email |\n"
    )

    import os as _os
    transport = ASGITransport(app=app)
    # /docs/recent uses youros_home() which reads YOUROS_HOME, not Path.home().
    with patch.object(projects_module, "TORIOS_DIR", fake_ws), \
         patch.dict(_os.environ, {"YOUROS_HOME": str(fake_home / ".youros")}):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/docs/recent")

    assert resp.status_code == 200
    names = [f["name"] for f in resp.json()["files"]]
    assert "roadmap.md" in names, (
        f"roadmap.md written to ~/.youros/files/ must appear in /api/docs/recent, got: {names}"
    )
