"""
Regression tests for →942: backend dies after merges/commits land on main.

Root cause: backend_watchdog.sh had two bugs:

1. Ownership-blind cleanup: cleanup() unconditionally removed PIDFILE on exit.
   If a successor watchdog (W2) had already overwritten PIDFILE with its own PID,
   W1's cleanup deleted W2's record — leaving W2 orphaned and invisible to the
   next dev-backend.sh run, which then started W3. Two concurrent watchdogs on
   the next backend crash both call restart_backend, both spawn dev-backend.sh,
   and the second kills the uvicorn the first just started (kill-and-replace).

2. No restart lock: restart_backend had no mutex. Two simultaneous watchdogs
   could both pass backend_pid_alive() (both read the same stale dead PID) and
   both spawn dev-backend.sh. The second dev-backend.sh then kills the uvicorn
   the first just started, creating a brief death that re-triggers both watchdogs
   — producing the 50-restart cascade seen in the watchdog log.

Fix: ownership-check cleanup (only remove PIDFILE if we own it) + atomic
RESTART_LOCK in restart_backend using the same O_CREAT|O_EXCL noclobber pattern
as the launcher lock fix from →934.

These tests mirror the structure of test_dev_backend_lock.py.
"""
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
WATCHDOG = REPO / "scripts" / "backend_watchdog.sh"


def _watchdog_text() -> str:
    return WATCHDOG.read_text()


# ---------------------------------------------------------------------------
# Static content checks
# ---------------------------------------------------------------------------


def test_restart_lock_variable_defined():
    """backend_watchdog.sh must define RESTART_LOCK."""
    assert "RESTART_LOCK=" in _watchdog_text(), (
        "backend_watchdog.sh must define RESTART_LOCK for concurrent-restart protection"
    )


def test_cleanup_checks_ownership():
    """cleanup() must guard both PIDFILE and RESTART_LOCK with an ownership check."""
    text = _watchdog_text()
    # The fix uses: [ "$(cat "$PIDFILE" ...)" = "$$" ] before rm
    assert '"$$"' in text or "= \"$$\"" in text, (
        "cleanup must compare file content to $$ before removing (ownership check)"
    )


def test_restart_lock_uses_noclobber():
    """restart_backend must acquire RESTART_LOCK with O_CREAT|O_EXCL (noclobber)."""
    text = _watchdog_text()
    # Both the RESTART_LOCK write AND noclobber must be in restart_backend
    restart_start = text.find("restart_backend()")
    restart_end = text.find("\n}", restart_start)
    assert restart_start != -1, "restart_backend function not found"
    body = text[restart_start:restart_end]
    assert "set -o noclobber" in body, (
        "restart_backend must use 'set -o noclobber' for atomic RESTART_LOCK acquisition"
    )
    assert "RESTART_LOCK" in body, (
        "restart_backend must reference RESTART_LOCK"
    )


def test_restart_backend_rechecks_after_lock():
    """restart_backend must re-check backend_pid_alive after acquiring the lock."""
    text = _watchdog_text()
    restart_start = text.find("restart_backend()")
    restart_end = text.find("\n}", restart_start)
    body = text[restart_start:restart_end]
    count = body.count("backend_pid_alive")
    assert count >= 2, (
        f"restart_backend must call backend_pid_alive at least twice "
        f"(before lock and after lock), found {count}"
    )


# ---------------------------------------------------------------------------
# Behavioural checks via bash subprocesses
# ---------------------------------------------------------------------------


def test_cleanup_does_not_remove_pidfile_owned_by_another_pid():
    """
    Simulate: W1 exits, but PIDFILE has W2's PID (W2 overwrote it).
    W1's cleanup must NOT remove PIDFILE.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        pidfile = f"{tmpdir}/watchdog.pid"
        restart_lock = f"{tmpdir}/restart.lock"

        script = f"""\
#!/usr/bin/env bash
set -u
PIDFILE="{pidfile}"
RESTART_LOCK="{restart_lock}"

