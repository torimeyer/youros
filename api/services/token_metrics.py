"""Per-turn token tracking for ostk self-measurement.

Records every chat turn (input/output tokens, model, whether ostk boot
context was injected, and the size of that boot context) to
``<project>/.ostk/metrics.jsonl`` so ``ostk metrics`` can report real
numbers about how much context ostk is loading and how that compares to
turns without it.

The whole point: if Tori asks "how many tokens has ostk saved", the
honest answer must come from recorded data, never from estimation or
guesswork. This module is the collection side. The Rust ``ostk metrics``
command aggregates the events on the read side.

Path note: ``ostk metrics`` is project-scoped. It reads
``<project>/.ostk/metrics.jsonl``. The squasher already writes there.
Writing chat turn
events to the same file is the only way the same command can show real
numbers without changing the Rust read path. ``.ostk/`` is gitignored,
so this data is not at risk from ``git pull``.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from config import PROJECT_ROOT

# Same path the Rust `ostk metrics` command reads from. The squasher
# also writes here under the "squash" event type.
_METRICS_PATH = Path(PROJECT_ROOT) / ".ostk" / "metrics.jsonl"

# TTL cache for get_ostk_savings(): (timestamp, result). The ostk binary
# subprocess is ~100 ms. Caching for 30 seconds means concurrent requests
# (e.g. dashboard + costs page loading simultaneously) share one call.
_SAVINGS_CACHE: Optional[tuple[float, Optional[dict]]] = None
_SAVINGS_TTL_SECONDS = 30

# Incremental running-totals cache for conv cache stats from metrics.jsonl.
# Keyed on (file_size, mtime_ns, inode). On cache miss where the file has
# only grown (same inode, larger size) we read only the new tail bytes
# instead of re-reading the entire 500 MB+ file.
# Tuple: (file_size, mtime_ns, inode, cache_read, cache_create, total_input)
_METRICS_TOTALS_CACHE: Optional[tuple[int, int, int, int, int, int]] = None

# Disk-persisted copy of _METRICS_TOTALS_CACHE so backend restarts skip the
# 4+ second cold full-scan of the 500 MB+ metrics.jsonl. Loaded once on the
# first call to _compute_conv_totals_incremental, then kept in sync with
# every in-memory update. If metrics.jsonl grew since the last save we do a
# fast tail-read (milliseconds); if the inode changed we fall through to the
# full re-read and overwrite the disk cache.
_METRICS_DISK_CACHE_PATH = Path(PROJECT_ROOT) / ".ostk" / "metrics_totals_cache.json"


def _load_metrics_disk_cache() -> None:
    """Populate _METRICS_TOTALS_CACHE from the persisted disk copy.

    No-op if the cache is already warm or the disk file is missing/corrupt.
    Called once at the top of _compute_conv_totals_incremental.
    """
    global _METRICS_TOTALS_CACHE
    if _METRICS_TOTALS_CACHE is not None:
        return
    try:
        if not _METRICS_DISK_CACHE_PATH.exists():
            return
        data = json.loads(_METRICS_DISK_CACHE_PATH.read_text())
        _METRICS_TOTALS_CACHE = (
            int(data["file_size"]),
            int(data["mtime_ns"]),
            int(data["inode"]),
            int(data["cache_read"]),
            int(data["cache_create"]),
            int(data["total_input"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass


def _save_metrics_disk_cache(cache: tuple) -> None:
    """Persist _METRICS_TOTALS_CACHE to disk for the next cold start.

    Best-effort: any I/O failure is silently swallowed.
    """
    file_size, mtime_ns, inode, cache_read, cache_create, total_input = cache
    try:
        _METRICS_DISK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _METRICS_DISK_CACHE_PATH.write_text(json.dumps({
            "file_size": file_size,
            "mtime_ns": mtime_ns,
            "inode": inode,
            "cache_read": cache_read,
            "cache_create": cache_create,
            "total_input": total_input,
        }))
    except OSError:
        pass


def _compute_conv_totals_incremental() -> tuple[int, int, int]:
    """Return (conv_cache_read, conv_cache_creation, conv_total_input).

    Uses an append-only incremental read: after the first full pass, only
    new bytes appended since the last read are parsed. Inode is checked so
    a file replacement always triggers a full re-read.

    On the first call after a backend restart _METRICS_TOTALS_CACHE is None.
    _load_metrics_disk_cache() restores it from disk so the hot path (tail
    read) kicks in immediately instead of triggering a 4+ second full scan
    of the 500 MB+ metrics.jsonl that holds the Python GIL and blocks TLS
    handshakes for all incoming connections (→29 wedge root cause).
    """
    global _METRICS_TOTALS_CACHE
    _load_metrics_disk_cache()
    try:
        if not _METRICS_PATH.exists():
            _METRICS_TOTALS_CACHE = None
            return (0, 0, 0)
        st = _METRICS_PATH.stat()
    except OSError:
        return (0, 0, 0)

    if _METRICS_TOTALS_CACHE is not None:
        c_size, c_mtime, c_ino, c_read, c_create, c_input = _METRICS_TOTALS_CACHE
        if st.st_size == c_size and st.st_mtime_ns == c_mtime and st.st_ino == c_ino:
            return (c_read, c_create, c_input)
        if st.st_ino == c_ino and st.st_size > c_size:
            try:
                with open(_METRICS_PATH, "rb") as f:
                    f.seek(c_size)
                    new_bytes = f.read()
                new_read, new_create, new_input = c_read, c_create, c_input
                for line in new_bytes.decode("utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    try:
                        ev = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if ev.get("event") != "chat_turn":
                        continue
                    new_read += int(ev.get("cache_read_input_tokens", 0) or 0)
                    new_create += int(ev.get("cache_creation_input_tokens", 0) or 0)
                    new_input += int(ev.get("input_tokens", 0) or 0)
                _METRICS_TOTALS_CACHE = (st.st_size, st.st_mtime_ns, st.st_ino, new_read, new_create, new_input)
                _save_metrics_disk_cache(_METRICS_TOTALS_CACHE)
                return (new_read, new_create, new_input)
            except OSError:
                pass

    # Full re-read (cold cache or inode changed — unavoidable but infrequent)
    conv_cache_read = conv_cache_creation = conv_total_input = 0
    try:
        with open(_METRICS_PATH, "rb") as f:
            raw = f.read()
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if ev.get("event") != "chat_turn":
                continue
            conv_cache_read += int(ev.get("cache_read_input_tokens", 0) or 0)
            conv_cache_creation += int(ev.get("cache_creation_input_tokens", 0) or 0)
            conv_total_input += int(ev.get("input_tokens", 0) or 0)
    except OSError:
        pass

    _METRICS_TOTALS_CACHE = (st.st_size, st.st_mtime_ns, st.st_ino, conv_cache_read, conv_cache_creation, conv_total_input)
    _save_metrics_disk_cache(_METRICS_TOTALS_CACHE)
    return (conv_cache_read, conv_cache_creation, conv_total_input)


def invalidate_conv_totals_cache() -> None:
    global _METRICS_TOTALS_CACHE
    _METRICS_TOTALS_CACHE = None


def _ensure_parent() -> None:
    try:
        _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def record_chat_turn(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    has_ostk_boot: bool,
    boot_context_bytes: int = 0,
    backend: str = "anthropic_api",
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> None:
    """Append one chat-turn event to ``<project>/.ostk/metrics.jsonl``.

    Best effort: any IO failure is swallowed so a metrics outage can
    never break a chat response.
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return
    _ensure_parent()
    event = {
        "event": "chat_turn",
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "has_ostk_boot": bool(has_ostk_boot),
        "boot_context_bytes": int(boot_context_bytes),
        "backend": backend,
        "cache_creation_input_tokens": int(cache_creation_input_tokens),
        "cache_read_input_tokens": int(cache_read_input_tokens),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    line = json.dumps(event, separators=(",", ":")) + "\n"
    try:
        with open(_METRICS_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def safe_record_chat_turn(**kwargs) -> None:
    """Wrapper that never raises. Use from chat handlers."""
    try:
        record_chat_turn(**kwargs)
    except Exception:
        pass
    # →2960: chat turns are the natural heartbeat for the compression
    # feed. Throttled and run on a daemon thread so the chat path (which
    # sits on the event loop) never blocks on the kernel subprocess.
    try:
        maybe_record_compression()
    except Exception:
        pass


def record_agent_run(
    *,
    agent_name: str,
    model: str,
    tokens_used: int,
    source: str = "agent",
) -> None:
    """Append one agent-run event to ``<project>/.ostk/metrics.jsonl``.

    Called at agent completion time with the run's REAL recorded token
    total (the agent board row's ``tokens_used`` counter). Chat turns are
    recorded per-turn by ``record_chat_turn``; agent runs previously
    recorded nothing, so the savings window only ever counted chat
    (→2961). The ``source`` tag lets readers tell agent runs apart from
    chat turns.

    Skips runs with no recorded tokens: writing a zero would fabricate
    savings-page availability for a run that did no measured work.
    Best effort: any IO failure is swallowed so a metrics outage can
    never break agent completion.
    """
    try:
        tokens = int(tokens_used or 0)
    except (TypeError, ValueError):
        return
    if tokens <= 0:
        return
    _ensure_parent()
    event = {
        "event": "agent_run",
        "agent": agent_name,
        "model": model,
        "tokens_used": tokens,
        "source": source,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    line = json.dumps(event, separators=(",", ":")) + "\n"
    try:
        with open(_METRICS_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def safe_record_agent_run(**kwargs) -> None:
    """Wrapper that never raises. Use from the agent completion path."""
    try:
        record_agent_run(**kwargs)
    except Exception:
        pass
    # Agent completions are a second natural heartbeat for the →2960
    # compression feed: a day of agent-only work still advances the
    # kernel squash counters, and without this the recorder would never
    # fire on days with zero chat turns.
    try:
        maybe_record_compression()
    except Exception:
        pass


def _run_ostk_metrics_json() -> Optional[dict]:
    """Shell out to ``ostk os metrics --json`` and return the raw payload.

    Returns ``None`` on any failure (missing binary, timeout, non-zero
    exit, unparseable output). Never raises and never invents data.
    """
    try:
        result = subprocess.run(
            [os.path.expanduser("~/.local/bin/ostk"), "os", "metrics", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(PROJECT_ROOT),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    try:
        payload = json.loads(result.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def _fetch_ostk_savings_raw() -> Optional[dict]:
    """Shell out to ``ostk os metrics --json`` and return parsed savings dict.

    Returns ``None`` on any failure. This is the cold-path call; callers
    should normally go through ``get_ostk_savings`` which adds a TTL cache.
    """
    payload = _run_ostk_metrics_json()
    if payload is None:
        return None

    prompt_cache = payload.get("prompt_cache") or {}
    squash = payload.get("squash") or {}
    if not isinstance(prompt_cache, dict):
        prompt_cache = {}
    if not isinstance(squash, dict):
        squash = {}

    def _as_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    cache_savings = _as_float(prompt_cache.get("cache_savings_usd"))
    compression_savings = _as_float(squash.get("est_saved_usd"))

    # Compute conversation-level cache stats from our own metrics.jsonl.
    # Uses the incremental tail-reader so only new bytes are parsed after
    # the first full read (avoids re-reading the 500 MB+ file every 30 s).
    conv_cache_read, conv_cache_creation, conv_total_input = _compute_conv_totals_incremental()

    conversation_cache_pct = 0.0
    if conv_total_input > 0:
        conversation_cache_pct = round((conv_cache_read / conv_total_input) * 100, 1)

    return {
        "savings_usd": round(cache_savings + compression_savings, 4),
        "cache_efficiency_pct": _as_float(prompt_cache.get("efficiency_pct")),
        "compression_pct": _as_float(squash.get("compression_pct")),
        "cost_without_ostk_usd": _as_float(prompt_cache.get("no_cache_cost_usd")),
        "cost_with_ostk_usd": _as_float(prompt_cache.get("cost_usd")),
        "conversation_cache_pct": conversation_cache_pct,
        "conversation_cache_read_tokens": conv_cache_read,
        "conversation_cache_creation_tokens": conv_cache_creation,
        "period": "session",
    }


def get_ostk_savings() -> Optional[dict]:
    """Return ostk savings data, cached for ``_SAVINGS_TTL_SECONDS`` seconds.

    Shells out to ``ostk os metrics --json`` on a cache miss and caches
    the result (including ``None`` on failure) so concurrent page loads
    and dashboard refreshes share a single subprocess call. The TTL is
    short enough that savings numbers update within half a minute.

    Returns ``None`` when the ``ostk`` binary is missing, exits with a
    non-zero status, or returns something that cannot be parsed. Callers
    should treat ``None`` as "no savings data available" and show a
    neutral empty state.

    The returned shape is:

    .. code-block:: python

        {
            "savings_usd": float,          # cache + compression savings
            "cache_efficiency_pct": float, # percent of prompts served from cache
            "compression_pct": float,      # compression ratio on stored context
            "cost_without_ostk_usd": float,
            "cost_with_ostk_usd": float,
            "period": "session",
        }
    """
    global _SAVINGS_CACHE
    now = time.monotonic()
    if _SAVINGS_CACHE is not None:
        cached_at, cached_result = _SAVINGS_CACHE
        if now - cached_at < _SAVINGS_TTL_SECONDS:
            return cached_result

    result = _fetch_ostk_savings_raw()
    _SAVINGS_CACHE = (now, result)
    return result


def invalidate_savings_cache() -> None:
    """Force the next ``get_ostk_savings`` call to re-run the subprocess.

    Useful in tests that need to observe fresh values.
    """
    global _SAVINGS_CACHE
    _SAVINGS_CACHE = None


# ---------------------------------------------------------------------------
# →2960: compression feed
#
# The kernel keeps session-scoped squash counters (``ostk os metrics --json``
# -> "squash": {original_bytes, compressed_bytes, events, token_savings}).
# Chat turns only ever wrote chat_turn events to metrics.jsonl, so the
# windowed savings reader (_compute_savings_for_period in routers/costs.py)
# honestly reported 0 compression for today/week. This recorder closes the
# gap: whenever the kernel counters have advanced since the last recorded
# event, it appends ONE squash event carrying the delta, plus the new
# counter totals (kernel_*_total fields) so the next delta needs no extra
# state file: the state lives inside the last event itself.
#
# Triggered from safe_record_chat_turn (throttled to at most one kernel
# read per _COMPRESSION_RECORD_INTERVAL_SECONDS, on a daemon thread). A
# day with zero chat turns therefore still shows 0 windowed compression:
# acceptable and honest, because nothing drives the recorder that day and
# the all-time number still comes from the kernel binary directly.
#
# Failure behavior: kernel call fails -> record nothing, never a
# fabricated event. No counter advance -> no event (idempotent).
# ---------------------------------------------------------------------------

_COMPRESSION_RECORD_INTERVAL_SECONDS = 600  # at most one kernel read per 10 min
_COMPRESSION_LOCK = threading.Lock()
# In-memory copy of the last recorded kernel counters. Cold after restart;
# reloaded from the last kernel_counters event in metrics.jsonl.
_LAST_SQUASH_COUNTERS: Optional[dict] = None
_LAST_COMPRESSION_ATTEMPT: Optional[float] = None

# Only recorder-written events carry this key, which makes it a reliable
# needle for the backwards scan below.
_KERNEL_COUNTER_MARKER = b'"kernel_original_bytes_total"'


def invalidate_compression_recorder_state() -> None:
    """Reset the recorder's in-memory state (tests and diagnostics)."""
    global _LAST_SQUASH_COUNTERS, _LAST_COMPRESSION_ATTEMPT
    _LAST_SQUASH_COUNTERS = None
    _LAST_COMPRESSION_ATTEMPT = None


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_kernel_counter_line(raw: bytes) -> Optional[dict]:
    try:
        ev = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(ev, dict):
        return None
    if ev.get("event") != "squash" or ev.get("source") != "kernel_counters":
        return None
    return {
        "events": _as_int(ev.get("kernel_events_total")),
        "original_bytes": _as_int(ev.get("kernel_original_bytes_total")),
        "compressed_bytes": _as_int(ev.get("kernel_compressed_bytes_total")),
        "token_savings": _as_int(ev.get("kernel_token_savings_total")),
    }


def _find_last_kernel_squash_counters(
    max_scan_bytes: int = 8 * 1024 * 1024,
) -> Optional[dict]:
    """Return the counter totals stored in the most recent recorder event.

    Scans metrics.jsonl backwards in chunks: the usual case (the recorder
    fires on chat turns, so its last event sits near the tail) reads a few
    KB, never the whole 500 MB+ file. The scan is capped; not finding the
    marker within the cap is treated as "no previous event", which only
    re-baselines (zero-delta event), never fabricates savings.
    """
    try:
        if not _METRICS_PATH.exists():
            return None
        size = _METRICS_PATH.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None

    chunk_size = 64 * 1024
    buf = b""
    pos = size
    try:
        with open(_METRICS_PATH, "rb") as f:
            while pos > 0 and (size - pos) < max_scan_bytes:
                step = min(chunk_size, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                if _KERNEL_COUNTER_MARKER not in buf:
                    continue
                lines = buf.split(b"\n")
                # Unless we reached the start of the file, the first line
                # is a potentially partial fragment: skip it and let the
                # next chunk complete it.
                candidates = lines if pos == 0 else lines[1:]
                for raw in reversed(candidates):
                    if _KERNEL_COUNTER_MARKER not in raw:
                        continue
                    counters = _parse_kernel_counter_line(raw)
                    if counters is not None:
                        return counters
    except OSError:
        return None
    return None


def _build_squash_event(
    d_original: int, d_compressed: int, d_tokens: int, totals: dict
) -> dict:
    return {
        "event": "squash",
        "source": "kernel_counters",
        "original": int(d_original),
        "compressed": int(d_compressed),
        "saved": int(d_original) - int(d_compressed),
        "tokens_saved": int(d_tokens),
        "kernel_events_total": totals["events"],
        "kernel_original_bytes_total": totals["original_bytes"],
        "kernel_compressed_bytes_total": totals["compressed_bytes"],
        "kernel_token_savings_total": totals["token_savings"],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _append_metrics_event(event: dict) -> bool:
    _ensure_parent()
    try:
        with open(_METRICS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False


def record_compression_event() -> Optional[dict]:
    """Read kernel squash counters; append the delta as a squash event.

    Returns the appended event dict, or ``None`` when nothing was
    appended (kernel failure, no counter advance, or write failure).
    """
    with _COMPRESSION_LOCK:
        return _record_compression_event_locked()


def _record_compression_event_locked() -> Optional[dict]:
    global _LAST_SQUASH_COUNTERS

    payload = _run_ostk_metrics_json()
    if payload is None:
        return None
    squash = payload.get("squash")
    if not isinstance(squash, dict) or not squash:
        return None

    current = {
        "events": _as_int(squash.get("events")),
        "original_bytes": _as_int(squash.get("original_bytes")),
        "compressed_bytes": _as_int(squash.get("compressed_bytes")),
        "token_savings": _as_int(squash.get("token_savings")),
    }

    last = _LAST_SQUASH_COUNTERS or _find_last_kernel_squash_counters()

    if last is None:
        # First run ever: establish the baseline with a zero-delta event.
        # The session's pre-existing compression is already inside the
        # all-time number; attributing it to today would be fabrication.
        event = _build_squash_event(0, 0, 0, current)
        if not _append_metrics_event(event):
            return None
        _LAST_SQUASH_COUNTERS = current
        return event

    reset = (
        current["original_bytes"] < last["original_bytes"]
        or current["events"] < last["events"]
    )
    if reset:
        # Session-scoped counters restarted. Everything currently on the
        # counters happened after the reset (which happened after our last
        # event), so the honest delta is the counters themselves. Squashes
        # from intervening sessions we never observed are lost:
        # undercounting, never overcounting.
        if current["original_bytes"] <= 0:
            return None
        d_original = current["original_bytes"]
        d_compressed = current["compressed_bytes"]
        d_tokens = current["token_savings"]
    else:
        d_original = current["original_bytes"] - last["original_bytes"]
        d_compressed = current["compressed_bytes"] - last["compressed_bytes"]
        d_tokens = current["token_savings"] - last["token_savings"]
        if d_original <= 0:
            # No advance: nothing to record (idempotent). Cache the
            # counters so the next check skips the disk scan.
            _LAST_SQUASH_COUNTERS = current
            return None

    event = _build_squash_event(
        d_original, max(d_compressed, 0), max(d_tokens, 0), current
    )
    if not _append_metrics_event(event):
        # Leave the baseline untouched so a later attempt retries the delta.
        return None
    _LAST_SQUASH_COUNTERS = current
    return event


def maybe_record_compression() -> Optional[threading.Thread]:
    """Throttled, non-blocking entry point for the chat-turn path.

    At most one kernel read per ``_COMPRESSION_RECORD_INTERVAL_SECONDS``,
    run on a daemon thread because callers sit on the event loop. Returns
    the started thread (so tests can join it) or ``None`` when throttled.
    Never raises.
    """
    global _LAST_COMPRESSION_ATTEMPT
    try:
        now = time.monotonic()
        if (
            _LAST_COMPRESSION_ATTEMPT is not None
            and now - _LAST_COMPRESSION_ATTEMPT < _COMPRESSION_RECORD_INTERVAL_SECONDS
        ):
            return None
        _LAST_COMPRESSION_ATTEMPT = now
        thread = threading.Thread(target=_record_compression_thread, daemon=True)
        thread.start()
        return thread
    except Exception:
        return None


def _record_compression_thread() -> None:
    try:
        record_compression_event()
    except Exception:
        pass
