"""Regression tests for Gemini CLI detection (UAT item 4).

The dev backend is launched by a script, not the user's interactive shell, so
its PATH is narrower. `shutil.which("gemini")` then returns None even when the
user installed and signed in to the CLI in their terminal, which made the
settings card show a red "not found or not signed in". These tests lock in the
fallback binary resolution, the OAuth-creds signal, and the PATH augmentation.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import services.gemini_cli_provider as gcp


def test_find_gemini_binary_uses_path_first(tmp_path):
    fake = tmp_path / "gemini"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    with patch("services.gemini_cli_provider.shutil.which", return_value=str(fake)):
        assert gcp._find_gemini_binary() == str(fake)


def test_find_gemini_binary_falls_back_to_install_dir(tmp_path):
    # PATH lookup fails (the backend's narrow PATH), but the binary is present
    # in a known install dir, it must still be found.
    install = tmp_path / "npm-global" / "bin"
    install.mkdir(parents=True, exist_ok=True)
    fake = install / "gemini"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    with patch("services.gemini_cli_provider.shutil.which", return_value=None), \
         patch("services.gemini_cli_provider._gemini_install_dirs", return_value=[install]):
        assert gcp._find_gemini_binary() == str(fake)


def test_find_gemini_binary_none_when_absent(tmp_path):
    with patch("services.gemini_cli_provider.shutil.which", return_value=None), \
         patch("services.gemini_cli_provider._gemini_install_dirs", return_value=[tmp_path]):
        assert gcp._find_gemini_binary() is None


def test_gemini_signed_in_reads_creds_file(tmp_path):
    creds = tmp_path / ".gemini" / "oauth_creds.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text("{}")
    with patch("services.gemini_cli_provider.Path.home", return_value=tmp_path):
        assert gcp._gemini_signed_in() is True


def test_gemini_signed_in_false_without_creds(tmp_path):
    with patch("services.gemini_cli_provider.Path.home", return_value=tmp_path):
        assert gcp._gemini_signed_in() is False


def test_build_subprocess_env_prepends_install_dirs_and_strips_keys(tmp_path):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    with patch("services.gemini_cli_provider._gemini_install_dirs", return_value=[d]), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "x", "PATH": "/usr/bin"}, clear=False):
        env = gcp._build_subprocess_env()
    assert str(d) in env["PATH"].split(os.pathsep)
    assert "GOOGLE_API_KEY" not in env  # blocked auth key stripped


@pytest.mark.asyncio
async def test_available_when_ping_times_out_but_signed_in(tmp_path, monkeypatch):
    # A slow ping must not report a signed-in user as logged out.
    fake = tmp_path / "gemini"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(gcp, "_find_gemini_binary", lambda: str(fake))
    monkeypatch.setattr(gcp, "_gemini_signed_in", lambda: True)

    async def fake_to_thread(_fn, *a, **k):
        return (-1, "__timeout__")

    monkeypatch.setattr(gcp.asyncio, "to_thread", fake_to_thread)
    gcp._detection_cache["result"] = None
    gcp._detection_cache["expires_at"] = 0.0
    assert await gcp.is_gemini_cli_available(force=True) is True


@pytest.mark.asyncio
async def test_unavailable_when_ping_times_out_and_not_signed_in(tmp_path, monkeypatch):
    fake = tmp_path / "gemini"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(gcp, "_find_gemini_binary", lambda: str(fake))
    monkeypatch.setattr(gcp, "_gemini_signed_in", lambda: False)

    async def fake_to_thread(_fn, *a, **k):
        return (-1, "__timeout__")

    monkeypatch.setattr(gcp.asyncio, "to_thread", fake_to_thread)
    gcp._detection_cache["result"] = None
    gcp._detection_cache["expires_at"] = 0.0
    assert await gcp.is_gemini_cli_available(force=True) is False
