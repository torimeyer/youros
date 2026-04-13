from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- Helpers ---

def _make_task(id="t-1", title="Test task", priority="P1", status="open", tags=None, description=None):
    task = {
        "id": id,
        "title": title,
        "priority": priority,
        "status": status,
        "tags": tags or [],
    }
    if description is not None:
        task["description"] = description
    return task


def _patch_ostk_and_labels(**ostk_attrs):
    """Context manager that patches both ostk and task_labels_store for list_tasks tests."""
    ostk_patch = patch("routers.tasks.ostk")
    tls_patch = patch("routers.tasks.task_labels_store")

    class _Ctx:
        def __enter__(self):
            self.mock_ostk = ostk_patch.__enter__()
            self.mock_tls = tls_patch.__enter__()
            self.mock_tls.get_all_assignments = MagicMock(return_value={})
            for attr, val in ostk_attrs.items():
                setattr(self.mock_ostk, attr, val)
            return self

        def __exit__(self, *args):
            tls_patch.__exit__(*args)
            ostk_patch.__exit__(*args)

    return _Ctx()


# --- GET /api/tasks ---

@pytest.mark.asyncio
async def test_list_tasks_returns_enriched_tasks(client):
    mock_tasks = [_make_task(tags=["lego-app"])]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["goal"] == "Lego App"
    assert data["tasks"][0]["label_ids"] == []


@pytest.mark.asyncio
async def test_list_tasks_with_status_filter(client):
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=[])) as ctx:
        resp = await client.get("/api/tasks?status=open")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status="open", priority=None)


@pytest.mark.asyncio
async def test_list_tasks_with_priority_filter(client):
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=[])) as ctx:
        resp = await client.get("/api/tasks?priority=P0")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status=None, priority="P0")


# --- POST /api/tasks ---

