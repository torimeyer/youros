import asyncio
import logging
import os
import random
from typing import Any, Awaitable, Callable, Optional

import anthropic
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

# Pre-warm the Google Generative AI SDK at module import time. On a cold
# uvicorn worker the first `import google.generativeai` inside a chat
# handler takes several seconds (transitively loads google.api_core,
# google.auth, grpc, protobuf, pkg_resources, etc.) while emitting
# FutureWarnings. During those seconds the chat WebSocket is blocked
# inside `_prepare_gemini_client` and the frontend's "Thinking"
# indicator hangs until its client side timeout closes the socket.
# Importing it here means the first Gemini chat request never pays the
# import cost, so stream_gemini reaches start_chat and send_message
# immediately after the user hits Send.
try:
    from google import genai as _genai_preload  # noqa: F401
except Exception:
    # If google.generativeai is missing (e.g. install did not include
    # the optional Gemini extras), fall through and let the later
    # lazy-import path surface the error to the user with the friendly
    # "install the Gemini package" message.
    _genai_preload = None  # type: ignore[assignment]

from config import PROJECT_ROOT
from services import claude_code_provider
from services import gemini_cli_provider
from services.ostk import ostk
from services.settings_store import settings_store
from services.template_matcher import match_template, merge_with_built_ins
from services.ostk import write_audit_entry
from services.token_metrics import safe_record_chat_turn
from services.tracing import trace_event
from services.tool_executor import TOOL_DEFINITIONS, execute_tool
import services.user_memory_store as _user_memory_store


def _extract_chat_topic(messages: list[dict], max_len: int = 60) -> str:
    """Return a short topic string from the last user message.

    Used by ``_log_chat_completion`` to give each chat session a
    meaningful name in the usage history instead of a generic
    "Chat session" label.
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            # Vision/multi-block payload: grab the first text block
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    content = block.get("text", "")
                    break
            else:
                content = ""
        if not isinstance(content, str) or not content.strip():
            continue
        text = content.strip().replace("\n", " ")
        if len(text) > max_len:
            text = text[:max_len].rsplit(" ", 1)[0] + "..."
        return text
    return "Chat"


def _log_chat_completion(
    *,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    provider: str = "anthropic",
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    topic: str = "Chat",
) -> None:
    """Write a chat.completion event to audit.jsonl for cost tracking.

    Called once per completed response (not per streaming chunk). The
    caller is responsible for invoking this only after the full response
    is assembled and token counts are final.
    """
    from datetime import datetime, timezone
    write_audit_entry({
        "event": "chat.completion",
        "name": "chat",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "provider": provider,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "topic": topic,
        "budget": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    trace_event(
        "llm_call_end",
        model=model,
        provider=provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    )


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

# Maps settings key names to the provider name used in enterprise_store
# org-level API key storage.
_SETTINGS_KEY_TO_PROVIDER = {
    "anthropic_api_key": "anthropic",
    "gemini_api_key": "gemini",
}


_GEMINI_AUTH_HINTS = (
    "ACCESS_TOKEN_TYPE_UNSUPPORTED",
    "API_KEY_INVALID",
    "API key not valid",
    "invalid authentication credentials",
    "401",
    "PERMISSION_DENIED",
)

# Signals that a quota or rate limit was hit. Google returns 429 with
# RESOURCE_EXHAUSTED when the per-minute or per-day quota is exceeded.
# We catch these separately so the user gets actionable copy ("you hit
# the free quota") rather than a raw "429 RESOURCE_EXHAUSTED" stack.
_GEMINI_QUOTA_HINTS = (
    "resource_exhausted",
    "resource has been exhausted",
    "429",
    "quota exceeded",
    "rate limit",
    "too many requests",
)


# Signals that the requested Gemini model itself is gone or wrong.
# Google returns a 404 NotFound with ``is no longer available`` when a
# previously valid model (e.g. ``gemini-2.0-flash``) is deprecated for
# new users. This is different from an auth failure and needs its own
# friendly hint that points at the env var override.
_GEMINI_MODEL_GONE_HINTS = (
    "no longer available",
    "is not found",
    "was not found",
    "not found for api version",
)


# Default Gemini model used by ``stream_gemini``. Verified live against
# Google's list_models API and a real generate_content call. We keep this
# as a module-level constant so tests can assert we never silently slide
# back to a deprecated name.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Per-phase timeouts for the Gemini streaming call. Without these a bad
# network or Google-side stall would leave ``stream_gemini`` hanging
# silently until the frontend's 30s dead-backend timer fires, which
# shows the generic "No response received" error with no hint of what
# went wrong. With these, a stall fails fast on the server side and
# sends a clear, actionable message.
#
# - CLIENT_READY: time to initialise the SDK client (first call only,
#   covers configure + GenerativeModel on a cold worker).
# - SEND_MESSAGE: time for start_chat().send_message(stream=True) to
#   return an iterator. This is the initial network round trip before
#   any chunks arrive.
# - FIRST_CHUNK: time from iterator ready to first chunk. A silent
#   Gemini (safety filter mid-processing, upstream outage) trips this.
# - NEXT_CHUNK: time between subsequent chunks. Loose enough to cover
#   thinking pauses on gemini-2.5 models.
_GEMINI_CLIENT_READY_TIMEOUT_S = 30.0
_GEMINI_SEND_MESSAGE_TIMEOUT_S = 30.0
_GEMINI_FIRST_CHUNK_TIMEOUT_S = 20.0
_GEMINI_NEXT_CHUNK_TIMEOUT_S = 45.0


# System instruction for Gemini. Kept as a module-level constant so tests
# can assert the "no self label" rule is in place and future edits do not
# accidentally drop it. The rule exists because Gemini likes to prefix
# replies with a literal "@Gemini:" tag, which is noisy in the chat panel
# since the bubble header already shows which model is speaking.
# Template form of the Gemini system instruction. The product-vs-instance
# terms are filled in at call time by ``_gemini_system_instruction()`` so
# every chat reflects the current user's ``instance_name`` setting while
# still introducing the product (myOS) correctly.
_GEMINI_SYSTEM_INSTRUCTION_TEMPLATE = (
    "You are Gemini, Google's AI model. "
    "If asked whether you are Gemini or which AI you are, confirm that you are Gemini. "
    "Do not describe yourself as local, embedded, or built into any system. "
    "You are answering inside an instance of myOS named {instance_name}. "
    "You can mention that you're running inside {instance_name} as context, "
    "but always answer as Gemini. "
    "Do not prefix your replies with your own name. The chat panel "
    "already shows who you are. "
    "When asked to chat with another AI, reply directly to what that "
    "other AI just said. Do not write a fake script with labels for "
    "both sides, do not narrate the exchange, do not add stage "
    "directions. Keep replies conversational and concise. "
    "Never use em-dashes. "
    "IMPORTANT: You cannot create calendar events, send emails, or use any tools. "
    "You are a chat-only model. If the user asks you to do something that requires "
    "tools (calendar, email, tasks, files), tell them to switch to Claude using the "
    "toggle below the chat input. Claude has access to all myOS tools."
)


def _gemini_system_instruction() -> str:
    """Return the Gemini system prompt with the current instance name filled in.

    Resolves ``instance_name`` from settings each call so a renaming of
    the user's instance takes effect on the next chat without a restart.
    Falls back to the product name ``myOS`` if the setting is missing.
    """
    instance_name = settings_store.get("instance_name", "myOS") or "myOS"
    return _GEMINI_SYSTEM_INSTRUCTION_TEMPLATE.format(instance_name=instance_name)


# Backwards-compatible alias. Existing callers and tests import this
# constant by name. It is frozen at import time using the then-current
# ``instance_name`` so a test that imports the module before patching
# settings still sees the default "myOS". Live code paths use the
# ``_gemini_system_instruction`` helper so they pick up renames.
GEMINI_SYSTEM_INSTRUCTION = _GEMINI_SYSTEM_INSTRUCTION_TEMPLATE.format(instance_name="myOS")


def _gemini_model_name() -> str:
    """Return the Gemini model name to use.

    Honors the ``MYOS_GEMINI_MODEL`` environment variable so users can
    override the default without editing code. When Google deprecates
    the default the user can swap in a working model immediately.
    """
    override = os.environ.get("MYOS_GEMINI_MODEL", "").strip()
    return override or DEFAULT_GEMINI_MODEL


# Module-level cache for the Gemini SDK client.
#
# ``genai.Client(api_key=...)`` re-initializes the underlying gRPC/HTTP
# transport on every call. For inline replies this means Tori pays a 2-8s
# cold-start penalty on the *second* and every subsequent Gemini message in
# the same backend process, even though the SDK is already imported. Caching
# keyed on ``(api_key, model_name)`` removes that cost. The tuple is
# intentionally small: if the API key rotates or the model override changes,
# the old entry is evicted naturally on the next miss (the dict never grows
# beyond a handful of entries since model names and keys rarely change at
# runtime).
#
# Thread safety: ``_prepare_gemini_client`` runs on a worker thread via
# ``asyncio.to_thread``.  Python's GIL protects simple dict reads and writes,
# so no explicit lock is required here.
#
# NOTE: we cache only ``(model_name, client)`` and NOT the genai module
# reference itself.  The caller always does a fresh ``from google import genai``
# after the cache lookup so that tests that swap ``sys.modules`` with a fake
# still get the currently-active module for exception types and protos.
_GEMINI_CLIENT_CACHE: dict[tuple[str, str], tuple[str, Any]] = {}


def _clear_gemini_client_cache() -> None:
    """Evict all cached Gemini clients (used in tests and on key rotation)."""
    _GEMINI_CLIENT_CACHE.clear()


# Module-level cache for the Anthropic AsyncClient.
#
# ``anthropic.AsyncAnthropic(api_key=...)`` constructs a brand new httpx
# async client on every call, which opens a fresh connection pool and
# resolves the API host on first use. Per chat turn this adds ~50 to
# ~200ms before the first byte goes out on the wire, depending on DNS
# and network warmth. Caching the client keyed on ``api_key`` makes the
# second and later turns in a session reuse the already-warm pool so the
# only cold path is the very first turn after the backend boots.
_ANTHROPIC_CLIENT_CACHE: dict[str, Any] = {}


def _get_anthropic_client(api_key: str) -> Any:
    """Return a cached ``AsyncAnthropic`` client for this api_key."""
    cached = _ANTHROPIC_CLIENT_CACHE.get(api_key)
    if cached is not None:
        return cached
    client = anthropic.AsyncAnthropic(api_key=api_key)
    _ANTHROPIC_CLIENT_CACHE[api_key] = client
    return client


def _clear_anthropic_client_cache() -> None:
    """Evict cached Anthropic clients (used in tests and on key rotation)."""
    _ANTHROPIC_CLIENT_CACHE.clear()


# Cache for resolved API keys keyed on settings_key. Avoids an ostk subprocess
# call (secret_get) on every chat turn. TTL is 60 seconds: short enough that a
# key rotation takes effect quickly, long enough to cover a full conversation.
_API_KEY_CACHE: dict[str, tuple[float, str]] = {}
_API_KEY_CACHE_TTL_S: float = 60.0


def _clear_api_key_cache() -> None:
    """Evict all cached API keys (used in tests and on key rotation)."""
    _API_KEY_CACHE.clear()


# One-time log marker so we print the active Gemini model on the first
# chat and stay quiet after that. Useful when debugging "which model is
# my instance actually talking to" without spamming the log every turn.
_GEMINI_MODEL_LOGGED: set[str] = set()


def _log_gemini_model_once(model_name: str) -> None:
    """Log the active Gemini model the first time we use it per process."""
    if model_name in _GEMINI_MODEL_LOGGED:
        return
    _GEMINI_MODEL_LOGGED.add(model_name)
    try:
        import logging
        logging.getLogger("myos.chat.gemini").info(
            "Gemini chat using model=%s (override MYOS_GEMINI_MODEL to change)",
            model_name,
        )
    except Exception:
        # Logging must never break a chat turn.
        pass


_GEMINI_KEY_HELP = (
    "Recommended: use the same Google Cloud project "
    "(https://console.cloud.google.com) you already set up for Drive, "
    "Calendar, or Gmail. Three steps:\n"
    "  1. Enable \"Generative Language API\" in the API library "
    "(https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com). "
    "It takes about 30 seconds.\n"
    "  2. Open Credentials and click Create credentials, API key.\n"
    "  3. Edit the new key and restrict it to \"Generative Language "
    "API\" under API restrictions. It only appears in the dropdown "
    "after step 1.\n\n"
    "Only using Gemini chat and nothing else from Google? Grab a free key "
    "at Google AI Studio (https://aistudio.google.com/apikey) instead. It "
    "ties to your personal Google account and is one click."
)


_GEMINI_QUOTA_HELP = (
    "You have hit the Gemini API usage limit. This usually resets within "
    "a minute (per-minute quota) or at midnight Pacific time (daily quota).\n\n"
    "If you hit this regularly, consider upgrading to a paid Google AI "
    "Studio plan at https://aistudio.google.com/apikey, or using a "
    "Google Cloud project with billing enabled."
)

_GEMINI_MODEL_GONE_HELP = (
    "The Gemini model myOS was using is no longer available. Google "
    "deprecates model names from time to time.\n\n"
    "Fix: set the MYOS_GEMINI_MODEL environment variable to a current "
    "model name (for example gemini-2.5-flash or gemini-flash-latest) "
    "and restart myOS. You can list the models your key can reach at "
    "https://ai.google.dev/gemini-api/docs/models.\n\n"
    "If the default stopped working for everyone, please report it at "
    "https://github.com/torimeyer/torios/issues so we can update the "
    "built-in default."
)


def _friendly_gemini_error(error_text: str) -> str:
    """Translate Google's cryptic errors into a friendly message.

    The Generative Language API returns long, jargon-heavy errors when the
    credentials are wrong (for example ``ACCESS_TOKEN_TYPE_UNSUPPORTED``
    when a user OAuth token is sent where an API key is expected) and
    when a previously valid model name has been deprecated for new users
    (a 404 with ``is no longer available``). For those cases we return a
    one-liner the user can act on. For all other errors we pass the
    original message through unchanged.
    """
    lowered = error_text.lower()
    for hint in _GEMINI_MODEL_GONE_HINTS:
        if hint in lowered:
            return (
                "The Gemini model used by myOS is no longer available. "
                + _GEMINI_MODEL_GONE_HELP
            )
    for hint in _GEMINI_QUOTA_HINTS:
        if hint in lowered:
            return (
                "Gemini hit a usage limit and could not respond. "
                + _GEMINI_QUOTA_HELP
            )
    for hint in _GEMINI_AUTH_HINTS:
        if hint.lower() in lowered:
            return (
                "Gemini API key is missing or invalid. Add one in Settings "
                "under AI Provider.\n\n" + _GEMINI_KEY_HELP
            )
    # The Gemini SDK raises a TypeError with a raw "Could not create Blob"
    # traceback when it cannot coerce a message value into a Part. The
    # class path inside the error mentions
    # ``google.ai.generativelanguage_v1beta.types.content.Content`` which
    # is not something the user can act on. Replace it with a plain
    # language message so the chat panel never shows a Python stack.
    if "could not create" in lowered and "blob" in lowered:
        return (
            "Gemini could not read part of this conversation. Try "
            "starting a new chat tab, or remove the last image or GIF "
            "and ask again."
        )
    # Unknown error path. Always prefix with "Gemini" so the chat bubble
    # tells the user WHICH provider failed, even when the raw message is
    # a Python exception string we can not translate further. Without
    # this prefix an error like "simulated upstream failure" or "503"
    # looks like a generic backend blip with no provider context.
    clean = (error_text or "").strip()
    if not clean:
        return (
            "Gemini returned an unknown error. Please try again in a "
            "moment."
        )
    if "gemini" not in clean.lower():
        return f"Gemini ran into a problem: {clean}. Please try again."
    return clean


# --- Gemini finish-reason handling ---
#
# Gemini's streaming API can cut a response off mid-sentence when its safety
# filter, recitation filter, or max-token cap trips. The SDK does NOT raise
# in that case. It just stops yielding chunks and leaves the reason on
# ``response.candidates[0].finish_reason``. If we ignore the reason and
# send a normal ``done`` event the chat panel renders the partial text as
# a finished turn, which is exactly the "As a" bubble bug. The helpers
# below translate each finish reason into plain-language copy keyed to
# what the user can do next. No em-dashes, no jargon, no raw enum names.

_gemini_log = logging.getLogger("myos.chat.gemini")

# Friendly messages for every non-STOP finish reason. Keys are the enum
# name strings (``SAFETY``, ``RECITATION``, etc.) so the lookup is a plain
# dict access. Any unknown reason (including future ones Google adds)
# falls through to the OTHER copy.
_GEMINI_FINISH_REASON_MESSAGES: dict[str, str] = {
    "SAFETY": (
        "Gemini stopped this reply because of its safety filters. "
        "Try rephrasing."
    ),
    "RECITATION": (
        "Gemini stopped this reply because it was about to repeat "
        "copyrighted text. Try a different question."
    ),
    "MAX_TOKENS": (
        "Gemini hit its response length limit. Ask a shorter "
        "question or split it up."
    ),
    "PROHIBITED_CONTENT": (
        "Gemini blocked this reply. Try rephrasing."
    ),
    "BLOCKLIST": (
        "Gemini blocked this reply. Try rephrasing."
    ),
    "SPII": (
        "Gemini blocked this reply. Try rephrasing."
    ),
    "LANGUAGE": (
        "Gemini could not answer in that language. Try again in "
        "English."
    ),
    "IMAGE_SAFETY": (
        "Gemini blocked the image it was about to generate. Try "
        "rephrasing."
    ),
    "MALFORMED_FUNCTION_CALL": (
        "Gemini stopped this reply unexpectedly. Try again."
    ),
    "OTHER": (
        "Gemini stopped this reply unexpectedly. Try again."
    ),
    "FINISH_REASON_UNSPECIFIED": (
        "Gemini stopped this reply unexpectedly. Try again."
    ),
}

# Friendly message for a prompt-level block (``prompt_feedback.block_reason``
# set before any tokens are emitted). Separate from the response-side
# finish reasons because the model never produced any output at all.
_GEMINI_PROMPT_BLOCKED_MESSAGE = (
    "Gemini blocked this question before answering because of its "
    "safety filters. Try rephrasing."
)


def _gemini_content_to_text(content: Any) -> str:
    """Flatten a chat message ``content`` field into a plain string for Gemini.

    The chat router runs every message through ``transform_image_messages``
    which rewrites any Claude vision payload (pasted screenshots, Giphy
    GIFs) into a LIST of Anthropic shaped content blocks like
    ``[{"type": "image", "source": {...}}, {"type": "text", "text": "..."}]``.
    That shape is valid for the Anthropic API but trips the Gemini SDK's
    ``parts`` coercion path with a confusing "Could not create Blob"
    error whose value is a completely unrelated prior turn.

    Gemini's ``send_message`` / ``start_chat`` accept a plain string, a
    ``dict``, a ``Blob``, or an ``Image``. To keep the fix surgical we
    collapse any list of blocks into a single string that preserves the
    text and substitutes a short placeholder for each image. Strings are
    returned untouched. Anything else is coerced via ``str()`` as a last
    resort so one bad message can never crash the whole turn.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    text = block.get("text", "")
                    if isinstance(text, str) and text:
                        pieces.append(text)
                elif block_type == "image":
                    pieces.append("[image attached]")
                else:
                    # Unknown block type: preserve any text field if one exists
                    # so we at least do not silently drop the message body.
                    text = block.get("text") if isinstance(block.get("text"), str) else ""
                    pieces.append(text or "[attachment]")
            elif isinstance(block, str):
                pieces.append(block)
        flattened = "\n".join(p for p in pieces if p).strip()
        return flattened or "[attachment]"
    if content is None:
        return ""
    return str(content)


