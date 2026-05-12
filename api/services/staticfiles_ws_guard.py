class StaticFilesWSGuard:
    """
    ASGI wrapper that intercepts WebSocket (and non-http) scopes before they
    reach StaticFiles, which asserts scope["type"] == "http" and crashes.

    WebSocket connections receive a clean close (code 1000). All other scope
    types are silently ignored. HTTP scopes pass through unchanged.
    """

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await receive()  # drain the websocket.connect event
            await send({"type": "websocket.close", "code": 1000})
            return
        if scope["type"] != "http":
            return
        await self._app(scope, receive, send)
