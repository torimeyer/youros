from datetime import datetime, timezone
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
    # →1694: default request now passes status="open" (not None) to avoid
    # shipping closed needles. Priority filter stacks on top.
    ctx.mock_ostk.list_tasks.assert_called_once_with(status="open", priority="P0")


# --- Regression: e2e smoke task filter on GET /api/tasks ---
#
# The tasks router hides any task whose title starts with "e2e-" from the
# default list so leftovers from a failed smoke run do not pollute the UI.
# The smoke script creates an e2e-prefixed task and then lists tasks to
# confirm it exists, so it must pass ?include_test_data=true to see its own
# task. These tests lock in both halves of that contract.

@pytest.mark.asyncio
async def test_list_tasks_hides_e2e_prefixed_titles_by_default(client):
    mock_tasks = [
        _make_task(id="t-e2e", title="e2e-foo"),
        _make_task(id="t-real", title="real-thing"),
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json()["tasks"]]
    assert "real-thing" in titles
    assert "e2e-foo" not in titles, (
        "Tasks with e2e- prefixed titles must be hidden from the default list "
        "so smoke leftovers do not pollute the UI."
    )


@pytest.mark.asyncio
async def test_list_tasks_include_test_data_flag_reveals_e2e_titles(client):
    mock_tasks = [
        _make_task(id="t-e2e", title="e2e-foo"),
        _make_task(id="t-real", title="real-thing"),
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks?include_test_data=true")

    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json()["tasks"]]
    assert "real-thing" in titles
    assert "e2e-foo" in titles, (
        "include_test_data=true must reveal e2e- prefixed tasks so the smoke "
        "script can verify its own CRUD round trip."
    )


@pytest.mark.asyncio
async def test_list_tasks_case_insensitive_e2e_prefix(client):
    mock_tasks = [
        _make_task(id="t-e2e-upper", title="E2E-FOO"),
        _make_task(id="t-real", title="real-thing"),
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json()["tasks"]]
    assert "real-thing" in titles
    assert "E2E-FOO" not in titles, (
        "The e2e- prefix filter must be case insensitive so uppercase smoke "
        "leftovers are also hidden from the default list."
    )


# --- Title sanitization on POST /api/tasks ---
#
# Tori was seeing machine-generated titles like
# "Build feature alpha 27557-1776316239" in her task list. The sanitizer
# strips trailing numeric ID sequences and trailing UUIDs so only
# plain-language titles reach ostk. Pure test patterns and
# empty-after-cleanup titles are rejected with a 400 and a plain
# message that tells the caller how to fix it.


