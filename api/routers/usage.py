"""
/api/usage — combined Claude + Gemini subscription status panel.

Source strategy (no stable CLI quota API exists):
- Claude: audit + transcript events, filtered by model prefix. Auth source is
  always "subscription" on this install (ANTHROPIC_API_KEY is stripped from
  spawn env per feedback_prefer_subscription_over_api_key.md).
- Gemini: same audit events, filtered to gemini/* model entries.
- Daily rolling window comes from the by_date aggregation in costs.py.
- Session-level token snapshot from `ostk profile tokens --json`.

What this does NOT include: Anthropic's server-side quota (reset date, hard
cap). That requires an authenticated call to the Anthropic account API which
is not yet wired. See docs/integrations/claude-usage.md.
"""
import asyncio
import json
import subprocess
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter

router = APIRouter(tags=["usage"])

_CLAUDE_PREFIXES = ("claude",)
_GEMINI_PREFIXES = ("gemini",)


def _is_model(model: str, prefixes: tuple[str, ...]) -> bool:
    m = (model or "").lower()
    return any(m.startswith(p) for p in prefixes)


def _rolling_daily(by_date: list[dict], prefixes: tuple[str, ...], days: int = 5) -> list[dict]:
    today = date.today()
    window = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    index: dict[str, dict] = {d["date"]: d for d in by_date}

    result = []
    for day_str in window:
        row = index.get(day_str, {})
        result.append({
            "date": day_str,
            "messages": row.get("count", 0),
            "input_tokens": row.get("input_tokens", 0),
            "output_tokens": row.get("output_tokens", 0),
        })
    return result


def _model_totals(by_model: list[dict], prefixes: tuple[str, ...]) -> dict:
    total_messages = 0
    input_tokens = 0
    output_tokens = 0
    for m in by_model:
        if _is_model(m.get("model", ""), prefixes):
            total_messages += m.get("count", 0)
            input_tokens += m.get("input_tokens", 0)
            output_tokens += m.get("output_tokens", 0)
    return {
        "total_messages": total_messages,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _today_model_totals(by_date_today: Optional[dict], by_model: list[dict], prefixes: tuple[str, ...]) -> int:
    if by_date_today is None:
        return 0
    # Use proportion of today's total from each model's share
    today_count = by_date_today.get("count", 0)
    all_count = sum(m.get("count", 0) for m in by_model if _is_model(m.get("model", ""), prefixes))
    return all_count if today_count > 0 else 0


async def _session_tokens() -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ostk", "profile", "tokens", "--json",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return json.loads(stdout)
    except Exception:
        return {}


@router.get("/usage")
async def get_usage():
    """Return combined Claude + Gemini subscription usage state.

    Pulls from the same audit+transcript cache as /api/costs so there
    is no extra disk I/O. Session tokens come from ostk profile tokens.
    """
    from routers.costs import _get_costs_cached

    all_data = _get_costs_cached("all")
    today_data = _get_costs_cached("today")

    by_model: list[dict] = all_data.get("by_model", [])
    by_date_all: list[dict] = all_data.get("by_date", [])
    today_str = date.today().isoformat()
    by_date_today: Optional[dict] = next((d for d in by_date_all if d.get("date") == today_str), None)

    claude_totals = _model_totals(by_model, _CLAUDE_PREFIXES)
    gemini_totals = _model_totals(by_model, _GEMINI_PREFIXES)

    claude_daily = _rolling_daily(by_date_all, _CLAUDE_PREFIXES)
    gemini_daily = _rolling_daily(by_date_all, _GEMINI_PREFIXES)

    today_model_data = today_data.get("by_model", [])
    claude_today = _model_totals(today_model_data, _CLAUDE_PREFIXES)
    gemini_today = _model_totals(today_model_data, _GEMINI_PREFIXES)

    session_tokens = await _session_tokens()

    return {
        "claude": {
            "auth_source": "subscription",
            "messages_today": claude_today["total_messages"],
            "tokens_today": claude_today["input_tokens"] + claude_today["output_tokens"],
            "total_messages": claude_totals["total_messages"],
            "recent_daily": claude_daily,
            "session_tokens": session_tokens.get("aggregate", {}),
            "quota_available": False,
            "quota_note": "Subscription quota not available without Anthropic account API auth. See docs/integrations/claude-usage.md.",
        },
        "gemini": {
            "auth_source": "gemini_cli",
            "messages_today": gemini_today["total_messages"],
            "tokens_today": gemini_today["input_tokens"] + gemini_today["output_tokens"],
            "total_messages": gemini_totals["total_messages"],
            "recent_daily": gemini_daily,
            "quota_available": False,
            "quota_note": "Gemini CLI has no native quota command. Usage sourced from local audit log.",
        },
    }
