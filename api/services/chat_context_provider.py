"""Unified chat context provider.

Routes intent using ostk semantic search (work domain) and a TOPIC_MAP
term-bag (external services), checks per-service connection guards,
fans out fetches in parallel, and returns a formatted context string.
"""

from __future__ import annotations

import asyncio
import re
from typing import Callable

from services.google_auth import is_authenticated
from services import slack, atlassian, github, imessage
from services import imessage_contacts
from services import gmail
from services import calendar as cal_service
from services.ostk import ostk

TOPIC_MAP: dict[str, set[str]] = {
    "gmail": {"email", "mail", "gmail", "inbox", "flight", "booking",
               "reservation", "attachment", "sent", "unread", "confirmation"},
    "calendar": {"calendar", "meeting", "schedule", "today", "tomorrow",
                  "event", "appointment", "agenda", "field trip"},
    "slack": {"slack", "channel", "workspace", "dm"},
    "jira": {"jira", "ticket", "sprint", "confluence", "atlassian"},
    "github": {"github", "pr", "repo", "commit", "branch"},
    "imessage": {"imessage", "sms", "texted"},
    "contacts": {"contact", "who is"},
}


def _words(text: str) -> set[str]:
    return set(re.split(r"\W+", text.lower()))


class ChatContextProvider:
    """Fetch and assemble context from connected services based on message intent."""

    async def route_intent(self, message: str) -> list[str]:
        """Return source names to fetch for this message (connected sources only)."""
        sources: list[str] = []

        try:
            result = await ostk.search_near(message)
            if result.get("tasks"):
                sources.append("tasks")
        except Exception:
            pass

        msg_words = _words(message)
        msg_lower = message.lower()
        for source, terms in TOPIC_MAP.items():
            multi_word = {t for t in terms if " " in t}
            single_word = terms - multi_word
            matched = bool(msg_words & single_word) or any(t in msg_lower for t in multi_word)
            if not matched:
                continue
            if not self._is_connected(source):
                continue
            if source not in sources:
                sources.append(source)

        return sources

    def _is_connected(self, source: str) -> bool:
        if source in ("gmail", "calendar"):
            return is_authenticated()
        if source == "slack":
            return slack.is_connected()
        if source == "jira":
            return atlassian.is_connected()
        if source == "github":
            return github.is_connected()
        if source in ("imessage", "contacts"):
            return imessage.is_available().get("available", False)
        return False

    async def build(self, message: str) -> str:
        """Return assembled context string, or '' when no sources match."""
        sources = await self.route_intent(message)
        if not sources:
            return ""

        fetch_map: dict[str, Callable] = {
            "tasks": self._fetch_tasks,
            "gmail": self._fetch_gmail,
            "calendar": self._fetch_calendar,
            "slack": self._fetch_slack,
            "jira": self._fetch_jira,
            "github": self._fetch_github,
            "imessage": self._fetch_imessage,
            "contacts": self._fetch_contacts,
        }

        coros = [fetch_map[s](message) for s in sources if s in fetch_map]
        results = await asyncio.gather(*coros, return_exceptions=True)

        parts = [r for r in results if isinstance(r, str) and r]
        if not parts:
            return ""
        return "[Context from your connected services]\n\n" + "\n\n".join(parts)

    async def _fetch_tasks(self, query: str) -> str:
        result = await ostk.search_near(query)
        tasks = result.get("tasks", [])
        if not tasks:
            return ""
        lines = ["## Tasks"]
        for t in tasks[:10]:
            tag = t.get("priority") or t.get("status", "")
            lines.append(f"  [{tag}] #{t.get('id', '')} {t.get('title', '')}")
        return "\n".join(lines)

    async def _fetch_gmail(self, query: str) -> str:
        messages = await gmail.search_messages(query, max_results=8)
        if not messages:
            return ""
        lines = ["## Email"]
        for m in messages:
            sender = m.get("from", m.get("sender", ""))
            subject = m.get("subject", "")
            snippet = m.get("snippet", "")
            lines.append(f"  From: {sender} | {subject}")
            if snippet:
                lines.append(f"    {snippet[:120]}")
        return "\n".join(lines)

    async def _fetch_calendar(self, query: str) -> str:
        events = await cal_service.get_today_events()
        if not events:
            return ""
        lines = ["## Calendar"]
        for ev in events:
            title = ev.get("summary", "Untitled")
            start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date") or ""
            end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date") or ""

            def _fmt(dt_str: str) -> str:
                if not dt_str:
                    return ""
                try:
                    from datetime import datetime
                    return datetime.fromisoformat(dt_str).strftime("%-I:%M%p").lower()
                except Exception:
                    return dt_str[:5]

            s = _fmt(start)
            e = _fmt(end)
            if s and e:
                lines.append(f"  {s}-{e}: {title}")
            elif s:
                lines.append(f"  {s}: {title}")
            else:
                lines.append(f"  {title}")
        return "\n".join(lines)

    async def _fetch_slack(self, query: str) -> str:
        messages = await slack.search_messages(query, count=8)
        if not messages:
            return ""
        lines = ["## Slack"]
        for m in messages:
            channel = m.get("channel", "")
            user = m.get("user", "")
            text = m.get("text", "")[:140]
            lines.append(f"  #{channel} {user}: {text}")
        return "\n".join(lines)

    async def _fetch_jira(self, query: str) -> str:
        results = await atlassian.search(query, limit=5)
        if not results:
            return ""
        lines = ["## Jira / Confluence"]
        for r in results:
            key = getattr(r, "key", "") or ""
            title = getattr(r, "title", "") or getattr(r, "summary", "")
            lines.append(f"  {key}: {title}")
        return "\n".join(lines)

    async def _fetch_github(self, query: str) -> str:
        issues = await github.list_issues(state="open", per_page=8)
        if not issues:
            return ""
        lines = ["## GitHub Issues"]
        for i in issues:
            lines.append(f"  #{i.get('number', '')}: {i.get('title', '')}")
        return "\n".join(lines)

    async def _fetch_imessage(self, query: str) -> str:
        messages = await imessage.search_messages(query, limit=8)
        if not messages:
            return ""
        lines = ["## Messages"]
        for m in messages:
            sender = m.get("handle", m.get("sender", ""))
            text = m.get("text", "")[:140]
            lines.append(f"  {sender}: {text}")
        return "\n".join(lines)

    async def _fetch_contacts(self, query: str) -> str:
        contacts = await asyncio.get_event_loop().run_in_executor(
            None, lambda: imessage_contacts.search_by_prefix(query, limit=5)
        )
        if not contacts:
            return ""
        lines = ["## Contacts"]
        for c in contacts:
            detail = c.get("phone", "") or c.get("email", "")
            lines.append(f"  {c.get('name', '')}: {detail}")
        return "\n".join(lines)


context_provider = ChatContextProvider()
