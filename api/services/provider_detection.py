from __future__ import annotations

import os
import subprocess
from pathlib import Path

from services.settings_store import settings_store
from services.claude_code_provider import is_claude_code_available


def detect_vertex_ai() -> bool:
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if creds_path and Path(creds_path).is_file():
        return True
    adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if adc.exists():
        return True
    try:
        r = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True,
            timeout=3,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass
    return False


def detect_bedrock() -> bool:
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return True
    profile = os.environ.get("AWS_PROFILE", "")
    if profile:
        creds_file = Path.home() / ".aws" / "credentials"
        if creds_file.exists() and f"[{profile}]" in creds_file.read_text():
            return True
    try:
        r = subprocess.run(
            ["aws", "sts", "get-caller-identity"],
            capture_output=True,
            timeout=3,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass
    return False


async def detect_providers() -> dict[str, bool]:
    """Return availability of known AI providers.

    Checks Claude Code subscription login, ANTHROPIC_API_KEY, GEMINI_API_KEY,
    Google Vertex AI ADC, and AWS Bedrock credentials. No secrets are returned.
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
        "vertex_ai": detect_vertex_ai(),
        "bedrock": detect_bedrock(),
    }
