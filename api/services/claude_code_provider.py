"""Provider that routes chat through the local ``claude`` command.

Tori's chat panel used to call the Anthropic API with an API key, which
charged pay-as-you-go tokens on top of her Claude subscription. When the
local ``claude`` program is installed and signed in, we shell out to it
instead so the chat bills against her existing subscription quota rather
than extra dollars.

Design notes:

- Detection runs ``claude auth status`` once and caches the answer for
  sixty seconds so we do not shell out on every chat turn.
- Spawning ``claude`` strips the ``ANTHROPIC_API_KEY`` and friends from
  the child environment. If we leave those env vars in place the local
  program silently falls back to API key billing, which is the exact
  thing we are trying to stop.
- The streaming JSON format is ``--output-format stream-json --verbose``.
  Each line is one JSON event. We forward text deltas to the websocket
  as ``token`` events and send a ``done`` event at the end.
- The existing Anthropic API path stays in place as a fallback for when
  the local program is not installed or signed in.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from typing import Any, Optional

from fastapi import WebSocket


# Anthropic env vars that force API-key auth when present.
# These MUST be stripped from the subprocess environment before spawning
# ``claude``. Leaving them in place makes the local program bypass the
# subscription and bill against the paid API instead, which defeats the
# entire purpose of this provider.
BLOCKED_AUTH_ENV_KEYS: frozenset[str] = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_API_KEY",
})


# Subscription tiers that map to the subscription quota rather than pay-
# as-you-go API billing. Team and enterprise are included for future use.
ALLOWED_SUBSCRIPTION_TYPES: frozenset[str] = frozenset({
    "pro",
    "max",
    "team",
    "enterprise",
})


# How long to trust a cached detection result.
_DETECTION_CACHE_TTL_SECONDS: float = 60.0

# How long to wait on ``claude auth status`` before declaring it broken.
_AUTH_STATUS_TIMEOUT_SECONDS: float = 3.0

# Cap on how long a single chat turn may run before we kill the subprocess.
_STREAM_TIMEOUT_SECONDS: float = 120.0


# Module-level cache for detection results. Not perfect across processes
# but good enough: the detection command itself is cheap and the cache
# just prevents us from running it on every chat turn.
_detection_cache: dict[str, Any] = {"result": None, "expires_at": 0.0}


def _strip_blocked_env(source: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``source`` with Anthropic auth vars removed.

    This is the critical safety step. The research report found that
    ``ANTHROPIC_API_KEY`` in the parent environment silently beats the
    subscription auth in the child process, so we must remove it before
    spawning ``claude``.
    """
    return {k: v for k, v in source.items() if k not in BLOCKED_AUTH_ENV_KEYS}


def _build_subprocess_env() -> dict[str, str]:
    """Return a clean environment dict for spawning ``claude``.

    We copy the parent env and drop any Anthropic auth variables so the
    subscription path wins inside the child process.
    """
    import os
    return _strip_blocked_env(dict(os.environ))


def clear_detection_cache() -> None:
    """Wipe the cached detection result. Used by tests."""
    _detection_cache["result"] = None
    _detection_cache["expires_at"] = 0.0


def _find_claude_binary() -> Optional[str]:
    """Return the absolute path to the ``claude`` program or None."""
    return shutil.which("claude")