@pytest.mark.asyncio
async def test_task_title_strips_trailing_id_sequence(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-99")
        resp = await client.post(
            "/api/tasks",
            json={"title": "Build the login form 27557-1776316239", "priority": "P1"},
        )

    assert resp.status_code == 200, resp.text
    # ostk.add_task must receive the clean title. No trailing digits.
    args, kwargs = mock_ostk.add_task.call_args
    assert args[0] == "Build the login form", (
        f"Expected trailing id sequence stripped, got: {args[0]!r}"
    )


@pytest.mark.asyncio
async def test_task_title_strips_trailing_uuid(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-100")
        resp = await client.post(
            "/api/tasks",
            json={
                "title": "Ship the welcome email 550e8400-e29b-41d4-a716-446655440000",
                "priority": "P1",
            },
        )

    assert resp.status_code == 200, resp.text
    args, _ = mock_ostk.add_task.call_args
    assert args[0] == "Ship the welcome email"


@pytest.mark.asyncio
async def test_task_title_rejects_pure_test_patterns(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="should never run")
        resp = await client.post(
            "/api/tasks",
            json={"title": "Build feature alpha 27557-1776316239"},
        )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "test artifact" in body["detail"].lower()
    # Plain-language message suggests a fix.
    assert "try" in body["detail"].lower()
    mock_ostk.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_task_title_rejects_empty_after_sanitization(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="should never run")
        # A title that is only a trailing id becomes empty after the
        # sanitizer runs. The stripped result has no user meaning, so
        # the router must reject it with a plain message.
        resp = await client.post(
            "/api/tasks",
            json={"title": "   27557-1776316239"},
        )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "empty" in body["detail"].lower() or "machine" in body["detail"].lower()
    mock_ostk.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_task_title_max_length_80_chars(client):
    long_title = "Build the shiny new onboarding wizard with twenty dozen fancy animated widgets plus confetti"
    assert len(long_title) > 80
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-long")
        resp = await client.post(
            "/api/tasks",
            json={"title": long_title, "priority": "P1"},
        )

    assert resp.status_code == 200, resp.text
    args, _ = mock_ostk.add_task.call_args
    # Must be truncated to 80 chars, with an ellipsis at the end.
    assert len(args[0]) <= 80, f"Title not truncated: {args[0]!r} has {len(args[0])} chars"
    assert args[0].endswith("\u2026"), f"Truncated title missing ellipsis: {args[0]!r}"


# --- Title length hard cap (Patterson step 2) ---


@pytest.mark.asyncio
async def test_task_title_rejects_over_120_chars(client):
    """POST with a title over 120 chars must return 400 with a hint to use description."""
    bloated_title = "A" * 121
    assert len(bloated_title) == 121
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="should never run")
        resp = await client.post(
            "/api/tasks",
            json={"title": bloated_title, "priority": "P1"},
        )

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "120" in detail, f"Hint missing '120': {detail!r}"
    assert "description" in detail.lower(), f"Hint missing 'description': {detail!r}"
    mock_ostk.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_task_title_accepts_exactly_120_chars(client):
    """POST with a title of exactly 120 chars must succeed (boundary is inclusive)."""
    boundary_title = "B" * 120
    assert len(boundary_title) == 120
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-boundary")
        resp = await client.post(
            "/api/tasks",
            json={"title": boundary_title, "priority": "P1"},
        )

    assert resp.status_code == 200, resp.text
    mock_ostk.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_task_title_short_with_multisection_description(client):
    """POST with a short title and multi-section description returns 200 and passes description through."""
    short_title = "Add settings page"
    description = (
        "**Why:** Users have no way to change preferences.\n\n"
        "**Acceptance:**\n- [ ] Settings panel opens from the sidebar\n\n"
        "**Blockers:** none"
    )
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-desc-2")
        resp = await client.post(
            "/api/tasks",
            json={"title": short_title, "priority": "P1", "description": description},
        )

    assert resp.status_code == 200, resp.text
    mock_ostk.add_task.assert_called_once_with(short_title, "P1", description=description)


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


@pytest.mark.asyncio
async def test_close_task_user_source_bypasses_burst_guard(client):
    """Needle 1208: ?source=user skips the rate limit so the user can close
    more than 3 tasks in 60 seconds from the Tasks page checkbox.
    """
    from routers.tasks import _recent_closes
    _recent_closes.clear()
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.close_task = AsyncMock(return_value="closed")

        # 5 rapid user-initiated closes — all should succeed.
        for i in range(5):
            resp = await client.post(f"/api/tasks/t-{i}/close?source=user")
            assert resp.status_code == 200, f"user close {i} should not be rate-limited"

    # User closes must NOT be counted in the burst window.
    assert len(_recent_closes) == 0


@pytest.mark.asyncio
async def test_close_task_agent_still_rate_limited_without_user_source(client):
    """The burst guard still fires for automated/agent closes (no ?source=user)."""
    from routers.tasks import _recent_closes
    _recent_closes.clear()
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.close_task = AsyncMock(return_value="closed")

        for i in range(3):
            resp = await client.post(f"/api/tasks/t-{i}/close")
            assert resp.status_code == 200

        # 4th agent close is rejected.
        resp = await client.post("/api/tasks/t-extra/close")
        assert resp.status_code == 429
        assert "Audit review" in resp.json()["detail"]


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
    assert resp.json()["message"] == "Work on lego-app"


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
async def test_list_tasks_json_decode_error_returns_500(client):
    """json.JSONDecodeError from _run_json must not escape as an unhandled 500.

    Before the fix, _run_json raised JSONDecodeError (not OstkError) when the
    ostk daemon emitted non-JSON output during startup races. The except clause
    only caught OstkError so FastAPI returned an opaque 500 with no detail.
    After the fix, _run_json wraps JSONDecodeError → OstkError, and list_tasks
    catches Exception, so both paths return a structured 500.
    """
    with _patch_ostk_and_labels(list_tasks=AsyncMock(side_effect=ValueError("not json"))):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "not json"


@pytest.mark.asyncio
async def test_list_tasks_unexpected_exception_returns_500(client):
    """Any unexpected exception from list_tasks or stores must yield a 500."""
    with _patch_ostk_and_labels(list_tasks=AsyncMock(side_effect=RuntimeError("boom"))):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_list_tasks_closed_sorted_by_closed_at_desc(client):
    mock_tasks = [
        {**_make_task(id="t-old", status="closed"), "closed_at": "2026-01-01T00:00:00Z"},
        {**_make_task(id="t-new", status="closed"), "closed_at": "2026-04-20T12:00:00Z"},
        {**_make_task(id="t-mid", status="closed"), "closed_at": "2026-03-15T06:00:00Z"},
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert ids == ["t-new", "t-mid", "t-old"]


@pytest.mark.asyncio
async def test_list_tasks_open_before_closed(client):
    mock_tasks = [
        {**_make_task(id="t-closed", status="closed"), "closed_at": "2026-04-20T12:00:00Z"},
        _make_task(id="t-open", status="open"),
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert ids.index("t-open") < ids.index("t-closed")


@pytest.mark.asyncio
async def test_list_tasks_closed_without_closed_at_sorts_last(client):
    mock_tasks = [
        {**_make_task(id="t-no-date", status="closed")},
        {**_make_task(id="t-with-date", status="closed"), "closed_at": "2026-04-01T00:00:00Z"},
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert ids.index("t-with-date") < ids.index("t-no-date")


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


# --- PATCH /api/tasks/{id} (update title + description) ---

@pytest.mark.asyncio
async def test_update_task_title_renames_task(client):
    """PATCH with title calls update_task_fields on the ostk service."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_fields = AsyncMock(return_value="updated t-1 title")
        resp = await client.patch(
            "/api/tasks/t-1",
            json={"title": "Session in torios"},
        )

    assert resp.status_code == 200
    mock_ostk.update_task_fields.assert_called_once_with(
        "t-1", title="Session in torios", description=None, notes=None
    )


@pytest.mark.asyncio
async def test_update_task_title_and_description(client):
    """PATCH can update both title and description in one call."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_fields = AsyncMock(
            return_value="updated t-1 title and description"
        )
        resp = await client.patch(
            "/api/tasks/t-1",
            json={
                "title": "Session in torios",
                "description": "session-task: Legacy migration. Close when this Claude Code session ends.",
            },
        )

    assert resp.status_code == 200
    mock_ostk.update_task_fields.assert_called_once_with(
        "t-1",
        title="Session in torios",
        description="session-task: Legacy migration. Close when this Claude Code session ends.",
        notes=None,
    )


@pytest.mark.asyncio
async def test_update_task_description_only(client):
    """Description-only PATCH does not trigger a title update."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_fields = AsyncMock(return_value="updated t-1 description")
        resp = await client.patch(
            "/api/tasks/t-1",
            json={"description": "new desc"},
        )

    assert resp.status_code == 200
    mock_ostk.update_task_fields.assert_called_once_with(
        "t-1", title=None, description="new desc", notes=None
    )


# --- PATCH /api/tasks/{id} (move task back to open) ---


@pytest.mark.asyncio
async def test_update_task_status_rejects_in_progress(client):
    """PATCH with status=in_progress must be rejected — in_progress is a read-only overlay."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_status = AsyncMock()
        resp = await client.patch(
            "/api/tasks/t-1",
            json={"status": "in_progress"},
        )

    assert resp.status_code == 400
    assert "Invalid status" in resp.json()["detail"]
    mock_ostk.update_task_status.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_status_back_to_open(client):
    """Moving a task back to open (e.g. user paused work) is accepted."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_status = AsyncMock(
            return_value="updated t-1 status to open"
        )
        resp = await client.patch(
            "/api/tasks/t-1",
            json={"status": "open"},
        )

    assert resp.status_code == 200
    assert resp.json()["result"] == "updated t-1 status to open"
    mock_ostk.update_task_status.assert_called_once_with("t-1", "open")


@pytest.mark.asyncio
async def test_update_task_status_rejects_closed_value(client):
    """Closing a task goes through /close, not PATCH status."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_status = AsyncMock()
        resp = await client.patch(
            "/api/tasks/t-1",
            json={"status": "closed"},
        )

    assert resp.status_code == 400
    assert "Invalid status" in resp.json()["detail"]
    mock_ostk.update_task_status.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_status_rejects_shelved_value(client):
    """Pausing a task goes through /shelve, not PATCH status."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_status = AsyncMock()
        resp = await client.patch(
            "/api/tasks/t-1",
            json={"status": "shelved"},
        )

    assert resp.status_code == 400
    mock_ostk.update_task_status.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_status_rejects_bogus_value(client):
    """Arbitrary strings must not be accepted as status values."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_status = AsyncMock()
        resp = await client.patch(
            "/api/tasks/t-1",
            json={"status": "blocked"},
        )

    assert resp.status_code == 400
    mock_ostk.update_task_status.assert_not_called()


@pytest.mark.asyncio
async def test_update_task_status_surfaces_service_error(client):
    """If the ostk service rejects the change (e.g. task is closed), the
    router surfaces the error as a 400 instead of silently succeeding."""
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_status = AsyncMock(
            side_effect=OstkError("task 't-1' is closed; reopen or unshelve it first")
        )
        resp = await client.patch(
            "/api/tasks/t-1",
            json={"status": "open"},
        )

    assert resp.status_code == 400
    assert "closed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_task_status_combines_with_priority(client):
    """One PATCH can update both status and priority in a single call."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(
            return_value="updated t-1 priority to P0"
        )
        mock_ostk.update_task_status = AsyncMock(
            return_value="updated t-1 status to open"
        )
        resp = await client.patch(
            "/api/tasks/t-1",
            json={"priority": "P0", "status": "open"},
        )

    assert resp.status_code == 200
    mock_ostk.update_task_priority.assert_called_once()
    mock_ostk.update_task_status.assert_called_once_with("t-1", "open")


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



# --- neighbor filtering tests ---

@pytest.mark.asyncio
async def test_task_briefing_filters_loose_neighbors(client):
    """Neighbors with no structural link and no shared tags are dropped."""
    mock_briefing = {
        "task_id": "→200",
        "priority": "P2",
        "status": "open",
        "title": "Chat feature",
        "sphere": None,
        "neighbors": [
            "300 [open] Deploy infrastructure",
            "301 [open] Write docs",
        ],
        "blocked_by": [],
        "unblocks": [],
        "all_blockers_resolved": True,
        "raw": "test",
    }
    current_task = {
        "id": "→200",
        "title": "Chat feature",
        "description": "Build the chat thing",
        "priority": "P2",
        "status": "open",
        "tags": ["chat"],
    }
    neighbor_300 = {
        "id": "→300",
        "title": "Deploy infrastructure",
        "description": "",
        "priority": "P3",
        "status": "open",
        "tags": ["foundation"],
    }
    neighbor_301 = {
        "id": "→301",
        "title": "Write docs",
        "description": "",
        "priority": "P3",
        "status": "open",
        "tags": ["docs"],
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.activate_task = AsyncMock(return_value=mock_briefing)
        mock_ostk.list_tasks = AsyncMock(
            return_value=[current_task, neighbor_300, neighbor_301]
        )
        resp = await client.get("/api/tasks/200/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["briefing"]["neighbors"] == [], (
        "loose-similarity neighbors with no structural link or shared tags must be filtered out"
    )


@pytest.mark.asyncio
async def test_task_briefing_keeps_neighbors_with_shared_tags(client):
    """Neighbors sharing at least one tag with the current task are kept."""
    mock_briefing = {
        "task_id": "→200",
        "priority": "P2",
        "status": "open",
        "title": "Chat feature",
        "sphere": None,
        "neighbors": [
            "302 [open] Chat UI revamp",
            "303 [open] Deploy infrastructure",
        ],
        "blocked_by": [],
        "unblocks": [],
        "all_blockers_resolved": True,
        "raw": "test",
    }
    current_task = {
        "id": "→200",
        "title": "Chat feature",
        "description": "",
        "priority": "P2",
        "status": "open",
        "tags": ["chat"],
    }
    neighbor_302 = {
        "id": "→302",
        "title": "Chat UI revamp",
        "description": "",
        "tags": ["chat"],
    }
    neighbor_303 = {
        "id": "→303",
        "title": "Deploy infrastructure",
        "description": "",
        "tags": ["foundation"],
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.activate_task = AsyncMock(return_value=mock_briefing)
        mock_ostk.list_tasks = AsyncMock(
            return_value=[current_task, neighbor_302, neighbor_303]
        )
        resp = await client.get("/api/tasks/200/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["briefing"]["neighbors"] == ["302 [open] Chat UI revamp"]


@pytest.mark.asyncio
async def test_task_briefing_keeps_neighbors_in_blocked_by(client):
    """A neighbor that also appears in blocked_by is structurally linked and kept."""
    mock_briefing = {
        "task_id": "→163",
        "priority": "P1",
        "status": "open",
        "title": "Integration dashboard",
        "sphere": None,
        "neighbors": [
            "160 [open] Mobile-friendly layout",
            "999 [open] Unrelated task",
        ],
        "blocked_by": [{"text": "→160 [open] Mobile-friendly layout", "resolved": False}],
        "unblocks": [],
        "all_blockers_resolved": False,
        "raw": "test",
    }
    current_task = {
        "id": "→163",
        "title": "Integration dashboard",
        "description": "",
        "priority": "P1",
        "status": "open",
        "tags": [],
    }
    neighbor_160 = {
        "id": "→160",
        "title": "Mobile-friendly layout",
        "description": "Make every page work on a phone screen",
        "priority": "P1",
        "status": "open",
        "tags": [],
    }
    neighbor_999 = {
        "id": "→999",
        "title": "Unrelated task",
        "description": "",
        "priority": "P3",
        "status": "open",
        "tags": [],
    }
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch(
             "services.blocker_explanation.explain_blocker",
             new=AsyncMock(return_value=None),
         ):
        mock_ostk.activate_task = AsyncMock(return_value=mock_briefing)
        mock_ostk.list_tasks = AsyncMock(
            return_value=[current_task, neighbor_160, neighbor_999]
        )
        resp = await client.get("/api/tasks/163/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["briefing"]["neighbors"] == ["160 [open] Mobile-friendly layout"]


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


# --- POST /api/tasks/duplicates/resolve ---

@pytest.mark.asyncio
async def test_resolve_duplicate_closes_discard_task(client):
    """Calling resolve with a valid pair closes discard_id with reason=duplicate."""
    mock_tasks = [
        _make_task(id="t-1", title="Fix the login bug"),
        _make_task(id="t-2", title="Fix login bug"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.close_task = AsyncMock(return_value="closed t-2")
        resp = await client.post(
            "/api/tasks/duplicates/resolve",
            json={"keep_id": "t-1", "discard_id": "t-2"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["resolved"] == 1
    assert data["closed"] == "t-2"
    assert data["kept"] == "t-1"
    mock_ostk.close_task.assert_called_once_with("t-2", closed_reason="duplicate")


@pytest.mark.asyncio
async def test_resolve_duplicate_returns_404_for_unknown_task(client):
    """Returns 404 when discard_id is not among open tasks."""
    mock_tasks = [
        _make_task(id="t-1", title="Fix the login bug"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.close_task = AsyncMock()
        resp = await client.post(
            "/api/tasks/duplicates/resolve",
            json={"keep_id": "t-1", "discard_id": "t-999"},
        )

    assert resp.status_code == 404
    assert "t-999" in resp.json()["detail"]
    mock_ostk.close_task.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_duplicate_already_closed(client):
    """Returns 404 when discard_id is already closed (not in open tasks)."""
    mock_tasks = [
        _make_task(id="t-1", title="Fix the login bug"),
        _make_task(id="t-2", title="Fix login bug", status="closed"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        # list_tasks returns only open tasks; t-2 is not there
        mock_ostk.list_tasks = AsyncMock(
            return_value=[t for t in mock_tasks if t["status"] == "open"]
        )
        mock_ostk.close_task = AsyncMock()
        resp = await client.post(
            "/api/tasks/duplicates/resolve",
            json={"keep_id": "t-1", "discard_id": "t-2"},
        )

    assert resp.status_code == 404
    mock_ostk.close_task.assert_not_called()


# --- POST /api/tasks/duplicates/resolve-all ---

@pytest.mark.asyncio
async def test_resolve_all_duplicates_closes_all_dupes(client):
    """Calling resolve-all closes every task_b in the detected duplicate pairs."""
    mock_tasks = [
        _make_task(id="t-1", title="Fix the login bug"),
        _make_task(id="t-2", title="Fix login bug"),
        _make_task(id="t-3", title="Completely unrelated task"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.close_task = AsyncMock(return_value="closed")
        resp = await client.post("/api/tasks/duplicates/resolve-all", json={})

    assert resp.status_code == 200
    data = resp.json()
    assert data["resolved"] == 1
    assert "t-2" in data["closed"]
    mock_ostk.close_task.assert_called_once_with("t-2", closed_reason="duplicate")


@pytest.mark.asyncio
async def test_resolve_all_duplicates_returns_zero_when_no_dupes(client):
    """When there are no duplicate pairs, resolved=0 is returned."""
    mock_tasks = [
        _make_task(id="t-1", title="Design the landing page"),
        _make_task(id="t-2", title="Fix the login bug"),
        _make_task(id="t-3", title="Write unit tests"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.close_task = AsyncMock()
        resp = await client.post("/api/tasks/duplicates/resolve-all", json={})

    assert resp.status_code == 200
    data = resp.json()
    assert data["resolved"] == 0
    assert data["closed"] == []
    mock_ostk.close_task.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_all_duplicates_custom_threshold(client):
    """A low threshold should catch more pairs; a high threshold fewer."""
    mock_tasks = [
        _make_task(id="t-1", title="Refactor auth module"),
        _make_task(id="t-2", title="Refactor the auth module"),  # ~0.91 similar
        _make_task(id="t-3", title="Rewrite auth from scratch"),  # ~0.5 similar
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.close_task = AsyncMock(return_value="closed")
        # High threshold: only the very similar pair should match
        resp = await client.post(
            "/api/tasks/duplicates/resolve-all", json={"threshold": 0.85}
        )

    assert resp.status_code == 200
    data = resp.json()
    # t-1 and t-2 are ~0.91 similar so t-2 should be closed
    assert data["resolved"] >= 1
    assert "t-2" in data["closed"]


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


@pytest.mark.asyncio
async def test_recently_deleted_task_not_recreated_by_same_source(client):
    """A task title deleted in the last 5 minutes cannot be re-created.

    Regression: live-demo resurrection bug. User deletes tasks 565-568
    from the Tasks page; a spec-build smoke loop instantly re-adds them
    via POST /api/tasks with the same titles. The tombstone cache now
    rejects those re-creates with 409 for the next five minutes.
    """
    from services import recent_deletes

    recent_deletes.clear()

    # Delete a task. The router looks up its title first so it can be
    # tombstoned, then runs the actual delete.
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store"), \
         patch("routers.tasks.threads_store"):
        mock_ostk.list_tasks = AsyncMock(return_value=[
            {"id": "t-42", "title": "Build basic homepage", "status": "open"},
        ])
        mock_ostk.delete_task = AsyncMock(return_value="deleted t-42")
        del_resp = await client.delete("/api/tasks/t-42")
    assert del_resp.status_code == 200

    # Immediately try to re-create a task with the same title. This
    # mirrors the sibling smoke agent re-running build_tasks_from_file
    # against roadmap.md right after the user wiped the tasks.
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-43")
        recreate_resp = await client.post(
            "/api/tasks",
            json={"title": "Build basic homepage", "priority": "P1"},
        )

    assert recreate_resp.status_code == 409
    body = recreate_resp.json()
    assert "deleted" in body["detail"].lower()
    # ostk.add_task must never have been invoked for the tombstoned title.
    mock_ostk.add_task.assert_not_called()

    recent_deletes.clear()


# --- ostk.delete_task unit tests ---

@pytest.mark.asyncio
async def test_ostk_delete_task_removes_entry(tmp_path):
    """delete_task removes the matching line from issues.jsonl."""
    from services.ostk import OstkService

    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True, exist_ok=True)
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
    issues_dir.mkdir(parents=True, exist_ok=True)
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
    issues_dir.mkdir(parents=True, exist_ok=True)
    issues_file = issues_dir / "issues.jsonl"
    issues_file.write_text('{"id": "t-1", "title": "Only task", "status": "open"}\n')

    svc = OstkService(cwd=str(tmp_path))
    await svc.delete_task("t-1")

    assert issues_file.read_text() == ""


@pytest.mark.asyncio
async def test_ostk_delete_task_missing_file_falls_back_to_cli(tmp_path):
    """When issues.jsonl does not exist, delete_task falls back to CLI close.

    If the CLI also cannot find the task, OstkError("not found") is raised.
    """
    from unittest.mock import AsyncMock, patch
    from services.ostk import OstkService, OstkError

    svc = OstkService(cwd=str(tmp_path))
    with patch.object(svc, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = OstkError("task 't-1' not found")
        with pytest.raises(OstkError, match="not found"):
            await svc.delete_task("t-1")

    mock_run.assert_called_once_with("work", "close", "t-1")


@pytest.mark.asyncio
async def test_ostk_delete_task_not_in_jsonl_falls_back_to_cli(tmp_path):
    """delete_task falls back to CLI close when task is not in issues.jsonl.

    Regression →1502: tasks that pre-date the current issues.jsonl (like
    →1477) returned 404 because issues.jsonl only holds ~22 recent entries.
    The fix routes through `ostk work close` for those tasks so the delete
    actually takes effect and the task stops reappearing on refresh.
    """
    from unittest.mock import AsyncMock, patch
    from services.ostk import OstkService

    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True, exist_ok=True)
    issues_file = issues_dir / "issues.jsonl"
    # issues.jsonl exists but does NOT contain the target task
    issues_file.write_text('{"id": "t-1", "title": "Other task", "status": "open"}\n')

    svc = OstkService(cwd=str(tmp_path))
    with patch.object(svc, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = "closed →1477"
        result = await svc.delete_task("→1477")

    mock_run.assert_called_once_with("work", "close", "→1477")
    assert "→1477" in result
    # Original task in issues.jsonl should be untouched
    assert "t-1" in issues_file.read_text()


@pytest.mark.asyncio
async def test_ostk_delete_task_not_in_jsonl_run_error_raises(tmp_path):
    """When task is not in issues.jsonl AND _run raises, delete_task must raise.

    Regression →1507: the daemon close fallback silently swallowed _run errors
    for tasks not tracked in issues.jsonl, returning 'deleted' even though
    ostk work close failed and the task stayed open in the daemon's view.
    """
    from unittest.mock import AsyncMock, patch
    from services.ostk import OstkService, OstkError

    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True, exist_ok=True)
    issues_file = issues_dir / "issues.jsonl"
    # File exists but does NOT contain the target task
    issues_file.write_text('{"id": "t-1", "title": "Other task", "status": "open"}\n')

    svc = OstkService(cwd=str(tmp_path))
    with patch.object(svc, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = OstkError("ostk work close: task not found")
        with pytest.raises(OstkError):
            await svc.delete_task("→1477")

    mock_run.assert_called_once_with("work", "close", "→1477")
    # issues.jsonl must not be touched — the task was never there
    assert issues_file.read_text() == '{"id": "t-1", "title": "Other task", "status": "open"}\n'


@pytest.mark.asyncio
async def test_ostk_delete_task_in_jsonl_run_error_raises(tmp_path):
    """When task IS in issues.jsonl AND _run raises, delete_task must raise.

    Regression →1507: when found_in_file=True the except block only re-raised
    when `not found_in_file`, so _run errors were silently swallowed. The file
    entry got removed but the daemon was never notified, causing the task to
    reappear on the next poll.
    """
    from unittest.mock import AsyncMock, patch
    from services.ostk import OstkService, OstkError

    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True, exist_ok=True)
    issues_file = issues_dir / "issues.jsonl"
    # issues.jsonl DOES contain the target task (found_in_file will be True)
    issues_file.write_text(
        '{"id": "→1477", "title": "Target task", "status": "open"}\n'
        '{"id": "t-2", "title": "Other task", "status": "open"}\n'
    )

    svc = OstkService(cwd=str(tmp_path))
    with patch.object(svc, "_run", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = OstkError("ostk work close: command failed")
        with pytest.raises(OstkError):
            await svc.delete_task("→1477")

    mock_run.assert_called_once_with("work", "close", "→1477")
    # File must NOT have been modified since daemon close failed
    assert "→1477" in issues_file.read_text()
    assert "t-2" in issues_file.read_text()


@pytest.mark.asyncio
async def test_delete_actually_removes_from_issues_jsonl(tmp_path):
    """DELETE /api/tasks/{id} removes the JSONL line, not just marks it closed.

    Regression: live demo had tasks 565-568 coming back after delete. We
    needed to prove delete really removes the row from disk, so tail the
    file pre and post and assert the id is gone.
    """
    from services.ostk import OstkService

    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True, exist_ok=True)
    issues_file = issues_dir / "issues.jsonl"
    issues_file.write_text(
        '{"id": "\u2192565", "title": "Land a basic homepage", "status": "open"}\n'
        '{"id": "\u2192566", "title": "Add a contact form", "status": "open"}\n'
        '{"id": "\u2192567", "title": "Wire up a newsletter signup", "status": "open"}\n'
        '{"id": "\u2192568", "title": "Publish to staging", "status": "open"}\n'
        '{"id": "\u2192569", "title": "Other unrelated", "status": "open"}\n'
    )

    svc = OstkService(cwd=str(tmp_path))
    for tid in ("\u2192565", "\u2192566", "\u2192567", "\u2192568"):
        await svc.delete_task(tid)

    remaining = issues_file.read_text()
    for tid in ("\u2192565", "\u2192566", "\u2192567", "\u2192568"):
        assert tid not in remaining, f"{tid} still present after delete"
    assert "\u2192569" in remaining


@pytest.mark.asyncio
async def test_ostk_delete_task_notifies_daemon_before_file_edit(tmp_path, monkeypatch):
    """delete_task must call _run('work', 'close', ...) and succeed when daemon succeeds.

    Regression for →1502: the original delete_task wrote directly to issues.jsonl
    without informing the ostk daemon. list_tasks routes via the daemon socket
    (needle tool), so the daemon's in-memory state still showed the task as open
    and it reappeared on every subsequent poll — even after a full page refresh.

    The fix: close via _run (subprocess notifies the daemon) inside the file-write
    lock, then strip all entries for the id from issues.jsonl. If the close call
    fails, the error propagates — file is NOT edited (→1507).
    """
    from services.ostk import OstkService, OstkError

    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True, exist_ok=True)
    issues_file = issues_dir / "issues.jsonl"
    issues_file.write_text(
        '{"id": "t-1", "title": "Task to delete", "status": "open"}\n'
        '{"id": "t-2", "title": "Keep this one", "status": "open"}\n'
    )

    svc = OstkService(cwd=str(tmp_path))
    run_calls: list = []

    async def fake_run(*args, **kwargs):
        run_calls.append(args)
        return "closed t-1"

    monkeypatch.setattr(svc, "_run", fake_run)

    result = await svc.delete_task("t-1")

    # Must have attempted to close via daemon
    assert any(
        len(c) >= 3 and c[0] == "work" and c[1] == "close" and c[2] == "t-1"
        for c in run_calls
    ), f"Expected 'work close t-1' daemon call, got: {run_calls}"

    # File entry must be removed after successful daemon close
    assert result == "deleted t-1"
    remaining = issues_file.read_text()
    assert "t-1" not in remaining
    assert "t-2" in remaining


@pytest.mark.asyncio
async def test_id_recycle_after_delete_allowed_with_new_title(client):
    """After deleting task id N, POST /api/tasks with a DIFFERENT title
    must succeed even when ostk's next-id sequence lands on the freed id.

    Regression guard (pre-demo-e2e sweep, →593): the old behaviour rolled
    back any create that reused a recently-deleted id. ostk's next-id
    sequence normally picks max+1, so deleting the last task and then
    creating a fresh, unrelated task would hit the ghost create rollback
    and 409 every time. Tori could not add a new task after a delete.

    The title tombstone still catches genuine title resurrection. The
    id tombstone remains in place for file-restore scenarios that do
    not go through this endpoint (see services/recent_deletes.py).
    """
    from services import recent_deletes

    recent_deletes.clear()

    # Delete t-565 first so the id tombstone records it.
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store"), \
         patch("routers.tasks.threads_store"):
        mock_ostk.list_tasks = AsyncMock(return_value=[
            {"id": "\u2192565", "title": "Build basic homepage", "status": "open"},
        ])
        mock_ostk.delete_task = AsyncMock(return_value="deleted \u2192565")
        del_resp = await client.delete("/api/tasks/\u2192565")
    assert del_resp.status_code == 200
    assert recent_deletes.is_id_recent("\u2192565") is True

    # ostk now hands back the same id for a completely different new
    # task. Must succeed: the new title is not in the title tombstone,
    # and the id-only check no longer blocks normal recycling.
    delete_calls: list[str] = []

    async def fake_delete(tid):
        delete_calls.append(tid)
        return f"deleted {tid}"

    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(
            return_value="added \u2192565: Ship a brand new unrelated feature [P1]"
        )
        mock_ostk.delete_task = AsyncMock(side_effect=fake_delete)
        resp = await client.post(
            "/api/tasks",
            json={"title": "Ship a brand new unrelated feature", "priority": "P1"},
        )

    assert resp.status_code == 200, resp.json()
    assert resp.json()["task_id"] == "\u2192565"
    # No rollback: the new task must stay in place.
    assert delete_calls == []

    recent_deletes.clear()


@pytest.mark.asyncio
async def test_smoke_does_not_recreate_deleted_demo_tasks(client):
    """The build_tasks_from_file chat tool must skip ids the user just
    deleted, even when the roadmap reworded the bullet.

    This is the live-demo smoke path the user hit twice: roadmap reads
    "Land a basic homepage", the user deletes it, the smoke runs again
    and now says "Build basic homepage" but ostk hands back the same
    numeric id. The id tombstone must reject it.
    """
    from services import recent_deletes
    from services import tool_executor

    recent_deletes.clear()
    recent_deletes.record_id("\u2192565")

    add_calls: list[str] = []
    delete_calls: list[str] = []

    async def fake_add_task(title, priority="P1", description="", ac=""):
        add_calls.append(title)
        return f"added \u2192565: {title} [P1]"

    async def fake_delete_task(tid):
        delete_calls.append(tid)
        return f"deleted {tid}"

    async def fake_list_tasks(status="open"):
        return []

    # Write a roadmap file into a fake home so we never touch the real
    # ~/.myos/files/ directory during tests. The resolver uses
    # Path.home(), so patching that redirects the lookup.
    import tempfile as _tempfile
    from pathlib import Path as _Path
    with _tempfile.TemporaryDirectory() as fake_home:
        files_dir = _Path(fake_home) / ".myos" / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        roadmap = files_dir / "pytest-smoke-roadmap.md"
        roadmap.write_text("# roadmap\n\n- Build basic homepage\n")
        try:
            with patch("services.tool_executor.Path.home", return_value=_Path(fake_home)), \
                 patch("services.tool_executor.ostk") as mock_ostk:
                mock_ostk.list_tasks = AsyncMock(side_effect=fake_list_tasks)
                mock_ostk.add_task = AsyncMock(side_effect=fake_add_task)
                mock_ostk.delete_task = AsyncMock(side_effect=fake_delete_task)
                result = await tool_executor._build_tasks_from_file(
                    "pytest-smoke-roadmap.md"
                )
        finally:
            recent_deletes.clear()

    # Tool tried to add once, got the tombstoned id back, and rolled it
    # back via delete_task so no ghost task lingers.
    assert add_calls == ["Build basic homepage"]
    assert delete_calls == ["\u2192565"]
    assert "recently deleted" in result or "No tasks created" in result


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
        patch("routers.tasks._has_labeling_auth", new_callable=AsyncMock, return_value=True),
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


# --- POST /api/tasks/delete-all ---

def _patch_delete_all(tasks_list, delete_result="deleted"):
    """Helper: patches ostk.list_tasks and ostk.delete_task for delete-all tests."""
    ostk_patch = patch("routers.tasks.ostk")
    tls_patch = patch("routers.tasks.task_labels_store")
    threads_patch = patch("routers.tasks.threads_store")
    order_patch = patch("routers.tasks.task_order_store")

    class _Ctx:
        def __enter__(self):
            self.mock_ostk = ostk_patch.__enter__()
            self.mock_tls = tls_patch.__enter__()
            self.mock_threads = threads_patch.__enter__()
            self.mock_order = order_patch.__enter__()
            self.mock_ostk.list_tasks = AsyncMock(return_value=tasks_list)
            self.mock_ostk.delete_task = AsyncMock(return_value=delete_result)
            self.mock_tls.get_all_assignments = MagicMock(return_value={})
            self.mock_tls.remove_task = MagicMock()
            self.mock_threads.remove_task_from_all_threads = MagicMock()
            self.mock_order.remove_task = MagicMock()
            return self

        def __exit__(self, *args):
            order_patch.__exit__(*args)
            threads_patch.__exit__(*args)
            tls_patch.__exit__(*args)
            ostk_patch.__exit__(*args)

    return _Ctx()


@pytest.mark.asyncio
async def test_delete_all_with_no_filter_deletes_every_task(client):
    """No filter body deletes every task regardless of status."""
    tasks = [
        _make_task(id="t-1", status="open"),
        _make_task(id="t-2", status="closed"),
        _make_task(id="t-3", status="open"),
    ]
    with _patch_delete_all(tasks) as ctx:
        resp = await client.post("/api/tasks/delete-all", json={})

    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == 3
    assert set(data["names"]) == {"t-1", "t-2", "t-3"}
    assert ctx.mock_ostk.delete_task.call_count == 3


@pytest.mark.asyncio
async def test_delete_all_with_status_filter_leaves_others(client):
    """status='open' deletes only open/in-progress tasks, leaves closed ones."""
    tasks = [
        _make_task(id="t-open", status="open"),
        _make_task(id="t-closed", status="closed"),
        _make_task(id="t-prog", status="in_progress"),
    ]
    with _patch_delete_all(tasks) as ctx:
        resp = await client.post("/api/tasks/delete-all", json={"status": "open"})

    assert resp.status_code == 200
    data = resp.json()
    # open and in_progress are both "not closed" so both should be deleted
    assert data["deleted"] == 2
    assert "t-open" in data["names"]
    assert "t-prog" in data["names"]
    assert "t-closed" not in data["names"]
    assert ctx.mock_ostk.delete_task.call_count == 2


@pytest.mark.asyncio
async def test_delete_all_with_priority_filter(client):
    """priority=['P0'] deletes only P0 tasks."""
    tasks = [
        _make_task(id="t-p0", priority="P0", status="open"),
        _make_task(id="t-p1", priority="P1", status="open"),
        _make_task(id="t-p2", priority="P2", status="open"),
    ]
    with _patch_delete_all(tasks) as ctx:
        resp = await client.post("/api/tasks/delete-all", json={"priority": ["P0"]})

    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == 1
    assert data["names"] == ["t-p0"]
    assert ctx.mock_ostk.delete_task.call_count == 1


@pytest.mark.asyncio
async def test_delete_all_returns_count(client):
    """Response always includes deleted (int) and names (list)."""
    tasks = [_make_task(id=f"t-{i}") for i in range(5)]
    with _patch_delete_all(tasks):
        resp = await client.post("/api/tasks/delete-all", json={})

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["deleted"], int)
    assert data["deleted"] == 5
    assert isinstance(data["names"], list)


# --- GET /api/tasks/counts ---

@pytest.mark.asyncio
async def test_task_counts_returns_open_count(client):
    """counts endpoint returns the number of visible open tasks.

    After →1694 the endpoint calls ostk.list_tasks(status="open") instead
    of list_tasks() so it doesn't load ~1400 closed needles just to count
    the active ones. in_progress tasks are stored as "open" in ostk and
    get the in_progress overlay in the router.
    """
    tasks = [
        _make_task(id="t-1", title="Write report", status="open"),
        _make_task(id="t-2", title="Fix bug", status="open"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/tasks/counts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["open"] == 2
    mock_ostk.list_tasks.assert_called_once_with(status="open")


@pytest.mark.asyncio
async def test_task_counts_excludes_session_tasks(client):
    """counts endpoint does not count session tasks."""
    tasks = [
        _make_task(id="t-1", title="Real task", status="open"),
        _make_task(
            id="t-2",
            title="Session placeholder",
            status="open",
            description="session-task: auto-filed by hook",
        ),
        _make_task(
            id="t-3",
            title="Claude Code session claude-code-abc123",
            status="open",
        ),
        _make_task(
            id="t-4",
            title="Another auto-filed",
            status="open",
            description="Auto-filed by SessionStart hook",
        ),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/tasks/counts")

    assert resp.status_code == 200
    # Only t-1 should count; the other three are session tasks
    assert resp.json()["open"] == 1


@pytest.mark.asyncio
async def test_task_counts_excludes_e2e_smoke_tasks(client):
    """counts endpoint skips e2e- smoke-test leftovers.

    GET /api/tasks hides any task whose title starts with "e2e-" unless
    include_test_data=true is passed. /counts must apply the same rule so
    the sidebar badge never counts rows the Tasks page refuses to show.
    """
    tasks = [
        _make_task(id="t-1", title="Real task", status="open"),
        _make_task(id="t-2", title="E2e-crud-1234567890", status="open"),
        _make_task(id="t-3", title="e2e-another-smoke", status="open"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/tasks/counts")

    assert resp.status_code == 200
    # Only t-1 counts. The two e2e- tasks are hidden.
    assert resp.json()["open"] == 1


@pytest.mark.asyncio
async def test_task_counts_includes_in_progress_tasks(client):
    """counts endpoint includes in_progress alongside open.

    A task claimed by an agent stays visible on the Tasks page (the
    isActiveTask helper treats anything not closed or shelved as active)
    so the badge must count it too. Without this, a pull-model claim
    would make the badge number drop even though the row is still there.
    """
    tasks = [
        _make_task(id="t-1", title="Real open task", status="open"),
        _make_task(id="t-2", title="Claimed by agent", status="in_progress"),
        _make_task(id="t-3", title="Done already", status="closed"),
        _make_task(id="t-4", title="Parked for later", status="shelved"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/tasks/counts")

    assert resp.status_code == 200
    # Open + in_progress = 2; closed and shelved do not count.
    assert resp.json()["open"] == 2


@pytest.mark.asyncio
async def test_counts_match_visible_open_tasks_default_filter(client):
    """Badge count equals the number of rows a Tasks-page default filter shows.

    The Tasks page default is statusFilter=open (isActiveTask) plus
    hideSessionTasks=true. /api/tasks also strips e2e- titles unless
    include_test_data=true. This test mixes every bucket and asserts the
    badge matches the visible set exactly.
    """
    tasks = [
        # These are visible on the default Tasks page view.
        _make_task(id="t-visible-1", title="Write report", status="open"),
        _make_task(id="t-visible-2", title="Claimed work", status="in_progress"),
        # Hidden: closed and shelved.
        _make_task(id="t-hidden-1", title="Old done thing", status="closed"),
        _make_task(id="t-hidden-2", title="Parked", status="shelved"),
        # Hidden: session tasks (three detection rules).
        _make_task(
            id="t-hidden-3",
            title="Session placeholder",
            status="open",
            description="session-task: auto-filed by hook",
        ),
        _make_task(
            id="t-hidden-4",
            title="Auto",
            status="open",
            description="Auto-filed by SessionStart hook",
        ),
        _make_task(
            id="t-hidden-5",
            title="Claude Code session claude-code-abc123",
            status="in_progress",
        ),
        # Hidden: e2e smoke-test leftovers.
        _make_task(id="t-hidden-6", title="E2e-crud-1776277919", status="open"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/tasks/counts")

    assert resp.status_code == 200
    # Only the two "visible" rows count.
    assert resp.json()["open"] == 2


@pytest.mark.asyncio
async def test_task_counts_includes_real_session_start_hook_tasks(client):
    """Regression: a real SessionStart-hook task is excluded from the badge.

    When the hook writes `description="session-task: <session_id>"` the
    counts endpoint must NOT count it. Guards against a future refactor
    that changes the detection rule and accidentally leaks session rows
    into the badge number.
    """
    tasks = [
        _make_task(id="t-1", title="Visible work", status="open"),
        # Exactly what the SessionStart hook writes today.
        _make_task(
            id="t-session",
            title="Claude session a1b2c3",
            status="open",
            description="session-task: a1b2c3d4-e5f6",
        ),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/tasks/counts")

    assert resp.status_code == 200
    assert resp.json()["open"] == 1


# --- POST /api/tasks/health/autofix/{issue_type} ---

@pytest.mark.asyncio
async def test_autofix_no_description_generates_and_saves(client):
    """no_description handler calls Claude to generate a description and saves it."""
    task = _make_task(id="→42", title="Build login screen")
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("services.task_autofix._generate_description", new=AsyncMock(return_value="Build the login screen with email and password fields.")) as mock_gen, \
         patch("services.task_autofix._save_description", new=AsyncMock()) as mock_save:
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        resp = await client.post(
            "/api/tasks/health/autofix/no_description",
            json={"task_id": "→42"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["fixed"] is True
    assert "Build the login screen" in data["details"]
    mock_gen.assert_awaited_once_with("Build login screen")
    mock_save.assert_awaited_once_with("→42", "Build the login screen with email and password fields.")


@pytest.mark.asyncio
async def test_autofix_no_description_no_api_key(client):
    """no_description handler returns fixed=False when no API key is available."""
    task = _make_task(id="→42", title="Build login screen")
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("services.task_autofix._generate_description", new=AsyncMock(return_value=None)):
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        resp = await client.post(
            "/api/tasks/health/autofix/no_description",
            json={"task_id": "→42"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["fixed"] is False
    assert "reason" in data


@pytest.mark.asyncio
async def test_autofix_no_labels_dispatches_apply(client):
    """no_labels handler calls apply_auto_labels for the task."""
    task = _make_task(id="→10", title="Update docs", description="Refresh the API docs.")
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("services.task_autofix.apply_auto_labels", new=AsyncMock()) as mock_apply:
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        resp = await client.post(
            "/api/tasks/health/autofix/no_labels",
            json={"task_id": "→10"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["fixed"] is True
    mock_apply.assert_awaited_once_with("→10", "Update docs", "Refresh the API docs.")


@pytest.mark.asyncio
async def test_autofix_no_priority_sets_p2(client):
    """no_priority handler defaults to P2 when no priority is set."""
    task = _make_task(id="→15", title="Refactor auth")
    task["priority"] = ""
    with patch("routers.tasks.ostk") as mock_router_ostk, \
         patch("services.task_autofix.ostk") as mock_svc_ostk:
        mock_router_ostk.list_tasks = AsyncMock(return_value=[task])
        mock_svc_ostk.update_task_priority = AsyncMock(return_value="updated")
        resp = await client.post(
            "/api/tasks/health/autofix/no_priority",
            json={"task_id": "→15"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["fixed"] is True
    mock_svc_ostk.update_task_priority.assert_awaited_once_with("→15", "P2")


@pytest.mark.asyncio
async def test_autofix_unknown_type_returns_not_fixable(client):
    """Unknown issue types return fixable=False without crashing."""
    task = _make_task(id="→99", title="Mystery task")
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        resp = await client.post(
            "/api/tasks/health/autofix/some_unknown_type",
            json={"task_id": "→99"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("fixable") is False


@pytest.mark.asyncio
async def test_autofix_missing_task_id_returns_422(client):
    """autofix endpoint returns 422 when task_id is missing from body."""
    resp = await client.post("/api/tasks/health/autofix/no_description", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_autofix_task_not_found_returns_404(client):
    """autofix endpoint returns 404 when task is not in the task list."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.post(
            "/api/tasks/health/autofix/no_description",
            json={"task_id": "→999"},
        )

    assert resp.status_code == 404


# --- POST /api/tasks/health/autofix-all ---

@pytest.mark.asyncio
async def test_autofix_all_dispatches_and_returns_counts(client):
    """autofix-all iterates issues, dispatches handlers, and returns correct counts."""
    health_result = {
        "tasks": [],
        "issues": [
            {"type": "no_description", "severity": "info", "message": "Task →1 has no description", "task_ids": ["→1"]},
            {"type": "no_description", "severity": "info", "message": "Task →2 has no description", "task_ids": ["→2"]},
            {"type": "isolated", "severity": "info", "message": "Task →3 is isolated", "task_ids": ["→3"]},
        ],
        "summary": {"total": 3, "issues": 3, "connected": 0, "isolated": 1},
    }
    tasks = [
        _make_task(id="→1", title="Task one"),
        _make_task(id="→2", title="Task two"),
        _make_task(id="→3", title="Task three"),
    ]
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("services.task_autofix._generate_description", new=AsyncMock(return_value="Short description.")), \
         patch("services.task_autofix._save_description", new=AsyncMock()):
        mock_ostk.refine_tasks = AsyncMock(return_value=health_result)
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.post("/api/tasks/health/autofix-all", json={})

    assert resp.status_code == 200
    data = resp.json()
    # isolated is filtered out, two no_description issues should be fixed
    assert data["fixed"] == 2
    assert data["skipped"] == 0
    assert len(data["details"]) == 2


@pytest.mark.asyncio
async def test_autofix_all_returns_zero_when_no_issues(client):
    """autofix-all returns fixed=0 skipped=0 when health check finds no issues."""
    health_result = {
        "tasks": [],
        "issues": [],
        "summary": {"total": 0, "issues": 0, "connected": 0, "isolated": 0},
    }
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.refine_tasks = AsyncMock(return_value=health_result)
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.post("/api/tasks/health/autofix-all", json={})

    assert resp.status_code == 200
    data = resp.json()
    assert data["fixed"] == 0
    assert data["skipped"] == 0


# --- e2e test-data filter (needle →539) ---

@pytest.mark.asyncio
async def test_e2e_tasks_hidden_by_default(client):
    """Tasks with e2e- prefix (any case) are excluded from the default /api/tasks response."""
    mock_tasks = [
        _make_task(id="t-1", title="Real task"),
        _make_task(id="t-2", title="e2e-smoke-task"),
        _make_task(id="t-3", title="E2e-label-task"),
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert "t-1" in ids
    assert "t-2" not in ids, "e2e- task should be hidden by default"
    assert "t-3" not in ids, "E2e- task (capitalized by title cleaner) should be hidden by default"


@pytest.mark.asyncio
async def test_session_lifecycle_tasks_hidden_by_default(client):
    """Session-lifecycle bookkeeping tasks ("Session in <cwd>") must not
    appear in the default /api/tasks response.

    The SessionStart hook files one of these rows per Claude Code boot so
    the session_to_task map can link a transcript back to its row. They
    are telemetry, not user work, and had previously leaked through this
    endpoint even though the Dashboard and Tasks page both hide them. A
    caller that needs them (the backfill script, enrichment tests) must
    pass ?include_session_tasks=true explicitly.
    """
    mock_tasks = [
        _make_task(id="t-real", title="Real user task"),
        _make_task(
            id="t-sess-desc",
            title="Session in torios",
            description="session-task: Work happening in /repo",
        ),
        _make_task(
            id="t-sess-legacy",
            title="Claude Code session claude-code-abc",
            description="old-format telemetry row",
        ),
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        # Default call: session-lifecycle rows are filtered out.
        resp_default = await client.get("/api/tasks")
        # Opt-in call: session-lifecycle rows are included for admin surfaces.
        resp_opt_in = await client.get(
            "/api/tasks?include_session_tasks=true"
        )

    assert resp_default.status_code == 200
    default_ids = [t["id"] for t in resp_default.json()["tasks"]]
    assert "t-real" in default_ids
    assert "t-sess-desc" not in default_ids, (
        "session-task: description row must be hidden by default"
    )
    assert "t-sess-legacy" not in default_ids, (
        "Legacy 'Claude Code session ...' title row must be hidden by default"
    )

    assert resp_opt_in.status_code == 200
    opt_in_ids = [t["id"] for t in resp_opt_in.json()["tasks"]]
    assert "t-real" in opt_in_ids
    assert "t-sess-desc" in opt_in_ids, (
        "?include_session_tasks=true must surface session-lifecycle rows"
    )
    assert "t-sess-legacy" in opt_in_ids


@pytest.mark.asyncio
async def test_e2e_tasks_visible_with_include_test_data(client):
    """Tasks with e2e- prefix are visible when ?include_test_data=true."""
    mock_tasks = [
        _make_task(id="t-1", title="Real task"),
        _make_task(id="t-2", title="e2e-smoke-task"),
        _make_task(id="t-3", title="E2e-label-task"),
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks?include_test_data=true")

    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert "t-1" in ids
    assert "t-2" in ids, "e2e- task should be visible with include_test_data=true"
    assert "t-3" in ids, "E2e- task should be visible with include_test_data=true"


# --- Session-task map: link tasks to Claude Code sessions ---

@pytest.fixture
def _isolated_session_task_map(tmp_path, monkeypatch):
    """Point the session-task map service at a temp file for this test."""
    tmp_store = tmp_path / "session_task_map.json"
    monkeypatch.setenv("MYOS_SESSION_TASK_MAP_PATH", str(tmp_store))
    # Clear between tests to protect against shared state from a prior run.
    from services import session_task_map
    session_task_map.clear()
    yield tmp_store
    session_task_map.clear()


@pytest.mark.asyncio
async def test_create_task_records_session_id_link(client, _isolated_session_task_map):
    """POST /api/tasks with session_id links the new task to the session
    so later GET /api/tasks rows include session_id and child_task_count."""
    from services import session_task_map as stm

    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="added t-sess-1: Session placeholder")
        resp = await client.post(
            "/api/tasks",
            json={
                "title": "Session placeholder",
                "session_id": "abc-123",
            },
        )

    assert resp.status_code == 200
    assert stm.get_task_for_session("abc-123") == "t-sess-1"


@pytest.mark.asyncio
async def test_create_task_records_parent_session_link(client, _isolated_session_task_map):
    """A task created during a session (parent_session_id) is counted as
    a child so child_task_count on the session row is accurate."""
    from services import session_task_map as stm

    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="added t-child-1: Work the session spawned")
        resp = await client.post(
            "/api/tasks",
            json={
                "title": "Work the session spawned",
                "parent_session_id": "abc-123",
            },
        )

    assert resp.status_code == 200
    assert stm.count_children("abc-123") == 1


@pytest.mark.asyncio
async def test_list_tasks_enriches_with_session_id_and_child_count(client, _isolated_session_task_map):
    """GET /api/tasks returns session_id for session-task rows and a
    child_task_count that matches how many child tasks were created
    during that session."""
    from services import session_task_map as stm

    # Pre-seed: t-sess is the auto-filed session task, t-c1 and t-c2 are
    # children created during that same session.
    stm.link_session_to_task("sess-xyz", "t-sess")
    stm.link_child_task("t-c1", "sess-xyz")
    stm.link_child_task("t-c2", "sess-xyz")

    mock_tasks = [
        _make_task(id="t-sess", title="Session in torios", description="session-task: auto-filed"),
        _make_task(id="t-c1", title="Fix login"),
        _make_task(id="t-c2", title="Update docs"),
        _make_task(id="t-other", title="Unrelated task"),
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
        # Session-lifecycle rows are hidden from the default /api/tasks
        # response. Pass include_session_tasks=true so we can assert the
        # enrichment fields on the session-task row itself.
        resp = await client.get("/api/tasks?include_session_tasks=true")

    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["tasks"]}

    assert by_id["t-sess"]["session_id"] == "sess-xyz"
    assert by_id["t-sess"]["child_task_count"] == 2

    # Child tasks also carry the parent session id so the row can link
    # back to the transcript the task was spawned from.
    assert by_id["t-c1"]["session_id"] == "sess-xyz"
    assert by_id["t-c1"]["child_task_count"] == 0

    # Unrelated task has no link.
    assert by_id["t-other"]["session_id"] is None
    assert by_id["t-other"]["child_task_count"] == 0


@pytest.mark.asyncio
async def test_link_session_task_endpoint(client, _isolated_session_task_map):
    """POST /api/sessions/{id}/link-task attaches an existing task to a session."""
    from services import session_task_map as stm

    resp = await client.post(
        "/api/sessions/sess-xyz/link-task",
        json={"task_id": "t-existing"},
    )
    assert resp.status_code == 200
    assert resp.json()["linked"] is True
    assert stm.get_task_for_session("sess-xyz") == "t-existing"


@pytest.mark.asyncio
async def test_link_child_task_endpoint(client, _isolated_session_task_map):
    """POST /api/sessions/{id}/link-child-task records a child task."""
    from services import session_task_map as stm

    resp = await client.post(
        "/api/sessions/sess-xyz/link-child-task",
        json={"task_id": "t-new", "parent_session_id": "sess-xyz"},
    )
    assert resp.status_code == 200
    assert resp.json()["child_task_count"] == 1
    assert stm.count_children("sess-xyz") == 1


@pytest.mark.asyncio
async def test_link_child_task_rejects_mismatched_session_id(client, _isolated_session_task_map):
    """Body parent_session_id must match the path session_id."""
    resp = await client.post(
        "/api/sessions/sess-xyz/link-child-task",
        json={"task_id": "t-new", "parent_session_id": "other-sess"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_child_tasks_count(client, _isolated_session_task_map):
    """GET /api/sessions/{id}/child-tasks returns the child count."""
    from services import session_task_map as stm
    stm.link_child_task("t-1", "sess-xyz")
    stm.link_child_task("t-2", "sess-xyz")

    resp = await client.get("/api/sessions/sess-xyz/child-tasks")
    assert resp.status_code == 200
    assert resp.json() == {"session_id": "sess-xyz", "count": 2}


# --- POST /api/tasks/close-by-session/{session_id} ---

@pytest.mark.asyncio
async def test_close_by_session_closes_linked_task(client, _isolated_session_task_map):
    """close-by-session closes the task linked to the given session_id."""
    from routers.tasks import _recent_closes
    _recent_closes.clear()
    from services import session_task_map as stm
    stm.link_session_to_task("sess-end-1", "t-sess")

    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[
            _make_task(id="t-sess", title="Session in torios", status="open"),
        ])
        mock_ostk.close_task = AsyncMock(return_value="closed t-sess")
        resp = await client.post("/api/tasks/close-by-session/sess-end-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["closed"] is True
    assert body["task_id"] == "t-sess"
    mock_ostk.close_task.assert_called_once_with("t-sess", closed_reason="completed")


@pytest.mark.asyncio
async def test_close_by_session_returns_404_when_no_mapping(client, _isolated_session_task_map):
    """With no session->task mapping, the endpoint 404s."""
    resp = await client.post("/api/tasks/close-by-session/no-such-session")
    assert resp.status_code == 404
    assert "No session task mapping" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_close_by_session_idempotent_when_already_closed(client, _isolated_session_task_map):
    """A second call after close returns closed=false with already_closed reason."""
    from services import session_task_map as stm
    stm.link_session_to_task("sess-end-2", "t-sess")

    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[
            _make_task(id="t-sess", title="Session in torios", status="closed"),
        ])
        mock_ostk.close_task = AsyncMock()
        resp = await client.post("/api/tasks/close-by-session/sess-end-2")

    assert resp.status_code == 200
    body = resp.json()
    assert body["closed"] is False
    assert body["task_id"] == "t-sess"
    assert body["reason"] == "already_closed"
    mock_ostk.close_task.assert_not_called()


@pytest.mark.asyncio
async def test_close_by_session_handles_deleted_task(client, _isolated_session_task_map):
    """If the mapping points at a task that no longer exists, return
    not_found so a repeat hook call is a no-op instead of a 500."""
    from services import session_task_map as stm
    stm.link_session_to_task("sess-end-3", "t-vanished")

    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        mock_ostk.close_task = AsyncMock()
        resp = await client.post("/api/tasks/close-by-session/sess-end-3")

    assert resp.status_code == 200
    body = resp.json()
    assert body["closed"] is False
    assert body["reason"] == "not_found"
    mock_ostk.close_task.assert_not_called()


# --- ostk.update_task_fields service ---

@pytest.mark.asyncio
async def test_update_task_fields_rewrites_title_and_description(tmp_path):
    """update_task_fields rewrites only the targeted needle's title and
    description and leaves other needles untouched."""
    import json
    from services.ostk import OstkService

    needles_dir = tmp_path / ".ostk" / "needles"
    needles_dir.mkdir(parents=True, exist_ok=True)
    issues_path = needles_dir / "issues.jsonl"
    issues_path.write_text(
        "\n".join(
            [
                json.dumps({"id": "\u2192100", "title": "first", "description": "a"}),
                json.dumps({"id": "\u2192101", "title": "Claude Code session claude-code-xyz", "description": "old"}),
                json.dumps({"id": "\u2192102", "title": "last", "description": "c"}),
            ]
        )
        + "\n"
    )

    svc = OstkService.__new__(OstkService)
    svc.cwd = str(tmp_path)

    result = await svc.update_task_fields(
        "\u2192101",
        title="Session in torios",
        description="session-task: Legacy migration. Close when this Claude Code session ends.",
    )
    assert "\u2192101" in result

    rewritten = [json.loads(line) for line in issues_path.read_text().strip().splitlines()]
    by_id = {e["id"]: e for e in rewritten}
    assert by_id["\u2192101"]["title"] == "Session in torios"
    assert "Legacy migration" in by_id["\u2192101"]["description"]
    # Siblings untouched.
    assert by_id["\u2192100"]["title"] == "first"
    assert by_id["\u2192100"]["description"] == "a"
    assert by_id["\u2192102"]["title"] == "last"


@pytest.mark.asyncio
async def test_update_task_fields_requires_title_or_description(tmp_path):
    """Calling update_task_fields with no fields raises OstkError."""
    from services.ostk import OstkService, OstkError

    svc = OstkService.__new__(OstkService)
    svc.cwd = str(tmp_path)

    with pytest.raises(OstkError):
        await svc.update_task_fields("\u2192100")


@pytest.mark.asyncio
async def test_update_task_fields_missing_task_raises(tmp_path):
    """Editing a non-existent task raises OstkError."""
    import json
    from services.ostk import OstkService, OstkError

    needles_dir = tmp_path / ".ostk" / "needles"
    needles_dir.mkdir(parents=True, exist_ok=True)
    (needles_dir / "issues.jsonl").write_text(
        json.dumps({"id": "\u2192100", "title": "t", "description": ""}) + "\n"
    )

    svc = OstkService.__new__(OstkService)
    svc.cwd = str(tmp_path)

    with pytest.raises(OstkError):
        await svc.update_task_fields("\u2192999", title="nope")


# --- scripts/backfill_session_task_titles.py ---

def _load_backfill_module():
    """Import the backfill script by path so tests work regardless of cwd."""
    import importlib.util
    from pathlib import Path

    script_path = (
        Path(__file__).resolve().parent.parent.parent
        / "scripts"
        / "backfill_session_task_titles.py"
    )
    spec = importlib.util.spec_from_file_location(
        "backfill_session_task_titles", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backfill_skips_tasks_already_renamed():
    """Tasks already matching the new 'Session in <basename>' format are
    never touched by the backfill script."""
    bf = _load_backfill_module()

    assert bf._is_already_migrated("Session in torios") is True
    assert bf._is_already_migrated("Session in my-project") is True
    # A legacy task should NOT be treated as migrated.
    assert bf._is_already_migrated("Claude Code session claude-code-abc123") is False
    # Safety: random other titles are also not "migrated"; they just
    # don't match the legacy pattern either.
    assert bf._is_already_migrated("Unrelated task") is False


def test_backfill_renames_legacy_claude_session_titles(monkeypatch):
    """Legacy 'Claude Code session claude-code-...' titles match the
    legacy pattern and are replaced with 'Session in <basename>'."""
    bf = _load_backfill_module()

    # Legacy pattern detection.
    assert bf._is_legacy("Claude Code session claude-code-ae81930b-5") is True
    assert bf._is_legacy("claude code session claude-code-xyz") is True
    assert bf._is_legacy("Session in torios") is False
    assert bf._is_legacy("") is False

    # End-to-end backfill: fake httpx.Client returns a mix of legacy,
    # already-migrated, and unrelated tasks. Only the legacy ones are
    # renamed.
    legacy_task = {
        "id": "\u2192200",
        "title": "Claude Code session claude-code-ae81930b-5",
    }
    migrated_task = {"id": "\u2192201", "title": "Session in torios"}
    unrelated_task = {"id": "\u2192202", "title": "Add login page"}

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    patch_calls: list[dict] = []

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            assert "/api/tasks" in url
            return _FakeResponse(
                {"tasks": [legacy_task, migrated_task, unrelated_task]}
            )

        def patch(self, url, json=None):
            patch_calls.append({"url": url, "body": json})
            return _FakeResponse({"result": "ok"})

    monkeypatch.setattr(bf.httpx, "Client", _FakeClient)
    # Pin the basename so the rename is predictable regardless of test cwd.
    monkeypatch.setattr(bf, "_cwd_basename_default", lambda: "torios")

    renamed = bf.backfill()

    assert renamed == 1, "only the single legacy task should be renamed"
    assert len(patch_calls) == 1
    call = patch_calls[0]
    assert "\u2192200" in call["url"] or "%E2%86%92200" in call["url"]
    assert call["body"]["title"] == "Session in torios"
    assert call["body"]["description"].startswith("session-task: Legacy migration")


def test_backfill_is_idempotent(monkeypatch):
    """A second run against the same data renames nothing. Tasks already
    at the new format are skipped."""
    bf = _load_backfill_module()

    migrated_task = {"id": "\u2192201", "title": "Session in torios"}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"tasks": [migrated_task]}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _FakeResponse()

        def patch(self, url, json=None):
            raise AssertionError("patch must not be called on idempotent run")

    monkeypatch.setattr(bf.httpx, "Client", _FakeClient)
    monkeypatch.setattr(bf, "_cwd_basename_default", lambda: "torios")

    renamed = bf.backfill()
    assert renamed == 0


# --- POST /api/tasks/cleanup-session-duplicates ---
#
# Cleanup endpoint sweeps OPEN session-task rows older than max_age_hours
# (default 1 hour) OR with no live session_id mapping. Live cleanup
# tested separately with a curl call. These unit tests pin the behavior
# so a future refactor cannot regress the dedup story.

@pytest.mark.asyncio
async def test_cleanup_session_duplicates_deletes_stale(client):
    """Open session tasks older than 1 hour with no mapping are deleted."""
    from datetime import datetime, timezone, timedelta

    old_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fresh_iso = datetime.now(timezone.utc).isoformat()

    tasks = [
        # Stale: 2 hours old, session-task: prefix, no mapping. Delete.
        {
            "id": "t-501",
            "title": "Session in torios",
            "priority": "P1",
            "status": "open",
            "tags": [],
            "description": "session-task: stale row",
            "created_at": old_iso,
        },
        # Fresh: less than 1 hour old AND has a mapping. Keep.
        {
            "id": "t-502",
            "title": "Session in torios",
            "priority": "P1",
            "status": "open",
            "tags": [],
            "description": "session-task: fresh row",
            "created_at": fresh_iso,
        },
        # Real user task: never touched even if old.
        {
            "id": "t-503",
            "title": "Write the report",
            "priority": "P1",
            "status": "open",
            "tags": [],
            "description": "Quarterly report draft.",
            "created_at": old_iso,
        },
    ]

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.session_task_map") as mock_map, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.threads_store") as mock_threads, \
         patch("routers.tasks.task_order_store") as mock_order:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        mock_ostk.delete_task = AsyncMock(return_value="deleted")
        # Only t-502 has a live mapping, so t-501 is doubly doomed
        # (stale AND no mapping). t-503 is not a session task.
        mock_map.all_session_task_pairs.return_value = {"sess-fresh": "t-502"}
        mock_tls.remove_task = MagicMock()
        mock_threads.remove_task_from_all_threads = MagicMock()
        mock_order.remove_task = MagicMock()

        resp = await client.post("/api/tasks/cleanup-session-duplicates")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert body["kept"] == 1
    assert body["deleted_ids"] == ["t-501"]
    # Verify only t-501 was deleted, not the fresh session task or the
    # real user task.
    mock_ostk.delete_task.assert_awaited_once_with("t-501")


@pytest.mark.asyncio
async def test_cleanup_session_duplicates_deletes_unmapped_even_when_fresh(client):
    """A session-task row younger than 1 hour is still deleted when it has
    no session_id mapping. No mapping means the session is gone, so the
    row is a leftover that nothing will ever close."""
    from datetime import datetime, timezone

    fresh_iso = datetime.now(timezone.utc).isoformat()
    tasks = [
        {
            "id": "t-510",
            "title": "Session in torios",
            "priority": "P1",
            "status": "open",
            "tags": [],
            "description": "session-task: orphan",
            "created_at": fresh_iso,
        },
    ]

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.session_task_map") as mock_map, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.threads_store") as mock_threads, \
         patch("routers.tasks.task_order_store") as mock_order:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        mock_ostk.delete_task = AsyncMock(return_value="deleted")
        mock_map.all_session_task_pairs.return_value = {}
        mock_tls.remove_task = MagicMock()
        mock_threads.remove_task_from_all_threads = MagicMock()
        mock_order.remove_task = MagicMock()

        resp = await client.post("/api/tasks/cleanup-session-duplicates")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert body["deleted_ids"] == ["t-510"]


@pytest.mark.asyncio
async def test_cleanup_session_duplicates_skips_non_session_tasks(client):
    """Real user tasks must never be deleted by the sweep, even when old."""
    from datetime import datetime, timezone, timedelta

    old_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    tasks = [
        {
            "id": "t-520",
            "title": "Quarterly OKR review",
            "priority": "P1",
            "status": "open",
            "tags": [],
            "description": "Plan Q3 priorities.",
            "created_at": old_iso,
        },
    ]

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.session_task_map") as mock_map:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        mock_ostk.delete_task = AsyncMock(return_value="deleted")
        mock_map.all_session_task_pairs.return_value = {}

        resp = await client.post("/api/tasks/cleanup-session-duplicates")

    assert resp.status_code == 200
    assert resp.json()["deleted"] == 0
    mock_ostk.delete_task.assert_not_called()


# --- SessionStart hook dedup contract ---
#
# The hook itself is bash, but the dedup contract is shared with the
# server-side helpers used by the cleanup endpoint and the link-task
# endpoint. This test pins the contract that two SessionStart calls
# with the SAME session_id NEVER create two entries in
# session_task_map.

def test_session_start_skips_duplicate_for_same_session_id(monkeypatch, tmp_path):
    """Linking the same session_id twice keeps one row, not two."""
    monkeypatch.setenv("MYOS_SESSION_TASK_MAP_PATH", str(tmp_path / "map.json"))
    from services import session_task_map as stm

    stm.link_session_to_task("sess-abc", "t-700")
    # Second SessionStart for the same session_id MUST overwrite, not
    # append. session_task_map is a dict keyed on session_id, so the
    # invariant we care about is: one session_id maps to exactly one
    # task_id, and the link is idempotent for the same pair.
    stm.link_session_to_task("sess-abc", "t-700")

    pairs = stm.all_session_task_pairs()
    assert pairs == {"sess-abc": "t-700"}
    assert stm.get_task_for_session("sess-abc") == "t-700"


# --- Hardened title sanitizer: test-artifact regex ---
#
# The old reject regex anchored at the start of the string and required
# the word "feature" immediately after the prefix, so real smoke titles
# like "Build e2e alpha feature" leaked past the check. These tests
# pin the new behavior: the reject regex must fire when any smoke
# signature appears anywhere in the title.


@pytest.mark.asyncio
async def test_reject_e2e_anywhere_in_title(client):
    """A title with the word e2e in the middle must be rejected."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="should never run")
        resp = await client.post(
            "/api/tasks",
            json={"title": "Build e2e alpha feature", "priority": "P1"},
        )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "test artifact" in body["detail"].lower()
    mock_ostk.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_reject_demo_smoke_prefix(client):
    """Smoke-demo prefixed titles must be rejected."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="should never run")
        resp = await client.post(
            "/api/tasks",
            json={"title": "demo-smoke-roadmap-12345", "priority": "P1"},
        )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "test artifact" in body["detail"].lower()
    mock_ostk.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_reject_label_leaked_into_title(client):
    """A title carrying a machine-generated label name must be rejected."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="should never run")
        resp = await client.post(
            "/api/tasks",
            json={"title": "Something e2e-label-1234567", "priority": "P1"},
        )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "test artifact" in body["detail"].lower()
    mock_ostk.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_test_artifacts_endpoint_deletes_matches(client):
    """The cleanup endpoint deletes every open task matching a smoke signature."""
    real_task = _make_task(id="t-real", title="Build the login form")
    smoke_title = _make_task(id="t-smoke-1", title="Build e2e alpha feature")
    smoke_demo = _make_task(id="t-smoke-2", title="demo-smoke-roadmap-12345")
    smoke_label = _make_task(id="t-smoke-3", title="Something useful")

    all_tasks = [real_task, smoke_title, smoke_demo, smoke_label]

    deleted_calls: list[str] = []

    async def _fake_delete(task_id):
        deleted_calls.append(task_id)
        return "deleted"

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.labels_store") as mock_labels, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.threads_store") as mock_threads, \
         patch("routers.tasks.task_order_store") as mock_order, \
         patch("routers.tasks.recent_deletes") as mock_rd:
        mock_ostk.list_tasks = AsyncMock(return_value=all_tasks)
        mock_ostk.delete_task = AsyncMock(side_effect=_fake_delete)
        mock_labels.list_labels = MagicMock(return_value=[
            {"id": "lbl-smoke", "name": "e2e-label-9999", "color": "#fff"},
            {"id": "lbl-real", "name": "launch", "color": "#000"},
        ])
        mock_labels.delete_label = MagicMock(return_value=True)
        mock_tls.get_all_assignments = MagicMock(return_value={
            "t-smoke-3": ["lbl-smoke"],
            "t-real": ["lbl-real"],
        })
        mock_tls.remove_task = MagicMock()
        mock_tls.remove_label_from_all_tasks = MagicMock()
        mock_threads.remove_task_from_all_threads = MagicMock()
        mock_order.remove_task = MagicMock()
        mock_rd.record = MagicMock()
        mock_rd.record_id = MagicMock()

        resp = await client.post("/api/tasks/cleanup-test-artifacts")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Three of the four tasks match (title or label), the real one stays.
    assert data["deleted"] == 3, data
    assert set(data["deleted_ids"]) == {"t-smoke-1", "t-smoke-2", "t-smoke-3"}
    assert "t-real" not in deleted_calls
    # The machine-generated label is pruned too.
    assert data["deleted_labels"] == 1
    assert data["deleted_label_ids"] == ["lbl-smoke"]


@pytest.mark.asyncio
async def test_cleanup_test_artifacts_matches_e2e_description_source(client):
    """Tasks whose DESCRIPTION references an e2e- automation source
    must be swept even when their TITLE reads like a real user task.
    Regression guard for roadmap-e2e-task-leak.
    """
    # Title is a clean user-readable phrase (would not trip the title
    # regex), but the description carries an e2e- source marker like
    # the real leak: "Follow-up from the automation run called
    # 'e2e-roadmap-probe-...'. The automation suggested ...".
    real_task = _make_task(
        id="t-real",
        title="Ship onboarding wizard",
        description="A real user task with no automation provenance.",
    )
    leaked_task = _make_task(
        id="t-leak",
        title="Ship onboarding wizard",
        description=(
            "Follow-up from the automation run called "
            "'e2e-roadmap-probe-2026-04-17T04-20'. "
            "The automation suggested this next step: Ship onboarding wizard"
        ),
    )
    non_e2e_auto_task = _make_task(
        id="t-prod-auto",
        title="Ship billing flow",
        description=(
            "Follow-up from the automation run called 'quarterly-plan'. "
            "The automation suggested this next step: Ship billing flow"
        ),
    )

    all_tasks = [real_task, leaked_task, non_e2e_auto_task]
    deleted_calls: list[str] = []

    async def _fake_delete(task_id):
        deleted_calls.append(task_id)
        return "deleted"

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.labels_store") as mock_labels, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.threads_store") as mock_threads, \
         patch("routers.tasks.task_order_store") as mock_order, \
         patch("routers.tasks.recent_deletes") as mock_rd:
        mock_ostk.list_tasks = AsyncMock(return_value=all_tasks)
        mock_ostk.delete_task = AsyncMock(side_effect=_fake_delete)
        mock_labels.list_labels = MagicMock(return_value=[])
        mock_labels.delete_label = MagicMock(return_value=False)
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.remove_task = MagicMock()
        mock_tls.remove_label_from_all_tasks = MagicMock()
        mock_threads.remove_task_from_all_threads = MagicMock()
        mock_order.remove_task = MagicMock()
        mock_rd.record = MagicMock()
        mock_rd.record_id = MagicMock()

        resp = await client.post("/api/tasks/cleanup-test-artifacts")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Only the leaked e2e task is swept. The real user task stays.
    # The production auto-task (non-e2e source) also stays, proving
    # the description match does not over-reach.
    assert data["deleted"] == 1, data
    assert data["deleted_ids"] == ["t-leak"]
    assert "t-real" not in deleted_calls
    assert "t-prod-auto" not in deleted_calls


def test_is_e2e_task_matches_description_source():
    """``is_e2e_task`` hides tasks whose description references an e2e-
    automation source so the leaked tasks never reach the user-facing
    list even when the title itself reads clean. Defense-in-depth for
    the display filter.
    """
    from services.task_visibility import is_e2e_task

    # Title prefix (original behaviour).
    assert is_e2e_task({"title": "e2e-crud-12345"}) is True
    # Description source prefix (new behaviour).
    assert is_e2e_task({
        "title": "Ship onboarding wizard",
        "description": (
            "Follow-up from the automation run called "
            "'e2e-roadmap-probe-2026'. The automation suggested ..."
        ),
    }) is True
    # Production auto-task must NOT be hidden.
    assert is_e2e_task({
        "title": "Ship onboarding wizard",
        "description": (
            "Follow-up from the automation run called 'quarterly-plan'. "
            "The automation suggested ..."
        ),
    }) is False
    # Plain user task must NOT be hidden.
    assert is_e2e_task({
        "title": "Ship onboarding wizard",
        "description": "User-written description",
    }) is False


@pytest.mark.asyncio
async def test_reject_all_caps_placeholder_title(client):
    """An all-caps FOO_BAR_BAZ style placeholder is a smoke artifact."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="should never run")
        resp = await client.post(
            "/api/tasks",
            json={"title": "FOO_BAR_BAZ", "priority": "P1"},
        )

    assert resp.status_code == 400, resp.text
    mock_ostk.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_accept_normal_title_with_letters(client):
    """Regression guard: a normal title must still pass the hardened check."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-ok")
        resp = await client.post(
            "/api/tasks",
            json={"title": "Plan the offsite agenda", "priority": "P1"},
        )

    assert resp.status_code == 200, resp.text
    mock_ostk.add_task.assert_called_once()


# --- include_test_data=true bypasses e2e rejection ---
#
# The smoke script creates tasks with titles like "e2e-crud-<timestamp>"
# to exercise the CRUD lifecycle. Those POSTs pass
# ?include_test_data=true so the sanitizer lets the e2e- prefix
# through while still blocking pure UI noise.


@pytest.mark.asyncio
async def test_reject_bad_title_allows_e2e_when_test_data_flag(client):
    """POST /tasks?include_test_data=true accepts an e2e- smoke title."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-smoke")
        resp = await client.post(
            "/api/tasks?include_test_data=true",
            json={"title": "e2e-crud-1760000000", "priority": "P1"},
        )

    assert resp.status_code == 200, resp.text
    mock_ostk.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_reject_bad_title_allows_e2e_label_leak_when_test_data_flag(client):
    """include_test_data=true also lets an e2e-label-<n> leak through."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-label-smoke")
        resp = await client.post(
            "/api/tasks?include_test_data=true",
            json={"title": "Something e2e-label-1234567", "priority": "P1"},
        )

    assert resp.status_code == 200, resp.text
    mock_ostk.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_reject_bad_title_still_rejects_feature_alpha_even_with_test_data(client):
    """Pure UI noise (feature alpha) must reject even with the flag.

    The test-data flag only loosens e2e-prefix rejection. Smoke markers
    that never belong in any task list (feature alpha, demo-smoke-*,
    all-caps placeholders) must keep returning 400.
    """
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="should never run")
        resp = await client.post(
            "/api/tasks?include_test_data=true",
            json={"title": "feature alpha preview", "priority": "P1"},
        )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "test artifact" in body["detail"].lower()
    mock_ostk.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_reject_bad_title_still_rejects_demo_smoke_with_test_data(client):
    """demo-smoke-* prefixes stay rejected even under the test-data flag."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="should never run")
        resp = await client.post(
            "/api/tasks?include_test_data=true",
            json={"title": "demo-smoke-roadmap-12345", "priority": "P1"},
        )

    assert resp.status_code == 400, resp.text
    mock_ostk.add_task.assert_not_called()


@pytest.mark.asyncio
async def test_reject_bad_title_still_rejects_all_caps_with_test_data(client):
    """All-caps placeholder stays rejected even under the test-data flag."""
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="should never run")
        resp = await client.post(
            "/api/tasks?include_test_data=true",
            json={"title": "FOO_BAR_BAZ", "priority": "P1"},
        )

    assert resp.status_code == 400, resp.text
    mock_ostk.add_task.assert_not_called()


