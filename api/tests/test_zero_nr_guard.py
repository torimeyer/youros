"""Guard test enforcing zero New Relic overlay leakage on main.

New Relic files, scripts, and identifiers belong exclusively on the separate
overlay branch and must never land in git-tracked files on main.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

# Construct banned tokens dynamically so this guard file itself contains
# zero literal matches if someone runs raw git grep on the repository.
_NR_STEM = "".join(("new", "relic"))
_NR_UNDERSCORE = "".join(("new", "_", "relic"))
_NR_HYPHEN = "".join(("new", "-", "relic"))
_NR_BRANCH = "".join(("nr", "-", "enterprise"))
_NR_BRANCH_UNDERSCORE = "".join(("nr", "_", "enterprise"))

_BANNED_PATTERN = re.compile(
    "|".join(
        re.escape(tok)
        for tok in (
            _NR_STEM,
            _NR_UNDERSCORE,
            _NR_HYPHEN,
            _NR_BRANCH,
            _NR_BRANCH_UNDERSCORE,
        )
    ),
    re.IGNORECASE,
)

# Binary extensions skipped during text scanning; filenames are always checked.
_BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".pdf",
    ".mp3",
    ".mp4",
    ".wav",
    ".zip",
    ".tar",
    ".gz",
}


def test_zero_nr_files_or_strings_on_main():
    """Verify zero tracked filenames or text contents match New Relic tokens."""
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    rel_paths = [
        p.decode("utf-8", errors="replace")
        for p in proc.stdout.split(b"\x00")
        if p
    ]
    assert rel_paths, "Expected git ls-files to return tracked repository files"

    offenders: list[str] = []
    for rel in rel_paths:
        if _BANNED_PATTERN.search(rel):
            offenders.append(f"path:{rel}")
            continue
        full_path = REPO_ROOT / rel
        if full_path.suffix.lower() in _BINARY_SUFFIXES or not full_path.is_file():
            continue
        try:
            text = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        match = _BANNED_PATTERN.search(text)
        if match:
            offenders.append(f"{rel}:{match.group(0)}")

    assert not offenders, (
        "Tracked files on main must contain zero overlay-specific identifiers. "
        f"Found: {offenders[:20]}"
    )
