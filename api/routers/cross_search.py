"""Cross-source search endpoint — POST /api/cross-source (P3).

Fan-out strategy: run all authorized Searchable connectors in parallel with
per-provider timeout; a slow or failed provider is added to providers_skipped,
not fatal. On main branch get_search_strategy always returns fan_out_strategy.
No GE/NR references.
"""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Body, Depends

from services.excerpts import Excerpt, Searchable

router = APIRouter()

_PROVIDER_TIMEOUT = 8.0  # seconds per provider


async def _run_provider(name: str, fn: Searchable, query: str, limit: int) -> tuple[str, list[Excerpt], str | None]:
    """Run one provider; return (name, results, skip_reason_or_None)."""
    try:
        results = await asyncio.wait_for(fn(query, limit), timeout=_PROVIDER_TIMEOUT)
        return name, results, None
    except asyncio.TimeoutError:
        return name, [], "timeout"
    except Exception as exc:
        return name, [], str(exc) or "error"


async def fan_out_strategy(query: str, limit: int, providers: list[str] | None = None) -> dict:
    """Run all connected providers in parallel; return merged results."""
    from services.connectors_main import slack_searchable, atlassian_searchable
    from services.slack import get_tokens as slack_get_tokens
    from services.atlassian import get_config as atlassian_get_config

    all_connectors: dict[str, Searchable] = {}

    # Slack — only include if a token is configured
    try:
        tokens = slack_get_tokens()
        if tokens.get("access_token") or tokens.get("authed_user", {}).get("access_token"):
            all_connectors["slack"] = slack_searchable
    except Exception:
        pass

    # Atlassian — only include if configured
    try:
        cfg = atlassian_get_config()
        if cfg:
            all_connectors["atlassian"] = atlassian_searchable
    except Exception:
        pass

    if providers:
        all_connectors = {k: v for k, v in all_connectors.items() if k in providers}

    if not all_connectors:
        return {"results": [], "providers_used": [], "providers_skipped": []}

    tasks = [
        _run_provider(name, fn, query, limit)
        for name, fn in all_connectors.items()
    ]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[dict] = []
    providers_used: list[str] = []
    providers_skipped: list[dict] = []

    for outcome in outcomes:
        if isinstance(outcome, Exception):
            continue
        name, results, skip_reason = outcome
        if skip_reason is not None:
            providers_skipped.append({"provider": name, "reason": skip_reason})
        else:
            providers_used.append(name)
            for exc in results:
                all_results.append({
                    "text": exc.text,
                    "source_id": exc.source_id,
                    "source_title": exc.source_title,
                    "deep_link": exc.deep_link,
                    "score": exc.score,
                    "access_denied": exc.access_denied,
                    "provider": exc.provider,
                })

    return {
        "results": all_results,
        "providers_used": providers_used,
        "providers_skipped": providers_skipped,
    }


def get_search_strategy():
    """FastAPI dependency that selects the cross-source search strategy.

    Returns the generic fan-out strategy. A deployment can substitute a
    different strategy by overriding this dependency, with no change here.
    """
    return fan_out_strategy


@router.post("/cross-source")
async def cross_source_search(
    body: Annotated[dict, Body()],
    strategy=Depends(get_search_strategy),
):
    query = body.get("query", "")
    limit = int(body.get("limit", 10))
    providers = body.get("providers")
    return await strategy(query, limit, providers)
