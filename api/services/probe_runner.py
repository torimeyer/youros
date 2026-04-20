import asyncio
import time
from datetime import datetime, timezone
from typing import Optional, Dict

import anthropic

async def run_probe() -> Dict[str, Any]:
    """Run a health check probe by sending a test message to Claude.

    Returns a dict with:
    - timestamp: ISO format timestamp of when probe ran
    - status: "ok" if response contains "ok" and takes ≤10s, "failed" otherwise
    - error: error message if failed
    """
    try:
        client = anthropic.Anthropic()

        start_time = time.time()
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=10,
            messages=[
                {"role": "user", "content": "Reply with the single word: ok"}
            ],
            timeout=15.0,
        )
        elapsed = time.time() - start_time

        # Check if response took too long or doesn't contain "ok"
        content = message.content[0].text if message.content else ""
        if elapsed > 10.0:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "failed",
                "error": f"Response took {elapsed:.1f}s (>10s timeout)"
            }

        if "ok" not in content.lower():
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "failed",
                "error": f"Response did not contain 'ok': {content[:100]}"
            }

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
        }
    except Exception as e:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error": str(e)
        }
