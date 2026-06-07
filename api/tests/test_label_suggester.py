"""Tests for the auto-label suggester service.

These tests never call the real Anthropic API. The Claude classifier is
mocked at the module level.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services import label_suggester
from services.label_suggester import suggest_labels, clear_cache, get_call_count, _stable_color_for_name
from services.labels_store import LABEL_COLORS


# --- Fixtures and helpers ---------------------------------------------------

EXISTING_LABELS = [
    {"id": "l-auth", "name": "auth", "color": "#ef4444", "description": "login and identity"},
    {"id": "l-ui", "name": "ui", "color": "#3b82f6", "description": "frontend look and feel"},
    {"id": "l-bug", "name": "bug", "color": "#f97316"},
    {"id": "l-docs", "name": "docs", "color": "#22c55e"},
]


def _claude_text(text: str):
    """Wrap a string in the structure anthropic.messages.create returns."""
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block])


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


# --- Keyword scan -----------------------------------------------------------

@pytest.mark.asyncio
async def test_keyword_match_finds_existing_label():
    """A task that mentions 'auth' should pick up the existing 'auth' label."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(return_value=_claude_text(""))

    with patch("services.label_suggester.get_ai_client", return_value=fake_client):
        result = await suggest_labels(
            "Fix auth redirect bug",
            "users get bounced back to the login screen",
            EXISTING_LABELS,
        )

    names = {r["name"] for r in result}
    assert "auth" in names
    # Every returned label should be marked as not new.
    assert all(r["is_new"] is False for r in result)


@pytest.mark.asyncio
async def test_keyword_match_avoids_partial_matches():
    """The label 'ui' should not match the word 'guide' inside text."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(return_value=_claude_text(""))

    with patch("services.label_suggester.get_ai_client", return_value=fake_client):
        result = await suggest_labels(
            "Write a user guide for the platform",
            "needs sample screenshots",
            EXISTING_LABELS,
        )

    names = {r["name"] for r in result}
    assert "ui" not in names


# --- AI classifier ----------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_suggests_existing_labels():
    """When Claude returns two existing label names, both should come back."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        return_value=_claude_text("docs\nui\n")
    )

    with patch("services.label_suggester.get_ai_client", return_value=fake_client):
        result = await suggest_labels(
            "Polish the help center",
            "rewrite copy and refresh visuals",
            EXISTING_LABELS,
        )

    names = [r["name"] for r in result]
    assert "docs" in names
    assert "ui" in names
    assert all(r["is_new"] is False for r in result)


@pytest.mark.asyncio
async def test_ai_suggests_new_label_when_nothing_fits():
    """If no existing label matches and Claude returns NEW:, create the label."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        return_value=_claude_text("NEW: feature-flags\n")
    )

    fake_label = {"id": "l-ff", "name": "feature-flags", "color": "#06b6d4"}

    with patch("services.label_suggester.get_ai_client", return_value=fake_client), \
         patch.object(label_suggester.labels_store, "create_label", return_value=fake_label) as mock_create:
        result = await suggest_labels(
            "Wire up gradual rollout toggles",
            "we need a way to ship a feature to 5 percent of users first",
            EXISTING_LABELS,
        )

    assert mock_create.called
    assert len(result) == 1
    assert result[0]["name"] == "feature-flags"
    assert result[0]["is_new"] is True


@pytest.mark.asyncio
async def test_invalid_new_label_name_is_dropped():
    """A NEW: suggestion that is not a valid name should be discarded."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        return_value=_claude_text("NEW: this is not a valid name with spaces\n")
    )

    with patch("services.label_suggester.get_ai_client", return_value=fake_client), \
         patch.object(label_suggester.labels_store, "create_label") as mock_create:
        result = await suggest_labels(
            "Random unrelated thing",
            "no overlap with any existing label",
            EXISTING_LABELS,
        )

    assert not mock_create.called
    assert result == []