def test_reject_bad_title_unit_allows_e2e_when_test_data_flag():
    """Unit-level: the helper accepts e2e titles when allow_test_data=True."""
    from routers.tasks import _reject_bad_title

    # Default behavior: e2e-prefixed titles still reject.
    assert _reject_bad_title("e2e-crud-123") is not None
    # With the flag, e2e-prefixed titles pass.
    assert _reject_bad_title("e2e-crud-123", allow_test_data=True) is None
    assert _reject_bad_title("Some e2e label leak", allow_test_data=True) is None
    assert (
        _reject_bad_title("Something e2e-label-1234567", allow_test_data=True)
        is None
    )


def test_reject_bad_title_unit_still_rejects_feature_alpha_with_flag():
    """Unit-level: allow_test_data=True must not loosen non-e2e rejections."""
    from routers.tasks import _reject_bad_title

    # Pure UI noise always rejects, flag or no flag.
    assert (
        _reject_bad_title("Build feature alpha now", allow_test_data=True)
        is not None
    )
    assert (
        _reject_bad_title("demo-smoke-roadmap-1", allow_test_data=True)
        is not None
    )
    assert _reject_bad_title("FOO_BAR_BAZ", allow_test_data=True) is not None
    assert _reject_bad_title("build-123 thing", allow_test_data=True) is not None


