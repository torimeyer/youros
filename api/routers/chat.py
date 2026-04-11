import asyncio
import os
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from services.chat_history_store import chat_history_store
from services.chat_providers import (
    MULTI_AI_DEFAULT_ROUNDS,
    chat_service,
    stream_group_broadcast,
    stream_multi_ai_conversation,
)
from services.ostk import ostk

router = APIRouter(tags=["chat"])

GIPHY_API_KEY = os.environ.get("GIPHY_API_KEY", "uac5bYV974kGSu3Pe0B92ChNrIQypZ0Y")

CONTEXT_KEYWORDS = {"tasks", "needles", "task", "needle", "focus", "agents", "hay", "ideas", "status"}

CALENDAR_KEYWORDS = {"calendar", "meeting", "meetings", "schedule", "today", "tomorrow"}

# Phrases that trigger saving the current conversation topic as an idea.
_SAVE_AS_IDEA_PATTERNS = [
    re.compile(r"\bsave\s+(?:this\s+)?as\s+an?\s+idea\b", re.IGNORECASE),
    re.compile(r"\bpark\s+(?:this\s+)?as\s+an?\s+idea\b", re.IGNORECASE),
    re.compile(r"\badd\s+(?:this\s+)?to\s+ideas\b", re.IGNORECASE),
    re.compile(r"\bfile\s+(?:this\s+)?as\s+(?:an?\s+)?idea\b", re.IGNORECASE),
]


def _detect_save_as_idea(text: str) -> bool:
    """Return True if the message is asking to save the topic as an idea."""
    for pattern in _SAVE_AS_IDEA_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _extract_idea_content(messages: list[dict]) -> str:
    """Pull the most relevant content to save as an idea from recent messages.

    Looks at the last few assistant and user turns to find something
    substantive to save. Falls back to the last user message if nothing
    useful is found.
    """
    # Walk backwards through the conversation looking for content.
    for msg in reversed(messages[:-1]):  # skip the command message itself
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        if role in ("assistant", "user") and len(content) > 10:
            # Trim to a reasonable length for an idea title.
            return content[:200]
    # Absolute fallback: use the user's command text stripped of the keyword.
    last = messages[-1].get("content", "") if messages else ""
    if isinstance(last, str):
        cleaned = re.sub(
            r"\b(?:save|park)\s+(?:this\s+)?as\s+an?\s+idea\b|"
            r"\badd\s+(?:this\s+)?to\s+ideas\b|"
            r"\bfile\s+(?:this\s+)?as\s+(?:an?\s+)?idea\b",
            "",
            last,
            flags=re.IGNORECASE,
        ).strip()
        return cleaned or last[:200]
    return "Idea from chat"

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
            result.append({"role": msg["role"], "content": blocks})
        elif isinstance(content, str) and GIF_RE.search(content):
            blocks = []
            remaining = content
            for match in GIF_RE.finditer(content):
                url = match.group(1)
                # Extract multiple frames so the model can see the animation.
                blocks.extend(_extract_gif_frames(url, max_frames=4))
                remaining = remaining.replace(match.group(0), "").strip()
            text = (
                remaining
                or "The user sent this GIF. The multiple frames above show the animation. Describe what is happening and react to it."
            )
            blocks.append({"type": "text", "text": text})
            result.append({"role": msg["role"], "content": blocks})
        else:
            result.append({"role": msg["role"], "content": content})
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


async def call_model(provider: str, messages: list[dict], websocket: WebSocket, label: str = "", use_tools: bool = False):
    """Call a single model and stream the response, returning the full text."""
    if label:
        await websocket.send_json({"type": "model_label", "data": label})

    if provider == "claude":
        if use_tools:
            return await chat_service.agent_anthropic(messages, websocket)
        return await chat_service.stream_anthropic(messages, websocket)
    elif provider == "gemini":
        return await chat_service.stream_gemini(messages, websocket)
    else:
        await websocket.send_json({"type": "error", "data": f"Unknown model: {provider}"})
        return ""


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


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            fallback_model = data.get("model", "@claude").lstrip("@")
            messages = data.get("messages", [])

            if not messages:
                await websocket.send_json({"type": "error", "data": "No messages"})
                continue

            last_text = messages[-1].get("content", "")
            use_tools = data.get("tools", False)
            mentioned_models = parse_mentions(last_text)

            # If the user explicitly tagged one model and asked it to
            # chat with another model by bare name ("@gemini chat with
            # claude ..."), infer the second model so the orchestration
            # loop still fires. Without this the router falls back to
            # the single model path and the user gets one bubble
            # instead of a real back and forth.
            if (
                isinstance(last_text, str)
                and len(mentioned_models) == 1
                and is_conversation(last_text)
            ):
                inferred = infer_second_model(last_text, mentioned_models)
                if inferred:
                    mentioned_models = [*mentioned_models, inferred]

            # --- Chat-to-Idea: intercept "save as idea" commands ---
            if isinstance(last_text, str) and _detect_save_as_idea(last_text):
                idea_content = _extract_idea_content(messages)
                try:
                    await ostk.add_hay_from_chat(idea_content)
                    saved_ok = True
                except Exception:
                    saved_ok = False

                confirm_text = (
                    "Saved to Ideas. You can break it into tasks from the Ideas page."
                    if saved_ok
                    else "I could not save that idea right now. Try again."
                )
                await websocket.send_json({"type": "text", "data": confirm_text})
                await websocket.send_json({"type": "done"})
                continue

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

            # Convert [gif:URL] markers to image content blocks for vision.
            # transform_image_messages calls urllib.request.urlopen and PIL
            # decode per GIF, both blocking. Run on a worker thread so the
            # uvicorn event loop keeps serving other HTTP requests while
            # a GIF is being fetched.
            messages = await asyncio.to_thread(transform_image_messages, messages)

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
                    await call_model(model, messages, websocket, label=label, use_tools=use_tools)
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
