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
        with patch("services.ostk_secrets.get_anthropic_key", new=AsyncMock(return_value="")):
            with patch("services.ostk_secrets.get_gemini_key", new=AsyncMock(return_value="")):
                with patch("services.provider_detection.detect_vertex_ai", return_value=False):
                    with patch("services.provider_detection.detect_bedrock", return_value=False):
                        with patch("services.provider_detection.is_gemini_cli_available", new=AsyncMock(return_value=False)):
                            result = await detect_providers()

    assert result == {
        "claude_code": False,
        "anthropic_key": False,
        "gemini_key": False,
        "vertex_ai": False,
        "vertex_ai_project": None,
        "bedrock": False,
        "gemini_cli": False,
    }


@pytest.mark.asyncio
async def test_detect_anthropic_env():
    """ANTHROPIC_API_KEY set → anthropic_key True, gemini_key False."""
    from services.provider_detection import detect_providers

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=False)):
        with patch("services.ostk_secrets.get_anthropic_key", new=AsyncMock(return_value="sk-ant-test")):
            with patch("services.ostk_secrets.get_gemini_key", new=AsyncMock(return_value="")):
                result = await detect_providers()

    assert result["anthropic_key"] is True
    assert result["claude_code"] is False
    assert result["gemini_key"] is False


@pytest.mark.asyncio
async def test_detect_gemini_env():
    """GEMINI_API_KEY set → gemini_key True, anthropic_key False."""
    from services.provider_detection import detect_providers

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=False)):
        with patch("services.ostk_secrets.get_anthropic_key", new=AsyncMock(return_value="")):
            with patch("services.ostk_secrets.get_gemini_key", new=AsyncMock(return_value="gemini-test")):
                result = await detect_providers()

    assert result["gemini_key"] is True
    assert result["anthropic_key"] is False
    assert result["claude_code"] is False


@pytest.mark.asyncio
async def test_detect_claude_code_available():
    """Mocked is_claude_code_available True → claude_code True, keys False."""
    from services.provider_detection import detect_providers

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=True)):
        with patch("services.ostk_secrets.get_anthropic_key", new=AsyncMock(return_value="")):
            with patch("services.ostk_secrets.get_gemini_key", new=AsyncMock(return_value="")):
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
        with patch("services.ostk_secrets.get_anthropic_key", new=AsyncMock(return_value="")):
            with patch("services.ostk_secrets.get_gemini_key", new=AsyncMock(return_value="")):
                client = TestClient(test_app)
                resp = client.get("/api/providers/detect")

    assert resp.status_code == 200
    data = resp.json()
    assert {"claude_code", "anthropic_key", "gemini_key", "vertex_ai", "vertex_ai_project", "bedrock"} <= set(data.keys())
    bool_keys = {"claude_code", "anthropic_key", "gemini_key", "vertex_ai", "bedrock"}
    for key in bool_keys:
        assert isinstance(data[key], bool), f"{key} should be bool"


# ---------------------------------------------------------------------------
# Vertex AI detection tests (slice 2, needle →933)
# ---------------------------------------------------------------------------

def test_detect_vertex_ai_env_credentials(tmp_path):
    """GOOGLE_APPLICATION_CREDENTIALS pointing to an existing file → vertex_ai True."""
    from services.provider_detection import detect_vertex_ai

    creds_file = tmp_path / "creds.json"
    creds_file.write_text("{}")
    with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": str(creds_file)}):
        assert detect_vertex_ai() is True


def test_detect_vertex_ai_gcloud_succeeds():
    """gcloud auth application-default returning exit 0 → vertex_ai True."""
    from services.provider_detection import detect_vertex_ai

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    with patch("services.provider_detection.Path") as MockPath:
        instance = MagicMock()
        instance.is_file.return_value = False
        instance.exists.return_value = False
        instance.__truediv__ = lambda self, x: instance
        MockPath.return_value = instance
        MockPath.home.return_value = instance
        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": ""}):
            with patch("services.provider_detection.subprocess.run", return_value=mock_proc):
                assert detect_vertex_ai() is True


