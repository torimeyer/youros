"""Tests for ChatContextProvider intent routing and context assembly."""

import pytest
from unittest.mock import AsyncMock, patch


class TestRouteIntent:
    @pytest.mark.asyncio
    async def test_empty_message_no_ostk_hits_returns_empty(self):
        from services.chat_context_provider import ChatContextProvider
        provider = ChatContextProvider()
        with (
            patch("services.chat_context_provider.ostk") as mock_ostk,
            patch("services.chat_context_provider.is_authenticated", return_value=False),
            patch("services.chat_context_provider.slack") as mock_slack,
            patch("services.chat_context_provider.atlassian") as mock_atlassian,
            patch("services.chat_context_provider.github") as mock_github,
            patch("services.chat_context_provider.imessage") as mock_imsg,
        ):
            mock_ostk.search_near = AsyncMock(return_value={"tasks": [], "query": ""})
            mock_slack.is_connected.return_value = False
            mock_atlassian.is_connected.return_value = False
            mock_github.is_connected.return_value = False
            mock_imsg.is_available.return_value = {"available": False}
            sources = await provider.route_intent("what is the capital of France")
        assert sources == []

    @pytest.mark.asyncio
    async def test_flight_keyword_selects_gmail_when_authenticated(self):
        from services.chat_context_provider import ChatContextProvider
        provider = ChatContextProvider()
        with (
            patch("services.chat_context_provider.ostk") as mock_ostk,
            patch("services.chat_context_provider.is_authenticated", return_value=True),
            patch("services.chat_context_provider.slack") as mock_slack,
            patch("services.chat_context_provider.atlassian") as mock_atlassian,
            patch("services.chat_context_provider.github") as mock_github,
            patch("services.chat_context_provider.imessage") as mock_imsg,
        ):
            mock_ostk.search_near = AsyncMock(return_value={"tasks": [], "query": ""})
            mock_slack.is_connected.return_value = False
            mock_atlassian.is_connected.return_value = False
            mock_github.is_connected.return_value = False
            mock_imsg.is_available.return_value = {"available": False}
            sources = await provider.route_intent("what time is our flight")
        assert "gmail" in sources

    @pytest.mark.asyncio
    async def test_slack_keyword_skipped_when_not_connected(self):
        from services.chat_context_provider import ChatContextProvider
        provider = ChatContextProvider()
        with (
            patch("services.chat_context_provider.ostk") as mock_ostk,
            patch("services.chat_context_provider.is_authenticated", return_value=False),
            patch("services.chat_context_provider.slack") as mock_slack,
            patch("services.chat_context_provider.atlassian") as mock_atlassian,
            patch("services.chat_context_provider.github") as mock_github,
            patch("services.chat_context_provider.imessage") as mock_imsg,
        ):
            mock_ostk.search_near = AsyncMock(return_value={"tasks": [], "query": ""})
            mock_slack.is_connected.return_value = False
            mock_atlassian.is_connected.return_value = False
            mock_github.is_connected.return_value = False
            mock_imsg.is_available.return_value = {"available": False}
            sources = await provider.route_intent("any slack messages about the launch")
        assert "slack" not in sources

    @pytest.mark.asyncio
    async def test_ostk_hit_includes_tasks(self):
        from services.chat_context_provider import ChatContextProvider
        provider = ChatContextProvider()
        with (
            patch("services.chat_context_provider.ostk") as mock_ostk,
            patch("services.chat_context_provider.is_authenticated", return_value=False),
            patch("services.chat_context_provider.slack") as mock_slack,
            patch("services.chat_context_provider.atlassian") as mock_atlassian,
            patch("services.chat_context_provider.github") as mock_github,
            patch("services.chat_context_provider.imessage") as mock_imsg,
        ):
            mock_ostk.search_near = AsyncMock(return_value={
                "tasks": [{"id": "42", "title": "Ship the thing", "status": "open"}],
                "query": "ship",
            })
            mock_slack.is_connected.return_value = False
            mock_atlassian.is_connected.return_value = False
            mock_github.is_connected.return_value = False
            mock_imsg.is_available.return_value = {"available": False}
            sources = await provider.route_intent("what are we shipping this week")
        assert "tasks" in sources


