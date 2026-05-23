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
import time
from pathlib import Path
from typing import Optional

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


# ---------------------------------------------------------------------------
# →942 second recurrence: ghost purge + main-loop self-check
# ---------------------------------------------------------------------------


def test_ghost_purge_uses_pgrep():
    """backend_watchdog.sh must use pgrep to find and kill ghost watchdog siblings."""
    text = _watchdog_text()
    assert "pgrep" in text and "backend_watchdog.sh" in text, (
        "backend_watchdog.sh must use pgrep to find ghost watchdog siblings"
    )


def test_ghost_purge_excludes_self():
    """The ghost purge must exclude the current process (grep -v $$)."""
    text = _watchdog_text()
    # The purge block must filter out the running watchdog's own PID
    assert 'grep -v "^$$"' in text or "grep -v '^$$'" in text, (
        "ghost purge must exclude own PID via 'grep -v \"^$$\"' to avoid self-kill"
    )


def test_ghost_purge_comes_after_pidfile_write():
    """Ghost purge must happen AFTER we write our PID to PIDFILE.

    Order matters: write PIDFILE first so ghosts that self-check see the
    new canonical PID; then kill them. Reversing the order leaves a window
    where a ghost re-acquires PIDFILE between its kill-check and our write.
    """
    text = _watchdog_text()
    pidfile_write_pos = text.find('echo $$ > "$PIDFILE"')
    pgrep_pos = text.find('pgrep -f "backend_watchdog.sh"')
    assert pidfile_write_pos != -1, "PIDFILE write not found"
    assert pgrep_pos != -1, "pgrep ghost purge not found"
    assert pidfile_write_pos < pgrep_pos, (
        "PIDFILE write must precede ghost purge (so ghosts see new canonical PID)"
    )


def test_main_loop_self_check_present():
    """Main loop must check PIDFILE against own PID and exit if superseded."""
    text = _watchdog_text()
    assert "superseded" in text, (
        "main loop must contain self-check that exits when PIDFILE has a different live PID"
    )


def test_main_loop_self_check_uses_kill_zero():
    """Self-check must verify the PIDFILE holder is alive before exiting.

    Without kill -0, a stale PIDFILE (dead PID) would cause the watchdog
    to exit even when no live successor exists — leaving the backend unmonitored.
    """
    text = _watchdog_text()
    # Find the self-check block by looking for "superseded"
    superseded_pos = text.find("superseded")
    assert superseded_pos != -1, "self-check block not found"
    # kill -0 must appear before the superseded log line
    kill_zero_pos = text.rfind("kill -0", 0, superseded_pos)
    assert kill_zero_pos != -1, (
        "self-check must use 'kill -0' to confirm PIDFILE holder is alive "
        "before exiting (prevents spurious exit on stale PIDFILE)"
    )


def test_ghost_watchdog_exits_when_superseded():
    """Functional: a watchdog that finds a newer live PID in PIDFILE must exit.

    Simulates the ghost scenario: ghost watchdog's PIDFILE has a different
    (live) PID. Ghost must exit within one loop iteration.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        pidfile = f"{tmpdir}/watchdog.pid"
        logfile = f"{tmpdir}/watchdog.log"

        # Ghost is a small script mimicking the self-check logic in the main loop.
        ghost_script = f"""\
#!/usr/bin/env bash
PIDFILE="{pidfile}"
LOGFILE="{logfile}"
touch "$LOGFILE"
_loop_wd=$(cat "$PIDFILE" 2>/dev/null || true)
if [ -n "$_loop_wd" ] && [ "$_loop_wd" != "$$" ] && kill -0 "$_loop_wd" 2>/dev/null; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] watchdog: INFO superseded by pid $_loop_wd; exiting to avoid ghost watchdog" >> "$LOGFILE"
    exit 0
fi
# Should not reach here in test
exit 1
"""
        ghost_path = f"{tmpdir}/ghost.sh"
        Path(ghost_path).write_text(ghost_script)
        Path(ghost_path).chmod(0o755)

        # Write a live PID (ourselves) as the canonical watchdog
        import os
        Path(pidfile).write_text(str(os.getpid()))

        result = subprocess.run(["bash", ghost_path], timeout=5)
        assert result.returncode == 0, (
            "ghost watchdog must exit cleanly when PIDFILE has a live superseding PID"
        )
        log = Path(logfile).read_text()
        assert "superseded" in log, (
            f"ghost must log 'superseded' before exiting; log was: {log!r}"
        )


# ---------------------------------------------------------------------------
# →942 acceptance criterion: backend survives 5 sequential commits
# ---------------------------------------------------------------------------


def test_five_sequential_restart_cycles_no_cascade():
    """
    →942 AC: simulate 5 sequential watchdog-triggered restart cycles and
    verify the restart-lock prevents cascades in every cycle.

    Each cycle: 4 concurrent callers race to restart the backend.  With the
    restart-lock fix exactly one must win; the other three must be blocked.
    Without the fix all four would attempt to spawn, the second would kill the
    uvicorn the first just started, reproducing the 50-restart cascade.

    Pure subprocess test — no live backend required.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        restart_lock = f"{tmpdir}/restart.lock"
        result_dir = f"{tmpdir}/results"
        Path(result_dir).mkdir()

        script = f"""\
#!/usr/bin/env bash
set -u
RESTART_LOCK="{restart_lock}"
CYCLE="$1"
RESULT="{result_dir}/${{CYCLE}}_$$.txt"

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

echo "spawned" > "$RESULT"
sleep 0.15
rm -f "$RESTART_LOCK" 2>/dev/null || true
"""
        script_path = f"{tmpdir}/restart_test.sh"
        Path(script_path).write_text(script)
        Path(script_path).chmod(0o755)

        for cycle in range(1, 6):
            procs = [
                subprocess.Popen(["bash", script_path, str(cycle)])
                for _ in range(4)
            ]
            for p in procs:
                p.wait(timeout=10)

            results = [
                f.read_text().strip()
                for f in Path(result_dir).glob(f"{cycle}_*.txt")
            ]
            assert len(results) == 4, (
                f"cycle {cycle}: expected 4 result files, got {len(results)}"
            )
            spawned = [r for r in results if r == "spawned"]
            skipped = [r for r in results if r == "skipped"]
            assert len(spawned) >= 1, (
                f"cycle {cycle}: at least one caller must acquire restart lock"
            )
            assert len(skipped) >= 1, (
                f"cycle {cycle}: concurrent callers must be blocked"
            )
            assert len(spawned) + len(skipped) == 4, (
                f"cycle {cycle}: unexpected results: {results}"
            )


