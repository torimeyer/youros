import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

PROBE_STATE_FILE = Path.home() / ".myos" / "probe_state.json"


async def probe_claude_api() -> dict:
    """Probe Claude API with a simple test message.

    Returns:
        dict with keys:
        - status: "pass" or "fail"
        - timestamp: ISO timestamp of when probe ran
        - duration_ms: how long the request took
        - error: error message if status is "fail", None otherwise
    """
    PROBE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    from services.ostk_secrets import get_anthropic_key
    api_key = await get_anthropic_key()
    if not api_key:
        result = {
            "status": "fail",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": 0,
            "error": "ANTHROPIC_API_KEY not set",
        }
        _save_probe_result(result)
        return result

    start_time = time.time()
    try:
        client = anthropic.Anthropic(api_key=api_key)

        # Set 10 second timeout
        message = await asyncio.wait_for(
            asyncio.to_thread(
                client.messages.create,
                model="claude-opus-4-7",
                max_tokens=10,
                messages=[
                    {
                        "role": "user",
                        "content": "Reply with just the word 'ok' and nothing else.",
                    }
                ],
            ),
            timeout=10.0,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        # Check if response contains "ok"
        response_text = ""
        if message.content and len(message.content) > 0:
            response_text = message.content[0].text.lower()

        if "ok" in response_text:
            result = {
                "status": "pass",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration_ms": duration_ms,
                "error": None,
            }
        else:
            result = {
                "status": "fail",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "duration_ms": duration_ms,
                "error": f"Response missing 'ok': {response_text[:100]}",
            }

    except asyncio.TimeoutError:
        duration_ms = int((time.time() - start_time) * 1000)
        result = {
            "status": "fail",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "error": "Request exceeded 10 second timeout",
        }
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        result = {
            "status": "fail",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "error": str(e),
        }

    _save_probe_result(result)
    return result


def get_last_probe_result() -> Optional[dict]:
    """Get the last probe result from disk, or None if no result exists."""
    if not PROBE_STATE_FILE.exists():
        return None

    try:
        data = json.loads(PROBE_STATE_FILE.read_text())
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _save_probe_result(result: dict) -> None:
    """Save probe result to disk for persistence."""
    try:
        PROBE_STATE_FILE.write_text(json.dumps(result, indent=2))
    except OSError as e:
        logger.error(f"Failed to save probe result: {e}")
