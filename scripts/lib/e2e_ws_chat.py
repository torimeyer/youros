"""WebSocket chat round-trip test for e2e_smoke.sh.

Reads config from env vars: API_PORT, _WS_MODEL, _WS_MESSAGE, _WS_USE_TLS.
Prints one of: OK, NO_WS_LIB, TIMEOUT, EMPTY_RESPONSE, NO_DONE,
ERROR:<msg>, CONNECT_FAIL:<msg>.
"""
import asyncio
import json
import os
import ssl as _ssl
import sys
import urllib.request

try:
    import websockets
except ImportError:
    print("NO_WS_LIB")
    sys.exit(0)

API_PORT = os.environ.get("API_PORT", "8000")
USE_TLS = os.environ.get("_WS_USE_TLS", "0") == "1"
if USE_TLS:
    URL = f"wss://127.0.0.1:{API_PORT}/ws/chat"
    HTTP_BASE = f"https://127.0.0.1:{API_PORT}"
    _ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = _ssl.CERT_NONE
else:
    URL = f"ws://localhost:{API_PORT}/ws/chat"
    HTTP_BASE = f"http://localhost:{API_PORT}"
    _ssl_ctx = None
MODEL = os.environ.get("_WS_MODEL", "@claude")
MESSAGE = os.environ.get("_WS_MESSAGE", "say hi")
# When the backend sends a peer_chat_turns_required event, auto-pick this
# many turns. Smoke runs headless so we always pick the default (2).
PEER_TURNS_DEFAULT = int(os.environ.get("_WS_PEER_TURNS", "2"))


def _post_peer_start(pending_id: str, turns: int) -> None:
    """POST /api/chat/peer/start to resume a peer-chat session that is
    blocked on the user picking a turn count. Best-effort: errors are
    swallowed so the WS handler still gets a chance to time out cleanly
    and report the right status string."""
    body = json.dumps({"pending_id": pending_id, "turns": turns}).encode()
    req = urllib.request.Request(
        f"{HTTP_BASE}/api/chat/peer/start",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(
            req,
            timeout=5,
            context=_ssl_ctx if USE_TLS else None,
        )
    except Exception:
        pass

async def main():
    try:
        connect_kwargs = {"open_timeout": 5}
        if _ssl_ctx is not None:
            connect_kwargs["ssl"] = _ssl_ctx
        async with websockets.connect(URL, **connect_kwargs) as ws:
            await ws.send(json.dumps({
                "messages": [{"role": "user", "content": MESSAGE}],
                "model": MODEL,
            }))
            got_token = False
            got_done = False
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=45)
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    et = event.get("type")
                    if et == "token" and event.get("data"):
                        got_token = True
                    if et in ("text",) and event.get("data"):
                        got_token = True
                    if et == "peer_chat_turns_required":
                        # Backend is waiting for the user to pick a turn
                        # count via the inline picker. Smoke is headless,
                        # so post the default and let the conversation
                        # resume.
                        pid = event.get("pending_id", "")
                        if pid:
                            _post_peer_start(pid, PEER_TURNS_DEFAULT)
                        continue
                    if et == "done":
                        got_done = True
                        break
                    if et == "error":
                        print(f"ERROR:{event.get('data','')[:200]}")
                        return
            except asyncio.TimeoutError:
                print("TIMEOUT")
                return
            if got_token and got_done:
                print("OK")
            elif got_done and not got_token:
                print("EMPTY_RESPONSE")
            else:
                print("NO_DONE")
    except Exception as exc:
        print(f"CONNECT_FAIL:{exc}")

asyncio.run(main())
