from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

from services.settings_store import settings_store
from services.claude_code_provider import is_claude_code_available
from services.gemini_cli_provider import is_gemini_cli_available

# ---------------------------------------------------------------------------
# TTL single-flight cache for detect_providers()
# Prevents N concurrent page-load requests from each spawning a full detection
# run (gemini ping + gcloud + aws). TTL of 30 s is fine because provider
# availability rarely changes within a session.
# ---------------------------------------------------------------------------
_PROVIDERS_CACHE_TTL: float = 30.0
_providers_cache_value: dict | None = None
_providers_cache_ts: float = 0.0
_providers_lock: asyncio.Lock | None = None  # lazy-init to avoid import-time loop issues


def _get_providers_lock() -> asyncio.Lock:
    global _providers_lock
    if _providers_lock is None:
        _providers_lock = asyncio.Lock()
    return _providers_lock


async def detect_vertex_ai() -> bool:
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if creds_path and Path(creds_path).is_file():
        return True
    adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if adc.exists():
        return True
    try:
        r = await asyncio.to_thread(
            subprocess.run,
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True,
            timeout=3,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass
    return False


async def _resolve_gcloud_default_project() -> str | None:
    try:
        r = await asyncio.to_thread(
            subprocess.run,
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            timeout=3,
            text=True,
        )
        if r.returncode == 0:
            val = r.stdout.strip()
            return val if val else None
    except Exception:
        pass
    return None


def _extract_hosted_domain(creds) -> str | None:
    try:
        id_token = getattr(creds, "id_token", None)
        if isinstance(id_token, dict):
            return id_token.get("hd")
    except Exception:
        pass
    return None


def _extract_user_email(creds) -> str | None:
    try:
        id_token = getattr(creds, "id_token", None)
        if isinstance(id_token, dict):
            return id_token.get("email")
    except Exception:
        pass
    return None


def _is_reauth_error(exc: Exception) -> bool:
    """True when exc indicates valid-but-expired/invalid ADC credentials."""
    try:
        import google.auth.exceptions
        return isinstance(exc, (google.auth.exceptions.RefreshError, google.auth.exceptions.TransportError))
    except Exception:
        return False


async def detect_vertex_gemini() -> dict:
    """Return {available, project, location, identity_email, hosted_domain, vertex_ai_needs_reauth}.

    vertex_ai_needs_reauth is True when ADC exists but is expired or invalid,
    so the UI can distinguish 'not set up' from 'needs re-authentication'.
    Plain availability is NOT gated on hosted_domain or any vendor-specific field.
    """
    if not await detect_vertex_ai():
        return {"available": False, "vertex_ai_needs_reauth": False}
    try:
        import google.auth
        import google.auth.exceptions  # noqa: F401 — imported so except clauses can reference it

        # google.auth.default() does synchronous file I/O (reads ADC json, keyfile,
        # or calls gcloud) and can block for hundreds of milliseconds on cold caches.
        # Offload it to a thread so the event loop stays responsive during concurrent
        # page-load requests.  →1738
        creds, project = await asyncio.to_thread(
            google.auth.default,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        if not project:
            project = await _resolve_gcloud_default_project()
        hosted_domain = _extract_hosted_domain(creds)
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        return {
            "available": bool(project),
            "project": project,
            "location": location,
            "identity_email": getattr(creds, "service_account_email", None)
            or _extract_user_email(creds),
            "hosted_domain": hosted_domain,
            "vertex_ai_needs_reauth": False,
        }
    except ImportError:
        return {"available": False, "vertex_ai_needs_reauth": False}
    except Exception as exc:
        return {"available": False, "vertex_ai_needs_reauth": _is_reauth_error(exc)}


async def detect_bedrock() -> bool:
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return True
    profile = os.environ.get("AWS_PROFILE", "")
    if profile:
        creds_file = Path.home() / ".aws" / "credentials"
        if creds_file.exists() and f"[{profile}]" in creds_file.read_text():
            return True
    try:
        r = await asyncio.to_thread(
            subprocess.run,
            ["aws", "sts", "get-caller-identity"],
            capture_output=True,
            timeout=3,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass
    return False


async def _run_full_detection() -> dict[str, bool]:
    """Run a full provider scan (no cache). Called only by detect_providers()."""
    from services.ostk_secrets import get_anthropic_key, get_gemini_key

    claude_code = await is_claude_code_available()
    gemini_cli = await is_gemini_cli_available()

    anthropic_key = bool(await get_anthropic_key())
    gemini_key = bool(await get_gemini_key())

    vx = await detect_vertex_gemini()
    return {
        "claude_code": claude_code,
        "gemini_cli": gemini_cli,
        "anthropic_key": anthropic_key,
        "gemini_key": gemini_key,
        "vertex_ai": vx.get("available", False),
        "vertex_ai_project": vx.get("project"),
        "vertex_ai_needs_reauth": vx.get("vertex_ai_needs_reauth", False),
        "bedrock": await detect_bedrock(),
    }


def _reset_provider_cache() -> None:
    """Reset the detect_providers() TTL cache.

    Intended for use in tests only — forces the next detect_providers() call to
    run a full detection scan rather than returning a stale cached result.
    """
    global _providers_cache_value, _providers_cache_ts
    _providers_cache_value = None
    _providers_cache_ts = 0.0


async def detect_providers() -> dict[str, bool]:
    """Return availability of known AI providers.

    Checks Claude Code subscription login, ANTHROPIC_API_KEY, GEMINI_API_KEY,
    Google Vertex AI ADC, and AWS Bedrock credentials. No secrets are returned.

    Results are cached for _PROVIDERS_CACHE_TTL seconds and protected by a
    single-flight lock so N concurrent page-load requests trigger at most one
    full detection run.  →1738
    """
    global _providers_cache_value, _providers_cache_ts

    now = time.monotonic()
    if _providers_cache_value is not None and now < _providers_cache_ts + _PROVIDERS_CACHE_TTL:
        return _providers_cache_value

    lock = _get_providers_lock()
    async with lock:
        # Re-check inside the lock — another waiter may have populated the cache.
        now = time.monotonic()
        if _providers_cache_value is not None and now < _providers_cache_ts + _PROVIDERS_CACHE_TTL:
            return _providers_cache_value

        result = await _run_full_detection()
        _providers_cache_value = result
        _providers_cache_ts = time.monotonic()
        return result
