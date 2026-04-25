"""Tests for provider auto-detection (slice 1 of S6, needle →931).

Covers detect_providers() and GET /api/providers/detect.
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# detect_providers() unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_no_providers():
    """When env is empty, settings store is empty, and claude is absent, all False."""
    from services.provider_detection import detect_providers

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=False)):
        with patch("services.provider_detection.settings_store") as mock_store:
            mock_store.get.return_value = ""
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": ""}):
                result = await detect_providers()

    assert result == {"claude_code": False, "anthropic_key": False, "gemini_key": False}


@pytest.mark.asyncio
async def test_detect_anthropic_env():
    """ANTHROPIC_API_KEY in env → anthropic_key True, others False."""
    from services.provider_detection import detect_providers

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=False)):
        with patch("services.provider_detection.settings_store") as mock_store:
            mock_store.get.return_value = ""
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test", "GEMINI_API_KEY": ""}):
                result = await detect_providers()

    assert result["anthropic_key"] is True
    assert result["claude_code"] is False
    assert result["gemini_key"] is False


@pytest.mark.asyncio
async def test_detect_gemini_env():
    """GEMINI_API_KEY in env → gemini_key True, others False."""
    from services.provider_detection import detect_providers

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=False)):
        with patch("services.provider_detection.settings_store") as mock_store:
            mock_store.get.return_value = ""
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "gemini-test"}):
                result = await detect_providers()

    assert result["gemini_key"] is True
    assert result["anthropic_key"] is False
    assert result["claude_code"] is False


@pytest.mark.asyncio
async def test_detect_claude_code_available():
    """Mocked is_claude_code_available True → claude_code True, others False."""
    from services.provider_detection import detect_providers

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=True)):
        with patch("services.provider_detection.settings_store") as mock_store:
            mock_store.get.return_value = ""
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": ""}):
                result = await detect_providers()

    assert result["claude_code"] is True
    assert result["anthropic_key"] is False
    assert result["gemini_key"] is False


def test_detect_endpoint_returns_dict():
    """GET /api/providers/detect returns a dict with the expected boolean keys."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.providers import router

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=False)):
        with patch("services.provider_detection.settings_store") as mock_store:
            mock_store.get.return_value = ""
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": ""}):
                client = TestClient(test_app)
                resp = client.get("/api/providers/detect")

    assert resp.status_code == 200
    data = resp.json()
    assert {"claude_code", "anthropic_key", "gemini_key"} <= set(data.keys())
    for val in data.values():
        assert isinstance(val, bool)
