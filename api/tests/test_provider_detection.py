"""Tests for provider auto-detection (slice 1 of S6, needle →931).

Covers detect_providers() and GET /api/providers/detect.
"""
import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import provider_detection as _pd


# ---------------------------------------------------------------------------
# Cache reset — prevents TTL cache from leaking state between tests (→1738)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_provider_cache():
    """Reset the detect_providers() TTL cache before each test.

    Commit 195f2a25 (→1738) added a 30-second single-flight cache.  Without
    this fixture, the first test's mocked result stays cached and subsequent
    tests see that stale value instead of re-running their own mocks.
    """
    _pd._reset_provider_cache()
    yield


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
                with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=False)):
                    with patch("services.provider_detection.detect_bedrock", new=AsyncMock(return_value=False)):
                        with patch("services.provider_detection.is_gemini_cli_available", new=AsyncMock(return_value=False)):
                            result = await detect_providers()

    assert result == {
        "claude_code": False,
        "anthropic_key": False,
        "gemini_key": False,
        "vertex_ai": False,
        "vertex_ai_project": None,
        "vertex_ai_needs_reauth": False,
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

@pytest.mark.asyncio
async def test_detect_vertex_ai_env_credentials(tmp_path):
    """GOOGLE_APPLICATION_CREDENTIALS pointing to an existing file → vertex_ai True."""
    from services.provider_detection import detect_vertex_ai

    creds_file = tmp_path / "creds.json"
    creds_file.write_text("{}")
    with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": str(creds_file)}):
        assert await detect_vertex_ai() is True


@pytest.mark.asyncio
async def test_detect_vertex_ai_gcloud_succeeds():
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
                assert await detect_vertex_ai() is True


@pytest.mark.asyncio
async def test_detect_vertex_ai_none():
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
                assert await detect_vertex_ai() is False


# ---------------------------------------------------------------------------
# AWS Bedrock detection tests (slice 2, needle →933)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_bedrock_access_key():
    """AWS_ACCESS_KEY_ID set in env → bedrock True."""
    from services.provider_detection import detect_bedrock

    with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "test-fake-key"}):
        assert await detect_bedrock() is True


@pytest.mark.asyncio
async def test_detect_bedrock_aws_sts_succeeds():
    """aws sts get-caller-identity returning exit 0 → bedrock True."""
    from services.provider_detection import detect_bedrock

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "", "AWS_PROFILE": ""}):
        with patch("services.provider_detection.subprocess.run", return_value=mock_proc):
            assert await detect_bedrock() is True


@pytest.mark.asyncio
async def test_detect_bedrock_none():
    """No AWS env vars, aws sts fails → bedrock False."""
    from services.provider_detection import detect_bedrock

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "", "AWS_PROFILE": ""}):
        with patch("services.provider_detection.subprocess.run", return_value=mock_proc):
            assert await detect_bedrock() is False


# ---------------------------------------------------------------------------
# detect_vertex_gemini() tests (Wave B1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_vertex_gemini_adc_present():
    """ADC present + google.auth.default returns creds+project → available True."""
    from services.provider_detection import detect_vertex_gemini

    mock_creds = MagicMock(spec=[])
    with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=True)):
        with patch("google.auth.default", return_value=(mock_creds, "test-project")):
            with patch.dict(os.environ, {"GOOGLE_CLOUD_LOCATION": "us-east1"}):
                result = await detect_vertex_gemini()

    assert result["available"] is True
    assert result["project"] == "test-project"
    assert result["location"] == "us-east1"


@pytest.mark.asyncio
async def test_detect_vertex_gemini_project_fallback():
    """google.auth.default returns project=None → falls back to gcloud config."""
    from services.provider_detection import detect_vertex_gemini

    mock_creds = MagicMock(spec=[])
    with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=True)):
        with patch("google.auth.default", return_value=(mock_creds, None)):
            with patch(
                "services.provider_detection._resolve_gcloud_default_project",
                new=AsyncMock(return_value="fallback-project"),
            ):
                result = await detect_vertex_gemini()

    assert result["available"] is True
    assert result["project"] == "fallback-project"