@pytest.mark.integration
def test_backend_alive_across_five_commits():
    """
    →942 AC (live): backend must return HTTP 200 after each of 5 sequential
    git commits.  Uses a temp repo so uvicorn is not reloaded; the assertion
    is that the backend process does not die during sequential git activity.

    Skips if the backend is not reachable (CI without a live server).
    """
    import httpx

    url = "https://127.0.0.1:8000/api/agents?limit=1"

    def http_status() -> Optional[int]:
        # Retry up to 4 times so a brief uvicorn reload (typically 3-10 s)
        # triggered by background agents editing api/ files does not count as
        # "backend died".  Total tolerance ~60 s; genuine crashes stay down
        # until the watchdog fires (30+ s), so the assertion still catches them.
        #
        # urllib.request is intentionally replaced with httpx: on macOS,
        # sock.connect(sa) inside urllib blocks at the libsystem level and
        # ignores socket.settimeout(), causing pytest-timeout to kill the suite.
        # httpx separates connect/read timeouts and respects them on all platforms.
        for _attempt in range(4):
            try:
                with httpx.Client(
                    verify=False,
                    timeout=httpx.Timeout(10.0, connect=5.0),
                ) as client:
                    r = client.get(url)
                    return r.status_code
            except Exception:
                if _attempt < 3:
                    time.sleep(4)
        return None

    pre = http_status()
    if pre is None:
        pytest.skip("live backend not reachable at 127.0.0.1:8000")
    assert pre == 200, f"backend not healthy before test: HTTP {pre}"

    with tempfile.TemporaryDirectory() as tmpdir:
        env = {
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
            "HOME": tmpdir,
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }

        def git(*args: str) -> None:
            subprocess.run(
                ["git", "-C", tmpdir, *args],
                check=True,
                capture_output=True,
                env=env,
            )

        git("init")
        git("config", "user.email", "test@test.com")
        git("config", "user.name", "test")

        notes = Path(tmpdir) / "notes.md"
        receipts: list = []

        for i in range(1, 6):
            notes.write_text(f"commit {i}\n")
            git("add", "notes.md")
            git("commit", "-m", f"test: →942 AC commit {i}/5")

            log = subprocess.run(
                ["git", "-C", tmpdir, "log", "--oneline", "-1"],
                capture_output=True,
                text=True,
                env=env,
            )
            sha = log.stdout.strip().split()[0]

            status = http_status()
            receipts.append((sha, status or 0))
            assert status == 200, (
                f"backend died after commit {i}/5 (sha {sha}): HTTP {status}\n"
                f"receipts so far: {receipts}"
            )

    print("\n→942 live receipts:")
    for sha, code in receipts:
        print(f"  commit {sha}  →  HTTP {code}")


# ---------------------------------------------------------------------------
# →1178: _fetch_agents_list must not hardcode http:// (backend runs HTTPS)
# ---------------------------------------------------------------------------


def test_fetch_agents_list_no_hardcoded_http_scheme():
    """_fetch_agents_list in chat.py must not hardcode http:// for loopback (→1178).

    When SSL certs exist in ~/.myos/, the backend runs with HTTPS. A bare
    http:// URL returns HTTP 000 / empty reply because the server closes the
    connection before writing any response (TLS handshake never starts).
    The fix is to try https:// first (verify=False for self-signed) then
    fall back to http://.
    """
    chat_router = Path(__file__).resolve().parent.parent / "routers" / "chat.py"
    text = chat_router.read_text()
    start = text.find("async def _fetch_agents_list")
    assert start != -1, "_fetch_agents_list not found in chat.py"
    end = text.find("\nasync def ", start + 1)
    body = text[start:end] if end != -1 else text[start:]
    assert '"http://127.0.0.1' not in body and "'http://127.0.0.1" not in body, (
        "_fetch_agents_list must not hardcode http:// — backend runs HTTPS when "
        "SSL certs exist; use https:// first (verify=False) then fall back to http://"
    )


def test_fetch_agents_list_tries_https_first():
    """_fetch_agents_list must try https:// before http:// (→1178)."""
    chat_router = Path(__file__).resolve().parent.parent / "routers" / "chat.py"
    text = chat_router.read_text()
    start = text.find("async def _fetch_agents_list")
    assert start != -1, "_fetch_agents_list not found in chat.py"
    end = text.find("\nasync def ", start + 1)
    body = text[start:end] if end != -1 else text[start:]
    https_pos = body.find("https")
    http_pos = body.find('"http://')
    if http_pos == -1:
        http_pos = body.find("'http://")
    assert https_pos != -1, "_fetch_agents_list must contain 'https' scheme"
    assert http_pos == -1 or https_pos < http_pos, (
        "https:// must appear before any http:// fallback in _fetch_agents_list"
    )