# --- Live-agent status overlay on GET /api/tasks ---------------------------
#
# These tests exercise the behavior described in the Tasks-are-live-while-
# agents-run requirement: any task with at least one running agent linked to
# it reports ``status=in_progress`` on read, and the override disappears
# when the last live agent ends. Terminal tasks are never touched.


@pytest.mark.asyncio
async def test_list_tasks_overlay_forces_in_progress_when_agent_is_live(client):
    """A task with a live agent linked to it reports in_progress.

    The stored status is still 'open' on disk; only the response is
    overridden. This ensures the Tasks board reflects "work is
    happening right now" regardless of which spawn path launched the
    agent.
    """
    from routers.agents import agent_metadata

    mock_tasks = [_make_task(id="task-live", status="open")]
    agent_metadata["agent-live-1"] = {
        "status": "running",
        "task_id": "task-live",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
            resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        returned = resp.json()["tasks"]
        assert len(returned) == 1
        assert returned[0]["id"] == "task-live"
        assert returned[0]["status"] == "in_progress"
    finally:
        agent_metadata.pop("agent-live-1", None)


@pytest.mark.asyncio
async def test_list_tasks_overlay_falls_back_when_agent_completes(client):
    """Once the agent has a completed_at timestamp the override stops.

    The task's stored status wins again. We do NOT auto-close or
    auto-advance the task; we simply stop forcing in_progress.
    """
    from routers.agents import agent_metadata

    mock_tasks = [_make_task(id="task-done", status="open")]
    agent_metadata["agent-completed-1"] = {
        "status": "completed",
        "task_id": "task-done",
        "completed_at": "2026-04-22T00:00:00+00:00",
    }
    try:
        with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
            resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        returned = resp.json()["tasks"]
        assert returned[0]["status"] == "open"
    finally:
        agent_metadata.pop("agent-completed-1", None)


@pytest.mark.asyncio
async def test_list_tasks_overlay_skips_terminal_closed_tasks(client):
    """Closed tasks are never re-surfaced as in_progress by the overlay."""
    from routers.agents import agent_metadata

    mock_tasks = [_make_task(id="task-closed", status="closed")]
    agent_metadata["agent-on-closed-task"] = {
        "status": "running",
        "task_id": "task-closed",
    }
    try:
        with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
            resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        returned = resp.json()["tasks"]
        # Closed must stay closed regardless of live agents.
        assert returned[0]["status"] == "closed"
    finally:
        agent_metadata.pop("agent-on-closed-task", None)


@pytest.mark.asyncio
async def test_list_tasks_overlay_skips_terminal_shelved_tasks(client):
    """Shelved tasks are never re-surfaced as in_progress by the overlay."""
    from routers.agents import agent_metadata

    mock_tasks = [_make_task(id="task-shelved", status="shelved")]
    agent_metadata["agent-on-shelved-task"] = {
        "status": "running",
        "task_id": "task-shelved",
    }
    try:
        with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
            resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        returned = resp.json()["tasks"]
        # Shelved must stay shelved regardless of live agents.
        assert returned[0]["status"] == "shelved"
    finally:
        agent_metadata.pop("agent-on-shelved-task", None)


@pytest.mark.asyncio
async def test_list_tasks_overlay_survives_multiple_agents_on_one_task(client):
    """Two concurrent agents on one task keep it in_progress.

    When one finishes, the override stays on because the other is
    still live. Only when BOTH are done does the override drop.
    """
    from routers.agents import agent_metadata

    # Use a unique task id so leftover rows from other tests with
    # different task_ids cannot pollute this assertion.
    tid = "task-multi-overlay-xyz"

    def _fresh_tasks():
        # Return a fresh list each call so the overlay's in-place
        # status mutation does not leak across the three probes below.
        # In production ``ostk.list_tasks`` reads from disk every call
        # and already returns a fresh list, so this only matters in
        # the mocked test path.
        return [_make_task(id=tid, status="open")]

    # Remove any stale rows that reference this task id, then seed two
    # fresh live agents. Copy keys to a list so we can mutate in-flight.
    for _name in [k for k, v in agent_metadata.items()
                  if isinstance(v, dict) and v.get("task_id") == tid]:
        agent_metadata.pop(_name, None)
    _now = datetime.now(timezone.utc).isoformat()
    agent_metadata["agent-A-overlay-xyz"] = {"status": "running", "task_id": tid, "spawned_at": _now}
    agent_metadata["agent-B-overlay-xyz"] = {"status": "running", "task_id": tid, "spawned_at": _now}
    try:
        # Both live: overlay forces in_progress.
        with _patch_ostk_and_labels(list_tasks=AsyncMock(side_effect=lambda **kw: _fresh_tasks())):
            resp = await client.get("/api/tasks")
        assert resp.json()["tasks"][0]["status"] == "in_progress"

        # One finishes, one still live: still in_progress.
        agent_metadata["agent-A-overlay-xyz"]["status"] = "completed"
        agent_metadata["agent-A-overlay-xyz"]["completed_at"] = "2026-04-22T00:00:00+00:00"
        with _patch_ostk_and_labels(list_tasks=AsyncMock(side_effect=lambda **kw: _fresh_tasks())):
            resp = await client.get("/api/tasks")
        assert resp.json()["tasks"][0]["status"] == "in_progress"

        # Both finished: override drops, stored status wins.
        agent_metadata["agent-B-overlay-xyz"]["status"] = "completed"
        agent_metadata["agent-B-overlay-xyz"]["completed_at"] = "2026-04-22T00:01:00+00:00"
        with _patch_ostk_and_labels(list_tasks=AsyncMock(side_effect=lambda **kw: _fresh_tasks())):
            resp = await client.get("/api/tasks")
        assert resp.json()["tasks"][0]["status"] == "open"
    finally:
        agent_metadata.pop("agent-A-overlay-xyz", None)
        agent_metadata.pop("agent-B-overlay-xyz", None)


@pytest.mark.asyncio
async def test_list_tasks_overlay_ignores_agents_without_task_linkage(client):
    """Agents spawned without a task_id never promote any task."""
    from routers.agents import agent_metadata

    mock_tasks = [_make_task(id="task-isolated", status="open")]
    agent_metadata["agent-freelance"] = {"status": "running"}  # no task_id
    try:
        with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)):
            resp = await client.get("/api/tasks")
        # Nothing to promote; stored status wins.
        assert resp.json()["tasks"][0]["status"] == "open"
    finally:
        agent_metadata.pop("agent-freelance", None)


