"""Provider-aware health check probe.

Sends a tiny test message to the configured LLM provider and reports
whether it answered in time. The provider is read from the user's
settings so this stays correct when they switch from Claude to Gemini
(or any future provider) without the frontend needing to know which one
was tested.

The return shape is deliberately provider-neutral:
    {
      "timestamp": ISO 8601 string,
      "status": "ok" | "failed",
      "provider": "claude" | "gemini" | ...,
      "error": str | (absent when status == "ok"),
    }

The sidebar dot treats "ok" / "pass" as healthy and anything else as
unhealthy, so new providers can plug in without frontend changes.
"""
import time
from datetime import datetime, timezone
from typing import Any, Dict

import anthropic

from services.settings_store import settings_store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_provider() -> str:
    """Return the lower-case provider key the user currently has selected.

    The settings store uses a model string like "@claude" or "@gemini".
    We strip the "@" prefix and lowercase so the rest of the function
    can branch on a simple string.
    """
    raw = settings_store.get("default_model", "@claude") or "@claude"
    if isinstance(raw, str) and raw.startswith("@"):
        raw = raw[1:]
    return str(raw or "claude").lower()


def _probe_claude() -> Dict[str, Any]:
    """Send a 'reply ok' message to Claude and report pass/fail.

    Returns the provider-neutral probe shape. Any exception (missing key,
    network error, rate limit) is caught and reported as status=failed
    so the sidebar dot shows red instead of crashing the endpoint.
    """
    try:
        client = anthropic.Anthropic()
        start = time.time()
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=10,
            messages=[
                {"role": "user", "content": "Reply with the single word: ok"}
            ],
            timeout=15.0,
        )
        elapsed = time.time() - start
        content = message.content[0].text if message.content else ""

        if elapsed > 10.0:
            return {
                "timestamp": _now(),
                "status": "failed",
                "provider": "claude",
                "error": f"Response took {elapsed:.1f}s (>10s timeout)",
            }
        if "ok" not in content.lower():
            return {
                "timestamp": _now(),
                "status": "failed",
                "provider": "claude",
                "error": f"Response did not contain 'ok': {content[:100]}",
            }
        return {
            "timestamp": _now(),
            "status": "ok",
            "provider": "claude",
        }
    except Exception as e:
        return {
            "timestamp": _now(),
            "status": "failed",
            "provider": "claude",
            "error": str(e),
        }


async def run_probe() -> Dict[str, Any]:
    """Run a health check against the user's configured provider.

    Branches on the provider key. Today only Claude has a wired client.
    TODO: when a Gemini or other provider client lands, add a branch
    here. The return shape already accommodates a "provider" field so
    the frontend does not need to care which one was tested.
    """
    provider = _resolve_provider()

    if provider == "claude":
        return _probe_claude()

    # TODO: Add Gemini / other provider branches as clients land. Until
    # then we report a clear "not configured" failure so the sidebar dot
    # turns red with an actionable message instead of pretending to pass.
    return {
        "timestamp": _now(),
        "status": "failed",
        "provider": provider,
        "error": f"Health check for '{provider}' is not wired yet",
    }