async def _run_auth_status(claude_path: str) -> Optional[dict]:
    """Run ``claude auth status`` and parse its JSON output.

    Returns the parsed dict on success or None on any failure. The
    command is considered broken on non-zero exit, timeout, or unparseable
    output.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_path,
            "auth",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_subprocess_env(),
        )
    except (OSError, FileNotFoundError):
        return None

    try:
        stdout, _stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_AUTH_STATUS_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return None

    if proc.returncode != 0:
        return None

    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def is_claude_code_available(force: bool = False) -> bool:
    """Return True when the local ``claude`` program is signed in with a subscription.

    Result is cached for ``_DETECTION_CACHE_TTL_SECONDS`` to avoid running
    a shell command on every chat turn. Pass ``force=True`` to bypass the
    cache (the Settings page uses this for the live status indicator).
    """
    now = time.monotonic()
    if not force and _detection_cache["result"] is not None and now < _detection_cache["expires_at"]:
        return bool(_detection_cache["result"])

    try:
        claude_path = _find_claude_binary()
        if not claude_path:
            result = False
        else:
            status = await _run_auth_status(claude_path)
            if not status:
                result = False
            else:
                api_provider = str(status.get("apiProvider", "")).lower()
                subscription = str(status.get("subscriptionType", "")).lower()
                logged_in = bool(status.get("loggedIn", False))
                result = (
                    logged_in
                    and api_provider == "firstparty"
                    and subscription in ALLOWED_SUBSCRIPTION_TYPES
                )
    except Exception:
        # Never let detection throw. A bad detection just means we fall
        # back to the Anthropic API provider.
        result = False

    _detection_cache["result"] = result
    _detection_cache["expires_at"] = now + _DETECTION_CACHE_TTL_SECONDS
    return result


def _messages_to_prompt(messages: list[dict], system_prompt: Optional[str]) -> str:
    """Flatten the chat history into a single string for ``claude -p``.

    ``claude -p`` expects a single prompt string. We turn the message
    history into a readable transcript so the model has the full
    conversation context, and prepend the system prompt when the caller
    provided one.
    """
    lines: list[str] = []
    if system_prompt:
        lines.append(system_prompt.strip())
        lines.append("")

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if isinstance(content, list):
            # Image blocks and tool blocks both land here. Pull plain text
            # pieces out so at least the text parts reach the model.
            text_parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(str(block.get("text", "")))
                    elif block.get("type") == "image":
                        text_parts.append("[image]")
            content_text = "\n".join(t for t in text_parts if t)
        else:
            content_text = str(content or "")

        if not content_text.strip():
            continue

        if role == "user":
            lines.append(f"User: {content_text}")
        elif role == "assistant":
            lines.append(f"Assistant: {content_text}")
        else:
            lines.append(content_text)

    # A trailing "Assistant:" hint coaxes the model into responding as
    # the assistant rather than continuing the user turn.
    lines.append("Assistant:")
    return "\n\n".join(lines)


async def _send_safe(websocket: WebSocket, payload: dict) -> None:
    """Send a JSON message, swallowing transport hiccups."""
    try:
        await websocket.send_json(payload)
    except Exception:
        pass


def _handle_stream_event(event: dict) -> tuple[Optional[str], bool, Optional[dict]]:
    """Extract a text fragment, done flag, and usage dict from a stream event.

    Returns ``(text, done, usage)``:
      - ``text``: a fragment to forward to the websocket as a token, or None
      - ``done``: True when this event signals the end of the response
      - ``usage``: token usage dict from the final ``result`` event, or None
    """
    etype = event.get("type")

    if etype == "assistant":
        # The main body of a response arrives as an assistant message with
        # content blocks. Pull any text blocks out.
        message = event.get("message") or {}
        blocks = message.get("content") or []
        parts: list[str] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        text = "".join(parts)
        return (text or None), False, None

    if etype == "result":
        usage_raw = event.get("usage") or {}
        usage = {
            "input_tokens": int(usage_raw.get("input_tokens", 0) or 0),
            "output_tokens": int(usage_raw.get("output_tokens", 0) or 0),
        }
        return None, True, usage

    # system/init, rate_limit_event, tool_use, partial deltas, and other
    # events are not forwarded here. Keeping this simple avoids having to
    # track every event type the local program might emit.
    return None, False, None


async def stream_chat(
    messages: list[dict],
    websocket: WebSocket,
    system_prompt: Optional[str] = None,
) -> str:
    """Stream a chat response from the local ``claude`` program.

    This is the main entry point used by ``chat_providers.ChatService``
    when the local program is available. It spawns the program with a
    clean environment (no leaked API key), reads the stream-json output
    line by line, forwards text fragments to the websocket, and returns
    the full assembled text.
    """
    claude_path = _find_claude_binary()
    if not claude_path:
        await _send_safe(
            websocket,
            {
                "type": "error",
                "data": "Your Claude subscription is not set up on this machine yet.",
            },
        )
        return ""

    prompt = _messages_to_prompt(messages, system_prompt)

    args = [
        claude_path,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "sonnet",
    ]
    env = _build_subprocess_env()

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except (OSError, FileNotFoundError):
        await _send_safe(
            websocket,
            {
                "type": "error",
                "data": "Your Claude subscription is not responding right now.",
            },
        )
        return ""

    full_text = ""
    final_usage: Optional[dict] = None

    async def _read_stdout() -> None:
        nonlocal full_text, final_usage
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                event = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue

            text, done, usage = _handle_stream_event(event)
            if text:
                full_text += text
                await _send_safe(websocket, {"type": "token", "data": text})
            if done:
                if usage:
                    final_usage = usage

    try:
        await asyncio.wait_for(_read_stdout(), timeout=_STREAM_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await _send_safe(
            websocket,
            {
                "type": "error",
                "data": "Your Claude subscription took too long to respond. Try again.",
            },
        )
        return full_text

    return_code = await proc.wait()

    # Drain stderr so we can surface a friendly error on failure. We do
    # this even on success because the program sometimes writes warnings
    # to stderr and we want to log them.
    stderr_bytes = b""
    if proc.stderr is not None:
        try:
            stderr_bytes = await proc.stderr.read()
        except Exception:
            stderr_bytes = b""

    if return_code != 0:
        # Log the stderr for debugging but do not leak it to the user.
        if stderr_bytes:
            try:
                print(
                    "[claude_code_provider] stderr:",
                    stderr_bytes.decode("utf-8", errors="replace"),
                )
            except Exception:
                pass
        await _send_safe(
            websocket,
            {
                "type": "error",
                "data": "Your Claude subscription is not responding right now.",
            },
        )
        return full_text

    await _send_safe(
        websocket,
        {
            "type": "done",
            "usage": final_usage or {"input_tokens": 0, "output_tokens": 0},
        },
    )
    return full_text
