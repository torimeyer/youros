"""Regression tests for →2042: logging must never block the event loop.

The live freeze was the event loop parked inside logging.emit because a log
handler's write() to a full stderr pipe blocked while holding the logging lock.
These tests assert the structural fix (handlers wrapped in a QueueHandler) and
the behavioural guarantee (emitting a record with a deliberately-blocking sink
handler returns immediately instead of blocking the caller).
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


class _BlockingHandler(logging.Handler):
    """Sink handler whose emit sleeps, simulating a stalled stderr pipe."""

    def __init__(self, block_seconds: float) -> None:
        super().__init__()
        self.block_seconds = block_seconds
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        time.sleep(self.block_seconds)
        self.records.append(record.getMessage())


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot and restore global logging state so each test is isolated.

    install_nonblocking_logging() mutates the root logger (and uvicorn-style
    named loggers) in place, so without a full snapshot one test would leak a
    QueueHandler onto root and poison the next.
    """
    root = logging.getLogger()
    saved_root = root.handlers[:]
    nbl._listener = None
    yield
    shutdown_nonblocking_logging()
    root.handlers = saved_root
    nbl._listener = None


def _make_logger(name: str, handler: logging.Handler) -> logging.Logger:
    lg = logging.getLogger(name)
    lg.handlers = [handler]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    return lg


def test_install_wraps_handlers_in_queue_handler():
    logger = _make_logger("test_2042_struct", _BlockingHandler(0.0))
    listener = install_nonblocking_logging()
    assert listener is not None
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.handlers.QueueHandler)


def test_emit_does_not_block_on_slow_handler():
    """The core guarantee: a slow sink must not block the calling (loop) thread."""
    sink = _BlockingHandler(2.0)  # 2s per write, like a stalled pipe
    logger = _make_logger("test_2042_block", sink)

    install_nonblocking_logging()
    t0 = time.perf_counter()
    logger.warning("this must not block the caller")
    elapsed = time.perf_counter() - t0
    # Without the fix this would take ~2s; with QueueHandler it is a
    # put_nowait and returns essentially instantly.
    assert elapsed < 0.25, f"logging blocked the caller for {elapsed:.2f}s"
    # And the record is still delivered by the listener thread.
    deadline = time.time() + 5
    while not sink.records and time.time() < deadline:
        time.sleep(0.05)
    assert sink.records == ["this must not block the caller"]


def test_install_is_idempotent():
    _make_logger("test_2042_idem", _BlockingHandler(0.0))
    first = install_nonblocking_logging()
    second = install_nonblocking_logging()
    assert first is second
