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

_ostk_trust_cache_value: dict | None = None
_ostk_trust_cache_ts: float = 0.0

_OSTK_SUBPROCESS_TIMEOUT: float = 5.0
_OSTK_ROSTER_CACHE_TTL: float = 5.0
_ostk_roster_cache_value: dict | None = None
_ostk_roster_cache_ts: float = 0.0
_ostk_roster_cache_cwd: str | None = None

_VERTEX_GEMINI_TIMEOUT: float = 3.5


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
        return {"available": False, "vertex_ai_needs_reauth": False, "vertex_ai_signed_in": False}
    try:
        import google.auth
        import google.auth.exceptions  # noqa: F401 — imported so except clauses can reference it

        # google.auth.default() does synchronous file I/O (reads ADC json, keyfile,
        # or calls gcloud) and can block for hundreds of milliseconds on cold caches.
        # Offload it to a thread so the event loop stays responsive during concurrent
        # page-load requests.  →1738
        # Bounded by _VERTEX_GEMINI_TIMEOUT to prevent a cold-start hang from
        # blocking the onboarding wizard past Vite's 5 s proxy budget.
        creds, project = await asyncio.wait_for(
            asyncio.to_thread(
                google.auth.default,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            ),
            timeout=_VERTEX_GEMINI_TIMEOUT,
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
            "vertex_ai_signed_in": True,
        }
    except asyncio.TimeoutError:
        return {"available": False, "vertex_ai_needs_reauth": False, "vertex_ai_signed_in": False}
    except ImportError:
        return {"available": False, "vertex_ai_needs_reauth": False, "vertex_ai_signed_in": False}
    except Exception as exc:
        return {"available": False, "vertex_ai_needs_reauth": _is_reauth_error(exc), "vertex_ai_signed_in": False}


async def classify_vertex_state(project: str) -> str:
    """Return "ok" | "api-disabled" | "adc-missing" based on project state.

    Uses gcloud to probe if aiplatform.googleapis.com is enabled.
    Conservative: returns "api-disabled" on gcloud probe failure.
    """
    if not await detect_vertex_ai():
        return "adc-missing"

    try:
        r = await asyncio.to_thread(
            subprocess.run,
            [
                "gcloud",
                "services",
                "list",
                "--enabled",
                f"--project={project}",
                "--filter=config.name:aiplatform.googleapis.com",
                "--format=value(config.name)",
            ],
            capture_output=True,
            timeout=5,
            text=True,
        )
        if r.returncode == 0 and "aiplatform.googleapis.com" in r.stdout:
            return "ok"
    except Exception:
        pass

    return "api-disabled"


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


async def detect_ostk_trust(force: bool = False, cwd: Path | None = None) -> dict:
    """Check ostk CLI availability and HUMANFILE signature trust posture.

    Runs ``ostk boot`` in a worker thread with a bounded timeout so the event
    loop is never blocked. Surfaces ``humanfile_valid=False`` and an actionable
    recovery command whenever HUMANFILE signature verification fails or warns
    of unsigned local edits that block secret injection.
    """
    global _ostk_trust_cache_value, _ostk_trust_cache_ts
    import shutil

    now = time.monotonic()
    if (
        not force
        and cwd is None
        and _ostk_trust_cache_value is not None
        and now < _ostk_trust_cache_ts + _PROVIDERS_CACHE_TTL
    ):
        return dict(_ostk_trust_cache_value)

    ostk_bin = shutil.which("ostk")
    if not ostk_bin:
        res = {
            "ostk_available": False,
            "humanfile_valid": False,
            "humanfile_warning": "ostk CLI not found on PATH.",
        }
        if cwd is None:
            _ostk_trust_cache_value = res
            _ostk_trust_cache_ts = now
        return dict(res)

    run_kwargs: dict = {
        "capture_output": True,
        "timeout": 3,
        "text": True,
    }
    if cwd is not None:
        run_kwargs["cwd"] = str(cwd)

    try:
        r = await asyncio.to_thread(
            subprocess.run,
            [ostk_bin, "boot"],
            **run_kwargs,
        )
        combined = f"{r.stdout or ''}\n{r.stderr or ''}"
        lowered = combined.lower()
        if "signature invalid" in lowered or "refusing secret injection" in lowered:
            res = {
                "ostk_available": True,
                "humanfile_valid": False,
                "humanfile_warning": (
                    "HUMANFILE signature is invalid, so ostk refuses secret injection. "
                    "Run `ostk trust sign --humanfile` (and `ostk trust sign ~/.HUMANFILE` if edited) to re-sign."
                ),
            }
        elif "modified since it was signed" in lowered or "subject hash mismatch" in lowered:
            res = {
                "ostk_available": True,
                "humanfile_valid": False,
                "humanfile_warning": (
                    "HUMANFILE was modified since it was signed. "
                    "Run `ostk trust sign ~/.HUMANFILE` (or `ostk trust sign --humanfile`) so secret injection stays trusted."
                ),
            }
        elif r.returncode != 0:
            res = {
                "ostk_available": True,
                "humanfile_valid": False,
                "humanfile_warning": "ostk boot reported an error. Run `ostk boot` in your terminal to inspect.",
            }
        else:
            res = {
                "ostk_available": True,
                "humanfile_valid": True,
                "humanfile_warning": None,
            }
    except Exception:
        res = {
            "ostk_available": False,
            "humanfile_valid": False,
            "humanfile_warning": "Could not verify ostk boot health.",
        }

    if cwd is None:
        _ostk_trust_cache_value = res
        _ostk_trust_cache_ts = time.monotonic()
    return dict(res)


