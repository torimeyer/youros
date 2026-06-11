"""F3: recently merged pull requests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services import github as github_service


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.asyncio
async def test_list_merged_prs_filters_to_window():
    payload = [
        {"number": 1, "title": "merged recently", "merged_at": _iso(2)},
        {"number": 2, "title": "merged long ago", "merged_at": _iso(30)},
        {"number": 3, "title": "closed not merged", "merged_at": None},
    ]
    with patch.object(github_service, "_repo", return_value="acme/web"), \
         patch.object(github_service, "_github_get", AsyncMock(return_value=payload)):
        result = await github_service.list_merged_prs(days=7)
    nums = [r["number"] for r in result]
    assert nums == [1]
    assert result[0]["title"] == "merged recently"
    assert "merged_at" in result[0]


@pytest.mark.asyncio
async def test_list_merged_prs_calls_closed_sorted():
    spy = AsyncMock(return_value=[])
    with patch.object(github_service, "_repo", return_value="acme/web"), \
         patch.object(github_service, "_github_get", spy):
        await github_service.list_merged_prs(days=7)
    path, params = spy.await_args.args[0], spy.await_args.args[1]
    assert path == "/repos/acme/web/pulls"
    assert params["state"] == "closed"
    assert params["sort"] == "updated"
