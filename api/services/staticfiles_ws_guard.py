from starlette.staticfiles import StaticFiles
from starlette.exceptions import HTTPException


class SPAStaticFiles(StaticFiles):
    """
    StaticFiles with SPA history fallback.

    Any path that would 404 (a client-side route like /specs or /tasks on a
    hard refresh or deep link) is served index.html instead, so the React
    router can take over rendering. API routes are mounted before this handler
    at "/", so only paths that match no API route reach here.
    """

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # Only fall back to index.html for client-side routes. An unmatched
            # path under api/ is a real missing endpoint (a removed or mistyped
            # API route): re-raise so it returns 404 instead of a 200 SPA page,
            # which would silently mask the broken call.
            if exc.status_code == 404 and not path.lstrip("/").startswith("api/"):
                return await super().get_response("index.html", scope)
            raise


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