# Supported image MIME types for Gemini vision.
_GEMINI_IMAGE_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)


def _gemini_content_to_parts(content: Any) -> list[Any]:
    """Convert a chat message ``content`` field into a list of Gemini SDK Parts.

    When the user pastes an image or GIF, ``transform_image_messages`` rewrites
    the message content into a list of Anthropic-shaped blocks like::

        [
            {"type": "image", "source": {"type": "base64",
                                          "media_type": "image/gif",
                                          "data": "<b64>"}},
            {"type": "text", "text": "what is this?"},
        ]

    For Claude, these blocks are sent directly. For Gemini, each block must
    become a ``protos.Part``. Text blocks become ``Part(text=...)`` and image
    blocks become ``Part(inline_data=Blob(mime_type=..., data=<bytes>))``.

    A plain string is returned as a single text Part. Unsupported MIME types
    fall back to a ``[image attached]`` text placeholder so the rest of the
    conversation still makes sense to the model.
    """
    try:
        import base64 as _b64
        from google import genai as _genai
        _protos = _genai.types
    except Exception:
        # SDK not installed; return a best-effort text-only list.
        return [_gemini_content_to_text(content)]

    if isinstance(content, str):
        return [_protos.Part(text=content)] if content else []

    if isinstance(content, list):
        parts: list[Any] = []
        for block in content:
            if not isinstance(block, dict):
                if isinstance(block, str) and block:
                    parts.append(_protos.Part(text=block))
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text:
                    parts.append(_protos.Part(text=text))
            elif block_type == "image":
                source = block.get("source", {})
                src_type = source.get("type", "")
                mime_type = source.get("media_type", "")
                if src_type == "base64" and mime_type in _GEMINI_IMAGE_MIME_TYPES:
                    raw_data = source.get("data", "")
                    if isinstance(raw_data, str) and raw_data:
                        try:
                            image_bytes = _b64.b64decode(raw_data)
                            parts.append(
                                _protos.Part(
                                    inline_data=_protos.Blob(
                                        mime_type=mime_type,
                                        data=image_bytes,
                                    )
                                )
                            )
                        except Exception:
                            parts.append(_protos.Part(text="[image attached]"))
                    else:
                        parts.append(_protos.Part(text="[image attached]"))
                else:
                    # Unsupported source type (e.g. url) or MIME type.
                    parts.append(_protos.Part(text="[image attached]"))
            else:
                text = block.get("text") if isinstance(block.get("text"), str) else ""
                parts.append(_protos.Part(text=text or "[attachment]"))
        # Ensure at least a minimal prompt so send_message never sees an empty list.
        return parts if parts else [_protos.Part(text="[attachment]")]

    if content is None:
        return [_protos.Part(text="")]
    return [_protos.Part(text=str(content))]


def _gemini_finish_reason_name(finish_reason: Any) -> str:
    """Return the finish reason as an upper-case enum name string.

    ``google.generativeai`` exposes finish_reason as a
    ``protos.Candidate.FinishReason`` proto enum. In practice it also
    shows up as a raw int on some code paths (for example when the SDK
    is swapped for a test double). This helper accepts both and returns
    the canonical enum name (``SAFETY``, ``STOP``, ``MAX_TOKENS``, etc.)
    so downstream lookups can use plain dict access.
    """
    if finish_reason is None:
        return "FINISH_REASON_UNSPECIFIED"
    name = getattr(finish_reason, "name", None)
    if isinstance(name, str) and name:
        return name
    # Fallback: map the raw int via the SDK enum so we never hard-code
    # the integer values. Anything unknown becomes OTHER so the friendly
    # message is still a useful, plain-language sentence.
    try:
        from google import genai  # local import: optional dep
        fr_enum = genai.types.FinishReason(int(finish_reason))
        return fr_enum.name
    except Exception:
        return "OTHER"


def _gemini_friendly_finish_message(
    finish_reason_name: str, partial_text: str
) -> str:
    """Return plain-language copy for a non-STOP Gemini finish reason.

    When ``partial_text`` has real content (more than a handful of
    characters) we prefix it so the user can still see what Gemini was
    starting to say before it stopped. Anything shorter than five
    characters (the "As a" bug case) is dropped because it is just noise.
    """
    base = _GEMINI_FINISH_REASON_MESSAGES.get(
        finish_reason_name,
        _GEMINI_FINISH_REASON_MESSAGES["OTHER"],
    )
    trimmed = (partial_text or "").strip()
    if len(trimmed) > 5:
        return f"[Gemini said: {trimmed}] before stopping. " + base
    return base


async def _send_friendly_gemini_error(
    websocket: WebSocket, message: str, *, reason_name: str = ""
) -> None:
    """Send a plain-language error to the chat panel for a Gemini failure.

    Mirrors ``_send_friendly_anthropic_error`` so the two providers share
    the same surface contract: a ``type:error`` event with user-ready
    copy, no raw enum values, no em-dashes, no jargon. The real reason
    is logged server-side for debugging but never leaks to the chat.
    """
    try:
        await websocket.send_json({"type": "error", "data": message})
    except Exception:
        # Never let a websocket hiccup mask the underlying failure.
        pass
    try:
        if reason_name:
            _gemini_log.warning(
                "gemini stream ended without STOP: finish_reason=%s",
                reason_name,
            )
    except Exception:
        pass


async def _resolve_api_key(settings_key: str) -> str:
    """Return API key from org config, system keychain (ostk), settings, or env.

    Resolution order:
    1. Org-level key (enterprise mode only)
    2. System keychain via ``ostk secret get``
    3. Legacy settings.json field (for backward compatibility)
    4. Environment variable
    """
    import time as _t
    now = _t.monotonic()
    cached = _API_KEY_CACHE.get(settings_key)
    if cached is not None and (now - cached[0]) < _API_KEY_CACHE_TTL_S:
        return cached[1]

    # 1. Org-level key (enterprise mode)
    provider = _SETTINGS_KEY_TO_PROVIDER.get(settings_key, "")
    if provider:
        from services import enterprise_store
        if enterprise_store.is_enterprise():
            org_key = enterprise_store.get_org_api_key(provider)
            if org_key:
                _API_KEY_CACHE[settings_key] = (now, org_key)
                return org_key

    env_name = _ENV_KEY_MAP.get(settings_key, "")

    # 2. System keychain (preferred)
    if env_name:
        keychain_value = await ostk.secret_get(env_name)
        if keychain_value:
            _API_KEY_CACHE[settings_key] = (now, keychain_value)
            return keychain_value

    # 3. Legacy settings.json (backward compat)
    key = settings_store.get(settings_key, "")
    if key:
        _API_KEY_CACHE[settings_key] = (now, key)
        return key

    # 4. Environment variable
    result = os.environ.get(env_name, "") if env_name else ""
    _API_KEY_CACHE[settings_key] = (now, result)
    return result

MAX_AGENT_TURNS = 40


# --- Anthropic transient-error retry policy ---
#
# Anthropic's API occasionally returns a 5xx (Internal server error, bad
# gateway, gateway timeout) or drops the TCP connection mid-request. These
# are almost always transient and recover on a second attempt. Without a
# retry in place, a single blip kills the whole chat turn and leaks a raw
# JSON error string into the chat panel. The policy below is intentionally
# small and boring:
#
# - At most ``_ANTHROPIC_MAX_ATTEMPTS`` attempts total.
# - Backoff delays come from ``_ANTHROPIC_RETRY_DELAYS`` (seconds between
#   attempts). If Anthropic sends a ``Retry-After`` header we honor that
#   instead.
# - ONLY retries on ``APIConnectionError``, ``APITimeoutError``, and
#   ``APIStatusError`` with a 5xx status code. 4xx errors are client bugs
#   (bad input, bad key, rate-limited by the model) and retrying them just
#   masks the real problem.
# - Adds a tiny random jitter to the delays so parallel callers do not
#   thunder on Anthropic the exact same millisecond.
_ANTHROPIC_MAX_ATTEMPTS = 3
_ANTHROPIC_RETRY_DELAYS = (0.5, 1.5, 4.0)

# Heartbeat cadence for long-running Anthropic calls. When the agent loop
# is waiting on a non-streaming messages.create that can take 30+ seconds
# (tool-use planning phase), or when a stream is open but emitting nothing
# (extended thinking pause), we periodically send a small {"type":
# "heartbeat"} frame so browser/proxy idle timers do not close the
# WebSocket mid-turn. The frontend ignores these frames entirely, they
# exist solely to keep bytes flowing across the socket.
_ANTHROPIC_HEARTBEAT_INTERVAL_S = 10.0

# Plain-language message shown to the user when every retry has failed.
# No em-dashes, no raw JSON, no jargon. Matches the writing-style rules in
# CLAUDE.md.
_ANTHROPIC_UNAVAILABLE_MESSAGE = (
    "Claude is having a moment and could not answer that. "
    "Give it a few seconds and try again."
)

_anthropic_retry_log = logging.getLogger("myos.chat.anthropic.retry")


def _is_retryable_anthropic_error(exc: BaseException) -> bool:
    """Return True if ``exc`` is a transient Anthropic error worth retrying.

    Retry-worthy cases:
      - Any ``APIConnectionError`` (includes ``APITimeoutError``). These
        mean the request never got a clean response, so repeating it is
        safe.
      - ``APIStatusError`` with a 5xx status code. These are Anthropic
        telling us *their* side blew up, and the Anthropic docs explicitly
        say to retry with backoff.
      - ``InternalServerError`` is just a subclass of ``APIStatusError``
        with status 500 so it falls out of the check above for free.

    Not retry-worthy:
      - ``APIStatusError`` with 4xx. Those are client bugs (bad key,
        unknown model, context too long, bad message shape). Retrying them
        just hides the real problem from the user and wastes latency.
    """
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", None)
        try:
            return bool(status is not None and 500 <= int(status) < 600)
        except (TypeError, ValueError):
            return False
    return False


def _classify_anthropic_error(
    exc: BaseException, *, model: Optional[str] = None
) -> dict:
    """Return a structured error payload for an Anthropic exception.

    Keys: ``category`` (str), ``http_status`` (int or None),
    ``user_message`` (str).  Consumed by ``_send_friendly_anthropic_error``
    and by the raw exception handlers so both paths produce consistent
    structured payloads the frontend can switch on.
    """
    http_status = getattr(exc, "status_code", None)
    try:
        http_status = int(http_status) if http_status is not None else None
    except (TypeError, ValueError):
        http_status = None

    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return {
            "category": "network",
            "http_status": None,
            "user_message": "Lost connection to backend.",
        }

    if isinstance(exc, anthropic.APIStatusError):
        if http_status in (401, 403):
            return {
                "category": "auth_invalid",
                "http_status": http_status,
                "user_message": "Anthropic API key invalid or missing.",
            }
        if http_status == 429:
            model_label = f" for {model}" if model else ""
            return {
                "category": "quota_exceeded",
                "http_status": 429,
                "user_message": (
                    f"Quota exceeded{model_label} right now. "
                    "Try again later or switch model."
                ),
            }
        if http_status is not None and 500 <= http_status < 600:
            return {
                "category": "provider_5xx",
                "http_status": http_status,
                "user_message": (
                    f"Anthropic returned {http_status}; "
                    "their API may be having issues."
                ),
            }

    msg = str(exc)[:200]
    return {
        "category": "unknown",
        "http_status": http_status,
        "user_message": f"Unexpected error: {msg}",
    }


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Return the Retry-After delay from an Anthropic error, if any.

    Anthropic sometimes sends a ``Retry-After`` header with a number of
    seconds to wait before the next attempt. When present we honor it
    instead of our own backoff schedule so we do not hammer the server
    harder than it asked us to. Returns None if no header is set or the
    value cannot be parsed.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # Clamp to a reasonable ceiling so a buggy header cannot stall chat
    # for a full minute.
    if value < 0:
        return None
    return min(value, 30.0)


async def _anthropic_retry_call(
    func: Callable[[], Awaitable[Any]],
    *,
    op_name: str = "anthropic.messages.create",
) -> Any:
    """Call ``func`` with bounded retry on transient Anthropic failures.

    ``func`` must be a zero-arg coroutine that performs the actual SDK
    call (for example ``lambda: client.messages.create(**kwargs)``). Any
    non-retryable error is re-raised immediately so upstream code can
    still show clear 4xx messages.
    """
    from services.tracing import trace_event as _trace_event
    _trace_event("llm_call_start", op=op_name)
    last_exc: Optional[BaseException] = None
    for attempt in range(_ANTHROPIC_MAX_ATTEMPTS):
        try:
            _result = await func()
            try:
                _trace_event("llm_call_end", op=op_name, ok=True, attempts=attempt + 1)
            except Exception:
                pass
            return _result
        except BaseException as exc:  # noqa: BLE001
            if not _is_retryable_anthropic_error(exc):
                try:
                    _trace_event("llm_call_end", op=op_name, ok=False, error=exc.__class__.__name__)
                except Exception:
                    pass
                raise
            last_exc = exc
            if attempt >= _ANTHROPIC_MAX_ATTEMPTS - 1:
                break
            # Honor Retry-After if Anthropic sent one, otherwise use the
            # configured backoff schedule with a little jitter.
            server_delay = _retry_after_seconds(exc)
            if server_delay is not None:
                delay = server_delay
            else:
                base = _ANTHROPIC_RETRY_DELAYS[attempt]
                delay = base + random.uniform(0, base * 0.25)
            try:
                _anthropic_retry_log.warning(
                    "%s transient failure (attempt %d/%d): %s. retrying in %.2fs",
                    op_name,
                    attempt + 1,
                    _ANTHROPIC_MAX_ATTEMPTS,
                    exc.__class__.__name__,
                    delay,
                )
            except Exception:
                pass
            await asyncio.sleep(delay)
    # All attempts exhausted on a retryable error. Re-raise the last one
    # so the caller can convert it to a friendly message.
    assert last_exc is not None
    raise last_exc


