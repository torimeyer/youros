"""Tests for services.idea_breakdown.

Every test mocks the Anthropic client. We never hit the real API here.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import idea_breakdown
from services.idea_breakdown import (
    BreakdownTask,
    IdeaBreakdownResult,
    break_down_idea,
)


def _make_claude_response(text: str) -> MagicMock:
    """Build a fake Anthropic response whose first block has the given text."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _setup_claude_mock(text_or_sequence):
    """Patch the AsyncAnthropic client so calls return the given text.

    Accepts either a single string (always returned) or a list of strings
    (returned one per call in order).
    """
    if isinstance(text_or_sequence, list):
        responses = [_make_claude_response(t) for t in text_or_sequence]
        create = AsyncMock(side_effect=responses)
    else:
        create = AsyncMock(return_value=_make_claude_response(text_or_sequence))

    messages = MagicMock()
    messages.create = create
    client = MagicMock()
    client.messages = messages
    return client, create


@pytest.fixture(autouse=True)
def _reset_cache():
    idea_breakdown._reset_cache_for_tests()
    yield
    idea_breakdown._reset_cache_for_tests()


@pytest.mark.asyncio
async def test_happy_path_three_tasks():
    """Claude returns 3 clean tasks. Function returns them."""
    payload = json.dumps(
        {
            "needs_clarification": False,
            "question": None,
            "tasks": [
                {
                    "title": "Sketch the layout",
                    "description": "Pencil sketch of the homepage.",
                    "priority": "P1",
                    "order": 0,
                },
                {
                    "title": "Pick colors",
                    "description": "Decide on the palette.",
                    "priority": "P2",
                    "order": 1,
                },
                {
                    "title": "Ship the first draft",
                    "description": "Upload to staging for feedback.",
                    "priority": "P2",
                    "order": 2,
                },
            ],
        }
    )
    client, _ = _setup_claude_mock(payload)

    with patch("services.idea_breakdown.anthropic.AsyncAnthropic", return_value=client), \
         patch("services.idea_breakdown._resolve_api_key", new=AsyncMock(return_value="sk-test")):
        result = await break_down_idea(
            title="Redesign the landing page",
            description="Make it feel fresh",
        )

    assert isinstance(result, IdeaBreakdownResult)
    assert result.needs_clarification is False
    assert result.question is None
    assert len(result.tasks) == 3
    assert result.tasks[0].title == "Sketch the layout"
    assert result.tasks[0].order == 0
    assert result.tasks[2].order == 2


@pytest.mark.asyncio
async def test_clarification_path():
    """Claude asks a clarifying question. Function returns it and no tasks."""
    payload = json.dumps(
        {
            "needs_clarification": True,
            "question": "What platform is this for, web or mobile?",
            "tasks": [],
        }
    )
    client, _ = _setup_claude_mock(payload)

    with patch("services.idea_breakdown.anthropic.AsyncAnthropic", return_value=client), \
         patch("services.idea_breakdown._resolve_api_key", new=AsyncMock(return_value="sk-test")):
        result = await break_down_idea(
            title="build an app",
            description="",
        )

    assert result.needs_clarification is True
    assert result.question == "What platform is this for, web or mobile?"
    assert result.tasks == []


@pytest.mark.asyncio
async def test_answer_flow_passes_extra_context():
    """Calling with extra_context should include it in the prompt."""
    payload1 = json.dumps(
        {"needs_clarification": True, "question": "Web or mobile?", "tasks": []}
    )
    payload2 = json.dumps(
        {
            "needs_clarification": False,
            "question": None,
            "tasks": [
                {
                    "title": "Scaffold web project",
                    "description": "Create the base repo.",
                    "priority": "P1",
                    "order": 0,
                }
            ],
        }
    )
    client, create_mock = _setup_claude_mock([payload1, payload2])

    with patch("services.idea_breakdown.anthropic.AsyncAnthropic", return_value=client), \
         patch("services.idea_breakdown._resolve_api_key", new=AsyncMock(return_value="sk-test")):
        first = await break_down_idea(title="build an app", description="")
        assert first.needs_clarification is True

        second = await break_down_idea(
            title="build an app",
            description="",
            extra_context="web",
        )

    assert second.needs_clarification is False
    assert len(second.tasks) == 1
    # The second call's user message should include the answer "web".
    call_args_list = create_mock.call_args_list
    assert len(call_args_list) == 2
    second_user_msg = call_args_list[1].kwargs["messages"][0]["content"]
    assert "web" in second_user_msg.lower()


