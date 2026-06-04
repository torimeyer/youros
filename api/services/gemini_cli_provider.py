"""Provider that routes chat through the local `gemini` command.

Modeled after claude_code_provider.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import WebSocket

_REPO_ROOT: Path = Path(__file__).parent.parent.parent
_gemini_log = logging.getLogger("myos.chat.gemini_cli")

# Env vars that might force API key usage or interfere with CLI auth.
# For gemini-cli, we typically want GOOGLE_API_KEY or GEMINI_API_KEY
# to be present if we want to use the API, but for the CLI we might
# want to ensure it uses the user's gcloud auth.
# However, the user mentioned "fallback plan for users who arent paying".
# The CLI itself often requires a subscription (Google One AI Ultra).
BLOCKED_AUTH_ENV_KEYS: frozenset[str] = frozenset({
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
})

_DETECTION_CACHE_TTL_SECONDS: float = 600.0
_AUTH_STATUS_TIMEOUT_SECONDS: float = 3.0   # was 20 s — gemini ping only needs a quick check →1738
_STREAM_TIMEOUT_SECONDS: float = 1800.0
_WS_HEARTBEAT_INTERVAL_S: float = 10.0

_detection_cache: dict[str, Any] = {"result": None, "expires_at": 0.0}
# Single-flight lock: prevents N concurrent callers from each spawning their own
# `gemini -p ping` subprocess when the cache is cold.  →1738
_detection_lock: asyncio.Lock | None = None


def _get_detection_lock() -> asyncio.Lock:
    global _detection_lock
    if _detection_lock is None:
        _detection_lock = asyncio.Lock()
    return _detection_lock

def _gemini_install_dirs() -> list[Path]:
    """Common locations where the gemini CLI (and node) may live but which a
    non-interactive backend's PATH might not include.

    The dev backend is launched by scripts/dev-backend.sh, not the user's
    interactive shell (Warp/zsh), so its PATH is narrower. When `gemini` is
    installed via npm global / Homebrew / ~/.local, shutil.which("gemini")
    returns None here even though the user is signed in and can run it in their
    terminal. (UAT item 4)
    """
    import os
    home = Path.home()
    dirs: list[Path] = []
    override = os.environ.get("GEMINI_CLI_PATH")
    if override:
        p = Path(override)
        dirs.append(p.parent if p.suffix or p.name == "gemini" else p)
    dirs += [
        home / ".npm-global" / "bin",
        home / ".local" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        home / "node_modules" / ".bin",
        Path("/usr/local/lib/node_modules/.bin"),
    ]
    return dirs


def _build_subprocess_env() -> dict[str, str]:
    import os
    env = dict(os.environ)
    # If we want to force CLI-only auth, we'd strip keys here.
    # For now let's keep it clean but allow the keys if they help the CLI.
    # The spec says "spawns the CLI ... strips ... if we leave those env vars
    # in place the local program silently falls back to API key billing".
    for k in BLOCKED_AUTH_ENV_KEYS:
        env.pop(k, None)
    # UAT item 4: prepend the common install dirs so the gemini shebang can find
    # `node` when it runs, even when the backend's own PATH is missing them.
    extra = [str(d) for d in _gemini_install_dirs() if d.is_dir()]
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def _gemini_signed_in() -> bool:
    """True when the gemini CLI has stored OAuth credentials on disk.

    Secondary signal so a slow/timed-out ping does not report a signed-in user
    as logged out (UAT item 4).
    """
    try:
        return (Path.home() / ".gemini" / "oauth_creds.json").is_file()
    except Exception:
        return False


def _find_gemini_binary() -> Optional[str]:
    import os
    # 1. Standard PATH lookup (works when the backend inherited a full PATH).
    found = shutil.which("gemini")
    if found:
        return found
    # 2. UAT item 4: fall back to common install dirs the backend's PATH may miss.
    for d in _gemini_install_dirs():
        cand = d / "gemini"
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        except Exception:
            continue
    return None

async def is_gemini_cli_available(force: bool = False) -> bool:
    now = time.monotonic()
    if not force and _detection_cache["result"] is not None and now < _detection_cache["expires_at"]:
        return bool(_detection_cache["result"])

    # Single-flight: only one subprocess runs at a time; concurrent callers wait
    # for the lock and then return the freshly cached value.  →1738
    async with _get_detection_lock():
        # Re-check after acquiring the lock — a previous waiter may have populated cache.
        now = time.monotonic()
        if not force and _detection_cache["result"] is not None and now < _detection_cache["expires_at"]:
            return bool(_detection_cache["result"])

        try:
            gemini_path = _find_gemini_binary()
            if not gemini_path:
                result = False
            else:
                # Check auth status. gemini-cli v0.42.0 shows "Plan: ..." in help or start.
                # There isn't a direct "auth status" command that returns JSON yet,
                # so we'll check if we can run a simple prompt.
                # Run the probe in a worker thread so the fork+exec of the heavy
                # gemini CLI happens OFF the event-loop thread. Forking on the
                # loop stalled TLS handshakes and wedged the backend during
                # startup and on every cache-miss settings poll (->1806).
                import subprocess

                def _probe() -> tuple[int, str]:
                    try:
                        r = subprocess.run(
                            [gemini_path, "-p", "ping", "--output-format", "text"],
                            capture_output=True,
                            timeout=_AUTH_STATUS_TIMEOUT_SECONDS,
                            env=_build_subprocess_env(),
                        )
                        return r.returncode, r.stderr.decode("utf-8", errors="replace").lower()
                    except subprocess.TimeoutExpired:
                        return -1, "__timeout__"
                    except Exception:
                        return -2, "__error__"

                rc, err_text = await asyncio.to_thread(_probe)
                if err_text == "__timeout__":
                    # UAT item 4: a slow ping must not report a signed-in user as
                    # logged out. If the CLI's OAuth creds are on disk, trust them.
                    if _gemini_signed_in():
                        _gemini_log.info(
                            "Gemini CLI ping timed out but OAuth creds present; treating as available."
                        )
                        result = True
                    else:
                        _gemini_log.warning("Gemini CLI availability check timed out.")
                        result = False
                elif rc < 0:
                    result = False
                else:
                    result = rc == 0
                    if "sign in" in err_text or "not authenticated" in err_text:
                        _gemini_log.info("Gemini CLI found but not authenticated.")
                        result = False
                    elif result:
                        _gemini_log.info("Gemini CLI is available and authenticated.")
                    else:
                        _gemini_log.warning(f"Gemini CLI ping failed with exit code {rc}: {err_text}")
        except Exception:
            result = False

        _detection_cache["result"] = result
        _detection_cache["expires_at"] = time.monotonic() + _DETECTION_CACHE_TTL_SECONDS
        return result

# ---------------------------------------------------------------------------
# RuntimeProvider implementation (→1891, →1892, →2145)
# ---------------------------------------------------------------------------
from services.runtime_provider import Feature, _BaseRuntimeProvider  # noqa: E402

# Feature set based on code inspection: gemini CLI streams tokens (stream-json
# output format) but has no worktrees, plan mode, hooks, isolation, monitor,
# or subagent-spawn support in this codebase.
_GEMINI_CLI_FEATURES = frozenset({Feature.STREAMING})


class GeminiCliRuntimeProvider(_BaseRuntimeProvider):
    """RuntimeProvider for the local gemini CLI. Streaming only."""

    _features = _GEMINI_CLI_FEATURES


# ---------------------------------------------------------------------------

async def stream_chat(
    messages: list[dict],
    websocket: WebSocket,
    system_prompt: Optional[str] = None,
    **kwargs: Any,
) -> str:
    gemini_path = _find_gemini_binary()
    if not gemini_path:
        await websocket.send_json({"type": "error", "data": "Gemini CLI not found."})
        return ""

    # Flatten messages
    prompt_lines = []
    if system_prompt:
        prompt_lines.append(f"System: {system_prompt}")
    
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        prompt_lines.append(f"{role.capitalize()}: {content}")
    
    prompt_lines.append("Assistant:")
    prompt = "\n\n".join(prompt_lines)

    args = [
        gemini_path,
        "-p", prompt,
        "--output-format", "stream-json",
        "--skip-trust",
        "--approval-mode", "auto",  # allow file reads without interactive approval
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_subprocess_env(),
            cwd=str(_REPO_ROOT),
            limit=32 * 1024 * 1024,
        )
    except Exception as e:
        await websocket.send_json({"type": "error", "data": f"Failed to start Gemini CLI: {e}"})
        return ""

    full_text = ""
    async def _read_stdout():
        nonlocal full_text
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                event = json.loads(line.decode("utf-8", errors="replace"))
                # format: {"type": "token", "data": "..."}
                if event.get("type") == "token":
                    token = event.get("data", "")
                    full_text += token
                    await websocket.send_json({"type": "token", "data": token})
                elif event.get("type") == "error":
                    await websocket.send_json({"type": "error", "data": event.get("data")})
            except:
                continue

    try:
        await asyncio.wait_for(_read_stdout(), timeout=_STREAM_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        try: proc.kill()
        except: pass
        await websocket.send_json({"type": "error", "data": "Gemini CLI timed out."})

    await proc.wait()
    await websocket.send_json({"type": "done", "usage": {"input_tokens": 0, "output_tokens": 0}})
    return full_text
