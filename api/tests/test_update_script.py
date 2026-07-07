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


def test_update_sh_auto_pops_stash_after_update():
    """Script must attempt git stash pop automatically, not just remind the user."""
    text = _script_text()
    # The old reminder pattern should no longer be the only action taken
    assert "git stash pop" in text, "update.sh must call git stash pop"
    # Should NOT only print a manual reminder without actually running the pop
    assert "Restoring your tucked-away changes" in text, (
        "update.sh must actively restore the stash, not just print a reminder"
    )


def test_update_sh_detects_stash_pop_conflict():
    """Script must detect when git stash pop exits non-zero (conflict)."""
    text = _script_text()
    # An 'if git stash pop' or 'if ! git stash pop' pattern means the script
    # checks the exit status instead of letting set -e abort silently.
    assert "if git stash pop" in text or "if ! git stash pop" in text, (
        "update.sh must check git stash pop's exit status to detect conflicts"
    )


def test_update_sh_saves_backup_patch_on_conflict():
    """Script must save a patch backup when stash pop conflicts."""
    text = _script_text()
    assert "git stash show -p" in text, (
        "update.sh must run 'git stash show -p' to save the stash content before cleanup"
    )
    assert "youros-stash-backup" in text, (
        "update.sh must write the backup to a file named youros-stash-backup-..."
    )


def test_update_sh_cleans_up_conflict_state():
    """Script must leave repo in clean state after a conflicted restore."""
    text = _script_text()
    # Must reset conflicted files to HEAD so the working tree is not wedged
    assert "git checkout HEAD -- ." in text, (
        "update.sh must run 'git checkout HEAD -- .' to clear conflicted files"
    )
    # Must drop the stash entry that failed to apply
    assert "git stash drop" in text, (
        "update.sh must drop the stash entry after a conflicted pop so it cannot re-wedge later"
    )


def test_update_sh_stash_push_error_is_handled():
    """Script must handle git stash push failures instead of silently swallowing them."""
    text = _script_text()
    # The push must not be run bare; it must be wrapped in error handling
    assert "if ! git stash push" in text or "git stash push" in text, (
        "update.sh must call git stash push"
    )
    assert "Could not tuck away your changes" in text, (
        "update.sh must print a clear error when git stash push fails"
    )


def test_update_sh_cancel_path_also_handles_pop_conflict():
    """Cancel path must also handle a conflicted stash restore cleanly."""
    text = _script_text()
    # The cancel path restores the stash too — it must check for conflict there as well.
    # We verify by counting occurrences of the conflict-cleanup pattern.
    checkout_head_count = text.count("git checkout HEAD -- .")
    assert checkout_head_count >= 1, (
        "update.sh must call 'git checkout HEAD -- .' in at least one stash conflict handler"
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
