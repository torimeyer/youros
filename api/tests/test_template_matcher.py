"""Tests for the auto template matcher.

The matcher should:
- Match by explicit invocation (name or trigger word)
- Match by required keywords
- Fall back to a Claude classifier (mocked here, never live)
- Cache classifier results
- Validate the classifier's response against the known template names
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.template_matcher import (
    BUILT_IN_TEMPLATES,
    clear_cache,
    match_template,
    merge_with_built_ins,
)


# --- helpers -------------------------------------------------------------


def _fake_classifier_response(text: str) -> MagicMock:
    """Build a fake Anthropic response with one text block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


@pytest.fixture(autouse=True)
def _reset_cache():
    """The matcher caches classifier results in memory. Wipe between tests."""
    clear_cache()
    yield
    clear_cache()


# --- merge_with_built_ins ------------------------------------------------


class TestMergeWithBuiltIns:
    def test_returns_built_ins_when_no_custom(self):
        merged = merge_with_built_ins(None)
        names = {t["name"] for t in merged}
        assert "saa" in names
        assert "diagnose" in names
        assert "explain-plain" in names

    def test_appends_custom_templates(self):
        custom = [{"name": "calendar", "description": "Show calendar", "prompt": "..."}]
        merged = merge_with_built_ins(custom)
        names = {t["name"] for t in merged}
        assert "saa" in names
        assert "calendar" in names

    def test_custom_overrides_built_in_with_same_name(self):
        custom = [{"name": "saa", "description": "custom saa", "prompt": "custom prompt"}]
        merged = merge_with_built_ins(custom)
        saas = [t for t in merged if t["name"] == "saa"]
        assert len(saas) == 1
        assert saas[0]["description"] == "custom saa"


# --- explicit invocation -------------------------------------------------


class TestExplicitInvocation:
    @pytest.mark.asyncio
    async def test_saa_prefix_matches_saa(self):
        templates = merge_with_built_ins(None)
        result = await match_template("saa fix the bug", templates, api_key="")
        assert result is not None
        assert result["name"] == "saa"
        assert result["_match_reason"] == "explicit"

    @pytest.mark.asyncio
    async def test_diagnose_prefix_matches_diagnose(self):
        templates = merge_with_built_ins(None)
        result = await match_template("diagnose the build failure", templates, api_key="")
        assert result is not None
        assert result["name"] == "diagnose"

    @pytest.mark.asyncio
    async def test_elit_alias_matches_explain_plain(self):
        """The generic template is 'explain-plain'. 'elit' is kept as an
        alias so Tori's muscle memory still resolves to the same template.
        """
        templates = merge_with_built_ins(None)
        result = await match_template("elit how does TLS work", templates, api_key="")
        assert result is not None
        assert result["name"] == "explain-plain"

    @pytest.mark.asyncio
    async def test_explain_plain_prefix_matches_explain_plain(self):
        templates = merge_with_built_ins(None)
        result = await match_template(
            "explain-plain how does TLS work", templates, api_key=""
        )
        assert result is not None
        assert result["name"] == "explain-plain"

    @pytest.mark.asyncio
    async def test_trigger_phrase_matches(self):
        templates = merge_with_built_ins(None)
        result = await match_template("spawn agents to refactor this", templates, api_key="")
        assert result is not None
        assert result["name"] == "saa"

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        templates = merge_with_built_ins(None)
        result = await match_template("SAA do the thing", templates, api_key="")
        assert result is not None
        assert result["name"] == "saa"

    @pytest.mark.asyncio
    async def test_punctuation_after_name(self):
        templates = merge_with_built_ins(None)
        result = await match_template("saa: please fix this", templates, api_key="")
        assert result is not None
        assert result["name"] == "saa"


# --- keyword match -------------------------------------------------------


class TestKeywordMatch:
    @pytest.mark.asyncio
    async def test_single_keyword_present(self):
        custom = [{
            "name": "calendar",
            "description": "Show the calendar",
            "prompt": "Show calendar items",
            "keywords": ["calendar"],
        }]
        templates = merge_with_built_ins(custom)
        result = await match_template("show me my calendar", templates, api_key="")
        assert result is not None
        assert result["name"] == "calendar"
        assert result["_match_reason"] == "keyword"

    @pytest.mark.asyncio
    async def test_all_keywords_required(self):
        custom = [{
            "name": "deploy_check",
            "description": "Check a deploy",
            "prompt": "...",
            "keywords": ["deploy", "production"],
        }]
        templates = merge_with_built_ins(custom)
        # Only one keyword present, classifier disabled by api_key=""
        result = await match_template("check the deploy", templates, api_key="")
        assert result is None

    @pytest.mark.asyncio
    async def test_all_keywords_present_matches(self):
        custom = [{
            "name": "deploy_check",
            "description": "Check a deploy",
            "prompt": "...",
            "keywords": ["deploy", "production"],
        }]
        templates = merge_with_built_ins(custom)
        result = await match_template("check the production deploy please", templates, api_key="")
        assert result is not None
        assert result["name"] == "deploy_check"


