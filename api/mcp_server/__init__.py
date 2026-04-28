"""myOS MCP stdio server — exposes task/spec/agent/memory tools over JSON-RPC 2.0."""

from .server import start_stdio_server

__all__ = ["start_stdio_server"]
