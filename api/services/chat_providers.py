import asyncio
import logging
import os
import random
from typing import Any, Awaitable, Callable, Optional

import anthropic
from fastapi import WebSocket

from config import PROJECT_ROOT
from services import claude_code_provider
from services.ostk import ostk
from services.settings_store import settings_store
from services.template_matcher import match_template, merge_with_built_ins
from services.ostk import write_audit_entry
from services.token_metrics import safe_record_chat_turn
from services.tool_executor import TOOL_DEFINITIONS, execute_tool


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


# System instruction for Gemini. Kept as a module-level constant so tests
# can assert the "no self label" rule is in place and future edits do not
# accidentally drop it. The rule exists because Gemini likes to prefix
# replies with a literal "@Gemini:" tag, which is noisy in the chat panel
# since the bubble header already shows which model is speaking.
GEMINI_SYSTEM_INSTRUCTION = (
    "You are Gemini replying inside a chat panel. "
    "Do not prefix your replies with your own name. The chat panel "
    "already shows who you are. "
    "When asked to chat with another AI, reply directly to what that "
    "other AI just said. Do not write a fake script with labels for "
    "both sides, do not narrate the exchange, do not add stage "
    "directions. Keep replies conversational and concise. "
    "Never use em-dashes."
)


def _gemini_model_name() -> str:
    """Return the Gemini model name to use.

    Honors the ``MYOS_GEMINI_MODEL`` environment variable so users can
    override the default without editing code. When Google deprecates
    the default the user can swap in a working model immediately.
    """
    override = os.environ.get("MYOS_GEMINI_MODEL", "").strip()
    return override or DEFAULT_GEMINI_MODEL


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
    return error_text


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
        import google.generativeai as genai  # local import: optional dep
        fr_enum = genai.protos.Candidate.FinishReason(int(finish_reason))
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
    # 1. Org-level key (enterprise mode)
    provider = _SETTINGS_KEY_TO_PROVIDER.get(settings_key, "")
    if provider:
        from services import enterprise_store
        if enterprise_store.is_enterprise():
            org_key = enterprise_store.get_org_api_key(provider)
            if org_key:
                return org_key

    env_name = _ENV_KEY_MAP.get(settings_key, "")

    # 2. System keychain (preferred)
    if env_name:
        keychain_value = await ostk.secret_get(env_name)
        if keychain_value:
            return keychain_value

    # 3. Legacy settings.json (backward compat)
    key = settings_store.get(settings_key, "")
    if key:
        return key

    # 4. Environment variable
    if env_name:
        return os.environ.get(env_name, "")
    return ""

MAX_AGENT_TURNS = 10


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
    last_exc: Optional[BaseException] = None
    for attempt in range(_ANTHROPIC_MAX_ATTEMPTS):
        try:
            return await func()
        except BaseException as exc:  # noqa: BLE001
            if not _is_retryable_anthropic_error(exc):
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


async def _send_friendly_anthropic_error(
    websocket: WebSocket, exc: BaseException
) -> None:
    """Send a plain-language error to the chat panel after retries failed.

    Never leaks raw JSON, never uses em-dashes, never uses jargon. The
    exact text comes from ``_ANTHROPIC_UNAVAILABLE_MESSAGE`` so the fix
    is a single place to edit if we want to tune the copy.
    """
    try:
        await websocket.send_json(
            {"type": "error", "data": _ANTHROPIC_UNAVAILABLE_MESSAGE}
        )
    except Exception:
        # Never let a websocket hiccup mask the underlying failure.
        pass
    # Log the real error so it shows up in server logs without leaking it
    # to the chat panel.
    try:
        _anthropic_retry_log.error(
            "anthropic call failed after retries: %s: %s",
            exc.__class__.__name__,
            exc,
        )
    except Exception:
        pass



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


