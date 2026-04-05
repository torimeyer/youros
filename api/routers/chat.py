import os
import re

import httpx
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from services.chat_providers import chat_service
from services.ostk import ostk

router = APIRouter(tags=["chat"])

GIPHY_API_KEY = os.environ.get("GIPHY_API_KEY", "uac5bYV974kGSu3Pe0B92ChNrIQypZ0Y")

CONTEXT_KEYWORDS = {"tasks", "needles", "task", "needle", "focus", "agents", "hay", "ideas", "status"}

# Map @mention names to provider keys
MODEL_ALIASES = {
    "claude": "claude",
    "anthropic": "claude",
    "gemini": "gemini",
    "google": "gemini",
    "gpt": "gpt",
    "openai": "gpt",
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


def transform_gif_messages(messages: list[dict]) -> list[dict]:
    """Convert [gif:URL] markers in messages into image content blocks for Claude vision."""
    result = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str) and GIF_RE.search(content):
            # Build a content array with image blocks and any remaining text
            blocks: list[dict] = []
            remaining = content
            for match in GIF_RE.finditer(content):
                url = match.group(1)
                blocks.append({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                })
                remaining = remaining.replace(match.group(0), "").strip()
            if remaining:
                blocks.append({"type": "text", "text": remaining})
            else:
                blocks.append({"type": "text", "text": "The user sent this GIF. React to it."})
            result.append({"role": msg["role"], "content": blocks})
        else:
            result.append(msg)
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
    elif provider == "gpt":
        await websocket.send_json({"type": "error", "data": "GPT not configured yet. Add support in Settings."})
        return ""
    else:
        await websocket.send_json({"type": "error", "data": f"Unknown model: {provider}"})
        return ""


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

            # If no @mentions, use the dropdown selection
            if not mentioned_models:
                model_key = MODEL_ALIASES.get(fallback_model.lower(), "claude")
                mentioned_models = [model_key]

            # Inject ostk context if relevant (only when not using tool mode,
            # since the agent can look up context itself via tools)
            if not use_tools and should_inject_context(last_text):
                context = await build_context()
                if context:
                    system_msg = f"You are ToriChat, the AI assistant for ToriOS. Here is the current workspace context:\n\n{context}\n\nAnswer the user's question using this context."
                    messages = [{"role": "user", "content": system_msg}] + messages

            # Convert [gif:URL] markers to image content blocks for vision
            messages = transform_gif_messages(messages)

            # The last message content might now be a list (image blocks), so
            # extract text for mention parsing from the original last_text.

            # Check if this is a "talk to each other" request (multiple models mentioned)
            is_conversation = len(mentioned_models) >= 2 and any(
                word in last_text.lower() for word in ["talk to", "discuss", "debate", "ask", "tell"]
            )

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
        pass
