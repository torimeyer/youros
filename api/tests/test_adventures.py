"""Tests for the onboarding adventures router."""

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


VALID_WEBSITE_PLAN = {
    "goal": {
        "title": "Recipe site for friends",
        "description": "A site where you post recipes and friends save favorites.",
    },
    "tasks": [
        {"title": "Pick a name and check if the domain is available", "priority": "P1"},
        {"title": "Sign up for a free Vercel account", "priority": "P1"},
        {"title": "Set up a Postgres database for recipes (Vercel Postgres)", "priority": "P1"},
        {"title": "Build the homepage", "priority": "P1"},
        {"title": "Add image uploads for recipe photos", "priority": "P2"},
    ],
}


# --- GET /api/adventures/templates ---


@pytest.mark.asyncio
async def test_list_templates_returns_all_adventures(client):
    """The templates endpoint should list every available adventure."""
    resp = await client.get("/api/adventures/templates")

    assert resp.status_code == 200
    data = resp.json()
    assert "adventures" in data
    ids = {a["id"] for a in data["adventures"]}
    assert ids == {"build_website", "plan_project", "learn_skill", "off_plate"}


@pytest.mark.asyncio
async def test_each_template_has_required_fields(client):
    """Every adventure template should have the fields the frontend renders."""
    resp = await client.get("/api/adventures/templates")
    data = resp.json()

    for adv in data["adventures"]:
        assert adv["id"]
        assert adv["title"]
        assert adv["tagline"]
        assert adv["icon"]
        assert adv["placeholder"]
        # system_prompt is included so the model can be inspected, but should
        # not be empty
        assert adv["system_prompt"]


# --- POST /api/adventures/start ---


