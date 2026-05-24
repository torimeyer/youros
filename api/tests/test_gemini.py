"""Tests for api/routers/gemini.py — Gemini Enterprise detection router."""
import pytest
from unittest.mock import AsyncMock, patch

from routers.gemini import _detect_gemini, is_gemini_available


@pytest.mark.asyncio
async def test_detect_gemini_returns_unavailable_when_no_credentials():
    with patch("google.auth.default", side_effect=Exception("no credentials")):
        result = await _detect_gemini()
    assert result["available"] is False
    assert result["authenticated"] is False
    assert result["email"] is None


@pytest.mark.asyncio
async def test_is_gemini_available_false_when_unavailable():
    with patch("routers.gemini._detect_gemini", new=AsyncMock(return_value={
        "available": False,
        "authenticated": False,
        "email": None,
        "workspace_connected": False,
        "api_reachable": False,
    })):
        with patch("routers.gemini._gemini_status_cache", None):
            result = await is_gemini_available()
    assert result is False


@pytest.mark.asyncio
async def test_gemini_status_endpoint_returns_dict():
    import importlib
    import routers.gemini as mod
    mod._gemini_status_cache = None

    with patch.object(mod, "_detect_gemini", new=AsyncMock(return_value={
        "available": False,
        "authenticated": False,
        "email": None,
        "workspace_connected": False,
        "api_reachable": False,
    })):
        result = await mod.gemini_status()

    assert isinstance(result, dict)
    assert "available" in result
    assert "authenticated" in result