async def _with_ws_heartbeat(
    websocket: WebSocket,
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    interval_s: Optional[float] = None,
) -> Any:
    """Run ``coro_factory()`` while periodically sending WS heartbeat frames.

    Purpose. A long non-streaming call to Anthropic (for example the
    ``messages.create`` used in the agent-tool-use loop, or the silent
    ``thinking`` phase at the start of a stream) can take 30+ seconds
    without any bytes flowing over the WebSocket. Browsers, vite proxies,
    and reverse proxies interpret that silence as an idle socket and
    close it, which surfaces in the UI as a "Connection dropped before
    the response finished" error and nukes the in-flight assistant
    bubble.

    Fix. While the real work runs, spawn a sibling task that sends a
    tiny ``{"type": "heartbeat"}`` JSON frame every ``interval_s``
    seconds. The frontend (``useWebSocket``) recognizes this type and
    drops it on the floor, so it never reaches the chat panel. Its only
    job is to keep the socket warm. The sibling task is always cancelled
    when the real coroutine finishes, whether it returned or raised, so
    we never leak background heartbeat loops.
    """
    # Resolve the interval at call time (not at def time) so tests that
    # patch ``_ANTHROPIC_HEARTBEAT_INTERVAL_S`` via ``unittest.mock.patch``
    # take effect for new invocations.
    effective_interval = interval_s if interval_s is not None else _ANTHROPIC_HEARTBEAT_INTERVAL_S
    stop = asyncio.Event()

    async def _beat() -> None:
        try:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=effective_interval)
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except Exception:
                    # If the socket is already dead there's no point in
                    # retrying. The real call will fail on its next write
                    # and the outer handler will surface the real error.
                    return
        except asyncio.CancelledError:
            return

    beat_task = asyncio.create_task(_beat())
    try:
        return await coro_factory()
    finally:
        stop.set()
        beat_task.cancel()
        try:
            await beat_task
        except (asyncio.CancelledError, Exception):
            pass


async def _send_friendly_anthropic_error(
    websocket: WebSocket, exc: BaseException, *, model: Optional[str] = None
) -> None:
    """Send a structured, plain-language error to the chat panel.

    Uses ``_classify_anthropic_error`` so every error path produces the
    same ``{type, category, data}`` shape.  The frontend switches on
    ``category`` to show the right copy; older clients fall back to
    displaying ``data`` directly.
    """
    classified = _classify_anthropic_error(exc, model=model)
    try:
        await websocket.send_json({
            "type": "error",
            "category": classified["category"],
            "data": classified["user_message"],
        })
    except Exception:
        pass
    try:
        _anthropic_retry_log.error(
            "anthropic call failed after retries: %s: %s",
            exc.__class__.__name__,
            exc,
        )
    except Exception:
        pass


# Smaller Claude model used as an automatic fallback when Sonnet returns 429.
_ANTHROPIC_FALLBACK_MODEL = "claude-haiku-4-5-20251001"


async def _handle_anthropic_rate_limit(
    chat_service_instance: "ChatService",
    messages: list[dict],
    websocket: WebSocket,
) -> str:
    """Handle a 429 rate-limit from Anthropic by retrying with a smaller model.

    Sends a ``provider_fallback`` notice so the chat panel can show a
    brief inline status, then re-runs the request with Haiku.
    """
    try:
        await websocket.send_json({
            "type": "provider_fallback",
            "from": "claude-sonnet",
            "to": "claude-haiku",
            "reason": "Claude Sonnet is busy right now. Switching to Claude Haiku for this message.",
        })
    except Exception:
        pass
    return await chat_service_instance.stream_anthropic(
        messages, websocket, _fallback_model=_ANTHROPIC_FALLBACK_MODEL
    )



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


# --- Extended thinking ---
#
# Complex questions benefit from extended thinking. We enable it when
# the user's message is long enough or contains question words that
# signal analytical reasoning. Short greetings and simple commands
# skip thinking to keep responses snappy.

import re as _re

_THINKING_QUESTION_WORDS = _re.compile(
    r"\b(why|how|explain|compare|analyze|analyse|evaluate|describe|"
    r"what\s+(?:are|is|would|should|could|does)|"
    r"should\s+i|can\s+you\s+explain)\b",
    _re.IGNORECASE,
)

EXTENDED_THINKING_CHAR_THRESHOLD = 50
EXTENDED_THINKING_BUDGET_TOKENS = 10000


def _should_use_thinking(text: str) -> bool:
    """Return True if the message seems complex enough to benefit from thinking.

    Criteria (either one triggers thinking):
      - The message is longer than EXTENDED_THINKING_CHAR_THRESHOLD characters.
      - The message contains analytical question words.
    """
    if not isinstance(text, str):
        return False
    if len(text) > EXTENDED_THINKING_CHAR_THRESHOLD:
        return True
    if _THINKING_QUESTION_WORDS.search(text):
        return True
    return False


_BOOT_CONTEXT_CACHE: Optional[tuple[float, str]] = None
_BOOT_CONTEXT_REFRESH_IN_FLIGHT: bool = False

# Audit event types that are surfaced as "recent activity" context.
_ACTIVITY_EVENTS = frozenset({
    "agent.completed",
    "agent.spawned",
    "specs.created",
    "spec.created",
    "files.written",
    "file.written",
    "needle.closed",
    "task.closed",
    "chat.completion",
})


_ACTIVITY_CONTEXT_CACHE: Optional[tuple[float, tuple[int, int], str]] = None
_ACTIVITY_CONTEXT_TTL_S: float = 15.0


# Agent statuses that mean "no real work landed". The chat context must
# never surface these as evidence of authorship, because their transcripts
# and summaries reflect unfinished or aborted runs. See the authorship
# hallucination regression: a cancelled 0-token demo agent was shown to
# the chat model as if it had actually built a UI feature.
_NON_AUTHORING_AGENT_STATUSES: frozenset[str] = frozenset({
    "cancelled",
    "failed",
    "terminated_stale",
    "killed",
    "stopped",
    "abandoned",
})


def _load_agent_state_for_filter() -> dict:
    """Return ``agent_state.json`` keyed by agent name, or ``{}`` on error.

    Used by ``_recent_activity_context`` to drop cancelled / 0-token
    agents from the system-prompt context so the chat model never
    mistakes an aborted run for a real code change. The loader is
    deliberately tolerant: any failure returns an empty dict and the
    caller falls back to showing the audit row unfiltered.
    """
    try:
        from routers.agents import AGENT_STATE_PATH  # type: ignore
        import json as _json
        path = AGENT_STATE_PATH
        if not path.exists():
            return {}
        data = _json.loads(path.read_text() or "{}")
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def _agent_is_non_authoring(meta: dict) -> bool:
    """Return True when an agent's meta means its output is not real work.

    Two signals:
      - status is in ``_NON_AUTHORING_AGENT_STATUSES`` (cancelled, failed, etc.)
      - tokens_used is explicitly zero (agent never produced output)

    Either trips the filter. An empty dict (agent not in
    ``agent_state.json`` at all) returns False so the audit row is
    shown unfiltered; we only suppress rows we have direct evidence
    are non-authoring.
    """
    if not isinstance(meta, dict) or not meta:
        return False
    status = str(meta.get("status") or "").lower()
    if status in _NON_AUTHORING_AGENT_STATUSES:
        return True
    # tokens_used of 0 when the key is explicitly set is the quota-cap
    # / instant-cancel shape. No tokens means no work landed
    # regardless of how the status was stamped. But a missing key is
    # NOT evidence of non-authoring work, so only filter when the
    # field is present.
    if "tokens_used" in meta:
        try:
            tokens_used = int(meta.get("tokens_used") or 0)
        except (TypeError, ValueError):
            tokens_used = 0
        if tokens_used == 0:
            return True
    return False


def _recent_activity_context(n: int = 20, max_chars: int = 1800) -> str:
    """Return a compact summary of the last *n* notable audit events.

    Reads audit.jsonl (cached), filters to activity-relevant event types,
    and formats them as a tight bullet list capped at *max_chars*.
    Prepended to the volatile system-prompt block so the model knows
    what was just built, spawned, or written.

    Safety filter: ``agent.completed`` and ``agent.spawned`` rows for
    cancelled or 0-token agents are dropped, and the free-form
    ``summary`` text is NEVER surfaced to the chat model. Agent
    transcripts can claim "I implemented X" even when the run was
    cancelled or never wrote code, and the chat model will treat that
    claim as ground truth authorship. Git is the only source of
    truth for code authorship, see ``_system_prompt``.

    Result is cached in-process for ``_ACTIVITY_CONTEXT_TTL_S`` seconds so
    back-to-back chat turns do not re-read the entire audit log (which
    measured around 190ms on Tori's machine). The cache key includes
    ``(n, max_chars)`` so callers with different args do not clobber
    each other.
    """
    global _ACTIVITY_CONTEXT_CACHE
    import time as _time
    now = _time.monotonic()
    key = (n, max_chars)
    if _ACTIVITY_CONTEXT_CACHE is not None:
        ts, cached_key, cached_value = _ACTIVITY_CONTEXT_CACHE
        if cached_key == key and (now - ts) < _ACTIVITY_CONTEXT_TTL_S:
            return cached_value
    try:
        from services.ostk import read_audit_entries
        entries = read_audit_entries()
    except Exception:
        return ""

    # Load agent metadata once so the authorship filter can skip
    # cancelled / 0-token agent rows without re-reading the file on
    # each entry. Missing file or parse errors return ``{}`` and the
    # filter falls back to a tolerant pass-through for those rows.
    agent_meta = _load_agent_state_for_filter()

    # Walk backwards through the full audit log, keeping only events the
    # model cares about, until we have n events.
    relevant: list[dict] = []
    for entry in reversed(entries):
        event = entry.get("event")
        if event not in _ACTIVITY_EVENTS:
            continue
        # Authorship filter: for agent rows, skip when the agent was
        # cancelled / failed / 0-token. The chat model should never see
        # these as evidence of completed work.
        if event in ("agent.completed", "agent.spawned"):
            name = entry.get("name", "")
            meta = agent_meta.get(name) if name else None
            if _agent_is_non_authoring(meta or {}):
                continue
        relevant.append(entry)
        if len(relevant) >= n:
            break
    if not relevant:
        return ""

    lines: list[str] = []
    for entry in reversed(relevant):  # chronological order
        event = entry.get("event", "")
        name = entry.get("name", "")
        ts_raw = entry.get("timestamp") or entry.get("ts") or ""
        # Shorten timestamp to HH:MM
        ts = ts_raw[11:16] if len(ts_raw) >= 16 else ts_raw

        if event in ("agent.completed",):
            # Deliberately DO NOT surface the free-form ``summary``
            # field. Summaries are self-reported by agents and are not
            # ground truth for authorship. Grounding authorship
            # requires git, not agent claims.
            line = f"[{ts}] agent '{name}' completed"
        elif event in ("agent.spawned",):
            model = entry.get("model", "")
            line = f"[{ts}] agent '{name}' spawned" + (f" ({model})" if model else "")
        elif event in ("specs.created", "spec.created"):
            line = f"[{ts}] spec created: {name}"
        elif event in ("files.written", "file.written"):
            path = entry.get("path", name)
            line = f"[{ts}] file written: {path}"
        elif event in ("needle.closed", "task.closed"):
            title = entry.get("title", name)
            line = f"[{ts}] task closed: {title}"
        elif event == "chat.completion":
            topic = entry.get("topic", "")
            line = f"[{ts}] chat: {topic}" if topic else f"[{ts}] chat turn"
        else:
            line = f"[{ts}] {event}: {name}"

        lines.append(line)

    block = "RECENT ACTIVITY:\n" + "\n".join(lines)
    if len(block) > max_chars:
        block = block[:max_chars].rsplit("\n", 1)[0]
    _ACTIVITY_CONTEXT_CACHE = (now, key, block)
    return block


def _clear_activity_context_cache() -> None:
    """Evict cached recent-activity block (used in tests)."""
    global _ACTIVITY_CONTEXT_CACHE
    _ACTIVITY_CONTEXT_CACHE = None


def _strip_ansi(output: str) -> str:
    """Remove ANSI escape codes from a string."""
    import re
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output).strip()


def _run_ostk_boot_sync() -> str:
    """Run ``ostk boot`` synchronously and return the stripped output.

    This is the blocking worker that actually shells out. It is safe to
    call from ``asyncio.to_thread`` or from a non-async context. Never
    call it directly from an async function, it will block the event
    loop for up to five seconds.
    """
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
        return _strip_ansi(output)
    except Exception:
        return ""


def _get_boot_context() -> str:
    """Return cached ``ostk boot`` output. Safe to call from async code.

    The output is injected into the system prompt so the model never has
    to call the shell tool (and ask for approval) to get session context.
    Cached for five minutes.

    Critical safety rule: when called from inside a running asyncio event
    loop and the cache is cold, this returns the stale value (or an empty
    string) and schedules a background refresh. Running ``subprocess.run``
    directly on the event loop would block it for up to five seconds,
    which is exactly long enough to time out a fresh WebSocket handshake
    with ``open_timeout=5``. That was the chat WS handshake deadlock bug.
    """
    global _BOOT_CONTEXT_CACHE, _BOOT_CONTEXT_REFRESH_IN_FLIGHT
    import time as _time
    now = _time.time()
    if _BOOT_CONTEXT_CACHE and now - _BOOT_CONTEXT_CACHE[0] < 300:
        return _BOOT_CONTEXT_CACHE[1]

    # Detect whether we are inside a running asyncio loop. If we are,
    # refuse to block on subprocess.run. Schedule the refresh in a
    # worker thread instead and return the stale cached value.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        if not _BOOT_CONTEXT_REFRESH_IN_FLIGHT:
            _BOOT_CONTEXT_REFRESH_IN_FLIGHT = True

            async def _refresh_and_cache() -> None:
                global _BOOT_CONTEXT_CACHE, _BOOT_CONTEXT_REFRESH_IN_FLIGHT
                try:
                    output = await asyncio.to_thread(_run_ostk_boot_sync)
                    _BOOT_CONTEXT_CACHE = (_time.time(), output)
                finally:
                    _BOOT_CONTEXT_REFRESH_IN_FLIGHT = False

            try:
                loop.create_task(_refresh_and_cache())
            except Exception:
                _BOOT_CONTEXT_REFRESH_IN_FLIGHT = False
        # Return the stale value if we have one, or empty string on cold start.
        return _BOOT_CONTEXT_CACHE[1] if _BOOT_CONTEXT_CACHE else ""

    # Synchronous context: safe to block.
    output = _run_ostk_boot_sync()
    _BOOT_CONTEXT_CACHE = (now, output)
    return output


# Cache the project's CLAUDE.md so repeated chat turns do not re-read
# the file. (path, mtime) -> framed block. Keyed on mtime so edits to
# CLAUDE.md land on the next turn without waiting for a TTL.
_PROJECT_CLAUDE_MD_CACHE: Optional[tuple[str, float, str]] = None
# Cap the injected CLAUDE.md so a stray long file cannot blow the
# context budget. Root CLAUDE.md files in this repo are well under 4KB.
_PROJECT_CLAUDE_MD_MAX_CHARS = 6000