@pytest.mark.asyncio
async def test_detect_vertex_gemini_no_adc():
    """detect_vertex_ai False → available False, google.auth never called."""
    from services.provider_detection import detect_vertex_gemini

    with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=False)):
        with patch("google.auth.default") as mock_auth:
            result = await detect_vertex_gemini()

    assert result == {"available": False, "vertex_ai_needs_reauth": False}
    mock_auth.assert_not_called()


@pytest.mark.asyncio
async def test_detect_vertex_gemini_auth_exception():
    """google.auth.default raising RuntimeError → available False, no leak."""
    from services.provider_detection import detect_vertex_gemini

    with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=True)):
        with patch("google.auth.default", side_effect=RuntimeError("no credentials")):
            result = await detect_vertex_gemini()

    assert result == {"available": False, "vertex_ai_needs_reauth": False}


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
                with patch("services.provider_detection.detect_vertex_gemini", new=AsyncMock(return_value=mock_vx)):
                    with patch("services.provider_detection.detect_bedrock", new=AsyncMock(return_value=False)):
                        result = await detect_providers()

    assert result["vertex_ai"] is True
    assert result["vertex_ai_project"] == "my-gcp-project"


# ---------------------------------------------------------------------------
# Bug 2: vertex_ai_needs_reauth field and no-domain-gate (RED tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_vertex_gemini_no_adc_has_needs_reauth_key():
    """When ADC is absent, result always includes vertex_ai_needs_reauth key."""
    from services.provider_detection import detect_vertex_gemini

    with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=False)):
        result = await detect_vertex_gemini()

    assert "vertex_ai_needs_reauth" in result
    assert result["vertex_ai_needs_reauth"] is False


@pytest.mark.asyncio
async def test_detect_vertex_gemini_success_has_needs_reauth_false():
    """Happy path returns vertex_ai_needs_reauth: False."""
    from services.provider_detection import detect_vertex_gemini

    mock_creds = MagicMock(spec=[])
    with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=True)):
        with patch("google.auth.default", return_value=(mock_creds, "test-project")):
            result = await detect_vertex_gemini()

    assert result["available"] is True
    assert result.get("vertex_ai_needs_reauth") is False


@pytest.mark.asyncio
async def test_detect_vertex_gemini_generic_exception_has_needs_reauth_false():
    """RuntimeError (non-reauth) yields vertex_ai_needs_reauth: False."""
    from services.provider_detection import detect_vertex_gemini

    with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=True)):
        with patch("google.auth.default", side_effect=RuntimeError("internal")):
            result = await detect_vertex_gemini()

    assert result["available"] is False
    assert result.get("vertex_ai_needs_reauth") is False


@pytest.mark.asyncio
async def test_detect_vertex_gemini_refresh_error_sets_needs_reauth_true():
    """google.auth.exceptions.RefreshError → vertex_ai_needs_reauth True."""
    import google.auth.exceptions
    from services.provider_detection import detect_vertex_gemini

    refresh_err = google.auth.exceptions.RefreshError("Token has been expired or revoked")

    with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=True)):
        with patch("google.auth.default", side_effect=refresh_err):
            result = await detect_vertex_gemini()

    assert result["available"] is False
    assert result.get("vertex_ai_needs_reauth") is True


@pytest.mark.asyncio
async def test_detect_providers_always_has_vertex_ai_needs_reauth():
    """detect_providers() result always contains vertex_ai_needs_reauth key."""
    from services.provider_detection import detect_providers

    mock_vx = {
        "available": False,
        "vertex_ai_needs_reauth": False,
    }

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=False)):
        with patch("services.ostk_secrets.get_anthropic_key", new=AsyncMock(return_value="")):
            with patch("services.ostk_secrets.get_gemini_key", new=AsyncMock(return_value="")):
                with patch("services.provider_detection.detect_vertex_gemini", new=AsyncMock(return_value=mock_vx)):
                    with patch("services.provider_detection.detect_bedrock", new=AsyncMock(return_value=False)):
                        with patch("services.provider_detection.is_gemini_cli_available", new=AsyncMock(return_value=False)):
                            result = await detect_providers()

    assert "vertex_ai_needs_reauth" in result
    assert result["vertex_ai_needs_reauth"] is False