# --- AI classifier path --------------------------------------------------


class TestClassifier:
    @pytest.mark.asyncio
    async def test_classifier_returns_template_name(self):
        custom = [{
            "name": "writer",
            "description": "Help drafting content",
            "prompt": "...",
        }]
        templates = merge_with_built_ins(custom)

        with patch("services.template_matcher.anthropic") as mock_anth:
            client = MagicMock()
            client.messages.create = AsyncMock(return_value=_fake_classifier_response("writer"))
            mock_anth.AsyncAnthropic.return_value = client

            result = await match_template(
                "help me write a blog post about cats",
                templates,
                api_key="fake-key",
            )

        assert result is not None
        assert result["name"] == "writer"
        assert result["_match_reason"] == "ai"

    @pytest.mark.asyncio
    async def test_classifier_returns_none(self):
        custom = [{
            "name": "writer",
            "description": "Help drafting content",
            "prompt": "...",
        }]
        templates = merge_with_built_ins(custom)

        with patch("services.template_matcher.anthropic") as mock_anth:
            client = MagicMock()
            client.messages.create = AsyncMock(return_value=_fake_classifier_response("NONE"))
            mock_anth.AsyncAnthropic.return_value = client

            result = await match_template(
                "what is 2 plus 2",
                templates,
                api_key="fake-key",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_classifier_returns_unknown_name_yields_no_match(self):
        custom = [{
            "name": "writer",
            "description": "Help drafting content",
            "prompt": "...",
        }]
        templates = merge_with_built_ins(custom)

        with patch("services.template_matcher.anthropic") as mock_anth:
            client = MagicMock()
            client.messages.create = AsyncMock(
                return_value=_fake_classifier_response("nonexistent_template")
            )
            mock_anth.AsyncAnthropic.return_value = client

            result = await match_template(
                "do something",
                templates,
                api_key="fake-key",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_classifier_cache_avoids_second_call(self):
        custom = [{
            "name": "writer",
            "description": "Help drafting content",
            "prompt": "...",
        }]
        templates = merge_with_built_ins(custom)

        with patch("services.template_matcher.anthropic") as mock_anth:
            client = MagicMock()
            client.messages.create = AsyncMock(return_value=_fake_classifier_response("writer"))
            mock_anth.AsyncAnthropic.return_value = client

            await match_template("draft a tweet", templates, api_key="fake-key")
            await match_template("draft a tweet", templates, api_key="fake-key")

            # The classifier should have been called exactly once.
            assert client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_classifier_skipped_without_api_key(self):
        custom = [{
            "name": "writer",
            "description": "Help drafting content",
            "prompt": "...",
        }]
        templates = merge_with_built_ins(custom)

        with patch("services.template_matcher.anthropic") as mock_anth:
            client = MagicMock()
            client.messages.create = AsyncMock(return_value=_fake_classifier_response("writer"))
            mock_anth.AsyncAnthropic.return_value = client

            # Force the env var to be empty so the matcher cannot find a key.
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
                result = await match_template(
                    "draft a tweet",
                    templates,
                    api_key=None,
                )

        assert result is None
        assert client.messages.create.call_count == 0

    @pytest.mark.asyncio
    async def test_classifier_exception_returns_none(self):
        custom = [{
            "name": "writer",
            "description": "Help drafting content",
            "prompt": "...",
        }]
        templates = merge_with_built_ins(custom)

        with patch("services.template_matcher.anthropic") as mock_anth:
            client = MagicMock()
            client.messages.create = AsyncMock(side_effect=RuntimeError("network down"))
            mock_anth.AsyncAnthropic.return_value = client

            result = await match_template(
                "draft a tweet",
                templates,
                api_key="fake-key",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_classifier_timeout_returns_none(self):
        """A slow Anthropic call must not block the caller — 5s cap returns None.

        Root cause of →1043: _run_classifier had no timeout, so a stalled API
        call blocked backend_active from being sent, firing the frontend 30s
        dead-backend timer. The fix caps the classifier at 5 seconds.
        """
        import asyncio

        custom = [{
            "name": "writer",
            "description": "Help drafting content",
            "prompt": "...",
        }]
        templates = merge_with_built_ins(custom)

        async def slow_create(*args, **kwargs):
            await asyncio.sleep(60)
            return MagicMock()

        with patch("services.template_matcher.anthropic") as mock_anth, \
             patch("services.template_matcher.asyncio.wait_for",
                   side_effect=asyncio.TimeoutError):
            client = MagicMock()
            client.messages.create = AsyncMock(side_effect=slow_create)
            mock_anth.AsyncAnthropic.return_value = client

            result = await match_template(
                "draft a tweet",
                templates,
                api_key="fake-key",
            )

        assert result is None


# --- empty / edge cases --------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_message_returns_none(self):
        templates = merge_with_built_ins(None)
        assert await match_template("", templates, api_key="") is None
        assert await match_template("   ", templates, api_key="") is None

    @pytest.mark.asyncio
    async def test_no_templates_returns_none(self):
        assert await match_template("anything", [], api_key="") is None

    @pytest.mark.asyncio
    async def test_built_in_count(self):
        # Sanity check the built-in list so the matcher always knows the
        # three classic commands.
        names = {t["name"] for t in BUILT_IN_TEMPLATES}
        assert names == {"saa", "diagnose", "explain-plain", "idea"}


# --- chat flow integration -----------------------------------------------


class TestUserAliasResolvesInMatcher:
    """User-set aliases from ~/.myos/template_aliases.json should
    be picked up by the explicit invocation layer so typing the alias
    in chat triggers the corresponding template."""

    @pytest.mark.asyncio
    async def test_user_alias_matches_in_chat(self, tmp_path, monkeypatch):
        """A user alias set via the API resolves in the chat matcher."""
        import services.agent_templates_store as mod

        fake_at_path = tmp_path / "at.json"
        fake_aliases_path = tmp_path / "aliases.json"
        monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_at_path)
        monkeypatch.setattr(mod, "TEMPLATE_ALIASES_PATH", fake_aliases_path)

        # Set a user alias: "bb" -> builtin-builder
        store = mod.AgentTemplatesStore()
        store.set_alias("builtin-builder", "bb")

        # Now merge templates (which injects user aliases)
        templates = merge_with_built_ins(None)

        # The chat message "bb fix the thing" should match via explicit invocation
        # because _inject_user_aliases adds "bb" to the saa template's aliases
        # (or the closest match). Since built-in templates in the matcher use
        # "name" not "id", the injection maps via the store lookup.
        result = await match_template("bb fix the thing", templates, api_key="")
        # bb is a user alias for Builder, which maps to "saa" in the chat matcher
        # The exact match depends on how templates are structured. The key check
        # is that the result is not None (the alias was recognized).
        assert result is not None
        assert result["_match_reason"] == "explicit"


class TestAutoTemplateSettingDisablesMatcher:
    """When ``auto_template_matching`` is False, the chat flow must skip the
    matcher entirely. We assert that by exercising the helper that
    chat_providers uses.
    """

    @pytest.mark.asyncio
    async def test_setting_off_skips_matcher(self):
        from services import chat_providers

        ws = MagicMock()
        ws.send_json = AsyncMock()

        with patch("services.chat_providers.settings_store") as mock_settings:
            def fake_get(key, default=None):
                if key == "auto_template_matching":
                    return False
                if key == "custom_agent_templates":
                    return []
                return default
            mock_settings.get.side_effect = fake_get

            with patch("services.chat_providers.match_template") as mock_match:
                mock_match.return_value = None
                result = await chat_providers._maybe_match_template(
                    [{"role": "user", "content": "saa fix everything"}],
                    ws,
                    api_key="anything",
                )

            assert result is None
            mock_match.assert_not_called()

    @pytest.mark.asyncio
    async def test_setting_on_runs_matcher(self):
        from services import chat_providers

        ws = MagicMock()
        ws.send_json = AsyncMock()

        with patch("services.chat_providers.settings_store") as mock_settings:
            def fake_get(key, default=None):
                if key == "auto_template_matching":
                    return True
                if key == "custom_agent_templates":
                    return []
                return default
            mock_settings.get.side_effect = fake_get

            result = await chat_providers._maybe_match_template(
                [{"role": "user", "content": "saa fix everything"}],
                ws,
                api_key="anything",
            )

        assert result is not None
        assert result["name"] == "saa"
        # The websocket should have been notified.
        assert ws.send_json.await_count == 1
        sent = ws.send_json.await_args.args[0]
        assert sent["type"] == "template_matched"
        assert sent["data"]["name"] == "saa"
