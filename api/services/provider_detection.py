from __future__ import annotations

import os

from services.settings_store import settings_store
from services.claude_code_provider import is_claude_code_available


async def detect_providers() -> dict[str, bool]:
    """Return availability of known AI providers.

    Checks Claude Code subscription login, ANTHROPIC_API_KEY, and GEMINI_API_KEY
    across environment variables and the settings store. No secrets are returned.
    """
    claude_code = await is_claude_code_available()

    anthropic_key = bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or settings_store.get("anthropic_api_key")
    )

    gemini_key = bool(
        os.environ.get("GEMINI_API_KEY")
        or settings_store.get("gemini_api_key")
    )

    return {
        "claude_code": claude_code,
        "anthropic_key": anthropic_key,
        "gemini_key": gemini_key,
    }
