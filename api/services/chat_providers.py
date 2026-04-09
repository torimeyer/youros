import asyncio
import os
from typing import Optional

import anthropic
from fastapi import WebSocket

from config import PROJECT_ROOT
from services import claude_code_provider
from services.ostk import ostk
from services.settings_store import settings_store
from services.template_matcher import match_template, merge_with_built_ins
from services.tool_executor import TOOL_DEFINITIONS, execute_tool


# Labels used in the ``backend_active`` websocket event so the chat panel
# can show which pathway is powering the response.
_BACKEND_LABEL_CLAUDE_CODE = "Powered by your Claude subscription"
_BACKEND_LABEL_ANTHROPIC_API = "Using Anthropic API"


async def _resolve_chat_backend() -> str:
    """Return the chat backend to use: ``claude_code`` or ``anthropic_api``.

    Honors the ``chat_backend_preference`` setting:
      - ``auto`` (default): pick ``claude_code`` when the local program is
        signed in, otherwise fall back to ``anthropic_api``.
      - ``claude_code``: force the Claude subscription path.
      - ``anthropic_api``: force the API key path.
    """
    preference = str(settings_store.get("chat_backend_preference", "auto") or "auto").lower()
    if preference == "claude_code":
        return "claude_code"
    if preference == "anthropic_api":
        return "anthropic_api"
    # auto
    if await claude_code_provider.is_claude_code_available():
        return "claude_code"
    return "anthropic_api"


async def _send_backend_active(websocket: WebSocket, backend: str) -> None:
    """Notify the chat panel which pathway is powering this response."""
    if backend == "claude_code":
        label = _BACKEND_LABEL_CLAUDE_CODE
    else:
        label = _BACKEND_LABEL_ANTHROPIC_API
    try:
        await websocket.send_json({
            "type": "backend_active",
            "data": {"name": backend, "label": label},
        })
    except Exception:
        pass

_ENV_KEY_MAP = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
}


_GEMINI_AUTH_HINTS = (
    "ACCESS_TOKEN_TYPE_UNSUPPORTED",
    "API_KEY_INVALID",
    "API key not valid",
    "invalid authentication credentials",
    "401",
    "PERMISSION_DENIED",
)


_GEMINI_KEY_HELP = (
    "Recommended: use the same Google Cloud project "
    "(https://console.cloud.google.com) you already set up for Drive, "
    "Calendar, or Gmail. In the API library, turn on \"Generative Language "
    "API\", then open Credentials and click Create credentials, API key. "
    "One project, one place to manage everything.\n\n"
    "Only using Gemini chat and nothing else from Google? Grab a free key "
    "at Google AI Studio (https://aistudio.google.com/apikey) instead. It "
    "ties to your personal Google account and is one click."
)


def _friendly_gemini_error(error_text: str) -> str:
    """Translate Google's cryptic auth errors into a friendly message.

    The Generative Language API returns long, jargon-heavy errors when the
    credentials are wrong (for example ``ACCESS_TOKEN_TYPE_UNSUPPORTED``
    when a user OAuth token is sent where an API key is expected). For
    those cases we return a one-liner the user can act on. For all other
    errors we pass the original message through unchanged.
    """
    lowered = error_text.lower()
    for hint in _GEMINI_AUTH_HINTS:
        if hint.lower() in lowered:
            return (
                "Gemini API key is missing or invalid. Add one in Settings "
                "under AI Provider.\n\n" + _GEMINI_KEY_HELP
            )
    return error_text


async def _resolve_api_key(settings_key: str) -> str:
    """Return API key from the system keychain (ostk), settings, or env.

    Resolution order:
    1. System keychain via ``ostk secret get``
    2. Legacy settings.json field (for backward compatibility)
    3. Environment variable
    """
    env_name = _ENV_KEY_MAP.get(settings_key, "")

    # 1. System keychain (preferred)
    if env_name:
        keychain_value = await ostk.secret_get(env_name)
        if keychain_value:
            return keychain_value

    # 2. Legacy settings.json (backward compat)
    key = settings_store.get(settings_key, "")
    if key:
        return key

    # 3. Environment variable
    if env_name:
        return os.environ.get(env_name, "")
    return ""

MAX_AGENT_TURNS = 10



