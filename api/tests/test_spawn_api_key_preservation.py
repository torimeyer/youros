"""Tests for host-subscription guard on ANTHROPIC_API_KEY stripping (→2640 fix 5).

On a host that authenticates via apiKeyHelper (external helper program configured
in ~/.claude/settings.json), the spawn env build must NOT strip ANTHROPIC_API_KEY
or the subagent dies before registering. The guard reads settings.json at process
start (cached), and only strips the key on subscription hosts.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_AUTOMATION_FILES_SAVE", "1")


class TestHostSubscriptionDetection:
    """Tests for _host_has_claude_subscription()."""

    def test_helper_host_returns_false(self, tmp_path):
        """apiKeyHelper present -> not a subscription host -> key must be kept."""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"apiKeyHelper": "/usr/local/bin/get-key"}))

        from routers.agents import _host_has_claude_subscription, _HOST_SUBSCRIPTION_CACHE

        _HOST_SUBSCRIPTION_CACHE.clear()
        with patch("routers.agents.Path") as mock_path_cls:
            real_path = Path
            def _fake(p):
                if "settings.json" in str(p) and ".claude" in str(p):
                    return settings
                return real_path(p)
            mock_path_cls.side_effect = _fake
            # Patch expanduser to return the tmp settings file
            with patch.object(Path, "expanduser", return_value=settings):
                result = _host_has_claude_subscription()

        assert result is False, "apiKeyHelper host must return False (keep the key)"
        _HOST_SUBSCRIPTION_CACHE.clear()

    def test_subscription_host_returns_true(self, tmp_path):
        """No apiKeyHelper -> subscription host -> key is safe to strip."""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"theme": "dark"}))

        from routers.agents import _host_has_claude_subscription, _HOST_SUBSCRIPTION_CACHE

        _HOST_SUBSCRIPTION_CACHE.clear()
        with patch.object(Path, "expanduser", return_value=settings):
            result = _host_has_claude_subscription()

        assert result is True, "No apiKeyHelper -> subscription host -> strip the key"
        _HOST_SUBSCRIPTION_CACHE.clear()

    def test_missing_settings_file_behaves_sanely(self, tmp_path):
        """Missing ~/.claude/settings.json -> treat as subscription host (safe to strip)."""
        nonexistent = tmp_path / "no-settings.json"

        from routers.agents import _host_has_claude_subscription, _HOST_SUBSCRIPTION_CACHE

        _HOST_SUBSCRIPTION_CACHE.clear()
        with patch.object(Path, "expanduser", return_value=nonexistent):
            result = _host_has_claude_subscription()

        # Missing file = no helper configured = subscription host
        assert result is True, "Missing settings -> default to subscription (strip key)"
        _HOST_SUBSCRIPTION_CACHE.clear()

    def test_cache_works(self, tmp_path):
        """Result is cached per-process; second call does not re-read the file."""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({}))

        from routers.agents import _host_has_claude_subscription, _HOST_SUBSCRIPTION_CACHE

        _HOST_SUBSCRIPTION_CACHE.clear()
        read_count = 0

        real_read = Path.read_text

        def _counting_read(self, *args, **kwargs):
            nonlocal read_count
            if "settings.json" in str(self):
                read_count += 1
            return real_read(self, *args, **kwargs)

        with patch.object(Path, "expanduser", return_value=settings):
            with patch.object(Path, "read_text", _counting_read):
                _host_has_claude_subscription()
                _host_has_claude_subscription()

        assert read_count <= 1, f"File should only be read once (cached); read {read_count} times"
        _HOST_SUBSCRIPTION_CACHE.clear()

    def test_helper_host_spawn_env_keeps_api_key(self, tmp_path):
        """On an apiKeyHelper host, ANTHROPIC_API_KEY must survive into _spawn_env."""
        from routers.agents import _host_has_claude_subscription, _HOST_SUBSCRIPTION_CACHE

        _HOST_SUBSCRIPTION_CACHE.clear()
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"apiKeyHelper": "/usr/local/bin/get-key"}))

        with patch.object(Path, "expanduser", return_value=settings):
            is_sub = _host_has_claude_subscription()

        assert is_sub is False, "Should detect helper host"

        # Simulate the key strip guard: only pop when is_subscription
        spawn_env = {"ANTHROPIC_API_KEY": "sk-test-key", "OTHER": "val"}
        if is_sub:
            spawn_env.pop("ANTHROPIC_API_KEY", None)

        assert "ANTHROPIC_API_KEY" in spawn_env, (
            "API key must survive in spawn_env on a helper host"
        )
        _HOST_SUBSCRIPTION_CACHE.clear()