@pytest.mark.asyncio
async def test_caps_at_fifteen_tasks():
    """Claude returns 30 tasks. Function returns 15."""
    tasks = [
        {
            "title": f"Task {i}",
            "description": f"Do thing {i}.",
            "priority": "P2",
            "order": i,
        }
        for i in range(30)
    ]
    payload = json.dumps(
        {"needs_clarification": False, "question": None, "tasks": tasks}
    )
    client, _ = _setup_claude_mock(payload)

    with patch("services.idea_breakdown.anthropic.AsyncAnthropic", return_value=client), \
         patch("services.idea_breakdown._resolve_api_key", new=AsyncMock(return_value="sk-test")):
        result = await break_down_idea(title="huge project", description="many pieces")

    assert result.needs_clarification is False
    assert len(result.tasks) == 15
    # Order should be re-numbered 0 to 14.
    assert [t.order for t in result.tasks] == list(range(15))


@pytest.mark.asyncio
async def test_malformed_json_falls_back_to_single_task():
    """Claude returns garbage on both tries. Function returns a fallback task."""
    client, create_mock = _setup_claude_mock(["not json at all", "still not json"])

    with patch("services.idea_breakdown.anthropic.AsyncAnthropic", return_value=client), \
         patch("services.idea_breakdown._resolve_api_key", new=AsyncMock(return_value="sk-test")):
        result = await break_down_idea(
            title="Write the newsletter",
            description="",
        )

    # One retry happened.
    assert create_mock.call_count == 2
    # Fallback task was produced.
    assert result.needs_clarification is False
    assert len(result.tasks) == 1
    assert "newsletter" in result.tasks[0].title.lower()


@pytest.mark.asyncio
async def test_cache_avoids_second_claude_call():
    """Same input twice should only call Claude once."""
    payload = json.dumps(
        {
            "needs_clarification": False,
            "question": None,
            "tasks": [
                {"title": "One task", "description": "", "priority": "P2", "order": 0}
            ],
        }
    )
    client, create_mock = _setup_claude_mock(payload)

    with patch("services.idea_breakdown.anthropic.AsyncAnthropic", return_value=client), \
         patch("services.idea_breakdown._resolve_api_key", new=AsyncMock(return_value="sk-test")):
        first = await break_down_idea(title="cached idea", description="")
        second = await break_down_idea(title="cached idea", description="")

    assert first == second
    assert create_mock.call_count == 1


@pytest.mark.asyncio
async def test_invalid_priority_defaults_to_p2():
    """Unknown priority values should fall back to P2."""
    payload = json.dumps(
        {
            "needs_clarification": False,
            "question": None,
            "tasks": [
                {"title": "T", "description": "d", "priority": "URGENT", "order": 0}
            ],
        }
    )
    client, _ = _setup_claude_mock(payload)

    with patch("services.idea_breakdown.anthropic.AsyncAnthropic", return_value=client), \
         patch("services.idea_breakdown._resolve_api_key", new=AsyncMock(return_value="sk-test")):
        result = await break_down_idea(title="idea", description="")

    assert result.tasks[0].priority == "P2"


@pytest.mark.asyncio
async def test_empty_task_list_becomes_clarification():
    """If Claude returns no tasks and no question, ask a clarifying question."""
    payload = json.dumps(
        {"needs_clarification": False, "question": None, "tasks": []}
    )
    client, _ = _setup_claude_mock(payload)

    with patch("services.idea_breakdown.anthropic.AsyncAnthropic", return_value=client), \
         patch("services.idea_breakdown._resolve_api_key", new=AsyncMock(return_value="sk-test")):
        result = await break_down_idea(title="idea", description="")

    assert result.needs_clarification is True
    assert result.question is not None


@pytest.mark.asyncio
async def test_json_with_surrounding_prose_is_extracted():
    """Claude sometimes adds prose around the JSON. We should still parse it."""
    payload = (
        "Here you go:\n\n"
        + json.dumps(
            {
                "needs_clarification": False,
                "question": None,
                "tasks": [
                    {"title": "Only task", "description": "", "priority": "P2", "order": 0}
                ],
            }
        )
        + "\n\nHope this helps."
    )
    client, _ = _setup_claude_mock(payload)

    with patch("services.idea_breakdown.anthropic.AsyncAnthropic", return_value=client), \
         patch("services.idea_breakdown._resolve_api_key", new=AsyncMock(return_value="sk-test")):
        result = await break_down_idea(title="any idea", description="")

    assert result.needs_clarification is False
    assert len(result.tasks) == 1
    assert result.tasks[0].title == "Only task"


@pytest.mark.asyncio
async def test_no_api_key_returns_fallback():
    """When no API key is available, the function should still return something."""
    with patch("services.idea_breakdown._resolve_api_key", new=AsyncMock(return_value="")):
        result = await break_down_idea(title="Write a blog post", description="about gardens")

    # With no key we cannot call Claude, so two retries both return "". Fallback single task.
    assert result.needs_clarification is False
    assert len(result.tasks) == 1
    assert "blog post" in result.tasks[0].title.lower()
