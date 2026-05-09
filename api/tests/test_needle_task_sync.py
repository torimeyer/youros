# Tests for →1069: needle-close task sync hook
import os
import re
import stat
import subprocess
import tempfile
import textwrap
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent.parent / ".githooks" / "post-commit"


def _hook_source() -> str:
    return HOOK_PATH.read_text()


def test_post_commit_hook_curl_format():
    """The hook must contain a curl call with --connect-timeout and /api/tasks/."""
    src = _hook_source()
    assert "--connect-timeout" in src, "hook must use --connect-timeout to avoid hanging commits"
    assert "/api/tasks/" in src, "hook must POST to /api/tasks/<needle>/close"
    # Must also suppress errors so a dead backend never blocks a commit
    assert ">/dev/null 2>&1 || true" in src or "2>&1 || true" in src, (
        "hook must silence errors and always succeed"
    )


def test_post_commit_hook_exits_zero_on_backend_down(tmp_path):
    """Running the hook with no backend should still exit 0."""
    # Build a tiny throwaway git repo so git log works
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "feat: →9999 close this needle"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com"},
    )
    # Copy the hook into the repo
    hook_dst = repo / ".git" / "hooks" / "post-commit"
    hook_dst.write_text(_hook_source())
    hook_dst.chmod(hook_dst.stat().st_mode | stat.S_IEXEC)

    # Stub out ostk so the hook thinks the close succeeded
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    ostk_stub = stub_bin / "ostk"
    ostk_stub.write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        # stub: echo "closed" so the hook's grep -q "closed" passes
        echo "closed →9999"
        exit 0
    """))
    ostk_stub.chmod(ostk_stub.stat().st_mode | stat.S_IEXEC)

    env = {**os.environ, "PATH": f"{stub_bin}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(hook_dst)],
        cwd=repo,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"hook must exit 0 even when backend is down; got {result.returncode}\n"
        f"stdout: {result.stdout.decode()}\nstderr: {result.stderr.decode()}"
    )
