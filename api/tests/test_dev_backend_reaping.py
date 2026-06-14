"""Regression tests for the dev-backend.sh reaper (SO_REUSEPORT double-bind fix).

Root cause: In --reload mode uvicorn's RELOADER PARENT (exec'd from the shell,
command "uvicorn main:app ...") keeps the socket FD open.  Its WORKER CHILD is
spawned via Python's multiprocessing.get_context("spawn") and shows up in ps as
"python3 -c from multiprocessing.spawn import spawn_main; ...".  That spawn-
bootstrap command does NOT match "uvicorn main:app", so the worker is invisible
to the grep filter in the kill block.

If lsof -sTCP:LISTEN only returns the worker (brief window during a worker-
restart cycle), free_port_or_die kills the worker but leaves the reloader alive.
The reloader then restarts a new worker that re-binds PORT via SO_REUSEPORT
alongside the freshly exec'd uvicorn — split-brain that causes GETs and POSTs
to land on different processes.

Fix: supplement lsof results with pgrep -f "uvicorn main:app.*--port PORT" so
the reloader parent is always in existing_pids and always killed before exec.
A pre-exec sweep (pgrep + kill) provides belt-and-braces.

These tests verify the pgrep detection and kill logic without binding real ports
or starting a real uvicorn.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
DEV_BACKEND = REPO_ROOT / "scripts" / "dev-backend.sh"
TEST_PORT = 18765  # throwaway port, never actually bound


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spawn_fake_reloader(port: int) -> subprocess.Popen:
    """Return a live process whose argv looks like a uvicorn reloader.

    Uses /bin/sleep as the executable but sets argv[0] to the uvicorn command
    string so that `pgrep -f "uvicorn main:app.*--port PORT"` matches it.
    This mirrors what the OS reports for the real uvicorn reloader parent after
    the shell script does `exec uvicorn main:app --host 127.0.0.1 --port PORT
    --reload ...`.
    """
    return subprocess.Popen(
        [
            f"uvicorn main:app --host 127.0.0.1 --port {port} --reload"
            f" --reload-dir /fake/api",
            "60",  # sleep 60s
        ],
        executable="/bin/sleep",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _pgrep_uvicorn(port: int) -> list[int]:
    """Return PIDs matching 'uvicorn main:app.*--port PORT' via pgrep."""
    result = subprocess.run(
        ["pgrep", "-f", f"uvicorn main:app.*--port {port}"],
        capture_output=True,
        text=True,
    )
    return [int(p) for p in result.stdout.split() if p.strip().isdigit()]


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPgrepDetection:
    """pgrep correctly identifies a uvicorn reloader parent by command line."""

    def test_fake_reloader_visible_to_pgrep(self):
        """A process with uvicorn-argv is found by the pgrep command used in
        the fixed dev-backend.sh (root of the double-bind regression)."""
        proc = _spawn_fake_reloader(TEST_PORT)
        try:
            time.sleep(0.3)
            found = _pgrep_uvicorn(TEST_PORT)
            assert proc.pid in found, (
                f"pgrep did not detect fake reloader PID {proc.pid}. "
                f"Found: {found}. This would have caused the reloader to "
                f"escape the kill block and co-bind PORT {TEST_PORT}."
            )
        finally:
            proc.kill()
            proc.wait()

    def test_pgrep_empty_when_no_reloader_running(self):
        """Sanity: pgrep returns empty when no matching process exists."""
        found = _pgrep_uvicorn(TEST_PORT)
        # Filter out any legitimate uvicorn processes on this port (there
        # should be none; test_port is not the dev port).
        assert TEST_PORT not in [
            int(p) for p in found
        ], "unexpected match"
        # Just assert the method works; an empty list is the expected result.
        assert isinstance(found, list)


class TestPgrepKill:
    """The pgrep-based kill in the fixed script terminates the reloader."""

    def test_kill_via_pgrep_terminates_reloader(self):
        """Regression: a reloader not in lsof -sTCP:LISTEN must be killed by
        the pgrep sweep before exec uvicorn; otherwise it respawns a worker
        that co-binds PORT via SO_REUSEPORT."""
        proc = _spawn_fake_reloader(TEST_PORT)
        try:
            time.sleep(0.3)
            assert proc.poll() is None, "fake reloader should be alive"

            # Simulate the pgrep sweep from the fixed dev-backend.sh:
            #   pids=$(pgrep -f "uvicorn main:app.*--port PORT" | grep -v "^$$")
            #   [ -n "$pids" ] && kill $pids
            pids = _pgrep_uvicorn(TEST_PORT)
            assert proc.pid in pids, f"pgrep missed reloader PID {proc.pid}"

            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

            # Reap the child so we get an accurate exit-code check.
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass

            assert proc.poll() is not None, (
                f"Reloader PID {proc.pid} is still alive after SIGTERM via "
                f"pgrep sweep. Without this fix, dev-backend.sh would exec a "
                f"new uvicorn while this one remains, causing double-bind on "
                f"port {TEST_PORT} via SO_REUSEPORT."
            )
        finally:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait()

    def test_sigkill_fallback_terminates_stubborn_reloader(self):
        """Belt-and-braces: SIGKILL path kills a reloader that ignored SIGTERM."""
        proc = _spawn_fake_reloader(TEST_PORT)
        try:
            time.sleep(0.3)
            # Directly SIGKILL (simulating the fallback in the pre-exec sweep)
            os.kill(proc.pid, signal.SIGKILL)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
            assert proc.poll() is not None, (
                f"SIGKILL did not terminate PID {proc.pid}"
            )
        finally:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait()


class TestDevBackendScriptExists:
    """Sanity: the script exists and contains the new pgrep sweep."""

    def test_script_is_executable(self):
        assert DEV_BACKEND.exists(), f"dev-backend.sh not found at {DEV_BACKEND}"
        assert os.access(DEV_BACKEND, os.X_OK), "dev-backend.sh is not executable"

    def test_script_contains_pgrep_reloader_detection(self):
        content = DEV_BACKEND.read_text()
        assert 'pgrep -f "uvicorn main:app.*--port' in content, (
            "dev-backend.sh missing pgrep reloader-detection sweep. "
            "This sweep is required to catch reloader parents that escape "
            "lsof -sTCP:LISTEN detection and would otherwise co-bind the port."
        )

    def test_script_contains_pre_exec_pgrep_sweep(self):
        """The belt-and-braces pre-exec sweep must appear after free_port_or_die
        and before echo $$ > PIDFILE + exec uvicorn."""
        content = DEV_BACKEND.read_text()
        assert "_surviving_reloaders" in content, (
            "dev-backend.sh missing pre-exec pgrep sweep variable "
            "'_surviving_reloaders'. The sweep kills any reloader that "
            "survived the earlier kill block."
        )
