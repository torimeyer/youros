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

    # Verify all tasks were persisted
    assert mock_ostk.add_task.call_count == 4


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
    # Fallback tasks are still persisted
    assert mock_ostk.add_task.call_count == len(data["tasks"])


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


# --- Task persistence ---

@pytest.mark.asyncio
async def test_dream_persists_tasks_with_correct_priorities(client):
    """Each generated task should be persisted with its assigned priority."""
    mock_response = _make_llm_response(VALID_LLM_DATA)

    with (
        patch("routers.onboarding._resolve_api_key", return_value="test-key"),
        patch("routers.onboarding.anthropic.AsyncAnthropic") as MockClient,
        patch("routers.onboarding.ostk") as mock_ostk,
    ):
        instance = MockClient.return_value
        instance.messages.create = AsyncMock(return_value=mock_response)
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        await client.post("/api/onboarding/dream", json={
            "dreading": "I need to do my taxes",
        })

    # Check the calls match the expected task titles and priorities
    calls = mock_ostk.add_task.call_args_list
    assert calls[0].args == ("Gather W-2s and 1099s from employers and banks", "P1")
    assert calls[1].args == ("Pick a filing method", "P1")
    assert calls[2].args == ("Fill out and review the return", "P1")
    assert calls[3].args == ("Submit the return and save confirmation", "P2")


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