@pytest.mark.asyncio
async def test_detect_providers_propagates_needs_reauth_true():
    """When vertex detection signals needs_reauth, detect_providers forwards it."""
    from services.provider_detection import detect_providers

    mock_vx = {
        "available": False,
        "vertex_ai_needs_reauth": True,
    }

    with patch("services.provider_detection.is_claude_code_available", new=AsyncMock(return_value=False)):
        with patch("services.ostk_secrets.get_anthropic_key", new=AsyncMock(return_value="")):
            with patch("services.ostk_secrets.get_gemini_key", new=AsyncMock(return_value="")):
                with patch("services.provider_detection.detect_vertex_gemini", new=AsyncMock(return_value=mock_vx)):
                    with patch("services.provider_detection.detect_bedrock", new=AsyncMock(return_value=False)):
                        with patch("services.provider_detection.is_gemini_cli_available", new=AsyncMock(return_value=False)):
                            result = await detect_providers()

    assert result["vertex_ai_needs_reauth"] is True


@pytest.mark.asyncio
async def test_detect_vertex_gemini_available_regardless_of_hosted_domain():
    """Vertex is available for personal ADC (no hosted_domain) when project resolves."""
    from services.provider_detection import detect_vertex_gemini

    mock_creds = MagicMock(spec=[])
    with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=True)):
        with patch("google.auth.default", return_value=(mock_creds, "personal-project")):
            with patch("services.provider_detection._extract_hosted_domain", return_value=None):
                result = await detect_vertex_gemini()

    assert result["available"] is True
    assert result.get("hosted_domain") is None
    assert result.get("vertex_ai_needs_reauth") is False


# ---------------------------------------------------------------------------
# Timeout and concurrency tests (fix for cold-start hang)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_vertex_gemini_timeout_returns_false(monkeypatch):
    """google.auth.default taking too long → returns available=False without hanging."""
    import services.provider_detection as _pd_mod

    # Shorten the cap so the test runs in milliseconds, not seconds.
    monkeypatch.setattr(_pd_mod, "_VERTEX_GEMINI_TIMEOUT", 0.05)

    async def _slow_to_thread(fn, *args, **kwargs):
        await asyncio.sleep(1.0)   # much longer than the 0.05s cap
        return (object(), "proj")  # would produce available=True if it completes

    with patch("services.provider_detection.detect_vertex_ai", new=AsyncMock(return_value=True)):
        with patch("services.provider_detection.asyncio.to_thread", new=_slow_to_thread):
            result = await _pd_mod.detect_vertex_gemini()

    assert result == {"available": False, "vertex_ai_needs_reauth": False}


@pytest.mark.asyncio
async def test_run_full_detection_concurrent():
    """All 6 probes run in parallel — total elapsed ≈ one probe, not the sum."""

    async def _slow_false():
        await asyncio.sleep(0.2)
        return False

    async def _slow_vx():
        await asyncio.sleep(0.2)
        return {"available": False, "vertex_ai_needs_reauth": False}

    async def _slow_str():
        await asyncio.sleep(0.2)
        return ""

    with patch("services.provider_detection.is_claude_code_available", new=_slow_false):
        with patch("services.provider_detection.is_gemini_cli_available", new=_slow_false):
            with patch("services.ostk_secrets.get_anthropic_key", new=_slow_str):
                with patch("services.ostk_secrets.get_gemini_key", new=_slow_str):
                    with patch("services.provider_detection.detect_vertex_gemini", new=_slow_vx):
                        with patch("services.provider_detection.detect_bedrock", new=_slow_false):
                            start = time.monotonic()
                            await _pd._run_full_detection()
                            elapsed = time.monotonic() - start

    # Sequential: ~1.2s (6 × 0.2s). Parallel: ~0.2-0.3s.
    assert elapsed < 0.6, (
        f"_run_full_detection took {elapsed:.2f}s — probes are still running sequentially"
    )