@pytest.mark.asyncio
async def test_existing_labels_take_priority_over_new():
    """If Claude returns both an existing label and NEW:, prefer the existing one."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        return_value=_claude_text("docs\nNEW: documentation\n")
    )

    with patch("services.label_suggester.get_ai_client", return_value=fake_client), \
         patch.object(label_suggester.labels_store, "create_label") as mock_create:
        result = await suggest_labels(
            "Polish help center",
            "improve onboarding writeups",
            EXISTING_LABELS,
        )

    names = [r["name"] for r in result]
    assert "docs" in names
    assert "documentation" not in names
    assert not mock_create.called


@pytest.mark.asyncio
async def test_cache_prevents_duplicate_calls():
    """Same input twice must call Claude only once."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(return_value=_claude_text("docs\n"))

    with patch("services.label_suggester.get_ai_client", return_value=fake_client):
        await suggest_labels("Polish help center", "writeups", EXISTING_LABELS)
        await suggest_labels("Polish help center", "writeups", EXISTING_LABELS)

    assert fake_client.messages.create.call_count == 1
    assert get_call_count() == 1


@pytest.mark.asyncio
async def test_capped_at_three_labels():
    """Even when Claude returns more than 3 labels, the result is capped."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        return_value=_claude_text("auth\nui\nbug\ndocs\n")
    )

    with patch("services.label_suggester.get_ai_client", return_value=fake_client):
        result = await suggest_labels(
            "Rework everything",
            "all areas touched",
            EXISTING_LABELS,
        )

    assert len(result) <= 3


@pytest.mark.asyncio
async def test_made_up_labels_are_dropped():
    """A label name Claude invents that is not on the list and not NEW: is dropped."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        return_value=_claude_text("totally-made-up\n")
    )

    with patch("services.label_suggester.get_ai_client", return_value=fake_client), \
         patch.object(label_suggester.labels_store, "create_label") as mock_create:
        result = await suggest_labels(
            "Some random task",
            "no clear theme",
            EXISTING_LABELS,
        )

    assert result == []
    assert not mock_create.called


@pytest.mark.asyncio
async def test_empty_task_returns_empty():
    """A task with no title or description should return no suggestions."""
    result = await suggest_labels("", "", EXISTING_LABELS)
    assert result == []


def test_stable_color_for_name_is_deterministic():
    """Same label name always maps to the same color, stable across process restarts."""
    c1 = _stable_color_for_name("feature-flags")
    c2 = _stable_color_for_name("feature-flags")
    assert c1 == c2
    assert c1 in LABEL_COLORS


@pytest.mark.asyncio
async def test_mint_then_reuse_no_duplicate():
    """Mint a missing label -> same input again -> same label returned, no second mint."""
    minted_color = _stable_color_for_name("rollout-flags")
    minted = {"id": "abc12345", "name": "rollout-flags", "color": minted_color}

    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        return_value=_claude_text("NEW: rollout-flags\n")
    )

    # First call: label does not exist -> create_label succeeds.
    with patch("services.label_suggester.get_ai_client", return_value=fake_client), \
         patch.object(label_suggester.labels_store, "create_label", return_value=minted) as mock_create1, \
         patch.object(label_suggester.labels_store, "list_labels", return_value=[]):
        result1 = await suggest_labels("gradual rollout", "ship to 5 percent", [])

    assert result1[0]["name"] == "rollout-flags"
    assert result1[0]["is_new"] is True
    assert mock_create1.call_count == 1

    # Same call again (cache cleared): create_label raises ValueError (label now exists);
    # list_labels returns it -> no duplicate, same label returned.
    clear_cache()

    with patch("services.label_suggester.get_ai_client", return_value=fake_client), \
         patch.object(label_suggester.labels_store, "create_label", side_effect=ValueError("already exists")) as mock_create2, \
         patch.object(label_suggester.labels_store, "list_labels", return_value=[minted]):
        result2 = await suggest_labels("gradual rollout", "ship to 5 percent", [])

    assert result2[0]["name"] == "rollout-flags"
    assert mock_create2.call_count == 1  # tried once, got ValueError, looked up instead
    assert result1[0]["color"] == result2[0]["color"]  # color is stable: same name, same color


@pytest.mark.asyncio
async def test_claude_failure_falls_back_gracefully():
    """If Claude raises, return whatever the keyword scan found, or empty."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(side_effect=Exception("network down"))

    with patch("services.label_suggester.get_ai_client", return_value=fake_client):
        result = await suggest_labels(
            "Fix auth redirect",
            "login broken",
            EXISTING_LABELS,
        )

    # Keyword scan still finds 'auth'.
    assert any(r["name"] == "auth" for r in result)