def test_detect_vertex_ai_none():
    """No env, gcloud fails, no ADC file → vertex_ai False."""
    from services.provider_detection import detect_vertex_ai

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    with patch("services.provider_detection.Path") as MockPath:
        instance = MagicMock()
        instance.is_file.return_value = False
        instance.exists.return_value = False
        instance.__truediv__ = lambda self, x: instance
        MockPath.return_value = instance
        MockPath.home.return_value = instance
        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": ""}):
            with patch("services.provider_detection.subprocess.run", return_value=mock_proc):
                assert detect_vertex_ai() is False


# ---------------------------------------------------------------------------
# AWS Bedrock detection tests (slice 2, needle →933)
# ---------------------------------------------------------------------------

def test_detect_bedrock_access_key():
    """AWS_ACCESS_KEY_ID set in env → bedrock True."""
    from services.provider_detection import detect_bedrock

    with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "test-fake-key"}):
        assert detect_bedrock() is True


def test_detect_bedrock_aws_sts_succeeds():
    """aws sts get-caller-identity returning exit 0 → bedrock True."""
    from services.provider_detection import detect_bedrock

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "", "AWS_PROFILE": ""}):
        with patch("services.provider_detection.subprocess.run", return_value=mock_proc):
            assert detect_bedrock() is True


def test_detect_bedrock_none():
    """No AWS env vars, aws sts fails → bedrock False."""
    from services.provider_detection import detect_bedrock

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "", "AWS_PROFILE": ""}):
        with patch("services.provider_detection.subprocess.run", return_value=mock_proc):
            assert detect_bedrock() is False


# ---------------------------------------------------------------------------
# detect_vertex_gemini() tests (Wave B1)
# ---------------------------------------------------------------------------

def test_detect_vertex_gemini_adc_present():
    """ADC present + google.auth.default returns creds+project → available True."""
    from services.provider_detection import detect_vertex_gemini

    mock_creds = MagicMock(spec=[])
    with patch("services.provider_detection.detect_vertex_ai", return_value=True):
        with patch("google.auth.default", return_value=(mock_creds, "test-project")):
            with patch.dict(os.environ, {"GOOGLE_CLOUD_LOCATION": "us-east1"}):
                result = detect_vertex_gemini()

    assert result["available"] is True
    assert result["project"] == "test-project"
    assert result["location"] == "us-east1"


def test_detect_vertex_gemini_project_fallback():
    """google.auth.default returns project=None → falls back to gcloud config."""
    from services.provider_detection import detect_vertex_gemini

    mock_creds = MagicMock(spec=[])
    with patch("services.provider_detection.detect_vertex_ai", return_value=True):
        with patch("google.auth.default", return_value=(mock_creds, None)):
            with patch(
                "services.provider_detection._resolve_gcloud_default_project",
                return_value="fallback-project",
            ):
                result = detect_vertex_gemini()

    assert result["available"] is True
    assert result["project"] == "fallback-project"


def test_detect_vertex_gemini_no_adc():
    """detect_vertex_ai False → available False, google.auth never called."""
    from services.provider_detection import detect_vertex_gemini

    with patch("services.provider_detection.detect_vertex_ai", return_value=False):
        with patch("google.auth.default") as mock_auth:
            result = detect_vertex_gemini()

    assert result == {"available": False}
    mock_auth.assert_not_called()


def test_detect_vertex_gemini_auth_exception():
    """google.auth.default raising RuntimeError → available False, no leak."""
    from services.provider_detection import detect_vertex_gemini

    with patch("services.provider_detection.detect_vertex_ai", return_value=True):
        with patch("google.auth.default", side_effect=RuntimeError("no credentials")):
            result = detect_vertex_gemini()

    assert result == {"available": False}


@pytest.mark.asyncio
async def test_detect_providers_includes_vertex_ai_project():
    """detect_providers() exposes vertex_ai_project alongside vertex_ai bool."""
    from services.provider_detection import detect_providers

    mock_vx = {
        "available": True,
        "project": "my-gcp-project",
        "location": "us-central1",
        "identity_email": None,
        "hosted_domain": None,
    }

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=False)):
        with patch("services.ostk_secrets.get_anthropic_key", new=AsyncMock(return_value="")):
            with patch("services.ostk_secrets.get_gemini_key", new=AsyncMock(return_value="")):
                with patch("services.provider_detection.detect_vertex_gemini", return_value=mock_vx):
                    with patch("services.provider_detection.detect_bedrock", return_value=False):
                        result = await detect_providers()

    assert result["vertex_ai"] is True
    assert result["vertex_ai_project"] == "my-gcp-project"
