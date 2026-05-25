import asyncio
import json as _json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from services.chat_history_store import chat_history_store
from services.chat_providers import (
    MULTI_AI_DEFAULT_ROUNDS,
    _resolve_chat_backend,
    _send_backend_active,
    chat_service,
    route_provider,
    stream_group_broadcast,
    stream_multi_ai_conversation,
)
from services.memory_trigger import handle as _handle_memory_trigger
from config import PROJECT_ROOT
from services.ostk import ostk
from services.settings_store import settings_store

router = APIRouter(tags=["chat"])


# Process-wide registry of live chat WebSockets. The shutdown hook in
# main.py iterates this set on SIGTERM / uvicorn reload and sends an
# error frame to every connected client before the socket is forcibly
# closed. Without this, a dev reload surfaces in the chat panel as
# "Connection dropped before the response finished" because the
# frontend's onclose fallback fires with no context. A plain set
# (not WeakSet) is fine here: the register / unregister calls bracket
# the websocket endpoint lifetime, so entries always get cleaned up.
_ACTIVE_CHAT_WEBSOCKETS: set[WebSocket] = set()

# Strong references to fire-and-forget background tasks. asyncio only keeps
# weak refs to tasks, so without this set the GC can destroy them before they
# complete and emit "Task was destroyed but it is pending!".
_BACKGROUND_TASKS: set[asyncio.Task] = set()

# In-memory pending peer-chat sessions. Keyed by pending_id (UUID string).
# Each entry holds: user_message, models, event (asyncio.Event), turns, created_at.
# Entries older than 300 s are pruned on each new peer-chat trigger.
_pending_peer_chats: dict[str, dict] = {}

# How long the WS handler waits for the user to pick a turn count before
# giving up and sending done. Tests can set this to a small value to avoid
# the 5-minute wait.
_PEER_CHAT_TURN_TIMEOUT: float = 300.0


