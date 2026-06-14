"""Upgrade check service.

Checks whether myOS or ostk have newer versions available.
Results are cached in ~/.youros/upgrade_cache.json for 1 hour to avoid
hammering GitHub on every page load.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx

from config import PROJECT_ROOT
from services.atomic_io import atomic_write_json
from services.youros_paths import youros_home

MYOS_DIR = youros_home()
UPGRADE_CACHE_FILE = MYOS_DIR / "upgrade_cache.json"
CACHE_TTL_SECONDS = 3600  # 1 hour

OSTK_REPO = "os-tack/ostk.ai"


def _semver_tuple(version: str) -> tuple[int, ...]:
    """Convert a semver string like '2.4.0' to a comparable tuple."""
    parts = re.split(r"[.\-]", version.lstrip("v"))
    result = []
    for p in parts[:3]:
        try:
            result.append(int(p))
        except ValueError:
            break
    while len(result) < 3:
        result.append(0)
    return tuple(result)


def _load_cache() -> Optional[dict]:
    if not UPGRADE_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(UPGRADE_CACHE_FILE.read_text())
        cached_at = datetime.fromisoformat(data.get("cached_at", ""))
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return data.get("result")
    except (json.JSONDecodeError, ValueError, OSError, KeyError):
        pass
    return None


def _save_cache(result: dict) -> None:
    payload = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }
    atomic_write_json(UPGRADE_CACHE_FILE, payload)


def _invalidate_cache() -> None:
    try:
        UPGRADE_CACHE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _humanize_git_describe(raw: str) -> str:
    """Turn 'v3.0-66-gff90e7e' into 'v3.0.66' for display.

    If HEAD is exactly on a tag the --long format is 'v3.0-0-gabcdef';
    in that case just return the tag itself ('v3.0').

    If the nearest tag is not a semver-looking tag (for example an
    internal marker like 'pre-dry-run'), return an empty string so the
    UI hides the line rather than showing noisy git plumbing.
    """
    m = re.match(r"^(v?\d+\.\d+(?:\.\d+)?)-(\d+)-g[0-9a-f]+$", raw)
    if not m:
        return ""
    tag, commits_after = m.group(1), int(m.group(2))
    if commits_after == 0:
        return tag
    return f"{tag}.{commits_after}"


async def check_myos() -> dict:
    """Return myOS version info: current commit, commits behind origin/main."""
    repo = str(PROJECT_ROOT)
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", repo, "fetch", "origin", "--quiet"],
            capture_output=True,
            timeout=15,
        )
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", repo, "rev-list", "HEAD..origin/main", "--count"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        behind = int(result.stdout.strip()) if result.returncode == 0 else 0

        # Current version: latest tag + number of commits since it
        desc_result = await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", repo, "describe", "--tags", "--long", "--always"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        current = desc_result.stdout.strip() if desc_result.returncode == 0 else "unknown"
        # Clean up git describe output: "v3.0-66-gff90e7e" -> "v3.0.66"
        current = _humanize_git_describe(current)

        # Latest remote tag
        remote_tag_result = await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", repo, "describe", "--tags", "--abbrev=0", "origin/main"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        latest = remote_tag_result.stdout.strip() if remote_tag_result.returncode == 0 else current

        return {"current": current, "latest": latest, "behind": behind > 0, "commits_behind": behind}
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return {"current": "unknown", "latest": "unknown", "behind": False, "commits_behind": 0}


async def check_ostk() -> dict:
    """Return ostk version info: current installed version vs. latest GitHub release."""
    current = "unknown"
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["ostk", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            output = result.stdout.strip() or result.stderr.strip()
            # ostk typically outputs something like "ostk 2.4.0" or just "2.4.0"
            match = re.search(r"(\d+\.\d+[\.\d]*)", output)
            if match:
                current = match.group(1)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    latest = "unknown"
    behind = False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{OSTK_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                tag = data.get("tag_name", "")
                latest = tag.lstrip("v")
                if current != "unknown" and latest != "unknown":
                    behind = _semver_tuple(latest) > _semver_tuple(current)
    except (httpx.HTTPError, KeyError, ValueError):
        pass

    return {"current": current, "latest": latest, "behind": behind}


async def check_all(force: bool = False) -> dict:
    """Return upgrade status for both myOS and ostk, using cache when fresh."""
    if not force:
        cached = _load_cache()
        if cached is not None:
            return cached

    myos_info, ostk_info = await asyncio.gather(
        check_myos(),
        check_ostk(),
    )

    result = {"youros": myos_info, "ostk": ostk_info}
    _save_cache(result)
    return result


async def run_upgrade(target: str) -> dict:
    """Run an upgrade for 'myos', 'ostk', or 'both'. Returns success and message."""
    targets = ["myos", "ostk"] if target == "both" else [target]
    messages = []
    success = True

    for t in targets:
        if t == "myos":
            msg = await _upgrade_myos()
        elif t == "ostk":
            msg = await _upgrade_ostk()
        else:
            msg = f"Unknown target: {t}"
            success = False
        if msg.startswith("Error"):
            success = False
        messages.append(msg)

    # Invalidate cache so the next status check reflects new versions
    _invalidate_cache()

    return {"success": success, "message": " ".join(messages)}


async def _upgrade_myos() -> str:
    repo = str(PROJECT_ROOT)
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", repo, "pull", "--ff-only", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return "myOS updated successfully. Restart myOS to apply the update."
        err = result.stderr.strip() or result.stdout.strip()
        return f"Error updating myOS: {err}"
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"Error updating myOS: {e}"


def _read_anchor_pid(project_root: Path) -> dict | None:
    """Read the ostk daemon anchor.pid file. Returns None if absent or unreadable."""
    anchor_path = project_root / ".ostk" / "anchor.pid"
    try:
        return json.loads(anchor_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _restart_stale_daemon(new_version: str) -> None:
    """Terminate the running ostk daemon if it predates new_version.

    The daemon runs from a cached binary (~/.cache/ostk/daemon-<version>).
    After a binary upgrade, the cached binary is not automatically replaced;
    only the anchor.pid file records which version the daemon was started with.
    When the running daemon's ostk_version differs from new_version the boot
    splash and ``ostk --version`` disagree. Sending SIGTERM forces the daemon
    to stop so the next ``ostk boot`` starts a fresh process from the new
    binary's embedded daemon.
    """
    anchor = _read_anchor_pid(PROJECT_ROOT)
    if anchor is None:
        return

    daemon_version = anchor.get("ostk_version", "")
    if daemon_version == new_version:
        return

    pid = anchor.get("pid")
    if not pid:
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass  # Already stopped or not ours to kill


async def _upgrade_ostk() -> str:
    """Download and install the latest ostk binary from GitHub releases."""
    import platform as _platform

    try:
        # Determine platform strings
        machine = _platform.machine().lower()
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        system = _platform.system()
        if system == "Darwin":
            os_tag = "apple-darwin"
        elif system == "Linux":
            os_tag = "unknown-linux-musl"
        else:
            return f"Error updating ostk: unsupported platform {system}"

        platform_str = f"{arch}-{os_tag}"

        # Fetch latest release info
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{OSTK_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code != 200:
                return f"Error updating ostk: could not fetch release info (HTTP {resp.status_code})"
            data = resp.json()
            version = data.get("tag_name", "").lstrip("v")
            if not version:
                return "Error updating ostk: could not determine latest version"

            tarball_name = f"ostk-v{version}-{platform_str}.tar.gz"
            download_url = (
                f"https://github.com/{OSTK_REPO}/releases/download/"
                f"v{version}/{tarball_name}"
            )

            # Download the tarball
            async with client.stream("GET", download_url, follow_redirects=True) as stream:
                if stream.status_code != 200:
                    return f"Error updating ostk: download failed (HTTP {stream.status_code})"
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_path = Path(tmpdir)
                    tarball_path = tmp_path / tarball_name
                    with open(tarball_path, "wb") as f:
                        async for chunk in stream.aiter_bytes(8192):
                            f.write(chunk)

                    # Extract and install
                    result = await asyncio.to_thread(
                        subprocess.run,
                        ["tar", "-xzf", str(tarball_path), "-C", tmpdir],
                        capture_output=True,
                        timeout=30,
                    )
                    if result.returncode != 0:
                        return "Error updating ostk: could not extract tarball"

                    binary = tmp_path / "ostk"
                    if not binary.exists():
                        return "Error updating ostk: binary not found in tarball"

                    bin_dir = Path.home() / ".local" / "bin"
                    bin_dir.mkdir(parents=True, exist_ok=True)
                    dest = bin_dir / "ostk"
                    binary.chmod(0o755)
                    binary.replace(dest)

        # Restart any stale daemon so the boot splash and --version agree.
        _restart_stale_daemon(version)
        return f"ostk updated to {version} successfully."
    except (httpx.HTTPError, OSError, subprocess.TimeoutExpired) as e:
        return f"Error updating ostk: {e}"
