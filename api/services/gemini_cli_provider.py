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

def _build_subprocess_env() -> dict[str, str]:
    import os
    env = dict(os.environ)
    # If we want to force CLI-only auth, we'd strip keys here.
    # For now let's keep it clean but allow the keys if they help the CLI.
    # The spec says "spawns the CLI ... strips ... if we leave those env vars
    # in place the local program silently falls back to API key billing".
    for k in BLOCKED_AUTH_ENV_KEYS:
        env.pop(k, None)
    return env

def _find_gemini_binary() -> Optional[str]:
    # Resolve the gemini CLI from PATH.
    return shutil.which("gemini")

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
                proc = await asyncio.create_subprocess_exec(
                    gemini_path,
                    "-p", "ping",
                    "--output-format", "text",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_build_subprocess_env(),
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=_AUTH_STATUS_TIMEOUT_SECONDS
                    )
                    result = proc.returncode == 0
                    err_text = stderr.decode("utf-8", errors="replace").lower()
                    if "sign in" in err_text or "not authenticated" in err_text:
                        _gemini_log.info("Gemini CLI found but not authenticated.")
                        result = False
                    elif result:
                        _gemini_log.info("Gemini CLI is available and authenticated.")
                    else:
                        _gemini_log.warning(f"Gemini CLI ping failed with exit code {proc.returncode}: {err_text}")
                except asyncio.TimeoutError:
                    _gemini_log.warning("Gemini CLI availability check timed out.")
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    result = False
        except Exception:
            result = False

        _detection_cache["result"] = result
        _detection_cache["expires_at"] = time.monotonic() + _DETECTION_CACHE_TTL_SECONDS
        return result

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