class TestBuild:
    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_sources(self):
        from services.chat_context_provider import ChatContextProvider
        provider = ChatContextProvider()
        with (
            patch("services.chat_context_provider.ostk") as mock_ostk,
            patch("services.chat_context_provider.is_authenticated", return_value=False),
            patch("services.chat_context_provider.slack") as mock_slack,
            patch("services.chat_context_provider.atlassian") as mock_atlassian,
            patch("services.chat_context_provider.github") as mock_github,
            patch("services.chat_context_provider.imessage") as mock_imsg,
        ):
            mock_ostk.search_near = AsyncMock(return_value={"tasks": [], "query": ""})
            mock_slack.is_connected.return_value = False
            mock_atlassian.is_connected.return_value = False
            mock_github.is_connected.return_value = False
            mock_imsg.is_available.return_value = {"available": False}
            result = await provider.build("what is 2+2")
        assert result == ""

    @pytest.mark.asyncio
    async def test_build_includes_both_section_headers_when_two_sources_match(self):
        from services.chat_context_provider import ChatContextProvider
        provider = ChatContextProvider()
        with (
            patch("services.chat_context_provider.ostk") as mock_ostk,
            patch("services.chat_context_provider.is_authenticated", return_value=True),
            patch("services.chat_context_provider.slack") as mock_slack,
            patch("services.chat_context_provider.atlassian") as mock_atlassian,
            patch("services.chat_context_provider.github") as mock_github,
            patch("services.chat_context_provider.imessage") as mock_imsg,
            patch("services.chat_context_provider.gmail") as mock_gmail,
            patch("services.chat_context_provider.cal_service") as mock_cal,
        ):
            mock_ostk.search_near = AsyncMock(return_value={"tasks": [], "query": ""})
            mock_slack.is_connected.return_value = False
            mock_atlassian.is_connected.return_value = False
            mock_github.is_connected.return_value = False
            mock_imsg.is_available.return_value = {"available": False}
            mock_gmail.search_messages = AsyncMock(return_value=[
                {"from": "airline@united.com", "subject": "Your flight", "snippet": "Gate B12"}
            ])
            mock_cal.get_today_events = AsyncMock(return_value=[
                {"summary": "Flight to NYC",
                 "start": {"dateTime": "2026-06-07T09:00:00"},
                 "end": {"dateTime": "2026-06-07T12:00:00"}}
            ])
            result = await provider.build("what time is our flight today")
        assert "## Email" in result
        assert "## Calendar" in result

    @pytest.mark.asyncio
    async def test_build_survives_one_fetch_raising(self):
        from services.chat_context_provider import ChatContextProvider
        provider = ChatContextProvider()
        with (
            patch("services.chat_context_provider.ostk") as mock_ostk,
            patch("services.chat_context_provider.is_authenticated", return_value=True),
            patch("services.chat_context_provider.slack") as mock_slack,
            patch("services.chat_context_provider.atlassian") as mock_atlassian,
            patch("services.chat_context_provider.github") as mock_github,
            patch("services.chat_context_provider.imessage") as mock_imsg,
            patch("services.chat_context_provider.gmail") as mock_gmail,
            patch("services.chat_context_provider.cal_service") as mock_cal,
        ):
            mock_ostk.search_near = AsyncMock(return_value={"tasks": [], "query": ""})
            mock_slack.is_connected.return_value = False
            mock_atlassian.is_connected.return_value = False
            mock_github.is_connected.return_value = False
            mock_imsg.is_available.return_value = {"available": False}
            mock_gmail.search_messages = AsyncMock(side_effect=RuntimeError("token expired"))
            mock_cal.get_today_events = AsyncMock(return_value=[
                {"summary": "Standup",
                 "start": {"dateTime": "2026-06-07T09:00:00"},
                 "end": {"dateTime": "2026-06-07T09:30:00"}}
            ])
            result = await provider.build("what is on my calendar today")
        assert "## Calendar" in result
        assert "Standup" in result
