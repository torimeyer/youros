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
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Optional

# Repo root — two parents up from api/services/claude_code_provider.py.
# Passed as cwd to every claude subprocess so the Claude Code CLI finds
# .claude/settings.json (with PreToolUse hooks) and .mcp.json (with the
# ostk MCP server) at the project root rather than inheriting the
# uvicorn worker's api/ directory where neither file exists directly.
_REPO_ROOT: Path = Path(__file__).parent.parent.parent

from fastapi import WebSocket


_claude_log = logging.getLogger("myos.chat.claude_code")


# Env vars stripped from the subprocess env before running ``claude auth status``
# or spawning ``claude`` for chat.
#
# ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / CLAUDE_API_KEY: force API-key
# billing inside the child process; stripping them makes the subscription path win.
#
# ANTHROPIC_BASE_URL: when the backend runs inside Claude Code the parent sets
# this to a local ostk proxy (127.0.0.1:8080). The Claude CLI routes its
# startup through that URL; if the proxy is busy or slow the ``auth status``
# call can exceed the 3-second timeout and return a false negative.
#
# CLAUDECODE / CLAUDE_CODE_SESSION_ID / CLAUDE_CODE_ENTRYPOINT / AI_AGENT:
# these tell the Claude CLI it is running inside a Claude Code session, causing
# it to attempt IPC with the parent session. That IPC can hang when the parent
# is processing a long request, again pushing the subprocess past the timeout.
BLOCKED_AUTH_ENV_KEYS: frozenset[str] = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_API_KEY",
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "AI_AGENT",
})


# Subscription tiers that map to the subscription quota rather than pay-
# as-you-go API billing. Team and enterprise are included for future use.
ALLOWED_SUBSCRIPTION_TYPES: frozenset[str] = frozenset({
    "pro",
    "max",
    "team",
    "enterprise",
})



# ---------- session persistence ----------
# Maps ToriChat tab IDs to deterministic UUIDs for Claude Code sessions.
# Once a tab has been used, subsequent turns resume the same session so
# the model has full conversation context without us flattening messages.
import uuid as _uuid_mod

_known_sessions: set[str] = set()

# Per-tab response cache — keyed by tab_id, value is the last full response
# text produced by stream_chat for that tab.  Populated on every successful
# (or partial) stream so the frontend can retrieve the response via
# GET /api/chat/last-response?tab_id=<id> if its WebSocket was torn down
# mid-stream (e.g. uvicorn reload, browser navigation). Capped at 100 entries.
_last_response_cache: dict[str, str] = {}


def _session_id_for_tab(tab_id: str) -> str:
    """Deterministic UUID from a tab ID so the same tab always resumes."""
    return str(_uuid_mod.uuid5(_uuid_mod.NAMESPACE_URL, f"torichat:{tab_id}"))


# How long to trust a cached detection result.
# Bumped from 60s to 600s (10 min) so back to back agent spawns during a
# demo reuse the same warm result. ``claude auth status`` costs ~1.5 s per
# call on a cold shell, and the underlying state (signed in or not) only
# changes when the user runs ``claude login``/``logout`` by hand. A 10 min
# window keeps the freshness reasonable while cutting the per-spawn tax.
_DETECTION_CACHE_TTL_SECONDS: float = 600.0

# How long to wait on ``claude auth status`` before declaring it broken.
# 8 s gives room for thread-pool saturation (the coroutine queues behind other
# workers before asyncio.to_thread finally dispatches it) without making the
# Settings page feel unresponsive on a normal laptop.
_AUTH_STATUS_TIMEOUT_SECONDS: float = 8.0

# Cap on how long a single chat turn may run before we kill the subprocess.
# 1800 s (30 min) gives even very complex multi-tool sessions room to finish
# without triggering the "took too long" error. The CLI has no such ceiling;
# this value is a safety net for hung processes, not a workload limit.
_STREAM_TIMEOUT_SECONDS: float = 1800.0