# --- Wave planning tests (→1370) ---

@pytest.mark.asyncio
async def test_plan_waves_returns_response_shape(client, tmp_path, monkeypatch):
    """GET /tasks/waves returns the expected JSON shape with waves + total_needles."""
    import routers.tasks as rt
    monkeypatch.setattr(rt, "_WAVES_PATH", tmp_path / "waves.json")

    tasks = [
        _make_task(id="a", title="Fix auth bug", priority="P0"),
        _make_task(id="b", title="Add dark mode", priority="P1"),
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=tasks)):
        resp = await client.get("/api/tasks/waves")

    assert resp.status_code == 200
    body = resp.json()
    assert "waves" in body
    assert "total_needles" in body
    assert body["total_needles"] == 2
    # Every wave has the required keys
    for wave in body["waves"]:
        assert "wave" in wave
        assert "needles" in wave
        assert "blocked_by_prior" in wave
        for n in wave["needles"]:
            assert "id" in n
            assert "title" in n
            assert "priority" in n
            assert "scope_hint" in n


@pytest.mark.asyncio
async def test_plan_waves_persists_assignments(client, tmp_path, monkeypatch):
    """GET /tasks/waves writes a waves.json sidecar with task_id → wave_number."""
    import routers.tasks as rt
    waves_path = tmp_path / "waves.json"
    monkeypatch.setattr(rt, "_WAVES_PATH", waves_path)

    tasks = [
        _make_task(id="x1", title="Fix login", priority="P0"),
        _make_task(id="x2", title="Update docs", priority="P2"),
    ]
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=tasks)):
        resp = await client.get("/api/tasks/waves")

    assert resp.status_code == 200
    assert waves_path.exists(), "waves.json should have been written"
    import json
    data = json.loads(waves_path.read_text())
    assert "assignments" in data
    assert "x1" in data["assignments"]
    assert "x2" in data["assignments"]
    assert isinstance(data["assignments"]["x1"], int)


