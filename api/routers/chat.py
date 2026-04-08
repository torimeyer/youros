import os
import re

import httpx
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from services.chat_history_store import chat_history_store
from services.chat_providers import chat_service
from services.ostk import ostk

router = APIRouter(tags=["chat"])

GIPHY_API_KEY = os.environ.get("GIPHY_API_KEY", "uac5bYV974kGSu3Pe0B92ChNrIQypZ0Y")

CONTEXT_KEYWORDS = {"tasks", "needles", "task", "needle", "focus", "agents", "hay", "ideas", "status"}

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


GIF_RE = re.compile(r"\[gif:(https?://[^\]]+)\]")


def transform_image_messages(messages: list[dict]) -> list[dict]:
    """Convert image data (GIF URLs and pasted base64 images) into content blocks for Claude vision."""
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
                blocks.append({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                })
                remaining = remaining.replace(match.group(0), "").strip()
            blocks.append({"type": "text", "text": remaining or "The user sent this GIF. React to it."})
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

            # If no @mentions, use the dropdown selection
            if not mentioned_models:
                model_key = MODEL_ALIASES.get(fallback_model.lower(), "claude")
                mentioned_models = [model_key]

            # Inject ostk context if relevant (only when not using tool mode,
            # since the agent can look up context itself via tools)
            if not use_tools and should_inject_context(last_text):
                context = await build_context()
                if context:
                    system_msg = f"You are the AI assistant for myOS. Here is the current workspace context:\n\n{context}\n\nAnswer the user's question using this context."
                    messages = [{"role": "user", "content": system_msg}] + messages

            # Convert [gif:URL] markers to image content blocks for vision
            messages = transform_image_messages(messages)

            # The last message content might now be a list (image blocks), so
            # extract text for mention parsing from the original last_text.

            # Check if this is a "talk to each other" request (multiple models mentioned)
            is_conversation = len(mentioned_models) >= 2 and any(
                word in last_text.lower() for word in ["talk to", "discuss", "debate", "ask", "tell"]
            )

            try:
                if is_conversation:
                    # Multi-model conversation: call first model, then pass response to second
                    clean_text = strip_mentions(last_text)
                    model_a = mentioned_models[0]
                    model_b = mentioned_models[1]

                    # First model responds
                    prompt_a = f"The user asked you to have a conversation with another AI ({model_b}). The user said: \"{clean_text}\"\n\nPlease share your thoughts. Be concise."
                    msgs_a = messages[:-1] + [{"role": "user", "content": prompt_a}]
                    await call_model(model_a, msgs_a, websocket, label=model_a.capitalize())

                    # Signal boundary between models
                    await websocket.send_json({"type": "model_boundary"})

                    # Second model responds to first model's output
                    # We collect the first model's response by re-reading from a buffer approach
                    # For simplicity, just prompt the second model with the same context
                    prompt_b = f"Another AI ({model_a}) was asked about: \"{clean_text}\"\n\nNow it's your turn. Share your perspective. You may agree or disagree. Be concise."
                    msgs_b = messages[:-1] + [{"role": "user", "content": prompt_b}]
                    await call_model(model_b, msgs_b, websocket, label=model_b.capitalize())

                    await websocket.send_json({"type": "done"})
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
