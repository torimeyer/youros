"""Prove the pytest-orphan prevention measures are in place and work.

Verifies:
  1. pytest-timeout is installed and active (signal method).
  2. A deliberately slow test is killed before it can run indefinitely.
  3. The reaper script (kill-pytest-orphans.sh) is executable and runs cleanly.
  4. worktree-gate.sh and e2e_smoke.sh carry --timeout flags.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PY = REPO_ROOT / "api" / ".venv" / "bin" / "python3.11"


def _venv_py() -> Path:
    """Return venv python, skip if not found."""
    if not VENV_PY.exists():
        import pytest
        pytest.skip(f"venv not found at {VENV_PY}")
    return VENV_PY


def test_pytest_timeout_plugin_installed():
    """pytest-timeout must be importable — not just declared in requirements."""
    import importlib
    spec = importlib.util.find_spec("pytest_timeout")
    assert spec is not None, (
        "pytest-timeout is not installed. "
        "Run: api/.venv/bin/pip install pytest-timeout>=2.4.0"
    )


def test_timeout_method_is_signal():
    """pyproject.toml must use signal (not thread) so hanging tests are actually killed."""
    pyproject = REPO_ROOT / "api" / "pyproject.toml"
    text = pyproject.read_text()
    assert 'timeout_method = "signal"' in text, (
        "api/pyproject.toml must set timeout_method = \"signal\". "
        "'thread' cannot kill blocked subprocesses."
    )


def test_timeout_value_is_set():
    """A non-zero timeout must be configured globally."""
    pyproject = REPO_ROOT / "api" / "pyproject.toml"
    import re
    m = re.search(r'^timeout\s*=\s*(\d+)', pyproject.read_text(), re.MULTILINE)
    assert m is not None, "api/pyproject.toml must have a timeout = N line"
    assert int(m.group(1)) > 0, "timeout must be > 0"


def test_slow_test_is_killed_by_timeout():
    """A test that sleeps 300s must be killed within the configured timeout."""
    py = _venv_py()
    slow_test = textwrap.dedent("""\
        import time
        def test_hangs():
            time.sleep(300)  # would run 5 minutes if no timeout
    """)
    tmp = Path("/tmp/test_slow_orphan_proof.py")
    tmp.write_text(slow_test)
    try:
        result = subprocess.run(
            [str(py), "-m", "pytest", str(tmp), "-x", "-q",
             "--timeout=3", "--timeout-method=signal", "--no-header"],
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT / "api"),
        )
        combined = result.stdout + result.stderr
        # pytest-timeout marks the test as FAILED with a timeout error
        assert result.returncode != 0, "Expected non-zero exit; slow test should have timed out"
        assert "timeout" in combined.lower(), (
            f"Expected 'timeout' in output; got:\n{combined}"
        )
    finally:
        tmp.unlink(missing_ok=True)


def test_reaper_script_exists_and_is_executable():
    """The kill-pytest-orphans.sh reaper must exist and be executable."""
    reaper = REPO_ROOT / "scripts" / "kill-pytest-orphans.sh"
    assert reaper.exists(), f"Reaper missing: {reaper}"
    assert os.access(reaper, os.X_OK), f"Reaper not executable: {reaper}"


def test_reaper_dry_run_exits_cleanly():
    """Dry-run of the reaper must complete without error."""
    reaper = REPO_ROOT / "scripts" / "kill-pytest-orphans.sh"
    result = subprocess.run(
        [str(reaper)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, (
        f"Reaper dry-run failed (exit {result.returncode}):\n{result.stderr}"
    )
    assert "DRY RUN" in result.stdout, "Expected 'DRY RUN' in reaper output"


def test_worktree_gate_has_timeout_flag():
    """worktree-gate.sh must pass --timeout to pytest to bound full-suite runs."""
    gate = REPO_ROOT / "scripts" / "worktree-gate.sh"
    text = gate.read_text()
    assert "--timeout=" in text, (
        "scripts/worktree-gate.sh must include --timeout=N in the pytest invocation. "
        "Without it the full suite can hang forever."
    )


def test_e2e_smoke_has_timeout_flag():
    """e2e_smoke.sh must pass --timeout to pytest."""
    smoke = REPO_ROOT / "scripts" / "e2e_smoke.sh"
    text = smoke.read_text()
    # Check that the pytest invocation line includes --timeout
    pytest_lines = [l for l in text.splitlines() if "pytest" in l and "--timeout" in l]
    assert pytest_lines, (
        "scripts/e2e_smoke.sh must include --timeout=N in the pytest invocation. "
        "Without it the full suite can hang forever."
    )
