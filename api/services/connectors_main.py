"""Main-branch connector adapters for cross-source search (P3).

Each function is a Searchable: async (query, limit) -> list[Excerpt].
Adapters bridge existing service return types to the Excerpt contract.
No GE/NR references.
"""
from __future__ import annotations

from services.excerpts import Excerpt


async def slack_searchable(query: str, limit: int = 10) -> list[Excerpt]:
    """Adapt slack.search_messages (dicts) to Excerpt objects."""
    from services.slack import search_messages

    try:
        messages = await search_messages(query, count=limit)
    except Exception:
        return []

    results = []
    for msg in messages:
        text = msg.get("text", "")
        channel = msg.get("channel", "")
        permalink = msg.get("permalink", "")
        user = msg.get("user", "")
        source_title = f"Slack #{channel}" if channel else "Slack"
        results.append(Excerpt(
            text=text,
            source_id=msg.get("ts", ""),
            source_title=source_title,
            deep_link=permalink if permalink else None,
            score=1.0,
            access_denied=False,
            provider="slack",
        ))
    return results


async def atlassian_searchable(query: str, limit: int = 10) -> list[Excerpt]:
    """Delegate to atlassian.search() which combines Jira + Confluence."""
    from services.atlassian import search as atlassian_search

    try:
        return await atlassian_search(query, limit)
    except Exception:
        return []
