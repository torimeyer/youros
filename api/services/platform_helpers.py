"""Cross platform helpers for opening files, copying text, and sending notifications.

myOS runs on macOS and Linux. Instead of sprinkling platform checks throughout
the code base, everything goes through this module. Each helper detects the
platform once and dispatches to the right underlying command.

None of these helpers raise on failure. They return a boolean so callers can
decide whether to fall back to something else (for example, showing a message
in the UI instead of opening a system notification).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Union


def current_platform() -> str:
    """Return 'macos', 'linux', 'windows', or 'unknown'.

    Wraps platform.system so tests can mock one function instead of chasing
    every call site.
    """
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    if system == "windows":
        return "windows"
    return "unknown"


def is_linux() -> bool:
    return current_platform() == "linux"


def is_macos() -> bool:
    return current_platform() == "macos"


def open_path(path: Union[str, Path]) -> bool:
    """Open a file, folder, or URL in the default application.

    On macOS this uses `open`. On Linux it uses `xdg-open`. Returns True if
    the command launched, False otherwise. It does not wait for the opened
    app to exit.
    """
    target = str(path)
    kind = current_platform()
    if kind == "macos":
        cmd = ["open", target]
    elif kind == "linux":
        if not shutil.which("xdg-open"):
            return False
        cmd = ["xdg-open", target]
    elif kind == "windows":
        try:
            import os as _os
            _os.startfile(target)
            return True
        except (OSError, AttributeError):
            return False
    else:
        return False

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, FileNotFoundError):
        return False


def clipboard_copy(text: str) -> bool:
    """Copy text to the system clipboard.

    macOS uses `pbcopy`. Linux tries `wl-copy` (Wayland) first, then
    `xclip`, then `xsel`. Returns True on success.
    """
    kind = current_platform()
    if kind == "macos":
        return _run_with_stdin(["pbcopy"], text)

    if kind == "linux":
        # Wayland first because new Linux desktops default to it.
        if shutil.which("wl-copy"):
            return _run_with_stdin(["wl-copy"], text)
        if shutil.which("xclip"):
            return _run_with_stdin(
                ["xclip", "-selection", "clipboard"], text
            )
        if shutil.which("xsel"):
            return _run_with_stdin(["xsel", "--clipboard", "--input"], text)
        return False

    return False


def notify(title: str, message: str) -> bool:
    """Show a system notification.

    On macOS this uses `osascript`. On Linux it uses `notify-send` if
    available. Returns True on success.
    """
    kind = current_platform()
    if kind == "macos":
        script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
        return _run(["osascript", "-e", script])

    if kind == "linux":
        if not shutil.which("notify-send"):
            return False
        return _run(["notify-send", title, message])

    return False


def _run_with_stdin(cmd: list[str], text: str) -> bool:
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.communicate(input=text.encode("utf-8"), timeout=5)
        return proc.returncode == 0
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run(cmd: list[str]) -> bool:
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _escape(text: str) -> str:
    """Escape a string for inclusion in an osascript string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')
