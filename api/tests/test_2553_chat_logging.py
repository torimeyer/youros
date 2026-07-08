"""Regression tests for →2553: myos.chat.* log records must reach the log file.

Root cause: root logger in production has level=WARNING and no handlers.
INFO records from myos.chat.claude_code (e.g. claude_phase=first_token) were
silently filtered before reaching any handler because:
  1. No handler on the myos namespace → records propagate to root with no sink.
  2. Root effective level = WARNING → INFO < WARNING → filtered at isEnabledFor.

Fix in install_nonblocking_logging():
  - Add a fallback StreamHandler to root when no handlers exist at startup.
  - Set logging.getLogger("myos").level = DEBUG so effective level resolves
    to DEBUG for all myos.* loggers, making INFO records pass through.
"""
from __future__ import annotations

import logging
import logging.handlers
import time

import pytest

import services.nonblocking_logging as nbl
from services.nonblocking_logging import (
    install_nonblocking_logging,
    shutdown_nonblocking_logging,
)


class _CapturingHandler(logging.Handler):
    """Minimal sink that records messages for assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot and restore global logging state so each test is isolated."""
    root = logging.getLogger()
    saved_root_handlers = root.handlers[:]
    saved_root_level = root.level

    myos_log = logging.getLogger("myos")
    saved_myos_handlers = myos_log.handlers[:]
    saved_myos_level = myos_log.level
    saved_myos_propagate = myos_log.propagate

    nbl._listener = None
    yield
    shutdown_nonblocking_logging()
    root.handlers = saved_root_handlers
    root.level = saved_root_level
    myos_log.handlers = saved_myos_handlers
    myos_log.level = saved_myos_level
    myos_log.propagate = saved_myos_propagate
    nbl._listener = None


def test_install_with_no_existing_handlers_returns_listener():
    """install_nonblocking_logging must succeed even when no handlers exist yet.

    Previous behaviour: returned None when root had no handlers (production
    cold-start). The fix always returns a started QueueListener.
    """
    root = logging.getLogger()
    root.handlers = []  # simulate production: no handlers
    root.setLevel(logging.WARNING)

    listener = install_nonblocking_logging()
    assert listener is not None, "expected a QueueListener, got None"


def test_myos_level_set_to_debug_after_install():
    """myos logger must have an explicit level after install so INFO passes."""
    root = logging.getLogger()
    root.handlers = []
    root.setLevel(logging.WARNING)

    install_nonblocking_logging()

    myos_log = logging.getLogger("myos")
    assert myos_log.level != logging.NOTSET, "myos logger level should not be NOTSET"
    assert myos_log.level <= logging.DEBUG, (
        f"myos logger level should be <= DEBUG, got {myos_log.level}"
    )


def test_myos_chat_info_records_reach_sink():
    """INFO records from myos.chat.* must survive the level filter and reach a handler.

    This is the direct regression test for the reported symptom:
    claude_phase=first_token lines never showed up in the log.
    """
    sink = _CapturingHandler()
    sink.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.handlers = [sink]
    root.setLevel(logging.WARNING)  # simulate production default

    install_nonblocking_logging()

    chat_log = logging.getLogger("myos.chat.claude_code")
    chat_log.info("claude_phase=first_token ms=1234")

    # Give the background queue thread time to flush.
    deadline = time.time() + 3.0
    while not sink.records and time.time() < deadline:
        time.sleep(0.02)

    assert any("claude_phase=first_token" in r for r in sink.records), (
        f"expected first_token record in sink, got: {sink.records}"
    )


def test_myos_level_not_overridden_if_already_set():
    """install_nonblocking_logging must not lower an explicitly-set myos level."""
    root = logging.getLogger()
    root.handlers = []
    root.setLevel(logging.WARNING)

    myos_log = logging.getLogger("myos")
    myos_log.setLevel(logging.ERROR)  # operator explicitly wants ERROR+

    install_nonblocking_logging()

    assert myos_log.level == logging.ERROR, (
        "install_nonblocking_logging must not override an already-set myos level"
    )


def test_fallback_handler_wrapped_in_queue_handler():
    """The fallback StreamHandler added for the no-handler case must be async (queued)."""
    root = logging.getLogger()
    root.handlers = []
    root.setLevel(logging.WARNING)

    install_nonblocking_logging()

    assert root.handlers, "root should have at least one handler after install"
    assert all(isinstance(h, logging.handlers.QueueHandler) for h in root.handlers), (
        "all handlers on root should be QueueHandler after install"
    )
