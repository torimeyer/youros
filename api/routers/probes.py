import logging
from fastapi import APIRouter

from services.probes import probe_claude_api, get_last_probe_result

logger = logging.getLogger(__name__)

router = APIRouter(tags=["probes"])


@router.post("/probes/claude")
async def run_claude_probe():
    """Run the Claude API probe immediately.

    Returns:
        dict with status, timestamp, duration_ms, and error (if any)
    """
    result = await probe_claude_api()
    return result


@router.get("/probes/claude/last")
async def get_claude_probe_status():
    """Get the last Claude API probe result.

    Returns:
        dict with status, timestamp, duration_ms, and error (if any),
        or 404 if no probe has been run yet
    """
    result = get_last_probe_result()
    if result is None:
        return {"status": "unknown", "error": "No probe results yet"}
    return result