# How often to send a WS keep-alive frame during silent subprocess phases
# (thinking, tool-use planning). The vite dev proxy and browsers treat a
# WebSocket as idle when no frames flow for ~60-120 s and close it, which
# surfaces in the UI as "Connection dropped before the response finished".
# 10 s matches the interval used by the direct Anthropic API path.
_WS_HEARTBEAT_INTERVAL_S: float = 10.0


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


def has_cached_auth_status() -> bool:
    """Return True when a non-stale cached detection result exists.

    Callers that want to warm the cache (e.g. ``prewarm_fleet``) can use
    this to skip a redundant shell-out when a very recent probe already
    answered the question. This keeps demo-critical spawns fast even when
    the user hits Prewarm multiple times in a row.
    """
    now = time.monotonic()
    return (
        _detection_cache["result"] is not None
        and now < _detection_cache["expires_at"]
    )


def _find_claude_binary() -> Optional[str]:
    """Return the absolute path to the ``claude`` program or None."""
    return shutil.which("claude")


async def _run_auth_status(claude_path: str) -> Optional[dict]:
    """Run ``claude auth status`` and parse its JSON output.

    Returns the parsed dict on success or None on any failure. The
    command is considered broken on non-zero exit, timeout, or unparseable
    output.
    """
    # Run `claude auth status` in a worker thread so the fork+exec of this
    # large backend process happens OFF the event-loop thread. Forking on the
    # loop holds process-wide locks that stall TLS handshakes and wedge the
    # backend during startup and on cache-miss settings polls (->1806).
    import subprocess

    def _probe() -> Optional[bytes]:
        try:
            r = subprocess.run(
                [claude_path, "auth", "status"],
                capture_output=True,
                timeout=_AUTH_STATUS_TIMEOUT_SECONDS,
                env=_build_subprocess_env(),
            )
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            return None
        if r.returncode != 0:
            return None
        return r.stdout

    stdout = await asyncio.to_thread(_probe)
    if stdout is None:
        return None

    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def prewarm_cli() -> None:
    """Fire up the local ``claude`` program once at backend boot so the first
    user chat does not pay the cold-start penalty.

    On a cold shell the first ``claude -p`` invocation takes 7 to 9 seconds
    before the first token arrives. Subsequent invocations in the same
    process tree are faster (measured 3.4 to 5.1 seconds) because the
    Node.js runtime, CLI plugin loader, and network stack are already warm
    in the OS file cache and DNS/TLS resolver cache. Running a tiny prompt
    here at startup pays that cost while the backend boots, so the user
    never sees it.

    This is best-effort. If the program is not installed, not signed in,
    or the subprocess errors out, we silently give up. The regular chat
    path still works on cold start, just a few seconds slower.
    """
    try:
        if not await is_claude_code_available():
            return
        claude_path = _find_claude_binary()
        if not claude_path:
            return
        env = _build_subprocess_env()
        proc = await asyncio.create_subprocess_exec(
            claude_path,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "ping",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(_REPO_ROOT),
            limit=1024 * 1024,
        )
        # Drain stdout so the pipe does not fill up while we wait.
        async def _drain() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break

        try:
            await asyncio.wait_for(_drain(), timeout=30.0)
            await proc.wait()
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        _claude_log.info("claude_cli_prewarm_complete")
    except Exception:
        # Startup warming must never break the backend. Swallow everything.
        return


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
                subscription_raw = status.get("subscriptionType")
                subscription = str(subscription_raw or "").lower()
                logged_in = bool(status.get("loggedIn", False))
                # A null subscriptionType is returned by some CLI versions when
                # the user is logged in via claude.ai. First-party auth and
                # loggedIn is sufficient evidence of subscription access; we do
                # not require a specific non-null tier so we never accidentally
                # fall through to API-key billing for a signed-in user.
                subscription_ok = (
                    subscription in ALLOWED_SUBSCRIPTION_TYPES
                    or not subscription  # null / empty → OK when first-party
                )
                result = (
                    logged_in
                    and api_provider == "firstparty"
                    and subscription_ok
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


def _handle_stream_event(
    event: dict,
    tool_index_map: Optional[dict[int, str]] = None,
) -> tuple[Optional[str], bool, Optional[dict], Optional[dict]]:
    """Extract text, done flag, usage, and extra WS payloads from a stream event.

    Returns ``(text, done, usage, extra_ws_msg)``:
      - ``text``: a text fragment to forward as a token, or None
      - ``done``: True when this event signals the end of the response
      - ``usage``: token usage dict from the final result event, or None
      - ``extra_ws_msg``: an additional WebSocket message to send (tool calls, thinking), or None

    ``tool_index_map`` (optional) is a mutable dict owned by the caller
    that maps Anthropic content-block indices to tool_use ids. It lets us
    route ``input_json_delta`` fragments to the owning tool_use block so
    the frontend can accumulate args inside the collapsed tool pill
    rather than leaking raw JSON into the assistant bubble body. Tests
    that only exercise text/thinking/start paths can leave it as None.
    """
    etype = event.get("type")

    # Real-time streaming deltas (from --include-partial-messages)
    if etype == "stream_event":
        inner = event.get("event", {})
        inner_type = inner.get("type", "")

        if inner_type == "content_block_delta":
            delta = inner.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                return delta.get("text") or None, False, None, None
            if delta_type == "thinking_delta":
                thinking = delta.get("thinking", "")
                if thinking:
                    return None, False, None, {"type": "thinking", "data": thinking}
            if delta_type == "input_json_delta":
                # Fragment of a tool_use block's input JSON. Forward to
                # the frontend as a tool_use_delta event so the pill's
                # args accumulate without ever landing in the text body.
                # Silently dropped if we never saw the matching start
                # (malformed stream), which is the safe behavior: the
                # assistant text body must never carry partial JSON.
                if tool_index_map is None:
                    return None, False, None, None
                idx = inner.get("index")
                if not isinstance(idx, int):
                    return None, False, None, None
                tool_id = tool_index_map.get(idx)
                if not tool_id:
                    return None, False, None, None
                partial = delta.get("partial_json", "")
                if not isinstance(partial, str) or not partial:
                    return None, False, None, None
                return None, False, None, {
                    "type": "tool_use_delta",
                    "data": {"id": tool_id, "partial_json": partial},
                }

        elif inner_type == "content_block_start":
            block = inner.get("content_block", {})
            if block.get("type") == "thinking":
                return None, False, None, {"type": "thinking", "data": True}
            if block.get("type") == "tool_use":
                tool_id = block.get("id", "")
                # Record the index→id mapping so subsequent
                # input_json_delta fragments route back to this block.
                if tool_index_map is not None:
                    idx = inner.get("index")
                    if isinstance(idx, int) and tool_id:
                        tool_index_map[idx] = tool_id
                return None, False, None, {
                    "type": "tool_use",
                    "data": {
                        "tool": block.get("name", ""),
                        "id": tool_id,
                        "input": {},
                    },
                }

        return None, False, None, None

    # Full assistant message (arrives after all deltas when using partial messages,
    # or as the only text event when not using partial messages)
    if etype == "assistant":
        message = event.get("message") or {}
        blocks = message.get("content") or []
        parts: list[str] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        text = "".join(parts)
        return (text or None), False, None, None

    if etype == "result":
        usage_raw = event.get("usage") or {}
        usage = {
            "input_tokens": int(usage_raw.get("input_tokens", 0) or 0),
            "output_tokens": int(usage_raw.get("output_tokens", 0) or 0),
            "cache_creation_input_tokens": int(usage_raw.get("cache_creation_input_tokens", 0) or 0),
            "cache_read_input_tokens": int(usage_raw.get("cache_read_input_tokens", 0) or 0),
        }
        return None, True, usage, None

    return None, False, None, None


async def complete(
    messages: list[dict],
    system: Optional[str] = None,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 1024,
) -> Optional[str]:
    """One-shot non-streaming call through the local Claude CLI.

    Used by non-chat routers (specs, adventures, narrative, etc.) when the
    AI backend preference is set to ``claude_code`` (use the subscription).

    Returns the response text on success, or ``None`` if the CLI is
    unavailable, returns a non-zero exit code, or times out.
    """
    claude_path = _find_claude_binary()
    if not claude_path:
        _claude_log.debug("claude_complete: claude binary not found")
        return None

    prompt = _messages_to_prompt(messages, system)
    args = [
        claude_path,
        "-p",
        "--output-format",
        "text",
        "--dangerously-skip-permissions",
        # Block every tool — these are simple text inference calls and we
        # do not want the CLI to run any actions.
        "--disallowed-tools="
        "Bash,Grep,Read,Glob,Edit,Write,WebFetch,WebSearch,Task,"
        "TodoWrite,NotebookEdit,BashOutput,KillShell,ExitPlanMode,"
        "SlashCommand,ToolSearch,AskUserQuestion,EnterPlanMode,"
        "CronCreate,CronDelete,CronList,EnterWorktree,ExitWorktree,"
        "ListMcpResourcesTool,Monitor,PushNotification,"
        "ReadMcpResourceTool,RemoteTrigger,ScheduleWakeup,Skill,"
        "TaskOutput",
        "--strict-mcp-config",
        prompt,
    ]
    env = _build_subprocess_env()
    import subprocess
    try:
        # ->1806: run the fork+wait inside a worker thread. Forking the heavy
        # backend via create_subprocess_exec ON the event-loop thread stalls
        # TLS handshakes and wedges the worker. subprocess.run releases the GIL
        # across fork/exec/wait, so the loop stays responsive while this runs.
        result = await asyncio.to_thread(
            lambda: subprocess.run(
                args,
                capture_output=True,
                timeout=30,
                env=env,
                cwd=str(_REPO_ROOT),
            )
        )
        if result.returncode != 0:
            _claude_log.warning("claude_complete: CLI exited %d", result.returncode)
            return None
        return result.stdout.decode("utf-8", errors="replace").strip()
    except subprocess.TimeoutExpired:
        _claude_log.warning("claude_complete: timed out after 30s")
        return None
    except Exception as exc:
        _claude_log.warning("claude_complete: unexpected error: %s", exc)
        return None


async def stream_chat(
    messages: list[dict],
    websocket: WebSocket,
    system_prompt: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Stream a chat response from the local ``claude`` program.

    This is the main entry point used by ``chat_providers.ChatService``
    when the local program is available. It spawns the program with a
    clean environment (no leaked API key), reads the stream-json output
    line by line, forwards text fragments to the websocket, and returns
    the full assembled text.
    """
    _t_entry = time.perf_counter()
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

    # Session mode: if we have a tab_id, use Claude Code session persistence
    # instead of flattening messages into a single prompt string.
    tab_id = kwargs.get("tab_id")
    saw_partial = False  # track if we got stream deltas (avoid double-counting)
    saw_deltas = False   # track if any streaming deltas arrived (skip assistant dup)

    if tab_id:
        session_uuid = _session_id_for_tab(tab_id)
        is_resume = session_uuid in _known_sessions
        # Extract the latest user message
        last_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    last_msg = " ".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    last_msg = str(content)
                break

        # Check if the conversation has messages from other models.
        # Claude's session only stores its own turns, so messages from
        # Gemini or other providers are invisible to it. When that
        # happens, include the full conversation as context so Claude
        # can see what the other models said.
        has_other_model_msgs = any(
            msg.get("model") and msg.get("model") != "claude"
            for msg in messages
            if msg.get("role") == "assistant"
        )
        if has_other_model_msgs and is_resume:
            # Build a context preamble with the conversation so far,
            # then append the latest user message at the end.
            context_lines: list[str] = [
                "Here is the full conversation so far (it includes "
                "responses from multiple AI models):",
                "",
            ]
            for msg in messages[:-1]:  # exclude the latest user msg
                role = msg.get("role", "")
                c = msg.get("content", "")
                if isinstance(c, list):
                    c = " ".join(
                        b.get("text", "") for b in c
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                c = str(c or "").strip()
                if not c:
                    continue
                model_tag = msg.get("model", role)
                if role == "user":
                    context_lines.append(f"User: {c}")
                else:
                    context_lines.append(f"{model_tag}: {c}")
            context_lines.append("")
            context_lines.append(f"User: {last_msg}")
            prompt = "\n\n".join(context_lines)
        else:
            prompt = last_msg or "hello"
        saw_partial = True  # session mode always uses partial messages
    else:
        session_uuid = None
        is_resume = False
        prompt = _messages_to_prompt(messages, system_prompt)

    args = [
        claude_path,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--dangerously-skip-permissions",
    ]
    # Disable every tool for plain-text chat turns (e.g. All pill
    # broadcast). Without this, the CLI runs its full agent loop even
    # for casual questions because --dangerously-skip-permissions gives
    # it unrestricted access. Using --disallowed-tools with = assignment
    # (space-separated breaks the positional prompt argument) blocks
    # every built-in tool, and --strict-mcp-config with no explicit
    # --mcp-config blocks every MCP server so remote tools cannot fire
    # either. The result is a pure text reply.
    if kwargs.get("disable_tools"):
        args.append(
            "--disallowed-tools="
            "Bash,Grep,Read,Glob,Edit,Write,WebFetch,WebSearch,Task,"
            "TodoWrite,NotebookEdit,BashOutput,KillShell,ExitPlanMode,"
            "SlashCommand,ToolSearch,AskUserQuestion,EnterPlanMode,"
            "CronCreate,CronDelete,CronList,EnterWorktree,ExitWorktree,"
            "ListMcpResourcesTool,Monitor,PushNotification,"
            "ReadMcpResourceTool,RemoteTrigger,ScheduleWakeup,Skill,"
            "TaskOutput"
        )
        args.append("--strict-mcp-config")
    else:
        # Tool-enabled chat turns: block the three native file-inspection
        # tools that have direct mcp__ostk__* equivalents.
        # --dangerously-skip-permissions bypasses PreToolUse hooks
        # (including ostk-first.sh), so we enforce the ostk-first rule at
        # the CLI layer here instead. Bash, Edit, Write stay available so
        # the model can still run shell commands and modify files.
        args.append("--disallowed-tools=Grep,Read,Glob")
    # Session persistence flags
    if session_uuid:
        if is_resume:
            args.extend(["--resume", session_uuid])
        else:
            args.extend(["--session-id", session_uuid])
            if system_prompt:
                args.extend(["--append-system-prompt", system_prompt])
        _known_sessions.add(session_uuid)

    # Append the prompt as the final argument
    args.append(prompt)

    env = _build_subprocess_env()

    # Register this chat turn as an agent so it shows up on the Agents
    # page and in the Activity feed. Without this, the in-app chat is
    # invisible to both surfaces even though it is doing real work.
    chat_agent_name = f"chat-{tab_id[:8]}" if tab_id else "chat-default"
    last_user = ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                last_user = c
            break
    try:
        from routers.agents import register_chat_session
        await register_chat_session(
            chat_agent_name,
            model="claude-code-subscription",
            prompt_preview=last_user,
        )
    except Exception:
        # Registration is best-effort. A router-import glitch must not
        # block the chat turn.
        pass

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            # Run from the repo root so the Claude Code CLI picks up
            # .claude/settings.json (PreToolUse hooks including
            # ostk-first.sh) and .mcp.json (ostk MCP server) from
            # the project root. Without this the CWD is the api/
            # directory inherited from uvicorn and neither file is
            # found directly, so hooks never fire and ostk MCP tools
            # are unavailable, causing the model to fall back to
            # native Grep/Read.
            cwd=str(_REPO_ROOT),
            # A single Claude Code stream event (e.g. a tool_result
            # containing a large Read or Grep output) can be much
            # larger than asyncio's default 64 KiB StreamReader limit.
            # When that happens, proc.stdout.readline() raises
            # LimitOverrunError("Separator is found, but chunk is
            # longer than limit") and the whole chat turn dies with
            # that message surfaced to the UI. 32 MiB comfortably
            # absorbs anything the CLI emits on one line.
            limit=32 * 1024 * 1024,
        )
        _t_spawn = time.perf_counter()
        _claude_log.info(
            "claude_phase=spawned ms=%.0f resume=%s",
            (_t_spawn - _t_entry) * 1000,
            bool(session_uuid and is_resume),
        )
    except (OSError, FileNotFoundError):
        await _send_safe(
            websocket,
            {
                "type": "error",
                "data": "Your Claude subscription is not responding right now.",
            },
        )
        try:
            from routers.agents import complete_chat_session
            await complete_chat_session(chat_agent_name, status="failed")
        except Exception:
            pass
        return ""

    full_text = ""
    final_usage: Optional[dict] = None
    _first_token_logged = False

    # Maps Anthropic content-block index → tool_use id for the current
    # stream so input_json_delta fragments can be routed back to the
    # owning tool_use block. Scoped to this turn; reset on each spawn.
    tool_index_map: dict[int, str] = {}

    async def _read_stdout() -> None:
        nonlocal full_text, final_usage, saw_deltas, _first_token_logged
        assert proc.stdout is not None
        # Track text-block boundaries so we can inject \n\n when a new text
        # block starts after a non-text block (e.g. tool_use). Mirrors the
        # same logic in chat_providers.py stream_anthropic(). Without this
        # the first token of the new block appends directly onto the last
        # character of the previous block: "world.Now" instead of
        # "world.\n\nNow". →1737
        _in_text_block = False
        _had_text_block = False
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                event = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")
            if etype == "stream_event":
                inner = event.get("event", {})
                inner_type = inner.get("type", "")
                if inner_type == "content_block_start":
                    block = inner.get("content_block", {})
                    if block.get("type") == "text":
                        if _had_text_block and not _in_text_block:
                            sep = "\n\n"
                            full_text += sep
                            await _send_safe(websocket, {"type": "token", "data": sep})
                        _in_text_block = True
                elif inner_type == "content_block_stop":
                    if _in_text_block:
                        _had_text_block = True
                        _in_text_block = False

            text, done, usage, extra_msg = _handle_stream_event(event, tool_index_map)
            if text:
                if not _first_token_logged:
                    _claude_log.info(
                        "claude_phase=first_token ms=%.0f",
                        (time.perf_counter() - _t_entry) * 1000,
                    )
                    _first_token_logged = True
                if etype == "stream_event":
                    saw_deltas = True
                # The "assistant" event carries the complete response text.
                # When streaming deltas already forwarded it chunk-by-chunk,
                # sending it again would duplicate the entire response.
                if etype == "assistant" and saw_deltas:
                    # Deltas already delivered this text chunk-by-chunk; skip
                    # both accumulation (would double-count) and WebSocket send
                    # (would duplicate the entire response to the frontend).
                    pass
                else:
                    # Always accumulate regardless of saw_partial. The old
                    # `if not saw_partial` guard prevented full_text from being
                    # built in session mode (saw_partial=True), making
                    # stream_chat always return "" → empty bubble regression.
                    full_text += text
                    await _send_safe(websocket, {"type": "token", "data": text})
            if extra_msg:
                await _send_safe(websocket, extra_msg)
            # Intercept TodoWrite calls to push live todo-list WS messages.
            # The full input is only available in the "assistant" event (not
            # streaming deltas), so we inspect there after the main handler.
            if etype == "assistant":
                _blocks = (event.get("message") or {}).get("content") or []
                for _blk in _blocks:
                    if (
                        isinstance(_blk, dict)
                        and _blk.get("type") == "tool_use"
                        and _blk.get("name") == "TodoWrite"
                    ):
                        _todos = (_blk.get("input") or {}).get("todos", [])
                        if isinstance(_todos, list):
                            await _send_safe(
                                websocket, {"type": "todo-list", "todos": _todos}
                            )
            if done:
                if usage:
                    final_usage = usage

    # Run a heartbeat loop alongside _read_stdout() so the WebSocket stays
    # warm during silent phases (thinking, tool-use planning). Without this,
    # the vite dev proxy closes the idle socket and the UI shows "Connection
    # dropped before the response finished" (→1122).
    async def _heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(_WS_HEARTBEAT_INTERVAL_S)
            try:
                await _send_safe(websocket, {"type": "heartbeat"})
            except Exception:
                return

    _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
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
    finally:
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass

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
        try:
            from routers.agents import complete_chat_session
            await complete_chat_session(chat_agent_name, status="failed")
        except Exception:
            pass
        return full_text

    await _send_safe(
        websocket,
        {
            "type": "done",
            "usage": final_usage or {"input_tokens": 0, "output_tokens": 0},
        },
    )
    _claude_log.info(
        "claude_phase=stream_complete ms=%.0f chars=%d",
        (time.perf_counter() - _t_entry) * 1000,
        len(full_text),
    )
    try:
        from routers.agents import complete_chat_session
        usage = final_usage or {}
        await complete_chat_session(
            chat_agent_name,
            tokens_in=int(usage.get("input_tokens", 0) or 0),
            tokens_out=int(usage.get("output_tokens", 0) or 0),
            status="completed",
        )
    except Exception:
        pass

    # Record the turn so `ostk metrics` can show real numbers. The boot
    # context is included in the system prompt that ostk built upstream,
    # so import it lazily and reuse the same cached value to tag the
    # event consistently with the anthropic_api path.
    try:
        from services.token_metrics import safe_record_chat_turn
        from services.chat_providers import _get_boot_context, _log_chat_completion, _extract_chat_topic
        usage = final_usage or {}
        boot_ctx = _get_boot_context()
        _cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
        _cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        safe_record_chat_turn(
            model="claude-code-subscription",
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            has_ostk_boot=bool(boot_ctx),
            boot_context_bytes=len(boot_ctx.encode("utf-8")) if boot_ctx else 0,
            backend="claude_code",
            cache_creation_input_tokens=_cache_creation,
            cache_read_input_tokens=_cache_read,
        )
        _log_chat_completion(
            model="claude-code-subscription",
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            provider="claude_code",
            cache_creation_input_tokens=_cache_creation,
            cache_read_input_tokens=_cache_read,
            topic=_extract_chat_topic(messages),
        )
        _claude_log.info(
            "claude_code_cache cache_read=%d cache_creation=%d ratio_pct=%d",
            _cache_read,
            _cache_creation,
            int(_cache_read / (_cache_read + _cache_creation) * 100) if (_cache_read + _cache_creation) > 0 else 0,
        )
    except Exception:
        pass

    # Cache the completed response so the frontend can recover it via
    # GET /api/chat/last-response?tab_id=<id> if the WebSocket was torn
    # down mid-stream (e.g. uvicorn reload, browser navigation).
    if tab_id and full_text:
        _last_response_cache[tab_id] = full_text
        if len(_last_response_cache) > 100:
            oldest = next(iter(_last_response_cache))
            del _last_response_cache[oldest]

    return full_text
