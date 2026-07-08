"""Make logging non-blocking so it can never freeze the asyncio event loop.

Root cause of →2042 ("app freezes for ~70s at a time"):
``SlowCallMiddleware`` (and other code paths) call ``logger.warning(...)``
directly on the event-loop thread. ``logging.Handler.emit`` writes to stderr,
which under ``dev-backend.sh`` / the spawn-capture layer is a *pipe*. When that
pipe's buffer fills under a burst of log lines, ``write()`` blocks while holding
the global ``logging`` lock, freezing the entire event loop until the pipe
drains. Under concurrent load this is self-amplifying: slow requests emit
"slow request" warnings, which block, which makes more requests slow. A
faulthandler dump taken during a live freeze showed the loop parked in
``logging/__init__.py`` -> ``emit`` under ``slow_call_middleware.py``.

Fix: route every existing handler behind a ``QueueHandler``. ``QueueHandler``'s
``emit`` only does ``queue.put_nowait()`` (never blocks). A single background
``QueueListener`` thread performs the actual, possibly-blocking writes. If
stderr stalls, only that one thread waits; the event loop keeps serving.

This is the standard CPython recipe for "logging must not block" and fixes the
whole class, not just the one middleware.
"""
from __future__ import annotations

import logging
import logging.handlers
import queue
from typing import List, Optional, Set

_listener: Optional[logging.handlers.QueueListener] = None


def install_nonblocking_logging() -> Optional[logging.handlers.QueueListener]:
    """Route all currently-configured log handlers through a background queue.

    Idempotent: a second call is a no-op and returns the existing listener.
    Returns the started ``QueueListener`` (or ``None`` if there were no
    handlers to wrap yet).
    """
    global _listener
    if _listener is not None:
        return _listener

    root = logging.getLogger()
    # Wrap the root logger plus any named logger that owns its own handlers
    # (uvicorn configures "uvicorn", "uvicorn.error", "uvicorn.access" with
    # propagate=False, so their records never reach root and must be wrapped
    # directly).
    targets: List[logging.Logger] = [root]
    for name in list(logging.root.manager.loggerDict):
        obj = logging.getLogger(name)
        if getattr(obj, "handlers", None):
            targets.append(obj)

    sink_handlers: List[logging.Handler] = []
    seen: Set[int] = set()
    for lg in targets:
        for h in list(lg.handlers):
            # Skip handlers already routed through a queue (e.g. a stale wrap
            # left by a prior install); they are not real sinks. Real-app
            # idempotency is handled by the module-level ``_listener`` guard
            # above; this only keeps test/reload re-entry from double-wrapping.
            if isinstance(h, logging.handlers.QueueHandler):
                continue
            if id(h) not in seen:
                sink_handlers.append(h)
                seen.add(id(h))

    # If no handlers exist anywhere (fresh interpreter, test with no prior
    # setup), add a stderr sink so myos.chat.* records have somewhere to go.
    # Without this the function returned None and never installed the queue,
    # leaving the myos namespace with no sink at all.
    if not sink_handlers:
        fallback = logging.StreamHandler()
        fallback.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        )
        root.addHandler(fallback)
        sink_handlers.append(fallback)

    # SimpleQueue is unbounded and thread-safe; put_nowait never blocks, so a
    # stalled stderr can never back-pressure onto the event loop.
    log_queue: "queue.SimpleQueue" = queue.SimpleQueue()
    qh = logging.handlers.QueueHandler(log_queue)
    for lg in targets:
        lg.handlers = [qh]

    listener = logging.handlers.QueueListener(
        log_queue, *sink_handlers, respect_handler_level=True
    )
    listener.start()
    _listener = listener

    # myos.chat.* loggers emit at INFO level (e.g. claude_phase=first_token).
    # The root logger in production has level=WARNING; without an explicit
    # level on the "myos" namespace those INFO records are filtered before any
    # handler sees them. Setting DEBUG here lets the effective level for all
    # myos.* loggers resolve to DEBUG so timing diagnostics reach the log.
    myos_log = logging.getLogger("myos")
    if myos_log.level == logging.NOTSET:
        myos_log.setLevel(logging.DEBUG)

    return listener


def shutdown_nonblocking_logging() -> None:
    """Stop the background listener (drains remaining records). For tests/shutdown."""
    global _listener
    if _listener is not None:
        try:
            _listener.stop()
        finally:
            _listener = None