detect_ostk_trust_async = detect_ostk_trust


_INTERACTIVE_SIGN_MARKERS = (
    "passphrase",
    "/dev/tty",
    "no tty",
    "not a tty",
    "inappropriate ioctl",
    "ioctl for device",
    "pinentry",
    "ssh-agent",
    "ssh_auth_sock",
    "agent refused operation",
    "couldn't connect to agent",
    "could not open a connection to your authentication agent",
    "communication with agent failed",
    "interactive",
)


def _reset_ostk_trust_cache() -> None:
    """Invalidate cached ostk trust and provider detection snapshots."""
    global _ostk_trust_cache_value, _ostk_trust_cache_ts
    global _providers_cache_value, _providers_cache_ts
    _ostk_trust_cache_value = None
    _ostk_trust_cache_ts = 0.0
    _providers_cache_value = None
    _providers_cache_ts = 0.0


def _reset_ostk_roster_cache() -> None:
    """Invalidate cached ostk roster snapshot."""
    global _ostk_roster_cache_value, _ostk_roster_cache_ts, _ostk_roster_cache_cwd
    _ostk_roster_cache_value = None
    _ostk_roster_cache_ts = 0.0
    _ostk_roster_cache_cwd = None


async def sign_ostk_humanfile(cwd: Path | None = None) -> dict:
    """Non-interactively run `ostk trust sign --humanfile` and re-verify trust.

    Uses a worker thread with stdin=DEVNULL and a 5s timeout so passphrase-protected
    keys never hang the server or block the asyncio event loop.
    """
    from services.ostk import get_effective_root

    effective_cwd = str(cwd or get_effective_root())
    manual_command = "ostk trust sign --humanfile"
    rc = -1
    combined_output = ""
    requires_tty = False
    missing_binary = False
    timed_out = False

    try:
        r = await asyncio.wait_for(
            asyncio.to_thread(
                subprocess.run,
                ["ostk", "trust", "sign", "--humanfile"],
                cwd=effective_cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_OSTK_SUBPROCESS_TIMEOUT,
                text=True,
            ),
            timeout=_OSTK_SUBPROCESS_TIMEOUT,
        )
        rc = r.returncode
        combined_output = f"{r.stdout or ''}\n{r.stderr or ''}".strip()
        lowered = combined_output.lower()
        if rc != 0 and any(marker in lowered for marker in _INTERACTIVE_SIGN_MARKERS):
            requires_tty = True
    except FileNotFoundError:
        missing_binary = True
    except (asyncio.TimeoutError, subprocess.TimeoutExpired):
        timed_out = True
        requires_tty = True
    except Exception as exc:
        combined_output = str(exc).strip()
        if any(marker in combined_output.lower() for marker in _INTERACTIVE_SIGN_MARKERS):
            requires_tty = True

    _reset_ostk_trust_cache()
    trust = (
        await detect_ostk_trust(force=True, cwd=cwd)
        if cwd is not None
        else await detect_ostk_trust(force=True)
    )
    humanfile_valid = bool(trust.get("humanfile_valid", False))
    signed = bool(rc == 0 and humanfile_valid)
    if signed:
        requires_tty = False
        message = "HUMANFILE signed and verified."
    elif missing_binary:
        message = (
            "ostk CLI was not found on PATH. Install ostk and run "
            f"`{manual_command}` in your terminal."
        )
    elif timed_out:
        message = (
            "Signing timed out waiting for non-interactive key access (your SSH or GPG key "
            f"likely requires a passphrase prompt). Run `{manual_command}` in your terminal."
        )
    elif requires_tty:
        message = (
            "Your signing key requires an interactive passphrase or unlocked SSH/GPG agent. "
            f"Run `{manual_command}` in your terminal to enter your passphrase."
        )
    elif rc == 0 and not humanfile_valid:
        message = (
            trust.get("humanfile_warning")
            or f"Signed repo HUMANFILE, but verification still needs attention. Run `{manual_command}` in your terminal."
        )
    else:
        first_line = next((ln.strip() for ln in combined_output.splitlines() if ln.strip()), "")
        if first_line:
            message = f"Could not sign HUMANFILE automatically ({first_line}). Run `{manual_command}` in your terminal."
        else:
            message = f"Could not sign HUMANFILE automatically. Run `{manual_command}` in your terminal."

    return {
        "signed": signed,
        "humanfile_valid": humanfile_valid,
        "trust": trust,
        "message": message,
        "manual_command": manual_command,
        "requires_tty": requires_tty,
    }


