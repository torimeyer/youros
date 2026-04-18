import asyncio
import json as _json
import os
import re
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from services.chat_history_store import chat_history_store
from services.chat_providers import (
    MULTI_AI_DEFAULT_ROUNDS,
    chat_service,
    stream_group_broadcast,
    stream_multi_ai_conversation,
)
from services.ostk import ostk
from services.settings_store import settings_store

router = APIRouter(tags=["chat"])

# Where roadmap .md files land. Same directory the Files tab scans. Kept
# as a module-level constant so tests can monkeypatch it onto a tmp_path.
MYOS_FILES_DIR = Path.home() / ".myos" / "files"

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


async def call_model(provider: str, messages: list[dict], websocket: WebSocket, label: str = "", use_tools: bool = False, tab_id: str = ""):
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
        await register_chat_session(
            chat_agent_name,
            model=provider,
            prompt_preview=last_user,
        )
    except Exception:
        pass

    full_text = ""
    status = "completed"
    try:
        if provider == "claude":
            if use_tools:
                full_text = await chat_service.agent_anthropic(messages, websocket, tab_id=tab_id)
            else:
                full_text = await chat_service.stream_anthropic(messages, websocket, tab_id=tab_id)
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
            await complete_chat_session(chat_agent_name, status=status)
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
        return {
            "status": "no_roadmap",
            "reply": (
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
            "Those items were already open tasks, so nothing new was "
            "created."
        )
    else:
        noun = "task" if count == 1 else "tasks"
        reply = (
            f"Created {count} {noun} from {roadmap_path.name}. "
            "See them on the [Tasks page](/tasks)."
        )
    return {
        "status": "ok",
        "reply": reply,
        "created": created,
        "roadmap_path": roadmap_path.as_posix(),
    }


@router.post("/api/chat/tools/run")
async def run_chat_tool(body: dict):
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
    "  /tasks    . List open tasks\n"
    "  /commit <message> . Commit with a message\n"
    "  /agents   . Show active and recent agents\n"
    "  /mcp      . Info about MCP server management\n"
    "  /help     . Show this list"
)

_MCP_INFO_TEXT = (
    "MCP servers are managed in Settings. "
    "Go to Settings to add or remove servers."
)


async def _handle_slash_command(text: str, websocket: WebSocket) -> bool:
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
                result = "No open tasks."
            else:
                lines = [f"Open tasks ({len(tasks)}):"]
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

    else:
        result = "Unknown command. Type /help for available commands."

    await websocket.send_json({"type": "text", "data": result})
    await websocket.send_json({"type": "done"})
    return True


async def _fetch_agents_list() -> str:
    """Fetch and format the agents list for the /agents slash command."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://127.0.0.1:8000/api/agents", timeout=5)
            data = resp.json()
    except Exception:
        # Fall back to ostk kernel ps if the HTTP endpoint is not reachable.
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


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    await websocket.accept()
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

            if not messages:
                await websocket.send_json({"type": "error", "data": "No messages"})
                continue

            last_text = messages[-1].get("content", "")

            # --- Slash commands: intercept before AI routing ---
            if isinstance(last_text, str) and last_text.strip().startswith("/"):
                handled = await _handle_slash_command(last_text.strip(), websocket)
                if handled:
                    continue

            use_tools = data.get("tools", False)
            mentioned_models = parse_mentions(last_text)

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

            # Inject ostk context if relevant (only when not using tool mode,
            # since the agent can look up context itself via tools)
            if not use_tools and (should_inject_context(last_text) or should_inject_calendar(last_text)):
                context_parts = []
                if should_inject_context(last_text):
                    ctx = await build_context()
                    if ctx:
                        context_parts.append(ctx)
                if should_inject_calendar(last_text):
                    cal_ctx = await build_calendar_context()
                    if cal_ctx:
                        context_parts.append(cal_ctx)
                if context_parts:
                    combined = "\n\n".join(context_parts)
                    system_msg = f"You are the AI assistant for myOS. Here is the current workspace context:\n\n{combined}\n\nAnswer the user's question using this context."
                    messages = [{"role": "user", "content": system_msg}] + messages

            # Inject prior conversation memory so the AI can reference
            # what the user talked about in their last chat tab.
            tab_id = data.get("tab_id", "")
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
                len(mentioned_models) >= 2 and is_conversation(last_text)
            )
            # Collective addressing without debate intent means broadcast:
            # every AI responds to the same message independently.
            trigger_broadcast = collective and not is_conversation(last_text)

            try:
                if trigger_multi_ai:
                    clean_text = strip_mentions(last_text)
                    await stream_multi_ai_conversation(
                        websocket=websocket,
                        models=mentioned_models[:2],
                        user_message=clean_text or last_text,
                        rounds=MULTI_AI_DEFAULT_ROUNDS,
                    )
                elif trigger_broadcast:
                    await stream_group_broadcast(
                        websocket=websocket,
                        models=list(mentioned_models),
                        messages=messages,
                        use_tools=use_tools,
                    )
                else:
                    # Single model call (even if @mentioned)
                    model = mentioned_models[0]
                    label = model.capitalize() if len(mentioned_models) > 0 else ""
                    await call_model(model, messages, websocket, label=label, use_tools=use_tools, tab_id=tab_id)
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                # Catch-all: make sure the frontend always receives an error
                # so it can clear the "Thinking" state instead of hanging.
                try:
                    await websocket.send_json({"type": "error", "data": str(exc)})
                except Exception:
                    pass

    except WebSocketDisconnect:
        pass