@pytest.mark.asyncio
async def test_create_task(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-2")
        resp = await client.post("/api/tasks", json={"title": "New task", "priority": "P0"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "created t-2"
    mock_ostk.add_task.assert_called_once_with("New task", "P0", description="")


@pytest.mark.asyncio
async def test_create_task_default_priority(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-3")
        resp = await client.post("/api/tasks", json={"title": "Basic task"})

    assert resp.status_code == 200
    mock_ostk.add_task.assert_called_once_with("Basic task", "P1", description="")


@pytest.mark.asyncio
async def test_create_task_with_description_round_trip(client):
    """Regression: a task created with a description must surface that description
    when the task list is fetched. Catches the data-source-drift bug where the
    writer (POST /tasks) accepted description but the reader (GET /tasks) dropped
    it before the UI could render it."""
    created_task = _make_task(
        id="t-desc-1",
        title="New task with summary",
        description="This is a one-line summary that should appear in the list.",
    )

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.threads_store") as mock_threads:
        mock_ostk.add_task = AsyncMock(return_value="created t-desc-1")
        mock_ostk.list_tasks = AsyncMock(return_value=[created_task])
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_auto_applied = MagicMock(return_value=[])
        mock_threads.get_all_task_thread_map = MagicMock(return_value={})

        # 1. Create the task with a description in the body
        create_resp = await client.post(
            "/api/tasks",
            json={
                "title": "New task with summary",
                "priority": "P1",
                "description": "This is a one-line summary that should appear in the list.",
            },
        )
        assert create_resp.status_code == 200
        mock_ostk.add_task.assert_called_once_with(
            "New task with summary",
            "P1",
            description="This is a one-line summary that should appear in the list.",
        )

        # 2. GET the task list
        list_resp = await client.get("/api/tasks")

    assert list_resp.status_code == 200
    data = list_resp.json()
    tasks = data.get("tasks", [])

    # 3. Assert the created task in the response has a non-empty description
    match = next((t for t in tasks if t["id"] == "t-desc-1"), None)
    assert match is not None, "created task missing from list response"
    assert match.get("description"), "description field missing or empty in list response"
    assert match["description"] == "This is a one-line summary that should appear in the list."


# --- POST /api/tasks/{id}/close ---

@pytest.mark.asyncio
async def test_close_task(client):
    from routers.tasks import _recent_closes
    _recent_closes.clear()
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.close_task = AsyncMock(return_value="closed t-1")
        resp = await client.post("/api/tasks/t-1/close")

    assert resp.status_code == 200
    assert resp.json()["result"] == "closed t-1"
    mock_ostk.close_task.assert_called_once_with("t-1", closed_reason=None)


@pytest.mark.asyncio
async def test_close_task_batch_guard_rejects_rapid_closes(client):
    """Needle 317: more than 3 closes in 60 seconds must be rejected.

    An agent batch-closed 13 tasks on 2026-04-11 without asking Tori.
    The guard ensures bulk closes go through the audit review flow.
    """
    from routers.tasks import _recent_closes
    _recent_closes.clear()
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.close_task = AsyncMock(return_value="closed")

        # First 3 closes succeed.
        for i in range(3):
            resp = await client.post(f"/api/tasks/t-{i}/close")
            assert resp.status_code == 200, f"close {i} should succeed"

        # 4th close is rejected with 429.
        resp = await client.post("/api/tasks/t-extra/close")
        assert resp.status_code == 429
        assert "Audit review" in resp.json()["detail"]

    # ostk.close_task was called exactly 3 times, not 4.
    assert mock_ostk.close_task.call_count == 3


# --- POST /api/tasks/{id}/reopen ---

@pytest.mark.asyncio
async def test_reopen_task(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.reopen_task = AsyncMock(return_value="reopened t-1")
        resp = await client.post("/api/tasks/t-1/reopen")

    assert resp.status_code == 200
    assert resp.json()["result"] == "reopened t-1"
    mock_ostk.reopen_task.assert_called_once_with("t-1")


# --- GET /api/tasks/next ---

@pytest.mark.asyncio
async def test_next_task(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.next_task = AsyncMock(return_value="Work on lego-app")
        resp = await client.get("/api/tasks/next")

    assert resp.status_code == 200
    assert resp.json()["suggestion"] == "Work on lego-app"


# --- Goal enrichment (backward compatible) ---

@pytest.mark.asyncio
async def test_goal_enrichment_lego_app(client):
    mock_tasks = [_make_task(tags=["lego-app"])]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.json()["tasks"][0]["goal"] == "Lego App"


@pytest.mark.asyncio
async def test_goal_enrichment_no_tags(client):
    mock_tasks = [_make_task(tags=[])]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.json()["tasks"][0]["goal"] is None


@pytest.mark.asyncio
async def test_goal_enrichment_phase_tag_skipped(client):
    """Phase tags (e.g. phase-1) are milestones, not labels. They should be skipped."""
    mock_tasks = [_make_task(tags=["phase-1", "chat"])]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.json()["tasks"][0]["goal"] == "Chat"


@pytest.mark.asyncio
async def test_goal_enrichment_unknown_tag_titlecased(client):
    """Tags not in the lookup table get title-cased with hyphens replaced."""
    mock_tasks = [_make_task(tags=["my-custom-tag"])]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.json()["tasks"][0]["goal"] == "My Custom Tag"


@pytest.mark.asyncio
async def test_goal_enrichment_only_phase_tag(client):
    """If the only tag is a phase tag, goal should be None."""
    mock_tasks = [_make_task(tags=["phase-2"])]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.json()["tasks"][0]["goal"] is None


# --- Task enrichment includes label_ids ---

@pytest.mark.asyncio
async def test_tasks_include_label_ids_field(client):
    mock_tasks = [_make_task(id="t-1")]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert "label_ids" in resp.json()["tasks"][0]


# --- Error handling ---

@pytest.mark.asyncio
async def test_list_tasks_ostk_error(client):
    from services.ostk import OstkError
    with _patch_ostk_and_labels(list_tasks=AsyncMock(side_effect=OstkError("connection failed"))):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_create_task_ostk_error(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(side_effect=OstkError("failed"))
        resp = await client.post("/api/tasks", json={"title": "Bad task"})

    assert resp.status_code == 400


# --- PATCH /api/tasks/{id} (update priority) ---

@pytest.mark.asyncio
async def test_update_task_priority(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(return_value="updated t-1 priority to P0")
        resp = await client.patch("/api/tasks/t-1", json={"priority": "P0"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "updated t-1 priority to P0"
    mock_ostk.update_task_priority.assert_called_once_with("t-1", "P0", reason=None)


@pytest.mark.asyncio
async def test_update_task_priority_p2(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(return_value="updated t-5 priority to P2")
        resp = await client.patch("/api/tasks/t-5", json={"priority": "P2"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "updated t-5 priority to P2"


@pytest.mark.asyncio
async def test_update_task_no_fields(client):
    """PATCH with empty body should return 400."""
    resp = await client.patch("/api/tasks/t-1", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_task_invalid_priority(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(
            side_effect=OstkError("invalid priority 'P9', must be one of {'P0', 'P1', 'P2', 'P3'}")
        )
        resp = await client.patch("/api/tasks/t-1", json={"priority": "P9"})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_task_not_found(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(
            side_effect=OstkError("task 'no-exist' not found")
        )
        resp = await client.patch("/api/tasks/no-exist", json={"priority": "P1"})

    assert resp.status_code == 400


# --- GET /api/tasks/{id}/briefing ---

@pytest.mark.asyncio
async def test_task_briefing_returns_parsed_data(client):
    mock_briefing = {
        "task_id": "\u2192088",
        "priority": "P1",
        "status": "open",
        "title": "Add task context briefing",
        "sphere": "point=\u2192088, 1 members, 0 joints, my radius=0",
        "neighbors": [],
        "blocked_by": [],
        "unblocks": [],
        "all_blockers_resolved": False,
        "raw": "test raw output",
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.activate_task = AsyncMock(return_value=mock_briefing)
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.get("/api/tasks/088/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert "briefing" in data
    assert data["briefing"]["task_id"] == "\u2192088"
    assert data["briefing"]["title"] == "Add task context briefing"
    mock_ostk.activate_task.assert_called_once_with("088")
    # No blockers means the enriched list stays empty.
    assert data["briefing"]["blocked_by"] == []


@pytest.mark.asyncio
async def test_task_briefing_with_blockers(client):
    mock_briefing = {
        "task_id": "\u2192002",
        "priority": "P1",
        "status": "closed",
        "title": "add computer question answering",
        "sphere": None,
        "neighbors": [],
        "blocked_by": [{"text": "\u2192001 [closed] fix images", "resolved": True}],
        "unblocks": [],
        "all_blockers_resolved": True,
        "raw": "test",
    }
    blocker_task = {
        "id": "\u2192001",
        "title": "fix images",
        "description": "Patch the broken image URLs",
        "priority": "P1",
        "status": "closed",
    }
    self_task = {
        "id": "\u2192002",
        "title": "add computer question answering",
        "description": "Let the computer answer questions",
        "priority": "P1",
        "status": "closed",
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.activate_task = AsyncMock(return_value=mock_briefing)
        mock_ostk.list_tasks = AsyncMock(return_value=[blocker_task, self_task])
        resp = await client.get("/api/tasks/002/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["briefing"]["blocked_by"]) == 1
    blocker = data["briefing"]["blocked_by"][0]
    assert blocker["resolved"] is True
    assert blocker["blocker_id"] == "001"
    assert blocker["blocker_task"]["title"] == "fix images"
    assert blocker["blocker_task"]["priority"] == "P1"
    assert blocker["blocker_task"]["status"] == "closed"
    # Resolved blockers do not need an explanation, the AI is skipped.
    assert blocker["explanation"] is None
    assert data["briefing"]["all_blockers_resolved"] is True


@pytest.mark.asyncio
async def test_task_briefing_with_unresolved_blocker_enriches(client):
    """An open blocker should get the full task record and (when AI is
    available) a plain-language explanation."""
    mock_briefing = {
        "task_id": "\u2192163",
        "priority": "P1",
        "status": "open",
        "title": "Integration health dashboard",
        "sphere": None,
        "neighbors": [],
        "blocked_by": [{"text": "\u2192160 [open] Mobile-friendly layout", "resolved": False}],
        "unblocks": [],
        "all_blockers_resolved": False,
        "raw": "test",
    }
    blocker_task = {
        "id": "\u2192160",
        "title": "Mobile-friendly layout",
        "description": "Make every page work on a phone screen",
        "priority": "P1",
        "status": "open",
    }
    self_task = {
        "id": "\u2192163",
        "title": "Integration health dashboard",
        "description": "Show every integration's status at a glance",
        "priority": "P1",
        "status": "open",
    }
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("services.blocker_explanation.explain_blocker", new=AsyncMock(return_value="Phones first.")):
        mock_ostk.activate_task = AsyncMock(return_value=mock_briefing)
        mock_ostk.list_tasks = AsyncMock(return_value=[blocker_task, self_task])
        resp = await client.get("/api/tasks/163/briefing")

    assert resp.status_code == 200
    blocker = resp.json()["briefing"]["blocked_by"][0]
    assert blocker["blocker_id"] == "160"
    assert blocker["blocker_task"]["title"] == "Mobile-friendly layout"
    assert blocker["blocker_task"]["status"] == "open"
    assert blocker["blocker_task"]["priority"] == "P1"
    assert blocker["explanation"] == "Phones first."


@pytest.mark.asyncio
async def test_task_briefing_with_multiple_blockers(client):
    """Multiple unresolved blockers should each get their own enriched entry."""
    mock_briefing = {
        "task_id": "\u2192300",
        "priority": "P0",
        "status": "open",
        "title": "Ship release",
        "sphere": None,
        "neighbors": [],
        "blocked_by": [
            {"text": "\u2192100 [open] Tests", "resolved": False},
            {"text": "\u2192101 [open] Docs", "resolved": False},
        ],
        "unblocks": [],
        "all_blockers_resolved": False,
        "raw": "test",
    }
    tasks = [
        {"id": "\u2192100", "title": "Tests", "description": "Add tests", "priority": "P1", "status": "open"},
        {"id": "\u2192101", "title": "Docs", "description": "Write docs", "priority": "P2", "status": "open"},
        {"id": "\u2192300", "title": "Ship release", "description": "ship", "priority": "P0", "status": "open"},
    ]
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("services.blocker_explanation.explain_blocker", new=AsyncMock(return_value=None)):
        mock_ostk.activate_task = AsyncMock(return_value=mock_briefing)
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/tasks/300/briefing")

    assert resp.status_code == 200
    blockers = resp.json()["briefing"]["blocked_by"]
    assert len(blockers) == 2
    titles = {b["blocker_task"]["title"] for b in blockers}
    assert titles == {"Tests", "Docs"}
    # explain_blocker returned None, so explanation field is None on both.
    assert all(b["explanation"] is None for b in blockers)


@pytest.mark.asyncio
async def test_task_briefing_blocker_no_match(client):
    """When the blocker id is not in the task list, the entry still
    returns the raw text and a None blocker_task."""
    mock_briefing = {
        "task_id": "\u2192500",
        "priority": "P1",
        "status": "open",
        "title": "Some task",
        "sphere": None,
        "neighbors": [],
        "blocked_by": [{"text": "\u2192999 [open] Mystery task", "resolved": False}],
        "unblocks": [],
        "all_blockers_resolved": False,
        "raw": "test",
    }
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("services.blocker_explanation.explain_blocker", new=AsyncMock(return_value=None)):
        mock_ostk.activate_task = AsyncMock(return_value=mock_briefing)
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.get("/api/tasks/500/briefing")

    assert resp.status_code == 200
    blocker = resp.json()["briefing"]["blocked_by"][0]
    assert blocker["blocker_id"] == "999"
    assert blocker["blocker_task"] is None
    assert blocker["text"] == "\u2192999 [open] Mystery task"


@pytest.mark.asyncio
async def test_task_briefing_with_unblocks(client):
    mock_briefing = {
        "task_id": "\u2192001",
        "priority": "P1",
        "status": "closed",
        "title": "fix images",
        "sphere": None,
        "neighbors": [],
        "blocked_by": [],
        "unblocks": ["\u2192002 add computer question answering"],
        "all_blockers_resolved": False,
        "raw": "test",
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.activate_task = AsyncMock(return_value=mock_briefing)
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.get("/api/tasks/001/briefing")

    assert resp.status_code == 200
    assert len(resp.json()["briefing"]["unblocks"]) == 1


@pytest.mark.asyncio
async def test_task_briefing_not_found(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.activate_task = AsyncMock(
            side_effect=OstkError("needle 'zzz' not found")
        )
        resp = await client.get("/api/tasks/zzz/briefing")

    assert resp.status_code == 404


# --- _parse_activate unit tests ---

def test_parse_activate_basic():
    from services.ostk import OstkService
    svc = OstkService.__new__(OstkService)
    output = (
        "\u2550\u2550\u2550 ACTIVATE \u2192088 [P1|open] \u2550\u2550\u2550\n"
        "  Add task context briefing\n"
        "\n"
        "  SPHERE: point=\u2192088, 1 members, 0 joints, my radius=0\n"
        "  NEIGHBORS (0):\n"
        "\n"
        "\u2550\u2550\u2550 ready \u2550\u2550\u2550"
    )
    result = svc._parse_activate(output)
    assert result["task_id"] == "\u2192088"
    assert result["priority"] == "P1"
    assert result["status"] == "open"
    assert result["title"] == "Add task context briefing"
    assert result["sphere"] == "point=\u2192088, 1 members, 0 joints, my radius=0"
    assert result["neighbors"] == []
    assert result["blocked_by"] == []
    assert result["unblocks"] == []
    assert result["all_blockers_resolved"] is False


def test_parse_activate_with_blockers():
    from services.ostk import OstkService
    svc = OstkService.__new__(OstkService)
    output = (
        "\u2550\u2550\u2550 ACTIVATE \u2192002 [P1|closed] \u2550\u2550\u2550\n"
        "  add computer question answering to guess who\n"
        "\n"
        "  BLOCKED BY:\n"
        "    \u2713 \u2192001 [closed] fix mega brand set images\n"
        "    \u2192 all blockers resolved \u2713\n"
        "\n"
        "\u2550\u2550\u2550 ready \u2550\u2550\u2550"
    )
    result = svc._parse_activate(output)
    assert result["task_id"] == "\u2192002"
    assert len(result["blocked_by"]) == 1
    assert result["blocked_by"][0]["resolved"] is True
    assert "\u2192001" in result["blocked_by"][0]["text"]
    assert result["all_blockers_resolved"] is True


def test_parse_activate_with_unresolved_blockers():
    """ostk uses a ballot X (\u2717) for still-open blockers. The parser must
    recognize that marker or the blocker is silently dropped and the UI
    shows only the warning footer."""
    from services.ostk import OstkService
    svc = OstkService.__new__(OstkService)
    output = (
        "\u2550\u2550\u2550 ACTIVATE \u2192163 [P1|open] \u2550\u2550\u2550\n"
        "  Integration health dashboard\n"
        "\n"
        "  BLOCKED BY:\n"
        "    \u2717 \u2192160 [open] Mobile-friendly layout\n"
        "    \u2192 \u26a0 unresolved blockers \u2014 may not be ready\n"
        "\n"
        "\u2550\u2550\u2550 ready \u2550\u2550\u2550"
    )
    result = svc._parse_activate(output)
    assert result["task_id"] == "\u2192163"
    # Only the real blocker row should come through, not the warning footer.
    assert len(result["blocked_by"]) == 1
    assert result["blocked_by"][0]["resolved"] is False
    assert "\u2192160" in result["blocked_by"][0]["text"]
    assert "Mobile-friendly layout" in result["blocked_by"][0]["text"]
    assert result["all_blockers_resolved"] is False


def test_parse_activate_ignores_warning_footer_line():
    """The \u2192 \u26a0 unresolved blockers footer is a status note, not an item."""
    from services.ostk import OstkService
    svc = OstkService.__new__(OstkService)
    output = (
        "\u2550\u2550\u2550 ACTIVATE \u2192999 [P1|open] \u2550\u2550\u2550\n"
        "  some task\n"
        "\n"
        "  BLOCKED BY:\n"
        "    \u2192 \u26a0 unresolved blockers \u2014 may not be ready\n"
        "\n"
        "\u2550\u2550\u2550 ready \u2550\u2550\u2550"
    )
    result = svc._parse_activate(output)
    assert result["blocked_by"] == []


def test_parse_activate_with_unblocks():
    from services.ostk import OstkService
    svc = OstkService.__new__(OstkService)
    output = (
        "\u2550\u2550\u2550 ACTIVATE \u2192001 [P1|closed] \u2550\u2550\u2550\n"
        "  fix mega brand set images\n"
        "\n"
        "  UNBLOCKS:\n"
        "    \u2192 \u2192002 add computer question answering to guess who\n"
        "\n"
        "\u2550\u2550\u2550 ready \u2550\u2550\u2550"
    )
    result = svc._parse_activate(output)
    assert result["task_id"] == "\u2192001"
    assert len(result["unblocks"]) == 1
    assert "\u2192002" in result["unblocks"][0]


def test_parse_activate_empty_output():
    from services.ostk import OstkService
    svc = OstkService.__new__(OstkService)
    result = svc._parse_activate("")
    assert result["task_id"] == ""
    assert result["title"] == ""
    assert result["neighbors"] == []
    assert result["blocked_by"] == []
    assert result["unblocks"] == []


# --- POST /api/tasks/{id}/commit ---

@pytest.mark.asyncio
async def test_commit_for_task(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.commit = AsyncMock(return_value="committed abc1234 for needle t-1")
        resp = await client.post(
            "/api/tasks/t-1/commit",
            json={"message": "fix login flow"},
        )

    assert resp.status_code == 200
    assert resp.json()["result"] == "committed abc1234 for needle t-1"
    mock_ostk.commit.assert_called_once_with(
        message="fix login flow",
        needle="t-1",
        spec=None,
        section=None,
        agent=None,
    )


@pytest.mark.asyncio
async def test_commit_for_task_with_all_options(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.commit = AsyncMock(return_value="committed def5678 for needle t-5")
        resp = await client.post(
            "/api/tasks/t-5/commit",
            json={
                "message": "add search feature",
                "spec": "search",
                "section": "backend",
                "agent": "builder-01",
            },
        )

    assert resp.status_code == 200
    mock_ostk.commit.assert_called_once_with(
        message="add search feature",
        needle="t-5",
        spec="search",
        section="backend",
        agent="builder-01",
    )


@pytest.mark.asyncio
async def test_commit_for_task_ostk_error(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.commit = AsyncMock(side_effect=OstkError("nothing to commit"))
        resp = await client.post(
            "/api/tasks/t-1/commit",
            json={"message": "empty commit"},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_commit_for_task_missing_message(client):
    """Commit without a message should be rejected by Pydantic validation."""
    resp = await client.post("/api/tasks/t-1/commit", json={})
    assert resp.status_code == 422


# --- POST /api/commits (standalone) ---

@pytest.mark.asyncio
async def test_commit_standalone_with_needle(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.commit = AsyncMock(return_value="committed 1234abc for needle t-3")
        resp = await client.post(
            "/api/commits",
            json={"message": "refactor auth module", "needle": "t-3"},
        )

    assert resp.status_code == 200
    mock_ostk.commit.assert_called_once_with(
        message="refactor auth module",
        needle="t-3",
        spec=None,
        section=None,
        agent=None,
    )


@pytest.mark.asyncio
async def test_commit_standalone_without_needle(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.commit = AsyncMock(return_value="committed abc0000")
        resp = await client.post(
            "/api/commits",
            json={"message": "cleanup formatting"},
        )

    assert resp.status_code == 200
    mock_ostk.commit.assert_called_once_with(
        message="cleanup formatting",
        needle=None,
        spec=None,
        section=None,
        agent=None,
    )


# --- OstkService.commit unit tests ---

@pytest.mark.asyncio
async def test_ostk_service_commit_builds_correct_args():
    """Verify the commit method builds the right CLI arguments."""
    from services.ostk import OstkService

    svc = OstkService.__new__(OstkService)
    calls = []

    async def fake_run(*args):
        calls.append(args)
        return "committed OK"

    svc._run = fake_run

    await svc.commit(message="test commit", needle="092")
    assert calls == [("commit", "-m", "test commit", "--needle", "092")]


@pytest.mark.asyncio
async def test_ostk_service_commit_all_flags():
    """Verify the commit method includes all optional flags when provided."""
    from services.ostk import OstkService

    svc = OstkService.__new__(OstkService)
    calls = []

    async def fake_run(*args):
        calls.append(args)
        return "committed OK"

    svc._run = fake_run

    await svc.commit(
        message="test", needle="042", spec="auth", section="login", agent="agent-1"
    )
    assert calls == [(
        "commit", "-m", "test",
        "--needle", "042",
        "--spec", "auth",
        "--section", "login",
        "--agent", "agent-1",
    )]


@pytest.mark.asyncio
async def test_ostk_service_commit_no_optional_flags():
    """Verify commit without optional flags only sends message."""
    from services.ostk import OstkService

    svc = OstkService.__new__(OstkService)
    calls = []

    async def fake_run(*args):
        calls.append(args)
        return "committed OK"

    svc._run = fake_run

    await svc.commit(message="just a message")
    assert calls == [("commit", "-m", "just a message")]


# --- POST /api/tasks/{id}/link (create dependency) ---

@pytest.mark.asyncio
async def test_link_tasks_blocks(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.link_tasks = AsyncMock(return_value="linked t-1 blocks t-2")
        resp = await client.post(
            "/api/tasks/t-1/link",
            json={"target": "t-2", "relation": "blocks"},
        )

    assert resp.status_code == 200
    assert resp.json()["result"] == "linked t-1 blocks t-2"
    mock_ostk.link_tasks.assert_called_once_with("t-1", "blocks", "t-2")


@pytest.mark.asyncio
async def test_link_tasks_depends_on(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.link_tasks = AsyncMock(return_value="linked t-2 depends-on t-1")
        resp = await client.post(
            "/api/tasks/t-2/link",
            json={"target": "t-1", "relation": "depends-on"},
        )

    assert resp.status_code == 200
    assert resp.json()["result"] == "linked t-2 depends-on t-1"
    mock_ostk.link_tasks.assert_called_once_with("t-2", "depends-on", "t-1")


@pytest.mark.asyncio
async def test_link_tasks_default_relation_is_blocks(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.link_tasks = AsyncMock(return_value="linked t-1 blocks t-3")
        resp = await client.post(
            "/api/tasks/t-1/link",
            json={"target": "t-3"},
        )

    assert resp.status_code == 200
    mock_ostk.link_tasks.assert_called_once_with("t-1", "blocks", "t-3")


@pytest.mark.asyncio
async def test_link_tasks_invalid_relation(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.link_tasks = AsyncMock(
            side_effect=OstkError("invalid relation 'foo'")
        )
        resp = await client.post(
            "/api/tasks/t-1/link",
            json={"target": "t-2", "relation": "foo"},
        )

    assert resp.status_code == 400


# --- DELETE /api/tasks/{id}/link (remove dependency) ---

@pytest.mark.asyncio
async def test_unlink_tasks(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.unlink_tasks = AsyncMock(return_value="unlinked t-1 blocks t-2")
        resp = await client.delete("/api/tasks/t-1/link?target=t-2&relation=blocks")

    assert resp.status_code == 200
    assert resp.json()["result"] == "unlinked t-1 blocks t-2"
    mock_ostk.unlink_tasks.assert_called_once_with("t-1", "blocks", "t-2")


@pytest.mark.asyncio
async def test_unlink_tasks_depends_on(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.unlink_tasks = AsyncMock(return_value="unlinked t-2 depends-on t-1")
        resp = await client.delete("/api/tasks/t-2/link?target=t-1&relation=depends-on")

    assert resp.status_code == 200
    mock_ostk.unlink_tasks.assert_called_once_with("t-2", "depends-on", "t-1")


@pytest.mark.asyncio
async def test_unlink_tasks_not_found(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.unlink_tasks = AsyncMock(
            side_effect=OstkError("link not found: t-1 blocks t-99")
        )
        resp = await client.delete("/api/tasks/t-1/link?target=t-99&relation=blocks")

    assert resp.status_code == 400


# --- GET /api/tasks/{id}/dependencies ---

@pytest.mark.asyncio
async def test_get_dependencies(client):
    mock_deps = {
        "task_id": "t-1",
        "blocks": [{"id": "t-2", "title": "Second task"}],
        "depends_on": [{"id": "t-0", "title": "Zeroth task"}],
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.get_dependencies = AsyncMock(return_value=mock_deps)
        resp = await client.get("/api/tasks/t-1/dependencies")

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "t-1"
    assert len(data["blocks"]) == 1
    assert data["blocks"][0]["id"] == "t-2"
    assert len(data["depends_on"]) == 1
    assert data["depends_on"][0]["id"] == "t-0"


@pytest.mark.asyncio
async def test_get_dependencies_empty(client):
    mock_deps = {"task_id": "t-5", "blocks": [], "depends_on": []}
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.get_dependencies = AsyncMock(return_value=mock_deps)
        resp = await client.get("/api/tasks/t-5/dependencies")

    assert resp.status_code == 200
    assert resp.json()["blocks"] == []
    assert resp.json()["depends_on"] == []


@pytest.mark.asyncio
async def test_get_dependencies_not_found(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.get_dependencies = AsyncMock(
            side_effect=OstkError("task 'zzz' not found")
        )
        resp = await client.get("/api/tasks/zzz/dependencies")

    assert resp.status_code == 404


# --- OstkService.get_dependencies unit test ---

@pytest.mark.asyncio
async def test_ostk_service_get_dependencies():
    """Unit test for OstkService.get_dependencies parsing."""
    from services.ostk import OstkService
    svc = OstkService.__new__(OstkService)
    mock_tasks = [
        {"id": "t-1", "title": "First", "blocks": ["t-2"], "depends_on": ["t-0"]},
        {"id": "t-0", "title": "Zeroth"},
        {"id": "t-2", "title": "Second"},
    ]
    svc.list_tasks = AsyncMock(return_value=mock_tasks)
    result = await svc.get_dependencies("t-1")

    assert result["task_id"] == "t-1"
    assert result["blocks"] == [{"id": "t-2", "title": "Second"}]
    assert result["depends_on"] == [{"id": "t-0", "title": "Zeroth"}]


@pytest.mark.asyncio
async def test_ostk_service_get_dependencies_no_links():
    """Task with no blocks or depends_on returns empty lists."""
    from services.ostk import OstkService
    svc = OstkService.__new__(OstkService)
    svc.list_tasks = AsyncMock(return_value=[
        {"id": "t-5", "title": "Standalone"},
    ])
    result = await svc.get_dependencies("t-5")

    assert result["blocks"] == []
    assert result["depends_on"] == []


# --- OstkService.link_tasks validation ---

@pytest.mark.asyncio
async def test_ostk_service_link_invalid_relation():
    """link_tasks rejects invalid relations before calling CLI."""
    from services.ostk import OstkService, OstkError
    svc = OstkService.__new__(OstkService)
    with pytest.raises(OstkError, match="invalid relation"):
        await svc.link_tasks("t-1", "foo", "t-2")


# --- Tasks list includes blocks/depends_on when present ---

@pytest.mark.asyncio
async def test_list_tasks_includes_dependency_fields(client):
    """blocks and depends_on from ostk pass through to the API response."""
    mock_tasks = [
        _make_task(id="t-1") | {"blocks": ["\u2192002"]},
        _make_task(id="t-2") | {"depends_on": ["\u2192001"]},
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert tasks[0]["blocks"] == ["\u2192002"]
    assert tasks[1]["depends_on"] == ["\u2192001"]


# --- GET /api/tasks/{id}/trace ---

@pytest.mark.asyncio
async def test_task_trace_returns_parsed_data(client):
    mock_trace = {
        "headline": "\u2192093: Add attribution tracing [P2, open]",
        "specs": [],
        "drafts": [],
        "agentfiles": [],
        "depends_on": [],
        "blocks": ["\u2192002"],
        "commits": ["abc1234 Fix the thing"],
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.trace = AsyncMock(return_value=mock_trace)
        resp = await client.get("/api/tasks/093/trace")

    assert resp.status_code == 200
    data = resp.json()
    assert "trace" in data
    assert data["trace"]["headline"] == "\u2192093: Add attribution tracing [P2, open]"
    assert data["trace"]["blocks"] == ["\u2192002"]
    assert data["trace"]["commits"] == ["abc1234 Fix the thing"]
    mock_ostk.trace.assert_called_once_with("093")


@pytest.mark.asyncio
async def test_task_trace_empty_chain(client):
    mock_trace = {
        "headline": "\u219209: Some task [P1, open]",
        "specs": [],
        "drafts": [],
        "agentfiles": [],
        "depends_on": [],
        "blocks": [],
        "commits": [],
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.trace = AsyncMock(return_value=mock_trace)
        resp = await client.get("/api/tasks/09/trace")

    assert resp.status_code == 200
    data = resp.json()["trace"]
    assert data["specs"] == []
    assert data["commits"] == []


@pytest.mark.asyncio
async def test_task_trace_not_found(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.trace = AsyncMock(
            side_effect=OstkError("needle 'zzz' not found")
        )
        resp = await client.get("/api/tasks/zzz/trace")

    assert resp.status_code == 404


# --- _parse_trace unit tests ---

def test_parse_trace_full():
    from services.ostk import OstkService
    raw = (
        "\u2192093: Add attribution tracing [P2, open]\n"
        "  specs: design/trace.md\n"
        "  drafts: (none)\n"
        "  agentfiles: .ostk/agents/builder-01.jsonl\n"
        "  depends_on: \u2192080\n"
        "  blocks: \u2192002\n"
        "  commits: abc1234 Fix the thing\n"
        "    def5678 Add trace endpoint\n"
    )
    result = OstkService._parse_trace(raw)
    assert result["headline"] == "\u2192093: Add attribution tracing [P2, open]"
    assert result["specs"] == ["design/trace.md"]
    assert result["drafts"] == []
    assert result["agentfiles"] == [".ostk/agents/builder-01.jsonl"]
    assert result["depends_on"] == ["\u2192080"]
    assert result["blocks"] == ["\u2192002"]
    assert result["commits"] == ["abc1234 Fix the thing", "def5678 Add trace endpoint"]


def test_parse_trace_all_none():
    from services.ostk import OstkService
    raw = (
        "\u2192093: Add attribution tracing [P2, open]\n"
        "  specs: (none)\n"
        "  drafts: (none)\n"
        "  agentfiles: (none)\n"
        "  depends_on: (none)\n"
        "  blocks: (none)\n"
        "  commits: (none)\n"
    )
    result = OstkService._parse_trace(raw)
    assert result["headline"] == "\u2192093: Add attribution tracing [P2, open]"
    assert result["specs"] == []
    assert result["drafts"] == []
    assert result["agentfiles"] == []
    assert result["depends_on"] == []
    assert result["blocks"] == []
    assert result["commits"] == []


def test_parse_trace_empty_input():
    from services.ostk import OstkService
    result = OstkService._parse_trace("")
    assert result["headline"] == ""
    assert result["specs"] == []
    assert result["commits"] == []


def test_parse_trace_multiple_blocks():
    from services.ostk import OstkService
    raw = (
        "\u2192001: Fix images [P1, closed]\n"
        "  specs: (none)\n"
        "  drafts: (none)\n"
        "  agentfiles: (none)\n"
        "  depends_on: (none)\n"
        "  blocks: \u2192002\n"
        "    \u2192003\n"
        "  commits: (none)\n"
    )
    result = OstkService._parse_trace(raw)
    assert result["blocks"] == ["\u2192002", "\u2192003"]


# --- GET /api/tasks/health (Health Check / Refine) ---

@pytest.mark.asyncio
async def test_health_check_returns_summary(client):
    """The health check endpoint should return tasks, issues, and summary."""
    mock_result = {
        "tasks": [
            {"id": "→001", "priority": "P1", "status": "open", "title": "Fix bug", "sphere": None, "degree": 0, "joints": []},
        ],
        "issues": [
            {"type": "no_description", "severity": "info", "message": "Task →001 has no description", "task_ids": ["→001"]},
            {"type": "isolated", "severity": "info", "message": "Task →001 is not linked to any other tasks", "task_ids": ["→001"]},
        ],
        "summary": {"total": 1, "issues": 2, "connected": 0, "isolated": 1},
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.refine_tasks = AsyncMock(return_value=mock_result)
        resp = await client.get("/api/tasks/health")

    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "issues" in data
    assert "tasks" in data
    assert data["summary"]["total"] == 1
    assert data["summary"]["issues"] == 2
    mock_ostk.refine_tasks.assert_called_once()


@pytest.mark.asyncio
async def test_health_check_empty_tasks(client):
    """Health check with no open tasks should return empty results."""
    mock_result = {
        "tasks": [],
        "issues": [],
        "summary": {"total": 0, "issues": 0, "connected": 0, "isolated": 0},
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.refine_tasks = AsyncMock(return_value=mock_result)
        resp = await client.get("/api/tasks/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total"] == 0
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_health_check_ostk_error(client):
    """Health check should return 500 when ostk fails."""
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.refine_tasks = AsyncMock(side_effect=OstkError("refine failed"))
        resp = await client.get("/api/tasks/health")

    assert resp.status_code == 500


# --- GET /api/tasks/duplicates ---

@pytest.mark.asyncio
async def test_find_duplicates_returns_similar_pairs(client):
    """Two tasks with nearly identical titles should be flagged as duplicates."""
    mock_tasks = [
        _make_task(id="t-1", title="Fix the login bug"),
        _make_task(id="t-2", title="Fix the login bugs"),
        _make_task(id="t-3", title="Buy groceries"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks/duplicates")

    assert resp.status_code == 200
    data = resp.json()
    assert "duplicates" in data
    assert len(data["duplicates"]) == 1
    pair = data["duplicates"][0]
    ids = {pair["task_a"]["id"], pair["task_b"]["id"]}
    assert ids == {"t-1", "t-2"}
    assert pair["similarity"] > 0.8
    mock_ostk.list_tasks.assert_called_once_with(status="open")


@pytest.mark.asyncio
async def test_find_duplicates_empty_when_all_unique(client):
    """No pairs should be returned when all titles are clearly distinct."""
    mock_tasks = [
        _make_task(id="t-1", title="Buy groceries"),
        _make_task(id="t-2", title="Write quarterly report"),
        _make_task(id="t-3", title="Call the dentist"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks/duplicates")

    assert resp.status_code == 200
    assert resp.json()["duplicates"] == []


@pytest.mark.asyncio
async def test_find_duplicates_sorted_by_similarity(client):
    """Pairs should come back sorted with the strongest match first."""
    mock_tasks = [
        _make_task(id="t-1", title="Refactor the user profile page"),
        _make_task(id="t-2", title="Refactor the user profile pages"),
        _make_task(id="t-3", title="Refactor the user profile"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks/duplicates")

    assert resp.status_code == 200
    duplicates = resp.json()["duplicates"]
    assert len(duplicates) >= 1
    # Highest similarity must come first.
    for i in range(len(duplicates) - 1):
        assert duplicates[i]["similarity"] >= duplicates[i + 1]["similarity"]


# --- OstkService._parse_refine unit tests ---

def test_parse_refine_basic():
    """Parse a simple refine output with one task."""
    from services.ostk import OstkService
    raw = (
        "── refine 1 needle(s) ──\n"
        "\n"
        "→083 [P1|open] Add task dependencies\n"
        "  sphere: 3 (1 needles, point=→083)\n"
        "  radius from point: 0\n"
        "  degree: 0 joints\n"
    )
    result = OstkService._parse_refine(OstkService(), raw)
    assert len(result) == 1
    assert result[0]["id"] == "→083"
    assert result[0]["priority"] == "P1"
    assert result[0]["status"] == "open"
    assert result[0]["title"] == "Add task dependencies"
    assert result[0]["degree"] == 0
    assert result[0]["joints"] == []
    assert result[0]["sphere"]["id"] == 3
    assert result[0]["sphere"]["size"] == 1
    assert result[0]["sphere"]["point"] == "→083"


def test_parse_refine_with_joints():
    """Parse refine output that includes connected tasks (joints)."""
    from services.ostk import OstkService
    raw = (
        "── refine 1 needle(s) ──\n"
        "\n"
        "→084 [P1|open] Build smart focus\n"
        "  sphere: 1 (3 needles, point=→084)\n"
        "  radius from point: 0\n"
        "  degree: 2 joints\n"
        "  joints:\n"
        "    ↔ →087 Build activity timeline\n"
        "    ↔ →089 Build session diff view\n"
    )
    result = OstkService._parse_refine(OstkService(), raw)
    assert len(result) == 1
    assert result[0]["degree"] == 2
    assert len(result[0]["joints"]) == 2
    assert result[0]["joints"][0] == {"id": "→087", "title": "Build activity timeline"}
    assert result[0]["joints"][1] == {"id": "→089", "title": "Build session diff view"}


def test_parse_refine_multiple_tasks():
    """Parse refine output with multiple tasks."""
    from services.ostk import OstkService
    raw = (
        "── refine 2 needle(s) ──\n"
        "\n"
        "→083 [P1|open] Task one\n"
        "  sphere: 1 (1 needles, point=→083)\n"
        "  degree: 0 joints\n"
        "\n"
        "→084 [P2|open] Task two\n"
        "  sphere: 2 (1 needles, point=→084)\n"
        "  degree: 0 joints\n"
    )
    result = OstkService._parse_refine(OstkService(), raw)
    assert len(result) == 2
    assert result[0]["id"] == "→083"
    assert result[1]["id"] == "→084"
    assert result[1]["priority"] == "P2"


# --- OstkService._detect_issues unit tests ---

def test_detect_issues_finds_duplicates():
    """Detect duplicate titles in open tasks."""
    from services.ostk import OstkService
    refined = [{"id": "→001", "degree": 0}, {"id": "→002", "degree": 0}]
    open_tasks = [
        {"id": "→001", "title": "Fix the login bug", "description": "some desc"},
        {"id": "→002", "title": "Fix the login bug", "description": "other desc"},
    ]
    issues = OstkService._detect_issues(OstkService(), refined, open_tasks)
    duplicate_issues = [i for i in issues if i["type"] == "duplicate"]
    assert len(duplicate_issues) == 1
    assert "→001" in duplicate_issues[0]["task_ids"]
    assert "→002" in duplicate_issues[0]["task_ids"]


def test_detect_issues_finds_missing_descriptions():
    """Detect tasks with no description."""
    from services.ostk import OstkService
    refined = [{"id": "→001", "degree": 1}]
    open_tasks = [
        {"id": "→001", "title": "Some task", "description": ""},
    ]
    issues = OstkService._detect_issues(OstkService(), refined, open_tasks)
    no_desc_issues = [i for i in issues if i["type"] == "no_description"]
    assert len(no_desc_issues) == 1
    assert no_desc_issues[0]["task_ids"] == ["→001"]


def test_detect_issues_finds_isolated_tasks():
    """Detect tasks with no connections."""
    from services.ostk import OstkService
    refined = [
        {"id": "→001", "degree": 0},
        {"id": "→002", "degree": 2},
    ]
    open_tasks = [
        {"id": "→001", "title": "Isolated task", "description": "has desc"},
        {"id": "→002", "title": "Connected task", "description": "has desc"},
    ]
    issues = OstkService._detect_issues(OstkService(), refined, open_tasks)
    isolated_issues = [i for i in issues if i["type"] == "isolated"]
    assert len(isolated_issues) == 1
    assert isolated_issues[0]["task_ids"] == ["→001"]


def test_detect_issues_no_issues_when_clean():
    """No issues should be detected when all tasks are well-formed."""
    from services.ostk import OstkService
    refined = [{"id": "→001", "degree": 1}]
    open_tasks = [
        {"id": "→001", "title": "Good task", "description": "Has a description"},
    ]
    issues = OstkService._detect_issues(OstkService(), refined, open_tasks)
    assert len(issues) == 0


# --- DELETE /api/tasks/{id} ---

@pytest.mark.asyncio
async def test_delete_task(client):
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.threads_store") as mock_ts:
        mock_ostk.delete_task = AsyncMock(return_value="deleted t-1")
        resp = await client.delete("/api/tasks/t-1")

    assert resp.status_code == 200
    assert resp.json()["result"] == "deleted t-1"
    mock_ostk.delete_task.assert_called_once_with("t-1")
    mock_tls.remove_task.assert_called_once_with("t-1")
    mock_ts.remove_task_from_all_threads.assert_called_once_with("t-1")


@pytest.mark.asyncio
async def test_delete_task_not_found(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.delete_task = AsyncMock(side_effect=OstkError("task 't-99' not found"))
        resp = await client.delete("/api/tasks/t-99")

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_delete_task_cleans_up_labels_and_threads(client):
    """Deleting a task removes its label assignments and thread memberships."""
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.threads_store") as mock_ts:
        mock_ostk.delete_task = AsyncMock(return_value="deleted t-5")
        resp = await client.delete("/api/tasks/t-5")

    assert resp.status_code == 200
    mock_tls.remove_task.assert_called_once_with("t-5")
    mock_ts.remove_task_from_all_threads.assert_called_once_with("t-5")


# --- ostk.delete_task unit tests ---

@pytest.mark.asyncio
async def test_ostk_delete_task_removes_entry(tmp_path):
    """delete_task removes the matching line from issues.jsonl."""
    from services.ostk import OstkService

    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True)
    issues_file = issues_dir / "issues.jsonl"
    issues_file.write_text(
        '{"id": "t-1", "title": "First", "status": "open"}\n'
        '{"id": "t-2", "title": "Second", "status": "open"}\n'
    )

    svc = OstkService(cwd=str(tmp_path))
    result = await svc.delete_task("t-1")

    assert result == "deleted t-1"
    remaining = issues_file.read_text()
    assert "t-1" not in remaining
    assert "t-2" in remaining


@pytest.mark.asyncio
async def test_ostk_delete_task_not_found_raises(tmp_path):
    """delete_task raises OstkError when the task ID does not exist."""
    from services.ostk import OstkService, OstkError

    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True)
    issues_file = issues_dir / "issues.jsonl"
    issues_file.write_text('{"id": "t-1", "title": "Only task", "status": "open"}\n')

    svc = OstkService(cwd=str(tmp_path))
    with pytest.raises(OstkError, match="not found"):
        await svc.delete_task("t-99")


@pytest.mark.asyncio
async def test_ostk_delete_task_last_task_leaves_empty_file(tmp_path):
    """Deleting the only task leaves issues.jsonl empty (not corrupted)."""
    from services.ostk import OstkService

    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True)
    issues_file = issues_dir / "issues.jsonl"
    issues_file.write_text('{"id": "t-1", "title": "Only task", "status": "open"}\n')

    svc = OstkService(cwd=str(tmp_path))
    await svc.delete_task("t-1")

    assert issues_file.read_text() == ""


@pytest.mark.asyncio
async def test_ostk_delete_task_missing_file_raises(tmp_path):
    """delete_task raises OstkError when issues.jsonl does not exist."""
    from services.ostk import OstkService, OstkError

    svc = OstkService(cwd=str(tmp_path))
    with pytest.raises(OstkError, match="issues.jsonl not found"):
        await svc.delete_task("t-1")


# --- Auto-label suggestion on task create ---------------------------------

@pytest.mark.asyncio
async def test_create_task_triggers_auto_label_suggestion(client):
    """Creating a task should kick off the label suggester and apply labels."""
    import asyncio as _asyncio
    fake_label = {"id": "l-bug", "name": "bug", "color": "#f97316", "is_new": False}

    async def fake_suggest(title, desc, labels):
        return [fake_label]

    captured = {}

    def fake_replace(task_id, label_ids):
        captured["task_id"] = task_id
        captured["label_ids"] = list(label_ids)
        return list(label_ids)

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("services.task_labeling.suggest_labels", new=fake_suggest), \
         patch("services.task_labeling.task_labels_store") as mock_tls, \
         patch("services.task_labeling.settings_store") as mock_settings, \
         patch("services.task_labeling.labels_store") as mock_labels:
        mock_ostk.add_task = AsyncMock(return_value="added \u2192201: New thing [P1]")
        mock_settings.get = MagicMock(return_value=True)
        mock_labels.list_labels = MagicMock(return_value=[])
        mock_tls.replace_auto_applied = MagicMock(side_effect=fake_replace)

        resp = await client.post(
            "/api/tasks",
            json={"title": "Crash on launch", "priority": "P0", "description": "the app dies"},
        )
        # Give the background task a chance to run.
        await _asyncio.sleep(0.05)

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "\u2192201"
    assert captured.get("task_id") == "\u2192201"
    assert captured.get("label_ids") == ["l-bug"]


@pytest.mark.asyncio
async def test_create_task_skips_auto_label_when_setting_off(client):
    """If auto_label_tasks is False, the suggester must not be called."""
    import asyncio as _asyncio
    suggest_called = {"count": 0}

    async def fake_suggest(title, desc, labels):
        suggest_called["count"] += 1
        return []

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("services.task_labeling.suggest_labels", new=fake_suggest), \
         patch("services.task_labeling.settings_store") as mock_settings:
        mock_ostk.add_task = AsyncMock(return_value="added \u2192202: thing [P1]")
        mock_settings.get = MagicMock(return_value=False)

        resp = await client.post("/api/tasks", json={"title": "thing"})
        await _asyncio.sleep(0.05)

    assert resp.status_code == 200
    assert suggest_called["count"] == 0


@pytest.mark.asyncio
async def test_remove_auto_applied_label_marks_rejected(client):
    """Removing an auto-applied label should call remove_label with mark_rejected=True."""
    captured = {}
    with patch("routers.tasks.task_labels_store") as mock_tls:
        mock_tls.is_auto_applied = MagicMock(return_value=True)
        def fake_remove(task_id, label_id, mark_rejected=False):
            captured["mark_rejected"] = mark_rejected
            return []
        mock_tls.remove_label = MagicMock(side_effect=fake_remove)

        resp = await client.delete("/api/tasks/t-1/labels/l-bug")

    assert resp.status_code == 200
    assert captured["mark_rejected"] is True


@pytest.mark.asyncio
async def test_remove_manual_label_does_not_mark_rejected(client):
    """A manually added label should be removed without rejecting it."""
    captured = {}
    with patch("routers.tasks.task_labels_store") as mock_tls:
        mock_tls.is_auto_applied = MagicMock(return_value=False)
        def fake_remove(task_id, label_id, mark_rejected=False):
            captured["mark_rejected"] = mark_rejected
            return []
        mock_tls.remove_label = MagicMock(side_effect=fake_remove)

        resp = await client.delete("/api/tasks/t-1/labels/l-bug")

    assert resp.status_code == 200
    assert captured["mark_rejected"] is False


def test_extract_task_id_parses_added_string():
    """The id parser should pull the raw needle id from the ostk add output."""
    from routers.tasks import _extract_task_id
    assert _extract_task_id("added \u2192201: my task [P1]") == "\u2192201"
    assert _extract_task_id("added abc123: another task [P0]") == "abc123"
    assert _extract_task_id("nonsense") is None
    assert _extract_task_id("") is None


# --- Shared auto-label helper regression tests (needle →137) --------------
# These tests cover the bug where tasks created from ideas (and other paths
# that do not hit POST /api/tasks) skipped auto-labeling. The fix extracted
# the auto-label logic into services/task_labeling.py so every task
# creation path can share it.


@pytest.mark.asyncio
async def test_schedule_auto_labels_applies_labels_for_any_path():
    """The shared helper must label a task regardless of which path creates it."""
    import asyncio as _asyncio
    from services import task_labeling

    fake_label = {"id": "l-growth", "name": "growth", "color": "#10b981", "is_new": False}

    async def fake_suggest(title, desc, labels):
        return [fake_label]

    captured = {}

    def fake_replace(task_id, label_ids):
        captured["task_id"] = task_id
        captured["label_ids"] = list(label_ids)
        return list(label_ids)

    with patch("services.task_labeling.suggest_labels", new=fake_suggest), \
         patch("services.task_labeling.task_labels_store") as mock_tls, \
         patch("services.task_labeling.settings_store") as mock_settings, \
         patch("services.task_labeling.labels_store") as mock_labels:
        mock_settings.get = MagicMock(return_value=True)
        mock_labels.list_labels = MagicMock(return_value=[])
        mock_tls.replace_auto_applied = MagicMock(side_effect=fake_replace)

        task_labeling.schedule_auto_labels("\u2192500", "Plan retreat", "")
        # Give the background task a chance to run.
        await _asyncio.sleep(0.05)

    assert captured.get("task_id") == "\u2192500"
    assert captured.get("label_ids") == ["l-growth"]


@pytest.mark.asyncio
async def test_schedule_auto_labels_noop_when_id_missing():
    """If the task id could not be parsed, the helper must not crash."""
    from services import task_labeling

    called = {"n": 0}

    async def fake_suggest(title, desc, labels):
        called["n"] += 1
        return []

    with patch("services.task_labeling.suggest_labels", new=fake_suggest):
        task_labeling.schedule_auto_labels(None, "title", "")
        task_labeling.schedule_auto_labels("", "title", "")

    assert called["n"] == 0


def test_task_labeling_extract_task_id_matches_router_helper():
    """The shared extractor and the router re-export must behave the same."""
    from services.task_labeling import extract_task_id as shared
    from routers.tasks import _extract_task_id as router_alias

    samples = [
        ("added \u2192201: a task [P1]", "\u2192201"),
        ("added abc: x [P0]", "abc"),
        ("nothing", None),
        ("", None),
    ]
    for text, expected in samples:
        assert shared(text) == expected
        assert router_alias(text) == expected


# --- Regression: issues.jsonl deduplication ---

@pytest.mark.asyncio
async def test_list_tasks_deduplicates_by_last_occurrence(tmp_path):
    """When the same task ID appears twice in issues.jsonl (open then closed),
    list_tasks must return it only once with the status from the last entry."""
    from unittest.mock import patch as _patch
    from services.ostk import OstkService

    # Simulate ostk CLI returning two entries for the same id: open first,
    # then closed (the append-only log pattern).
    duplicate_entries = [
        {"id": "\u2192140", "title": "e2e-smoke-task", "status": "open", "priority": "P2", "tags": []},
        {"id": "\u2192140", "title": "e2e-smoke-task", "status": "closed", "priority": "P2", "tags": []},
        {"id": "\u2192141", "title": "Another task", "status": "open", "priority": "P1", "tags": []},
    ]

    svc = OstkService.__new__(OstkService)
    svc.cwd = str(tmp_path)

    with _patch.object(svc, "_run_json", return_value=duplicate_entries):
        result = await svc.list_tasks()

    ids = [t["id"] for t in result]
    assert ids.count("\u2192140") == 1, "duplicate id must appear only once"
    assert ids.count("\u2192141") == 1

    task_140 = next(t for t in result if t["id"] == "\u2192140")
    assert task_140["status"] == "closed", "last occurrence (closed) must win"


# --- POST /api/tasks/{id}/labels/auto ---

@pytest.mark.asyncio
async def test_auto_label_task_assigns_labels(client):
    """Auto-label endpoint runs labeling and returns the resulting label_ids."""
    task = _make_task(id="t-auto", title="Fix login page crash")
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.apply_auto_labels", new_callable=AsyncMock) as mock_apply,
        patch("routers.tasks.task_labels_store") as mock_tls,
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        mock_tls.get_labels_for_task = MagicMock(return_value=["lbl-1", "lbl-2"])
        resp = await client.post("/api/tasks/t-auto/labels/auto")

    assert resp.status_code == 200
    data = resp.json()
    assert "label_ids" in data
    assert data["label_ids"] == ["lbl-1", "lbl-2"]
    mock_apply.assert_awaited_once_with("t-auto", "Fix login page crash", "")


@pytest.mark.asyncio
async def test_auto_label_task_not_found_returns_404(client):
    """Auto-label endpoint returns 404 when the task id does not exist."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.post("/api/tasks/missing-id/labels/auto")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_auto_label_task_no_labels_assigned(client):
    """When the suggester finds no matching labels, label_ids is empty."""
    task = _make_task(id="t-unlabeled", title="Something random")
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.apply_auto_labels", new_callable=AsyncMock),
        patch("routers.tasks.task_labels_store") as mock_tls,
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        mock_tls.get_labels_for_task = MagicMock(return_value=[])
        resp = await client.post("/api/tasks/t-unlabeled/labels/auto")

    assert resp.status_code == 200
    assert resp.json()["label_ids"] == []


# --- POST /api/tasks/{id}/shelve ---

@pytest.mark.asyncio
async def test_shelve_task(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.shelve_task = AsyncMock(return_value="shelved t-1")
        resp = await client.post("/api/tasks/t-1/shelve")

    assert resp.status_code == 200
    assert resp.json()["result"] == "shelved t-1"
    mock_ostk.shelve_task.assert_called_once_with("t-1")


@pytest.mark.asyncio
async def test_shelve_task_not_found(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.shelve_task = AsyncMock(
            side_effect=OstkError("task 'no-exist' not found")
        )
        resp = await client.post("/api/tasks/no-exist/shelve")

    assert resp.status_code == 400


# --- POST /api/tasks/{id}/unshelve ---

@pytest.mark.asyncio
async def test_unshelve_task(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.unshelve_task = AsyncMock(return_value="unshelved t-1")
        resp = await client.post("/api/tasks/t-1/unshelve")

    assert resp.status_code == 200
    assert resp.json()["result"] == "unshelved t-1"
    mock_ostk.unshelve_task.assert_called_once_with("t-1")


@pytest.mark.asyncio
async def test_unshelve_task_not_found(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.unshelve_task = AsyncMock(
            side_effect=OstkError("task 'no-exist' not found")
        )
        resp = await client.post("/api/tasks/no-exist/unshelve")

    assert resp.status_code == 400


# --- PATCH /api/tasks/{id} with reason ---

@pytest.mark.asyncio
async def test_update_task_priority_with_reason(client):
    """When a reason is provided, it is forwarded to ostk."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(return_value="promoted t-1 to P0")
        resp = await client.patch(
            "/api/tasks/t-1",
            json={"priority": "P0", "reason": "customer request"},
        )

    assert resp.status_code == 200
    mock_ostk.update_task_priority.assert_called_once_with(
        "t-1", "P0", reason="customer request"
    )


@pytest.mark.asyncio
async def test_update_task_priority_without_reason(client):
    """When no reason is provided, reason=None is forwarded."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(return_value="updated t-2 priority to P1")
        resp = await client.patch("/api/tasks/t-2", json={"priority": "P1"})

    assert resp.status_code == 200
    mock_ostk.update_task_priority.assert_called_once_with("t-2", "P1", reason=None)
