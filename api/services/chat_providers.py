import anthropic
from fastapi import WebSocket

from config import PROJECT_ROOT
from services.settings_store import settings_store
from services.tool_executor import TOOL_DEFINITIONS, execute_tool

MAX_AGENT_TURNS = 10


def _system_prompt() -> str:
    os_name = settings_store.get("os_name", "YourOS")
    user_name = settings_store.get("user_name", "")
    owner = user_name if user_name else "the user"
    return (
        f"You are {os_name}, {owner}'s personal operating system. "
        "You have access to tools that let you read files, write files, edit files, "
        f"run shell commands, search code, and manage tasks in the workspace at "
        f"{PROJECT_ROOT}. "
        f"Use these tools to help {owner} with whatever they need. "
        "When you need information from the codebase, read files or search. "
        f"When {owner} asks you to change something, use the edit or write tools. "
        "Explain what you are doing in plain language. Never use em-dashes."
    )


class ChatService:
    async def stream_anthropic(self, messages: list[dict], websocket: WebSocket) -> str:
        api_key = settings_store.get("anthropic_api_key", "")
        if not api_key:
            await websocket.send_json({"type": "error", "data": "No Anthropic API key set. Add one in Settings."})
            return ""

        client = anthropic.AsyncAnthropic(api_key=api_key)
        full_text = ""
        try:
            async with client.messages.stream(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                messages=messages,
            ) as stream:
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

    async def agent_anthropic(self, messages: list[dict], websocket: WebSocket) -> str:
        """Run the Anthropic agent loop with tool use.

        Sends messages with tool definitions, executes any tool calls Claude
        makes, feeds results back, and repeats until Claude responds with
        just text (no more tool calls). Then streams that final text response.
        """
        api_key = settings_store.get("anthropic_api_key", "")
        if not api_key:
            await websocket.send_json({"type": "error", "data": "No Anthropic API key set. Add one in Settings."})
            return ""

        client = anthropic.AsyncAnthropic(api_key=api_key)
        conversation: list[dict] = list(messages)
        total_input_tokens = 0
        total_output_tokens = 0

        try:
            for turn in range(MAX_AGENT_TURNS):
                # Non-streaming call so we can inspect content blocks
                response = await client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=4096,
                    system=_system_prompt(),
                    messages=conversation,
                    tools=TOOL_DEFINITIONS,
                )

                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens

                # Process content blocks
                has_tool_use = False
                text_parts = []
                tool_uses = []

                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)
                    elif block.type == "tool_use":
                        has_tool_use = True
                        tool_uses.append(block)

                # If there is intermediate text before tool calls, send it
                if text_parts and has_tool_use:
                    for text in text_parts:
                        await websocket.send_json({"type": "token", "data": text})

                if not has_tool_use:
                    # Final response with just text. Stream it.
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

                # Build the assistant message with all content blocks
                assistant_content = []
                for block in response.content:
                    if block.type == "text":
                        assistant_content.append({
                            "type": "text",
                            "text": block.text,
                        })
                    elif block.type == "tool_use":
                        assistant_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": dict(block.input),
                        })

                conversation.append({"role": "assistant", "content": assistant_content})

                # Execute each tool and build tool_result messages
                tool_results = []
                for block in tool_uses:
                    # Notify the frontend about the tool call
                    await websocket.send_json({
                        "type": "tool_use",
                        "data": {
                            "tool": block.name,
                            "input": dict(block.input),
                            "id": block.id,
                        },
                    })

                    result = await execute_tool(block.name, dict(block.input))

                    # Notify the frontend about the tool result
                    await websocket.send_json({
                        "type": "tool_result",
                        "data": {
                            "tool": block.name,
                            "id": block.id,
                            "result": result[:2000] if len(result) > 2000 else result,
                        },
                    })

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                conversation.append({"role": "user", "content": tool_results})

            # If we hit the turn limit, send what we have
            await websocket.send_json({
                "type": "token",
                "data": "\n\n(Reached the maximum number of tool use rounds.)",
            })
            await websocket.send_json({
                "type": "done",
                "usage": {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                },
            })
            return "(max turns reached)"

        except anthropic.APIError as e:
            await websocket.send_json({"type": "error", "data": str(e)})
            return ""

    async def stream_gemini(self, messages: list[dict], websocket: WebSocket) -> str:
        api_key = settings_store.get("gemini_api_key", "")
        if not api_key:
            await websocket.send_json({"type": "error", "data": "No Gemini API key set. Add one in Settings."})
            return ""

        full_text = ""
        try:
            import google.generativeai as genai
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
            await websocket.send_json({"type": "error", "data": str(e)})

        return full_text


chat_service = ChatService()
