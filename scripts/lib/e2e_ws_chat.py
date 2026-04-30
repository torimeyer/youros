"""WebSocket chat round-trip test for e2e_smoke.sh.

Reads config from env vars: API_PORT, _WS_MODEL, _WS_MESSAGE, _WS_USE_TLS.
Prints one of: OK, NO_WS_LIB, TIMEOUT, EMPTY_RESPONSE, NO_DONE,
ERROR:<msg>, CONNECT_FAIL:<msg>.
"""
import asyncio
import json
import os
import sys

try:
    import websockets
except ImportError:
    print("NO_WS_LIB")
    sys.exit(0)

import ssl as _ssl
API_PORT = os.environ.get("API_PORT", "8000")
USE_TLS = os.environ.get("_WS_USE_TLS", "0") == "1"
if USE_TLS:
    URL = f"wss://127.0.0.1:{API_PORT}/ws/chat"
    _ssl_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = _ssl.CERT_NONE
else:
    URL = f"ws://localhost:{API_PORT}/ws/chat"
    _ssl_ctx = None
MODEL = os.environ.get("_WS_MODEL", "@claude")
MESSAGE = os.environ.get("_WS_MESSAGE", "say hi")

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
