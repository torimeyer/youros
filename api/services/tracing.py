"""Structured audit tracing for myOS.

Two tiers:
  A. Durable audit entries -> .ostk/audit.jsonl
  B. Structured log lines  -> backend logger (per-request, not durable)

All functions are wrapped in try/except and will never raise.
"""
import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT

logger = logging.getLogger(__name__)

# Per-request trace ID. Set by TraceMiddleware for each HTTP request.
# Default is empty string so callers never receive None.
_trace_id_var: ContextVar[str] = ContextVar("_trace_id_var", default="")


def get_trace_id() -> str:
    """Return the trace ID for the current request context."""
    return _trace_id_var.get()


def trace_event(name: str, **kwargs: Any) -> None:
    """Emit one structured audit event.

    Tier A: appends a JSON line to .ostk/audit.jsonl (durable).
    Tier B: logs a JSON line at INFO level (per-request, not durable).

    Never raises. Any exception is silently swallowed so instrumentation
    can never break a request path.
    """
    try:
        ts = datetime.now(timezone.utc).isoformat()
        trace_id = _trace_id_var.get()
        payload: dict[str, Any] = {
            "ts": ts,
            "trace_id": trace_id,
            "event": name,
        }
        payload.update(kwargs)

        line = json.dumps(payload, default=str)

        # Tier B: structured log (always)
        logger.info(line)

        # Tier A: durable append to audit.jsonl
        try:
            ostk_dir = Path(PROJECT_ROOT).parent / ".ostk"
            audit_path = ostk_dir / "audit.jsonl"
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            with audit_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass  # audit write failure must never surface

    except Exception:
        pass  # tracing must never raise
