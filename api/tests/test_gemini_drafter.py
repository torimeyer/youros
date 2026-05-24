"""Tests for services.gemini_drafter."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_draft_returns_text():
    mock_creds = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Here is my reply."

    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response

    from services import gemini_drafter

    with (
        patch("google.auth.default", return_value=(mock_creds, "my-project")),
        patch("google.auth.transport.requests.Request", return_value=MagicMock()),
        patch("google.generativeai.configure"),
        patch("google.generativeai.GenerativeModel", return_value=mock_model),
    ):
        result = await gemini_drafter.draft("Say hi", system="Be brief.")

    assert result == "Here is my reply."


@pytest.mark.asyncio
async def test_draft_raises_when_creds_missing():
    """RuntimeError is raised when google.auth.default() fails."""
    import google.auth.exceptions
    from services import gemini_drafter

    with patch(
        "google.auth.default",
        side_effect=google.auth.exceptions.DefaultCredentialsError("no credentials"),
    ):
        with pytest.raises(RuntimeError, match="Gemini Enterprise not connected"):
            await gemini_drafter.draft("Say hi")


@pytest.mark.asyncio
async def test_draft_raises_when_refresh_fails():
    """RuntimeError is raised when credentials cannot be refreshed."""
    from services import gemini_drafter

    mock_creds = MagicMock()
    mock_creds.refresh.side_effect = Exception("token refresh failed")

    with patch("google.auth.default", return_value=(mock_creds, "proj")):
        with pytest.raises(RuntimeError, match="Gemini Enterprise not connected"):
            await gemini_drafter.draft("hi")
