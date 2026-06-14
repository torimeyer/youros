"""FCP one-shot export helper.

Wraps the full lifecycle of an FCP (File Context Protocol) server session
for generating files in a single pass:

    1. Start the MCP server subprocess (uvx or npx depending on server)
    2. Send a batch of mutation operations
    3. Query status/digest
    4. Save to a target path
    5. Clean up the session

Output directory defaults to ~/.youros/exports/, created on first use.

FCP servers supported:
    - fcp-sheets (Python, via uvx)
    - @ostk-ai/fcp-drawio (Node, via npx)
    - @ostk-ai/fcp-pdf (Node, via npx)
    - @ostk-ai/fcp-slides (Node, via npx)

Each server exposes the same verb-based DSL over MCP stdio transport:
    session/start -> mutations -> query -> session/save -> session/close
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional
from services.youros_paths import youros_home

logger = logging.getLogger(__name__)

# Default output directory for one-shot exports.
EXPORTS_DIR = Path(str(youros_home() / "exports"))

# Map of FCP server name to {"transport": ..., "cmd": [...]} entries.
# Transport "uvx" means Python package, "npx" means Node package.
_SERVER_REGISTRY: dict[str, dict] = {
    "fcp-sheets": {"transport": "uvx", "cmd": ["uvx", "fcp-sheets"]},
    "fcp-slides": {"transport": "uvx", "cmd": ["uvx", "fcp-slides"]},
    "fcp-pdf": {"transport": "uvx", "cmd": ["uvx", "fcp-pdf"]},
    "@ostk-ai/fcp-drawio": {"transport": "npx", "cmd": ["npx", "-y", "@ostk-ai/fcp-drawio"]},
    "@ostk-ai/fcp-pdf": {"transport": "npx", "cmd": ["npx", "-y", "@ostk-ai/fcp-pdf"]},
    "@ostk-ai/fcp-slides": {"transport": "npx", "cmd": ["npx", "-y", "@ostk-ai/fcp-slides"]},
    # Short aliases for convenience
    "drawio": {"transport": "npx", "cmd": ["npx", "-y", "@ostk-ai/fcp-drawio"]},
    "sheets": {"transport": "npx", "cmd": ["npx", "-y", "@ostk-ai/fcp-sheets"]},
    "slides": {"transport": "npx", "cmd": ["npx", "-y", "@ostk-ai/fcp-slides"]},
    "pdf": {"transport": "npx", "cmd": ["npx", "-y", "@ostk-ai/fcp-pdf"]},
}


def detect_transport(server_name: str) -> str:
    """Return 'uvx' or 'npx' based on the server name.

    Known servers use the registry. Unknown names starting with '@' are assumed
    to be npm packages (npx); all other unknown names default to uvx.
    """
    entry = _SERVER_REGISTRY.get(server_name)
    if entry:
        return entry["transport"]
    return "npx" if server_name.startswith("@") else "uvx"


def get_server_command(server_name: str) -> list[str]:
    """Return the subprocess command list for starting the given FCP server.

    Known servers use the registry. Unknown names are inferred: '@'-prefixed
    names get npx -y, plain names get uvx.
    """
    entry = _SERVER_REGISTRY.get(server_name)
    if entry:
        return list(entry["cmd"])
    if server_name.startswith("@"):
        return ["npx", "-y", server_name]
    return ["uvx", server_name]


def _ensure_exports_dir(output_dir: Optional[Path] = None) -> Path:
    """Create and return the exports directory."""
    target = output_dir or EXPORTS_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


class FCPExportError(Exception):
    """Raised when an FCP export operation fails.

    Always includes an actionable message explaining what went wrong and
    how to fix it.
    """
    pass


class FCPExporter:
    """One-shot FCP file generator.

    Usage::

        exporter = FCPExporter("fcp-sheets")
        result = await exporter.export(
            mutations=[
                {"verb": "add_sheet", "args": {"name": "Daily Spend"}},
                {"verb": "set_cell", "args": {"sheet": "Daily Spend", "cell": "A1", "value": "Date"}},
            ],
            output_path=Path("~/.youros/exports/costs-2026-04-15.xlsx"),
        )
        # result = {"path": "/Users/.../costs.xlsx", "filename": "costs.xlsx", "size_bytes": 12345}
    """

    def __init__(self, server_name: str, timeout: int = 30):
        if server_name not in _SERVER_REGISTRY:
            available = ", ".join(sorted(_SERVER_REGISTRY))
            raise ValueError(
                f"Unknown FCP server '{server_name}'. Available servers: {available}"
            )
        self._server_name = server_name
        self.server_name = server_name  # kept for backward compatibility
        self.timeout = timeout
        self._process: Optional[Any] = None
        self._started: bool = False
        self._request_id = 0

    # ------------------------------------------------------------------
    # Async interface (used by fcp_session and live-editing endpoints)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the FCP MCP server subprocess asynchronously."""
        import asyncio
        cmd = get_server_command(self._server_name)
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._started = True

    async def send(self, verb: str, args: dict) -> dict:
        """Send a verb to the FCP server and return the result dict."""
        if not self._started or self._process is None:
            raise RuntimeError(
                f"FCPExporter for '{self._server_name}' is not started. "
                "Call start() first."
            )
        self._request_id += 1
        request = json.dumps({
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": verb,
            "params": args,
        }) + "\n"
        try:
            assert self._process.stdin is not None
            assert self._process.stdout is not None
            self._process.stdin.write(request.encode())
            await self._process.stdin.drain()
            line = await self._process.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"FCP server '{self._server_name}' closed without responding to '{verb}'."
                )
            response = json.loads(line)
            if "error" in response:
                msg = response["error"].get("message", str(response["error"]))
                raise RuntimeError(f"FCP server error for '{verb}': {msg}")
            return response.get("result", {})
        except BrokenPipeError:
            raise RuntimeError(
                f"FCP server '{self._server_name}' pipe is broken during '{verb}'."
            )

    async def close(self) -> None:
        """Close the FCP server subprocess."""
        proc = self._process
        if proc is not None:
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
        self._process = None
        self._started = False

    async def save(self, output_path: "str | Path") -> Path:
        """Create parent directories and send session/save to the FCP server."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        await self.send("session/save", {"path": str(path)})
        return path

    async def __aenter__(self) -> "FCPExporter":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _start_server(self) -> subprocess.Popen:
        """Start the FCP MCP server subprocess."""
        cmd = get_server_command(self.server_name)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return proc
        except FileNotFoundError:
            transport = detect_transport(self.server_name)
            if transport == "uvx":
                raise FCPExportError(
                    f"Could not start '{self.server_name}'. "
                    f"Make sure uv is installed: curl -LsSf https://astral.sh/uv/install.sh | sh"
                )
            raise FCPExportError(
                f"Could not start '{self.server_name}'. "
                f"Make sure Node.js and npx are installed: https://nodejs.org/"
            )
        except OSError as e:
            raise FCPExportError(
                f"Failed to start FCP server '{self.server_name}': {e}. "
                f"Check that the command is available on your PATH."
            )

    def _send_jsonrpc(self, proc: subprocess.Popen, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and read the response."""
        req_id = self._next_id()
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        line = json.dumps(request) + "\n"
        try:
            assert proc.stdin is not None
            assert proc.stdout is not None
            proc.stdin.write(line)
            proc.stdin.flush()

            # Read response line
            response_line = proc.stdout.readline()
            if not response_line:
                raise FCPExportError(
                    f"FCP server '{self.server_name}' closed without responding to '{method}'. "
                    f"The server may have crashed. Check that '{self.server_name}' is installed correctly."
                )
            response = json.loads(response_line)
            if "error" in response:
                err = response["error"]
                msg = err.get("message", str(err))
                raise FCPExportError(
                    f"FCP server returned error for '{method}': {msg}. "
                    f"Check that your mutation operations use the correct verb names and arguments."
                )
            return response.get("result", {})
        except json.JSONDecodeError as e:
            raise FCPExportError(
                f"Invalid JSON response from FCP server for '{method}': {e}. "
                f"This usually means the server printed debug output to stdout. "
                f"Try reinstalling: {'uv tool install ' if detect_transport(self.server_name) == 'uvx' else 'npm install -g '}{self.server_name}"
            )
        except BrokenPipeError:
            raise FCPExportError(
                f"FCP server '{self.server_name}' exited unexpectedly during '{method}'. "
                f"Check server logs in stderr for details."
            )

    def _cleanup(self, proc: Optional[subprocess.Popen]) -> None:
        """Kill and clean up the subprocess."""
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except OSError:
            pass

    async def export(
        self,
        mutations: list[dict[str, Any]],
        output_path: Path,
        output_dir: Optional[Path] = None,
    ) -> dict[str, Any]:
        """Run the full export lifecycle and return file metadata.

        Args:
            mutations: List of mutation dicts, each with "verb" and "args" keys.
            output_path: Where to save the generated file.
            output_dir: Override for the exports directory (default ~/.youros/exports/).

        Returns:
            {"path": str, "filename": str, "size_bytes": int}
        """
        target_dir = _ensure_exports_dir(output_dir or output_path.parent)
        if not output_path.is_absolute():
            output_path = target_dir / output_path.name

        # Ensure parent exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        proc = None
        try:
            proc = self._start_server()

            # 1. Initialize session
            self._send_jsonrpc(proc, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "myos-fcp-export", "version": "1.0.0"},
            })

            # 2. Start a new document session
            self._send_jsonrpc(proc, "tools/call", {
                "name": "session_start",
                "arguments": {"path": str(output_path)},
            })

            # 3. Apply mutations
            for mutation in mutations:
                verb = mutation.get("verb", "")
                args = mutation.get("args", {})
                self._send_jsonrpc(proc, "tools/call", {
                    "name": verb,
                    "arguments": args,
                })

            # 4. Save
            self._send_jsonrpc(proc, "tools/call", {
                "name": "session_save",
                "arguments": {"path": str(output_path)},
            })

            # 5. Close session
            try:
                self._send_jsonrpc(proc, "tools/call", {
                    "name": "session_close",
                    "arguments": {},
                })
            except FCPExportError:
                # Some servers don't have session_close. That's fine.
                pass

            # Verify the file was created
            if not output_path.exists():
                raise FCPExportError(
                    f"FCP server completed but the output file was not created at {output_path}. "
                    f"Check that the server has write permissions to that directory."
                )

            size = output_path.stat().st_size
            return {
                "path": str(output_path),
                "filename": output_path.name,
                "size_bytes": size,
            }

        except FCPExportError:
            raise
        except Exception as e:
            raise FCPExportError(
                f"Unexpected error during FCP export: {e}. "
                f"Try running the server manually to check: {' '.join(get_server_command(self.server_name))}"
            )
        finally:
            self._cleanup(proc)

    def query(self, proc: subprocess.Popen) -> dict:
        """Query the current document state/digest."""
        return self._send_jsonrpc(proc, "tools/call", {
            "name": "session_query",
            "arguments": {},
        })
