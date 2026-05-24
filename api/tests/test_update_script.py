"""
Tests for update.sh ostk upgrade logic.

Verifies that update.sh contains version-comparison logic so users
automatically get the latest ostk binary when running myos-update.
"""

import re
from pathlib import Path

UPDATE_SH = Path(__file__).parents[2] / "update.sh"


def _script_text() -> str:
    return UPDATE_SH.read_text()


def test_update_sh_exists():
    assert UPDATE_SH.exists(), "update.sh not found at repo root"


def test_update_sh_fetches_latest_version():
    text = _script_text()
    assert "api.github.com/repos/os-tack/ostk.ai/releases/latest" in text, (
        "update.sh must fetch the latest ostk release from the GitHub API"
    )


def test_update_sh_reads_installed_version():
    text = _script_text()
    assert "ostk --version" in text, (
        "update.sh must read the currently installed ostk version via 'ostk --version'"
    )


def test_update_sh_compares_versions():
    text = _script_text()
    # Presence of a compare_versions function or explicit string comparison
    has_compare_fn = "compare_versions" in text
    has_version_eq = re.search(r'CURRENT_VERSION.*LATEST_VERSION|LATEST_VERSION.*CURRENT_VERSION', text)
    assert has_compare_fn or has_version_eq, (
        "update.sh must contain version-comparison logic (compare_versions function or equivalent)"
    )


def test_update_sh_downloads_tarball_on_upgrade():
    text = _script_text()
    assert "tar.gz" in text or "TARBALL" in text, (
        "update.sh must download a tarball when a newer ostk version is available"
    )


def test_update_sh_installs_binary_on_upgrade():
    text = _script_text()
    assert "install -m 755" in text or 'cp.*ostk' in text, (
        "update.sh must install the downloaded ostk binary"
    )


def test_update_sh_skips_upgrade_when_current():
    text = _script_text()
    assert "already on the latest version" in text or "up to date" in text, (
        "update.sh must print a clear message when ostk is already current"
    )


def test_ostk_toml_kernel_allows_240():
    """ostk.toml kernel constraint must allow v2.4.0 (the current latest release)."""
    toml_path = Path(__file__).parents[2] / "ostk.toml"
    assert toml_path.exists(), "ostk.toml not found at repo root"
    text = toml_path.read_text()
    # Extract the upper bound from e.g. ">=2.2.9, <2.5.0"
    match = re.search(r'kernel\s*=\s*"[^"]*<(\d+\.\d+)', text)
    assert match, "Could not parse kernel upper bound from ostk.toml"
    upper = tuple(int(x) for x in match.group(1).split("."))
    target = (2, 4)
    assert upper > target, (
        f"ostk.toml kernel upper bound <{match.group(1)} must be > 2.4 to allow v2.4.0"
    )
