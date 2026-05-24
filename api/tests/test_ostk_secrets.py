"""Unit tests for services.ostk_secrets key-resolution helpers."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_cache():
    from services import ostk_secrets
    ostk_secrets._clear_cache()
    yield
    ostk_secrets._clear_cache()


def _mock_ostk(key_value: str):
    m = MagicMock()
    m.secret_get = AsyncMock(return_value=key_value)
    return m


def _mock_settings(value: str):
    m = MagicMock()
    m.get = MagicMock(return_value=value)
    return m


class TestGetAnthropicKey:
    @pytest.mark.asyncio
    async def test_keychain_hit_returns_key(self):
        """Keychain value takes priority over settings and env."""
        from services.ostk_secrets import get_anthropic_key

        with patch.dict("sys.modules", {
            "services.ostk": MagicMock(ostk=_mock_ostk("sk-ant-keychain")),
        }):
            result = await get_anthropic_key()

        assert result == "sk-ant-keychain"

    @pytest.mark.asyncio
    async def test_keychain_miss_env_present_returns_env(self):
        """When keychain and settings return empty, env var is the fallback."""
        from services.ostk_secrets import get_anthropic_key

        env = {**os.environ, "ANTHROPIC_API_KEY": "env-key"}
        with patch.dict("sys.modules", {
            "services.ostk": MagicMock(ostk=_mock_ostk("")),
            "services.settings_store": MagicMock(settings_store=_mock_settings("")),
        }):
            with patch.dict("os.environ", env, clear=True):
                result = await get_anthropic_key()

        assert result == "env-key"

    @pytest.mark.asyncio
    async def test_keychain_miss_env_missing_returns_empty(self):
        """When nothing is configured, returns empty string (caller decides how to handle)."""
        from services.ostk_secrets import get_anthropic_key

        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict("sys.modules", {
            "services.ostk": MagicMock(ostk=_mock_ostk("")),
            "services.settings_store": MagicMock(settings_store=_mock_settings("")),
        }):
            with patch.dict("os.environ", env, clear=True):
                result = await get_anthropic_key()

        assert result == ""

    @pytest.mark.asyncio
    async def test_settings_store_fills_gap_when_keychain_empty(self):
        """settings_store is consulted when keychain returns empty."""
        from services.ostk_secrets import get_anthropic_key

        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict("sys.modules", {
            "services.ostk": MagicMock(ostk=_mock_ostk("")),
            "services.settings_store": MagicMock(settings_store=_mock_settings("settings-key")),
        }):
            with patch.dict("os.environ", env, clear=True):
                result = await get_anthropic_key()

        assert result == "settings-key"

    @pytest.mark.asyncio
    async def test_keychain_exception_falls_back_to_env(self):
        """If ostk raises, the fallback chain still works."""
        from services.ostk_secrets import get_anthropic_key
        from services.ostk import OstkError

        bad_ostk = MagicMock()
        bad_ostk.secret_get = AsyncMock(side_effect=OstkError("keychain locked"))

        env = {**os.environ, "ANTHROPIC_API_KEY": "fallback-env-key"}
        with patch.dict("sys.modules", {
            "services.ostk": MagicMock(ostk=bad_ostk),
            "services.settings_store": MagicMock(settings_store=_mock_settings("")),
        }):
            with patch.dict("os.environ", env, clear=True):
                result = await get_anthropic_key()

        assert result == "fallback-env-key"


class TestGetGeminiKey:
    @pytest.mark.asyncio
    async def test_keychain_hit_returns_key(self):
        from services.ostk_secrets import get_gemini_key

        with patch.dict("sys.modules", {
            "services.ostk": MagicMock(ostk=_mock_ostk("gemini-keychain-key")),
        }):
            result = await get_gemini_key()

        assert result == "gemini-keychain-key"

    @pytest.mark.asyncio
    async def test_keychain_miss_env_present_returns_env(self):
        from services.ostk_secrets import get_gemini_key

        env = {**os.environ, "GEMINI_API_KEY": "gemini-env-key"}
        with patch.dict("sys.modules", {
            "services.ostk": MagicMock(ostk=_mock_ostk("")),
            "services.settings_store": MagicMock(settings_store=_mock_settings("")),
        }):
            with patch.dict("os.environ", env, clear=True):
                result = await get_gemini_key()

        assert result == "gemini-env-key"

    @pytest.mark.asyncio
    async def test_keychain_miss_env_missing_returns_empty(self):
        from services.ostk_secrets import get_gemini_key

        env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        with patch.dict("sys.modules", {
            "services.ostk": MagicMock(ostk=_mock_ostk("")),
            "services.settings_store": MagicMock(settings_store=_mock_settings("")),
        }):
            with patch.dict("os.environ", env, clear=True):
                result = await get_gemini_key()

        assert result == ""


class TestCaching:
    @pytest.mark.asyncio
    async def test_result_is_cached_within_ttl(self):
        """Second call within TTL should not hit ostk again."""
        from services.ostk_secrets import get_anthropic_key

        mock_ostk_svc = _mock_ostk("cached-key")
        with patch.dict("sys.modules", {
            "services.ostk": MagicMock(ostk=mock_ostk_svc),
        }):
            first = await get_anthropic_key()
            second = await get_anthropic_key()

        assert first == second == "cached-key"
        assert mock_ostk_svc.secret_get.await_count == 1

    def test_clear_cache_resets_state(self):
        from services import ostk_secrets
        ostk_secrets._CACHE["ANTHROPIC_API_KEY"] = (0.0, "stale-value")
        ostk_secrets._clear_cache()
        assert "ANTHROPIC_API_KEY" not in ostk_secrets._CACHE