def _project_claude_md_context() -> str:
    """Return the project's ``CLAUDE.md`` framed as a system-prompt block.

    Claude Code (the external CLI the parent agent runs under) auto-loads
    ``CLAUDE.md`` from the workspace root into its system prompt, which
    is how the parent knows about project-specific vocabulary like OAE,
    saa, diagnose, ENTITYFILE, and sigstore. The in-app chat does not
    run through Claude Code, so the same file must be injected here for
    parity. Without it the in-app agent behaves like generic Claude and
    invents guesses for project-specific terms (e.g. reading "Oae
    Verify" in the activity stream as a corruption of "Spec").

    The block is left out entirely when no ``CLAUDE.md`` exists at the
    workspace root, so non-myOS deployments of this code are unaffected.
    Capped at ``_PROJECT_CLAUDE_MD_MAX_CHARS`` to bound context growth.
    """
    global _PROJECT_CLAUDE_MD_CACHE
    path = PROJECT_ROOT / "CLAUDE.md"
    try:
        stat = path.stat()
    except (FileNotFoundError, OSError):
        return ""

    mtime = stat.st_mtime
    key = str(path)
    if _PROJECT_CLAUDE_MD_CACHE is not None:
        cached_key, cached_mtime, cached_text = _PROJECT_CLAUDE_MD_CACHE
        if cached_key == key and cached_mtime == mtime:
            return cached_text

    try:
        raw = path.read_text()
    except OSError:
        return ""

    text = raw.strip()
    if not text:
        _PROJECT_CLAUDE_MD_CACHE = (key, mtime, "")
        return ""

    if len(text) > _PROJECT_CLAUDE_MD_MAX_CHARS:
        text = text[: _PROJECT_CLAUDE_MD_MAX_CHARS].rstrip() + "\n..."

    block = (
        "PROJECT CLAUDE.md (workspace instructions, always apply):\n"
        f"{text}"
    )
    _PROJECT_CLAUDE_MD_CACHE = (key, mtime, block)
    return block


def _clear_project_claude_md_cache() -> None:
    """Evict cached CLAUDE.md block (used in tests)."""
    global _PROJECT_CLAUDE_MD_CACHE
    _PROJECT_CLAUDE_MD_CACHE = None


# Cache: (components_mtime, pages_mtime) -> index string. Invalidated when
# either directory mtime changes (a file is added/removed).
_COMPONENT_INDEX_CACHE: Optional[tuple[float, float, str]] = None
_COMPONENT_INDEX_MAX_CHARS = 2000


def _build_component_index() -> str:
    """Return a compact list of frontend component and page file names.

    The inline Claude starts cold with no codebase map. When the user asks
    about a UI element (e.g. "template card"), Claude has to search broadly
    unless it already knows which file to open. This function injects the
    actual file names from app/src/components/ and app/src/pages/ so the
    model can pick the right file in one call instead of 40 exploratory probes.

    Cached by directory mtime so additions/removals are picked up without a
    restart, but repeated chat turns pay no I/O cost.
    """
    global _COMPONENT_INDEX_CACHE

    components_dir = PROJECT_ROOT / "app" / "src" / "components"
    pages_dir = PROJECT_ROOT / "app" / "src" / "pages"

    try:
        c_mtime = components_dir.stat().st_mtime if components_dir.exists() else 0.0
        p_mtime = pages_dir.stat().st_mtime if pages_dir.exists() else 0.0
    except OSError:
        return ""

    if _COMPONENT_INDEX_CACHE is not None:
        cached_c, cached_p, cached_text = _COMPONENT_INDEX_CACHE
        if cached_c == c_mtime and cached_p == p_mtime:
            return cached_text

    def _list_tsx(directory: "Path") -> list[str]:
        if not directory.exists():
            return []
        try:
            return sorted(
                f.name
                for f in directory.glob("*.tsx")
                if not f.name.endswith(".test.tsx")
            )
        except OSError:
            return []

    components = _list_tsx(components_dir)
    pages = _list_tsx(pages_dir)

    if not components and not pages:
        _COMPONENT_INDEX_CACHE = (c_mtime, p_mtime, "")
        return ""

    parts: list[str] = ["FRONTEND FILE MAP (use these paths directly — no search needed):"]
    if components:
        parts.append("app/src/components/: " + ", ".join(components))
    if pages:
        parts.append("app/src/pages/: " + ", ".join(pages))

    text = "\n".join(parts)
    if len(text) > _COMPONENT_INDEX_MAX_CHARS:
        text = text[:_COMPONENT_INDEX_MAX_CHARS].rstrip() + "\n..."

    _COMPONENT_INDEX_CACHE = (c_mtime, p_mtime, text)
    return text


def _clear_component_index_cache() -> None:
    """Evict cached component index (used in tests)."""
    global _COMPONENT_INDEX_CACHE
    _COMPONENT_INDEX_CACHE = None


