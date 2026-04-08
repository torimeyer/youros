import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- Helpers ---

def _make_llm_response(data: dict) -> MagicMock:
    """Build a mock Anthropic message response containing JSON text."""
    text_block = SimpleNamespace(type="text", text=json.dumps(data))
    response = MagicMock()
    response.content = [text_block]
    response.usage = SimpleNamespace(input_tokens=100, output_tokens=200)
    return response


VALID_LLM_DATA = {
    "goal": {
        "title": "Get taxes filed",
        "description": "File your taxes without stress or penalties",
    },
    "tasks": [
        {"title": "Gather W-2s and 1099s from employers and banks", "priority": "P1"},
        {"title": "Pick a filing method", "priority": "P1"},
        {"title": "Fill out and review the return", "priority": "P1"},
        {"title": "Submit the return and save confirmation", "priority": "P2"},
    ],
}


# --- POST /api/onboarding/dream ---

@pytest.mark.asyncio
async def test_dream_returns_goal_and_tasks(client):
    """Happy path: LLM returns valid JSON, tasks are persisted."""
    mock_response = _make_llm_response(VALID_LLM_DATA)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
            "done_looks_like": "Taxes filed",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["goal"]["title"] == "Get taxes filed"
    assert len(data["tasks"]) == 4
    assert data["tasks"][0]["priority"] == "P1"

    # The dream endpoint returns the generated plan. Persistence is handled
    # by the frontend calling /api/tasks for each task, so add_task is not
    # called from inside this endpoint anymore.
    assert mock_ostk.add_task.call_count == 0


@pytest.mark.asyncio
async def test_dream_without_done_looks_like(client):
    """The done_looks_like field is optional."""
    mock_response = _make_llm_response(VALID_LLM_DATA)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to clean my garage",
        })

    assert resp.status_code == 200
    assert "goal" in resp.json()
    assert "tasks" in resp.json()


