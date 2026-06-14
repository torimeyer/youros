#!/usr/bin/env python3
"""CI chat smoke: drive one exchange over the real /ws/chat WebSocket.

Run against a backend booted with YOUROS_MOCK_LLM=1. Sends a 'hello' and
asserts the reply STREAMS as one or more token frames ending in a done frame.
Exits non-zero with a clear message if the stream never starts (the exact
"installed but chat doesn't respond" failure users hit) or never completes.

Usage: ci_chat_smoke.py [ws_url]   (default ws://127.0.0.1:8000/ws/chat)
"""
import asyncio
import json
import sys

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8000/ws/chat"


async def main() -> int:
    async with websockets.connect(URL, open_timeout=10) as ws:
        await ws.send(json.dumps({
            "model": "@claude",
            "messages": [{"role": "user", "content": "hello"}],
        }))
        tokens = 0
        text = ""
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "token":
                tokens += 1
                text += msg.get("data", "")
            elif kind == "done":
                break
            elif kind == "error":
                print(f"SMOKE FAIL: server error frame: {msg.get('data')}", file=sys.stderr)
                return 1
        if tokens < 1:
            print("SMOKE FAIL: no token frames (response never started streaming)", file=sys.stderr)
            return 1
        print(f"SMOKE OK: {tokens} token frame(s); reply={text!r}")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 - smoke should fail loudly, not trace
        print(f"SMOKE FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
