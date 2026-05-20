"""
Tests for the ostk daemon saturation watchdog (→1527).

Root cause: daemon-6.0.5 leaks /dev/ptmx fds (PTY masters) per shell
command. After 6+ days: 124 fds open, tokio/kqueue busy-loops on EOF
from dead PTY masters → 80%+ CPU.

The watchdog (scripts/ostk_daemon_watchdog.sh) detects:
1. PTY fd count > threshold → restart
2. Running binary inode != installed binary inode → stale binary, restart
"""

import os
import re
import stat
import subprocess
import pytest

_SCRIPTS = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
)
WATCHDOG = os.path.join(_SCRIPTS, "ostk_daemon_watchdog.sh")
BASH_TESTS = os.path.join(_SCRIPTS, "ostk_daemon_watchdog_test.sh")


def test_watchdog_script_exists():
    assert os.path.isfile(WATCHDOG), f"Missing: {WATCHDOG}"


def test_watchdog_script_is_executable():
    assert os.path.isfile(WATCHDOG)
    assert os.stat(WATCHDOG).st_mode & stat.S_IXUSR, f"Not executable: {WATCHDOG}"


def test_watchdog_dry_run_exits_zero():
    result = subprocess.run(
        ["bash", WATCHDOG, "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"--dry-run exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_watchdog_dry_run_produces_status_output():
    result = subprocess.run(
        ["bash", WATCHDOG, "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    combined = result.stdout + result.stderr
    markers = ("OK: pid", "CRITICAL:", "STALE:", "WARN:", "No ostk daemon", "Scan complete")
    assert any(m in combined for m in markers), (
        f"Expected one of {markers} in output:\n{combined}"
    )


def test_watchdog_pty_warn_threshold_default_is_sane():
    with open(WATCHDOG) as f:
        src = f.read()
    match = re.search(r"PTY_WARN[^}]*:-(\d+)", src)
    assert match, "PTY_WARN threshold default not found in script"
    assert int(match.group(1)) >= 10, f"PTY_WARN default too low: {match.group(1)}"


def test_watchdog_pty_critical_threshold_default_is_sane():
    with open(WATCHDOG) as f:
        src = f.read()
    match = re.search(r"PTY_CRITICAL[^}]*:-(\d+)", src)
    assert match, "PTY_CRITICAL threshold default not found in script"
    assert int(match.group(1)) >= 20, f"PTY_CRITICAL default too low: {match.group(1)}"


def test_watchdog_critical_threshold_exceeds_warn_threshold():
    with open(WATCHDOG) as f:
        src = f.read()
    warn = re.search(r"PTY_WARN[^}]*:-(\d+)", src)
    critical = re.search(r"PTY_CRITICAL[^}]*:-(\d+)", src)
    assert warn and critical
    assert int(critical.group(1)) > int(warn.group(1)), (
        "Critical threshold must exceed warn threshold"
    )


def test_watchdog_dry_run_never_calls_daemon_restart():
    """--dry-run must not invoke 'ostk daemon restart'."""
    result = subprocess.run(
        ["bash", WATCHDOG, "--dry-run"],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "OSTK_DAEMON_PTY_CRITICAL_THRESHOLD": "1"},
    )
    combined = result.stdout + result.stderr
    assert "ostk daemon restart" not in combined or "DRY-RUN" in combined, (
        "Dry-run mode must not run restart without DRY-RUN label"
    )


def test_bash_self_tests_pass():
    if not os.path.isfile(BASH_TESTS):
        pytest.skip(f"Bash test suite not found: {BASH_TESTS}")
    result = subprocess.run(
        ["bash", BASH_TESTS],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"Bash tests failed:\n{result.stdout}\n{result.stderr}"
    )