def _messages_contain_images(messages: list[dict]) -> bool:
    """True if any message has an image block in its content.

    Used to route around backends that cannot receive vision input.
    """
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    return True
    return False


_BOOT_CONTEXT_CACHE: Optional[tuple[float, str]] = None


def _get_boot_context() -> str:
    """Run `ostk boot` once per 5 minutes and cache the output.

    The output is injected into the system prompt so the model never has
    to call the shell tool (and ask for approval) to get session context.
    """
    global _BOOT_CONTEXT_CACHE
    import time as _time
    now = _time.time()
    if _BOOT_CONTEXT_CACHE and now - _BOOT_CONTEXT_CACHE[0] < 300:
        return _BOOT_CONTEXT_CACHE[1]
    try:
        import subprocess
        result = subprocess.run(
            ["ostk", "boot"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        output = (result.stdout or "") + (result.stderr or "")
        # Strip ANSI escape codes.
        import re
        output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output).strip()
    except Exception:
        output = ""
    _BOOT_CONTEXT_CACHE = (now, output)
    return output


def _system_prompt() -> str:
    os_name = settings_store.get("os_name", "myOS")
    user_name = settings_store.get("user_name", "")
    owner = user_name if user_name else "the user"
    boot_context = _get_boot_context()
    boot_section = (
        f"\n\nSESSION CONTEXT (from `ostk boot` — already run, do not run again):\n{boot_context}\n"
        if boot_context else ""
    )
    return (
        f"You are {os_name}, {owner}'s personal operating system. "
        "You have access to tools that let you read files, write files, edit files, "
        "run shell commands, search code, manage tasks, search the web, fetch web pages, "
        f"run git operations, and spawn background agents in the workspace at {PROJECT_ROOT}. "
        "All tools including shell commands are pre-authorized. Never ask the user to approve "
        "a shell or tool call. Just run it. "
        f"Use these tools to help {owner} with whatever they need. "
        "When you need information from the codebase, read files or search. "
        f"When {owner} asks you to change something, use the edit or write tools. "
        "When a task is complex or can run in parallel with other work, use spawn_agent to "
        "create a background agent. When the user says 'spawn' or asks you to run "
        "something in the background, always use the spawn_agent tool.\n\n"
        "SAA COMMAND: When the user says 'saa' followed by a task description, this is "
        "the 'spawn and assign' command. You must follow this exact process:\n"
        "1. Plan: briefly outline your approach in 2-3 bullet points.\n"
        "2. Spawn agents: use the spawn_agent tool to create one or more background agents "
        "to do the actual work. Do NOT try to do the work yourself inline.\n"
        "3. Each agent's prompt must include clear, specific instructions for its piece of "
        "the task, and must instruct the agent to write tests for its work and verify they pass.\n"
        "4. If the task naturally splits into independent pieces, spawn multiple agents "
        "so they can work in parallel.\n"
        "5. After spawning, tell the user what agents you launched and what each one is "
        "working on. Keep it brief.\n"
        "Remember: 'saa' always means spawn agents. Never do the work inline when the user says 'saa'.\n\n"
        "DIAGNOSE COMMAND: When the user says 'diagnose' followed by a problem, find the "
        "root cause, fix it, and write a regression test so it never happens again. "
        "Read the actual code before making any claims. Never assume.\n\n"
        "ELIT COMMAND: When the user says 'elit' (explain like I'm Tori), explain the "
        "topic in plain language with no code, no jargon, and keep it brief.\n\n"
        "IDEA CAPTURE: If the user mentions a stray thought, aside, or rough idea in "
        "passing (phrases like 'random thought', 'idea:', 'btw it would be cool if', "
        "'note to self', or any musing they want captured but not acted on), silently "
        "call the capture_idea tool to file it as hay. Do NOT announce that you captured "
        "it. Do NOT capture questions, direct action requests, or things they want done "
        "right now. When in doubt, do not capture. After capturing, continue answering "
        "the rest of their message normally.\n\n"
        "Keep your responses brief and focused on outcomes, not process. "
        "Do NOT narrate your steps. Do NOT say 'Let me check' or 'Let me look'. "
        "Just do the work and share the result. "
        "Be action-oriented: read only what you need, then make edits quickly. "
        "Do not over-research. If you know enough to make a change, make it. "
        "Never use em-dashes."
        + boot_section
    )


def _extract_last_user_text(messages: list[dict]) -> str:
    """Return the plain text of the last user message, or ''.

    Handles both string content and list-of-blocks content (for images).
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return str(block.get("text", ""))
        return ""
    return ""


async def _maybe_match_template(
    messages: list[dict],
    websocket: WebSocket,
    api_key: str,
) -> Optional[dict]:
    """Run the template matcher on the last user message and notify the UI.

    Returns the matched template (with ``_match_reason``) or None. When a
    template matches, sends a ``template_matched`` event over the websocket
    so the chat panel can show a small "Using: <name>" badge.
    """
    if not settings_store.get("auto_template_matching", True):
        return None

    last_user_text = _extract_last_user_text(messages)
    if not last_user_text.strip():
        return None

    custom_raw = settings_store.get("custom_agent_templates", [])
    custom_list = custom_raw if isinstance(custom_raw, list) else []
    templates = merge_with_built_ins(custom_list)
    if not templates:
        return None

    # Only run the AI classifier when the user has added at least one
    # custom template. Built-in templates (saa, diagnose, elit) all reach
    # via explicit triggers, so the classifier adds no value for the
    # built-in only case and would burn an extra Claude call per chat.
    enable_classifier = any(
        isinstance(t, dict) and t.get("name") for t in custom_list
    )

    matched = await match_template(
        last_user_text,
        templates,
        api_key=api_key,
        enable_classifier=enable_classifier,
    )
    if not matched:
        return None

    try:
        await websocket.send_json({
            "type": "template_matched",
            "data": {
                "name": matched.get("name", ""),
                "description": matched.get("description", ""),
                "reason": matched.get("_match_reason", ""),
            },
        })
    except Exception:
        # Never let a websocket hiccup break the chat flow.
        pass
    return matched


def _compose_system_prompt(matched_template: Optional[dict]) -> str:
    """Return the system prompt, optionally augmented by a matched template."""
    base = _system_prompt()
    if not matched_template:
        return base
    extra = str(matched_template.get("prompt") or "").strip()
    if not extra:
        return base
    return base + "\n\n---\nACTIVE TEMPLATE: " + str(matched_template.get("name", "")) + "\n" + extra


class ChatService:
    async def stream_anthropic(self, messages: list[dict], websocket: WebSocket) -> str:
        # Run template matching up front so both backends pick up any
        # matched helper. The matcher itself uses the API key when one is
        # available, but it also handles the no-key case gracefully.
        api_key = await _resolve_api_key("anthropic_api_key")
        matched_template = await _maybe_match_template(messages, websocket, api_key)
        system_prompt = (
            _compose_system_prompt(matched_template) if matched_template else None
        )

        backend = await _resolve_chat_backend()

        # Claude Code CLI cannot receive inline image blocks. If the last
        # message includes an image (GIF or pasted screenshot), force the
        # Anthropic API backend so the model can actually see it.
        if backend == "claude_code" and _messages_contain_images(messages):
            api_key = await _resolve_api_key("anthropic_api_key")
            if api_key:
                backend = "anthropic_api"

        await _send_backend_active(websocket, backend)

        if backend == "claude_code":
            return await claude_code_provider.stream_chat(
                messages, websocket, system_prompt=system_prompt
            )

        if not api_key:
            await websocket.send_json({
                "type": "error",
                "data": (
                    "No Anthropic API key found. Sign in to your Claude subscription "
                    "by installing the local program, or add an Anthropic key in Settings."
                ),
            })
            return ""

        client = anthropic.AsyncAnthropic(api_key=api_key)
        stream_kwargs: dict = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "messages": messages,
        }
        if system_prompt:
            stream_kwargs["system"] = system_prompt
        full_text = ""
        try:
            async with client.messages.stream(**stream_kwargs) as stream:
                async for text in stream.text_stream:
                    full_text += text
                    await websocket.send_json({"type": "token", "data": text})

            response = await stream.get_final_message()
            await websocket.send_json({
                "type": "done",
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
            })
        except anthropic.APIError as e:
            await websocket.send_json({"type": "error", "data": str(e)})

        return full_text

    def _get_mcp_servers(self) -> list[dict]:
        """Return enabled MCP server configs from settings."""
        servers = settings_store.get("mcp_servers", [])
        if not isinstance(servers, list):
            return []
        return [s for s in servers if isinstance(s, dict) and s.get("enabled", True) and s.get("url")]

    async def agent_anthropic(self, messages: list[dict], websocket: WebSocket) -> str:
        """Run the Anthropic agent loop with tool use.

        Sends messages with tool definitions, executes any tool calls Claude
        makes, feeds results back, and repeats until Claude responds with
        just text (no more tool calls). Then streams that final text response.

        When MCP servers are configured in settings, uses the Anthropic beta
        MCP client API. Anthropic fetches tools from those servers and executes
        MCP tool calls server-side, returning results inline in the response.
        """
        api_key = await _resolve_api_key("anthropic_api_key")

        # Auto-match agent template based on the user's message. If matched,
        # the system prompt picks up the template's extra instructions and
        # the chat panel shows a small "Using: <name>" badge. We do this
        # before picking a backend so both paths get the matched helper.
        matched_template = await _maybe_match_template(messages, websocket, api_key)
        active_system_prompt = _compose_system_prompt(matched_template)

        backend = await _resolve_chat_backend()

        # Claude Code CLI cannot receive inline image blocks. If the last
        # message includes an image (GIF or pasted screenshot), force the
        # Anthropic API backend so the model can actually see it.
        if backend == "claude_code" and _messages_contain_images(messages):
            if api_key:
                backend = "anthropic_api"

        await _send_backend_active(websocket, backend)

        # The local program cannot run our Python tool loop today, so when
        # the subscription path is active we fall back to text-only chat
        # and skip the tool loop. The API-key path still runs the full
        # agent loop below.
        if backend == "claude_code":
            return await claude_code_provider.stream_chat(
                messages, websocket, system_prompt=active_system_prompt
            )

        if not api_key:
            await websocket.send_json({
                "type": "error",
                "data": (
                    "No Anthropic API key found. Sign in to your Claude subscription "
                    "by installing the local program, or add an Anthropic key in Settings."
                ),
            })
            return ""

        client = anthropic.AsyncAnthropic(api_key=api_key)
        conversation: list[dict] = list(messages)
        total_input_tokens = 0
        total_output_tokens = 0
        mcp_servers = self._get_mcp_servers()
        use_mcp = len(mcp_servers) > 0

        try:
            turn = 0
            while True:
                turn += 1
                if turn > MAX_AGENT_TURNS:
                    msg = "Reached max turns limit."
                    await websocket.send_json({"type": "token", "data": msg})
                    await websocket.send_json({
                        "type": "done",
                        "usage": {
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens,
                        },
                    })
                    return msg

                # Signal the frontend that we're working
                await websocket.send_json({"type": "thinking", "data": True})

                if use_mcp:
                    mcp_server_params = [
                        {
                            "name": s["name"],
                            "type": "url",
                            "url": s["url"],
                            **({"authorization_token": s["auth_token"]} if s.get("auth_token") else {}),
                        }
                        for s in mcp_servers
                    ]
                    response = await client.beta.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=4096,
                        system=active_system_prompt,
                        messages=conversation,
                        tools=TOOL_DEFINITIONS,  # type: ignore[arg-type]
                        mcp_servers=mcp_server_params,  # type: ignore[arg-type]
                        betas=["mcp-client-2025-04-04"],
                    )
                else:
                    response = await client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=4096,
                        system=active_system_prompt,
                        messages=conversation,
                        tools=TOOL_DEFINITIONS,
                    )

                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens

                # Process content blocks. MCP tool blocks (mcp_tool_use /
                # mcp_tool_result) are handled server-side by Anthropic and
                # returned inline. Local tool_use blocks need to be executed here.
                has_local_tool_use = False
                text_parts = []
                local_tool_uses = []
                assistant_content = []

                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)
                        assistant_content.append({"type": "text", "text": block.text})

                    elif block.type == "tool_use":
                        has_local_tool_use = True
                        local_tool_uses.append(block)
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": dict(block.input),
                        })

                    elif block.type == "mcp_tool_use":
                        # MCP tool called server-side. Notify the frontend.
                        await websocket.send_json({
                            "type": "mcp_tool_use",
                            "data": {
                                "tool": block.name,
                                "server": block.server_name,
                                "input": dict(block.input),
                                "id": block.id,
                            },
                        })
                        assistant_content.append({
                            "type": "mcp_tool_use",
                            "id": block.id,
                            "name": block.name,
                            "server_name": block.server_name,
                            "input": dict(block.input),
                        })

                    elif block.type == "mcp_tool_result":
                        # Result from the MCP server, already resolved by Anthropic.
                        content = block.content if isinstance(block.content, str) else str(block.content)
                        await websocket.send_json({
                            "type": "mcp_tool_result",
                            "data": {
                                "id": block.tool_use_id,
                                "result": content[:2000] if len(content) > 2000 else content,
                                "is_error": block.is_error,
                            },
                        })
                        assistant_content.append({
                            "type": "mcp_tool_result",
                            "tool_use_id": block.tool_use_id,
                            "content": content,
                            "is_error": block.is_error,
                        })

                if not has_local_tool_use:
                    # No local tools to execute. Stream the final text and exit.
                    for text in text_parts:
                        await websocket.send_json({"type": "token", "data": text})
                    await websocket.send_json({
                        "type": "done",
                        "usage": {
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens,
                        },
                    })
                    return "\n".join(text_parts)

                conversation.append({"role": "assistant", "content": assistant_content})

                # Notify the frontend of all pending local tool calls upfront.
                for block in local_tool_uses:
                    await websocket.send_json({
                        "type": "tool_use",
                        "data": {
                            "tool": block.name,
                            "input": dict(block.input),
                            "id": block.id,
                        },
                    })

                # Execute all local tools in parallel. A lock prevents concurrent
                # writes to the WebSocket transport.
                ws_lock = asyncio.Lock()

                async def _exec_and_notify(b: object, lock: asyncio.Lock) -> str:
                    result = await execute_tool(b.name, dict(b.input))
                    async with lock:
                        await websocket.send_json({
                            "type": "tool_result",
                            "data": {
                                "tool": b.name,
                                "id": b.id,
                                "result": result[:2000] if len(result) > 2000 else result,
                            },
                        })
                    return result

                raw_results = await asyncio.gather(
                    *[_exec_and_notify(block, ws_lock) for block in local_tool_uses],
                    return_exceptions=True,
                )

                # Collect results in original tool call order for the API message.
                tool_results = []
                for block, result in zip(local_tool_uses, raw_results):
                    if isinstance(result, BaseException):
                        result = f"Error executing {block.name}: {result}"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                conversation.append({"role": "user", "content": tool_results})

            # Loop exits naturally when Claude responds with text only (no tool calls)

        except anthropic.APIError as e:
            await websocket.send_json({"type": "error", "data": str(e)})
            return ""

    async def stream_gemini(self, messages: list[dict], websocket: WebSocket) -> str:
        # Gemini's public Generative Language API only accepts API keys.
        # User OAuth tokens (even with cloud-platform scope) are rejected
        # with ACCESS_TOKEN_TYPE_UNSUPPORTED, so we no longer try to use
        # them here. The Google sign-in flow is for Drive/Calendar/Gmail,
        # not for Gemini chat.
        api_key = await _resolve_api_key("gemini_api_key")

        if not api_key:
            await websocket.send_json({
                "type": "error",
                "data": (
                    "Gemini API key is missing. Add one in Settings under AI Provider.\n\n"
                    + _GEMINI_KEY_HELP
                ),
            })
            return ""

        full_text = ""
        try:
            import google.generativeai as genai
            # Always pass the API key explicitly so the SDK never falls back
            # to ambient default credentials (ADC), which could pick up the
            # user's Drive/Calendar OAuth token and fail with
            # ACCESS_TOKEN_TYPE_UNSUPPORTED.
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.0-flash")

            history = []
            for msg in messages[:-1]:
                role = "user" if msg["role"] == "user" else "model"
                history.append({"role": role, "parts": [msg["content"]]})

            chat = model.start_chat(history=history)
            response = chat.send_message(messages[-1]["content"], stream=True)
            for chunk in response:
                if chunk.text:
                    full_text += chunk.text
                    await websocket.send_json({"type": "token", "data": chunk.text})

            await websocket.send_json({"type": "done"})
        except Exception as e:
            error_text = str(e)
            friendly = _friendly_gemini_error(error_text)
            await websocket.send_json({"type": "error", "data": friendly})

        return full_text


chat_service = ChatService()
