"""MCP socket transport for ostk daemon.

Provides a persistent Unix socket connection to the ostk daemon using
the MCP protocol (JSON-RPC 2.0 over newline-delimited JSON). This
replaces per-call subprocess spawning with a single long-lived
connection, cutting latency from ~200ms/call to ~5ms/call.

Protocol summary:
  1. Connect to {PROJECT_ROOT}/.ostk/ostk.sock
  2. Send ``initialize`` with protocolVersion "2024-11-05"
  3. Wait for ``initialize`` result
  4. Send ``notifications/initialized``
  5. Call tools via ``tools/call`` with {name, arguments}
  6. Read newline-delimited JSON responses

The module exposes a single ``call_tool`` coroutine for use by
OstkService, plus lifecycle helpers for connect/close.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from config import PROJECT_ROOT

logger = logging.getLogger(__name__)

SOCKET_PATH = PROJECT_ROOT / ".ostk" / "ostk.sock"
MCP_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT = 5.0
CIRCUIT_RETRY_SECONDS = 60.0


class OstkSocketError(Exception):
    """Raised when the socket transport encounters a non-recoverable error."""
    pass


class OstkSocket:
    """Persistent MCP connection to the ostk daemon over a Unix socket.

    Usage::

        sock = OstkSocket()
        await sock.connect()
        result = await sock.call_tool("status", {})
        await sock.close()

    Or use the module-level ``call_tool`` helper which manages the
    singleton connection automatically.
    """

    def __init__(self, socket_path: Optional[Path] = None):
        self._socket_path: Path = Path(socket_path) if socket_path is not None else SOCKET_PATH
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._request_id = 0
        self._connected = False
        self._handshake_done = False
        self._lock = asyncio.Lock()
        self._failed_at: float = 0.0
        self._circuit_retry_seconds: float = CIRCUIT_RETRY_SECONDS

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    @property
    def is_circuit_open(self) -> bool:
        if self._failed_at <= 0.0:
            return False
        return (time.monotonic() - self._failed_at) < self._circuit_retry_seconds

    def record_failure(self) -> None:
        self._failed_at = time.monotonic()

    def reset_circuit(self) -> None:
        self._failed_at = 0.0

    @property
    def connected(self) -> bool:
        return self._connected and self._writer is not None

    async def connect(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Open the Unix socket connection and perform the MCP handshake."""
        if self.connected:
            return

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)),
                timeout=timeout,
            )
        except FileNotFoundError:
            self.record_failure()
            raise OstkSocketError(
                f"Socket not found: {self._socket_path}"
            )
        except (ConnectionRefusedError, OSError) as exc:
            self.record_failure()
            raise OstkSocketError(
                f"Cannot connect to ostk daemon: {exc}"
            )
        except asyncio.TimeoutError:
            self.record_failure()
            raise OstkSocketError(
                "Timed out connecting to ostk daemon"
            )

        self._connected = True
        self._handshake_done = False

        try:
            await self._handshake(timeout)
        except Exception:
            self.record_failure()
            raise
        self.reset_circuit()

    async def _handshake(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Perform the MCP initialize / initialized exchange."""
        # Step 1: send initialize
        init_result = await self._send_request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "myos-api",
                    "version": "1.0.0",
                },
            },
            timeout=timeout,
        )

        if "error" in init_result:
            err = init_result["error"]
            raise OstkSocketError(
                f"MCP handshake failed: {err.get('message', err)}"
            )

        # Step 2: send notifications/initialized (no response expected)
        await self._send_notification("notifications/initialized", {})
        self._handshake_done = True
        logger.info("MCP handshake complete with ostk daemon")

    async def call_tool(
        self,
        name: str,
        arguments: Optional[dict[str, Any]] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> str:
        """Call an ostk tool over MCP and return the text content.

        Automatically reconnects on stale connections.

        Args:
            name: Tool name (e.g. "status", "ps", "close").
            arguments: Tool arguments dict. Defaults to empty dict.
            timeout: Per-call timeout in seconds.

        Returns:
            The text content from the tool response.

        Raises:
            OstkSocketError: On protocol errors or timeouts.
        """
        arguments = arguments or {}

        async with self._lock:
            # Auto-connect if needed
            if not self.connected:
                await self.connect(timeout=timeout)

            try:
                response = await self._send_request(
                    "tools/call",
                    {"name": name, "arguments": arguments},
                    timeout=timeout,
                )
            except (OstkSocketError, asyncio.TimeoutError, ConnectionError):
                # Connection may be stale. Close and retry once.
                await self._close_internal()
                try:
                    await self.connect(timeout=timeout)
                    response = await self._send_request(
                        "tools/call",
                        {"name": name, "arguments": arguments},
                        timeout=timeout,
                    )
                except Exception as exc:
                    self.record_failure()
                    await self._close_internal()
                    raise OstkSocketError(
                        f"Socket call failed after reconnect: {exc}"
                    ) from exc

            self.reset_circuit()

        # Extract text from MCP response
        if "error" in response:
            err = response["error"]
            raise OstkSocketError(
                f"Tool '{name}' error: {err.get('message', err)}"
            )

        return self._extract_text(response)

    async def close(self) -> None:
        """Close the socket connection."""
        async with self._lock:
            await self._close_internal()

    async def _close_internal(self) -> None:
        """Close without acquiring the lock (caller must hold it)."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self._connected = False
        self._handshake_done = False
        self._request_id = 0

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """Send a JSON-RPC request and wait for the matching response."""
        self._request_id += 1
        req_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        await self._write_message(message)
        return await self._read_response(req_id, timeout)

    async def _send_notification(
        self,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._write_message(message)

    async def _write_message(self, message: dict) -> None:
        """Serialize and write a JSON-RPC message to the socket."""
        if self._writer is None:
            raise OstkSocketError("Not connected")

        data = json.dumps(message) + "\n"
        self._writer.write(data.encode())
        await self._writer.drain()

    async def _read_response(self, req_id: int, timeout: float) -> dict:
        """Read newline-delimited JSON until we get the response for req_id."""
        if self._reader is None:
            raise OstkSocketError("Not connected")

        deadline = asyncio.get_event_loop().time() + timeout

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"Timed out waiting for response to request {req_id}"
                )

            try:
                line = await asyncio.wait_for(
                    self._reader.readline(),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"Timed out reading response for request {req_id}"
                )

            if not line:
                raise OstkSocketError(
                    "Connection closed by ostk daemon"
                )

            line_str = line.decode().strip()
            if not line_str:
                continue

            try:
                msg = json.loads(line_str)
            except json.JSONDecodeError:
                logger.warning("Non-JSON line from socket: %s", line_str[:200])
                continue

            # Skip notifications (no id field)
            if "id" not in msg:
                continue

            if msg.get("id") == req_id:
                return msg

            # Response for a different request id. Log and skip.
            logger.debug(
                "Skipping response for id=%s (waiting for %s)",
                msg.get("id"),
                req_id,
            )

    @staticmethod
    def _extract_text(response: dict) -> str:
        """Extract the text content from an MCP tools/call response.

        Expected shape: {"result": {"content": [{"text": "..."}]}}
        """
        result = response.get("result", {})
        content = result.get("content", [])
        if not content:
            return ""

        # Concatenate all text blocks
        parts = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Module-level per-repo socket pool + backward-compatible default singleton
# ---------------------------------------------------------------------------

_instance: Optional[OstkSocket] = None
_pool: dict[Path, OstkSocket] = {}
_instance_lock = asyncio.Lock()


def _resolve_socket_path(cwd: str | Path | None = None) -> Path:
    if cwd is None:
        return SOCKET_PATH.resolve()
    return (Path(cwd).expanduser().resolve() / ".ostk" / "ostk.sock").resolve()


def get_ostk_socket(cwd: str | Path | None = None) -> OstkSocket:
    """Return a pooled OstkSocket keyed by resolved repo socket path.

    When ``cwd`` is None, returns (and populates) the default ``_instance``
    bound to ``SOCKET_PATH`` for backward compatibility with zero-arg callers.
    """
    global _instance
    default_key = SOCKET_PATH.resolve()
    if cwd is None:
        if _instance is None:
            _instance = OstkSocket(socket_path=SOCKET_PATH)
            _pool[default_key] = _instance
        return _instance

    sock_path = Path(cwd).expanduser().resolve() / ".ostk" / "ostk.sock"
    key = sock_path.resolve()
    if key == default_key and _instance is not None:
        _pool[key] = _instance
        return _instance
    sock = _pool.get(key)
    if sock is None:
        sock = OstkSocket(socket_path=sock_path)
        _pool[key] = sock
        if key == default_key and _instance is None:
            _instance = sock
    return sock


def reset_ostk_socket() -> None:
    """Clear both the default singleton and the per-repo socket pool."""
    global _instance
    _instance = None
    _pool.clear()


async def get_socket(cwd: str | Path | None = None) -> OstkSocket:
    """Return the pooled OstkSocket for ``cwd`` (or default singleton when None)."""
    async with _instance_lock:
        return get_ostk_socket(cwd)


async def call_tool(
    name: str,
    arguments: Optional[dict[str, Any]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    cwd: str | Path | None = None,
) -> str:
    """Call an ostk tool via the persistent socket connection for ``cwd``.

    This is the main entry point for OstkService and tool_executor. It manages
    the pooled connection and raises OstkSocketError on failure so the caller
    can fall back to subprocess.
    """
    sock = await get_socket(cwd)
    return await sock.call_tool(name, arguments, timeout)


async def close_socket(cwd: str | Path | None = None) -> None:
    """Close a repo's pooled socket, or all pooled sockets when ``cwd`` is None."""
    global _instance
    async with _instance_lock:
        if cwd is None:
            seen: set[int] = set()
            if _instance is not None:
                seen.add(id(_instance))
                await _instance.close()
                _instance = None
            for sock in list(_pool.values()):
                if id(sock) not in seen:
                    seen.add(id(sock))
                    await sock.close()
            _pool.clear()
        else:
            key = _resolve_socket_path(cwd)
            sock = _pool.pop(key, None)
            if sock is not None:
                await sock.close()
                if sock is _instance:
                    _instance = None