def _system_prompt() -> str:
    """Return the static system prompt without boot context.

    Boot context is added separately by ``_build_cached_system_blocks``
    (as its own cached block) or by ``_compose_system_prompt`` (appended
    inline for the Claude Code backend fallback).
    """
    from datetime import datetime, timezone
    import time as _time
    os_name = settings_store.get("os_name", "myOS")
    user_name = settings_store.get("user_name", "")
    owner = user_name if user_name else "the user"
    # Anchor relative dates AND the local timezone so the LLM stops
    # defaulting to Pacific for any datetime it constructs for calendar
    # events. Tori had a "Thursday at 12" end up at 2pm Central because
    # the create_event call went out without a timezone offset and
    # Google assumed Pacific server-side. Inject the server's local
    # timezone (the user's account timezone on this machine) and
    # instruct the model to always attach the matching offset.
    today_utc = datetime.now(timezone.utc)
    today_local = today_utc.astimezone()
    today_iso = today_local.strftime("%Y-%m-%d")
    today_human = today_local.strftime("%A, %B %d, %Y")
    # strftime %z returns a compact "+/-HHMM"; reformat to "+/-HH:MM"
    # which Google Calendar, ISO 8601, and RFC3339 all accept.
    _raw_off = today_local.strftime("%z") or "+0000"
    tz_offset = f"{_raw_off[:3]}:{_raw_off[3:]}" if len(_raw_off) == 5 else _raw_off
    tz_name = _time.tzname[0] if _time.tzname else "local time"
    return (
        f"Today is {today_human} ({today_iso}) in {tz_name} (UTC{tz_offset}). "
        "Use this as the anchor for any relative date in the user's message. "
        "Examples: 'the 28th' means the next occurrence of day-28 from today (this "
        "month if today is <= 28, otherwise next month), 'Friday' means the next "
        "Friday on or after today, 'next week' means the 7-day window starting "
        f"the Monday after today. Never invent a year. If today is "
        f"{today_iso} and the user says 'the 28th' with no year, the year is "
        f"{today_local.year}.\n\n"
        f"TIMEZONE: The user's local timezone is {tz_name} (UTC{tz_offset}). "
        "When you construct any datetime for a calendar event or other "
        "time-sensitive tool call, ALWAYS attach this offset. Example: "
        f"'Thursday at 12pm' becomes '2026-04-23T12:00:00{tz_offset}'. "
        "Never emit a bare datetime without an offset — Google Calendar "
        "and similar services will fall back to Pacific, which is "
        f"wrong for {owner}. Do not call a calendar update tool to fix "
        "timezone after the fact; put the offset in the create call.\n\n"
        f"You are {os_name}, {owner}'s personal operating system. "
        "You have access to tools that let you read files, write files, edit files, "
        "run shell commands, search code, manage tasks, search the web, fetch web pages, "
        "run git operations, spawn background agents, create Google Calendar events, "
        f"send emails via Gmail, delete Gmail messages (move to Trash), and upload files to Google Drive "
        f"in the workspace at {PROJECT_ROOT}. "
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
        "EXPLAIN-PLAIN COMMAND: When the user says 'explain-plain' or its alias "
        "'elit', explain the subject in plain language so someone with no "
        "background in the field can follow it. No jargon. Cover every relevant "
        "point, do not skip material for brevity. Use analogies for technical "
        "or abstract concepts. No code. No em-dashes.\n\n"
        "GOOGLE INTEGRATION: Google Calendar, Gmail, and Drive are connected through myOS Settings, "
        "NOT through Claude Code's MCP integrations. NEVER use mcp__claude_ai_Google_Calendar, "
        "mcp__claude_ai_Gmail, or mcp__claude_ai_Google_Drive tools. NEVER tell the user to "
        "connect via Claude Code Settings or /mcp. Use ONLY the myOS tools listed below:\n"
        "- create_calendar_event: Use when the user says 'add to calendar', 'schedule', "
        "'put on my calendar', or similar. For events without a specific time (field trips, "
        "birthdays), set all_day to true.\n"
        "- send_email: Use when the user says 'send an email', 'email', or 'write to'. "
        "Draft the email text and send it. Confirm what you sent.\n"
        "- delete_emails: Use when the user says 'delete the <X> emails', "
        "'delete emails from <Y>', 'trash the marketing emails', or similar. "
        "ALWAYS a two-step flow. First call with just a 'query' (translate the "
        "user's natural language into Gmail search syntax, for example "
        "'from:amazon marketing' for 'the marketing emails from amazon'). "
        "Show the matching count and list to the user and ask them to confirm. "
        "Only after they say 'yes', 'delete them', 'go ahead', or similar, call "
        "again with confirm=true and the same ids. Default is Trash, which "
        "matches Gmail's Delete button. Pass permanent=true only if the user "
        "explicitly said 'delete forever', 'permanently delete', or 'purge'.\n"
        "- upload_to_drive: Use when the user says 'save to Drive', 'upload to Drive', "
        "or asks to create a document in Drive.\n"
        "- get_calendar_events: Use to check what is on the calendar today.\n"
        "If the user asks to create a calendar event and Google is not connected, tell them "
        "to connect Google in the myOS Settings page: go to Settings, select Google Gemini "
        "as the chat provider, then click 'Sign in with Google'.\n\n"
        "TASK CREATION: When the user says 'create tasks for all of it', 'turn this into tasks', "
        "'break this down into tasks', 'make needles for this', or anything that implies converting "
        "a plan or list into trackable work items, you MUST actually create the tasks, not just "
        "describe them in text. Do NOT list tasks without creating them. "
        "If you have a create_task tool available, call it once per task and report the created IDs. "
        "If you have a create_tasks_from_spec tool and a spec file path is known "
        "(e.g. 'docs/spec/...' or 'docs/draft/...'), call that instead to create all tasks at once. "
        "If you do NOT have a create_task tool (e.g. you only have Bash), use the Bash tool to POST "
        "each task to the myOS API: "
        "curl -sk -X POST https://127.0.0.1:8000/api/tasks "
        "-H 'Content-Type: application/json' "
        "-d '{\"title\": \"<title>\", \"priority\": \"P1\", \"description\": \"<desc>\"}' "
        "Run one curl per task, then report the task IDs from the JSON responses. "
        "When multiple tasks need to be created, create them all in sequence without stopping to ask.\n\n"
        "myOS VOCABULARY: In myOS, a 'spec' means a row on the Specs page (app/src/pages/Specs.tsx). "
        "Users track product specs and requirements there. When the user says 'spec' or 'specs', "
        "they mean a Specs page entry unless they say 'technical spec', 'openapi spec', or similar "
        "that clearly refers to something else. A 'needle' is a task in the ostk task tracker. "
        "'saa' means spawn agent(s). "
        "'tack' means remember this forever. 'nvrfgt' means never forget.\n\n"
        "PATH HINTS: When the user includes a file path in their message (starting with "
        "'file://', '/', '~/', or a relative path like 'docs/'), use that path directly "
        "with the read_file tool first. Do not run a search when the user has already told "
        "you where the file is. Only search if reading the direct path fails.\n\n"
        "INVESTIGATE BEFORE EDITING: When the user's request mentions a UI component, "
        "layout element, or named file (e.g. 'footer', 'header', 'navbar', 'sidebar', "
        "'Layout', 'Footer.tsx', or any React component name), you MUST read the "
        "actual component file before making any edit. Use the FRONTEND FILE MAP at the "
        "top of this prompt to find the exact filename — pick the closest match by name "
        "and call read_file on it directly. Do NOT run list_directory or search when the "
        "file map already shows the filename. Example: user says 'template card' -> "
        "look up 'TemplateCard.tsx' in the file map -> read_file('app/src/components/TemplateCard.tsx') "
        "-> make targeted edits. One read, then edit. Never run exploratory probes.\n\n"
        "Keep your responses brief and focused on outcomes, not process. "
        "Do NOT narrate your steps. Do NOT say 'Let me check' or 'Let me look'. "
        "Just do the work and share the result. "
        "Be action-oriented: read only what you need, then make edits quickly. "
        "Do not over-research. If you know enough to make a change, make it. "
        "TOOL CONSERVATION: For chat questions, use the MINIMUM number of tool calls needed. "
        "A question like 'help plan my week' should take 1-3 tool calls (fetch tasks, fetch calendar, answer), NOT 15. "
        "Do NOT read source code files for planning or advice questions. Do NOT browse directories exploratorily. "
        "Do NOT run multiple searches when one will do. If you can answer from context, just answer. "
        "Only use tools when the user asks for something that requires live data (tasks, calendar, emails, files). "
        "EXCEPTION: tool conservation does NOT apply to pre-edit investigation. Always read the component before editing it. "
        "STEP EFFICIENCY: Each tool call uses one step of your budget. "
        "When you need to inspect a file, use read_file — it returns the full file in one step. "
        "Do NOT use run_command with cat, head, tail, or grep to probe the same file across multiple calls. "
        "Do NOT read the same file twice. Do NOT use list_directory when the file path is already known from the user's message or a prior read. "
        "Prefer search_files over run_command grep for content searches. "
        "Plan reads before acting: one read_file at the right path beats three exploratory probes.\n\n"
        "STEP LIMIT: If you are running low on steps and the task is not yet done, stop and tell the user "
        "what you found. Ask them to narrow the request or give you the exact file path. "
        "Do NOT call spawn_agent to continue an investigation that ran long — spawn agents only when the user "
        "explicitly asks you to run something in the background, not as an escape hatch for a stalled search.\n\n"
        "OSTK TOOLS REQUIRED: Never use the native Grep, Read, Edit, Write, or Bash tools. "
        "They are blocked by the ostk-first hook whenever ostk MCP is available. "
        "Use the MCP tools instead: mcp__ostk__search (replaces Grep/Glob), "
        "mcp__ostk__fs_read (replaces Read), mcp__ostk__edit (replaces Edit), "
        "mcp__ostk__fs_write (replaces Write), mcp__ostk__bash (replaces Bash). "
        "If any of these tools appear deferred, call ToolSearch with "
        "\"select:mcp__ostk__bash,mcp__ostk__fs_read,mcp__ostk__edit,mcp__ostk__search\" "
        "to load them before use. Only fall back to native tools when the ostk MCP server "
        "is genuinely offline (socket at .ostk/ostk.sock is absent). This applies to every "
        "chat turn where tools are enabled.\n\n"
        "ANSWERING QUESTIONS EFFICIENTLY: When the user asks a question that requires "
        "looking up information, use the fewest tool calls needed to get a defensible answer. "
        "One targeted search beats five exploratory ones. Form a precise query first. "
        "Prefer mcp__ostk__search over bash find/grep -- it is faster and audited. "
        "If your first search misses, refine the query; do not broaden the search area. "
        "Stop searching the moment you have a defensible answer. Do not keep looking for "
        "more confirmation. "
        "If you have made 3 or more tool calls and still do not have an answer, state your "
        "best hypothesis and ask the user to confirm or narrow the question. "
        "Do not cascade into new directories, files, or topics just because the first result "
        "was not a perfect match.\n\n"
        "Never use em-dashes. "
        "When the user sends a GIF, do not describe what is in the GIF. They can already see it. "
        "Just react naturally to the sentiment behind it, like you would in a text conversation.\n\n"
        "AUTHORSHIP GROUNDING: When the user asks who added, who built, who wrote, "
        "or why a specific file, component, or feature exists, you MUST verify the "
        "answer with git before responding. Run `git log -n 5 --format='%h %an %s' "
        "-- <path>` (or `git blame <path>`) and cite the real author and commit. "
        "NEVER attribute code to an agent based on its transcript, its activity "
        "row, or the RECENT ACTIVITY block in this prompt. Agent transcripts and "
        "summaries are self-reported and frequently reflect cancelled, failed, or "
        "uncommitted runs. The RECENT ACTIVITY block lists agents that ran but is "
        "not evidence that any code landed. Git is the only source of truth for "
        "authorship. If git log shows the repo owner authored a file, say so "
        "plainly; do not invent an agent as the author. If no path is obvious from "
        "the question, ask the user which file they mean before answering."
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
    # custom template. Built-in templates (saa, diagnose, explain-plain) all reach
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


def _standing_instructions_block() -> str:
    """Return the user's saved standing instructions, framed for the model.

    Standing instructions are a free-form block the user writes once in
    Settings. They apply to every chat turn, agent spawn, and task so the
    AI picks up tone, tool preferences, and house rules without the user
    having to repeat them. Returns an empty string when the setting is
    blank so callers can no-op prepend safely.
    """
    try:
        value = settings_store.get("standing_instructions", "")
    except Exception:
        return ""
    text = str(value or "").strip()
    if not text:
        return ""
    return (
        "STANDING INSTRUCTIONS (from the user, always apply):\n"
        f"{text}"
    )


def _user_memory_block() -> str:
    """Return the user memory markdown block for system-prompt injection.

    Reads ~/.myos/users/default/MEMORY.md via the mtime-cached store.
    Returns an empty string when the file is absent or empty (no error logged).
    """
    content = _user_memory_store.read().strip()
    if not content:
        return ""
    return f"## User preferences and facts\n\n{content}"


def _compose_system_prompt(matched_template: Optional[dict]) -> str:
    """Return the full system prompt as a single string.

    Used by the Claude Code backend fallback where we cannot split into
    separate cached blocks. Includes boot context and recent activity inline.
    """
    base = _system_prompt()
    component_index = _build_component_index()
    if component_index:
        base = component_index + "\n\n" + base
    claude_md = _project_claude_md_context()
    if claude_md:
        base = claude_md + "\n\n" + base
    standing = _standing_instructions_block()
    if standing:
        base = standing + "\n\n" + base
    boot_context = _get_boot_context()
    if boot_context:
        base += f"\n\nSESSION CONTEXT (from `ostk boot`, already run, do not run again):\n{boot_context}\n"
    activity = _recent_activity_context()
    if activity:
        base += f"\n\n{activity}\n"
    user_memory = _user_memory_block()
    if user_memory:
        base += f"\n\n{user_memory}\n"
    if not matched_template:
        return base
    extra = str(matched_template.get("prompt") or "").strip()
    if not extra:
        return base
    return base + "\n\n---\nACTIVE TEMPLATE: " + str(matched_template.get("name", "")) + "\n" + extra


def _build_cached_system_blocks(matched_template: Optional[dict]) -> list[dict]:
    """Build system prompt as separate cached blocks.

    Splits the system prompt into a stable instructions block (cached)
    and a volatile block containing boot context and recent activity
    (separate). This way the large, mostly-static instructions stay cached
    even when needle counts, fleet status, or recent activity changes between
    turns.

    The Anthropic API supports up to 4 cache breakpoints. We use 2 here
    (instructions + volatile context) leaving 2 for conversation prefix caching.
    """
    # Static instructions block. This text rarely changes, so it stays
    # cached across many turns and even across conversations within the
    # 5-minute TTL.
    base = _system_prompt()
    component_index = _build_component_index()
    if component_index:
        base = component_index + "\n\n" + base
    claude_md = _project_claude_md_context()
    if claude_md:
        base = claude_md + "\n\n" + base
    standing = _standing_instructions_block()
    if standing:
        base = standing + "\n\n" + base
    if matched_template:
        extra = str(matched_template.get("prompt") or "").strip()
        if extra:
            base += "\n\n---\nACTIVE TEMPLATE: " + str(matched_template.get("name", "")) + "\n" + extra

    boot_context = _get_boot_context()
    activity = _recent_activity_context()
    user_memory = _user_memory_block()

    volatile_parts: list[str] = []
    if boot_context:
        volatile_parts.append(
            f"SESSION CONTEXT (from `ostk boot`, already run, do not run again):\n{boot_context}"
        )
    if activity:
        volatile_parts.append(activity)
    if user_memory:
        volatile_parts.append(user_memory)

    if not volatile_parts:
        return [
            {
                "type": "text",
                "text": base,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    # Split: static instructions (cached) + volatile context (cached
    # separately so a boot/activity change does not bust the instructions cache).
    return [
        {
            "type": "text",
            "text": base,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "\n\n".join(volatile_parts),
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _no_tools_system_blocks(matched_template: Optional[dict]) -> list[dict]:
    """System prompt used on broadcast (All pill) turns for Claude.

    The full ``_system_prompt`` is heavy with tool instructions
    ("create_calendar_event", "spawn_agent", "PREFER OSTK OVER RAW
    SHELL", etc). When broadcast routes Claude through the direct
    Anthropic API with no ``tools=`` parameter, those tool mentions
    make the model emit XML-style tool calls as plain text
    (`<function_calls><invoke name="...">`) that stream straight
    into the user-visible bubble. This function returns a minimal
    no-tools system prompt so Claude answers the user directly.

    Keeps the bare essentials: identity, date, tone, and the
    user's standing instructions / matched template prompt.
    """
    from datetime import datetime, timezone

    today_utc = datetime.now(timezone.utc)
    today_local = today_utc.astimezone()
    today_human = today_local.strftime("%A, %B %d, %Y")
    today_iso = today_local.strftime("%Y-%m-%d")

    os_name = settings_store.get("os_name", "myOS")
    user_name = settings_store.get("user_name", "")
    owner = user_name if user_name else "the user"

    base = (
        f"Today is {today_human} ({today_iso}).\n\n"
        f"You are {os_name}, {owner}'s personal operating system. "
        "You have NO tools available on this turn. Respond to the "
        "user directly in plain text. Do not attempt to call any "
        "tool. Do not emit XML-style tool tags "
        "(<function_calls>, <invoke>, <parameter>, <tool_use>, "
        "etc). Do not describe or plan tool calls. If the user's "
        "request would need a tool, explain briefly what you would "
        "do and let them know you cannot run it on this turn.\n\n"
        "Keep answers brief and conversational. Never use em-dashes. "
        "Write in plain language with no jargon."
    )

    standing = _standing_instructions_block()
    if standing:
        base = standing + "\n\n" + base
    if matched_template:
        extra = str(matched_template.get("prompt") or "").strip()
        if extra:
            base += (
                "\n\n---\nACTIVE TEMPLATE: "
                + str(matched_template.get("name", ""))
                + "\n"
                + extra
            )
    return [
        {
            "type": "text",
            "text": base,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _strip_tool_blocks_from_messages(messages: list[dict]) -> list[dict]:
    """Remove ``tool_use`` / ``tool_result`` blocks from prior turns.

    On a broadcast turn we pass ``disable_tools=True`` and do not
    register any tools with the API. If the conversation history
    contains an earlier assistant turn where Claude DID call a
    tool, those blocks remain in the message list. The model sees
    its own prior tool calls in context and will happily continue
    the pattern, emitting XML tool calls as plain text.

    This strips any ``tool_use`` block from assistant messages and
    drops any message whose role is ``tool`` or whose content is
    a pure ``tool_result`` list. Assistant messages that had a
    text block plus a tool_use block keep the text block.
    """
    cleaned: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        # Anthropic never uses role="tool" on the wire (tool
        # results are user messages with a tool_result block),
        # but some provider shims do. Strip defensively.
        if role == "tool":
            continue
        if isinstance(content, list):
            filtered_blocks: list = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype in ("tool_use", "tool_result"):
                        continue
                filtered_blocks.append(block)
            if not filtered_blocks:
                # Whole message was tool-only; drop it so the API
                # does not reject an empty-content message.
                continue
            cleaned.append({**m, "content": filtered_blocks})
        else:
            cleaned.append(m)
    return cleaned


_WIRE_MESSAGE_ALLOWED_KEYS: frozenset[str] = frozenset({"role", "content"})


def _sanitize_messages_for_wire(messages: list[dict]) -> list[dict]:
    """Strip any keys that Anthropic's Messages API does not allow.

    The frontend attaches extra fields to message dicts for rendering
    (e.g. ``model`` to track which AI produced a bubble, ``image`` for
    pasted screenshots). Anthropic's API only accepts ``role`` and
    ``content`` on each message object. Any other key produces:
        400 - {'type':'error','error':{'type':'invalid_request_error',
               'message':'messages.N.model: Extra inputs are not permitted'}}

    This function must be called on every messages list before it is
    sent to any Anthropic API endpoint.
    """
    result = []
    for m in messages:
        wire_msg: dict = {k: v for k, v in m.items() if k in _WIRE_MESSAGE_ALLOWED_KEYS}
        result.append(wire_msg)
    return result


def _add_conversation_prefix_cache(messages: list[dict]) -> list[dict]:
    """Add a cache breakpoint to the conversation prefix.

    Marks the second-to-last message with cache_control so the entire
    conversation history up to the previous turn is served from cache
    on the next API call. Only the new message is billed at full price.

    Returns a shallow copy with the cache marker injected. The original
    list is not mutated.
    """
    if len(messages) < 2:
        return messages

    result = list(messages)
    # The prefix boundary is the message just before the latest one.
    target_idx = len(result) - 2
    target = result[target_idx]
    content = target.get("content")

    if isinstance(content, str) and content.strip():
        result[target_idx] = {
            **target,
            "content": [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    elif isinstance(content, list) and len(content) > 0:
        # Add cache_control to the last non-empty text block in the content list.
        new_content = list(content)
        for i in range(len(new_content) - 1, -1, -1):
            block = new_content[i]
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text", "").strip():
                new_content[i] = {**block, "cache_control": {"type": "ephemeral"}}
                break
        result[target_idx] = {**target, "content": new_content}

    return result


_anthropic_log = logging.getLogger("myos.chat.anthropic")


class ChatService:
    async def stream_anthropic(self, messages: list[dict], websocket: WebSocket, tab_id: str = "", disable_tools: bool = False, force_api: bool = False, _fallback_model: Optional[str] = None, claude_tier: str = "") -> str:
        # Run template matching up front so both backends pick up any
        # matched helper. The matcher itself uses the API key when one is
        # available, but it also handles the no-key case gracefully.
        import time as _time
        _t0 = _time.perf_counter()
        api_key = await _resolve_api_key("anthropic_api_key")

        # Resolve backend and send backend_active BEFORE template matching.
        # The AI classifier in _maybe_match_template makes a blocking
        # Anthropic call that can stall 30+ seconds when the API is slow,
        # preventing any event from reaching the frontend and causing the
        # dead-backend timer to fire. Sending backend_active first clears
        # that timer immediately so the UI stays responsive.
        backend = await _resolve_chat_backend()

        # Broadcast ("All" pill) path: skip the Claude Code CLI in favor
        # of the direct Anthropic API when a key is available. The CLI
        # subprocess adds several seconds of first-token latency because
        # of shell spin-up and streaming framing, which makes broadcast
        # feel serial even though the two providers run under
        # asyncio.gather. Direct API streaming puts Claude's first token
        # within the same second as Gemini's in practice. Falls through
        # to whatever the normal backend is if no key is configured.
        if force_api and backend == "claude_code":
            if api_key:
                backend = "anthropic_api"

        # Claude Code CLI cannot receive inline image blocks. If the last
        # message includes an image (GIF or pasted screenshot), force the
        # Anthropic API backend so the model can actually see it.
        if backend == "claude_code" and _messages_contain_images(messages):
            if api_key:
                backend = "anthropic_api"

        await _send_backend_active(websocket, backend)

        matched_template = await _maybe_match_template(messages, websocket, api_key)
        _t_template = _time.perf_counter()
        _anthropic_log.info(
            "anthropic_phase=template_matched ms=%.0f matched=%s",
            (_t_template - _t0) * 1000,
            bool(matched_template),
        )
        # Always compose the system prompt when the user has standing
        # instructions saved, even if no template matched, so the Claude
        # Code fallback still picks them up.
        if matched_template or _standing_instructions_block():
            system_prompt = _compose_system_prompt(matched_template)
        else:
            system_prompt = None

        if backend == "claude_code":
            return await claude_code_provider.stream_chat(
                messages,
                websocket,
                system_prompt=system_prompt,
                tab_id=tab_id,
                disable_tools=disable_tools,
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

        client = _get_anthropic_client(api_key)
        # Label assistant messages from other models so Claude knows
        # which responses are Gemini's vs its own. Also strip any
        # non-API keys (the frontend attaches a ``model`` field to each
        # message for rendering, but Anthropic's Messages API rejects
        # unknown fields with "Extra inputs are not permitted").
        labeled = []
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role == "assistant" and isinstance(content, str):
                source = (m.get("model") or "").lower()
                if source and "gemini" in source:
                    content = f"[Gemini's response]: {content}"
            labeled.append({"role": role, "content": content})
        # On broadcast ("All pill") turns we call the API with NO
        # tools registered. If the history carries ``tool_use`` or
        # ``tool_result`` blocks from an earlier tool-using turn,
        # Claude will continue the tool-call pattern and the XML
        # leaks into the user-visible bubble as literal text. Strip
        # those blocks before sending.
        if disable_tools:
            labeled = _strip_tool_blocks_from_messages(labeled)
        cached_messages = _add_conversation_prefix_cache(labeled)
        _CLAUDE_TIER_MODELS = {
            "haiku": "claude-haiku-4-5-20251001",
            "sonnet": "claude-sonnet-4-6",
            "opus": "claude-opus-4-7",
        }
        _model_id = _fallback_model or _CLAUDE_TIER_MODELS.get(claude_tier, "claude-sonnet-4-6")
        stream_kwargs: dict = {
            "model": _model_id,
            "max_tokens": 4096,
            "messages": cached_messages,
        }
        # Use split system blocks so stable instructions stay cached
        # even when volatile boot context changes between turns.
        # Broadcast path (disable_tools=True) uses a minimal no-tools
        # system prompt so Claude never generates XML tool calls as
        # plain text. Sharing the regular prompt here caused Tori's
        # "Pepper - Magic Show Field Trip" bubble to stream literal
        # `<function_calls>` markup.
        if disable_tools:
            stream_kwargs["system"] = _no_tools_system_blocks(matched_template)
        else:
            stream_kwargs["system"] = _build_cached_system_blocks(matched_template)

        # Enable extended thinking for complex questions so the model
        # can reason through multi-step problems before answering.
        last_user_text = _extract_last_user_text(messages)
        use_thinking = _should_use_thinking(last_user_text)
        if use_thinking:
            stream_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": EXTENDED_THINKING_BUDGET_TOKENS,
            }
            # Extended thinking requires max_tokens to cover both
            # thinking and response. Bump it so the budget fits.
            stream_kwargs["max_tokens"] = max(
                stream_kwargs["max_tokens"],
                EXTENDED_THINKING_BUDGET_TOKENS + 4096,
            )

        full_text = ""
        _t_payload = _time.perf_counter()
        _anthropic_log.info(
            "anthropic_phase=payload_built ms=%.0f thinking=%s msgs=%d",
            (_t_payload - _t_template) * 1000,
            use_thinking,
            len(cached_messages),
        )
        _first_token_logged = [False]

        async def _run_stream_once() -> Any:
            """Open the stream, pump tokens to the websocket, and return usage.

            Defined as a local coroutine so ``_anthropic_retry_call`` can
            retry the whole attempt cleanly if Anthropic returns a 5xx
            during stream setup and no tokens have been forwarded yet.

            When extended thinking is enabled, handles ``thinking`` type
            content blocks by sending ``{"type": "thinking", ...}`` events
            so the frontend can show a thinking indicator.
            """
            nonlocal full_text
            if use_thinking:
                # With thinking enabled we need to iterate raw stream
                # events to capture both thinking and text blocks.
                async with client.messages.stream(**stream_kwargs) as stream:
                    async for event in stream:
                        if hasattr(event, "type"):
                            if event.type == "content_block_start":
                                block = getattr(event, "content_block", None)
                                if block and getattr(block, "type", "") == "thinking":
                                    await websocket.send_json({
                                        "type": "thinking",
                                        "data": True,
                                    })
                            elif event.type == "content_block_delta":
                                delta = getattr(event, "delta", None)
                                if delta:
                                    delta_type = getattr(delta, "type", "")
                                    if delta_type == "thinking_delta":
                                        thinking_text = getattr(delta, "thinking", "")
                                        if thinking_text:
                                            await websocket.send_json({
                                                "type": "thinking",
                                                "data": thinking_text,
                                            })
                                    elif delta_type == "text_delta":
                                        text = getattr(delta, "text", "")
                                        if text:
                                            if not _first_token_logged[0]:
                                                _anthropic_log.info(
                                                    "anthropic_phase=first_token ms=%.0f",
                                                    (_time.perf_counter() - _t0) * 1000,
                                                )
                                                _first_token_logged[0] = True
                                            full_text += text
                                            await websocket.send_json({
                                                "type": "token",
                                                "data": text,
                                            })
                    return await stream.get_final_message()
            else:
                # →1355/→1354: switch from stream.text_stream to raw event
                # iteration so we can detect text-block boundaries.
                #
                # stream.text_stream is a convenience iterator that yields
                # text from ALL text blocks in sequence with NO separator.
                # When a response has [text_block] [tool_use] [text_block]
                # the two text chunks arrive back-to-back with no space or
                # newline, smashing sentences together and breaking code
                # fences that rely on being on their own line.
                #
                # By watching content_block_start / content_block_stop we
                # know when one text block ends and a new one begins (with
                # a non-text block in between).  At that boundary we emit a
                # "\n\n" token so the frontend accumulates a proper
                # paragraph separator.
                async with client.messages.stream(**stream_kwargs) as stream:
                    _in_text_block = False
                    _had_text_block = False
                    async for event in stream:
                        if not hasattr(event, "type"):
                            continue
                        if event.type == "content_block_start":
                            block = getattr(event, "content_block", None)
                            if block and getattr(block, "type", "") == "text":
                                # Starting a new text block.  If a previous
                                # text block already ended (and something
                                # non-text came between), inject a separator.
                                if _had_text_block and not _in_text_block:
                                    sep = "\n\n"
                                    full_text += sep
                                    await websocket.send_json(
                                        {"type": "token", "data": sep}
                                    )
                                _in_text_block = True
                        elif event.type == "content_block_stop":
                            if _in_text_block:
                                _had_text_block = True
                                _in_text_block = False
                        elif event.type == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta and getattr(delta, "type", "") == "text_delta":
                                text = getattr(delta, "text", "")
                                if text:
                                    if not _first_token_logged[0]:
                                        _anthropic_log.info(
                                            "anthropic_phase=first_token ms=%.0f",
                                            (_time.perf_counter() - _t0) * 1000,
                                        )
                                        _first_token_logged[0] = True
                                    full_text += text
                                    await websocket.send_json(
                                        {"type": "token", "data": text}
                                    )
                    return await stream.get_final_message()

        try:
            # Retry ONLY while no tokens have been emitted. Once the
            # stream starts sending text, retrying would re-emit the
            # beginning of the response and confuse the chat panel.
            if full_text:
                response = await _run_stream_once()
            else:
                # Wrap the stream with a heartbeat so the socket stays
                # warm during extended-thinking or tool-use stalls that
                # emit no tokens for 30+ seconds. Without this, the
                # browser or vite proxy closes the idle socket and the
                # chat panel surfaces a "Connection dropped" error.
                response = await _with_ws_heartbeat(
                    websocket,
                    lambda: _anthropic_retry_call(
                        _run_stream_once,
                        op_name="anthropic.messages.stream",
                    ),
                )
            _cache_creation = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            _cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            _anthropic_log.info(
                "anthropic_phase=stream_complete ms=%.0f chars=%d cache_read=%d",
                (_time.perf_counter() - _t0) * 1000,
                len(full_text),
                _cache_read,
            )
            await websocket.send_json({
                "type": "done",
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cache_creation_input_tokens": _cache_creation,
                    "cache_read_input_tokens": _cache_read,
                }
            })
            _boot_ctx = _get_boot_context()
            safe_record_chat_turn(
                model="claude-sonnet-4-20250514",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                has_ostk_boot=bool(_boot_ctx),
                boot_context_bytes=len(_boot_ctx.encode("utf-8")) if _boot_ctx else 0,
                backend="anthropic_api",
                cache_creation_input_tokens=_cache_creation,
                cache_read_input_tokens=_cache_read,
            )
            _log_chat_completion(
                model="claude-sonnet-4-20250514",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                provider="anthropic",
                cache_creation_input_tokens=_cache_creation,
                cache_read_input_tokens=_cache_read,
                topic=_extract_chat_topic(messages),
            )
        except anthropic.APIStatusError as e:
            http_status = getattr(e, "status_code", None)
            if http_status is not None and 500 <= int(http_status) < 600:
                # Retries were already attempted inside
                # ``_anthropic_retry_call``. Surface a plain-language
                # error instead of the raw JSON body.
                await _send_friendly_anthropic_error(websocket, e)
            elif http_status is not None and int(http_status) == 429:
                if _fallback_model:
                    # Already running on the fallback model. Don't recurse.
                    await websocket.send_json({
                        "type": "error",
                        "category": "quota_exceeded",
                        "data": "Quota exceeded right now. Try again later or switch model.",
                    })
                else:
                    full_text = await _handle_anthropic_rate_limit(self, messages, websocket)
            else:
                # Other 4xx: classify and surface a user-readable message.
                _c = _classify_anthropic_error(e)
                await websocket.send_json({
                    "type": "error",
                    "category": _c["category"],
                    "data": _c["user_message"],
                })
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            await _send_friendly_anthropic_error(websocket, e)
        except anthropic.APIError as e:
            _c = _classify_anthropic_error(e)
            await websocket.send_json({
                "type": "error",
                "category": _c["category"],
                "data": _c["user_message"],
            })

        return full_text

    def _get_mcp_servers(self) -> list[dict]:
        """Return enabled MCP server configs from settings."""
        servers = settings_store.get("mcp_servers", [])
        if not isinstance(servers, list):
            return []
        return [s for s in servers if isinstance(s, dict) and s.get("enabled", True) and s.get("url")]

    async def agent_anthropic(self, messages: list[dict], websocket: WebSocket, tab_id: str = "") -> str:
        """Run the Anthropic agent loop with tool use.

        Sends messages with tool definitions, executes any tool calls Claude
        makes, feeds results back, and repeats until Claude responds with
        just text (no more tool calls). Then streams that final text response.

        When MCP servers are configured in settings, uses the Anthropic beta
        MCP client API. Anthropic fetches tools from those servers and executes
        MCP tool calls server-side, returning results inline in the response.
        """
        api_key = await _resolve_api_key("anthropic_api_key")

        # Resolve backend and send backend_active BEFORE template matching.
        # Same fix as stream_anthropic: the classifier can stall 30+ seconds
        # on a slow API, blocking backend_active and firing the frontend timer.
        backend = await _resolve_chat_backend()

        # Claude Code CLI cannot receive inline image blocks. If the last
        # message includes an image (GIF or pasted screenshot), force the
        # Anthropic API backend so the model can actually see it.
        if backend == "claude_code" and _messages_contain_images(messages):
            if api_key:
                backend = "anthropic_api"

        await _send_backend_active(websocket, backend)

        # Auto-match agent template based on the user's message. If matched,
        # the system prompt picks up the template's extra instructions and
        # the chat panel shows a small "Using: <name>" badge.
        matched_template = await _maybe_match_template(messages, websocket, api_key)
        # Use split system blocks so stable instructions stay cached
        # even when volatile boot context changes between turns.
        cached_system_prompt = _build_cached_system_blocks(matched_template)
        active_system_prompt = _compose_system_prompt(matched_template)

        # With session mode, the local program handles tools natively
        # via --dangerously-skip-permissions. The tab_id enables session
        # persistence so the model has full conversation context.
        if backend == "claude_code":
            return await claude_code_provider.stream_chat(
                messages, websocket, system_prompt=active_system_prompt, tab_id=tab_id
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

        client = _get_anthropic_client(api_key)
        conversation: list[dict] = _sanitize_messages_for_wire(messages)
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_creation_tokens = 0
        total_cache_read_tokens = 0
        mcp_servers = self._get_mcp_servers()
        use_mcp = len(mcp_servers) > 0

        try:
            turn = 0
            WARN_AT = 15
            files_modified: set[str] = set()
            while True:
                turn += 1
                # Stop check runs first so WARN_AT can never fire on the same
                # turn as the cap — prevents a contradictory "Still working" /
                # "Stopped after" pair in the same response bubble.
                if turn > MAX_AGENT_TURNS:
                    if files_modified:
                        file_list = ", ".join(sorted(files_modified))
                        msg = (
                            f"I've used {MAX_AGENT_TURNS} steps and made changes to: {file_list}. "
                            "Tell me if you'd like me to continue or review what I changed."
                        )
                    else:
                        msg = (
                            f"I've used {MAX_AGENT_TURNS} steps on this and haven't finished. "
                            "To pick it up: tell me the exact file or component you want changed and I'll go straight there."
                        )
                    await websocket.send_json({"type": "token", "data": msg})
                    await websocket.send_json({
                        "type": "done",
                        "usage": {
                            "input_tokens": total_input_tokens,
                            "output_tokens": total_output_tokens,
                            "cache_creation_input_tokens": total_cache_creation_tokens,
                            "cache_read_input_tokens": total_cache_read_tokens,
                        },
                    })
                    _boot_ctx = _get_boot_context()
                    safe_record_chat_turn(
                        model="claude-sonnet-4-20250514",
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        has_ostk_boot=bool(_boot_ctx),
                        boot_context_bytes=len(_boot_ctx.encode("utf-8")) if _boot_ctx else 0,
                        backend="anthropic_api",
                        cache_creation_input_tokens=total_cache_creation_tokens,
                        cache_read_input_tokens=total_cache_read_tokens,
                    )
                    _log_chat_completion(
                        model="claude-sonnet-4-20250514",
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        provider="anthropic",
                        cache_creation_input_tokens=total_cache_creation_tokens,
                        cache_read_input_tokens=total_cache_read_tokens,
                        topic=_extract_chat_topic(messages),
                    )
                    return msg

                if turn == WARN_AT:
                    await websocket.send_json({
                        "type": "step_progress",
                        "data": {"step": turn, "max_steps": MAX_AGENT_TURNS},
                    })

                # Signal the frontend that we're working
                await websocket.send_json({"type": "thinking", "data": True})

                # Cache the conversation prefix so prior turns are served
                # from cache on each new API call. Only the latest tool
                # results and new messages are billed at full price.
                cached_conversation = _add_conversation_prefix_cache(conversation)

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

                    async def _mcp_create() -> Any:
                        return await client.beta.messages.create(
                            model="claude-sonnet-4-20250514",
                            max_tokens=4096,
                            system=cached_system_prompt,
                            messages=cached_conversation,
                            tools=TOOL_DEFINITIONS,  # type: ignore[arg-type]
                            mcp_servers=mcp_server_params,  # type: ignore[arg-type]
                            betas=["mcp-client-2025-04-04"],
                        )

                    response = await _with_ws_heartbeat(
                        websocket,
                        lambda: _anthropic_retry_call(
                            _mcp_create,
                            op_name="anthropic.beta.messages.create",
                        ),
                    )
                else:
                    async def _create() -> Any:
                        return await client.messages.create(
                            model="claude-sonnet-4-20250514",
                            max_tokens=4096,
                            system=cached_system_prompt,
                            messages=cached_conversation,
                            tools=TOOL_DEFINITIONS,
                        )

                    response = await _with_ws_heartbeat(
                        websocket,
                        lambda: _anthropic_retry_call(
                            _create,
                            op_name="anthropic.messages.create",
                        ),
                    )

                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens
                total_cache_creation_tokens += getattr(response.usage, "cache_creation_input_tokens", 0) or 0
                total_cache_read_tokens += getattr(response.usage, "cache_read_input_tokens", 0) or 0

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
                            "cache_creation_input_tokens": total_cache_creation_tokens,
                            "cache_read_input_tokens": total_cache_read_tokens,
                        },
                    })
                    _boot_ctx = _get_boot_context()
                    safe_record_chat_turn(
                        model="claude-sonnet-4-20250514",
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        has_ostk_boot=bool(_boot_ctx),
                        boot_context_bytes=len(_boot_ctx.encode("utf-8")) if _boot_ctx else 0,
                        backend="anthropic_api",
                        cache_creation_input_tokens=total_cache_creation_tokens,
                        cache_read_input_tokens=total_cache_read_tokens,
                    )
                    _log_chat_completion(
                        model="claude-sonnet-4-20250514",
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        provider="anthropic",
                        topic=_extract_chat_topic(messages),
                    )
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
                    else:
                        # Track successful file mutations so the cap-hit message
                        # can report what actually changed instead of "haven't finished".
                        if block.name in ("edit_file", "write_file"):
                            path_arg = dict(block.input).get("path", "")
                            if path_arg and not result.startswith("Error"):
                                files_modified.add(str(path_arg))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                conversation.append({"role": "user", "content": tool_results})

            # Loop exits naturally when Claude responds with text only (no tool calls)

        except anthropic.APIStatusError as e:
            http_status = getattr(e, "status_code", None)
            if http_status is not None and 500 <= int(http_status) < 600:
                # Retries were already attempted inside
                # ``_anthropic_retry_call``. Surface a plain-language
                # error instead of the raw JSON body.
                await _send_friendly_anthropic_error(websocket, e)
            elif http_status is not None and int(http_status) == 429:
                # Rate-limited. Try Gemini if a key is available.
                return await _handle_anthropic_rate_limit(self, messages, websocket)
            else:
                # Other 4xx: classify and surface a user-readable message.
                _c = _classify_anthropic_error(e)
                await websocket.send_json({
                    "type": "error",
                    "category": _c["category"],
                    "data": _c["user_message"],
                })
            return ""
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            await _send_friendly_anthropic_error(websocket, e)
            return ""
        except anthropic.APIError as e:
            _c = _classify_anthropic_error(e)
            await websocket.send_json({
                "type": "error",
                "category": _c["category"],
                "data": _c["user_message"],
            })
            return ""

    async def _stream_gemini_vertex(
        self,
        messages: list[dict],
        websocket: WebSocket,
        project: str,
        location: str,
        datastore: Optional[str] = None,
        system_instruction: Optional[str] = None,
    ) -> str:
        """Stream Gemini via Vertex AI (Application Default Credentials).

        Imports vertexai inside the method so the library not being installed
        does not affect other import paths. Uses the same merged-history shape
        as stream_gemini.
        """
        import asyncio as _asyncio

        history = []
        for msg in messages[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            text = _gemini_content_to_text(msg.get("content", ""))
            if role == "model":
                source = (msg.get("model") or "").lower()
                if source and "claude" in source:
                    text = f"[Claude's response]: {text}"
                elif source and "gemini" in source:
                    text = f"[Your previous response]: {text}"
            history.append({"role": role, "parts": [{"text": text}]})

        merged_history: list[dict] = []
        for entry in history:
            if merged_history and merged_history[-1]["role"] == entry["role"]:
                prev_texts = [
                    p["text"] for p in merged_history[-1].get("parts", [])
                    if isinstance(p, dict) and p.get("text")
                ]
                new_texts = [
                    p["text"] for p in entry.get("parts", [])
                    if isinstance(p, dict) and p.get("text")
                ]
                combined = "\n\n".join(prev_texts + new_texts)
                merged_history[-1] = {"role": entry["role"], "parts": [{"text": combined}]}
            else:
                merged_history.append(entry)

        if merged_history and merged_history[-1]["role"] == "user":
            merged_history.append({
                "role": "model",
                "parts": [{"text": "Got it. What would you like to know?"}],
            })

        last_text = _gemini_content_to_text(messages[-1].get("content", ""))
        contents = merged_history + [{"role": "user", "parts": [{"text": last_text}]}]

        full_text = ""
        citations: list[dict] = []

        try:
            def _init_and_get_stream():
                import vertexai
                from vertexai.generative_models import (
                    GenerativeModel,
                    Tool,
                    grounding as _grounding,
                )
                vertexai.init(project=project, location=location)
                _tools = None
                if datastore:
                    _tools = [Tool.from_retrieval(
                        _grounding.Retrieval(
                            _grounding.VertexAISearch(datastore=datastore)
                        )
                    )]
                _model = GenerativeModel(_gemini_model_name(), tools=_tools)
                return _model.generate_content(contents, stream=True), _tools

            response_stream, active_tools = await _asyncio.to_thread(_init_and_get_stream)

            _CHUNK_STOP = object()
            _chunk_iter = iter(response_stream)

            def _pull_next():
                try:
                    return next(_chunk_iter)
                except StopIteration:
                    return _CHUNK_STOP

            while True:
                chunk = await _asyncio.to_thread(_pull_next)
                if chunk is _CHUNK_STOP:
                    break
                try:
                    text = chunk.text
                except (ValueError, AttributeError):
                    text = None
                if text:
                    full_text += text
                    await websocket.send_json({"type": "token", "data": text})

                if active_tools is not None:
                    try:
                        for candidate in (getattr(chunk, "candidates", None) or []):
                            gm = getattr(candidate, "grounding_metadata", None)
                            if not gm:
                                continue
                            for gc in (getattr(gm, "grounding_chunks", None) or []):
                                web = getattr(gc, "web", None)
                                uri = getattr(web, "uri", None) if web else None
                                title = getattr(web, "title", None) if web else None
                                if uri and uri not in {c.get("uri") for c in citations}:
                                    citations.append({"uri": uri, "title": title or uri})
                    except Exception:
                        pass

            if citations:
                await websocket.send_json({"type": "citations", "data": citations})

            if not full_text:
                await websocket.send_json({
                    "type": "error",
                    "data": "Vertex AI returned an empty response. Please try again.",
                })
                return full_text

            await websocket.send_json({"type": "done"})

        except WebSocketDisconnect:
            pass
        except Exception as e:
            friendly = _friendly_gemini_error(str(e))
            try:
                await websocket.send_json({"type": "error", "data": friendly})
            except Exception:
                pass

        return full_text

    async def stream_gemini(
        self,
        messages: list[dict],
        websocket: WebSocket,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        # Priority 0: Gemini CLI (if enabled and available).
        if settings_store.get("use_gemini_cli") and await gemini_cli_provider.is_gemini_cli_available():
            try:
                return await gemini_cli_provider.stream_chat(
                    messages, websocket, system_prompt=system_instruction, **kwargs
                )
            except Exception as e:
                _gemini_log.warning(f"Gemini CLI failed, falling back to API: {e}")
                # Fall through to API logic below

        # Priority 1: Vertex AI via Application Default Credentials.
        # detect_vertex_gemini() checks gcloud ADC and never raises.
        # Imported here to avoid a circular-import at module load time.
        import os
        from .provider_detection import detect_vertex_gemini
        vx = detect_vertex_gemini()
        if vx.get("available"):
            datastore = os.environ.get("VERTEX_SEARCH_DATASTORE", "") or None
            return await self._stream_gemini_vertex(
                messages, websocket, vx["project"], vx["location"],
                datastore=datastore, system_instruction=system_instruction,
            )

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
            import asyncio as _asyncio_init
            import time as _time
            _t0 = _time.monotonic()
            # The google-generativeai import and configure step do
            # grpc/ssl/auth warmup that can take 5 to 30 seconds on a
            # cold backend. Running them on the event loop freezes every
            # other request for that window. Push them onto a worker
            # thread so the event loop stays responsive. Any exception
            # surfaces the same way through the outer except.
            def _prepare_gemini_client():
                from google import genai as _genai_mod
                _model_name = _gemini_model_name()
                cache_key = (api_key, _model_name)
                cached = _GEMINI_CLIENT_CACHE.get(cache_key)
                if cached is not None:
                    # Return the cached client. Client(api_key=...) is skipped,
                    # saving 2-8s of gRPC/HTTP transport re-initialization on
                    # every message after the first in a session.
                    return cached
                _client = _genai_mod.Client(api_key=api_key)
                # Store (model_name, client). The genai module reference is NOT
                # cached so that tests which swap sys.modules still see the
                # currently-active module for exception types and protos.
                result = (_model_name, _client)
                _GEMINI_CLIENT_CACHE[cache_key] = result
                return result

            try:
                model_name, model = await _asyncio_init.wait_for(
                    _asyncio_init.to_thread(_prepare_gemini_client),
                    timeout=_GEMINI_CLIENT_READY_TIMEOUT_S,
                )
            except _asyncio_init.TimeoutError:
                await _send_friendly_gemini_error(
                    websocket,
                    (
                        f"Gemini did not finish starting up in "
                        f"{int(_GEMINI_CLIENT_READY_TIMEOUT_S)} seconds. "
                        "This usually means the network or Google's API is "
                        "slow. Please try again."
                    ),
                    reason_name="CLIENT_READY_TIMEOUT",
                )
                return full_text
            # Fetch the current genai module after the thread returns so
            # tests that swap sys.modules still see the right module.
            from google import genai
            _t_client_ready = _time.monotonic()
            _gemini_log.info(
                "gemini_phase=client_ready ms=%.0f cache_hit=%s model=%s",
                (_t_client_ready - _t0) * 1000,
                _GEMINI_CLIENT_CACHE.get((api_key, model_name)) is not None,
                model_name,
            )
            _log_gemini_model_once(model_name)

            history = []
            for msg in messages[:-1]:
                role = "user" if msg["role"] == "user" else "model"
                text = _gemini_content_to_text(msg.get("content", ""))
                # Label assistant messages with their source model so Gemini
                # knows which responses came from Claude vs itself.
                if role == "model":
                    source = (msg.get("model") or "").lower()
                    if source and "claude" in source:
                        text = f"[Claude's response]: {text}"
                    elif source and "gemini" in source:
                        text = f"[Your previous response]: {text}"
                history.append(
                    {"role": role, "parts": [{"text": text}]}
                )

            # ``send_message`` accepts a string, dict, Blob, Image, or a list
            # of Parts. Convert the last message (always the live user turn)
            # into a list of Gemini Parts so that inline images (GIFs, pasted
            # screenshots) are passed as ``inline_data`` blobs rather than
            # dropped or turned into a ``[image attached]`` placeholder.
            # History messages (prior turns) stay as plain text because Gemini
            # vision is most useful on the current turn and the history path
            # does not need multipart support.
            last_content = _gemini_content_to_parts(messages[-1].get("content", ""))
            # Gemini's start_chat requires STRICTLY ALTERNATING roles
            # (user, model, user, model, ...). myOS prepends a role="user"
            # context message (memory from prior tabs, calendar digest,
            # workspace status) before the real user turn, which produces
            # two consecutive user entries in history. The google-generativeai
            # SDK responds by falling back to a non-streaming single-shot
            # call that takes 25s+ to return, during which the chat panel
            # shows "Thinking" with no tokens and the user thinks it hung.
            # Collapse runs of the same role into one entry by joining
            # their parts with a blank line separator. This preserves the
            # full context while giving Gemini the alternating shape it
            # streams against.
            merged_history: list[dict] = []
            for entry in history:
                if merged_history and merged_history[-1]["role"] == entry["role"]:
                    prev_parts = merged_history[-1].get("parts", [])
                    new_parts = entry.get("parts", [])
                    prev_texts = [
                        p["text"] for p in prev_parts
                        if isinstance(p, dict) and p.get("text")
                    ]
                    new_texts = [
                        p["text"] for p in new_parts
                        if isinstance(p, dict) and p.get("text")
                    ]
                    combined_text = "\n\n".join(prev_texts + new_texts)
                    merged_history[-1] = {"role": entry["role"], "parts": [{"text": combined_text}]}
                else:
                    merged_history.append(entry)
            # Gemini requires history to END with a model turn so the
            # next send_message (always a user turn) keeps alternation.
            # If history ends with a user entry (happens when myOS
            # prepends prior-conversation memory as role=user and the
            # user starts a fresh tab, so the ONLY prior turn is that
            # memory block), synthesize a tiny model acknowledgement so
            # the alternation holds. Previously we popped the trailing
            # user and merged it into last_content, which emptied
            # history entirely. With empty history and a long user
            # prompt, the deprecated google.generativeai SDK hung on
            # send_message(stream=True) forever and the chat panel sat
            # on "Thinking" with no tokens. The synthetic ack keeps
            # history non-empty and alternation valid.
            if merged_history and merged_history[-1]["role"] == "user":
                merged_history.append({
                    "role": "model",
                    "parts": [{"text": "Got it. What would you like to know?"}],
                })
            chat = model.chats.create(
                model=model_name,
                history=merged_history,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_instruction if system_instruction is not None else _gemini_system_instruction()
                ),
            )
            # The google.genai SDK's streaming ``send_message_stream()``
            # returns a SYNCHRONOUS generator. Calling ``next()`` on it blocks
            # the current thread on each network read, and because this runs
            # inside an async websocket handler on the uvicorn event loop,
            # every other HTTP request served by the same worker (tasks,
            # briefings, agents, health) FREEZES until the stream finishes.
            # We saw /openapi.json, /api/health, /api/tasks all time out at
            # 5s while a Gemini turn was streaming. Fix: run both the initial
            # send_message call and every ``next(iterator)`` on a worker
            # thread via asyncio.to_thread so the event loop stays free
            # between chunks. Async-native SDK would be nicer but the
            # existing test suite mocks the sync API so we preserve it.
            import asyncio as _asyncio
            _t_send = _time.monotonic()
            _gemini_log.info(
                "gemini_phase=payload_built ms=%.0f history_turns=%d",
                (_t_send - _t_client_ready) * 1000,
                len(merged_history),
            )
            try:
                response = await _asyncio.wait_for(
                    _asyncio.to_thread(
                        chat.send_message_stream, last_content
                    ),
                    timeout=_GEMINI_SEND_MESSAGE_TIMEOUT_S,
                )
            except _asyncio.TimeoutError:
                await _send_friendly_gemini_error(
                    websocket,
                    (
                        f"Gemini did not respond in "
                        f"{int(_GEMINI_SEND_MESSAGE_TIMEOUT_S)} seconds. "
                        "This usually means the network or Google's API is "
                        "slow. Please try again."
                    ),
                    reason_name="SEND_MESSAGE_TIMEOUT",
                )
                return full_text
            _t_network = _time.monotonic()
            _gemini_log.info(
                "gemini_phase=send_message_returned ms=%.0f",
                (_t_network - _t_send) * 1000,
            )

            # Stream chunks. We guard ``chunk.text`` because the SDK's
            # ``.text`` property raises ValueError when a chunk has no
            # parts (which happens on the final SAFETY chunk). A bad
            # chunk must NOT abort the whole turn, we just skip it and
            # let the post-loop finish_reason check decide what to show.
            _CHUNK_STOP = object()
            _chunk_iter = iter(response)
            _first_token_logged = False

            def _pull_next_chunk():
                try:
                    return next(_chunk_iter)
                except StopIteration:
                    return _CHUNK_STOP

            try:
                while True:
                    # Use a tighter budget for the first chunk and a
                    # looser one after the stream is producing content.
                    # A silent upstream (no chunks ever) trips the first
                    # chunk timeout. Long "thinking" pauses on 2.5
                    # models are covered by the next-chunk budget.
                    chunk_timeout = (
                        _GEMINI_FIRST_CHUNK_TIMEOUT_S
                        if not _first_token_logged
                        else _GEMINI_NEXT_CHUNK_TIMEOUT_S
                    )
                    try:
                        chunk = await _asyncio.wait_for(
                            _asyncio.to_thread(_pull_next_chunk),
                            timeout=chunk_timeout,
                        )
                    except _asyncio.TimeoutError:
                        phase = (
                            "first_chunk"
                            if not _first_token_logged
                            else "next_chunk"
                        )
                        if full_text:
                            friendly = (
                                f"[Gemini said: {full_text.strip()}] "
                                f"before going quiet. Gemini did not "
                                f"send any more text within "
                                f"{int(chunk_timeout)} seconds. Please "
                                "try again."
                            )
                        else:
                            friendly = (
                                f"Gemini did not send any text within "
                                f"{int(chunk_timeout)} seconds. This "
                                "usually means Google's API is slow or "
                                "the network is flaky. Please try again."
                            )
                        await _send_friendly_gemini_error(
                            websocket,
                            friendly,
                            reason_name=f"{phase.upper()}_TIMEOUT",
                        )
                        return full_text
                    if chunk is _CHUNK_STOP:
                        break
                    try:
                        text = chunk.text
                    except (ValueError, AttributeError):
                        # Empty-parts chunk. The reason lives on the
                        # final response which we inspect below.
                        continue
                    if text:
                        if not _first_token_logged:
                            _gemini_log.info(
                                "gemini_phase=first_token ms=%.0f total_ms=%.0f",
                                (_time.monotonic() - _t_network) * 1000,
                                (_time.monotonic() - _t0) * 1000,
                            )
                            _first_token_logged = True
                        full_text += text
                        await websocket.send_json({"type": "token", "data": text})
            except WebSocketDisconnect:
                # Client disconnected mid-stream. Nothing to send.
                return full_text

            # After the stream has drained, inspect the accumulated
            # finish_reason. Anything other than STOP means Gemini cut
            # the response off (safety filter, recitation filter,
            # max_tokens, etc.). In that case we must NOT send a normal
            # done event, because the chat panel treats done as "this
            # turn completed successfully" and would leave the orphan
            # partial text on screen. Send a plain-language error
            # instead, keyed to the reason, and log the real enum name.
            finish_reason_name = "STOP"
            prompt_block_reason = None
            try:
                prompt_feedback = getattr(response, "prompt_feedback", None)
                if prompt_feedback is not None:
                    prompt_block_reason = getattr(
                        prompt_feedback, "block_reason", None
                    )
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    finish_reason_name = _gemini_finish_reason_name(
                        getattr(candidates[0], "finish_reason", None)
                    )
            except Exception:
                # If the SDK surface changes, fail open: treat as STOP
                # so a clean response still sends done. Any real
                # mid-stream failures are caught by the outer except.
                finish_reason_name = "STOP"

            # Prompt-side block caught after iteration (rare, but the
            # SDK can populate prompt_feedback without raising).
            if prompt_block_reason:
                await _send_friendly_gemini_error(
                    websocket,
                    _GEMINI_PROMPT_BLOCKED_MESSAGE,
                    reason_name=f"PROMPT_BLOCKED:{prompt_block_reason}",
                )
                return full_text

            if finish_reason_name != "STOP":
                friendly = _gemini_friendly_finish_message(
                    finish_reason_name, full_text
                )
                await _send_friendly_gemini_error(
                    websocket,
                    friendly,
                    reason_name=finish_reason_name,
                )
                return full_text

            # Guard against empty responses. When Gemini returns 0 candidates
            # (which can happen on quota soft-limits or brief API hiccups),
            # finish_reason defaults to "STOP" and we would normally send
            # "done" with no tokens, leaving a blank assistant bubble. Treat
            # an empty response + STOP as an error so the user sees something
            # actionable instead of a silent empty turn.
            if not full_text:
                _gemini_log.warning(
                    "gemini returned empty response (0 tokens, finish_reason=STOP); "
                    "sending error instead of silent done"
                )
                await _send_friendly_gemini_error(
                    websocket,
                    "Gemini returned an empty response. This can happen when the "
                    "API is temporarily busy or a usage limit was hit. Please try "
                    "again in a moment.",
                    reason_name="EMPTY_RESPONSE",
                )
                return full_text

            # Log Gemini usage to audit.jsonl. The streaming response
            # exposes usage_metadata on some SDK versions. Fall back to 0
            # when the field is absent so the audit entry still records the
            # model and timestamp even without exact token counts.
            _gem_input = 0
            _gem_output = 0
            try:
                _usage_meta = getattr(response, "usage_metadata", None)
                if _usage_meta is not None:
                    _gem_input = getattr(_usage_meta, "prompt_token_count", 0) or 0
                    _gem_output = getattr(_usage_meta, "candidates_token_count", 0) or 0
            except Exception:
                pass
            _log_chat_completion(
                model=model_name,
                input_tokens=_gem_input,
                output_tokens=_gem_output,
                provider="gemini",
                topic=_extract_chat_topic(messages),
            )
            _gemini_log.info(
                "gemini_phase=done total_ms=%.0f tokens=%d",
                (_time.monotonic() - _t0) * 1000,
                _gem_output,
            )
            await websocket.send_json({"type": "done"})
        except WebSocketDisconnect:
            # Client already gone; nothing to send.
            pass
        except Exception as e:
            error_text = str(e)
            friendly = _friendly_gemini_error(error_text)
            try:
                await websocket.send_json({"type": "error", "data": friendly})
            except Exception:
                pass  # socket already closed; client will show its own disconnect message

        return full_text


# --- Multi-AI chat orchestration ---
#
# When the user mentions two models with conversational intent ("chat
# with", "debate", "talk to", etc.), we run a real round-robin: each
# model reads the full transcript so far and replies in turn. Each turn
# streams as its own bubble in the chat panel, bracketed by
# ``multi_ai_turn_start`` and ``multi_ai_turn_end`` events so the panel
# can open a fresh bubble with the right model header. A
# ``multi_ai_status`` event is sent before and after each turn so the
# user can watch the "thinking" state move between models.
#
# Event shapes (all wrapped in {"type": ..., "data": ...}):
#
#   multi_ai_status:
#     {"phase": "starting", "models": [...], "rounds": int}
#     {"phase": "thinking", "model": "gemini", "round": 1}
#     {"phase": "speaking",  "model": "gemini", "round": 1}
#     {"phase": "complete"}
#
#   multi_ai_turn_start:
#     {"model": "gemini", "round": 1}
#
#   multi_ai_turn_end:
#     {"model": "gemini", "round": 1}
#
# Between turn_start and turn_end the existing per-provider ``token``
# events stream through untouched, so the frontend can render the text
# into the fresh bubble without any new token plumbing.

# Default number of rounds in a multi-AI conversation. Each round is one
# reply per model, so the default of 3 yields 6 total turns for a two
# model exchange (A B A B A B). Kept as a module-level constant so
# callers and tests can reference it instead of hard coding.
MULTI_AI_DEFAULT_ROUNDS = 3


class _MultiAiTurnWebSocket:
    """WebSocket proxy that records the full response text of one turn.

    Wraps the real WebSocket so that a single per-provider stream call
    (``stream_gemini`` or ``stream_anthropic``) can flow its ``token``
    events to the panel unchanged, while we capture the concatenated
    text for the next turn's prompt. The proxy also swallows the
    per-turn ``done`` event so the panel does not see six "turn ended"
    signals during a multi AI exchange. The outer orchestration function
    sends exactly one ``done`` at the end.

    Any event the proxy does not recognise is passed through verbatim so
    error messages, finish-reason warnings, and backend badges still
    reach the panel.

    When ``model_tag`` is supplied, every forwarded frame is annotated
    with a ``model`` field so the frontend can route tokens and errors
    to the correct bubble when multiple models are streaming in
    parallel. Without this tag, parallel broadcast frames would all
    target the most recently opened bubble and the two responses would
    collide inside a single bubble.
    """

    def __init__(self, inner: WebSocket, model_tag: Optional[str] = None):
        self._inner = inner
        self._model_tag = model_tag
        self.collected_text: list[str] = []
        # Serialize writes so parallel tasks sharing the underlying
        # WebSocket never interleave partial JSON frames mid-send.
        self._write_lock: Optional[asyncio.Lock] = None

    def _attach_lock(self, lock: asyncio.Lock) -> None:
        self._write_lock = lock

    async def send_json(self, data: dict) -> None:
        msg_type = data.get("type") if isinstance(data, dict) else None
        if msg_type == "token":
            token_text = data.get("data", "")
            if isinstance(token_text, str):
                self.collected_text.append(token_text)
        if msg_type == "done":
            # Swallow interstitial done events. The outer orchestrator
            # sends exactly one done after every turn finishes.
            return
        if msg_type == "error":
            # In single-model orchestration (no model_tag), swallow
            # provider errors so they don't close the WebSocket before
            # the outer orchestrator sends its terminal ``done``.
            #
            # In broadcast mode (model_tag set), pass the error through
            # tagged with the owning model so the frontend can display it
            # in that model's bubble without tearing down the sibling stream.
            if not self._model_tag:
                return
            # Fall through to the model-tag injection and send below.
        # Tag the frame with the owning model so the frontend can route
        # parallel per-model streams into the right bubble. We merge
        # without clobbering an existing model field if the inner event
        # already carried one.
        if isinstance(data, dict) and self._model_tag:
            if "model" not in data:
                data = {**data, "model": self._model_tag}
        if self._write_lock is not None:
            async with self._write_lock:
                await self._inner.send_json(data)
        else:
            await self._inner.send_json(data)

    @property
    def text(self) -> str:
        return "".join(self.collected_text)


def _format_multi_ai_transcript(transcript: list[dict]) -> str:
    """Render the running conversation transcript as plain text.

    ``transcript`` is a list of ``{"model": str, "text": str}`` dicts in
    chronological order. The output is a simple labeled block the next
    speaker can read to understand what has been said so far. Labels use
    the model's display name (capitalized) to match the chat panel
    headers.
    """
    lines: list[str] = []
    for turn in transcript:
        model = str(turn.get("model", "")).capitalize() or "Unknown"
        text = str(turn.get("text", "")).strip()
        if not text:
            continue
        lines.append(f"{model}: {text}")
    return "\n\n".join(lines)


def _build_multi_ai_prompt(
    *,
    self_model: str,
    other_models: list[str],
    user_message: str,
    transcript: list[dict],
    round_index: int,
) -> str:
    """Build the prompt the next speaker sees.

    The prompt includes the original user topic, the full transcript so
    far, and a short instruction telling the model to reply directly to
    the previous speaker without prefixing its own name. The instruction
    also forbids writing a fake script with labels for both sides, which
    was the exact failure in the "@Gemini: ... @Claude: ..." bubble the
    user reported.
    """
    self_display = self_model.capitalize()
    others_display = ", ".join(m.capitalize() for m in other_models if m != self_model)
    transcript_block = _format_multi_ai_transcript(transcript)
    if transcript_block:
        transcript_section = (
            "Conversation so far:\n\n" + transcript_block + "\n\n"
        )
    else:
        transcript_section = (
            "This is the first message in the conversation. Nothing has "
            "been said yet.\n\n"
        )
    instruction = (
        f"You are {self_display}. You are having a real back and forth "
        f"with {others_display}. The user asked you both: \"{user_message}\".\n\n"
        + transcript_section
        + "Reply directly to the previous speaker. Do not prefix your "
        "reply with your own name. Do not write a fake script with "
        "labels for both sides. Do not narrate the exchange. Keep it "
        "short and conversational, two or three sentences."
    )
    if round_index == 1 and not transcript:
        instruction += (
            " You are going first, so open the conversation by sharing "
            "your own view on the topic."
        )
    return instruction


async def _run_multi_ai_turn(
    *,
    websocket: WebSocket,
    model: str,
    prompt: str,
    round_index: int,
) -> str:
    """Stream one model's turn and return the concatenated reply text.

    Brackets the turn with ``multi_ai_turn_start`` and
    ``multi_ai_turn_end`` so the panel can open a fresh bubble with the
    correct model header. Reuses ``stream_gemini`` / ``stream_anthropic``
    via a recording proxy so the existing retry, friendly-error, and
    finish-reason logic all still apply.
    """
    await websocket.send_json({
        "type": "multi_ai_turn_start",
        "data": {"model": model, "round": round_index},
    })
    await websocket.send_json({
        "type": "multi_ai_status",
        "data": {"phase": "speaking", "model": model, "round": round_index},
    })

    proxy = _MultiAiTurnWebSocket(websocket)
    messages = [{"role": "user", "content": prompt}]
    try:
        if model == "gemini":
            await chat_service.stream_gemini(messages, proxy)  # type: ignore[arg-type]
        elif model == "claude":
            await chat_service.stream_anthropic(messages, proxy)  # type: ignore[arg-type]
        else:
            await websocket.send_json(
                {"type": "error", "data": f"Unknown model: {model}"}
            )
    finally:
        await websocket.send_json({
            "type": "multi_ai_turn_end",
            "data": {"model": model, "round": round_index},
        })

    return proxy.text


async def stream_multi_ai_conversation(
    *,
    websocket: WebSocket,
    models: list[str],
    user_message: str,
    rounds: int = MULTI_AI_DEFAULT_ROUNDS,
) -> None:
    """Run a real back and forth between two AI models.

    Each round, every model in ``models`` takes one turn. Each turn is
    its own bubble in the chat panel (bracketed by
    ``multi_ai_turn_start`` and ``multi_ai_turn_end``), and the full
    transcript so far is fed into the next speaker's prompt so the
    models are actually replying to each other instead of writing
    independent monologues.

    The function sends exactly one ``done`` event at the end of the
    whole exchange. Per-turn ``done`` events from the underlying
    provider streams are swallowed by the recording proxy so the panel
    sees a single "turn ended" signal.
    """
    if not models:
        await websocket.send_json(
            {"type": "error", "data": "No models supplied for multi AI chat."}
        )
        return
    if rounds < 1:
        rounds = 1

    await websocket.send_json({
        "type": "multi_ai_status",
        "data": {
            "phase": "starting",
            "models": list(models),
            "rounds": rounds,
        },
    })

    transcript: list[dict] = []
    try:
        for round_index in range(1, rounds + 1):
            for model in models:
                await websocket.send_json({
                    "type": "multi_ai_status",
                    "data": {
                        "phase": "thinking",
                        "model": model,
                        "round": round_index,
                    },
                })
                other_models = [m for m in models if m != model]
                prompt = _build_multi_ai_prompt(
                    self_model=model,
                    other_models=other_models,
                    user_message=user_message,
                    transcript=transcript,
                    round_index=round_index,
                )
                reply_text = await _run_multi_ai_turn(
                    websocket=websocket,
                    model=model,
                    prompt=prompt,
                    round_index=round_index,
                )
                if reply_text.strip():
                    transcript.append({"model": model, "text": reply_text})
    finally:
        await websocket.send_json({
            "type": "multi_ai_status",
            "data": {"phase": "complete"},
        })
        await websocket.send_json({"type": "done"})


def _transform_messages_for_provider(
    messages: list[dict], target_model: str
) -> list[dict]:
    """Rewrite cross-model assistant turns so each provider only sees its own
    prior responses as ``role: "assistant"``.

    In all-mode broadcast the conversation history accumulates assistant turns
    from multiple models (each carrying a ``"model"`` field). When that history
    is fed back into a subsequent call, the target provider would otherwise see
    another model's responses as if it had generated them, breaking attribution
    and making it impossible to reference what "the other model" said.

    For every assistant turn whose ``"model"`` field differs from
    ``target_model``, this function rewrites the turn to ``role: "user"`` with
    a ``[ModelName]:`` prefix so the provider understands the source. Turns
    with no ``"model"`` field (plain single-model history) are passed through
    unchanged.

    Consecutive ``user`` messages produced by the rewrite are merged (joined
    with ``\\n\\n``) so the resulting list alternates user/assistant as both
    the Anthropic and Gemini APIs require.
    """
    result: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        msg_model = msg.get("model", "")

        if role == "assistant" and msg_model and msg_model != target_model:
            label = msg_model.capitalize()
            text = content if isinstance(content, str) else str(content)
            new_msg: dict = {"role": "user", "content": f"[{label}]: {text}"}
        else:
            new_msg = dict(msg)

        if result and result[-1]["role"] == "user" and new_msg["role"] == "user":
            prev_content = result[-1].get("content", "")
            new_content = new_msg.get("content", "")
            if isinstance(prev_content, str) and isinstance(new_content, str):
                result[-1] = dict(result[-1])
                result[-1]["content"] = prev_content + "\n\n" + new_content
            else:
                result.append(new_msg)
        else:
            result.append(new_msg)

    return result


async def stream_group_broadcast(
    *,
    websocket: WebSocket,
    models: list[str],
    messages: list[dict],
    use_tools: bool = False,
) -> None:
    """Each model in *models* responds in parallel to the same message.

    Used when the user addresses multiple AIs collectively (e.g. "you guys",
    "both of you", "everyone") or toggles the All pill. Every AI receives
    the full conversation history and responds directly to the user. Each
    model sees the other model's prior turns rewritten as user-role context
    with a ``[ModelName]:`` prefix so models can reference each other's
    previous responses (see ``_transform_messages_for_provider``).

    Execution is parallel (asyncio.gather) so the user sees both bubbles
    update simultaneously rather than waiting for Claude to finish before
    Gemini starts. All ``multi_ai_turn_start`` frames are emitted up front
    so the UI renders a thinking bubble for every model immediately, even
    before the first token arrives from either side. Each per-model stream
    is wrapped in a ``_MultiAiTurnWebSocket`` tagged with its model name so
    the frontend can route token / error frames to the correct bubble.

    One task raising an exception does not cancel the other. Failures are
    isolated per model so a Claude outage still lets Gemini respond and
    vice versa. Sends exactly one ``done`` event at the end.
    """
    if not models:
        return

    # Serialize WebSocket writes so parallel per-model streams never
    # interleave partial JSON frames on the wire.
    write_lock = asyncio.Lock()

    async def _safe_send(payload: dict) -> None:
        async with write_lock:
            try:
                await websocket.send_json(payload)
            except Exception:
                pass

    # Emit every turn_start up front. This creates one thinking bubble per
    # model in the UI immediately, so the user sees "Claude thinking" and
    # "Gemini thinking" side by side rather than watching a single bubble
    # stall while the backend works through models serially.
    for model in models:
        await _safe_send({
            "type": "multi_ai_turn_start",
            "data": {"model": model, "round": 1},
        })

    async def _run_one(model: str) -> None:
        proxy = _MultiAiTurnWebSocket(websocket, model_tag=model)
        proxy._attach_lock(write_lock)
        # Each model gets a transformed view: the other model's prior assistant
        # turns become user-role context with a [ModelName]: prefix.
        provider_messages = _transform_messages_for_provider(messages, model)
        try:
            if model == "gemini":
                await chat_service.stream_gemini(provider_messages, proxy)  # type: ignore[arg-type]
            elif model == "claude":
                if use_tools:
                    await chat_service.agent_anthropic(provider_messages, proxy)  # type: ignore[arg-type]
                else:
                    # Pass disable_tools=True so the Claude Code CLI
                    # backend runs in pure text mode. Without this the
                    # CLI uses its built-in Bash/Grep/Read tools even
                    # though we picked the no-tool stream path.
                    # force_api=True routes broadcast Claude through the
                    # direct Anthropic API when a key is available. The
                    # CLI subprocess adds multi-second startup latency
                    # that made Claude's first token arrive tens of
                    # seconds after Gemini's. See stream_anthropic for
                    # the fallback behavior when no key is present.
                    await chat_service.stream_anthropic(provider_messages, proxy, disable_tools=True, force_api=True)  # type: ignore[arg-type]
            else:
                await _safe_send(
                    {"type": "error", "data": f"Unknown model: {model}", "model": model}
                )
        except Exception as exc:
            # A crash in one provider must NEVER silence the other. Surface
            # a friendly, bubble-scoped error so the frontend can paint the
            # failure in that model's bubble while the sibling task keeps
            # streaming. Without this guard, stream_anthropic raising on
            # (e.g.) a transport reset would propagate out of gather and
            # the Gemini task would be cancelled mid-stream.
            await _safe_send(
                {
                    "type": "error",
                    "data": f"{model.capitalize()} failed to respond: {exc}",
                    "model": model,
                }
            )
        finally:
            await _safe_send({
                "type": "multi_ai_turn_end",
                "data": {"model": model, "round": 1},
            })

    try:
        # return_exceptions=True so a single raised task never prevents
        # the other from finishing. _run_one already converts exceptions
        # into a friendly error frame, so this is a belt-and-braces guard.
        await asyncio.gather(
            *(_run_one(m) for m in models),
            return_exceptions=True,
        )
    finally:
        await _safe_send({"type": "done"})


chat_service = ChatService()


import re as _re

_CODE_BLOCK_RE = _re.compile(r"```", _re.DOTALL)
_TOOL_INDICATOR_RE = _re.compile(
    r"\b(bash|python|javascript|typescript|code|script|function|class|import|def |lambda|grep|awk|sed|curl|git |npm |pip )\b",
    _re.IGNORECASE,
)


def route_provider(
    message: str,
    org_policy: Optional[str],
    available_providers: list[str],
) -> str:
    """Select a provider key based on org policy and message heuristics.

    Policy values:
      - "claude_only": always claude
      - "gemini_only": gemini, fallback to claude if unavailable
      - "prefer_gemini" / "auto": gemini for short simple messages, else claude
      - None / anything else: treated as "auto"

    Always falls back to "claude" if the preferred provider is not in
    available_providers.
    """
    policy = org_policy or "auto"

    def _available(provider: str) -> bool:
        return provider in available_providers

    if policy == "claude_only":
        return "claude"

    if policy == "gemini_only":
        return "gemini" if _available("gemini") else "claude"

    # "auto" or "prefer_gemini": use gemini for short, code-free messages
    short = len(message) < 500
    has_code = bool(_CODE_BLOCK_RE.search(message)) or bool(_TOOL_INDICATOR_RE.search(message))
    if short and not has_code and _available("gemini"):
        return "gemini"
    return "claude"