cleanup() {{
    if [ -f "$PIDFILE" ] && [ "$(cat "$PIDFILE" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$PIDFILE" 2>/dev/null || true
    fi
    if [ -f "$RESTART_LOCK" ] && [ "$(cat "$RESTART_LOCK" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$RESTART_LOCK" 2>/dev/null || true
    fi
}}
trap cleanup EXIT

# Simulate: W2 has already overwritten PIDFILE with its own PID (99999)
echo 99999 > "$PIDFILE"
# This process (W1) does NOT own PIDFILE — its $$ != 99999.
# cleanup should leave PIDFILE intact.
exit 0
"""
        script_path = f"{tmpdir}/test_cleanup.sh"
        Path(script_path).write_text(script)
        Path(script_path).chmod(0o755)

        result = subprocess.run(["bash", script_path], capture_output=True, timeout=5)
        assert result.returncode == 0
        # PIDFILE must still exist with value 99999
        assert Path(pidfile).exists(), "cleanup must NOT remove PIDFILE it does not own"
        assert Path(pidfile).read_text().strip() == "99999", (
            "PIDFILE must still contain the successor watchdog's PID"
        )


def test_cleanup_removes_pidfile_when_owned():
    """
    When PIDFILE contains this process's own PID, cleanup must remove it.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        pidfile = f"{tmpdir}/watchdog.pid"
        restart_lock = f"{tmpdir}/restart.lock"

        script = f"""\
#!/usr/bin/env bash
set -u
PIDFILE="{pidfile}"
RESTART_LOCK="{restart_lock}"

cleanup() {{
    if [ -f "$PIDFILE" ] && [ "$(cat "$PIDFILE" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$PIDFILE" 2>/dev/null || true
    fi
}}
trap cleanup EXIT

echo $$ > "$PIDFILE"
exit 0
"""
        script_path = f"{tmpdir}/test_cleanup_own.sh"
        Path(script_path).write_text(script)
        Path(script_path).chmod(0o755)

        result = subprocess.run(["bash", script_path], capture_output=True, timeout=5)
        assert result.returncode == 0
        assert not Path(pidfile).exists(), (
            "cleanup MUST remove PIDFILE when it contains this process's own PID"
        )


def test_restart_lock_prevents_concurrent_spawns():
    """
    Two concurrent callers of restart_backend's lock section: only one
    should proceed past the lock at a time. The second must log 'skipping'.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        restart_lock = f"{tmpdir}/restart.lock"
        result_dir = f"{tmpdir}/results"
        Path(result_dir).mkdir()

        # Minimal script that replicates the restart_backend locking section
        script = f"""\
#!/usr/bin/env bash
set -u
RESTART_LOCK="{restart_lock}"
RESULT="{result_dir}/$$.txt"

if ! ( set -o noclobber; echo $$ > "$RESTART_LOCK" ) 2>/dev/null; then
    _rl_holder=$(cat "$RESTART_LOCK" 2>/dev/null || true)
    if [ -n "$_rl_holder" ] && kill -0 "$_rl_holder" 2>/dev/null; then
        echo "skipped" > "$RESULT"
        exit 0
    fi
    rm -f "$RESTART_LOCK" 2>/dev/null || true
    if ! ( set -o noclobber; echo $$ > "$RESTART_LOCK" ) 2>/dev/null; then
        echo "skipped" > "$RESULT"
        exit 0
    fi
fi

# We hold the lock: simulate restart work (0.2 s)
echo "spawned" > "$RESULT"
sleep 0.2
rm -f "$RESTART_LOCK" 2>/dev/null || true
"""
        script_path = f"{tmpdir}/lock_test.sh"
        Path(script_path).write_text(script)
        Path(script_path).chmod(0o755)

        procs = [subprocess.Popen(["bash", script_path]) for _ in range(4)]
        for p in procs:
            p.wait(timeout=10)

        results = [f.read_text().strip() for f in Path(result_dir).iterdir()]
        assert len(results) == 4, f"expected 4 result files, got {len(results)}"
        spawned = [r for r in results if r == "spawned"]
        skipped = [r for r in results if r == "skipped"]
        assert len(spawned) >= 1, "at least one caller must acquire the restart lock"
        assert len(skipped) >= 1, "at least one concurrent caller must be blocked"
        assert len(spawned) + len(skipped) == 4, f"unexpected results: {results}"


# ---------------------------------------------------------------------------
# →942 recurrence: startup dedup lock
# ---------------------------------------------------------------------------


def test_startup_lock_variable_defined():
    """backend_watchdog.sh must define STARTUP_LOCK for the dedup TOCTOU fix."""
    assert "STARTUP_LOCK=" in _watchdog_text(), (
        "backend_watchdog.sh must define STARTUP_LOCK to serialise concurrent startups"
    )


def test_dedup_guard_uses_startup_lock():
    """The dedup check must be wrapped in STARTUP_LOCK acquisition (noclobber)."""
    text = _watchdog_text()
    # Find the dedup block: it precedes the main loop and must contain both
    # STARTUP_LOCK and set -o noclobber before the PIDFILE read.
    assert "STARTUP_LOCK" in text, "STARTUP_LOCK must appear in backend_watchdog.sh"
    # noclobber must be used to acquire STARTUP_LOCK
    startup_lock_idx = text.find("STARTUP_LOCK")
    noclobber_idx = text.find("set -o noclobber", startup_lock_idx)
    assert noclobber_idx != -1, (
        "set -o noclobber must appear after STARTUP_LOCK is defined "
        "(to acquire the startup lock atomically)"
    )


def test_startup_lock_prevents_duplicate_watchdog_registration():
    """
    Simulates →942 recurrence: four watchdog processes start concurrently
    against a stale PIDFILE (dead PID 99999). With the startup lock, exactly
    one must register; the other three must exit as duplicates.

    Without the STARTUP_LOCK fix, all four would read the dead PID, all four
    would pass the kill -0 check, and all four would write their own PID —
    producing four live watchdogs confirmed by duplicate log lines at identical
    timestamps in /tmp/myos-backend-watchdog.log.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        pidfile = f"{tmpdir}/watchdog.pid"
        startup_lock = f"{tmpdir}/watchdog-startup.lock"
        result_dir = f"{tmpdir}/results"
        Path(result_dir).mkdir()

        # Pre-populate PIDFILE with a dead PID to simulate the stale-pid race.
        Path(pidfile).write_text("99999")

        script = f"""\
#!/usr/bin/env bash
set -u
PIDFILE="{pidfile}"
STARTUP_LOCK="{startup_lock}"
RESULT="{result_dir}/$$.txt"

_sl_waited=0
while ! ( set -o noclobber; echo $$ > "$STARTUP_LOCK" ) 2>/dev/null; do
    _sl_holder=$(cat "$STARTUP_LOCK" 2>/dev/null || true)
    if [ -n "$_sl_holder" ] && kill -0 "$_sl_holder" 2>/dev/null; then
        if [ "$_sl_waited" -ge 5 ]; then
            echo "exited_timeout" > "$RESULT"
            exit 0
        fi
        sleep 1
        _sl_waited=$((_sl_waited + 1))
    else
        rm -f "$STARTUP_LOCK" 2>/dev/null || true
    fi
done

if [ -f "$PIDFILE" ]; then
    _existing_wd=$(cat "$PIDFILE" 2>/dev/null || true)
    if [ -n "$_existing_wd" ] && [ "$_existing_wd" != "$$" ] && kill -0 "$_existing_wd" 2>/dev/null; then
        echo "exited_duplicate" > "$RESULT"
        rm -f "$STARTUP_LOCK" 2>/dev/null || true
        exit 0
    fi
fi
echo $$ > "$PIDFILE"
rm -f "$STARTUP_LOCK" 2>/dev/null || true
echo "registered" > "$RESULT"
# Stay alive long enough for all 3 competing processes to serialize
# through STARTUP_LOCK and see a live PID via kill -0.
sleep 5
"""
        script_path = f"{tmpdir}/dedup_test.sh"
        Path(script_path).write_text(script)
        Path(script_path).chmod(0o755)

        procs = [subprocess.Popen(["bash", script_path]) for _ in range(4)]
        for p in procs:
            p.wait(timeout=30)

        results = [f.read_text().strip() for f in Path(result_dir).iterdir()]
        assert len(results) == 4, f"expected 4 result files, got {len(results)}"
        registered = [r for r in results if r == "registered"]
        exited = [r for r in results if r != "registered"]
        assert len(registered) == 1, (
            f"exactly one watchdog must register (startup lock should block others); "
            f"got {len(registered)} registrations: {results}"
        )
        assert len(exited) == 3, (
            f"other 3 must exit as duplicates; got: {exited}"
        )
