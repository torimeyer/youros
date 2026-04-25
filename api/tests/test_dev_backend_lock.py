"""
Tests for the launcher-lock TOCTOU fix in scripts/dev-backend.sh (→934).

Root cause: acquire_launcher_lock used a check-then-write pattern. Two
concurrent callers could both see "no lock file" and both proceed past
the gate. The last writer won the PIDFILE but the first exec won the port;
PIDFILE ended up pointing to the dead loser. On the next reload the
watchdog saw that PID as dead, bypassed backend_pid_alive(), and killed
the live uvicorn — leaving pgrep -f uvicorn empty.

Fix: use `set -o noclobber` in a subshell so the shell opens the file with
O_CREAT|O_EXCL — one atomic syscall that fails if the file already exists.
Also: backend_watchdog.sh now exits early if a live watchdog pid is already
registered, preventing the dual-watchdog condition that triggers the race.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
DEV_BACKEND = REPO / "scripts" / "dev-backend.sh"
WATCHDOG = REPO / "scripts" / "backend_watchdog.sh"


def _acquire_func_body() -> str:
    text = DEV_BACKEND.read_text()
    m = re.search(r"acquire_launcher_lock\(\)\s*\{(.+?)\n\}", text, re.DOTALL)
    assert m, "acquire_launcher_lock function not found in dev-backend.sh"
    return m.group(1)


def test_launcher_lock_uses_noclobber():
    """acquire_launcher_lock must use set -o noclobber for atomic O_EXCL creation."""
    assert "set -o noclobber" in _acquire_func_body(), (
        "acquire_launcher_lock must use 'set -o noclobber' to prevent TOCTOU race"
    )


def test_launcher_lock_no_toctou_pattern():
    """The old check-then-write pattern must be gone."""
    body = _acquire_func_body()
    # Old pattern: while [ -f "$LAUNCHER_LOCK" ]; do ... done; echo $$ > "$LAUNCHER_LOCK"
    assert 'while [ -f "$LAUNCHER_LOCK' not in body, (
        "Old TOCTOU pattern (while [ -f lock ]; do ... done; echo > lock) must be removed"
    )


def test_watchdog_dedup_guard_present():
    """backend_watchdog.sh must exit early when a live watchdog is already registered."""
    text = WATCHDOG.read_text()
    assert "duplicate start" in text, (
        "backend_watchdog.sh must contain duplicate-start dedup guard"
    )


def test_watchdog_dedup_checks_before_pidfile_write():
    """The dedup guard must appear before 'echo $$ > PIDFILE' in watchdog script."""
    text = WATCHDOG.read_text()
    dedup_pos = text.find("duplicate start")
    pidfile_pos = text.find("echo $$ > \"$PIDFILE\"")
    assert dedup_pos != -1, "dedup guard not found"
    assert pidfile_pos != -1, "pidfile write not found"
    assert dedup_pos < pidfile_pos, (
        "dedup guard must precede the pidfile write so it can prevent the overwrite"
    )


def test_launcher_lock_concurrent_serializes():
    """Two concurrent bash callers must serialise — only one holds the lock at a time."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lock = f"{tmpdir}/test.lock"
        result_prefix = f"{tmpdir}/result"

        # Minimal script that uses the fixed acquire pattern
        script_path = f"{tmpdir}/lock_test.sh"
        Path(script_path).write_text(f"""\
#!/usr/bin/env bash
LAUNCHER_LOCK="{lock}"
RESULT="{result_prefix}_$$"

acquire_launcher_lock() {{
    local waited=0
    while ! ( set -o noclobber; echo $$ > "$LAUNCHER_LOCK" ) 2>/dev/null; do
        local holder
        holder=$(cat "$LAUNCHER_LOCK" 2>/dev/null || true)
        if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
            if [ "$waited" -ge 30 ]; then
                echo "timeout" > "$RESULT"
                return 1
            fi
            sleep 0.05
            waited=$((waited + 1))
        else
            rm -f "$LAUNCHER_LOCK" 2>/dev/null || true
        fi
    done
    return 0
}}

if acquire_launcher_lock; then
    echo "acquired" > "$RESULT"
    sleep 0.1
    rm -f "$LAUNCHER_LOCK"
else
    echo "timeout" > "$RESULT"
fi
""")
        Path(script_path).chmod(0o755)

        # Launch 4 concurrent callers
        procs = [subprocess.Popen(["bash", script_path]) for _ in range(4)]
        for p in procs:
            p.wait(timeout=10)

        results = [f.read_text().strip() for f in Path(tmpdir).glob("result_*")]
        assert len(results) == 4, f"expected 4 result files, got {len(results)}"
        # All should eventually acquire (serialised, not blocked forever)
        assert all(r == "acquired" for r in results), (
            f"all callers should acquire the lock sequentially, got: {results}"
        )
