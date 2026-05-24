"""Tests for the cross platform helpers.

These tests pin the dispatch behavior so macOS keeps working and Linux gets
the right commands. They mock subprocess calls so nothing actually runs on the
host machine, which lets the tests pass on CI and on Coulter's Linux box.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services import platform_helpers


# --- current_platform ---


@pytest.mark.parametrize(
    "system_value,expected",
    [
        ("Darwin", "macos"),
        ("Linux", "linux"),
        ("Windows", "windows"),
        ("FreeBSD", "unknown"),
    ],
)
def test_current_platform_maps_system_names(system_value, expected):
    with patch("services.platform_helpers.platform.system", return_value=system_value):
        assert platform_helpers.current_platform() == expected


def test_is_linux_true_on_linux():
    with patch("services.platform_helpers.platform.system", return_value="Linux"):
        assert platform_helpers.is_linux() is True
        assert platform_helpers.is_macos() is False


def test_is_macos_true_on_darwin():
    with patch("services.platform_helpers.platform.system", return_value="Darwin"):
        assert platform_helpers.is_macos() is True
        assert platform_helpers.is_linux() is False


# --- open_path ---


def test_open_path_uses_open_on_macos():
    with patch("services.platform_helpers.platform.system", return_value="Darwin"), \
         patch("services.platform_helpers.subprocess.Popen") as popen:
        popen.return_value = MagicMock()
        result = platform_helpers.open_path("/tmp/hello.txt")
        assert result is True
        args, _kwargs = popen.call_args
        assert args[0][0] == "open"
        assert args[0][1] == "/tmp/hello.txt"


def test_open_path_uses_xdg_open_on_linux():
    with patch("services.platform_helpers.platform.system", return_value="Linux"), \
         patch("services.platform_helpers.shutil.which", return_value="/usr/bin/xdg-open"), \
         patch("services.platform_helpers.subprocess.Popen") as popen:
        popen.return_value = MagicMock()
        result = platform_helpers.open_path("/tmp/hello.txt")
        assert result is True
        args, _kwargs = popen.call_args
        assert args[0][0] == "xdg-open"


def test_open_path_returns_false_when_xdg_open_missing():
    with patch("services.platform_helpers.platform.system", return_value="Linux"), \
         patch("services.platform_helpers.shutil.which", return_value=None):
        assert platform_helpers.open_path("/tmp/hello.txt") is False


def test_open_path_uses_startfile_on_windows():
    import os as _os
    with patch("services.platform_helpers.platform.system", return_value="Windows"), \
         patch.object(_os, "startfile", create=True) as mock_start:
        result = platform_helpers.open_path("/tmp/hello.txt")
        assert result is True
        mock_start.assert_called_once_with("/tmp/hello.txt")


def test_open_path_returns_false_on_unknown_platform():
    with patch("services.platform_helpers.platform.system", return_value="FreeBSD"):
        assert platform_helpers.open_path("/tmp/hello.txt") is False


# --- clipboard_copy ---


def test_clipboard_copy_uses_pbcopy_on_macos():
    with patch("services.platform_helpers.platform.system", return_value="Darwin"), \
         patch("services.platform_helpers.subprocess.Popen") as popen:
        fake = MagicMock()
        fake.returncode = 0
        fake.communicate.return_value = (b"", b"")
        popen.return_value = fake
        assert platform_helpers.clipboard_copy("hi") is True
        args, _kwargs = popen.call_args
        assert args[0][0] == "pbcopy"


def test_clipboard_copy_prefers_wl_copy_on_linux():
    def which(name):
        return "/usr/bin/wl-copy" if name == "wl-copy" else None

    with patch("services.platform_helpers.platform.system", return_value="Linux"), \
         patch("services.platform_helpers.shutil.which", side_effect=which), \
         patch("services.platform_helpers.subprocess.Popen") as popen:
        fake = MagicMock()
        fake.returncode = 0
        fake.communicate.return_value = (b"", b"")
        popen.return_value = fake
        assert platform_helpers.clipboard_copy("hi") is True
        args, _kwargs = popen.call_args
        assert args[0][0] == "wl-copy"


def test_clipboard_copy_falls_back_to_xclip_on_linux():
    def which(name):
        return "/usr/bin/xclip" if name == "xclip" else None

    with patch("services.platform_helpers.platform.system", return_value="Linux"), \
         patch("services.platform_helpers.shutil.which", side_effect=which), \
         patch("services.platform_helpers.subprocess.Popen") as popen:
        fake = MagicMock()
        fake.returncode = 0
        fake.communicate.return_value = (b"", b"")
        popen.return_value = fake
        assert platform_helpers.clipboard_copy("hi") is True
        args, _kwargs = popen.call_args
        assert args[0][0] == "xclip"
        assert "clipboard" in args[0]


def test_clipboard_copy_returns_false_when_no_tool_on_linux():
    with patch("services.platform_helpers.platform.system", return_value="Linux"), \
         patch("services.platform_helpers.shutil.which", return_value=None):
        assert platform_helpers.clipboard_copy("hi") is False


# --- notify ---


def test_notify_uses_osascript_on_macos():
    with patch("services.platform_helpers.platform.system", return_value="Darwin"), \
         patch("services.platform_helpers.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        assert platform_helpers.notify("Title", "Body") is True
        args, _kwargs = run.call_args
        assert args[0][0] == "osascript"


def test_notify_uses_notify_send_on_linux():
    with patch("services.platform_helpers.platform.system", return_value="Linux"), \
         patch("services.platform_helpers.shutil.which", return_value="/usr/bin/notify-send"), \
         patch("services.platform_helpers.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        assert platform_helpers.notify("Title", "Body") is True
        args, _kwargs = run.call_args
        assert args[0][0] == "notify-send"


def test_notify_returns_false_when_notify_send_missing():
    with patch("services.platform_helpers.platform.system", return_value="Linux"), \
         patch("services.platform_helpers.shutil.which", return_value=None):
        assert platform_helpers.notify("Title", "Body") is False


# --- escape ---


def test_escape_handles_quotes_and_backslashes():
    out = platform_helpers._escape('say "hi" \\n')
    assert '\\"hi\\"' in out
    assert "\\\\n" in out


# --- Windows branch coverage ---


def test_clipboard_copy_returns_false_on_windows():
    with patch("services.platform_helpers.platform.system", return_value="Windows"):
        assert platform_helpers.clipboard_copy("hello") is False


def test_notify_returns_false_on_windows():
    with patch("services.platform_helpers.platform.system", return_value="Windows"):
        assert platform_helpers.notify("Title", "Body") is False