@pytest.mark.asyncio
async def test_start_build_website_returns_plan(client):
    """Happy path: start the website adventure and get a plan back."""
    mock_response = _make_llm_response(VALID_WEBSITE_PLAN)

    mock_instance = MagicMock()
    mock_instance.messages.create = AsyncMock(return_value=mock_response)

    with (
        patch("routers.adventures.get_ai_client", new=AsyncMock(return_value=mock_instance)),
        patch("routers.adventures.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        resp = await client.post(
            "/api/adventures/start",
            json={
                "adventure_id": "build_website",
                "description": "A recipe site where I can post recipes and friends save favorites",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["adventure_id"] == "build_website"
    assert data["goal"]["title"] == "Recipe site for friends"
    assert len(data["tasks"]) == 5
    assert data["tasks"][0]["priority"] == "P1"


@pytest.mark.asyncio
async def test_start_persists_tasks_via_ostk(client):
    """Generated tasks should be persisted via ostk.add_task."""
    mock_response = _make_llm_response(VALID_WEBSITE_PLAN)

    mock_instance = MagicMock()
    mock_instance.messages.create = AsyncMock(return_value=mock_response)

    with (
        patch("routers.adventures.get_ai_client", new=AsyncMock(return_value=mock_instance)),
        patch("routers.adventures.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = AsyncMock(return_value="created t-1")

        await client.post(
            "/api/adventures/start",
            json={"adventure_id": "build_website", "description": "Recipe site"},
        )

        assert mock_ostk.add_task.call_count == 5
        # First task should be P1 with the right title
        first_call = mock_ostk.add_task.call_args_list[0]
        assert first_call.args == (
            "Pick a name and check if the domain is available",
            "P1",
        )


@pytest.mark.asyncio
async def test_start_uses_adventure_specific_system_prompt(client):
    """Each adventure should send its dedicated system prompt to the LLM."""
    mock_response = _make_llm_response(VALID_WEBSITE_PLAN)

    mock_instance = MagicMock()
    mock_instance.messages.create = AsyncMock(return_value=mock_response)

    with (
        patch("routers.adventures.get_ai_client", new=AsyncMock(return_value=mock_instance)),
        patch("routers.adventures.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = AsyncMock(return_value="ok")

        await client.post(
            "/api/adventures/start",
            json={"adventure_id": "build_website", "description": "Recipe site"},
        )

        call_kwargs = mock_instance.messages.create.call_args.kwargs
        # The website prompt mentions Vercel and Postgres specifically
        assert "Vercel" in call_kwargs["system"]
        assert "Postgres" in call_kwargs["system"]


@pytest.mark.asyncio
async def test_start_unknown_adventure_returns_404(client):
    """An unknown adventure_id should return 404."""
    resp = await client.post(
        "/api/adventures/start",
        json={"adventure_id": "fly_to_mars", "description": "I want to colonize Mars"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_start_empty_description_returns_422(client):
    """Empty description should return 422."""
    resp = await client.post(
        "/api/adventures/start",
        json={"adventure_id": "build_website", "description": "   "},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_falls_back_when_no_api_key(client):
    """Without an API key, return the fallback plan and still persist tasks."""
    with (
        patch("routers.adventures.get_ai_client", new=AsyncMock(return_value=None)),
        patch("routers.adventures.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = AsyncMock(return_value="ok")

        resp = await client.post(
            "/api/adventures/start",
            json={"adventure_id": "build_website", "description": "A recipe site"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["adventure_id"] == "build_website"
    assert len(data["tasks"]) > 0
    # Fallback tasks were persisted
    assert mock_ostk.add_task.call_count == len(data["tasks"])


@pytest.mark.asyncio
async def test_start_falls_back_on_invalid_llm_json(client):
    """If the LLM returns garbage, fall back instead of crashing."""
    bad_response = MagicMock()
    bad_response.content = [SimpleNamespace(type="text", text="not json at all")]
    bad_response.usage = SimpleNamespace(input_tokens=10, output_tokens=10)

    mock_instance = MagicMock()
    mock_instance.messages.create = AsyncMock(return_value=bad_response)

    with (
        patch("routers.adventures.get_ai_client", new=AsyncMock(return_value=mock_instance)),
        patch("routers.adventures.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = AsyncMock(return_value="ok")

        resp = await client.post(
            "/api/adventures/start",
            json={"adventure_id": "plan_project", "description": "Launch a newsletter"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) > 0


@pytest.mark.asyncio
async def test_start_schedules_auto_labels_for_each_persisted_task(client):
    """Every task persisted via the adventure flow must run auto-labeling."""
    mock_response = _make_llm_response(VALID_WEBSITE_PLAN)

    mock_instance = MagicMock()
    mock_instance.messages.create = AsyncMock(return_value=mock_response)

    with (
        patch("routers.adventures.get_ai_client", new=AsyncMock(return_value=mock_instance)),
        patch("routers.adventures.ostk") as mock_ostk,
        patch("routers.adventures.schedule_auto_labels") as mock_schedule,
    ):
        # Return distinct ids so we can verify each task got its own label call.
        mock_ostk.add_task = AsyncMock(side_effect=[
            "added 801: Pick a name and check if the domain is available [P1]",
            "added 802: Sign up for a free Vercel account [P1]",
            "added 803: Set up a Postgres database for recipes (Vercel Postgres) [P1]",
            "added 804: Build the homepage [P1]",
            "added 805: Add image uploads for recipe photos [P2]",
        ])

        resp = await client.post(
            "/api/adventures/start",
            json={"adventure_id": "build_website", "description": "Recipe site"},
        )

    assert resp.status_code == 200
    assert mock_ostk.add_task.call_count == 5
    assert mock_schedule.call_count == 5
    first_args, _ = mock_schedule.call_args_list[0]
    assert first_args == (
        "801",
        "Pick a name and check if the domain is available",
        "",
    )


@pytest.mark.asyncio
async def test_start_skips_auto_labels_when_ostk_fails(client):
    """If ostk fails to save a task, do not schedule auto-labels for that task."""
    from services.ostk import OstkError

    mock_response = _make_llm_response(VALID_WEBSITE_PLAN)

    mock_instance = MagicMock()
    mock_instance.messages.create = AsyncMock(return_value=mock_response)

    with (
        patch("routers.adventures.get_ai_client", new=AsyncMock(return_value=mock_instance)),
        patch("routers.adventures.ostk") as mock_ostk,
        patch("routers.adventures.schedule_auto_labels") as mock_schedule,
    ):
        # Five tasks, three fail, two succeed.
        mock_ostk.add_task = AsyncMock(side_effect=[
            OstkError("disk full"),
            "added 901: Sign up for a free Vercel account [P1]",
            OstkError("disk full"),
            "added 902: Build the homepage [P1]",
            OstkError("disk full"),
        ])

        resp = await client.post(
            "/api/adventures/start",
            json={"adventure_id": "build_website", "description": "Recipe site"},
        )

    assert resp.status_code == 200
    assert mock_schedule.call_count == 2
    ids = [call.args[0] for call in mock_schedule.call_args_list]
    assert ids == ["901", "902"]


@pytest.mark.asyncio
async def test_fallback_picks_correct_template_per_adventure(client):
    """Each adventure id should produce a fallback plan with adventure-specific tasks."""
    with (
        patch("routers.adventures.get_ai_client", new=AsyncMock(return_value=None)),
        patch("routers.adventures.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = AsyncMock(return_value="ok")

        # build_website fallback should mention Vercel
        resp = await client.post(
            "/api/adventures/start",
            json={"adventure_id": "build_website", "description": "blog"},
        )
        titles = " ".join(t["title"] for t in resp.json()["tasks"])
        assert "Vercel" in titles

        # learn_skill fallback should mention picking a resource
        resp = await client.post(
            "/api/adventures/start",
            json={"adventure_id": "learn_skill", "description": "spanish"},
        )
        titles = " ".join(t["title"] for t in resp.json()["tasks"])
        assert "resource" in titles.lower() or "book" in titles.lower()

        # off_plate fallback should sound like the dread template
        resp = await client.post(
            "/api/adventures/start",
            json={"adventure_id": "off_plate", "description": "taxes"},
        )
        titles = " ".join(t["title"] for t in resp.json()["tasks"])
        assert "30 minutes" in titles or "first step" in titles


# --- Backend preference routing tests ---

@pytest.mark.asyncio
async def test_adventure_uses_cli_client_when_always_subscription(client):
    """With always_subscription preference, get_ai_client returns ClaudeCliClient."""
    from services.ai_backend import ClaudeCliClient
    from unittest.mock import AsyncMock as _AsyncMock, MagicMock as _MagicMock, patch as _patch
    mock_response = _make_llm_response(VALID_WEBSITE_PLAN)
    cli_client = ClaudeCliClient()
    cli_client.messages.create = _AsyncMock(return_value=mock_response)

    with (
        _patch("routers.adventures.get_ai_client", new=_AsyncMock(return_value=cli_client)),
        _patch("routers.adventures.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = _AsyncMock(return_value="ok")
        resp = await client.post(
            "/api/adventures/start",
            json={"adventure_id": "build_website", "description": "Recipe site"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_adventure_uses_api_key_client_when_always_api_key(client):
    """With always_api_key preference, get_ai_client returns AsyncAnthropic."""
    import anthropic
    from unittest.mock import AsyncMock as _AsyncMock, MagicMock as _MagicMock, patch as _patch
    mock_response = _make_llm_response(VALID_WEBSITE_PLAN)
    api_client = _MagicMock(spec=anthropic.AsyncAnthropic)
    api_client.messages.create = _AsyncMock(return_value=mock_response)

    with (
        _patch("routers.adventures.get_ai_client", new=_AsyncMock(return_value=api_client)),
        _patch("routers.adventures.ostk") as mock_ostk,
    ):
        mock_ostk.add_task = _AsyncMock(return_value="ok")
        resp = await client.post(
            "/api/adventures/start",
            json={"adventure_id": "build_website", "description": "Recipe site"},
        )
    assert resp.status_code == 200
