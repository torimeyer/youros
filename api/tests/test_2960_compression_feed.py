"""Tests for →2960: real compression events recorded into metrics.jsonl.

The kernel (``ostk os metrics --json``) keeps session-scoped squash
counters. ``services.token_metrics.record_compression_event`` reads those
counters and appends a delta ``squash`` event to metrics.jsonl whenever
the counters have advanced since the last recorded event. The last-seen
counters are stored inside the event itself (``kernel_*_total`` fields)
so no extra state file is needed.

All subprocess calls are mocked; nothing here touches the real kernel.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from services import token_metrics


def _fake_completed(stdout: str, returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


def _kernel_payload(events=10, original=5000, compressed=2800, token_savings=550):
    """A realistic ``ostk os metrics --json`` payload (squash counters)."""
    return {
        "prompt_cache": {
            "cache_savings_usd": 0.01,
            "efficiency_pct": 50.0,
            "cost_usd": 0.01,
            "no_cache_cost_usd": 0.02,
        },
        "squash": {
            "token_savings": token_savings,
            "compression_pct": 44.0,
            "original_bytes": original,
            "compressed_bytes": compressed,
            "events": events,
            "pre_squash_tokens": 1250,
            "post_squash_tokens": 700,
            "est_saved_usd": 0.005,
        },
    }


def _marker_event(
    events=4,
    original=1000,
    compressed=800,
    token_savings=50,
    ts="2026-07-18T00:00:00Z",
):
    """A previously recorded kernel-counter squash event (the stored state)."""
    return {
        "event": "squash",
        "source": "kernel_counters",
        "original": 0,
        "compressed": 0,
        "saved": 0,
        "tokens_saved": 0,
        "kernel_events_total": events,
        "kernel_original_bytes_total": original,
        "kernel_compressed_bytes_total": compressed,
        "kernel_token_savings_total": token_savings,
        "ts": ts,
    }


def _chat_turn(ts: str, input_tokens=500, cache_read=400) -> dict:
    return {
        "event": "chat_turn",
        "model": "claude-sonnet-4",
        "input_tokens": input_tokens,
        "output_tokens": 100,
        "has_ostk_boot": True,
        "boot_context_bytes": 500,
        "backend": "anthropic_api",
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cache_read,
        "ts": ts,
    }


def _lines(path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


@pytest.fixture(autouse=True)
def _reset_recorder_state():
    token_metrics.invalidate_compression_recorder_state()
    token_metrics.invalidate_savings_cache()
    token_metrics.invalidate_conv_totals_cache()
    yield
    token_metrics.invalidate_compression_recorder_state()
    token_metrics.invalidate_savings_cache()
    token_metrics.invalidate_conv_totals_cache()


# ---------------------------------------------------------------------------
# (a) counter advance appends exactly one squash event with the delta
# ---------------------------------------------------------------------------

def test_counter_advance_appends_one_delta_event(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text(
        json.dumps(
            _marker_event(events=4, original=1000, compressed=800, token_savings=50)
        )
        + "\n"
    )

    with patch("services.token_metrics.subprocess.run") as run, \
         patch("services.token_metrics._METRICS_PATH", metrics):
        run.return_value = _fake_completed(
            json.dumps(
                _kernel_payload(
                    events=10, original=5000, compressed=2800, token_savings=550
                )
            )
        )
        appended = token_metrics.record_compression_event()

    assert appended is not None
    rows = _lines(metrics)
    assert len(rows) == 2, f"expected exactly one appended event, got {rows}"
    ev = rows[-1]
    assert ev["event"] == "squash"
    assert ev["source"] == "kernel_counters"
    # Deltas: 5000-1000, 2800-800, 550-50
    assert ev["original"] == 4000
    assert ev["compressed"] == 2000
    assert ev["saved"] == 2000
    assert ev["tokens_saved"] == 500
    # New totals stored inside the event (the state for the next delta)
    assert ev["kernel_events_total"] == 10
    assert ev["kernel_original_bytes_total"] == 5000
    assert ev["kernel_compressed_bytes_total"] == 2800
    assert ev["kernel_token_savings_total"] == 550
    assert "ts" in ev


# ---------------------------------------------------------------------------
# (b) no counter advance appends nothing (idempotent)
# ---------------------------------------------------------------------------

def test_no_counter_advance_appends_nothing(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text(
        json.dumps(
            _marker_event(events=10, original=5000, compressed=2800, token_savings=550)
        )
        + "\n"
    )

    with patch("services.token_metrics.subprocess.run") as run, \
         patch("services.token_metrics._METRICS_PATH", metrics):
        run.return_value = _fake_completed(
            json.dumps(
                _kernel_payload(
                    events=10, original=5000, compressed=2800, token_savings=550
                )
            )
        )
        # First call: reads state from disk (the marker event)
        assert token_metrics.record_compression_event() is None
        # Second call: reads state from the in-memory cache
        assert token_metrics.record_compression_event() is None
        # Third call with cold in-memory state: disk path again
        token_metrics.invalidate_compression_recorder_state()
        assert token_metrics.record_compression_event() is None

    assert len(_lines(metrics)) == 1, "no advance must append nothing"


# ---------------------------------------------------------------------------
# (c) kernel failure appends nothing, never a fabricated event
# ---------------------------------------------------------------------------

def test_kernel_failure_appends_nothing(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text(json.dumps(_marker_event()) + "\n")

    with patch("services.token_metrics.subprocess.run") as run, \
         patch("services.token_metrics._METRICS_PATH", metrics):
        run.side_effect = FileNotFoundError("ostk not found")
        assert token_metrics.record_compression_event() is None

        run.side_effect = None
        run.return_value = _fake_completed("", returncode=2)
        assert token_metrics.record_compression_event() is None

        run.return_value = _fake_completed("not json at all")
        assert token_metrics.record_compression_event() is None

        # Payload without a squash section is also a no-op
        run.return_value = _fake_completed(json.dumps({"prompt_cache": {}}))
        assert token_metrics.record_compression_event() is None

    assert len(_lines(metrics)) == 1, "kernel failure must never fabricate an event"


# ---------------------------------------------------------------------------
# (d) the appended event makes _compute_savings_for_period report the
#     window's compression (exercises the real reader in routers.costs)
# ---------------------------------------------------------------------------

def test_appended_event_feeds_windowed_savings(tmp_path):
    from routers import costs as costs_router

    metrics = tmp_path / "metrics.jsonl"
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # A chat turn today plus the stored counter state from a previous run.
    metrics.write_text(
        json.dumps(_chat_turn(now_ts)) + "\n"
        + json.dumps(
            _marker_event(events=4, original=1000, compressed=800, token_savings=50, ts=now_ts)
        )
        + "\n"
    )

    payload = _kernel_payload(events=10, original=5000, compressed=2800, token_savings=550)

    with patch("services.token_metrics.subprocess.run") as run, \
         patch("services.token_metrics._METRICS_PATH", metrics):
        run.return_value = _fake_completed(json.dumps(payload))
        appended = token_metrics.record_compression_event()
        assert appended is not None

        # Now exercise the real reader for the "today" window.
        token_metrics.invalidate_savings_cache()
        costs_router._savings_cache.clear()
        costs_router.invalidate_metrics_parse_cache()
        run.return_value = _fake_completed(json.dumps(payload))
        result = costs_router._compute_savings_for_period("today")

    assert result["available"] is True
    # Delta: original 4000 -> compressed 2000 = 50% compression in the window.
    assert result["compression_pct"] == pytest.approx(50.0), (
        f"windowed compression must come from the recorded delta event, got {result}"
    )


# ---------------------------------------------------------------------------
# First run ever: a zero-delta baseline event establishes the counter state
# without attributing all-time compression to today
# ---------------------------------------------------------------------------

def test_first_run_writes_zero_delta_baseline(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text("")

    payload = _kernel_payload(events=10, original=5000, compressed=2800, token_savings=550)

    with patch("services.token_metrics.subprocess.run") as run, \
         patch("services.token_metrics._METRICS_PATH", metrics):
        run.return_value = _fake_completed(json.dumps(payload))
        baseline = token_metrics.record_compression_event()
        assert baseline is not None
        # Same counters again: nothing new
        assert token_metrics.record_compression_event() is None
        token_metrics.invalidate_compression_recorder_state()
        assert token_metrics.record_compression_event() is None

    rows = _lines(metrics)
    assert len(rows) == 1
    ev = rows[0]
    # Zero delta: the pre-existing session compression is already in the
    # all-time number and must not be attributed to today.
    assert ev["original"] == 0
    assert ev["compressed"] == 0
    assert ev["saved"] == 0
    assert ev["tokens_saved"] == 0
    assert ev["kernel_original_bytes_total"] == 5000


# ---------------------------------------------------------------------------
# Kernel session reset: counters went backwards, the new session's counters
# are recorded as the delta (all of it happened since the reset)
# ---------------------------------------------------------------------------

def test_session_reset_records_current_counters(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text(
        json.dumps(
            _marker_event(
                events=900, original=2000000, compressed=1100000, token_savings=220000
            )
        )
        + "\n"
    )

    with patch("services.token_metrics.subprocess.run") as run, \
         patch("services.token_metrics._METRICS_PATH", metrics):
        run.return_value = _fake_completed(
            json.dumps(
                _kernel_payload(
                    events=3, original=6000, compressed=4500, token_savings=380
                )
            )
        )
        appended = token_metrics.record_compression_event()

    assert appended is not None
    rows = _lines(metrics)
    assert len(rows) == 2
    ev = rows[-1]
    assert ev["original"] == 6000
    assert ev["compressed"] == 4500
    assert ev["tokens_saved"] == 380
    assert ev["kernel_original_bytes_total"] == 6000


def test_session_reset_to_zero_appends_nothing(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text(
        json.dumps(
            _marker_event(events=900, original=2000000, compressed=1100000)
        )
        + "\n"
    )

    with patch("services.token_metrics.subprocess.run") as run, \
         patch("services.token_metrics._METRICS_PATH", metrics):
        run.return_value = _fake_completed(
            json.dumps(
                _kernel_payload(events=0, original=0, compressed=0, token_savings=0)
            )
        )
        assert token_metrics.record_compression_event() is None

    assert len(_lines(metrics)) == 1


# ---------------------------------------------------------------------------
# Wiring: the chat-turn path triggers the recorder (throttled, off-thread)
# ---------------------------------------------------------------------------

def test_safe_record_chat_turn_triggers_recorder(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    with patch("services.token_metrics._METRICS_PATH", metrics), \
         patch.object(token_metrics, "maybe_record_compression") as maybe:
        token_metrics.safe_record_chat_turn(
            model="claude-sonnet-4",
            input_tokens=10,
            output_tokens=5,
            has_ostk_boot=False,
        )
    maybe.assert_called_once()


def test_maybe_record_compression_throttles(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text("")

    with patch("services.token_metrics.subprocess.run") as run, \
         patch("services.token_metrics._METRICS_PATH", metrics):
        run.return_value = _fake_completed(json.dumps(_kernel_payload()))
        thread = token_metrics.maybe_record_compression()
        assert thread is not None
        thread.join(timeout=5)
        # First eligible call wrote the baseline event
        assert len(_lines(metrics)) == 1
        # Within the throttle window nothing fires
        assert token_metrics.maybe_record_compression() is None
        assert len(_lines(metrics)) == 1


def test_maybe_record_compression_never_raises(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    with patch("services.token_metrics._METRICS_PATH", metrics), \
         patch.object(
             token_metrics, "record_compression_event",
             side_effect=RuntimeError("boom"),
         ):
        thread = token_metrics.maybe_record_compression()
        assert thread is not None, "first eligible call must start the thread"
        thread.join(timeout=5)
        assert not thread.is_alive(), "the recorder thread must finish"
    # The raising recorder must not have written anything.
    assert not metrics.exists() or metrics.read_text() == ""