def _system_prompt() -> str:
    """Return the static system prompt without boot context.

    Boot context is added separately by ``_build_cached_system_blocks``
    (as its own cached block) or by ``_compose_system_prompt`` (appended
    inline for the Claude Code backend fallback).
    """
    os_name = settings_store.get("os_name", "myOS")
    user_name = settings_store.get("user_name", "")
    owner = user_name if user_name else "the user"
    return (
        f"You are {os_name}, {owner}'s personal operating system. "
        "You have access to tools that let you read files, write files, edit files, "
        "run shell commands, search code, manage tasks, search the web, fetch web pages, "
        "run git operations, spawn background agents, create Google Calendar events, "
        f"send emails via Gmail, and upload files to Google Drive in the workspace at {PROJECT_ROOT}. "
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
        "GOOGLE INTEGRATION: When the user's Google account is connected through Settings, "
        "you can use these tools directly. Never tell the user to connect via /mcp when "
        "they are already connected through Settings.\n"
        "- create_calendar_event: Use when the user says 'add to calendar', 'schedule', "
        "'put on my calendar', or similar. For events without a specific time (field trips, "
        "birthdays), set all_day to true.\n"
        "- send_email: Use when the user says 'send an email', 'email', or 'write to'. "
        "Draft the email text and send it. Confirm what you sent.\n"
        "- upload_to_drive: Use when the user says 'save to Drive', 'upload to Drive', "
        "or asks to create a document in Drive.\n"
        "- get_calendar_events: Use to check what is on the calendar today.\n\n"
        "IDEA MANAGEMENT: You can capture, list, and delete ideas.\n"
        "- To capture a new idea when the user mentions one in passing, use capture_idea silently.\n"
        "- To list active ideas, use list_ideas.\n"
        "- To list converted ideas (turned into tasks), use list_converted_ideas.\n"
        "- To delete an idea (active or converted), use delete_idea with the text or a fragment.\n"
        "When the user asks to remove, delete, or clean up an idea, use delete_idea. Do not suggest they do it manually. When capturing ideas in passing, do NOT announce it.\n\n"
        "Keep your responses brief and focused on outcomes, not process. "
        "Do NOT narrate your steps. Do NOT say 'Let me check' or 'Let me look'. "
        "Just do the work and share the result. "
        "Be action-oriented: read only what you need, then make edits quickly. "
        "Do not over-research. If you know enough to make a change, make it. "
        "Never use em-dashes. "
        "When the user sends a GIF, do not describe what is in the GIF. They can already see it. "
        "Just react naturally to the sentiment behind it, like you would in a text conversation."
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
    """Return the full system prompt as a single string.

    Used by the Claude Code backend fallback where we cannot split into
    separate cached blocks. Includes boot context inline.
    """
    base = _system_prompt()
    boot_context = _get_boot_context()
    if boot_context:
        base += f"\n\nSESSION CONTEXT (from `ostk boot`, already run, do not run again):\n{boot_context}\n"
    if not matched_template:
        return base
    extra = str(matched_template.get("prompt") or "").strip()
    if not extra:
        return base
    return base + "\n\n---\nACTIVE TEMPLATE: " + str(matched_template.get("name", "")) + "\n" + extra


def _build_cached_system_blocks(matched_template: Optional[dict]) -> list[dict]:
    """Build system prompt as separate cached blocks.

    Splits the system prompt into a stable instructions block (cached)
    and a volatile boot context block (separate). This way the large,
    mostly-static instructions stay cached even when needle counts,
    fleet status, or other boot context changes between turns.

    The Anthropic API supports up to 4 cache breakpoints. We use 2 here
    (instructions + boot context) leaving 2 for conversation prefix caching.
    """
    os_name = settings_store.get("os_name", "myOS")
    user_name = settings_store.get("user_name", "")
    owner = user_name if user_name else "the user"

    # Static instructions block. This text rarely changes, so it stays
    # cached across many turns and even across conversations within the
    # 5-minute TTL.
    base = _system_prompt()
    if matched_template:
        extra = str(matched_template.get("prompt") or "").strip()
        if extra:
            base += "\n\n---\nACTIVE TEMPLATE: " + str(matched_template.get("name", "")) + "\n" + extra

    boot_context = _get_boot_context()
    if not boot_context:
        return [
            {
                "type": "text",
                "text": base,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    # Split: static instructions (cached) + volatile boot context (cached
    # separately so a boot context change does not bust the instructions cache).
    return [
        {
            "type": "text",
            "text": base,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": f"SESSION CONTEXT (from `ostk boot`, already run, do not run again):\n{boot_context}",
            "cache_control": {"type": "ephemeral"},
        },
    ]


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

    if isinstance(content, str):
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
        # Add cache_control to the last block in the content list.
        new_content = list(content)
        last_block = dict(new_content[-1])
        last_block["cache_control"] = {"type": "ephemeral"}
        new_content[-1] = last_block
        result[target_idx] = {**target, "content": new_content}

    return result


class ChatService:
    async def stream_anthropic(self, messages: list[dict], websocket: WebSocket, tab_id: str = "") -> str:
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
                messages, websocket, system_prompt=system_prompt, tab_id=tab_id
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
        cached_messages = _add_conversation_prefix_cache(messages)
        stream_kwargs: dict = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "messages": cached_messages,
        }
        # Use split system blocks so stable instructions stay cached
        # even when volatile boot context changes between turns.
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
                                            full_text += text
                                            await websocket.send_json({
                                                "type": "token",
                                                "data": text,
                                            })
                    return await stream.get_final_message()
            else:
                async with client.messages.stream(**stream_kwargs) as stream:
                    async for text in stream.text_stream:
                        full_text += text
                        await websocket.send_json({"type": "token", "data": text})
                    return await stream.get_final_message()

        try:
            # Retry ONLY while no tokens have been emitted. Once the
            # stream starts sending text, retrying would re-emit the
            # beginning of the response and confuse the chat panel.
            if full_text:
                response = await _run_stream_once()
            else:
                response = await _anthropic_retry_call(
                    _run_stream_once,
                    op_name="anthropic.messages.stream",
                )
            _cache_creation = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            _cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
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
            status = getattr(e, "status_code", None)
            if status is not None and 500 <= int(status) < 600:
                # Retries were already attempted inside
                # ``_anthropic_retry_call``. Surface a plain-language
                # error instead of the raw JSON body.
                await _send_friendly_anthropic_error(websocket, e)
            else:
                # 4xx: show the real error text so the user can fix it
                # (bad key, unknown model, bad input, etc.).
                await websocket.send_json({"type": "error", "data": str(e)})
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            await _send_friendly_anthropic_error(websocket, e)
        except anthropic.APIError as e:
            await websocket.send_json({"type": "error", "data": str(e)})

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

        # Auto-match agent template based on the user's message. If matched,
        # the system prompt picks up the template's extra instructions and
        # the chat panel shows a small "Using: <name>" badge. We do this
        # before picking a backend so both paths get the matched helper.
        matched_template = await _maybe_match_template(messages, websocket, api_key)
        # Use split system blocks so stable instructions stay cached
        # even when volatile boot context changes between turns.
        cached_system_prompt = _build_cached_system_blocks(matched_template)
        active_system_prompt = _compose_system_prompt(matched_template)

        backend = await _resolve_chat_backend()

        # Claude Code CLI cannot receive inline image blocks. If the last
        # message includes an image (GIF or pasted screenshot), force the
        # Anthropic API backend so the model can actually see it.
        if backend == "claude_code" and _messages_contain_images(messages):
            if api_key:
                backend = "anthropic_api"

        await _send_backend_active(websocket, backend)

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

        client = anthropic.AsyncAnthropic(api_key=api_key)
        conversation: list[dict] = list(messages)
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_creation_tokens = 0
        total_cache_read_tokens = 0
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

                    response = await _anthropic_retry_call(
                        _mcp_create,
                        op_name="anthropic.beta.messages.create",
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

                    response = await _anthropic_retry_call(
                        _create,
                        op_name="anthropic.messages.create",
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
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                conversation.append({"role": "user", "content": tool_results})

            # Loop exits naturally when Claude responds with text only (no tool calls)

        except anthropic.APIStatusError as e:
            status = getattr(e, "status_code", None)
            if status is not None and 500 <= int(status) < 600:
                # Retries were already attempted inside
                # ``_anthropic_retry_call``. Surface a plain-language
                # error instead of the raw JSON body.
                await _send_friendly_anthropic_error(websocket, e)
            else:
                # 4xx: real bug in the request. Show the actual error so
                # the user can fix it (bad key, bad input, etc.).
                await websocket.send_json({"type": "error", "data": str(e)})
            return ""
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            await _send_friendly_anthropic_error(websocket, e)
            return ""
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
            model_name = _gemini_model_name()
            _log_gemini_model_once(model_name)
            # Pass the system instruction so Gemini stops prefixing its
            # replies with "@Gemini:" and stops writing fake back and
            # forth scripts when asked to chat with another AI.
            try:
                model = genai.GenerativeModel(
                    model_name,
                    system_instruction=GEMINI_SYSTEM_INSTRUCTION,
                )
            except TypeError:
                # Older SDK builds do not accept system_instruction. Fall
                # back to the no-arg constructor so we still return a
                # usable model. The behavior rule is enforced elsewhere
                # in the prompt body for the orchestration path.
                model = genai.GenerativeModel(model_name)

            history = []
            for msg in messages[:-1]:
                role = "user" if msg["role"] == "user" else "model"
                history.append(
                    {"role": role, "parts": [_gemini_content_to_text(msg.get("content", ""))]}
                )

            # ``send_message`` accepts a string, dict, Blob, or Image, but
            # NOT a list of Claude style image blocks. If the last message
            # was rewritten by ``transform_image_messages`` into a list of
            # Anthropic-shaped blocks, flatten it back to plain text here
            # so Gemini sees a normal prompt instead of tripping the SDK's
            # "Could not create Blob" error.
            last_content = _gemini_content_to_text(messages[-1].get("content", ""))
            chat = model.start_chat(history=history)
            # The google.generativeai SDK's streaming ``send_message(stream=True)``
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
            response = await _asyncio.to_thread(
                chat.send_message, last_content, stream=True
            )

            # Stream chunks. We guard ``chunk.text`` because the SDK's
            # ``.text`` property raises ValueError when a chunk has no
            # parts (which happens on the final SAFETY chunk). A bad
            # chunk must NOT abort the whole turn, we just skip it and
            # let the post-loop finish_reason check decide what to show.
            _CHUNK_STOP = object()
            _chunk_iter = iter(response)

            def _pull_next_chunk():
                try:
                    return next(_chunk_iter)
                except StopIteration:
                    return _CHUNK_STOP

            try:
                while True:
                    chunk = await _asyncio.to_thread(_pull_next_chunk)
                    if chunk is _CHUNK_STOP:
                        break
                    try:
                        text = chunk.text
                    except (ValueError, AttributeError):
                        # Empty-parts chunk. The reason lives on the
                        # final response which we inspect below.
                        continue
                    if text:
                        full_text += text
                        await websocket.send_json({"type": "token", "data": text})
            except genai.types.BlockedPromptException:
                # The PROMPT itself was blocked before any tokens were
                # emitted. The model never produced a response at all,
                # so there is no partial text to salvage. Send a
                # friendly error and do NOT send a done event, otherwise
                # the chat panel would render a blank bubble.
                await _send_friendly_gemini_error(
                    websocket,
                    _GEMINI_PROMPT_BLOCKED_MESSAGE,
                    reason_name="PROMPT_BLOCKED",
                )
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
            await websocket.send_json({"type": "done"})
        except Exception as e:
            error_text = str(e)
            friendly = _friendly_gemini_error(error_text)
            await websocket.send_json({"type": "error", "data": friendly})

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
    """

    def __init__(self, inner: WebSocket):
        self._inner = inner
        self.collected_text: list[str] = []

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


async def stream_group_broadcast(
    *,
    websocket: WebSocket,
    models: list[str],
    messages: list[dict],
    use_tools: bool = False,
) -> None:
    """Each model in *models* responds independently to the same message.

    Used when the user addresses multiple AIs collectively (e.g. "you guys",
    "both of you", "everyone"). Every AI receives the full conversation history
    and responds directly to the user. Models do not read each other's answers.

    Sends exactly one ``done`` event at the end. Per-model ``done`` events from
    the underlying provider streams are swallowed by the recording proxy so the
    panel sees a single "turn ended" signal.
    """
    if not models:
        return

    try:
        for model in models:
            await websocket.send_json({
                "type": "multi_ai_turn_start",
                "data": {"model": model, "round": 1},
            })
            proxy = _MultiAiTurnWebSocket(websocket)
            try:
                if model == "gemini":
                    await chat_service.stream_gemini(messages, proxy)  # type: ignore[arg-type]
                elif model == "claude":
                    if use_tools:
                        await chat_service.agent_anthropic(messages, proxy)  # type: ignore[arg-type]
                    else:
                        await chat_service.stream_anthropic(messages, proxy)  # type: ignore[arg-type]
                else:
                    await websocket.send_json(
                        {"type": "error", "data": f"Unknown model: {model}"}
                    )
            finally:
                await websocket.send_json({
                    "type": "multi_ai_turn_end",
                    "data": {"model": model, "round": 1},
                })
    finally:
        await websocket.send_json({"type": "done"})


chat_service = ChatService()