@pytest.mark.asyncio
async def test_get_wave_assignments_returns_empty_when_no_file(client, tmp_path, monkeypatch):
    """GET /tasks/waves/assignments returns {} when no waves.json exists."""
    import routers.tasks as rt
    monkeypatch.setattr(rt, "_WAVES_PATH", tmp_path / "missing.json")

    resp = await client.get("/api/tasks/waves/assignments")
    assert resp.status_code == 200
    assert resp.json() == {"assignments": {}}


@pytest.mark.asyncio
async def test_get_wave_assignments_returns_saved_data(client, tmp_path, monkeypatch):
    """GET /tasks/waves/assignments returns previously saved assignments."""
    import json
    import routers.tasks as rt
    waves_path = tmp_path / "waves.json"
    waves_path.write_text(json.dumps({"assignments": {"t1": 1, "t2": 2}}))
    monkeypatch.setattr(rt, "_WAVES_PATH", waves_path)

    resp = await client.get("/api/tasks/waves/assignments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["assignments"] == {"t1": 1, "t2": 2}


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/clarify/suggest  (→1565)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_clarify_suggest_returns_proposed_fix(client):
    """Returns {proposed_fix, rationale} without persisting anything."""
    from unittest.mock import AsyncMock, patch

    task = _make_task(id="→42", title="Ship the widget", description="TBD details")

    fake_suggest = AsyncMock(return_value={"proposed_fix": "Ship the widget by doing X", "rationale": "TBD removed"})

    with (
        patch("routers.tasks.ostk.list_tasks", AsyncMock(return_value=[task])),
        patch("routers.tasks.task_labels_store.get_all_assignments", MagicMock(return_value={})),
        patch("services.clarity_suggest.suggest_clarification", fake_suggest),
    ):
        resp = await client.post("/api/tasks/42/clarify/suggest", json={"check": "outcome_concrete"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["proposed_fix"] == "Ship the widget by doing X"
    assert body["rationale"] == "TBD removed"


@pytest.mark.asyncio
async def test_task_clarify_suggest_404_when_task_missing(client):
    with patch("routers.tasks.ostk.list_tasks", AsyncMock(return_value=[])):
        resp = await client.post("/api/tasks/999/clarify/suggest", json={"check": "outcome_concrete"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_task_clarify_suggest_400_when_no_api_key(client):
    task = _make_task(id="→42", title="Task", description="desc")

    async def _raise_valueerror(*a, **kw):
        raise ValueError("No Anthropic API key configured.")

    with (
        patch("routers.tasks.ostk.list_tasks", AsyncMock(return_value=[task])),
        patch("routers.tasks.task_labels_store.get_all_assignments", MagicMock(return_value={})),
        patch("services.clarity_suggest.suggest_clarification", _raise_valueerror),
    ):
        resp = await client.post("/api/tasks/42/clarify/suggest", json={"check": "outcome_concrete"})

    assert resp.status_code == 400
    assert "No Anthropic API key" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/tasks/{task_id}/clarify/apply  (→1565)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_clarify_apply_persists_and_returns_fresh_checks(client):
    """apply updates the description and returns re-computed readiness checks."""
    from unittest.mock import AsyncMock, patch

    task = _make_task(id="→42", title="Ship the widget", description="Initial desc")

    updated_task = dict(task, description="Initial desc\n\nAI fix text")

    with (
        patch("routers.tasks.ostk.list_tasks", AsyncMock(side_effect=[
            [task],      # first call: fetch task to apply to
            [updated_task],  # second call: re-fetch after update
        ])),
        patch("routers.tasks.task_labels_store.get_all_assignments", MagicMock(return_value={})),
        patch("routers.tasks.ostk.update_task_fields", AsyncMock(return_value="ok")),
    ):
        resp = await client.post(
            "/api/tasks/42/clarify/apply",
            json={"check": "outcome_concrete", "fix": "AI fix text"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "checks" in body
    assert "ready" in body
    # outcome_concrete should pass for "Ship the widget" + "AI fix text" (no vague tokens)
    check_names = [c["name"] for c in body["checks"]]
    assert "outcome_concrete" in check_names


@pytest.mark.asyncio
async def test_task_clarify_apply_404_when_task_missing(client):
    with patch("routers.tasks.ostk.list_tasks", AsyncMock(return_value=[])):
        resp = await client.post(
            "/api/tasks/999/clarify/apply",
            json={"check": "outcome_concrete", "fix": "some fix"},
        )
    assert resp.status_code == 404


# --- closed-task readiness skip (→1574 perf) ---

@pytest.mark.asyncio
async def test_closed_tasks_skip_readiness_check(client):
    """Closed tasks get clear_to_build=False and empty checks without calling compute_task_readiness."""
    closed = _make_task(id="c-1", status="closed")
    open_task = _make_task(id="o-1", status="open")

    readiness_called_for = []

    def fake_readiness(t):
        readiness_called_for.append(t.get("id"))
        r = MagicMock()
        r.ready = False
        r.as_dict.return_value = {"checks": [{"name": "outcome_concrete", "pass": False}]}
        return r

    with _patch_ostk_and_labels(
        list_tasks=AsyncMock(return_value=[closed, open_task])
    ):
        with patch("services.gemini_ready.compute_task_readiness", side_effect=fake_readiness):
            resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    tasks = {t["id"]: t for t in resp.json()["tasks"]}

    # Closed task: readiness not called, checks are empty
    assert "c-1" not in readiness_called_for
    assert tasks["c-1"]["clear_to_build"] is False
    assert tasks["c-1"]["clear_to_build_checks"] == []

    # Open task: readiness was called
    assert "o-1" in readiness_called_for


@pytest.mark.asyncio
async def test_closed_tasks_clear_to_build_false_even_if_readiness_import_fails(client):
    """Closed tasks always get clear_to_build=False regardless of the readiness import path."""
    closed = _make_task(id="c-2", status="closed")

    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=[closed])):
        with patch("services.gemini_ready.compute_task_readiness", side_effect=ImportError("no module")):
            resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["clear_to_build"] is False


# --- Performance / event-loop-safety regression (large task set) ---
#
# Guards the GET /api/tasks hot path against two regressions that would
# wedge the backend on a large project:
#   1. O(N) ostk daemon calls — the handler must read the whole task list
#      with a SINGLE ``ostk.list_tasks`` call and do all per-task work
#      (enrichment, readiness, plan-path, overlays) in memory. A future
#      change that shells out to ostk once per task would scale the page
#      load with the needle count and starve the event loop.
#   2. Slow synchronous per-task work — with ~130 tasks the endpoint must
#      still return quickly. The assertion is intentionally loose (a few
#      seconds) so it flags an O(N) subprocess regression without being
#      flaky on slow CI; the real handler completes well under that.
#
# Context: a 2026-06-01 report blamed a /api/tasks hang on per-task work
# in this handler. Measurement showed the handler itself is fast (one
# daemon read + in-memory enrichment); this test locks that contract in.

@pytest.mark.asyncio
async def test_list_tasks_large_set_single_daemon_call_and_fast(client):
    # ~130 open tasks, mirroring a long-lived project's active needle set.
    mock_tasks = [
        _make_task(
            id=f"t-{i}",
            title=f"Task number {i} with a concrete outcome",
            description=f"Implement feature {i} in api/services/thing_{i}.py",
        )
        for i in range(130)
    ]
    call_counter = {"n": 0}

    async def _list_tasks(*args, **kwargs):
        call_counter["n"] += 1
        return list(mock_tasks)

    import time as _time
    with _patch_ostk_and_labels(list_tasks=_list_tasks):
        start = _time.monotonic()
        resp = await client.get("/api/tasks")
        elapsed = _time.monotonic() - start

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 130
    # Contract 1: the daemon task list is read exactly once, never once
    # per task. A regression that calls ostk per task would push this >1.
    assert call_counter["n"] == 1, (
        f"GET /api/tasks made {call_counter['n']} ostk.list_tasks calls; "
        "expected exactly 1 (no O(N) daemon calls)"
    )
    # Contract 2: in-memory enrichment of 130 tasks stays fast.
    assert elapsed < 5.0, (
        f"GET /api/tasks took {elapsed:.2f}s for 130 tasks; "
        "expected well under 5s (per-task work must not block the loop)"
    )


@pytest.mark.asyncio
async def test_list_tasks_does_not_shell_out_per_task(client):
    """The enrichment/readiness/overlay passes must not invoke the ostk
    transport (``_run`` / ``_run_socket``) per task. Only the single
    ``list_tasks`` read is allowed to touch the daemon."""
    mock_tasks = [_make_task(id=f"t-{i}", title=f"Concrete task {i}") for i in range(130)]

    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=mock_tasks)) as ctx:
        # Any per-task daemon round-trip would go through _run/_run_socket.
        ctx.mock_ostk._run = AsyncMock(
            side_effect=AssertionError("_run called during list_tasks enrichment")
        )
        ctx.mock_ostk._run_socket = AsyncMock(
            side_effect=AssertionError("_run_socket called during list_tasks enrichment")
        )
        ctx.mock_ostk.cwd = "/tmp"
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    assert len(resp.json()["tasks"]) == 130