@pytest.mark.asyncio
async def test_dream_empty_dreading_returns_422(client):
    """An empty dreading string should be rejected."""
    resp = await client.post("/api/onboarding/dream", json={
        "dreading": "   ",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_dream_missing_dreading_returns_422(client):
    """Missing the required dreading field should return 422."""
    resp = await client.post("/api/onboarding/dream", json={})
    assert resp.status_code == 422


# --- Fallback behavior ---

@pytest.mark.asyncio
async def test_dream_fallback_when_no_api_key(client):
    """When no API key is available, return a sensible fallback plan."""
    with (
        patch("routers.onboarding._resolve_api_key", return_value=""),
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "goal" in data
    assert len(data["tasks"]) > 0
    # The endpoint returns the plan. Persistence happens on the frontend.
    assert mock_ostk.add_task.call_count == 0


@pytest.mark.asyncio
async def test_dream_fallback_when_llm_returns_bad_json(client):
    """When the LLM returns invalid JSON, fall back to a generic plan."""
    bad_response = MagicMock()
    bad_response.content = [SimpleNamespace(type="text", text="This is not JSON at all")]
    bad_response.usage = SimpleNamespace(input_tokens=50, output_tokens=50)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=bad_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "goal" in data
    assert len(data["tasks"]) > 0


@pytest.mark.asyncio
async def test_dream_fallback_when_llm_api_error(client):
    """When the Anthropic API raises an error, fall back gracefully."""
    import anthropic as anth

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(
            side_effect=anth.APIError(
                message="overloaded",
                request=MagicMock(),
                body=None,
            )
        )
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "goal" in data
    assert len(data["tasks"]) > 0


@pytest.mark.asyncio
async def test_dream_fallback_when_llm_missing_fields(client):
    """When the LLM returns JSON but with missing required fields, fall back."""
    incomplete_data = {"goal": {"title": "Test"}}  # missing description and tasks
    mock_response = _make_llm_response(incomplete_data)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "goal" in data
    assert len(data["tasks"]) > 0


# --- Returned plan shape ---

@pytest.mark.asyncio
async def test_dream_returns_tasks_with_correct_priorities(client):
    """Each generated task should appear in the response with its priority.

    Note: the dream endpoint no longer persists tasks itself. The frontend
    reads the response and calls POST /api/tasks for each task. This test
    pins the returned ordering and priority so the frontend can rely on it.
    """
    mock_response = _make_llm_response(VALID_LLM_DATA)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert tasks[0]["title"] == "Gather W-2s and 1099s from employers and banks"
    assert tasks[0]["priority"] == "P1"
    assert tasks[1]["title"] == "Pick a filing method"
    assert tasks[1]["priority"] == "P1"
    assert tasks[2]["title"] == "Fill out and review the return"
    assert tasks[2]["priority"] == "P1"
    assert tasks[3]["title"] == "Submit the return and save confirmation"
    assert tasks[3]["priority"] == "P2"
    # The endpoint must not write to ostk directly; the frontend does that.
    assert mock_ostk.add_task.call_count == 0


@pytest.mark.asyncio
async def test_dream_still_returns_plan_when_task_persistence_fails(client):
    """If ostk fails to save a task, the endpoint should still return the plan."""
    from services.ostk import OstkError

    mock_response = _make_llm_response(VALID_LLM_DATA)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(side_effect=OstkError("disk full"))

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    # The endpoint should still succeed with the generated plan
    assert resp.status_code == 200
    data = resp.json()
    assert data["goal"]["title"] == "Get taxes filed"
    assert len(data["tasks"]) == 4


# --- done_looks_like in fallback ---

@pytest.mark.asyncio
async def test_dream_fallback_uses_done_looks_like_in_description(client):
    """When falling back, the done_looks_like text should appear in the goal description."""
    with (
        patch("routers.onboarding._resolve_api_key", return_value=""),
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
            "done_looks_like": "Taxes filed and no penalties",
        })

    data = resp.json()
    assert data["goal"]["description"] == "Taxes filed and no penalties"


# --- Auto-labeling for the persist helper ---

@pytest.mark.asyncio
async def test_persist_tasks_schedules_auto_labels():
    """The onboarding _persist_tasks helper must run auto-labeling on each task.

    Even though the dream endpoint currently lets the frontend do persistence,
    the helper exists and must wire auto-labeling so any future caller stays
    consistent with POST /api/tasks.
    """
    from routers.onboarding import _persist_tasks, DreamResponse, GoalItem, TaskItem

    plan = DreamResponse(
        goal=GoalItem(title="Get taxes filed", description="File without stress"),
        tasks=[
            TaskItem(title="Gather W-2s", priority="P1"),
            TaskItem(title="Pick a filing method", priority="P1"),
        ],
    )

    with (
        patch("routers.onboarding.ostk") as mock_ostk,
        patch("routers.onboarding.schedule_auto_labels") as mock_schedule,
    ):
        mock_ostk.add_task = AsyncMock(side_effect=[
            "added 601: Gather W-2s [P1]",
            "added 602: Pick a filing method [P1]",
        ])
        await _persist_tasks(plan)

    assert mock_schedule.call_count == 2
    first_args, _ = mock_schedule.call_args_list[0]
    assert first_args == ("601", "Gather W-2s", "")
    second_args, _ = mock_schedule.call_args_list[1]
    assert second_args == ("602", "Pick a filing method", "")


@pytest.mark.asyncio
async def test_persist_tasks_skips_auto_labels_on_ostk_failure():
    """If ostk.add_task fails, do not schedule auto-labels for that task."""
    from routers.onboarding import _persist_tasks, DreamResponse, GoalItem, TaskItem
    from services.ostk import OstkError

    plan = DreamResponse(
        goal=GoalItem(title="Test goal", description="desc"),
        tasks=[
            TaskItem(title="task that fails", priority="P1"),
            TaskItem(title="task that succeeds", priority="P1"),
        ],
    )

    with (
        patch("routers.onboarding.ostk") as mock_ostk,
        patch("routers.onboarding.schedule_auto_labels") as mock_schedule,
    ):
        mock_ostk.add_task = AsyncMock(side_effect=[
            OstkError("disk full"),
            "added 701: task that succeeds [P1]",
        ])
        await _persist_tasks(plan)

    # Only the successful task gets auto-labeled.
    assert mock_schedule.call_count == 1
    args, _ = mock_schedule.call_args_list[0]
    assert args == ("701", "task that succeeds", "")