def _fire_and_forget(coro) -> asyncio.Task:
    """Schedule a coroutine as a background task with a strong reference."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _register_active_ws(ws: WebSocket) -> None:
    _ACTIVE_CHAT_WEBSOCKETS.add(ws)


def _unregister_active_ws(ws: WebSocket) -> None:
    _ACTIVE_CHAT_WEBSOCKETS.discard(ws)


async def notify_active_websockets_of_shutdown(
    reason: str = "backend restarting",
) -> None:
    """Send an error frame to every live chat WS before shutdown.

    Called from main.py's shutdown event. Each send is best-effort: if
    the socket is already half-closed we still try the rest. We collect
    a snapshot of the set first because the ``finally`` blocks in
    ``chat_websocket`` will mutate the registry as sockets unwind.
    """
    snapshot = list(_ACTIVE_CHAT_WEBSOCKETS)
    for ws in snapshot:
        try:
            await ws.send_json({"type": "error", "data": reason})
        except Exception:
            # Socket already gone. Nothing useful we can do here, and
            # the outer shutdown sequence still needs to run for the
            # remaining sockets.
            pass
    # Give the OS/event-loop a brief window to flush the queued frames
    # before uvicorn force-closes the sockets. 200ms is plenty for local
    # loopback and small enough not to delay a real shutdown.
    if snapshot:
        try:
            await asyncio.sleep(0.2)
        except Exception:
            pass

# Where roadmap .md files land. Same directory the Files tab scans. Kept
# as a module-level constant so tests can monkeypatch it onto a tmp_path.
def __getattr__(name):  # PEP 562: resolve MYOS_FILES_DIR at call-time
    if name == "MYOS_FILES_DIR":
        from services.files_dir import get_files_dir
        return get_files_dir()
    raise AttributeError(name)

# Phrases that route to the "create tasks from this roadmap" handler.
# Matched case-insensitively against the full user message (after strip).
# The patterns deliberately accept "the roadmap", "this roadmap", "that
# roadmap", etc., so conversational phrasing lands. Keep the list tight
# to avoid over-matching on unrelated "task" talk.
_ROADMAP_TO_TASKS_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bcreate\s+tasks?\s+from\s+(?:this|the|that|my)?\s*roadmap\b",
        r"\bmake\s+tasks?\s+from\s+(?:this|the|that|my)?\s*roadmap\b",
        r"\bturn\s+(?:this|the|that|my)?\s*roadmap\s+into\s+tasks?\b",
        r"\bconvert\s+(?:this|the|that|my)?\s*roadmap\s+(?:in)?to\s+tasks?\b",
        r"\bbreak\s+(?:this|the|that|my)?\s*roadmap\s+(?:down\s+)?into\s+tasks?\b",
        r"\bbuild\s+tasks?\s+from\s+(?:this|the|that|my)?\s*roadmap\b",
        r"\bgenerate\s+tasks?\s+from\s+(?:this|the|that|my)?\s*roadmap\b",
    ]
)


def is_roadmap_to_tasks_request(text: str) -> bool:
    """Return True when the user is asking to convert a roadmap into tasks.

    Used by the chat router to intercept these messages before normal AI
    routing. Accepts phrasings like "create tasks from this roadmap",
    "make tasks from the roadmap", "turn the roadmap into tasks", etc.
    """
    if not isinstance(text, str):
        return False
    candidate = text.strip()
    if not candidate:
        return False
    return any(p.search(candidate) for p in _ROADMAP_TO_TASKS_PATTERNS)


def _latest_roadmap_path() -> Optional[Path]:
    """Return the most recent roadmap ``.md`` on disk, or None.

    Prefers ``roadmap.md`` (the stable name the Roadmap template writes
    for chat's shortcut) and falls back to the newest timestamped
    ``roadmap-*.md`` or any file whose front matter declares
    ``kind: roadmap``.
    """
    base = MYOS_FILES_DIR
    if not base.exists():
        return None

    stable = base / "roadmap.md"
    if stable.exists():
        return stable

    candidates: list[Path] = []
    for path in base.glob("*.md"):
        if not path.is_file():
            continue
        try:
            head = path.read_text()[:400]
        except OSError:
            continue
        if "kind: roadmap" in head or path.name.startswith("roadmap-"):
            candidates.append(path)
    if not candidates:
        return None
    # Newest mtime wins.
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]

GIPHY_API_KEY = os.environ.get("GIPHY_API_KEY", "uac5bYV974kGSu3Pe0B92ChNrIQypZ0Y")

CONTEXT_KEYWORDS = {"tasks", "needles", "task", "needle", "focus", "agents", "status"}

CALENDAR_KEYWORDS = {"calendar", "meeting", "meetings", "schedule", "today", "tomorrow", "event", "field trip"}


# Map @mention names to provider keys
MODEL_ALIASES = {
    "claude": "claude",
    "anthropic": "claude",
    "gemini": "gemini",
    "google": "gemini",
}

MENTION_RE = re.compile(r"@(\w+)", re.IGNORECASE)


def parse_mentions(text: str) -> list[str]:
    """Extract unique model names from @mentions in text."""
    mentions = MENTION_RE.findall(text)
    models = []
    seen = set()
    for m in mentions:
        key = MODEL_ALIASES.get(m.lower())
        if key and key not in seen:
            models.append(key)
            seen.add(key)
    return models


def strip_mentions(text: str) -> str:
    """Remove @model mentions from the text for cleaner prompts."""
    return MENTION_RE.sub(lambda m: "" if m.group(1).lower() in MODEL_ALIASES else m.group(0), text).strip()


# Phrases that mean "I want these two AIs to actually talk to each other".
# Matched case-insensitively as substrings against the text AFTER mentions
# are stripped, so "@gemini chat with @claude" trips "chat with". The list
# deliberately covers stems and alternate conjugations so common phrasings
# like "talks to", "talking to", "debating", "arguing with", etc. all land
# in the orchestration path. The ordering does not matter, substrings do
# their own matching.
#
# Do NOT add plain "chat" or plain "talk" to this list. Those words appear
# in normal single-model questions ("what's a chat?", "let's talk about
# my taxes") and would over-route to the orchestration path.
_CONVERSATION_KEYWORDS: tuple[str, ...] = (
    "chat with",
    "talk to",
    "talks to",
    "talking to",
    "have a conversation",
    "discuss with",
    "discusses with",
    "discussing with",
    "debate",
    "debates",
    "debating",
    "argue with",
    "arguing with",
    "back and forth",
    "exchange with",
    "exchange messages",
    "respond to each other",
    "work this out",
    "work it out",
)

# Regex patterns that allow words between the verb and the preposition,
# so phrasings like "discuss your favorite song with @gemini" or
# "argue about politics with claude" still trip the orchestration. Each
# pattern allows up to 60 characters between the verb stem and the
# preposition, which fits a normal sentence object without overmatching
# across an entire message. Built once at import time.
_CONVERSATION_REGEX_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bdiscuss(?:es|ing)?\b.{0,60}?\bwith\b",
        r"\bchat(?:s|ting)?\b.{0,60}?\bwith\b",
        r"\btalk(?:s|ing)?\b.{0,60}?\b(?:to|with)\b",
        r"\bargue(?:s|d)?\b.{0,60}?\bwith\b",
        r"\barguing\b.{0,60}?\bwith\b",
        r"\bdebate(?:s|d)?\b.{0,60}?\bwith\b",
        r"\bdebating\b.{0,60}?\bwith\b",
        r"\bexchange(?:s|d)?\b.{0,60}?\b(?:with|messages?)\b",
    ]
)

# All provider keys available for group broadcast.
ALL_MODELS: tuple[str, ...] = ("claude", "gemini")

# Patterns that mean the user is addressing multiple AIs as a group.
# When any of these match (and no debate intent is detected), every AI in
# ALL_MODELS responds to the same message independently via broadcast mode.
_COLLECTIVE_ADDRESS_RE: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\byou\s+guys\b",
        r"\byou\s+two\b",
        r"\byou\s+both\b",
        r"\bboth\s+of\s+you\b",
        r"\ball\s+of\s+you\b",
        r"\beveryone\b",
        r"\beverybody\b",
        r"\byou\s+all\b",
        r"\by'all\b",
        r"\bthe\s+two\s+of\s+you\b",
        r"\bboth\s+AIs\b",
        r"\bboth\s+models\b",
    ]
)


def is_collective_address(text: str) -> bool:
    """Return True if the message addresses all AIs as a group.

    Patterns like "you guys", "both of you", and "everyone" mean the user
    wants every participating AI to respond, not just one.
    """
    if not isinstance(text, str) or not text:
        return False
    return any(p.search(text) for p in _COLLECTIVE_ADDRESS_RE)


def is_conversation(text: str) -> bool:
    """Return True if ``text`` asks two AIs to actually talk to each other.

    The caller already knows there are at least two mentions in the
    message. This function decides whether those two mentions should
    trigger the multi AI orchestration loop or just fall back to the
    legacy "first mention wins" single model path. Two passes:
    first the literal substring keywords for tight phrasings like
    "chat with" or "back and forth", then the regex patterns that
    allow an object phrase between a verb and its preposition like
    "discuss your favorite song with" or "argue about politics with".
    """
    if not isinstance(text, str) or not text:
        return False
    lowered = text.lower()
    if any(keyword in lowered for keyword in _CONVERSATION_KEYWORDS):
        return True
    return any(pattern.search(lowered) for pattern in _CONVERSATION_REGEX_PATTERNS)


# Regex used to find a second model referenced by bare name after a
# conversation keyword. Built once from MODEL_ALIASES so any new alias
# automatically works. The trailing word boundary stops "claudette" or
# "geminids" from matching by accident.
_BARE_MODEL_RE = re.compile(
    r"\b(" + "|".join(sorted(MODEL_ALIASES.keys(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


# Pronouns that signal the user themself is the other party in the
# conversation phrase ("@gemini chat with me", "talk to us about X").
# When the first noun-like token after a conversation keyword is one of
# these, the router must NOT pull in the host model as a second speaker,
# because the user is the interlocutor, not another AI.
_SELF_REFERENCE_TARGETS: frozenset[str] = frozenset({
    "me", "us", "myself", "yourself", "ourselves", "you",
})


def conversation_target_is_user(text: str) -> bool:
    """Return True if the conversation phrase's object is the user.

    Scans the first word right after each conversation keyword and
    returns True if that word is a self-reference pronoun like ``me``,
    ``us``, or ``myself``. Punctuation around the word is stripped
    before the comparison. Used to suppress the host-fallback path so
    ``@gemini chat with me about my day`` stays a single-AI request.

    Only the first token matters. ``chat with me about X`` has ``me``
    as the object. ``chat with claude about me`` has ``claude`` as the
    object and that case should NOT suppress host fallback.
    """
    if not isinstance(text, str) or not text:
        return False
    lowered = text.lower()
    for keyword in _CONVERSATION_KEYWORDS:
        idx = lowered.find(keyword)
        if idx == -1:
            continue
        tail = lowered[idx + len(keyword):].strip()
        if not tail:
            continue
        first_word = tail.split()[0]
        # Strip punctuation like "me," or "us!" so the comparison matches.
        first_word = re.sub(r"[^a-z']+", "", first_word)
        if first_word in _SELF_REFERENCE_TARGETS:
            return True
    return False


def infer_second_model(text: str, already_mentioned: list[str]) -> Optional[str]:
    """Infer a second model referenced by bare name in a conversation phrase.

    Tori often types ``@gemini chat with claude about X`` where only the
    first model has an ``@``. ``parse_mentions`` only catches explicit
    ``@mentions`` so the router used to see a single model, skip the
    orchestration path, and answer with a single bubble. This helper
    looks for a bare model name (``claude``, ``gemini``, ``anthropic``,
    ``google``) that appears right after one of the conversation
    keywords (``chat with``, ``talk to``, ``debate``, ...) and returns
    its canonical provider key if it is not already in
    ``already_mentioned``.

    Returns ``None`` if no new model is inferred. Only returns a model
    name that is actually wired up to a provider via ``MODEL_ALIASES``.
    """
    if not isinstance(text, str) or not text:
        return None
    lowered = text.lower()
    already = set(already_mentioned)
    for keyword in _CONVERSATION_KEYWORDS:
        idx = lowered.find(keyword)
        if idx == -1:
            continue
        tail = lowered[idx + len(keyword):]
        # Scan only the short window right after the keyword. Four words
        # is enough to catch "chat with claude a few times" and
        # "debate claude about X" without dragging in unrelated uses of
        # the bare model name later in the same sentence.
        window = tail.strip().split()
        window = " ".join(window[:4])
        match = _BARE_MODEL_RE.search(window)
        if not match:
            continue
        key = MODEL_ALIASES.get(match.group(1).lower())
        if key and key not in already:
            return key
    return None


CHAT_MEMORY_MSG_LIMIT = 10


def build_memory_context(current_tab_id: str = "") -> list[dict]:
    """Build a prior-conversation context block if chat memory is enabled.

    Returns a list with a single user-role message summarizing the prior
    conversation, or an empty list when memory is disabled or there are
    no prior messages.
    """
    if not settings_store.get("chat_memory_enabled", True):
        return []

    prior = chat_history_store.get_prior_messages(
        current_tab_id=current_tab_id,
        limit=CHAT_MEMORY_MSG_LIMIT,
    )
    if not prior:
        return []

    lines = ["[Prior conversation for context]"]
    for msg in prior:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"{role_label}: {msg['content']}")
    lines.append("[End of prior conversation]")
    return [{"role": "user", "content": "\n".join(lines)}]


GIF_RE = re.compile(r"\[gif:(https?://[^\]]+)\]")


def _extract_gif_frames(url: str, max_frames: int = 4) -> list[dict]:
    """Download a GIF and extract evenly spaced frames as base64 PNG blocks.

    Returns a list of Anthropic image blocks. If the URL cannot be fetched
    or decoded, returns a single URL-based image block as a fallback so
    Claude at least sees the first frame.
    """
    import base64
    import io
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read()
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        n_frames = getattr(img, "n_frames", 1)
        if n_frames <= 1:
            # Static image, just one frame.
            return [{"type": "image", "source": {"type": "url", "url": url}}]
        # Pick evenly spaced frame indices including the first and last.
        take = min(max_frames, n_frames)
        indices = [round(i * (n_frames - 1) / (take - 1)) for i in range(take)] if take > 1 else [0]
        blocks: list[dict] = []
        for idx in indices:
            img.seek(idx)
            frame = img.convert("RGB")
            buf = io.BytesIO()
            frame.save(buf, format="PNG", optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            })
        return blocks
    except Exception:
        # Fallback: single URL-based image block.
        return [{"type": "image", "source": {"type": "url", "url": url}}]


def transform_image_messages(messages: list[dict]) -> list[dict]:
    """Convert image data (GIF URLs and pasted base64 images) into content blocks for Claude vision.

    For animated GIFs, extracts up to 4 evenly spaced frames and sends
    them all so the model can see motion, not just the first frame.
    """
    result = []
    for msg in messages:
        content = msg.get("content", "")
        image_data = msg.get("image")
        # Preserve the model field so downstream providers (Gemini) can
        # label assistant messages by their source model. Without this,
        # multi-AI chats lose cross-model attribution and Gemini thinks
        # it has no access to Claude's prior responses.
        source_model = msg.get("model")

        if image_data and isinstance(image_data, str) and image_data.startswith("data:"):
            # Pasted image: data:image/png;base64,...
            blocks: list[dict] = []
            header, b64 = image_data.split(",", 1)
            media_type = header.split(":")[1].split(";")[0]
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            })
            text = content if isinstance(content, str) and content.strip() else "The user pasted this image. Describe what you see."
            blocks.append({"type": "text", "text": text})
            new_msg: dict = {"role": msg["role"], "content": blocks}
            if source_model:
                new_msg["model"] = source_model
            result.append(new_msg)
        elif isinstance(content, str) and GIF_RE.search(content):
            blocks = []
            remaining = content
            for match in GIF_RE.finditer(content):
                url = match.group(1)
                # Single frame is enough for reaction GIFs and keeps response fast.
                blocks.extend(_extract_gif_frames(url, max_frames=1))
                remaining = remaining.replace(match.group(0), "").strip()
            text = (
                remaining
                or "The user sent this GIF. React to it briefly and playfully."
            )
            blocks.append({"type": "text", "text": text})
            new_msg = {"role": msg["role"], "content": blocks}
            if source_model:
                new_msg["model"] = source_model
            result.append(new_msg)
        else:
            new_msg = {"role": msg["role"], "content": content}
            if source_model:
                new_msg["model"] = source_model
            result.append(new_msg)
    return result


async def build_context() -> str:
    try:
        tasks = await ostk.list_tasks(status="open")
        lines = ["Open tasks:"]
        for t in tasks[:20]:
            lines.append(f"  {t.get('id')} [{t.get('priority')}] {t.get('title')}")
        return "\n".join(lines)
    except Exception:
        return ""


def _build_workspace_doc_index() -> str:
    """Return a compact list of draft and spec docs (filenames only, no content).

    Lets the model answer 'what is the status of X' questions without filesystem
    discovery calls. Draft = in-progress; spec = promoted/complete. Capped at
    ~50 entries to keep the injected block small (→1699).
    """
    parts: list[str] = []
    try:
        draft_dir = PROJECT_ROOT / "docs" / "draft"
        if draft_dir.is_dir():
            drafts = sorted(
                p.name for p in draft_dir.iterdir()
                if p.suffix == ".md" and not p.name.startswith("_")
            )[:25]
            if drafts:
                parts.append("Draft docs (in progress): " + ", ".join(drafts))
    except Exception:
        pass
    try:
        spec_dir = PROJECT_ROOT / "docs" / "spec"
        if spec_dir.is_dir():
            specs = sorted(
                p.name for p in spec_dir.iterdir()
                if p.suffix == ".md" and not p.name.startswith("_")
            )[:25]
            if specs:
                parts.append("Spec docs (promoted/complete): " + ", ".join(specs))
    except Exception:
        pass
    return "\n".join(parts)


async def build_baseline_context() -> str:
    """Always-on system context injected into every chat turn."""
    lines = []
    os_name = settings_store.get("os_name") or "myOS"
    user_name = settings_store.get("user_name") or ""
    standing = settings_store.get("standing_instructions") or ""
    lines.append(
        f"You are the AI assistant inside {os_name}, a personal AI operating system running locally on the user's machine."
    )
    if user_name:
        lines.append(f"The user's name is {user_name}.")
    if standing:
        lines.append(f"STANDING INSTRUCTIONS (from the user, always apply):\n{standing}")
    try:
        needles = await ostk.list_tasks(status="open")
        if needles:
            lines.append("Current open work items:")
            for n in needles[:10]:
                pri = n.get("priority", "")
                title = n.get("title", "")
                lines.append(f"  [{pri}] {title}")
    except Exception:
        pass
    doc_index = _build_workspace_doc_index()
    if doc_index:
        lines.append(doc_index)
    return "\n".join(lines)


def should_inject_context(text: str) -> bool:
    words = set(text.lower().split())
    return bool(words & CONTEXT_KEYWORDS)


def should_inject_calendar(text: str) -> bool:
    """Return True if the message likely relates to calendar or schedule."""
    words = set(re.split(r"\W+", text.lower()))
    return bool(words & CALENDAR_KEYWORDS)


async def build_calendar_context() -> str:
    """Return a short summary of today's calendar events.

    Returns an empty string if the user is not authenticated or an error occurs.
    """
    try:
        from services.google_auth import is_authenticated
        if not is_authenticated():
            return ""
        from services import calendar as cal_service
        events = await cal_service.get_today_events()
        if not events:
            return ""
        parts = []
        for ev in events:
            title = ev.get("summary", "Untitled")
            start = ev.get("start", {})
            end = ev.get("end", {})
            start_val = start.get("dateTime") or start.get("date") or ""
            end_val = end.get("dateTime") or end.get("date") or ""
            # Format times as HHam/pm if possible
            def _fmt(dt_str: str) -> str:
                if not dt_str:
                    return ""
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(dt_str)
                    return dt.strftime("%-I:%M%p").lower()
                except Exception:
                    return dt_str[:5]
            start_fmt = _fmt(start_val)
            end_fmt = _fmt(end_val)
            if start_fmt and end_fmt:
                parts.append(f"{start_fmt}-{end_fmt}: {title}")
            elif start_fmt:
                parts.append(f"{start_fmt}: {title}")
            else:
                parts.append(title)
        return "Today's calendar: " + "; ".join(parts)
    except Exception:
        return ""


async def call_model(provider: str, messages: list[dict], websocket: WebSocket, label: str = "", use_tools: bool = False, tab_id: str = "", claude_tier: str = "", plan_mode: bool = False):
    """Call a single model and stream the response, returning the full text."""
    if label:
        await websocket.send_json({"type": "model_label", "data": label})

    # Register this chat turn as an agent so it appears in the Agents
    # page and Activity feed regardless of which provider answered.
    #
    # Naming: when the user message starts with a saa verb ("saa foo",
    # "diagnose foo", "ship foo"), use a slug of the task text so each
    # ask becomes its own visible agent row. Otherwise fall back to a
    # per-tab generic name so idle chat still shows up but does not
    # clutter the list with one row per passing remark.
    last_user = ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                last_user = c
            break

    import re as _re
    _SAA_VERBS = ("saa", "diagnose", "ship", "implement", "build", "fix")
    def _slugify_task(text: str) -> str:
        s = text.strip().lower()
        s = _re.sub(r"[^a-z0-9]+", "-", s).strip("-")
        return s[:40] or "task"

    stripped = (last_user or "").strip()
    lowered = stripped.lower()
    matched_verb: str | None = None
    for verb in _SAA_VERBS:
        if lowered.startswith(verb + " ") or lowered == verb:
            matched_verb = verb
            break
    if matched_verb:
        task_text = stripped[len(matched_verb):].strip() or matched_verb
        chat_agent_name = f"{matched_verb}-{_slugify_task(task_text)}"
    else:
        chat_agent_name = f"chat-{tab_id[:8]}" if tab_id else "chat-default"
    try:
        from routers.agents import register_chat_session
        _fire_and_forget(register_chat_session(
            chat_agent_name,
            model=provider,
            prompt_preview=last_user,
        ))
    except Exception:
        pass

    # Rewrite cross-model assistant turns so this provider only sees its own
    # prior responses as role:assistant. Other models' turns become user-role
    # context with a [ModelName]: prefix, enabling cross-model references in
    # single-model follow-ups after an all-mode broadcast exchange.
    from services.chat_providers import _transform_messages_for_provider
    messages = _transform_messages_for_provider(messages, provider)

    full_text = ""
    status = "completed"
    try:
        if provider == "claude":
            if use_tools:
                full_text = await chat_service.agent_anthropic(messages, websocket, tab_id=tab_id, plan_mode=plan_mode)
            else:
                full_text = await chat_service.stream_anthropic(messages, websocket, tab_id=tab_id, claude_tier=claude_tier)
        elif provider == "gemini":
            full_text = await chat_service.stream_gemini(messages, websocket)
        else:
            await websocket.send_json({"type": "error", "data": f"Unknown model: {provider}"})
            status = "failed"
    except Exception:
        status = "failed"
        raise
    finally:
        try:
            from routers.agents import complete_chat_session
            _fire_and_forget(complete_chat_session(chat_agent_name, status=status))
        except Exception:
            pass

    return full_text


@router.get("/api/chat/history")
async def get_chat_history():
    """Return all saved chat tabs and the active tab id."""
    return chat_history_store.load()


@router.put("/api/chat/history")
async def put_chat_history(body: dict):
    """Replace the saved chat history with the supplied tabs and active id."""
    saved = chat_history_store.save(body)
    return {"result": "saved", "data": saved}


@router.delete("/api/chat/history")
async def clear_chat_history():
    """Clear all saved chat tabs."""
    chat_history_store.clear()
    return {"result": "cleared"}


async def _spawn_roadmap_agent() -> Optional[str]:
    """Spawn a Roadmap agent via the internal ASGI app. Returns agent name or None."""
    import time as _time
    from main import app as _app
    name = f"chat-roadmap-build-{int(_time.time())}"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_app),
            base_url="http://testserver",
            timeout=10.0,
        ) as client:
            resp = await client.post(
                "/agents/spawn",
                json={"name": name, "template": "Roadmap", "prompt": ""},
            )
            if resp.status_code < 300:
                return name
    except Exception:
        pass
    return None


async def _wait_for_roadmap(timeout: float = 90.0) -> Optional[Path]:
    """Poll until roadmap.md appears on disk or timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        path = _latest_roadmap_path()
        if path is not None:
            return path
        await asyncio.sleep(2.0)
    return None


@router.post("/api/chat/roadmap/create-tasks")
async def chat_create_tasks_from_roadmap(body: Optional[dict] = None):
    """Convert the latest roadmap ``.md`` into tasks on user request.

    The torichat UI calls this endpoint when the user types a phrase
    like "create tasks from this roadmap". It reads the most recent
    roadmap under ``~/.myos/files/``, harvests actionable items via
    :func:`services.automation_outputs.parse_roadmap_items`, and runs
    the shared :func:`auto_create_tasks` pass so labels and dedup work
    the same way they do for every other automation.

    Request body is optional. When present it may carry:

        {
          "roadmap_path": "~/.myos/files/roadmap.md",
          "agent_name": "e2e-roadmap-check-123"
        }

    ``agent_name`` is used to tag the source name stored on each
    generated task so the test-artifact sweep can catch them. When the
    agent name starts with ``e2e-``, the source name gets the same
    ``e2e-`` prefix. This keeps e2e probe tasks cleanable even when the
    roadmap filename itself does not carry the prefix.

    Returns:

        {
          "status": "ok" | "no_roadmap",
          "reply": "<short chat-ready message>",
          "created": [{"id": "...", "title": "..."}, ...],
          "roadmap_path": "..."
        }

    When no roadmap is found, ``status`` is ``"no_roadmap"`` and
    ``reply`` is a plain-language suggestion. The caller renders the
    reply as an assistant message in the current chat tab.
    """
    from services import automation_outputs as _auto

    body = body or {}
    explicit_path = str(body.get("roadmap_path") or "").strip()
    agent_name = str(body.get("agent_name") or "").strip()
    roadmap_path: Optional[Path]
    if explicit_path:
        roadmap_path = Path(explicit_path).expanduser()
        if not roadmap_path.exists():
            roadmap_path = None
    else:
        roadmap_path = _latest_roadmap_path()

    if roadmap_path is None or not roadmap_path.exists():
        # No roadmap on disk yet — spawn one and wait for it to land.
        spawned_name = await _spawn_roadmap_agent()
        if spawned_name:
            roadmap_path = await _wait_for_roadmap(timeout=90.0)
        if roadmap_path is None or not roadmap_path.exists():
            return {
                "status": "no_roadmap",
                "reply": (
                    "I started building a roadmap but it isn't ready yet. "
                    "Check the Agents page and try again in a moment."
                )
                if spawned_name
                else (
                    "I don't see a recent roadmap. Open or create one "
                    "first, then ask again."
                ),
                "created": [],
                "roadmap_path": "",
            }

    try:
        content = roadmap_path.read_text()
    except OSError as exc:
        return {
            "status": "no_roadmap",
            "reply": f"I could not read that roadmap: {exc}",
            "created": [],
            "roadmap_path": roadmap_path.as_posix(),
        }

    items = _auto.parse_roadmap_items(content)
    if not items:
        return {
            "status": "empty",
            "reply": (
                "I read the roadmap but could not find any items to "
                "turn into tasks. Add bullets under a Milestones or "
                "Next steps section and try again."
            ),
            "created": [],
            "roadmap_path": roadmap_path.as_posix(),
        }

    source_name = roadmap_path.stem or "roadmap"
    # When an e2e- prefixed agent drives this endpoint, stamp the e2e-
    # prefix onto the source name so the task-artifact sweep can catch
    # the generated tasks by their description. Production agents (no
    # e2e- prefix) are unaffected.
    if agent_name.lower().startswith("e2e-") and not source_name.lower().startswith("e2e-"):
        source_name = f"e2e-{source_name}"
    created = await _auto.auto_create_tasks(
        items,
        source_name=source_name,
        automation_kind="roadmap_from_chat",
        priority="P2",
    )
    count = len(created)
    if count == 0:
        reply = (
            "Those items were already open needles, so nothing new was "
            "created."
        )
    else:
        noun = "needle" if count == 1 else "needles"
        reply = (
            f"Created {count} {noun} from {roadmap_path.name}. "
            "See them on the [Needles page](/tasks)."
        )
    return {
        "status": "ok",
        "reply": reply,
        "created": created,
        "roadmap_path": roadmap_path.as_posix(),
    }


@router.post("/api/chat/peer/start")
async def start_peer_chat(body: dict):
    """Resume a pending peer-chat session with a user-chosen turn count.

    Called by the frontend after the user clicks one of the 1/2/3-turn
    buttons in the PeerChatTurnsPicker. Validates the turn count, stores
    it in the pending-session dict, and signals the asyncio Event that
    the WebSocket handler is blocking on, causing it to resume and run
    stream_multi_ai_conversation with the chosen turn count.
    """
    pending_id = (body or {}).get("pending_id", "")
    raw_turns = (body or {}).get("turns")
    turns = max(1, min(3, int(raw_turns) if raw_turns is not None else 2))
    state = _pending_peer_chats.get(pending_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session expired or not found")
    state["turns"] = turns
    state["event"].set()
    return {"ok": True, "turns": turns}


@router.post("/api/chat/tools/run")
async def run_chat_tool(body: dict, request: Request = None):
    from services.rate_limit import rate_limit_check
    if request is not None:
        rate_limit_check(request, "chat.send")
    """Execute a single chat tool by name and return the result string.

    Mirrors the inline tool dispatch the chat WebSocket performs after
    the model emits a ``tool_use`` block. Used by the demo smoke script
    so each chat-driven surface can be hit by a plain HTTP curl without
    booting a full streaming chat session. Body shape:

        {"name": "build_tasks_from_file", "input": {"file_path": "..."}}

    Returns ``{"result": "<tool stdout>"}`` on success, or HTTP 400 if
    the tool name is unknown / the input is malformed.
    """
    from services.tool_executor import execute_tool

    name = (body or {}).get("name")
    if not name or not isinstance(name, str):
        raise HTTPException(status_code=400, detail="name is required")
    raw_input = (body or {}).get("input") or {}
    if not isinstance(raw_input, dict):
        raise HTTPException(status_code=400, detail="input must be an object")
    try:
        result = await execute_tool(name, raw_input)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing field: {exc}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"result": result}


@router.get("/api/giphy/search")
async def search_giphy(q: str = Query(...), limit: int = Query(default=12, le=25)):
    """Search Giphy for GIFs."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.giphy.com/v1/gifs/search",
            params={"api_key": GIPHY_API_KEY, "q": q, "limit": limit, "rating": "g"},
        )
        data = resp.json()
        return [
            {
                "id": g["id"],
                "url": g["images"]["fixed_height"]["url"],
                "preview": g["images"]["fixed_height_small"]["url"],
                "title": g["title"],
            }
            for g in data.get("data", [])
        ]


_SLASH_HELP_TEXT = (
    "Available commands:\n"
    "  /status   . Show system status\n"
    "  /tasks    . List open needles\n"
    "  /commit <message> . Commit with a message\n"
    "  /agents   . Show active and recent agents\n"
    "  /build-spec <slug> . Claim a spec and flip its status to Building\n"
    "  /mcp      . Info about MCP server management\n"
    "  /help     . Show this list"
)

_MCP_INFO_TEXT = (
    "MCP servers are managed in Settings. "
    "Go to Settings to add or remove servers."
)


async def _handle_slash_command(text: str, websocket: WebSocket, tab_id: str = "") -> bool:
    """Intercept messages starting with ``/`` and run the matching command.

    Returns True if a command was handled (caller should skip AI routing).
    Returns False if the message is not a slash command.
    """
    if not isinstance(text, str) or not text.startswith("/"):
        return False

    parts = text.strip().split(None, 1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    result = ""

    if command == "/help":
        result = _SLASH_HELP_TEXT

    elif command == "/mcp":
        result = _MCP_INFO_TEXT

    elif command == "/status":
        try:
            result = await ostk.os_status()
        except Exception as exc:
            result = f"Could not get status: {exc}"

    elif command == "/tasks":
        try:
            tasks = await ostk.list_tasks(status="open")
            if not tasks:
                result = "No open needles."
            else:
                lines = [f"Open needles ({len(tasks)}):"]
                for t in tasks[:30]:
                    tid = t.get("id", "?")
                    title = t.get("title", "Untitled")
                    priority = t.get("priority", "")
                    status = t.get("status", "")
                    line = f"  {tid}"
                    if priority:
                        line += f" [{priority}]"
                    line += f" {title}"
                    if status:
                        line += f" ({status})"
                    lines.append(line)
                if len(tasks) > 30:
                    lines.append(f"  ... and {len(tasks) - 30} more")
                result = "\n".join(lines)
        except Exception as exc:
            result = f"Could not list tasks: {exc}"

    elif command == "/commit":
        if not args.strip():
            result = "Usage: /commit <message>"
        else:
            try:
                result = await ostk.commit(args.strip())
                if not result.strip():
                    result = "Commit completed."
            except Exception as exc:
                result = f"Commit failed: {exc}"

    elif command == "/agents":
        try:
            resp = await _fetch_agents_list()
            result = resp
        except Exception as exc:
            result = f"Could not list agents: {exc}"

    elif command == "/build-spec":
        slug = args.strip()
        if not slug or "/" in slug or "\\" in slug or ".." in slug:
            result = "Usage: /build-spec <slug>  (the spec name without path or extension)"
        else:
            try:
                import os as _os
                from pathlib import Path as _Path
                from routers.specs import _ClaimBody, claim_spec

                _user_specs = _Path(_os.environ.get("MYOS_USER_SPECS_DIR", _os.path.expanduser("~/.myos/specs")))
                _project_root = _Path(__file__).resolve().parent.parent
                _resolved: Optional[str] = None

                for _candidate in [
                    _user_specs / f"{slug}.md",
                    _project_root / "docs" / "draft" / f"{slug}.md",
                ]:
                    if _candidate.exists():
                        _resolved = str(_candidate.resolve())
                        break

                if _resolved is None:
                    result = (
                        f"Spec '{slug}' not found. "
                        f"Checked ~/.myos/specs/{slug}.md and docs/draft/{slug}.md."
                    )
                else:
                    _agent = f"chat-{tab_id[:8]}" if tab_id else "chat-slash"
                    _resp = await claim_spec(_resolved, _ClaimBody(agent=_agent, source="slash"))
                    _n = len(_resp.get("task_ids", []))
                    result = (
                        f"Spec '{slug}' claimed — {_n} task{'s' if _n != 1 else ''} queued. "
                        "Status is now Building."
                    )
            except Exception as exc:
                result = f"Could not claim spec '{slug}': {exc}"

    else:
        result = "Unknown command. Type /help for available commands."

    await websocket.send_json({"type": "text", "data": result})
    await websocket.send_json({"type": "done"})
    return True


async def _fetch_agents_list() -> str:
    """Fetch and format the agents list for the /agents slash command."""
    _port = os.environ.get("PORT", os.environ.get("UVICORN_PORT", "8000"))
    data = None
    for _scheme in ("https", "http"):
        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(
                    f"{_scheme}://127.0.0.1:{_port}/api/agents", timeout=5
                )
                data = resp.json()
                break
        except Exception:
            continue
    if data is None:
        # Fall back to ostk kernel ps if neither scheme reaches the backend.
        ps = await ostk.kernel_ps()
        agents = ps.get("agents", [])
        if not agents:
            return "No agents found."
        lines = ["Agents:"]
        for a in agents:
            name = a.get("name", "?")
            status = a.get("status", "unknown")
            lines.append(f"  {name} ({status})")
        return "\n".join(lines)

    agents = data if isinstance(data, list) else data.get("agents", [])
    if not agents:
        return "No agents found."

    running = [a for a in agents if a.get("status") == "running"]
    recent = [a for a in agents if a.get("status") != "running"][:10]

    lines = []
    if running:
        lines.append(f"Running ({len(running)}):")
        for a in running:
            lines.append(f"  {a.get('name', '?')}")
    if recent:
        lines.append(f"Recent ({len(recent)}):")
        for a in recent:
            name = a.get("name", "?")
            status = a.get("status", "unknown")
            lines.append(f"  {name} ({status})")
    return "\n".join(lines) if lines else "No agents found."


def build_thread_context(messages: list[dict], thread_id: str) -> list[dict]:
    """Return only the messages that belong to the given thread.

    The root message (whose id == thread_id) is included first, followed
    by all messages whose thread_id field matches. This isolates the AI's
    context so replies in thread A never see thread B's content.

    ``messages`` is the full flat list the client sent (already cleaned).
    Each message may carry an optional ``thread_id`` and ``id`` field.
    """
    result: list[dict] = []
    for msg in messages:
        msg_id = msg.get("id", "")
        msg_thread_id = msg.get("thread_id")
        # Include the root message itself.
        if msg_id == thread_id and not msg_thread_id:
            result.append(msg)
        # Include all messages in this thread.
        elif msg_thread_id == thread_id:
            result.append(msg)
    return result


# Terminal frame types. When one of these is sent, the frontend considers
# the turn complete and tears down its "thinking" state. If the server
# exits a turn without emitting one, useWebSocket's onclose fallback fires
# the "Connection dropped before the response finished" banner. The
# _TerminalTrackingWS wrapper below watches for these so we can emit a
# guaranteed fallback in a finally block.
_TERMINAL_FRAME_TYPES = {"done", "error"}


class _TerminalTrackingWS:
    """WebSocket facade that records whether a terminal frame was sent.

    We want a finally block in the per-message handler to emit a fallback
    ``error`` frame if, and only if, no terminal frame (``done`` / ``error``)
    has been sent during the turn. The cleanest way to observe that
    without touching every provider is to wrap the real ``WebSocket`` with
    a thin facade that forwards everything but flips ``terminal_sent``
    when it sees a terminal ``type`` on a ``send_json`` call.

    The facade is a drop-in replacement: ``send_json``, ``send_text``,
    ``receive_json``, ``accept``, ``close`` all pass straight through, and
    any attribute the underlying WS exposes (``.client_state``, etc.) is
    delegated via ``__getattr__``.
    """

    def __init__(self, inner: WebSocket, tab_id: str = "") -> None:
        self._inner = inner
        self.terminal_sent: bool = False
        self._tab_id = tab_id

    async def send_json(self, data: dict) -> None:
        # Inject tab_id into every streaming frame so the frontend can filter by tab
        is_terminal = (
            isinstance(data, dict) and data.get("type") in _TERMINAL_FRAME_TYPES
        )
        if isinstance(data, dict) and self._tab_id and "tab_id" not in data:
            data = {**data, "tab_id": self._tab_id}
        # Send first, then flip terminal_sent. The previous order flipped
        # the flag BEFORE awaiting the inner send. If the inner send
        # raised (socket already half-closed, TLS reset, browser nav
        # mid-turn), the flag was True but no terminal frame ever
        # reached the client. The chat-router fallback at the end of
        # the turn loop checks terminal_sent and skipped emitting a
        # backup done because it thought the client got one. The client
        # then saw the socket close with no terminal frame and surfaced
        # "Connection dropped before the response finished" (→1290).
        await self._inner.send_json(data)
        if is_terminal:
            self.terminal_sent = True

    async def send_text(self, data: str) -> None:
        # Best-effort peek at the type field so raw send_text callers are
        # tracked too. If the payload is not JSON or not a dict with a
        # known terminal type, just forward untouched.
        is_terminal = False
        try:
            parsed = _json.loads(data)
            if isinstance(parsed, dict) and parsed.get("type") in _TERMINAL_FRAME_TYPES:
                is_terminal = True
        except Exception:
            pass
        # Same ordering rule as send_json: flip terminal_sent only after
        # the inner send returns successfully so a failed send does not
        # falsely advertise that the client received a terminal frame.
        await self._inner.send_text(data)
        if is_terminal:
            self.terminal_sent = True

    async def receive_json(self) -> dict:
        return await self._inner.receive_json()

    async def accept(self, *args, **kwargs):
        return await self._inner.accept(*args, **kwargs)

    async def close(self, *args, **kwargs):
        return await self._inner.close(*args, **kwargs)

    def __getattr__(self, name: str):
        # Delegate anything we did not explicitly wrap (client_state,
        # headers, scope, etc.) straight to the underlying WS.
        return getattr(self._inner, name)


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    await websocket.accept()
    # Register this socket in the process-wide active set so the shutdown
    # hook can notify every live client before uvicorn tears them down.
    _register_active_ws(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            fallback_model = data.get("model", "@claude").lstrip("@")
            messages = data.get("messages", [])
            # Thread-scoped reply support. When replyToId is provided, the
            # message belongs to a thread. thread_id is the id of the root
            # message that started that thread (same as replyToId on the
            # first reply, or the inherited thread_id on subsequent replies).
            reply_to_id: Optional[str] = data.get("replyToId")
            payload_thread_id: Optional[str] = data.get("thread_id")

            # --- Plan confirmation intercept (→1533) ---
            # plan_confirm / plan_cancel / plan_revise arrive as bare
            # WebSocket messages without the normal ``messages`` payload.
            _msg_type = data.get("type")
            if _msg_type in ("plan_confirm", "plan_cancel", "plan_revise"):
                from services.plan_mode_store import plan_mode_store as _pms
                _plan_id = data.get("id", "")
                _pending = _pms.consume(_plan_id)

                if _msg_type == "plan_cancel":
                    await websocket.send_json({"type": "token", "data": "Plan cancelled."})
                    await websocket.send_json({"type": "done"})

                elif _msg_type == "plan_confirm" and _pending:
                    _resume_messages = list(_pending["messages"]) + [
                        {"role": "user", "content": "Plan approved. Please proceed."}
                    ]
                    _resume_tab = _pending.get("tab_id", tab_id if "tab_id" in dir() else "")
                    _tracked = _TerminalTrackingWS(websocket, tab_id=_resume_tab)
                    try:
                        await call_model(
                            "claude",
                            _resume_messages,
                            _tracked,
                            use_tools=True,
                            tab_id=_resume_tab,
                            plan_mode=False,
                        )
                    finally:
                        if not _tracked.terminal_sent:
                            await websocket.send_json({"type": "done"})

                elif _msg_type == "plan_revise" and _pending:
                    _comment = data.get("comment", "")
                    _revise_messages = list(_pending["messages"]) + [
                        {"role": "user", "content": _comment or "Please revise the plan."}
                    ]
                    _revise_tab = _pending.get("tab_id", "")
                    _tracked = _TerminalTrackingWS(websocket, tab_id=_revise_tab)
                    try:
                        await call_model(
                            "claude",
                            _revise_messages,
                            _tracked,
                            use_tools=True,
                            tab_id=_revise_tab,
                            plan_mode=True,
                        )
                    finally:
                        if not _tracked.terminal_sent:
                            await websocket.send_json({"type": "done"})

                else:
                    # Plan not found (expired or double-clicked) — just ack
                    await websocket.send_json({"type": "done"})

                continue
            # --- End plan confirmation intercept ---

            if not messages:
                await websocket.send_json({"type": "error", "data": "No messages"})
                continue

            last_text = messages[-1].get("content", "")

            # --- Slash commands: intercept before AI routing ---
            if isinstance(last_text, str) and last_text.strip().startswith("/"):
                _slash_tab = data.get("tab_id", "")
                handled = await _handle_slash_command(last_text.strip(), websocket, tab_id=_slash_tab)
                if handled:
                    continue

            # --- Memory trigger: detect "remember X", "from now on X", etc. ---
            # Fires before model routing so the bullet lands in the file
            # before the model assembles its system prompt for this turn.
            # The message is still forwarded to the model unchanged.
            if isinstance(last_text, str):
                await _handle_memory_trigger(last_text.strip(), websocket)

            # Clear the 30s dead-backend timer BEFORE any blocking work.
            # build_baseline_context (ostk.list_tasks) and image transforms can
            # stall for seconds when the task list is large (e.g. after a roadmap
            # build that created many tasks). Sending backend_active here means
            # the timer is cleared regardless of which provider path follows,
            # including the roadmap path that previously bypassed agent_anthropic
            # and stream_anthropic's own backend_active sends.
            try:
                _early_backend = await _resolve_chat_backend()
                await _send_backend_active(websocket, _early_backend)
            except Exception:
                pass

            use_tools = data.get("tools", False)
            plan_mode = bool(data.get("plan_mode", False))
            mentioned_models = parse_mentions(last_text)
            had_explicit_mention = bool(mentioned_models)

            # Side-by-side broadcast flag. Baseline-permanent so the
            # frontend can opt a turn into broadcast without requiring
            # a coordinated uvicorn reload. When the UI's All pill is
            # active it sends ``side_by_side: true``; when the flag is
            # false (the default for every existing caller) this is a
            # no-op and the rest of the routing behaves as before.
            side_by_side_flag = bool(data.get("side_by_side", False))
            if side_by_side_flag:
                mentioned_models = list(ALL_MODELS)

            # If the user explicitly tagged one model and asked it to
            # chat with another model by bare name ("@gemini chat with
            # claude ..."), infer the second model so the orchestration
            # loop still fires. Without this the router falls back to
            # the single model path and the user gets one bubble
            # instead of a real back and forth.
            #
            # If the bare name is missing too ("chat with @gemini about
            # X"), the implicit second speaker is the current host model
            # the user is chatting with in the UI (the dropdown
            # selection sent as fallback_model). Example: user is
            # "Talking to Claude" and types "chat with @gemini about
            # songs" → mentioned_models becomes [gemini, claude] so
            # orchestration alternates both models for three rounds.
            if (
                isinstance(last_text, str)
                and len(mentioned_models) == 1
                and is_conversation(last_text)
            ):
                inferred = infer_second_model(last_text, mentioned_models)
                if inferred:
                    mentioned_models = [*mentioned_models, inferred]
                elif not conversation_target_is_user(last_text):
                    # No explicit second model name, and the phrasing is
                    # NOT "chat with me" / "talk to us". Pull in the
                    # host model the user is talking to (the dropdown
                    # selection) as the second speaker so phrases like
                    # "chat with @gemini about X" become a real back
                    # and forth instead of one silent Claude turn.
                    host_key = MODEL_ALIASES.get(fallback_model.lower())
                    if host_key and host_key not in mentioned_models:
                        mentioned_models = [*mentioned_models, host_key]

            # Collective addressing ("you guys", "both of you", "everyone") means
            # the user wants every AI to respond. Override whatever mentions were
            # parsed and route to broadcast mode below.
            collective = is_collective_address(last_text)
            if collective:
                mentioned_models = list(ALL_MODELS)

            # If no @mentions, use the dropdown selection
            if not mentioned_models:
                model_key = MODEL_ALIASES.get(fallback_model.lower(), "claude")
                mentioned_models = [model_key]

            # Re-run inference after the fallback. Bare-name phrasings
            # like "chat with claude" parse to zero @mentions, so the
            # earlier inference block sees len(mentioned_models)==0 and
            # skips. Now that the fallback has filled in the host model
            # (e.g. ["gemini"] when the user is on the Gemini panel),
            # infer the second speaker from the bare name in the text
            # so peer-chat triggers correctly.
            if (
                isinstance(last_text, str)
                and len(mentioned_models) == 1
                and is_conversation(last_text)
                and not conversation_target_is_user(last_text)
            ):
                inferred = infer_second_model(last_text, mentioned_models)
                if inferred and inferred not in mentioned_models:
                    mentioned_models = [*mentioned_models, inferred]

            # Always inject baseline project context (who the user is, what's in flight)
            baseline = await build_baseline_context()
            if baseline:
                messages = [{"role": "user", "content": baseline}] + messages

            # Inject ostk context if relevant (only when not using tool mode,
            # since the agent can look up context itself via tools)
            if not use_tools and (should_inject_context(last_text) or should_inject_calendar(last_text)):
                context_parts = []
                needs_ctx = should_inject_context(last_text)
                needs_cal = should_inject_calendar(last_text)
                gather_coros = []
                if needs_ctx:
                    gather_coros.append(build_context())
                if needs_cal:
                    gather_coros.append(build_calendar_context())
                gather_results = await asyncio.gather(*gather_coros)
                result_idx = 0
                if needs_ctx:
                    ctx = gather_results[result_idx]; result_idx += 1
                    if ctx:
                        context_parts.append(ctx)
                if needs_cal:
                    cal_ctx = gather_results[result_idx]
                    if cal_ctx:
                        context_parts.append(cal_ctx)
                if context_parts:
                    combined = "\n\n".join(context_parts)
                    system_msg = f"You are the AI assistant for myOS. Here is the current workspace context:\n\n{combined}\n\nAnswer the user's question using this context."
                    messages = [{"role": "user", "content": system_msg}] + messages

            # Inject prior conversation memory so the AI can reference
            # what the user talked about in their last chat tab.
            tab_id = data.get("tab_id", "")
            claude_tier = data.get("claude_tier", "")
            memory_msgs = build_memory_context(current_tab_id=tab_id)
            if memory_msgs:
                messages = memory_msgs + messages

            # Clean up tool-heavy history to prevent context pollution.
            # When a prior turn used tools (Bash, Read, etc.), the message
            # list contains dozens of tool_use/tool_result blocks that bloat
            # context and confuse the model on new requests. Strip them down
            # to just the final assistant text from each turn.
            MAX_HISTORY_MESSAGES = 20  # keep last 20 messages max
            cleaned: list[dict] = []
            for m in messages:
                content = m.get("content", "")
                role = m.get("role", "")
                # Keep user messages as-is
                if role == "user":
                    cleaned.append(m)
                    continue
                # For assistant messages, extract only the text content
                if role == "assistant":
                    if isinstance(content, str) and content.strip():
                        cleaned.append(m)
                    elif isinstance(content, list):
                        # Extract text blocks, skip tool_use blocks
                        text_parts = [
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()
                        ]
                        if text_parts:
                            # Preserve the model field so downstream providers
                            # can label assistant messages by source model.
                            rebuilt: dict = {"role": "assistant", "content": " ".join(text_parts)}
                            source_model = m.get("model")
                            if source_model:
                                rebuilt["model"] = source_model
                            cleaned.append(rebuilt)
                    continue
                # Keep system/other messages
                cleaned.append(m)
            # Only keep the last N messages to bound context size
            if len(cleaned) > MAX_HISTORY_MESSAGES:
                cleaned = cleaned[-MAX_HISTORY_MESSAGES:]
            messages = cleaned

            # Convert [gif:URL] markers to image content blocks for vision.
            # transform_image_messages calls urllib.request.urlopen and PIL
            # decode per GIF, both blocking. Run on a worker thread so the
            # uvicorn event loop keeps serving other HTTP requests while
            # a GIF is being fetched.
            messages = await asyncio.to_thread(transform_image_messages, messages)

            # Thread context isolation. When the client sends replyToId (or
            # thread_id), we restrict the conversation context to only the
            # messages in that thread. This prevents replies in thread A from
            # seeing thread B's content, giving each thread its own isolated
            # back-and-forth that the AI only sees from that thread's root.
            effective_thread_id = payload_thread_id or reply_to_id
            if effective_thread_id:
                thread_msgs = build_thread_context(messages, effective_thread_id)
                # Always use the thread-scoped subset. If thread is empty
                # (e.g. first reply before history is sent), fall through to
                # full context so the user gets a response rather than nothing.
                if thread_msgs:
                    messages = thread_msgs

            # The last message content might now be a list (image blocks), so
            # extract text for mention parsing from the original last_text.

            # Two mentions with conversational intent trigger the real
            # multi AI orchestration loop. Each model reads the full
            # transcript so far and replies in turn, one bubble per
            # turn, with live "thinking" / "speaking" status events
            # streaming to the chat panel. Without the intent keywords
            # we fall back to the legacy single-model path so a message
            # like "@claude what does @gemini mean?" still goes to one
            # model only.
            trigger_multi_ai = (
                len(mentioned_models) >= 2
                and is_conversation(last_text)
                and not side_by_side_flag
            )
            # Collective addressing without debate intent means broadcast:
            # every AI responds to the same message independently. The
            # side-by-side flag always forces broadcast so the All pill
            # does not fall through to the debate path.
            trigger_broadcast = (
                (collective or side_by_side_flag)
                and not trigger_multi_ai
            )

            # Wrap the real WebSocket so we can observe whether a terminal
            # frame (done or error) was emitted during this turn. The
            # finally block below uses that flag to send a fallback error
            # frame when a provider bailed without a terminal frame, which
            # is what surfaced as "Connection dropped before the response
            # finished" in the UI on backend reload / subprocess crash.
            # Also inject tab_id into all streaming frames so the frontend
            # can route them to the correct tab.
            tracked_ws = _TerminalTrackingWS(websocket, tab_id=tab_id)

            try:
                if trigger_multi_ai:
                    clean_text = strip_mentions(last_text)
                    _pending_id = str(uuid.uuid4())
                    _peer_evt = asyncio.Event()
                    _now = time.time()
                    # Prune entries older than 300 s to cap dict size.
                    for _k in list(_pending_peer_chats):
                        if _now - _pending_peer_chats[_k].get("created_at", 0) > 300:
                            del _pending_peer_chats[_k]
                    _pending_peer_chats[_pending_id] = {
                        "user_message": clean_text or last_text,
                        "models": mentioned_models[:2],
                        "event": _peer_evt,
                        "turns": None,
                        "created_at": _now,
                    }
                    await tracked_ws.send_json({
                        "type": "peer_chat_turns_required",
                        "pending_id": _pending_id,
                        "participants": mentioned_models[:2],
                        "prompt": last_text,
                    })
                    _timed_out = False
                    try:
                        await asyncio.wait_for(_peer_evt.wait(), timeout=_PEER_CHAT_TURN_TIMEOUT)
                    except asyncio.TimeoutError:
                        _timed_out = True
                    _state = _pending_peer_chats.pop(_pending_id, {})
                    if _timed_out:
                        await tracked_ws.send_json({"type": "done"})
                    else:
                        _turns = max(1, min(3, _state.get("turns") or 2))
                        await stream_multi_ai_conversation(
                            websocket=tracked_ws,
                            models=_state.get("models", mentioned_models[:2]),
                            user_message=_state.get("user_message", clean_text or last_text),
                            rounds=_turns,
                        )
                elif trigger_broadcast:
                    # Broadcast (All pill or "everyone"/"you guys") is a
                    # plain text fan-out. Every model streams a direct
                    # reply to the user. No tool loops, no grep/read/bash,
                    # no agent tools. Forcing use_tools=False here means
                    # the Claude leg goes through stream_anthropic
                    # (text-only) instead of agent_anthropic (tool loop),
                    # which matches what Gemini does and keeps both
                    # bubbles symmetric for casual questions.
                    await stream_group_broadcast(
                        websocket=tracked_ws,
                        models=list(mentioned_models),
                        messages=messages,
                        use_tools=False,
                    )
                else:
                    # Single model call (even if @mentioned)
                    model = mentioned_models[0]
                    # Apply org provider policy only in team mode and when the
                    # user did not explicitly @mention a specific model.
                    if not had_explicit_mention:
                        try:
                            from services import enterprise_store as _es
                            from routers.gemini import is_gemini_available
                            org = _es.get_org()
                            if org:
                                from routers.org_settings import _load_settings, _default_settings
                                org_settings = {**_default_settings(), **_load_settings(org["id"])}
                                org_policy = org_settings.get("provider_policy", "auto")
                                gemini_ok = await is_gemini_available()
                                available = ["claude"] + (["gemini"] if gemini_ok else [])
                                model = route_provider(
                                    message=last_text if isinstance(last_text, str) else "",
                                    org_policy=org_policy,
                                    available_providers=available,
                                )
                        except Exception:
                            pass
                    label = model.capitalize() if len(mentioned_models) > 0 else ""
                    try:
                        from services.turn_audit_store import turn_audit_store as _turn_audit_store
                        _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
                        _turn_id = _turn_audit_store.begin_turn(tab_id=tab_id, repo_root=_REPO_ROOT)
                    except Exception:
                        _turn_id = None
                    _reply_text = await call_model(model, messages, tracked_ws, label=label, use_tools=use_tools, tab_id=tab_id, claude_tier=claude_tier, plan_mode=plan_mode)
                    if _turn_id:
                        try:
                            from services.turn_audit_store import turn_audit_store as _turn_audit_store
                            _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
                            _turn_audit_store.end_turn(_turn_id, repo_root=_REPO_ROOT)
                            _fc = _turn_audit_store.get_file_changes(_turn_id)
                            if _fc:
                                await tracked_ws.send_json({
                                    "type": "file_changes",
                                    "turn_id": _turn_id,
                                    "files": [
                                        {
                                            "path": f["path"],
                                            "diff": f["diff"],
                                            "is_binary": f["is_binary"],
                                            "size_bytes": f["size_bytes"],
                                            "mime_type": f["mime_type"],
                                        }
                                        for f in _fc
                                    ],
                                })
                        except Exception:
                            pass
                    if settings_store.get("chat_receipts_gate_enabled", True):
                        try:
                            from services.receipts_gate import check_receipts as _check_receipts
                            _rw = _check_receipts(_reply_text or "")
                            if _rw:
                                await tracked_ws.send_json({
                                    "type": "receipts-warning",
                                    "trigger_word": _rw.trigger_word,
                                    "message": _rw.message,
                                })
                        except Exception:
                            pass
                    # --- Pattern watcher: end-of-turn observation write (→1539 AC1/AC2) ---
                    try:
                        from services.pattern_watcher import observe_turn as _pw_observe
                        _fire_and_forget(
                            asyncio.to_thread(
                                _pw_observe,
                                last_text if isinstance(last_text, str) else "",
                                _reply_text or "",
                            )
                        )
                    except Exception:
                        pass
            except WebSocketDisconnect:
                raise
            except BaseException as exc:
                # Catch-all including CancelledError (a BaseException in
                # Python 3.8+). Make sure the frontend always receives a
                # terminal frame so it clears its "Thinking" state instead
                # of hanging. Re-raise non-Exception BaseExceptions (like
                # CancelledError) after trying to notify the client.
                try:
                    await tracked_ws.send_json({"type": "error", "data": str(exc)})
                except Exception:
                    pass
                if not isinstance(exc, Exception):
                    raise
            finally:
                # Guarantee terminal frame. If the provider reached a
                # silent exit path (subprocess torn down, cleanup
                # happened before ``done`` or ``error`` reached the
                # wire), emit a fallback ``done`` frame so the frontend
                # clears its "Thinking" state and the WebSocket close
                # handshake can complete cleanly instead of the client
                # seeing "no close frame received or sent".
                if not tracked_ws.terminal_sent:
                    try:
                        await tracked_ws.send_json({"type": "done"})
                    except Exception:
                        pass

    except WebSocketDisconnect:
        pass
    finally:
        _unregister_active_ws(websocket)
        # Close the WebSocket so the WS close-frame handshake completes
        # cleanly. Without this, if the handler exits due to an unhandled
        # BaseException (e.g. CancelledError from a server reload mid-
        # stream), the TCP connection is dropped without a WS CLOSE frame
        # and the client raises "no close frame received or sent". Guard
        # on client_state so we do not call close() on a socket that the
        # client has already torn down — that would block until the
        # starlette TestClient's internal channel times out.
        try:
            from starlette.websockets import WebSocketState
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close()
        except BaseException:
            # BaseException covers CancelledError (not caught by
            # Exception in Python 3.8+) as well as standard errors.
            pass


# ---------------------------------------------------------------------------
# Intel digest synthesis (FR-005 — →1441 Theme C v2)
# ---------------------------------------------------------------------------


def _rule_based_digest(captures: list[dict]) -> str:
    """Rule-based fallback when no LLM key is available."""
    if not captures:
        return "## Intel Digest\n\nNo competitor signals captured in this window."
    lines = ["## Intel Digest\n"]
    by_competitor: dict[str, list[dict]] = {}
    for c in captures:
        name = c.get("competitor", "Unknown")
        by_competitor.setdefault(name, []).append(c)
    for name, signals in sorted(by_competitor.items()):
        lines.append(f"### {name}\n")
        for s in signals:
            snippet = s.get("text") or s.get("url") or "(no detail)"
            lines.append(f"- {snippet}")
        lines.append("")
    return "\n".join(lines)


async def synthesize_intel_digest(captures: list[dict]) -> str:
    """Synthesize a list of intel captures into a markdown digest.

    # TODO: replace with intel-synthesize skill
    This inline LLM call will be replaced when the intel-synthesize marketplace
    skill ships. The TODO marker is intentional per FR-005 so the upgrade path
    is obvious. Until then: if ANTHROPIC_API_KEY is set, use claude-haiku-4-5
    for synthesis; otherwise fall back to a rule-based summary.
    """
    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not captures:
        return _rule_based_digest(captures)

    try:
        import anthropic
        bullets = "\n".join(
            f"- [{c.get('competitor', 'Unknown')}] {c.get('text') or c.get('url') or '(no detail)'}"
            for c in captures
        )
        client = anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Synthesize these competitor signals into a concise markdown digest. "
                        "Group by competitor. Lead with a one-line summary per competitor, "
                        "then bullet the key signals.\n\n"
                        f"{bullets}"
                    ),
                }
            ],
        )
        return message.content[0].text
    except Exception:
        return _rule_based_digest(captures)