def _empty_roster(available: bool) -> dict:
    return {
        "available": available,
        "seats": [],
        "counts": {"total": 0, "valid": 0, "impostor": 0, "unverifiable": 0},
    }


def _clone_roster(data: dict) -> dict:
    return {
        "available": bool(data.get("available", False)),
        "seats": [dict(s) for s in data.get("seats", [])],
        "counts": dict(data.get("counts", {"total": 0, "valid": 0, "impostor": 0, "unverifiable": 0})),
    }


def _looks_like_roster_table(raw_text: str) -> bool:
    for raw_line in (raw_text or "").splitlines():
        parts = raw_line.strip().split()
        if len(parts) >= 3 and parts[0].lower() == "seat" and parts[1].lower() == "pid":
            return True
    return False


def parse_ostk_roster(raw_text: str) -> dict:
    """Parse `ostk roster` tabular output into structured seat verdicts and counts."""
    seats: list[dict] = []
    for raw_line in (raw_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if set(line) <= {"-", "=", " "}:
            continue
        parts = line.split(None, 11)
        if len(parts) >= 3 and parts[0].lower() == "seat" and parts[1].lower() == "pid":
            continue
        if len(parts) < 12:
            continue

        (
            seat_name,
            pid_raw,
            health,
            age,
            session_raw,
            role_raw,
            serving_raw,
            lineage,
            wake,
            ctx_pct,
            attested,
            verdict_raw,
        ) = parts

        pid_val = int(pid_raw) if pid_raw.isdigit() else None
        verdict = verdict_raw.strip()
        verdict_upper = verdict.upper()
        if verdict_upper.startswith("IMPOSTOR"):
            verdict_category = "impostor"
        elif verdict_upper.startswith("VALID"):
            verdict_category = "valid"
        else:
            verdict_category = "unverifiable"

        seats.append(
            {
                "seat": seat_name,
                "pid": pid_val,
                "health": health,
                "age": age,
                "session": None if session_raw == "-" else session_raw,
                "role": None if role_raw == "-" else role_raw,
                "serving": None if serving_raw == "-" else serving_raw,
                "lineage": lineage,
                "wake": wake,
                "ctx_pct": ctx_pct,
                "attested": attested,
                "verdict": verdict,
                "verdict_category": verdict_category,
            }
        )

    counts = {
        "total": len(seats),
        "valid": sum(1 for s in seats if s["verdict_category"] == "valid"),
        "impostor": sum(1 for s in seats if s["verdict_category"] == "impostor"),
        "unverifiable": sum(1 for s in seats if s["verdict_category"] == "unverifiable"),
    }
    return {
        "available": True,
        "seats": seats,
        "counts": counts,
    }


async def get_ostk_roster(cwd: Path | None = None, *, force: bool = False) -> dict:
    """Run `ostk roster` off the event loop with a 5s timeout and 5s TTL cache."""
    global _ostk_roster_cache_value, _ostk_roster_cache_ts, _ostk_roster_cache_cwd
    from services.ostk import get_effective_root

    effective_cwd = str(cwd or get_effective_root())
    now = time.monotonic()
    if (
        not force
        and _ostk_roster_cache_value is not None
        and _ostk_roster_cache_cwd == effective_cwd
        and now < _ostk_roster_cache_ts + _OSTK_ROSTER_CACHE_TTL
    ):
        return _clone_roster(_ostk_roster_cache_value)

    try:
        r = await asyncio.wait_for(
            asyncio.to_thread(
                subprocess.run,
                ["ostk", "roster"],
                cwd=effective_cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_OSTK_SUBPROCESS_TIMEOUT,
                text=True,
            ),
            timeout=_OSTK_SUBPROCESS_TIMEOUT,
        )
        stdout = r.stdout or ""
        if r.returncode == 0 or _looks_like_roster_table(stdout):
            parsed = parse_ostk_roster(stdout)
        else:
            parsed = _empty_roster(available=False)
    except Exception:
        parsed = _empty_roster(available=False)

    _ostk_roster_cache_value = parsed
    _ostk_roster_cache_ts = time.monotonic()
    _ostk_roster_cache_cwd = effective_cwd
    return _clone_roster(parsed)


async def _run_full_detection() -> dict:
    """Run a full provider scan (no cache). Called only by detect_providers()."""
    from services.ostk_secrets import get_anthropic_key, get_gemini_key

    (
        claude_code,
        gemini_cli,
        anthropic_key_val,
        gemini_key_val,
        vx,
        bedrock,
        ostk_trust,
    ) = await asyncio.gather(
        is_claude_code_available(),
        is_gemini_cli_available(),
        get_anthropic_key(),
        get_gemini_key(),
        detect_vertex_gemini(),
        detect_bedrock(),
        detect_ostk_trust(),
    )
    return {
        "claude_code": claude_code,
        "gemini_cli": gemini_cli,
        "anthropic_key": bool(anthropic_key_val),
        "gemini_key": bool(gemini_key_val),
        "vertex_ai": vx.get("available", False),
        "vertex_ai_project": vx.get("project"),
        "vertex_ai_needs_reauth": vx.get("vertex_ai_needs_reauth", False),
        "vertex_ai_signed_in": vx.get("vertex_ai_signed_in", False),
        "bedrock": bedrock,
        "ostk_available": ostk_trust.get("ostk_available", False),
        "humanfile_valid": ostk_trust.get("humanfile_valid", False),
        "humanfile_warning": ostk_trust.get("humanfile_warning"),
    }


def _reset_provider_cache() -> None:
    """Reset the detect_providers() TTL cache.

    Intended for use in tests only — forces the next detect_providers() call to
    run a full detection scan rather than returning a stale cached result.
    """
    global _providers_cache_value, _providers_cache_ts, _providers_lock
    global _ostk_trust_cache_value, _ostk_trust_cache_ts
    _providers_cache_value = None
    _providers_cache_ts = 0.0
    _providers_lock = None
    _ostk_trust_cache_value = None
    _ostk_trust_cache_ts = 0.0
    _reset_ostk_roster_cache()


async def _refresh_provider_cache() -> None:
    """Background cache refresh — only called when a stale value already exists."""
    global _providers_cache_value, _providers_cache_ts
    lock = _get_providers_lock()
    async with lock:
        # Another refresh may have beaten us to the lock.
        if time.monotonic() < _providers_cache_ts + _PROVIDERS_CACHE_TTL:
            return
        result = await _run_full_detection()
        _providers_cache_value = result
        _providers_cache_ts = time.monotonic()


async def warm_provider_cache() -> None:
    """Populate the provider cache at startup. Swallows all errors."""
    try:
        await detect_providers()
    except Exception:
        pass


async def detect_providers() -> dict[str, bool]:
    """Return availability of known AI providers.

    Checks Claude Code subscription login, ANTHROPIC_API_KEY, GEMINI_API_KEY,
    Google Vertex AI ADC, and AWS Bedrock credentials. No secrets are returned.

    Results are cached for _PROVIDERS_CACHE_TTL seconds. Stale-while-revalidate:
    when the cache is expired but not empty, the stale value is returned instantly
    while a background task refreshes it. Only the very first call (empty cache)
    blocks. This prevents the 5s Vite proxy timeout from firing on cold caches.
    Protected by a single-flight lock so at most one refresh runs at a time.  →1738
    """
    global _providers_cache_value, _providers_cache_ts

    now = time.monotonic()

    # Fresh cache: return immediately.
    if _providers_cache_value is not None and now < _providers_cache_ts + _PROVIDERS_CACHE_TTL:
        return _providers_cache_value

    # Stale cache (exists but TTL expired): return immediately, refresh in background.
    if _providers_cache_value is not None:
        lock = _get_providers_lock()
        if not lock.locked():
            asyncio.create_task(_refresh_provider_cache())
        return _providers_cache_value

    # No cache at all: first-ever call, must block and populate.
    lock = _get_providers_lock()
    async with lock:
        # Re-check — another waiter may have populated the cache while we waited.
        if _providers_cache_value is not None:
            return _providers_cache_value
        result = await _run_full_detection()
        _providers_cache_value = result
        _providers_cache_ts = time.monotonic()
        return result
