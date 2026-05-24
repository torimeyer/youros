"""Tests for the api_reachable / api_error fix in api/routers/gemini.py.

Ensures that when genai.list_models() fails the function does NOT silently
return api_reachable=True.
"""
import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_api_reachable_false_when_list_models_raises():
    """api_reachable must be False when list_models() raises an exception."""
    from routers.gemini import _detect_gemini_sync

    fake_creds = MagicMock()
    fake_creds.service_account_email = None
    fake_creds.id_token = None

    with patch("google.auth.default", return_value=(fake_creds, "my-project")):
        with patch("google.auth.transport.requests.Request", return_value=MagicMock()):
            with patch.object(fake_creds, "refresh"):
                with patch("google.generativeai.configure"):
                    with patch(
                        "google.generativeai.list_models",
                        side_effect=Exception(
                            "serviceusage.services.use permission denied on project my-project"
                        ),
                    ):
                        result = _detect_gemini_sync()

    assert result["api_reachable"] is False, (
        f"Expected api_reachable=False but got {result['api_reachable']!r}. "
        "Current code sets api_reachable=True before the try block — bug."
    )
    assert "api_error" in result, "api_error key must be present in result"
    assert result["api_error"], "api_error must be a non-empty string"
    assert "permission" in result["api_error"].lower() or "serviceusage" in result["api_error"].lower(), (
        f"api_error should contain the original exception message, got: {result['api_error']!r}"
    )


@pytest.mark.asyncio
async def test_api_reachable_true_when_list_models_succeeds():
    """api_reachable must be True when list_models() returns successfully."""
    from routers.gemini import _detect_gemini_sync

    fake_creds = MagicMock()
    fake_creds.service_account_email = "svc@project.iam.gserviceaccount.com"
    fake_creds.id_token = None

    with patch("google.auth.default", return_value=(fake_creds, "my-project")):
        with patch("google.auth.transport.requests.Request", return_value=MagicMock()):
            with patch.object(fake_creds, "refresh"):
                with patch("google.generativeai.configure"):
                    with patch(
                        "google.generativeai.list_models",
                        return_value=iter([MagicMock(name="models/gemini-pro")]),
                    ):
                        result = _detect_gemini_sync()

    assert result["api_reachable"] is True
    assert result["api_error"] is None
    assert result["available"] is True


@pytest.mark.asyncio
async def test_api_error_in_outer_except_path():
    """The outer except path (no credentials) must also include api_error=None."""
    from routers.gemini import _detect_gemini_sync

    with patch("google.auth.default", side_effect=Exception("no credentials")):
        result = _detect_gemini_sync()

    assert result["available"] is False
    assert "api_error" in result, "api_error must be present even on outer except path"
